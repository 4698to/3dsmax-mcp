# -*- coding: utf-8 -*-
"""GoSkin 任务队列的 MCP 工具与 HTTP 路由（docs/goskin-job-queue-design.md §8）。

- MCP 工具（主接口，Agent 首选）：submit/get/list/cancel/confirm 五个
  ``mcp.tool()``。owner 由 ``ctx.session`` 派生（仅本人可查询/取消/确认，
  管理员见 ``MAXMCP_JOB_ADMIN_IDS``）；
- HTTP 路由（参照 files.py 的 ``@mcp.custom_route``）：``POST /jobs``、
  ``GET /jobs/{id}``、``GET /jobs``、``POST /jobs/{id}/cancel``、
  ``POST /jobs/{id}/confirm``、``POST /jobs/{id}/retry``，owner 取
  ``X-Client-Id`` 头，缺失视为匿名公共队列（受 ``MAXMCP_JOB_ANON_QUEUE_MAX``
  约束）；
- 审计 user_id：提交方（Agent）可显式传 ``user_id`` 参数（MCP 工具）或
  ``X-Maxmcp-User-Id`` 请求头（HTTP）。它**只记录到任务日志与 Job 字段**，
  用于追踪"谁提交的"，不参与 owner 鉴权。
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import os
import uuid
from typing import Any, Optional

from mcp.server.fastmcp import Context
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

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


def _request_user_id(request: Request) -> str:
    """HTTP 侧审计 user_id：``X-Maxmcp-User-Id`` 头，仅记录不参与鉴权。"""
    return (request.headers.get("x-maxmcp-user-id") or "").strip()


def _ctx_request_user_id(ctx: Optional[Context]) -> str:
    """MCP 工具侧审计 user_id 兜底：从当前请求的 ``X-Maxmcp-User-Id`` 头读取。

    ``user_id`` 参数优先；参数未传时回退到请求头（agent 常统一在
    HTTP 层带 ``X-Maxmcp-User-Id``）。仅记录不参与鉴权。
    """
    if ctx is None:
        return ""
    try:
        req = getattr(ctx.request_context, "request", None)
        if req is None:
            return ""
        return (req.headers.get("x-maxmcp-user-id") or "").strip()
    except Exception:  # noqa: BLE001 头部读取失败按未提供处理
        return ""


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
# 只读视图 URL / 令牌
# --------------------------------------------------------------------------- #

def _view_base(
    request: Optional[Request] = None,
    ctx: Optional[Context] = None,
) -> str:
    """浏览器可访问的服务根地址。

    优先级：``MAXMCP_PUBLIC_URL`` 环境变量（反向代理等场景）> 当前请求的
    base_url（Host 头）。拿不到时返回空串，status_url 退化为相对路径，
    由客户端自行拼接已知的服务器地址。
    """
    env = os.environ.get("MAXMCP_PUBLIC_URL", "").strip().rstrip("/")
    if env:
        return env
    try:
        if request is not None:
            return str(request.base_url).rstrip("/")
        if ctx is not None:
            req = getattr(ctx.request_context, "request", None)
            if req is not None:
                return str(req.base_url).rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _job_status_url(job_id: str, view_token: Optional[str], base: str = "") -> str:
    """提交后返回给用户的任务状态页 URL（只读）。"""
    path = f"/jobs/{job_id}/view?t={view_token or ''}"
    return f"{base}{path}" if base else path


def _token_matches(actual: Optional[str], provided: str) -> bool:
    """恒定时间比较只读令牌；None/空一律不通过。"""
    if not actual or not provided:
        return False
    try:
        return hmac.compare_digest(actual, provided)
    except TypeError:
        return False


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
    debug: bool = False,
    user_id: Optional[str] = None,
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
        user_id: Optional audit identifier (who submitted the job). It is
            recorded into the job log and the ``user_id`` field for tracing,
            and does NOT affect ownership/authorization. When omitted, the
            request's ``X-Maxmcp-User-Id`` header is used as a fallback.
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
        debug: When true the job is only queued and simulated (succeeded) — it
            is never sent to a 3ds Max instance. Useful for testing the queue.
    """
    owner = _owner_from_session(ctx.session)
    uid = (user_id or "").strip() or _ctx_request_user_id(ctx) or None
    try:
        job = job_manager.submit(
            owner,
            user_id=uid,
            scene_local_path=scene_local_path,
            instance=instance,
            mesh_names=mesh_names,
            bone_names=bone_names,
            confirm_mode=confirm_mode,
            priority=priority,
            preserve_scene=preserve_scene,
            debug=debug,
        )
    except Exception as exc:  # noqa: BLE001 统一映射为结构化错误
        return json.dumps(_error_payload(exc), ensure_ascii=False)
    return json.dumps(
        {
            "job_id": job.job_id,
            "status": job.status,
            "queue_position": job.queue_position,
            "owner": job.owner,
            "user_id": job.user_id,
            "confirm_mode": job.confirm_mode,
            "debug": job.debug,
            "status_url": _job_status_url(job.job_id, job.view_token, _view_base(ctx=ctx)),
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
    """POST /jobs — multipart（file=场景文件 + params=JSON）一步提交。

    owner 取 ``X-Client-Id`` 头；审计 ``X-Maxmcp-User-Id`` 头仅记录到任务。
    """
    owner = _client_id(request)
    audit_user_id = _request_user_id(request)
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
            user_id=audit_user_id,
            scene_local_path=scene_local_path,
            instance=params.get("instance"),
            mesh_names=params.get("mesh_names"),
            bone_names=params.get("bone_names"),
            confirm_mode=params.get("confirm_mode"),
            priority=params.get("priority", "normal"),
            preserve_scene=bool(params.get("preserve_scene", False)),
            debug=bool(params.get("debug", False)),
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse(
        {
            "job_id": job.job_id,
            "status": job.status,
            "queue_position": job.queue_position,
            "owner": job.owner,
            "user_id": job.user_id,
            "confirm_mode": job.confirm_mode,
            "debug": job.debug,
            "status_url": _job_status_url(job.job_id, job.view_token, _view_base(request=request)),
        },
        status_code=202,
    )


@mcp.custom_route("/jobs/{job_id}", methods=["GET"])
async def get_job_http(request: Request) -> Response:
    """GET /jobs/{id} — 查询状态/日志/结果（仅本人或管理员）。"""
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    token = request.query_params.get("t") or ""
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=404)
    if not (
        job_manager.is_admin(owner)
        or data.get("owner") == owner
        or _token_matches(data.get("view_token") or "", token)
    ):
        return JSONResponse(
            {
                "ok": False,
                "code": "JOB_PERMISSION_DENIED",
                "retryable": False,
                "error": "只能查看自己的任务（或由管理员查看）",
            },
            status_code=403,
        )
    data["queue"] = job_manager.queue_context(job_id)
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
    """POST /jobs/{id}/cancel — 取消任务（本人、管理员、或持 view 令牌者）。

    状态页通过 ?t=view_token 调用即可取消自己的提交。
    """
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    token = request.query_params.get("t") or ""
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=404)
    view_ok = _token_matches(data.get("view_token") or "", token)
    if not (
        job_manager.is_admin(owner) or data.get("owner") == owner or view_ok
    ):
        return JSONResponse(
            {
                "ok": False,
                "code": "JOB_PERMISSION_DENIED",
                "retryable": False,
                "error": "只能取消自己的任务（或由管理员操作）",
            },
            status_code=403,
        )
    try:
        result = job_manager.cancel(
            job_id,
            requester=owner,
            is_admin=job_manager.is_admin(owner),
            view_token_ok=view_ok,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse({"ok": True, **result})


@mcp.custom_route("/jobs/{job_id}/retry", methods=["POST"])
async def retry_job_http(request: Request) -> Response:
    """POST /jobs/{id}/retry — 重试失败任务（本人、管理员、或持 view 令牌者）。

    仅 failed 可重试；状态页通过 ?t=view_token 调用即可重试自己的提交，
    重试后同一 URL 继续跟踪（原地重跑，不生成新任务）。
    """
    owner = _client_id(request)
    job_id = request.path_params["job_id"]
    token = request.query_params.get("t") or ""
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=404)
    view_ok = _token_matches(data.get("view_token") or "", token)
    if not (
        job_manager.is_admin(owner) or data.get("owner") == owner or view_ok
    ):
        return JSONResponse(
            {
                "ok": False,
                "code": "JOB_PERMISSION_DENIED",
                "retryable": False,
                "error": "只能重试自己的任务（或由管理员操作）",
            },
            status_code=403,
        )
    try:
        result = job_manager.retry(
            job_id,
            requester=owner,
            is_admin=job_manager.is_admin(owner),
            view_token_ok=view_ok,
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_error_payload(exc), status_code=400)
    return JSONResponse({"ok": True, **result})


