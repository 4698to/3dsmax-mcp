"""任务模型：多用户 GoSkin 自动蒙皮队列的数据结构（服务端）。

与设计文档 docs/goskin-job-queue-design.md §4 对应：Job 字段、状态机
常量、日志条目。本模块只定义纯数据结构与 JSON 序列化，不涉及持久化
与调度（见 job_store / scheduler）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


class JobStatus:
    """Job 状态机取值（§4.2）。"""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRM = "awaiting_confirm"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)

VALID_STATUSES = frozenset(
    {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.AWAITING_CONFIRM}
    | TERMINAL_STATUSES
)

VALID_PRIORITIES = ("high", "normal", "low")
VALID_CONFIRM_MODES = ("auto", "manual")

# 内存中 Job.log 保留的尾部条数；全量日志始终完整落在 JSONL 文件中。
MAX_LOG_TAIL = 200

_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def new_job_id() -> str:
    """生成不透明、路径安全的任务 ID（uuid hex）。"""
    return uuid.uuid4().hex


def priority_rank(priority: str) -> int:
    """出队排序权重（§6.2：high > normal > low）。"""
    return _PRIORITY_ORDER.get(priority, 1)


@dataclass
class JobLogEntry:
    """一条步骤日志（§4.1 Job.log）。"""

    ts: float
    step: str
    note: str
    level: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "step": self.step, "note": self.note, "level": self.level}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JobLogEntry":
        return cls(
            ts=float(raw.get("ts", 0.0)),
            step=str(raw.get("step", "")),
            note=str(raw.get("note", "")),
            level=str(raw.get("level", "info")),
        )


@dataclass
class Job:
    """一个 GoSkin 自动蒙皮队列任务（§4.1）。"""

    job_id: str
    owner: str
    status: str = JobStatus.QUEUED
    instance: Optional[str] = None
    scene_local_path: Optional[str] = None
    preserve_scene: bool = False
    mesh_names: Optional[list[str]] = None
    bone_names: Optional[list[str]] = None
    confirm_mode: str = "auto"  # auto | manual
    priority: str = "normal"  # high | normal | low
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    log: list[JobLogEntry] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    retries: int = 0
    queue_position: Optional[int] = None

    def add_log(self, step: str, note: str, level: str = "info") -> JobLogEntry:
        """追加日志并截断到尾部上限（全量由 JobStore 持久化）。"""
        entry = JobLogEntry(ts=time.time(), step=step, note=note, level=level)
        self.log.append(entry)
        if len(self.log) > MAX_LOG_TAIL:
            del self.log[: len(self.log) - MAX_LOG_TAIL]
        return entry

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner": self.owner,
            "status": self.status,
            "instance": self.instance,
            "scene_local_path": self.scene_local_path,
            "preserve_scene": self.preserve_scene,
            "mesh_names": self.mesh_names,
            "bone_names": self.bone_names,
            "confirm_mode": self.confirm_mode,
            "priority": self.priority,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": [e.to_dict() for e in self.log],
            "result": self.result,
            "cancel_requested": self.cancel_requested,
            "retries": self.retries,
            "queue_position": self.queue_position,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Job":
        return cls(
            job_id=str(raw["job_id"]),
            owner=str(raw.get("owner", "")),
            status=str(raw.get("status", JobStatus.QUEUED)),
            instance=raw.get("instance"),
            scene_local_path=raw.get("scene_local_path"),
            preserve_scene=bool(raw.get("preserve_scene", False)),
            mesh_names=raw.get("mesh_names"),
            bone_names=raw.get("bone_names"),
            confirm_mode=str(raw.get("confirm_mode", "auto")),
            priority=str(raw.get("priority", "normal")),
            created_at=float(raw.get("created_at", 0.0) or time.time()),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
            log=[JobLogEntry.from_dict(e) for e in (raw.get("log") or [])],
            result=raw.get("result") or {},
            cancel_requested=bool(raw.get("cancel_requested", False)),
            retries=int(raw.get("retries", 0) or 0),
            queue_position=raw.get("queue_position"),
        )
