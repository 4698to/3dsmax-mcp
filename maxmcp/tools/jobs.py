# -*- coding: utf-8 -*-
"""GoSkin 任务队列的 MCP 工具与 HTTP 路由（docs/goskin-job-queue-design.md §8）。

- MCP 工具（主接口，Agent 首选）：submit/get/list/cancel/confirm 五个
  ``mcp.tool()``。owner 由 ``ctx.session`` 派生（仅本人可查询/取消/确认，
  管理员见 ``MAXMCP_JOB_ADMIN_IDS``）；
- HTTP 路由（参照 files.py 的 ``@mcp.custom_route``）：``POST /jobs``、
  ``GET /jobs/{id}``、``GET /jobs``、``POST /jobs/{id}/cancel``、
  ``POST /jobs/{id}/confirm``、``POST /jobs/{id}/retry``、浏览器管理台
  ``GET /jobs/admin``（查看/控制所有任务，需 ``MAXMCP_JOB_ADMIN_TOKEN``），
  owner 取 ``X-Client-Id`` 头，缺失视为匿名公共队列（受 ``MAXMCP_JOB_ANON_QUEUE_MAX``
  约束）；
- 审计 user_id：提交方（Agent）可显式传 ``user_id`` 参数（MCP 工具）或
  ``X-Maxmcp-User-Id`` 请求头（HTTP）。它**只记录到任务日志与 Job 字段**，
  用于追踪"谁提交的"，不参与 owner 鉴权。
"""

from __future__ import annotations

import asyncio
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
from ..instance_manager import InstanceReservedError, manager as instance_manager
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


@mcp.custom_route("/jobs/admin", methods=["GET"])
async def admin_view_http(request: Request) -> Response:
    """GET /jobs/admin?token=… — 浏览器任务管理台（查看/控制所有任务）。

    管理员令牌 = ``MAXMCP_JOB_ADMIN_TOKEN``（或 ``MAXMCP_JOB_ADMIN_IDS`` 中任一
    ID）。令牌经 ``?token=`` 查询参数或 ``mcp_job_admin_token`` Cookie 传入；
    页面 JS 用同一令牌作为 ``X-Client-Id`` 头调用 ``/jobs/*`` API（取消/重试/
    确认）。未配置任何管理员令牌时控制台不可用（403）。

    注意：本路由必须在 ``/jobs/{job_id}`` 之前注册，避免 ``admin`` 被当作
    job_id 匹配。
    """
    token = request.query_params.get("token") or ""
    cookie = request.cookies.get("mcp_job_admin_token") or ""
    if not (job_manager.is_admin(token) or job_manager.is_admin(cookie)):
        return HTMLResponse("管理令牌无效", status_code=403)
    resp = HTMLResponse(_ADMIN_PAGE_TEMPLATE)
    if token:
        # 首次带 token 打开时写入 Cookie，之后直接访问 /jobs/admin 即可。
        resp.set_cookie("mcp_job_admin_token", token, path="/", samesite="lax")
    return resp


@mcp.custom_route("/jobs/admin/instances", methods=["GET"])
async def admin_instances_http(request: Request) -> Response:
    """管理员专用：实例连通状态与占用情况。"""
    owner = _client_id(request)
    cookie = request.cookies.get("mcp_job_admin_token") or ""
    if not (job_manager.is_admin(owner) or job_manager.is_admin(cookie)):
        return JSONResponse({"ok": False, "error": "管理令牌无效"}, status_code=403)
    try:
        instances = await asyncio.to_thread(instance_manager.list_instances)
        queue_depth = instance_manager.queue_depth()
        queue_max = instance_manager.acquire_queue_max
    except Exception as exc:  # noqa: BLE001
        _log.exception("admin instance status failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse(
        {"instances": instances, "queue_depth": queue_depth, "queue_max": queue_max}
    )


