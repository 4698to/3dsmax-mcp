"""HTTP file transfer endpoints for the remote 3ds Max server.

3ds Max runs on a remote machine. This module exposes plain HTTP routes on the
same FastMCP server so the client can upload assets (textures, models, ...) into
the shared workspace and download Max-generated results (renders, exports, ...)
without base64 overhead through the MCP channel.

Served roots (GET ``/files/{name}``):
  1. ``WORKSPACE_DIR`` — uploads + optional shared ``[workspace]`` path
  2. ``COMMS_DIR`` (``%TEMP%/3dsmax-mcp``) — viewport/screen captures and spilled
     payloads, always available even when no shared workspace is configured
  3. ``COMMS_DIR/payloads`` — oversized tool-envelope binaries

Uploads still write only to ``WORKSPACE_DIR``. Max scripts consume returned
*local* absolute paths (the Max sandbox itself can only read local files).
"""

from __future__ import annotations

import json
import os
import uuid
from base64 import b64decode, b64encode
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..helpers.file_http import (
    base_url as _base_url,
    file_download_url as _file_download_url,
    iter_served_files,
    resolve_download,
    sanitize_filename,
)
from ..server import COMMS_DIR, WORKSPACE_DIR, mcp

#: Maximum multipart part size accepted for uploads (1 GB) - large EXR/FBX/ABC
#: assets must be transferable.
_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

#: Extensions that browsers can render inline; served without Content-Disposition
#: so a plain GET shows the image instead of forcing a download.
_INLINE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}


def _ensure_workspace() -> None:
    os.makedirs(WORKSPACE_DIR, exist_ok=True)


def _ensure_comms() -> None:
    os.makedirs(COMMS_DIR, exist_ok=True)
    os.makedirs(os.path.join(COMMS_DIR, "payloads"), exist_ok=True)


def _sanitize_filename(name: str) -> str:
    return sanitize_filename(name)


def _resolve_in_workspace(name: str) -> str:
    """Resolve a name inside WORKSPACE_DIR, rejecting path traversal."""
    root = os.path.realpath(str(WORKSPACE_DIR))
    target = os.path.realpath(os.path.join(root, os.path.basename(name)))
    if not (target == root or target.startswith(root + os.sep)):
        raise ValueError(f"invalid filename: {name!r}")
    return target


def _resolve_download(name: str) -> str:
    return resolve_download(name, workspace_dir=WORKSPACE_DIR, comms_dir=COMMS_DIR)


def file_download_url(path: str | os.PathLike[str] | None) -> str | None:
    """Return a GET /files/{basename} URL when *path* is under a served root."""
    return _file_download_url(path, workspace_dir=WORKSPACE_DIR, comms_dir=COMMS_DIR)


@mcp.custom_route("/files/upload", methods=["POST"])
async def upload_file(request: Request) -> Response:
    """POST a multipart form (field name: ``file``) to store it in the workspace.

    Returns JSON with the generated unique filename, size, the local absolute
    path (what Max scripts should use) and the download URL.
    """
    _ensure_workspace()
    form = await request.form(max_part_size=_MAX_UPLOAD_BYTES)
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "missing 'file' field"}, status_code=400)

    safe_name = _sanitize_filename(upload.filename or "unnamed")
    stored_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    dest = os.path.join(WORKSPACE_DIR, stored_name)

    with open(dest, "wb") as fh:
        fh.write(await upload.read())

    return JSONResponse(
        {
            "name": stored_name,
            "size": os.path.getsize(dest),
            "local_path": dest,
            "url": f"{_base_url()}/files/{quote(stored_name, safe='')}",
        }
    )


