# -*- coding: utf-8 -*-
"""GoSkin 任务队列的 MCP 工具与 HTTP 路由（docs/goskin-job-queue-design.md §8）。

- MCP 工具（主接口，Agent 首选）：submit/get/list/cancel/confirm 五个
  ``mcp.tool()``，owner 由 ``ctx.session`` 派生；
- HTTP 路由（参照 files.py 的 ``@mcp.custom_route``）：``POST /jobs``、
  ``GET /jobs/{id}``、``GET /jobs``、``POST /jobs/{id}/cancel``、
  ``POST /jobs/{id}/confirm``，owner 取 ``X-Client-Id`` 头，缺失视为匿名
  公共队列（受 ``MAXMCP_JOB_ANON_QUEUE_MAX`` 约束）。

跨用户查询/取消仅允许本人 + ``MAXMCP_JOB_ADMIN_IDS`` 白名单。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

from mcp.server.fastmcp import Context
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..helpers.file_http import sanitize_filename
from ..instance_manager import InstanceReservedError
from ..jobs import (
    JobNotFoundError,
    JobPermissionError,
    JobQuotaExceededError,
    JobStateError,
    job_manager,
)
from ..server import WORKSPACE_DIR, mcp

_log = logging.getLogger("maxmcp.tools.jobs")

#: POST /jobs multipart 场景文件大小上限（与 files.py 上传一致，1 GB）。
_MAX_JOB_UPLOAD_BYTES = 1024 * 1024 * 1024


# --------------------------------------------------------------------------- #
# owner 派生与错误映射
# --------------------------------------------------------------------------- #

def _owner_from_session(session: Any) -> str:
    """MCP 侧 owner：优先 session 自带 id，否则用稳定内存地址派生。"""
    if session is None:
        return ""
    sid = getattr(session, "id", None)
    if sid:
        return f"mcp:{sid}"
    return f"mcp:{id(session):x}"


def _client_id(request: Request) -> str:
    """HTTP 侧 owner：``X-Client-Id`` 头，缺失为空（匿名公共队列）。"""
    return (request.headers.get("x-client-id") or "").strip()


def _error_payload(exc: Exception) -> dict[str, Any]:
    """把 JobManager 抛出的异常映射为结构化错误（instances.py 同款风格）。"""
    if isinstance(exc, JobNotFoundError):
        return {"ok": False, "code": "JOB_NOT_FOUND", "retryable": False, "error": str(exc)}
    if isinstance(exc, JobQuotaExceededError):
        return {"ok": False, "code": "JOB_QUOTA_EXCEEDED", "retryable": False, "error": str(exc)}
    if isinstance(exc, JobPermissionError):
        return {"ok": False, "code": "JOB_PERMISSION_DENIED", "retryable": False, "error": str(exc)}
    if isinstance(exc, JobStateError):
        return {"ok": False, "code": "JOB_STATE", "retryable": False, "error": str(exc)}
    if isinstance(exc, InstanceReservedError):
        return {"ok": False, "code": "INSTANCE_RESERVED", "retryable": False, "error": str(exc)}
    if isinstance(exc, ValueError):
        return {"ok": False, "code": "BAD_REQUEST", "retryable": False, "error": str(exc)}
    _log.exception("job api error: %s", exc)
    return {"ok": False, "code": "JOB_ERROR", "retryable": False, "error": str(exc)}


# --------------------------------------------------------------------------- #
# MCP 工具（主接口）
# --------------------------------------------------------------------------- #

@mcp.tool()
def submit_goskin_job(
    ctx: Context,
    scene_local_path: Optional[str] = None,
    instance: Optional[str] = None,
    mesh_names: Optional[list[str]] = None,
    bone_names: Optional[list[str]] = None,
    confirm_mode: Optional[str] = None,
    priority: str = "normal",
    preserve_scene: bool = False,
) -> str:
    """Submit a GoSkin auto-skinning job to the shared queue.

    The server arbitrates all jobs: each job is queued, then dispatched to a
    free 3ds Max instance (jobs/shared pool) with an exclusive lease. Pass the
    local scene path as returned by ``workspace_upload``; when omitted the
    currently open scene on the assigned instance is used.

    ``confirm_mode`` defaults to the server setting (MAXMCP_JOB_AUTO_CONFIRM,
    usually ``auto``): the job then clicks「开始蒙皮」itself and runs to
    completion. Use ``manual`` to stop at ``awaiting_confirm`` and later call
    ``confirm_goskin_job`` to click「开始蒙皮」.

    Args:
        scene_local_path: Local absolute path of the .max scene on the 3ds Max
            machine (from workspace_upload / HTTP upload). null = current scene.
        instance: Optional instance name from list_instances to pin the job to
            (must not belong to the interactive pool).
        mesh_names: Optional mesh names to skin (default: unhidden meshes).
        bone_names: Optional bone names to use (default: propose from scene).
        confirm_mode: ``auto`` or ``manual`` (server default if omitted).
        priority: ``high``, ``normal`` (default) or ``low``.
        preserve_scene: When true and no scene_local_path, do not reset the
            current scene before running.
    """
    owner = _owner_from_session(ctx.session)
    try:
        job = job_manager.submit(
            owner,
            scene_local_path=scene_local_path,
            instance=instance,
            mesh_names=mesh_names,
            bone_names=bone_names,
            confirm_mode=confirm_mode,
            priority=priority,
            preserve_scene=preserve_scene,
        )
    except Exception as exc:  # noqa: BLE001 统一映射为结构化错误
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    return json.dumps(
        {
            "job_id": job.job_id,
            "status": job.status,
            "queue_position": job.queue_position,
            "owner": job.owner,
            "confirm_mode": job.confirm_mode,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def get_goskin_job(ctx: Context, job_id: str) -> str:
    """Get one GoSkin job: status, tail logs, result/error.

    Only the job owner (or a MAXMCP_JOB_ADMIN_IDS admin) may read a job.
    """
    owner = _owner_from_session(ctx.session)
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    if not (job_manager.is_admin(owner) or data.get("owner") == owner):
        return json.dumps(
            {
                "ok": False,
                "code": "JOB_PERMISSION_DENIED",
                "retryable": False,
                "error": "只能查看自己的任务（或由管理员查看）",
            },
            ensure_ascii=False,
        )
    return json.dumps(data, ensure_ascii=False)


@mcp.tool()
def list_goskin_jobs(
    ctx: Context,
    status: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> str:
    """List GoSkin jobs. By default only the caller's own jobs are returned;
    admins (MAXMCP_JOB_ADMIN_IDS) see all jobs.
    """
    owner = _owner_from_session(ctx.session)
    try:
        items = job_manager.list_dicts(
            owner, status=status, limit=limit, offset=offset, is_admin=job_manager.is_admin(owner)
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    return json.dumps({"jobs": items, "count": len(items)}, ensure_ascii=False)


@mcp.tool()
def cancel_goskin_job(ctx: Context, job_id: str) -> str:
    """Cancel a GoSkin job (own jobs only, or by an admin).

    Queued and awaiting_confirm jobs cancel immediately; a running job stops at
    the next safe point (the Max lease is released).
    """
    owner = _owner_from_session(ctx.session)
    try:
        result = job_manager.cancel(job_id, requester=owner, is_admin=job_manager.is_admin(owner))
    except Exception as exc:  # noqa: BLE001
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


@mcp.tool()
def confirm_goskin_job(ctx: Context, job_id: str) -> str:
    """Confirm the「开始蒙皮」click for a manual-mode GoSkin job.

    Only valid while the job is ``awaiting_confirm``; after confirmation it
    returns to ``running`` and completes automatically.
    """
    owner = _owner_from_session(ctx.session)
    try:
        result = job_manager.confirm(job_id, requester=owner, is_admin=job_manager.is_admin(owner))
    except Exception as exc:  # noqa: BLE001
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# HTTP 路由（参照 files.py 的 @mcp.custom_route）
# --------------------------------------------------------------------------- #

async def _save_scene_upload(upload: Any) -> str:
    """把 multipart 场景文件写入 WORKSPACE_DIR，返回本地绝对路径。"""
    safe = sanitize_filename(upload.filename or "scene.max")
    stored = f"{uuid.uuid4().hex[:8]}_{safe}"
    dest = os.path.join(str(WORKSPACE_DIR), stored)
    with open(dest, "wb") as fh:
        fh.write(await upload.read())
    return dest


@mcp.custom_route("/jobs", methods=["POST"])
async def submit_job_http(request: Request) -> Response:
    """POST /jobs — multipart（file=场景文件 + params=JSON）一步提交。"""
    owner = _client_id(request)
    try:
        form = await request.form(max_part_size=_MAX_JOB_UPLOAD_BYTES)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"multipart 解析失败: {exc}"}, status_code=400)
    scene_local_path = None
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", None):
        try:
            scene_local_path = await _save_scene_upload(upload)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": f"场景文件保存失败: {exc}"}, status_code=400)
    params: dict[str, Any] = {}
    raw_params = form.get("params")
    if raw_params:
        try:
            params = json.loads(str(raw_params))
        except (TypeError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": f"params 不是合法 JSON: {exc}"}, status_code=400
            )
    try:
        job = job_manager.submit(
            owner,
            scene_local_path=scene_local_path,
            instance=params.get("instance"),
            mesh_names=params.get("mesh_names"),
            bone_names=params.get("bone_names"),
            confirm_mode=params.get("confirm_mode"),
            priority=params.get("priority", "normal"),
            preserve_scene=bool(params.get("preserve_scene", False)),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse(
        {
            "job_id": job.job_id,
            "status": job.status,
            "queue_position": job.queue_position,
            "owner": job.owner,
            "confirm_mode": job.confirm_mode,
        },
        status_code=202,
    )


@mcp.custom_route("/jobs/{job_id}", methods=["GET"])
async def get_job_http(request: Request) -> Response:
    """GET /jobs/{id} — 查询状态/日志/结果（仅本人或管理员）。"""
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=404)
    if not (job_manager.is_admin(owner) or data.get("owner") == owner):
        return JSONResponse(
            {
                "ok": False,
                "code": "JOB_PERMISSION_DENIED",
                "retryable": False,
                "error": "只能查看自己的任务（或由管理员查看）",
            },
            status_code=403,
        )
    return JSONResponse(data)


@mcp.custom_route("/jobs", methods=["GET"])
async def list_jobs_http(request: Request) -> Response:
    """GET /jobs?status=&owner=&limit=&offset= — 列表（分页）。

    非管理员只能看自己的任务；管理员可传 ``owner`` 过滤或省略看全量。
    """
    owner = _client_id(request)
    is_admin = job_manager.is_admin(owner)
    status = request.query_params.get("status") or None
    try:
        limit = int(request.query_params.get("limit", "") or 0) or None
        offset = int(request.query_params.get("offset", "") or 0)
    except ValueError:
        return JSONResponse({"ok": False, "error": "limit/offset 必须是整数"}, status_code=400)
    filter_owner = request.query_params.get("owner") or None
    if not is_admin:
        filter_owner = owner  # 非管理员强制只看自己的
    try:
        items = job_manager.list_dicts(
            filter_owner, status=status, limit=limit, offset=offset, is_admin=is_admin
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse({"jobs": items, "count": len(items)})


@mcp.custom_route("/jobs/{job_id}/cancel", methods=["POST"])
async def cancel_job_http(request: Request) -> Response:
    """POST /jobs/{id}/cancel — 取消任务（仅本人或管理员）。"""
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    try:
        result = job_manager.cancel(job_id, requester=owner, is_admin=job_manager.is_admin(owner))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse({"ok": True, **result})


@mcp.custom_route("/jobs/{job_id}/confirm", methods=["POST"])
async def confirm_job_http(request: Request) -> Response:
    """POST /jobs/{id}/confirm — manual 模式确认「开始蒙皮」（仅本人或管理员）。"""
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    try:
        result = job_manager.confirm(job_id, requester=owner, is_admin=job_manager.is_admin(owner))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse({"ok": True, **result})