# --------------------------------------------------------------------------- #
# 只读状态页（浏览器视图）
# --------------------------------------------------------------------------- #

_STATUS_STYLES = {
    "queued": ("排队中", "#2563eb"),
    "running": ("运行中", "#3b82f6"),
    "awaiting_confirm": ("待确认", "#f59e0b"),
    "succeeded": ("成功", "#16a34a"),
    "failed": ("失败", "#dc2626"),
    "cancelled": ("已取消", "#6b7280"),
}


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


# --------------------------------------------------------------------------- #
# 只读状态页（浏览器视图）
# --------------------------------------------------------------------------- #

_STATUS_STYLES = {
    "queued": ("排队中", "#2563eb"),
    "running": ("运行中", "#3b82f6"),
    "awaiting_confirm": ("待确认", "#f59e0b"),
    "succeeded": ("成功", "#16a34a"),
    "failed": ("失败", "#dc2626"),
    "cancelled": ("已取消", "#6b7280"),
}

_JOB_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GoSkin 任务状态</title>
<style>
body{margin:0;background:#0f172a;color:#e2e8f0;font:14px system-ui,'Segoe UI',sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:18px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;font-size:13px;margin-left:8px}
.meta{color:#94a3b8;font-size:13px;margin:6px 0 16px}
.card{background:#1e293b;border-radius:8px;padding:12px 14px;margin-bottom:12px}
.card h2{font-size:12px;margin:0 0 8px;color:#94a3b8;letter-spacing:.5px}
ul.log{list-style:none;margin:0;padding:0}
ul.log li{font-size:13px;padding:3px 0;border-bottom:1px dashed #334155}
.ts{color:#64748b;margin-right:8px;font-family:Consolas,monospace}
.step{color:#93c5fd;margin-right:8px}
.lvl-error{color:#f87171}.lvl-warn{color:#fbbf24}
.err{color:#f87171;white-space:pre-wrap}.ok{color:#4ade80;white-space:pre-wrap}.dim{color:#64748b}
#errbox{display:none;background:#450a0a;border:1px solid #dc2626;border-radius:8px;padding:10px 14px;margin-bottom:12px;color:#fecaca;font-size:13px}
.kv{color:#94a3b8;display:inline-block;min-width:6em}
.mline{margin:2px 0}
.files{list-style:none;margin:0;padding:0}
.files li{padding:3px 0;border-bottom:1px dashed #334155}
.files a{color:#60a5fa;text-decoration:none;word-break:break-all}
.files a:hover{text-decoration:underline}
#queue{color:#94a3b8;margin-bottom:8px}
.btn-danger{display:none;background:#dc2626;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer}
.btn-danger:hover{background:#b91c1c}.btn-danger:disabled{opacity:.5;cursor:not-allowed}
.btn-retry{display:none;background:#16a34a;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer;margin-left:8px}
.btn-retry:hover{background:#15803d}.btn-retry:disabled{opacity:.5;cursor:not-allowed}
.shotlink{display:block;margin-bottom:10px}
.shot{display:block;max-width:100%;max-height:420px;border:1px solid #334155;border-radius:8px;background:#0f172a}
</style>
</head>
<body>
<div class="wrap"><div id="errbox"></div><h1>GoSkin 任务 <span id="jid" class="dim"></span><span id="badge" class="badge"></span></h1><div class="card"><h2>任务信息</h2><div id="meta"></div></div><div class="card"><h2>下载</h2><ul id="files" class="files"></ul></div><div class="card"><h2>队列 / 操作</h2><div id="queue"></div><button id="cancelBtn" class="btn-danger">取消任务</button><button id="retryBtn" class="btn-retry">重试任务</button></div><div class="card"><h2>日志</h2><ul id="log" class="log"></ul></div><div class="card"><h2>结果</h2><div id="result"></div></div></div>
<script>
const JOB_ID=__JOB_ID__,TOKEN=__TOKEN__,TERMINAL=['succeeded','failed','cancelled'];
const SS={queued:['排队中','#2563eb'],running:['运行中','#3b82f6'],awaiting_confirm:['待确认','#f59e0b'],succeeded:['成功','#16a34a'],failed:['失败','#dc2626'],cancelled:['已取消','#6b7280']};
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function dlLinks(j){
 const L=[];
 if(j.scene_local_path){const n=String(j.scene_local_path).split(/[\\/]/).pop();if(n)L.push({label:'场景文件',name:n,url:'/files/'+encodeURIComponent(n)})}
 const r=j.result||{};
 if(r&&typeof r==='object')for(const k of Object.keys(r)){
  const v=r[k];if(typeof v!=='string'||!v)continue;
  let url=null;
  if(new RegExp('^https?://', 'i').test(v))url=v;
  else if(new RegExp('\\.(max|fbx|png|jpe?g|bmp|tga|exr|json|txt)$', 'i').test(v)){const n=v.split(/[\\/]/).pop();if(n)url='/files/'+encodeURIComponent(n)}
  if(url)L.push({label:k,name:v.split(/[\\/]/).pop()||k,url:url});
 }
 return L;
}
function fmt(ts){if(!ts)return'';const d=new Date(ts*1e3),p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())}
function eb(m){const b=document.getElementById('errbox');b.style.display='block';b.textContent=m}
let t=setInterval(rf,2e3);function stop(){clearInterval(t)}
async function rf(){
 try{
  const r=await fetch('/jobs/'+encodeURIComponent(JOB_ID)+'?t='+encodeURIComponent(TOKEN));
  if(r.status===403){eb('访问令牌无效，请用提交时返回的完整链接打开');stop();return}
  if(r.status===404){eb('任务不存在（可能已过期清理）');stop();return}
  if(!r.ok){eb('查询失败 HTTP '+r.status);return}
  const j=await r.json();document.getElementById('errbox').style.display='none';
  const st=SS[j.status]||[j.status,'#2563eb'];
  document.getElementById('jid').textContent=j.job_id;
  const b=document.getElementById('badge');b.textContent=st[0];b.style.background=st[1];
  const M=[['实例',j.instance||'-'],['优先级',j.priority||'-'],['确认',j.confirm_mode||'-'],['创建',fmt(j.created_at)]];
  if(j.started_at)M.push(['开始',fmt(j.started_at)]);if(j.finished_at)M.push(['结束',fmt(j.finished_at)]);
  if(j.user_id)M.push(['用户',j.user_id]);
  if(j.debug)M.push(['模式','DEBUG 模拟']);
  if(j.mesh_names&&j.mesh_names.length)M.push(['网格',j.mesh_names.join(', ')]);
  if(j.bone_names&&j.bone_names.length)M.push(['骨骼',j.bone_names.join(', ')]);
  M.push(['保留场景',j.preserve_scene?'是':'否']);
  if(j.scene_local_path)M.push(['场景文件',j.scene_local_path]);
  document.getElementById('meta').innerHTML=M.map(x=>'<div class="mline"><span class="kv">'+esc(x[0])+'</span><span>'+esc(x[1])+'</span></div>').join('');
  const fl=document.getElementById('files');fl.innerHTML='';
  const D=dlLinks(j);
  if(D.length){for(const d of D){const li=document.createElement('li'),a=document.createElement('a');a.href=d.url;a.textContent=d.label+' — '+d.name;li.appendChild(a);fl.appendChild(li)}}
  else{const li=document.createElement('li');li.className='dim';li.textContent='（暂无文件）';fl.appendChild(li)}
  const qe=document.getElementById('queue');
  const q=j.queue||{},cs=q.counts||{};
  qe.textContent='排队中 '+(cs.queued||0)+' · 运行中 '+(cs.running||0)+' · 待确认 '+(cs.awaiting_confirm||0)+' · 已完成 '+(q.terminal||0)+(q.position?(' · 本任务排第 '+q.position+' 位'):'');
  const cb=document.getElementById('cancelBtn');
  if(TERMINAL.includes(j.status)){cb.style.display='none'}
  else{cb.style.display='inline-block';cb.disabled=false;cb.onclick=async()=>{if(!window.confirm('确定取消任务 '+JOB_ID+' 吗？'))return;cb.disabled=true;try{const r=await fetch('/jobs/'+encodeURIComponent(JOB_ID)+'/cancel?t='+encodeURIComponent(TOKEN),{method:'POST'});const jj=await r.json();if(r.status===403){eb('无权取消：令牌无效');cb.disabled=false;return}if(!r.ok||!jj.ok){eb('取消失败：'+((jj.error&&(jj.error.message||jj.error))||('HTTP '+r.status)));cb.disabled=false;return}eb('已发送取消请求，正在刷新…');rf()}catch(e){eb('网络错误：'+e.message);cb.disabled=false}}}
  const rb=document.getElementById('retryBtn');
  if(j.status==='failed'){rb.style.display='inline-block';rb.disabled=false;rb.onclick=async()=>{if(!window.confirm('确定重试任务 '+JOB_ID+' 吗？'))return;rb.disabled=true;try{const r=await fetch('/jobs/'+encodeURIComponent(JOB_ID)+'/retry?t='+encodeURIComponent(TOKEN),{method:'POST'});const jj=await r.json();if(r.status===403){eb('无权重试：令牌无效');rb.disabled=false;return}if(!r.ok||!jj.ok){eb('重试失败：'+((jj.error&&(jj.error.message||jj.error))||('HTTP '+r.status)));rb.disabled=false;return}eb('已重试，任务重新排队…');rf()}catch(e){eb('网络错误：'+e.message);rb.disabled=false}}}
  else{rb.style.display='none'}
  const ul=document.getElementById('log');ul.innerHTML='';
  for(const e of(j.log||[])){const li=document.createElement('li');li.innerHTML='<span class="ts">'+esc(fmt(e.ts))+'</span><span class="step">'+esc(e.step||'')+'</span><span class="lvl lvl-'+esc(e.level||'info')+'">'+esc(e.level||'')+'</span><span>'+esc(e.note||'')+'</span>';ul.appendChild(li)}
  if(!(j.log||[]).length){const li=document.createElement('li');li.textContent='（暂无日志）';ul.appendChild(li)}
  const res=document.getElementById('result');res.innerHTML='';const err=j.error||(j.result&&j.result.error);
  if(err)res.innerHTML='<div class="err">'+esc(err)+'</div>';
  else if(j.result&&Object.keys(j.result).length){let rhtml='';const shot=(typeof j.result==='object'&&j.result.viewport_name)?String(j.result.viewport_name).split(/[\\/]/).pop():'';if(shot)rhtml+='<a class="shotlink" href="/files/'+encodeURIComponent(shot)+'" target="_blank"><img class="shot" src="/files/'+encodeURIComponent(shot)+'" alt="viewport"></a>';const t2=typeof j.result==='string'?j.result:(j.result.message||JSON.stringify(j.result,null,2));rhtml+='<div class="ok">'+esc(t2)+'</div>';res.innerHTML=rhtml}
  else if(TERMINAL.includes(j.status))res.innerHTML='<div class="dim">（无结果）</div>';
  if(TERMINAL.includes(j.status))stop();
 }catch(e){eb('网络错误：'+e.message)}
}
rf();
</script>
</body>
</html>"""


def _render_job_page(job_id: str, token: str, data: dict[str, Any]) -> str:
    """渲染任务状态页：服务端出首帧，JS 每 2s 自动刷新到终态。"""
    return (
        _JOB_PAGE_TEMPLATE.replace("__JOB_ID__", json.dumps(job_id, ensure_ascii=False))
        .replace("__TOKEN__", json.dumps(token, ensure_ascii=False))
    )


@mcp.custom_route("/jobs/{job_id}/view", methods=["GET"])
async def job_view_http(request: Request) -> Response:
    """GET /jobs/{id}/view?t={token} — 浏览器状态页（自动刷新）。

    令牌由提交时返回的 status_url 携带；页面可查看详情/下载，并可通过
    页面按钮取消任务（POST /jobs/{id}/cancel 需携带同一令牌）。
    """
    job_id = request.path_params["job_id"]
    token = request.query_params.get("t") or ""
    try:
        data = job_manager.get_dict(job_id)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse("任务不存在", status_code=404)
    if not _token_matches(data.get("view_token") or "", token):
        return HTMLResponse("访问令牌无效", status_code=403)
    return HTMLResponse(_render_job_page(job_id, token, data))