@mcp.custom_route("/files/{filename}", methods=["GET"])
async def download_file(request: Request) -> Response:
    """Download a file from the workspace or ``%TEMP%/3dsmax-mcp`` by basename."""
    try:
        path = _resolve_download(request.path_params["filename"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Serving with filename= sets Content-Disposition: attachment (download).
    # For viewable images omit it so the browser renders them inline.
    if os.path.splitext(path)[1].lower() in _INLINE_IMAGE_EXTS:
        return FileResponse(path)
    return FileResponse(path, filename=os.path.basename(path))


@mcp.custom_route("/files", methods=["GET"])
async def list_files(request: Request) -> Response:
    """List workspace + ``%TEMP%/3dsmax-mcp`` files.

    Default: HTML page with download links; append ``?format=json`` for JSON.
    """
    _ensure_workspace()
    _ensure_comms()
    entries = list(iter_served_files(WORKSPACE_DIR, COMMS_DIR))

    if request.query_params.get("format") == "json":
        return JSONResponse(
            {
                "workspace": str(WORKSPACE_DIR),
                "comms": str(COMMS_DIR),
                "files": entries,
            }
        )

    rows = "".join(
        f'<li><a href="/files/{quote(e["name"], safe="")}">{e["name"]}</a> '
        f'({e["size"]} bytes)</li>'
        for e in entries
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>3dsmax-mcp files</title></head><body>"
        f"<h1>3dsmax-mcp files</h1>"
        f"<p>workspace: {WORKSPACE_DIR}<br>comms: {COMMS_DIR}</p>"
        f"<ul>{rows or '<li>(empty)</li>'}</ul>"
        "</body></html>"
    )
    return HTMLResponse(html)


@mcp.tool()
def get_file_service_info() -> dict:
    """Describe the HTTP file transfer service for the remote 3ds Max server.

    Use this to get the base URL for uploading/downloading files and, more
    importantly, the ``workspace`` path: files uploaded through
    ``POST /files/upload`` land there, and Max scripts can read/write that
    path directly. When ``shared_configured`` is true, the path comes from
    max_instances.ini [workspace] (or MAXMCP_WORKSPACE) and must be writable
    by every Max host and the Python MCP host.

    ``comms`` (``%TEMP%/3dsmax-mcp``) is always served for viewport captures
    even when no shared workspace is configured. Important tool calls are
    audited under ``comms/audit``; optional ``user_id`` may be passed inside
    ``tools/call`` arguments (see docs/AUDIT.md).
    """
    from ..helpers.audit_log import audit_dir
    from ..helpers.file_http import download_roots
    from ..workspace_config import workspace_info

    _ensure_workspace()
    _ensure_comms()
    base = _base_url()
    info = workspace_info()
    audit_path = audit_dir()
    try:
        audit_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return {
        "base_url": base,
        "workspace": str(WORKSPACE_DIR),
        "comms": str(COMMS_DIR),
        "shared_configured": info["shared_configured"],
        "shared_workspace": info["shared_workspace"],
        "workspace_source": info["source"],
        "upload_url": f"{base}/files/upload",
        "list_url": f"{base}/files",
        "download_url_template": f"{base}/files/{{filename}}",
        "upload_method": "POST multipart form field 'file'",
        "download_roots": download_roots(WORKSPACE_DIR, COMMS_DIR),
        "audit": {
            "dir": str(audit_path),
            "enabled_env": "MAXMCP_AUDIT",
            "default_enabled": True,
            "user_id_argument": (
                "optional string in tools/call arguments (not in tool schemas); "
                "see docs/AUDIT.md"
            ),
            "fields": [
                "ts",
                "date",
                "user_id",
                "tool",
                "audit_reason",
                "scene_path",
                "ok",
                "elapsed_ms",
                "args",
                "error",
                "transport",
            ],
        },
    }


# ── stdio-compatible transfer tools (base64 over the MCP channel) ────────────


@mcp.tool()
def workspace_upload(file_name: str, data_b64: str) -> str:
    """Upload a file into the shared workspace (base64-encoded content).

    Saves the decoded bytes to ``workspace`` on the 3ds Max machine and
    returns the local absolute path, which Max scripts can read directly.
    Works over any transport (stdio or HTTP); use ``get_file_service_info``
    for the HTTP-only upload endpoint.

    Args:
        file_name: Target name in the workspace (basename is used).
        data_b64: Base64-encoded file content.
    """
    _ensure_workspace()
    safe = _sanitize_filename(file_name)
    try:
        raw = b64decode(data_b64, validate=True)
    except Exception as exc:
        raise ValueError(f"data_b64 is not valid base64: {exc}") from exc
    dest = os.path.join(WORKSPACE_DIR, safe)
    with open(dest, "wb") as fh:
        fh.write(raw)
    return json.dumps(
        {
            "name": safe,
            "size": len(raw),
            "local_path": dest,
            "url": f"{_base_url()}/files/{quote(safe, safe='')}",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def workspace_download(file_name: str) -> str:
    """Download a workspace or ``%TEMP%/3dsmax-mcp`` file as base64.

    Use this to fetch Max-generated results (renders, viewport captures, ...)
    back to the client. Looks up the basename under the shared workspace and
    under ``%TEMP%/3dsmax-mcp`` (and ``payloads/``).

    Args:
        file_name: Basename of the file.
    """
    try:
        path = _resolve_download(file_name)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise FileNotFoundError(str(exc)) from exc
    with open(path, "rb") as fh:
        raw = fh.read()
    return json.dumps(
        {
            "name": os.path.basename(path),
            "size": len(raw),
            "local_path": path,
            "data_b64": b64encode(raw).decode("ascii"),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def workspace_list_files() -> str:
    """List files in the shared workspace and ``%TEMP%/3dsmax-mcp``."""
    _ensure_workspace()
    _ensure_comms()
    entries = list(iter_served_files(WORKSPACE_DIR, COMMS_DIR))
    return json.dumps(
        {
            "workspace": str(WORKSPACE_DIR),
            "comms": str(COMMS_DIR),
            "files": entries,
        },
        ensure_ascii=False,
    )
