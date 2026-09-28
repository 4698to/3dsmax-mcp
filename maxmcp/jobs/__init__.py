# -*- coding: utf-8 -*-
"""GoSkin 任务队列包（docs/goskin-job-queue-design.md）。

聚合导出 Job 模型 / JSONL 存储 / GoSkin 执行器 / JobManager 调度器。
"""

from .executor_goskin import DEFAULT_COMPLETE_TIMEOUT_S, confirm_goskin_job_step, run_goskin_job
from .job_model import (
    MAX_LOG_TAIL,
    TERMINAL_STATUSES,
    VALID_CONFIRM_MODES,
    VALID_PRIORITIES,
    VALID_STATUSES,
    Job,
    JobLogEntry,
    JobStatus,
    new_job_id,
    priority_rank,
)
from .job_store import DEFAULT_RETENTION_SECONDS, JobStore, default_jobs_dir
from .scheduler import (
    JobError,
    JobManager,
    JobNotFoundError,
    JobPermissionError,
    JobQuotaExceededError,
    JobStateError,
    job_manager,
)

__all__ = [
    "DEFAULT_COMPLETE_TIMEOUT_S",
    "DEFAULT_RETENTION_SECONDS",
    "MAX_LOG_TAIL",
    "TERMINAL_STATUSES",
    "VALID_CONFIRM_MODES",
    "VALID_PRIORITIES",
    "VALID_STATUSES",
    "Job",
    "JobError",
    "JobLogEntry",
    "JobManager",
    "JobNotFoundError",
    "JobPermissionError",
    "JobQuotaExceededError",
    "JobStateError",
    "JobStatus",
    "JobStore",
    "confirm_goskin_job_step",
    "default_jobs_dir",
    "job_manager",
    "new_job_id",
    "priority_rank",
    "run_goskin_job",
]
