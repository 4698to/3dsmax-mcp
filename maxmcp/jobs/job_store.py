"""JSONL 追加式任务持久化 + 启动恢复（设计文档 §5）。

每任务一个 ``jobs/{job_id}.jsonl`` 文件：

- 头行：``{"type": "snapshot", "job": {...}}``（初始完整快照，log 为空）；
- 后续行：增量事件 ``log`` / ``status`` / ``result`` / ``cancel_requested``。

只 append、不原地改，避免并发写锁。调度器单线程出队 + 每运行任务一个
写者，事件追加加锁防交错；写失败仅告警，任务主流程仍以内存为准。

恢复规则（§5）：queued 重新入队、running / awaiting_confirm 交由调度器
统一标记 failed(interrupted)、终态保留，均由调用方（scheduler）处理；
本模块只负责原样重建 Job 对象。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .job_model import (
    MAX_LOG_TAIL,
    Job,
    JobLogEntry,
    JobStatus,
    TERMINAL_STATUSES,
)

_log = logging.getLogger("maxmcp.jobs.store")

ENV_JOBS_DIR = "MAXMCP_JOBS_DIR"
# %LOCALAPPDATA%\3dsmax-mcp\jobs（设计 §5 默认）。
DEFAULT_JOBS_DIR_REL = Path("3dsmax-mcp") / "jobs"
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600  # 默认 7 天


def default_jobs_dir() -> Path:
    env = os.environ.get(ENV_JOBS_DIR)
    if env and env.strip():
        return Path(env.strip()).expanduser()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / DEFAULT_JOBS_DIR_REL


class JobStore:
    """追加式 JSONL 任务存储。线程安全（事件写入加锁）。"""

    def __init__(
        self,
        jobs_dir: Optional[Path] = None,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
    ):
        self._dir = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
        self._retention_seconds = retention_seconds
        self._lock = threading.Lock()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log.warning("Could not create jobs dir %s: %s", self._dir, exc)

    @property
    def jobs_dir(self) -> Path:
        return self._dir

    @property
    def retention_seconds(self) -> float:
        return self._retention_seconds

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    def create(self, job: Job) -> None:
        """落初始快照（queued 状态，log 为空）。"""
        self._append(job.job_id, {"type": "snapshot", "job": job.to_dict()})

    def append_log(self, job_id: str, entry: JobLogEntry) -> None:
        self._append(job_id, {"type": "log", "entry": entry.to_dict()})

    def set_status(self, job_id: str, status: str, at: Optional[float] = None) -> None:
        self._append(job_id, {"type": "status", "status": status, "at": at or time.time()})

    def set_result(self, job_id: str, result: dict[str, Any]) -> None:
        self._append(job_id, {"type": "result", "result": result})

    def set_cancel_requested(self, job_id: str) -> None:
        self._append(job_id, {"type": "cancel_requested", "at": time.time()})

    def _append(self, job_id: str, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                with open(self._path(job_id), "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
            except OSError as exc:
                _log.warning("append event to job %s failed: %s", job_id, exc)

    # ------------------------------------------------------------------ #
    # 读取 / 恢复
    # ------------------------------------------------------------------ #

    def load_all(self) -> dict[str, Job]:
        """扫描全部 jsonl，按事件流重建 Job；损坏行跳过，不抛出。"""
        jobs: dict[str, Job] = {}
        if not self._dir.is_dir():
            return jobs
        for path in sorted(self._dir.glob("*.jsonl")):
            job = self._load_one(path)
            if job is not None:
                jobs[job.job_id] = job
        return jobs

    def _load_one(self, path: Path) -> Optional[Job]:
        job: Optional[Job] = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    job = self._apply_event(job, event)
        except OSError as exc:
            _log.warning("Could not read job file %s: %s", path, exc)
            return None
        if job is not None and len(job.log) > MAX_LOG_TAIL:
            del job.log[: len(job.log) - MAX_LOG_TAIL]
        return job

    @staticmethod
    def _apply_event(job: Optional[Job], event: dict[str, Any]) -> Optional[Job]:
        etype = event.get("type")
        if etype == "snapshot":
            return Job.from_dict(event.get("job") or {})
        if job is None:
            return None  # 无头行，忽略后续增量
        if etype == "log":
            job.log.append(JobLogEntry.from_dict(event.get("entry") or {}))
        elif etype == "status":
            status = str(event.get("status", ""))
            if status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.AWAITING_CONFIRM) or status in TERMINAL_STATUSES:
                job.status = status
            at = event.get("at")
            if isinstance(at, (int, float)):
                if status == JobStatus.RUNNING and job.started_at is None:
                    job.started_at = float(at)
                if status in TERMINAL_STATUSES:
                    job.finished_at = float(at)
        elif etype == "result":
            job.result = event.get("result") or {}
        elif etype == "cancel_requested":
            job.cancel_requested = True
        return job

    # ------------------------------------------------------------------ #
    # 清理
    # ------------------------------------------------------------------ #

    def purge_expired(self, now: Optional[float] = None) -> int:
        """删除超过保留期的终态任务文件（§9），返回删除数。"""
        now = time.time() if now is None else now
        removed = 0
        for job in self.load_all().values():
            if not job.is_terminal():
                continue
            finished = job.finished_at or job.created_at
            if now - finished <= self._retention_seconds:
                continue
            if self.delete(job.job_id):
                removed += 1
        return removed

    def delete(self, job_id: str) -> bool:
        with self._lock:
            try:
                self._path(job_id).unlink(missing_ok=True)
                return True
            except OSError as exc:
                _log.warning("delete job file %s failed: %s", job_id, exc)
                return False

    def _path(self, job_id: str) -> Path:
        # job_id 由本系统生成（uuid hex），路径安全，无需额外净化。
        return self._dir / f"{job_id}.jsonl"
