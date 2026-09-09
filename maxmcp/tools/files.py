"""HTTP file transfer endpoints for the remote 3ds Max server.

3ds Max runs on a remote machine. This module exposes plain HTTP routes on the
same FastMCP server so the client can upload assets (textures, models, ...) into
the shared workspace and download Max-generated results (renders, exports, ...)
without base64 overhead through the MCP channel. All files live under
``%TEMP%/3dsmax-mcp/workspace``; Max scripts consume the returned *local*
absolute path directly (the Max sandbox itself can only read local files).
"""

import os
import uuid

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
        "workspace": WORKSPACE_DIR,  # local path on the 3ds Max server machine
        "upload_url": f"{base}/files/upload",
        "list_url": f"{base}/files",
        "download_url_template": f"{base}/files/{{filename}}",
        "upload_method": "POST multipart form field 'file'",
    }
