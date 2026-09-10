"""Composite workflow: open a remote .max file and fetch a viewport capture.

One MCP tool call for the common "upload my local .max -> load it in 3ds Max ->
show me what the viewport looks like" flow. Internally reuses the existing
primitive tools so the whole pipeline is CJK-safe (the original filename,
including Chinese characters and spaces, is preserved end-to-end).

The capture PNG is always placed in the shared workspace, so the client can
either decode ``png_b64`` (transport-agnostic, works over stdio too) or GET
``download_url`` over plain HTTP (fast path, no base64 overhead).
"""

import base64
import json
import os
import shutil
import time
from urllib.parse import quote

from ..server import WORKSPACE_DIR, mcp
from .files import _base_url, workspace_upload
from .scene_manage import load_scene
from .viewport import capture_viewport


@mcp.tool()
def upload_scene_and_capture(
    data_b64: str,
    file_name: str,
    max_width: int = 1600,
    return_png_b64: bool = True,
) -> dict:
    """Upload a .max file, load it in 3ds Max, and return a viewport capture.

    One-call pipeline for "open a local scene on the remote Max and see it":
    the bytes are uploaded into the shared workspace (original filename kept,
    including CJK/space characters), the scene is loaded (replacing the current
    one), and the viewport is captured after it redraws.

    The capture is copied into the workspace, so the client can fetch it either
    through ``result.download_url`` (plain HTTP GET, no base64 overhead) or by
    decoding ``result.png_b64`` (transport-agnostic — the only option when the
    server is reached over stdio, where the HTTP file routes do not exist).

    The file is always re-uploaded — the workspace is not treated as a cache.
    Capture retries internally while the freshly loaded viewport is still a
    tiny placeholder (unrendered), waiting a few seconds between attempts.

    Args:
        data_b64: Base64-encoded .max file content.
        file_name: Target filename in the workspace (basename is used).
        max_width: Downscale the capture so its width does not exceed this.
        return_png_b64: Also inline the capture as base64 in the result. Set to
            False to keep the response small when you plan to GET
            ``download_url`` instead (png_b64 will be "").

    Returns:
        Dict: {scene, file_name, local_path, upload_size, capture_file,
        capture_name, download_url, png_size, png_b64, attempts}.
    """
    # 1. Upload (original filename preserved, incl. CJK/space).
    up = json.loads(workspace_upload(file_name, data_b64))
    local_path = up["local_path"]

    # 2. Load the scene, then let the viewport redraw.
    scene_result = load_scene(local_path)
    time.sleep(3)

    # 3-4. Capture -> copy into workspace, retrying while the capture is still
    #      a tiny placeholder (viewport not yet rendered).
    capture_file = ""
    base = ""
    attempts = 0
    for attempt in range(1, 4):
        attempts = attempt
        capture = capture_viewport(source="auto", max_width=max_width)
        capture_file = capture["file"]
        base = os.path.basename(capture_file)
        dst = os.path.join(WORKSPACE_DIR, base)
        shutil.copyfile(capture_file, dst)
        if os.path.getsize(dst) >= 10_000:
            break
        time.sleep(4)  # placeholder still; wait for the viewport to redraw

    png_size = os.path.getsize(dst)
    png_b64 = ""
    if return_png_b64:
        with open(dst, "rb") as fh:
            png_b64 = base64.b64encode(fh.read()).decode("ascii")

    return {
        "scene": scene_result,
        "file_name": up["name"],
        "local_path": local_path,
        "upload_size": up["size"],
        "capture_file": capture_file,
        "capture_name": base,
        "download_url": f"{_base_url()}/files/{quote(base, safe='')}",
        "png_size": png_size,
        "png_b64": png_b64,
        "attempts": attempts,
    }
