# -*- coding: utf-8 -*-
"""GoSkin 任务调度器（设计文档 docs/goskin-job-queue-design.md §6、§10、§12）。

JobManager 是服务端唯一的仲裁点：

- 有界并发：``MIN(MAXMCP_JOB_MAX_CONCURRENT, 在线实例数)``，每运行任务独立
  worker session（普通 object()），``manager.acquire(wait=False, for_job=True)``
  快抢实例（不占交互 FIFO 槽），``finally`` 必释放；
- 出队：priority（high>normal>low）→ FIFO（created_at）；``QUEUE_TTL`` 过期
  自动 ``failed(queue_timeout)``；指定实例的任务只等该实例空闲，不阻塞其他任务；
- 池分配：未指定实例优先 jobs 池再 shared（由 ``acquire(for_job=True)`` 内置），
  ``MAXMCP_JOB_USE_SHARED=false`` 时严格隔离（手动挑 jobs 池空闲实例）；
- 确认门禁：``confirm_mode=auto`` 跑到底自动点「开始蒙皮」；``manual`` 停在
  ``awaiting_confirm`` 持租约等待 ``confirm`` 动作，``HOLD_TTL`` 超时自动取消；
- 恢复：queued 重入队；running / awaiting_confirm 默认 ``failed(interrupted)``
  （``MAXMCP_JOB_RECOVER_RUNNING=true`` 时重入队一次）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..instance_manager import (
    InstanceBusyError,
    InstanceError,
    InstanceReservedError,
    NoFreeInstanceError,
    manager,
)
from ..workspace_config import (
    ensure_workspace_dir,
    get_ocr_base,
    resolve_workspace_dir,
)
from .executor_goskin import (
    DEFAULT_COMPLETE_TIMEOUT_S,
    confirm_goskin_job_step,
    run_goskin_job,
)
from .job_model import (
    TERMINAL_STATUSES,
    VALID_CONFIRM_MODES,
    VALID_PRIORITIES,
    Job,
    JobStatus,
    new_job_id,
    new_view_token,
    priority_rank,
)
from .job_store import DEFAULT_RETENTION_SECONDS, JobStore

_log = logging.getLogger("maxmcp.jobs.scheduler")


# --------------------------------------------------------------------------- #
# 配置环境变量（§10，全部可选、均有默认）
# --------------------------------------------------------------------------- #

def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: Optional[float] = None) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


# --------------------------------------------------------------------------- #
# 错误类型（tools/jobs.py 映射为结构化错误码）
# --------------------------------------------------------------------------- #

class JobError(RuntimeError):
    """任务队列基类错误。"""


class JobNotFoundError(JobError):
    """job_id 不存在。"""


class JobQuotaExceededError(JobError):
    """提交者已达排队上限。"""


class JobPermissionError(JobError):
    """无权操作他人任务。"""


class JobStateError(JobError):
    """任务状态不允许该操作（如非 awaiting_confirm 时 confirm）。"""


# --------------------------------------------------------------------------- #
# worker 租约
# --------------------------------------------------------------------------- #

@dataclass
class _Worker:
    """一个运行中（或 awaiting_confirm）任务持有的租约。"""

    job_id: str
    session: object
    client: Any
    thread: Optional[threading.Thread] = None


class JobManager:
    """任务队列仲裁者。单例由 server.py 持有并 start()。"""

    def __init__(
        self,
        store: Optional[JobStore] = None,
        *,
        max_concurrent: Optional[int] = None,
        run_timeout_s: Optional[float] = None,
        queue_ttl_s: Optional[float] = None,
        max_queued_per_owner: Optional[int] = None,
        anon_queue_max: Optional[int] = None,
        use_shared: Optional[bool] = None,
        poll_seconds: Optional[float] = None,
        auto_confirm: Optional[bool] = None,
        hold_ttl_s: Optional[float] = None,
        retention_s: Optional[float] = None,
        recover_running: Optional[bool] = None,
        cleanup_files: Optional[bool] = None,
        admin_ids: Optional[list[str]] = None,
        ocr_base: Optional[str] = None,
        complete_timeout_s: Optional[float] = None,
        output_dir: Optional[str] = None,
        debug_mode: Optional[bool] = None,
    ):
        self._store = store if store is not None else JobStore()
        # 并发上限：None = 在线实例数（§6.2）。
        self._max_concurrent = (
            _env_int("MAXMCP_JOB_MAX_CONCURRENT") if max_concurrent is None else max_concurrent
        )
        self._run_timeout_s = float(
            _env_float("MAXMCP_JOB_RUN_TIMEOUT", 1800.0) if run_timeout_s is None else run_timeout_s
        )
        self._queue_ttl_s = float(
            _env_float("MAXMCP_JOB_QUEUE_TTL", 7200.0) if queue_ttl_s is None else queue_ttl_s
        )
        self._max_queued_per_owner = int(
            _env_int("MAXMCP_JOB_MAX_QUEUED_PER_OWNER", 5)
            if max_queued_per_owner is None
            else max_queued_per_owner
        )
        self._anon_queue_max = int(
            _env_int("MAXMCP_JOB_ANON_QUEUE_MAX", 3) if anon_queue_max is None else anon_queue_max
        )
        self._use_shared = (
            _env_bool("MAXMCP_JOB_USE_SHARED", True) if use_shared is None else bool(use_shared)
        )
        self._poll_seconds = float(
            _env_float("MAXMCP_JOB_POLL_SECONDS", 5.0) if poll_seconds is None else poll_seconds
        )
        self._auto_confirm = (
            _env_bool("MAXMCP_JOB_AUTO_CONFIRM", True) if auto_confirm is None else bool(auto_confirm)
        )
        self._hold_ttl_s = float(
            _env_float("MAXMCP_JOB_HOLD_TTL", 1800.0) if hold_ttl_s is None else hold_ttl_s
        )
        self._retention_s = float(
            _env_float("MAXMCP_JOB_RETENTION", float(DEFAULT_RETENTION_SECONDS))
            if retention_s is None
            else retention_s
        )
        self._recover_running = (
            _env_bool("MAXMCP_JOB_RECOVER_RUNNING", False)
            if recover_running is None
            else bool(recover_running)
        )
        self._cleanup_files = (
            _env_bool("MAXMCP_JOB_CLEANUP_FILES", False)
            if cleanup_files is None
            else bool(cleanup_files)
        )
        # 管理员：MAXMCP_JOB_ADMIN_IDS 白名单 + MAXMCP_JOB_ADMIN_TOKEN（管理台令牌，
        # 网页端无法设置 X-Client-Id 头，页面用它作为 X-Client-Id 即被视为管理员）。
        _ids = list(admin_ids) if admin_ids is not None else _env_list("MAXMCP_JOB_ADMIN_IDS")
        _token = (os.environ.get("MAXMCP_JOB_ADMIN_TOKEN") or "").strip()
        if _token:
            _ids.append(_token)
        self._admin_ids = frozenset(_ids)
        self._debug_mode = (
            _env_bool("MAXMCP_JOB_DEBUG", False) if debug_mode is None else bool(debug_mode)
        )
        self._ocr_base = get_ocr_base() if ocr_base is None else ocr_base
        self._complete_timeout_s = float(
            DEFAULT_COMPLETE_TIMEOUT_S if complete_timeout_s is None else complete_timeout_s
        )
        # 蒙皮产物保存目录：MAXMCP_JOB_OUTPUT_DIR 或共享 workspace（/files/ 可下载）。
        if output_dir is None:
            raw = (os.environ.get("MAXMCP_JOB_OUTPUT_DIR") or "").strip()
            self._output_dir = raw or str(ensure_workspace_dir(resolve_workspace_dir()))
        else:
            self._output_dir = output_dir

        self._jobs: dict[str, Job] = {}
        self._workers: dict[str, _Worker] = {}
        self._holds: dict[str, float] = {}  # awaiting_confirm 截止时间
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # 只读状态
    # ------------------------------------------------------------------ #

    @property
    def admin_ids(self) -> frozenset:
        return self._admin_ids

    @property
    def auto_confirm(self) -> bool:
        return self._auto_confirm

    @property
    def jobs_dir(self) -> Path:
        return self._store.jobs_dir

    def is_admin(self, owner: Optional[str]) -> bool:
        return bool(owner) and owner in self._admin_ids

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "queued": sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED),
                "running": sum(1 for j in self._jobs.values() if j.status == JobStatus.RUNNING),
                "awaiting_confirm": sum(
                    1 for j in self._jobs.values() if j.status == JobStatus.AWAITING_CONFIRM
                ),
                "terminal": sum(1 for j in self._jobs.values() if j.is_terminal()),
                "workers": len(self._workers),
                "holds": len(self._holds),
                "max_concurrent": self._max_concurrent,
                "use_shared": self._use_shared,
                "auto_confirm": self._auto_confirm,
                "ocr_base": self._ocr_base,
            }

    def queue_context(self, job_id: str) -> dict[str, Any]:
        """全局队列统计 + 指定任务（若仍在排队）的实时位置（§6.2 顺序）。"""
        with self._lock:
            counts = {JobStatus.QUEUED: 0, JobStatus.RUNNING: 0, JobStatus.AWAITING_CONFIRM: 0}
            terminal = 0
            for j in self._jobs.values():
                if j.status in counts:
                    counts[j.status] += 1
                elif j.is_terminal():
                    terminal += 1
            position: Optional[int] = None
            job = self._jobs.get(job_id)
            if job is not None and job.status == JobStatus.QUEUED and not job.cancel_requested:
                for i, cand in enumerate(self._queued_candidates_locked(time.time()), 1):
                    if cand.job_id == job_id:
                        position = i
                        break
            return {"counts": counts, "terminal": terminal, "position": position}

    # ------------------------------------------------------------------ #
    # 生命周期（§6.4）
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._recover()
        self._stop.clear()
        thread = threading.Thread(
            target=self._loop, name="goskin-job-scheduler", daemon=True
        )
        self._thread = thread
        thread.start()
        _log.info(
            "JobManager started (jobs_dir=%s, max_concurrent=%s, use_shared=%s, "
            "auto_confirm=%s)",
            self._store.jobs_dir,
            self._max_concurrent,
            self._use_shared,
            self._auto_confirm,
        )

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 调度循环永不因单次异常退出
                _log.exception("scheduler tick failed")

    # ------------------------------------------------------------------ #
    # 恢复（§12：queued 重入队；running/awaiting_confirm → interrupted）
    # ------------------------------------------------------------------ #

    def _recover(self) -> None:
        now = time.time()
        for job_id, job in self._store.load_all().items():
            self._jobs[job_id] = job
            if job.is_terminal():
                continue
            if job.status == JobStatus.QUEUED:
                if job.cancel_requested:
                    self._mark_terminal_locked(
                        job_id, JobStatus.CANCELLED, code="cancelled", error="重启前已请求取消"
                    )
                else:
                    job.add_log("recover", "服务重启：任务重新入队")
                    self._store.append_log(job_id, job.log[-1])
                continue
            # running / awaiting_confirm：重启时租约已失效。
            if self._recover_running:
                job.status = JobStatus.QUEUED
                job.cancel_requested = False
                self._store.set_status(job_id, JobStatus.QUEUED)
                job.add_log("recover", "服务重启：running/awaiting_confirm 任务重新入队")
                self._store.append_log(job_id, job.log[-1])
            else:
                self._mark_terminal_locked(
                    job_id,
                    JobStatus.FAILED,
                    code="interrupted",
                    error="服务重启时任务未完成",
                )
                job.add_log("recover", "服务重启：running/awaiting_confirm 标记 failed(interrupted)", "error")
                self._store.append_log(job_id, job.log[-1])
            _log.info("recovered job %s -> %s", job_id, job.status)

    # ------------------------------------------------------------------ #
    # 提交 / 查询 / 取消 / 确认（供 MCP / HTTP 工具调用）
    # ------------------------------------------------------------------ #

    def submit(
        self,
        owner: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        scene_local_path: Optional[str] = None,
        instance: Optional[str] = None,
        mesh_names: Optional[list[str]] = None,
        bone_names: Optional[list[str]] = None,
        confirm_mode: Optional[str] = None,
        priority: str = "normal",
        preserve_scene: bool = False,
        debug: Optional[bool] = None,
    ) -> Job:
        owner = owner or ""
        user_id = (user_id or "").strip() or None
        with self._lock:
            if priority not in VALID_PRIORITIES:
                raise ValueError(f"priority 必须是 {VALID_PRIORITIES} 之一，收到 {priority!r}")
            if confirm_mode is None:
                confirm_mode = "auto" if self._auto_confirm else "manual"
            if confirm_mode not in VALID_CONFIRM_MODES:
                raise ValueError(
                    f"confirm_mode 必须是 {VALID_CONFIRM_MODES} 之一，收到 {confirm_mode!r}"
                )
            active = sum(
                1 for j in self._jobs.values() if j.owner == owner and not j.is_terminal()
            )
            limit = self._max_queued_per_owner
            if not owner:
                limit = min(limit, self._anon_queue_max)
            if active >= limit:
                raise JobQuotaExceededError(
                    f"提交者 {owner or 'anonymous'} 已达到排队上限 {limit}（含排队/运行/待确认）"
                )
            # 池校验（§6.5）：interactive 池拒绝；use_shared=false 时拒绝 shared 池。
            if instance:
                if instance in set(manager.pool_instance_names("interactive")):
                    raise InstanceReservedError(
                        f"实例 {instance!r} 属于 interactive 池，队列任务不可用"
                    )
                if not self._use_shared and instance in set(manager.pool_instance_names("shared")):
                    raise ValueError(
                        f"MAXMCP_JOB_USE_SHARED=false 严格隔离，不能指定 shared 池实例 {instance!r}"
                    )
            job = Job(
                job_id=new_job_id(),
                owner=owner,
                status=JobStatus.QUEUED,
                instance=instance,
                scene_local_path=scene_local_path,
                preserve_scene=preserve_scene,
                mesh_names=list(mesh_names) if mesh_names else None,
                bone_names=list(bone_names) if bone_names else None,
                confirm_mode=confirm_mode,
                priority=priority,
                view_token=new_view_token(),
                debug=debug if debug is not None else self._debug_mode,
                user_id=user_id,
            )
            if user_id:
                job.add_log("submit", f"提交者 user_id={user_id}")
            job.queue_position = (
                sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED) + 1
            )
            self._store.create(job)
            self._jobs[job.job_id] = job
            _log.info(
                "job %s submitted by %r: scene=%s instance=%s confirm=%s priority=%s",
                job.job_id,
                owner or "anonymous",
                job.scene_local_path,
                job.instance,
                job.confirm_mode,
                job.priority,
            )
            return job

    def get_dict(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(f"任务不存在: {job_id}")
            return job.to_dict()

    def list_dicts(
        self,
        owner: Optional[str] = None,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        owner = owner or ""
        with self._lock:
            items = [
                job.to_dict()
                for job in self._jobs.values()
                if (is_admin or not owner or job.owner == owner)
                and (not status or job.status == status)
            ]
        items.sort(key=lambda d: d.get("created_at", 0.0))
        if offset:
            items = items[offset:]
        if limit is not None:
            items = items[:limit]
        return items

    def cancel(
        self,
        job_id: str,
        requester: Optional[str] = None,
        is_admin: bool = False,
        view_token_ok: bool = False,
    ) -> dict[str, Any]:
        requester = requester or ""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(f"任务不存在: {job_id}")
            if not (is_admin or requester == job.owner or view_token_ok):
                raise JobPermissionError("只能取消自己的任务（或由管理员操作）")
            if job.is_terminal():
                return {"ok": True, "status": job.status, "already_terminal": True}
            now = time.time()
            job.cancel_requested = True
            self._store.set_cancel_requested(job_id)
            if job.status == JobStatus.QUEUED:
                self._mark_terminal_locked(
                    job_id, JobStatus.CANCELLED, code="cancelled", error="排队中已取消"
                )
                job.add_log("cancel", "排队中取消")
                self._store.append_log(job_id, job.log[-1])
                return {"ok": True, "status": JobStatus.CANCELLED}
            if job.status == JobStatus.AWAITING_CONFIRM:
                # 确认等待中没有运行线程，直接终态并释放租约。
                self._mark_terminal_locked(
                    job_id, JobStatus.CANCELLED, code="cancelled", error="确认等待中取消"
                )
                job.add_log("cancel", "确认等待中取消", "warn")
                self._store.append_log(job_id, job.log[-1])
                self._holds.pop(job_id, None)
                self._release_worker_locked(job_id)
                return {"ok": True, "status": JobStatus.CANCELLED}
            # running：置标志，执行器在安全点停止。
            job.add_log("cancel", "已收到取消请求，将在安全点停止", "warn")
            self._store.append_log(job_id, job.log[-1])
            return {"ok": True, "status": job.status}

    def confirm(
        self,
        job_id: str,
        requester: Optional[str] = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        requester = requester or ""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(f"任务不存在: {job_id}")
            if not (is_admin or requester == job.owner):
                raise JobPermissionError("只能确认自己的任务（或由管理员操作）")
            if job.status != JobStatus.AWAITING_CONFIRM:
                raise JobStateError(
                    f"任务不在 awaiting_confirm 状态（当前 {job.status}），无需确认"
                )
            worker = self._workers.get(job_id)
            if worker is None:
                raise JobStateError("任务实例租约已失效，无法确认")
            # 回到 running（§8.1 confirm 返回 status=running），继续持租约。
            job.status = JobStatus.RUNNING
            job.cancel_requested = False
            self._store.set_status(job_id, JobStatus.RUNNING)
            self._holds.pop(job_id, None)
            job.add_log("confirm", "收到确认动作，点击「开始蒙皮」")
            self._store.append_log(job_id, job.log[-1])
            thread = threading.Thread(
                target=self._run_confirm,
                args=(job_id,),
                name=f"goskin-confirm-{job_id[:8]}",
                daemon=True,
            )
            worker.thread = thread
            thread.start()
        return {"ok": True, "status": JobStatus.RUNNING}

    def retry(
        self,
        job_id: str,
        requester: Optional[str] = None,
        is_admin: bool = False,
        view_token_ok: bool = False,
    ) -> dict[str, Any]:
        """重试失败任务：原地重跑（failed → queued，retries+1，复用视图令牌）。

        仅 FAILED 可重试；其他状态抛 JobStateError。重试不复制新任务，同一
        job_id / status_url 继续跟踪；created_at 刷新为当前时间重新排队
        （避免重启后排队 TTL 误判），store 追加 reset_terminal 事件保证
        重启重建一致。
        """
        requester = requester or ""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(f"任务不存在: {job_id}")
            if not (is_admin or requester == job.owner or view_token_ok):
                raise JobPermissionError("只能重试自己的任务（或由管理员操作）")
            if job.status != JobStatus.FAILED:
                raise JobStateError(f"只有失败任务可重试（当前状态 {job.status}）")
            now = time.time()
            job.status = JobStatus.QUEUED
            job.cancel_requested = False
            job.started_at = None
            job.finished_at = None
            job.created_at = now
            job.result = {}
            job.retries += 1
            self._store.reset_terminal(job_id, at=now, retries=job.retries)
            self._store.set_status(job_id, JobStatus.QUEUED, at=now)
            job.add_log("retry", f"手动重试（第 {job.retries} 次），任务重新入队")
            self._store.append_log(job_id, job.log[-1])
            job.queue_position = (
                sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED) + 1
            )
            _log.info(
                "job %s retried by %r (retries=%d) -> queued",
                job_id,
                requester or "anonymous",
                job.retries,
            )
            return {
                "ok": True,
                "status": JobStatus.QUEUED,
                "retries": job.retries,
                "job_id": job_id,
            }

    # ------------------------------------------------------------------ #
    # 调度循环
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        now = time.time()
        with self._lock:
            try:
                self._store.purge_expired(now)
            except Exception:  # noqa: BLE001 清理失败不影响调度
                _log.exception("purge_expired failed")
            # 内存侧同步清理过期的终态任务。
            for job_id in list(self._jobs):
                job = self._jobs[job_id]
                if job.is_terminal() and now - (job.finished_at or job.created_at) > self._retention_s:
                    del self._jobs[job_id]
            self._expire_hold_locked(now)
            self._try_dispatch_locked(now)

    def _expire_hold_locked(self, now: float) -> None:
        for job_id, deadline in list(self._holds.items()):
            if now < deadline:
                continue
            job = self._jobs.get(job_id)
            if job is None or job.status != JobStatus.AWAITING_CONFIRM:
                self._holds.pop(job_id, None)
                continue
            self._holds.pop(job_id, None)
            self._mark_terminal_locked(
                job_id,
                JobStatus.CANCELLED,
                code="hold_timeout",
                error=f"manual 确认超过持有上限 {self._hold_ttl_s:.0f}s",
            )
            job.add_log("hold", "manual 确认超时，任务取消", "error")
            self._store.append_log(job_id, job.log[-1])
            self._release_worker_locked(job_id)
            _log.warning("job %s hold expired -> cancelled", job_id)

    def _try_dispatch_locked(self, now: float) -> None:
        # debug 任务不依赖在线实例：先全部模拟完成，再正常调度。
        for job in self._queued_candidates_locked(now):
            if job.debug:
                self._complete_debug_locked(job)
            # 非 debug 任务留给下面主循环处理。
        instances = manager.list_instances()
        usable = [
            i
            for i in instances
            if i.get("pool") in ("shared", "jobs") and i.get("online") is True
        ]
        cap = self._max_concurrent if self._max_concurrent is not None else len(usable)
        cap = min(cap, len(usable))
        if len(self._workers) >= cap:
            return
        for job in self._queued_candidates_locked(now):
            if len(self._workers) >= cap:
                return
            name = job.instance
            if name is None and not self._use_shared:
                # 严格隔离：手动挑一个 jobs 池空闲实例。
                idle_jobs = [
                    i for i in usable if i.get("pool") == "jobs" and not i.get("busy")
                ]
                if not idle_jobs:
                    return
                name = idle_jobs[0]["name"]
            worker_session = object()
            reset_scene = not (job.scene_local_path is None and job.preserve_scene)
            try:
                client = manager.acquire(
                    worker_session,
                    name=name,
                    wait=False,
                    reset_scene=reset_scene,
                    for_job=True,
                )
            except InstanceReservedError as exc:
                self._mark_terminal_locked(
                    job.job_id,
                    JobStatus.FAILED,
                    code="instance_reserved",
                    error=str(exc),
                )
                job.add_log("scheduler", f"实例池冲突，任务失败: {exc}", "error")
                self._store.append_log(job.job_id, job.log[-1])
                continue
            except (NoFreeInstanceError, InstanceBusyError):
                if job.instance:
                    continue  # 指定实例忙：等该实例，其他任务继续尝试。
                return  # 无空闲实例：本轮停止。
            except InstanceError as exc:
                if job.instance:
                    self._log_entry(job.job_id, "scheduler", f"指定实例不可用: {exc}", "warn")
                    continue
                return
            except Exception as exc:  # noqa: BLE001 acquire 内部异常
                _log.warning("acquire failed for job %s: %s", job.job_id, exc)
                if job.instance:
                    continue
                return
            # 成功：绑定实例、置 running、起执行线程。
            manager.register_job_session(worker_session)
            job.instance = name
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            self._store.set_status(job.job_id, JobStatus.RUNNING, at=job.started_at)
            worker = _Worker(job_id=job.job_id, session=worker_session, client=client)
            self._workers[job.job_id] = worker
            thread = threading.Thread(
                target=self._run_worker,
                args=(job.job_id,),
                name=f"goskin-job-{job.job_id[:8]}",
                daemon=True,
            )
            worker.thread = thread
            thread.start()
            _log.info(
                "job %s dispatched to %s (workers=%d/%d)",
                job.job_id,
                job.instance,
                len(self._workers),
                cap,
            )

    def _complete_debug_locked(self, job: Job) -> None:
        """debug 模式：仅入队模拟，不发送到 Max 实例，直接标记成功。"""
        now = time.time()
        job.status = JobStatus.SUCCEEDED
        job.finished_at = now
        self._store.set_status(job.job_id, JobStatus.SUCCEEDED, at=now)
        result = {
            "ok": True,
            "status": JobStatus.SUCCEEDED,
            "code": "debug",
            "error": None,
            "message": "debug 模式：任务仅入队模拟，未发送到 Max 实例",
        }
        job.result = result
        self._store.set_result(job.job_id, result)
        job.add_log("scheduler", "debug 模式：仅入队模拟，未发送到 Max 实例", "warn")
        self._store.append_log(job.job_id, job.log[-1])
        _log.info("job %s completed in debug mode (not dispatched)", job.job_id)

    def _queued_candidates_locked(self, now: float) -> list[Job]:
        candidates: list[Job] = []
        for job in self._jobs.values():
            if job.status != JobStatus.QUEUED or job.cancel_requested:
                continue
            if self._queue_ttl_s > 0 and now - job.created_at > self._queue_ttl_s:
                self._mark_terminal_locked(
                    job.job_id,
                    JobStatus.FAILED,
                    code="queue_timeout",
                    error=f"排队超过 {self._queue_ttl_s:.0f}s",
                )
                job.add_log("scheduler", f"排队超时，任务失败", "error")
                self._store.append_log(job.job_id, job.log[-1])
                continue
            candidates.append(job)
        candidates.sort(key=lambda j: (priority_rank(j.priority), j.created_at))
        return candidates

    # ------------------------------------------------------------------ #
    # 执行线程
    # ------------------------------------------------------------------ #

    def _run_worker(self, job_id: str) -> None:
        worker = self._workers.get(job_id)
        job = self._jobs.get(job_id)
        if worker is None or job is None:
            return
        try:
            result = run_goskin_job(
                worker.client,
                job,
                ocr_base=self._ocr_base,
                run_timeout_s=self._run_timeout_s,
                complete_timeout_s=self._complete_timeout_s,
                output_dir=self._output_dir,
                callback=lambda step, note, level="info": self._log_entry(
                    job_id, step, note, level
                ),
                is_cancelled=lambda: job.cancel_requested,
            )
        except Exception as exc:  # noqa: BLE001 执行器异常兜底为 failed
            self._log_entry(job_id, "scheduler", f"执行器异常: {exc}", "error")
            result = {
                "ok": False,
                "status": JobStatus.FAILED,
                "code": "exception",
                "error": str(exc),
            }
        self._finalize(job_id, result)

    def _run_confirm(self, job_id: str) -> None:
        worker = self._workers.get(job_id)
        job = self._jobs.get(job_id)
        if worker is None or job is None:
            return
        try:
            result = confirm_goskin_job_step(
                worker.client,
                job,
                ocr_base=self._ocr_base,
                complete_timeout_s=self._complete_timeout_s,
                output_dir=self._output_dir,
                callback=lambda step, note, level="info": self._log_entry(
                    job_id, step, note, level
                ),
                is_cancelled=lambda: job.cancel_requested,
            )
        except Exception as exc:  # noqa: BLE001
            self._log_entry(job_id, "scheduler", f"确认动作异常: {exc}", "error")
            result = {
                "ok": False,
                "status": JobStatus.FAILED,
                "code": "exception",
                "error": str(exc),
            }
        self._finalize(job_id, result)

    # ------------------------------------------------------------------ #
    # 终态与释放
    # ------------------------------------------------------------------ #

    def _finalize(self, job_id: str, result: dict[str, Any]) -> None:
        """执行线程收尾：manual 停在 awaiting_confirm（保留租约），其余进终态。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_terminal():
                return
            status = result.get("status")
            if status == JobStatus.AWAITING_CONFIRM:
                job.status = JobStatus.AWAITING_CONFIRM
                job.result = result
                self._store.set_status(job_id, JobStatus.AWAITING_CONFIRM)
                self._store.set_result(job_id, result)
                self._holds[job_id] = time.time() + self._hold_ttl_s
                _log.info("job %s awaiting_confirm (hold_ttl=%.0fs)", job_id, self._hold_ttl_s)
                return
            if status not in TERMINAL_STATUSES:
                status = JobStatus.FAILED
                result = dict(result)
                result["status"] = status
            self._mark_terminal_locked(
                job_id, status, code=result.get("code"), error=result.get("error")
            )
            # 上面先把内存置为失败占位（与 store 一致），这里再覆盖为执行器
            # 返回的真实 result（output_file/viewport_file 等），保持内存=JSONL 末条。
            job.result = result
            self._store.set_result(job_id, result)
            self._holds.pop(job_id, None)
            if self._cleanup_files and job.scene_local_path:
                try:
                    Path(job.scene_local_path).unlink(missing_ok=True)
                except OSError as exc:
                    _log.warning("cleanup scene file %s failed: %s", job.scene_local_path, exc)
            self._release_worker_locked(job_id)
            _log.info("job %s finalized -> %s", job_id, job.status)

    def _mark_terminal_locked(
        self,
        job_id: str,
        status: str,
        *,
        code: Optional[str] = None,
        error: Optional[Any] = None,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.is_terminal():
            return
        now = time.time()
        job.status = status
        job.finished_at = now
        term_result = {"ok": False, "status": status, "code": code, "error": error}
        job.result = term_result
        self._store.set_status(job_id, status, at=now)
        self._store.set_result(job_id, term_result)

    def _release_worker_locked(self, job_id: str) -> None:
        worker = self._workers.pop(job_id, None)
        if worker is None:
            return
        try:
            manager.unregister_job_session(worker.session)
        except Exception:  # noqa: BLE001
            _log.warning("unregister_job_session failed for job %s", job_id, exc_info=True)
        try:
            manager.release(worker.session)
        except Exception as exc:  # noqa: BLE001 释放失败不阻塞后续调度
            _log.warning("release worker for job %s failed: %s", job_id, exc)

    def _log_entry(self, job_id: str, step: str, note: str, level: str = "info") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            entry = job.add_log(step, note, level)
            self._store.append_log(job_id, entry)


# --------------------------------------------------------------------------- #
# 模块级单例（server.py import 后 start()）
# --------------------------------------------------------------------------- #

job_manager = JobManager()