_ADMIN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GoSkin 任务管理台</title>
<style>
body{margin:0;background:#0f172a;color:#e2e8f0;font:14px system-ui,'Segoe UI',sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 12px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.section-title{font-size:15px;margin:16px 0 8px}
.instances{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px;margin-bottom:16px}
.instance{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:10px 12px;min-width:0}
.instance-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}
.instance-name{font-weight:600;overflow-wrap:anywhere}
.instance-meta{color:#94a3b8;font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.instance-error{color:#fca5a5;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.stat{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:8px 14px;min-width:86px}
.stat b{display:block;font-size:20px;line-height:1.2}
.stat span{color:#94a3b8;font-size:12px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
select,input[type=text]{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 8px;font-size:13px}
button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer}
button:hover{background:#1d4ed8}
button:disabled{opacity:.5;cursor:not-allowed}
label.chk{font-size:13px;color:#94a3b8;display:flex;align-items:center;gap:5px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;color:#fff;font-size:12px;white-space:nowrap}
table{width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden}
th,td{padding:6px 10px;font-size:13px;border-bottom:1px solid #334155;text-align:left;vertical-align:middle}
th{color:#94a3b8;font-size:12px;letter-spacing:.3px;background:#0b1220}
tr:hover td{background:#24324a}
td.jid{font-family:Consolas,monospace;color:#93c5fd;font-size:12px}
.dim{color:#64748b}
.err{color:#f87171;white-space:pre-wrap}.ok{color:#4ade80;white-space:pre-wrap}
.op a,.op button{margin-right:6px}
a{color:#60a5fa;text-decoration:none}
a:hover{text-decoration:underline}
tr.detail{display:none}
tr.detail.open{display:table-row}
tr.detail td{background:#0f172a;padding:10px 14px}
ul.log{list-style:none;margin:0;padding:0;max-height:260px;overflow:auto}
ul.log li{font-size:13px;padding:3px 0;border-bottom:1px dashed #334155}
.ts{color:#64748b;margin-right:8px;font-family:Consolas,monospace}
.step{color:#93c5fd;margin-right:8px}
.lvl-error{color:#f87171}.lvl-warn{color:#fbbf24}
.files{list-style:none;margin:0;padding:0}
.files li{padding:2px 0;border-bottom:1px dashed #334155}
.kv{color:#94a3b8;display:inline-block;min-width:7em}
.mline{margin:2px 0}
#errbox{display:none;background:#450a0a;border:1px solid #dc2626;border-radius:8px;padding:10px 14px;margin-bottom:12px;color:#fecaca;font-size:13px}
.act-ok{background:#16a34a}.act-ok:hover{background:#15803d}
.act-danger{background:#dc2626}.act-danger:hover{background:#b91c1c}
.bar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}
</style>
</head>
<body>
<div class="wrap">
<div id="errbox"></div>
<h1>GoSkin 任务管理台</h1>
<div id="stats" class="stats"></div>
<h2 class="section-title">Max 实例</h2>
<div id="instances" class="instances"><div class="dim">加载中…</div></div>
<div class="filters">
<select id="fStatus"><option value="">全部状态</option><option value="queued">queued</option><option value="running">running</option><option value="awaiting_confirm">awaiting_confirm</option><option value="succeeded">succeeded</option><option value="failed">failed</option><option value="cancelled">cancelled</option></select>
<select id="fPriority"><option value="">全部优先级</option><option value="high">high</option><option value="normal">normal</option><option value="low">low</option></select>
<select id="fConfirm"><option value="">全部确认模式</option><option value="auto">auto</option><option value="manual">manual</option></select>
<input type="text" id="fOwner" placeholder="过滤 owner（精确）">
<input type="text" id="fKeyword" placeholder="过滤 job_id / 用户 / 实例">
<button id="btnGo">刷新</button>
<label class="chk"><input type="checkbox" id="chkAuto" checked>自动刷新(2s)</label>
</div>
<div class="bar"><button id="btnMore">加载更多</button><span id="count" class="dim"></span></div>
<table>
<thead><tr><th>状态</th><th>任务ID</th><th>优先级</th><th>确认</th><th>实例</th><th>owner</th><th>提交者</th><th>创建时间</th><th>耗时</th><th>重试</th><th>操作</th></tr></thead>
<tbody id="rows"></tbody>
</table>
</div>
<script>
const SS={queued:['排队中','#2563eb'],running:['运行中','#3b82f6'],awaiting_confirm:['待确认','#f59e0b'],succeeded:['成功','#16a34a'],failed:['失败','#dc2626'],cancelled:['已取消','#6b7280']};
const PR={high:['高','#dc2626'],normal:['中','#94a3b8'],low:['低','#64748b']};
const TERMINAL=['succeeded','failed','cancelled'];
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function fmt(ts){if(!ts)return'';const d=new Date(ts*1e3),p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds())}
function dur(j){const s=j.started_at,f=j.finished_at;if(!s)return'-';const t=(f||Date.now()/1e3)-s;if(t<60)return Math.round(t)+'s';if(t<3600)return (t/60).toFixed(1)+'m';return (t/3600).toFixed(1)+'h'}
function readCookie(n){const m=document.cookie.split('; ').find(r=>r.startsWith(n+'='));return m?decodeURIComponent(m.slice(n.length+1)):''}
let TOKEN=readCookie('mcp_job_admin_token')||new URLSearchParams(location.search).get('token')||'';
if(TOKEN){localStorage.setItem('mcp_job_admin_token',TOKEN);document.cookie='mcp_job_admin_token='+encodeURIComponent(TOKEN)+';path=/;SameSite=Lax'}
function B(id){return document.getElementById(id)}
function eb(m){const x=B('errbox');x.style.display='block';x.textContent=m}
function clearErr(){B('errbox').style.display='none'}
async function api(path,opts){
 opts=opts||{};opts.headers=Object.assign({'X-Client-Id':TOKEN},opts.headers||{});
 const r=await fetch(path,opts);let j={};
 try{j=await r.json()}catch(e){}
 if(r.status===403){eb('管理令牌无效：请用 /jobs/admin?token=… 打开');throw new Error('403')}
 return {http:r.status,ok:r.ok,data:j}
}
async function loadInstances(){
 const box=B('instances');
 try{
  const r=await api('/jobs/admin/instances');
  if(r.http!==200){box.innerHTML='<div class="instance-error">实例状态加载失败 HTTP '+esc(r.http)+'</div>';return}
  INSTANCES=r.data.instances||[];
  renderInstances(r.data);
 }catch(e){box.innerHTML='<div class="instance-error">实例状态加载失败：'+esc(e.message||e)+'</div>'}
}
function renderInstances(data){
 const box=B('instances'),items=data.instances||[];
 let out='<div class="instance-meta" style="grid-column:1/-1">实例等待队列：'+esc(data.queue_depth||0)+' / '+esc(data.queue_max==null?'-':data.queue_max)+'</div>';
 if(!items.length){box.innerHTML=out+'<div class="instance"><div class="instance-meta">未发现 Max 实例</div></div>';return}
 for(const i of items){
  const online=i.online===true?['在线','#16a34a']:i.online===false?['离线','#dc2626']:['未知','#64748b'];
  const busy=i.busy?'占用中':'空闲';
  out+='<div class="instance"><div class="instance-head"><span class="instance-name">'+esc(i.name)+'</span><span class="badge" style="background:'+online[1]+'">'+online[0]+'</span></div>'+
   '<div class="instance-meta">'+esc(i.host)+':'+esc(i.port)+' · '+busy+' · 池 '+esc(i.pool||'shared')+'</div>'+
   '<div class="instance-meta">PID '+esc(i.pid==null?'-':i.pid)+' · Max '+esc(i.max_version||'未知')+' · 连接 '+esc((i.transports||[]).join(', ')||'无')+'</div>'+
   '<div class="instance-meta">租约 '+esc(i.lease_kind||'-')+(i.busy?' · 持有 '+esc(i.locked_for_seconds||0)+' 秒':'')+' · '+(i.pinned?'固定配置':'自动发现')+'</div>'+
   (i.online_error?'<div class="instance-error">'+esc(i.online_error)+'</div>':'')+'</div>';
 }
 box.innerHTML=out;
}
let ALL=[],STATS=null,EXP={},limit=500,autoOn=true;
async function load(){
 try{
  const qs=new URLSearchParams();qs.set('limit',String(limit));
  const st=B('fStatus').value;if(st)qs.set('status',st);
  const ow=B('fOwner').value.trim();if(ow)qs.set('owner',ow);
  const r=await api('/jobs?'+qs.toString());
  if(r.http!==200){eb('列表查询失败 HTTP '+r.http);return}
  clearErr();ALL=r.data.jobs||[];STATS=r.data.stats||null;
  if(STATS){const box=B('stats');box.innerHTML='';
   const cards=[['排队中',STATS.queued||0,'#2563eb'],['运行中',STATS.running||0,'#3b82f6'],['待确认',STATS.awaiting_confirm||0,'#f59e0b'],['已完成',STATS.terminal||0,'#16a34a'],['并发上限',STATS.max_concurrent==null?'-':STATS.max_concurrent,'#64748b']];
   for(const c of cards){const d=document.createElement('div');d.className='stat';d.innerHTML='<b style="color:'+c[2]+'">'+esc(c[1])+'</b><span>'+esc(c[0])+'</span>';box.appendChild(d)}
  }
  render();
 }catch(e){}
}
function filt(){
 const kw=B('fKeyword').value.trim().toLowerCase(),pr=B('fPriority').value,cm=B('fConfirm').value;
 return ALL.filter(j=>{
  if(pr&&j.priority!==pr)return false;
  if(cm&&j.confirm_mode!==cm)return false;
  if(!kw)return true;
  const hay=String(j.job_id+' '+(j.owner||'')+' '+(j.user_id||'')+' '+(j.instance||'')).toLowerCase();
  return hay.indexOf(kw)>=0;
 });
}
function badge(st){const s=SS[st]||[st,'#2563eb'];return '<span class="badge" style="background:'+s[1]+'">'+esc(s[0])+'</span>'}
function renderRes(j){const err=j.error||(j.result&&j.result.error);if(err)return'<div class="err">'+esc(err)+'</div>';if(j.result&&Object.keys(j.result).length){let r='';const shot=(typeof j.result==='object'&&j.result.viewport_name)?String(j.result.viewport_name).split(/[\\/]/).pop():'';if(shot)r+='<a href="/files/'+encodeURIComponent(shot)+'" target="_blank"><img src="/files/'+encodeURIComponent(shot)+'" alt="viewport" style="max-width:100%;max-height:360px;border:1px solid #334155;border-radius:8px;display:block;margin-bottom:8px"></a>';const t=typeof j.result==='string'?j.result:(j.result.message||JSON.stringify(j.result,null,2));return r+'<div class="ok">'+esc(t)+'</div>'}return'<div class="dim">（无结果）</div>'}
function renderFiles(j){
 const L=[];
 const LABELS={output_file:'蒙皮结果',viewport_file:'视口截图'};
 if(j.scene_local_path){const n=String(j.scene_local_path).split(/[\\/]/).pop();if(n)L.push('<li><a href="/files/'+encodeURIComponent(n)+'">场景文件 — '+esc(n)+'</a></li>')}
 const r=j.result||{};
 if(r&&typeof r==='object')for(const k of Object.keys(r)){
  const v=r[k];if(typeof v!=='string'||!v)continue;
  let url=null;
  if(new RegExp('^https?://','i').test(v))url=v;
  else if(new RegExp('\\.(max|fbx|png|jpe?g|bmp|tga|exr|json|txt)$','i').test(v)){const n=v.split(/[\\/]/).pop();if(n)url='/files/'+encodeURIComponent(n)}
  if(url)L.push('<li><a href="'+esc(url)+'">'+esc(LABELS[k]||k)+' — '+esc(v.split(/[\\/]/).pop()||k)+'</a></li>');
 }
 return L.join('');
}
function render(){
 const rows=filt().slice().sort((a,b)=>(b.created_at||0)-(a.created_at||0));
 B('count').textContent='显示 '+rows.length+' / 共 '+ALL.length+' 条（当前上限 '+limit+'）';
 const tb=B('rows');tb.innerHTML='';
 for(const j of rows){
  const tr=document.createElement('tr');
  let ops='';
  if(!TERMINAL.includes(j.status))ops+='<button class="act-danger" data-a="cancel" data-id="'+esc(j.job_id)+'">取消</button>';
  if(j.status==='failed')ops+='<button class="act-ok" data-a="retry" data-id="'+esc(j.job_id)+'">重试</button>';
  if(j.status==='awaiting_confirm')ops+='<button class="act-ok" data-a="confirm" data-id="'+esc(j.job_id)+'">确认</button>';
  ops+='<button data-a="detail" data-id="'+esc(j.job_id)+'">详情</button>';
  if(j.view_token)ops+='<a href="/jobs/'+encodeURIComponent(j.job_id)+'/view?t='+encodeURIComponent(j.view_token)+'" target="_blank">状态页</a>';
  const p=PR[j.priority]||['-','#64748b'];
  tr.innerHTML='<td>'+badge(j.status)+'</td><td class="jid" title="'+esc(j.job_id)+'">'+esc(j.job_id.slice(0,8))+'</td><td><span class="badge" style="background:'+p[1]+'">'+esc(p[0])+'</span></td><td>'+esc(j.confirm_mode||'-')+'</td><td>'+esc(j.instance||'-')+'</td><td class="dim">'+esc(j.owner||'-')+'</td><td class="dim">'+esc(j.user_id||'-')+'</td><td>'+fmt(j.created_at)+'</td><td>'+dur(j)+'</td><td>'+(j.retries||0)+'</td><td class="op">'+ops+'</td>';
  tb.appendChild(tr);
  if(EXP[j.job_id]){
   const d=document.createElement('tr');d.className='detail open';
   d.innerHTML='<td colspan="11"><div class="mline"><span class="kv">实例</span>'+esc(j.instance||'-')+'</div><div class="mline"><span class="kv">场景</span>'+esc(j.scene_local_path||'-')+'</div><div class="mline"><span class="kv">网格</span>'+esc((j.mesh_names||[]).join(', ')||'-')+'</div><div class="mline"><span class="kv">骨骼</span>'+esc((j.bone_names||[]).join(', ')||'-')+'</div><div class="mline"><span class="kv">模式</span>'+esc(j.debug?'DEBUG 模拟':'真实')+'</div><div class="mline"><span class="kv">排位</span>'+(j.queue_position==null?'-':j.queue_position)+'</div><h3>日志</h3><ul class="log">'+((j.log&&j.log.length)?j.log.map(e=>'<li><span class="ts">'+esc(fmt(e.ts))+'</span><span class="step">'+esc(e.step||'')+'</span><span class="lvl lvl-'+esc(e.level||'info')+'">'+esc(e.level||'')+'</span><span>'+esc(e.note||'')+'</span></li>').join(''):'<li class="dim">（暂无日志）</li>')+'</ul><h3>结果</h3><div>'+renderRes(j)+'</div><h3>下载</h3><ul class="files">'+(renderFiles(j)||'<li class="dim">（暂无文件）</li>')+'</ul></td>';
   tb.appendChild(d);
  }
 }
}
document.addEventListener('click',async ev=>{
 const b=ev.target.closest('button');if(!b)return;
 const a=b.dataset.a,id=b.dataset.id;if(!a)return;
 if(a==='detail'){
  if(EXP[id]){delete EXP[id]}
  else{EXP[id]=1;if(!DET||!DET[id]){try{const r=await api('/jobs/'+encodeURIComponent(id));if(r.http===200){DET[id]=r.data;const j=ALL.find(x=>x.job_id===id);if(j)Object.assign(j,r.data)}}catch(e){}}}
  render();return;
 }
 if(!id)return;
 const label={cancel:'取消任务 '+id+' 吗？',retry:'重试任务 '+id+' 吗？',confirm:'确认「开始蒙皮」 '+id+' 吗？'}[a];
 if(!window.confirm(label))return;
 b.disabled=true;
 try{
  const r=await api('/jobs/'+encodeURIComponent(id)+'/'+a,{method:'POST'});
  if(!r.ok||!r.data.ok){eb('操作失败：'+((r.data.error&&(r.data.error.message||r.data.error))||('HTTP '+r.http)));return}
  clearErr();await load();
 }catch(e){b.disabled=false}
});
B('btnGo').onclick=()=>{limit=500;load()};
B('btnMore').onclick=()=>{limit+=500;load()};
B('chkAuto').onchange=e=>{autoOn=e.target.checked};
B('fStatus').onchange=B('fOwner').oninput=B('fKeyword').oninput=B('fPriority').onchange=B('fConfirm').onchange=()=>render();
setInterval(()=>{if(autoOn&&document.hasFocus())load()},2e3);
setInterval(()=>{if(autoOn&&document.hasFocus())loadInstances()},1e4);
load();loadInstances();
</script>
</body>
</html>"""


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
    payload: dict[str, Any] = {"jobs": items, "count": len(items)}
    if is_admin:
        payload["stats"] = job_manager.stats()
    return JSONResponse(payload)


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
 const LABELS={output_file:'蒙皮结果',viewport_file:'视口截图'};
 if(j.scene_local_path){const n=String(j.scene_local_path).split(/[\\/]/).pop();if(n)L.push({label:'场景文件',name:n,url:'/files/'+encodeURIComponent(n)})}
 const r=j.result||{};
 if(r&&typeof r==='object')for(const k of Object.keys(r)){
  const v=r[k];if(typeof v!=='string'||!v)continue;
  let url=null;
  if(new RegExp('^https?://', 'i').test(v))url=v;
  else if(new RegExp('\\.(max|fbx|png|jpe?g|bmp|tga|exr|json|txt)$', 'i').test(v)){const n=v.split(/[\\/]/).pop();if(n)url='/files/'+encodeURIComponent(n)}
  if(url)L.push({label:(LABELS[k]||k),name:v.split(/[\\/]/).pop()||k,url:url});
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
