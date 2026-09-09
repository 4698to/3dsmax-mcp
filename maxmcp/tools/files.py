"""HTTP file transfer endpoints for the remote 3ds Max server.

3ds Max runs on a remote machine. This module exposes plain HTTP routes on the
same FastMCP server so the client can upload assets (textures, models, ...) into
the shared workspace and download Max-generated results (renders, exports, ...)
without base64 overhead through the MCP channel. All files live under
``%TEMP%/3dsmax-mcp/workspace``; Max scripts consume the returned *local*
absolute path directly (the Max sandbox itself can only read local files).
"""

import json
import os
import uuid
from base64 import b64decode, b64encode

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ..server import WORKSPACE_DIR, mcp

#: Maximum multipart part size accepted for uploads (1 GB) - large EXR/FBX/ABC
#: assets must be transferable.
_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

_ILLEGAL_CHARS = set('<>:"/\\|?*')

#: Extensions that browsers can render inline; served without Content-Disposition
#: so a plain GET shows the image instead of forcing a download.
_INLINE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}


def _ensure_workspace() -> None:
    os.makedirs(WORKSPACE_DIR, exist_ok=True)


def _sanitize_filename(name: str) -> str:
    """Keep only the basename and drop characters Windows forbids in paths."""
    base = os.path.basename(name.replace("\\", "/"))
    cleaned = "".join("_" if c in _ILLEGAL_CHARS else c for c in base).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"invalid filename: {name!r}")
    return cleaned


def _resolve_in_workspace(name: str) -> str:
    """Resolve a name inside WORKSPACE_DIR, rejecting path traversal."""
    root = os.path.realpath(WORKSPACE_DIR)
    target = os.path.realpath(os.path.join(root, os.path.basename(name)))
    if not (target == root or target.startswith(root + os.sep)):
        raise ValueError(f"invalid filename: {name!r}")
    return target


def _host_ip() -> str:
    """Best-effort LAN IP so remote clients can reach the file routes."""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def _base_url() -> str:
    return f"http://{_host_ip()}:8000"


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
            "url": f"{_base_url()}/files/{stored_name}",
        }
    )


@mcp.custom_route("/files/{filename}", methods=["GET"])
async def download_file(request: Request) -> Response:
    """Download a file from the workspace by name."""
    _ensure_workspace()
    try:
        path = _resolve_in_workspace(request.path_params["filename"])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Serving with filename= sets Content-Disposition: attachment (download).
    # For viewable images omit it so the browser renders them inline.
    if os.path.splitext(path)[1].lower() in _INLINE_IMAGE_EXTS:
        return FileResponse(path)
    return FileResponse(path, filename=os.path.basename(path))


@mcp.custom_route("/files", methods=["GET"])
async def list_files(request: Request) -> Response:
    """List workspace files. Default: HTML page with download links; append
    ``?format=json`` for a machine-readable listing."""
    _ensure_workspace()
    entries = []
    for name in sorted(os.listdir(WORKSPACE_DIR)):
        full = os.path.join(WORKSPACE_DIR, name)
        if os.path.isfile(full):
            entries.append(
                {
                    "name": name,
                    "size": os.path.getsize(full),
                    "url": f"{_base_url()}/files/{name}",
                }
            )

    if request.query_params.get("format") == "json":
        return JSONResponse({"files": entries})

    rows = "".join(
        f'<li><a href="/files/{name}">{name}</a> ({size} bytes)</li>'
        for name, size in [(e["name"], e["size"]) for e in entries]
    )
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>3dsmax-mcp workspace</title></head><body>"
        f"<h1>3dsmax-mcp workspace</h1><ul>{rows or '<li>(empty)</li>'}</ul>"
        "</body></html>"
    )
    return HTMLResponse(html)


@mcp.tool()
def get_file_service_info() -> dict:
    """Describe the HTTP file transfer service for the remote 3ds Max server.

    Use this to get the base URL for uploading/downloading files and, more
    importantly, the local ``workspace`` path on the server: files uploaded
    through ``POST /files/upload`` land there, and Max scripts can read/write
    that local path directly (the Max side cannot reach the client's disk).
    """
    _ensure_workspace()
    base = _base_url()
    return {
        "base_url": base,
        "workspace": str(WORKSPACE_DIR),  # local path on the 3ds Max server machine
        "upload_url": f"{base}/files/upload",
        "list_url": f"{base}/files",
        "download_url_template": f"{base}/files/{{filename}}",
        "upload_method": "POST multipart form field 'file'",
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
            "url": f"{_base_url()}/files/{safe}",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def workspace_download(file_name: str) -> str:
    """Download a workspace file as base64-encoded content.

    Use this to fetch Max-generated results (renders, exports, ...) back to
    the client. The file must already exist in ``workspace`` on the 3ds Max
    machine (Max scripts can write there directly).

    Args:
        file_name: Name of the file in the workspace.
    """
    _ensure_workspace()
    try:
        path = _resolve_in_workspace(file_name)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{file_name!r} not found in workspace {WORKSPACE_DIR}")
    with open(path, "rb") as fh:
        raw = fh.read()
    return json.dumps(
        {
            "name": os.path.basename(path),
            "size": len(raw),
            "data_b64": b64encode(raw).decode("ascii"),
        },
        ensure_ascii=False,
    )


@mcp.tool()
def workspace_list_files() -> str:
    """List the files currently stored in the shared workspace."""
    _ensure_workspace()
    entries = []
    for name in sorted(os.listdir(WORKSPACE_DIR)):
        full = os.path.join(WORKSPACE_DIR, name)
        if os.path.isfile(full):
            entries.append(
                {
                    "name": name,
                    "size": os.path.getsize(full),
                    "local_path": full,
                    "url": f"{_base_url()}/files/{name}",
                }
            )
    return json.dumps({"workspace": str(WORKSPACE_DIR), "files": entries}, ensure_ascii=False)
