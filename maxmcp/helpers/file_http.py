"""HTTP download URL helpers for workspace + %TEMP%/3dsmax-mcp captures.

Kept separate from ``maxmcp.tools.files`` so viewport tools can attach
``download_url`` without a circular import through ``server`` tool registration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

_ILLEGAL_CHARS = set('<>:"/\\|?*')


def http_port() -> int:
    try:
        return int(os.environ.get("MCP_HTTP_PORT", "8000"))
    except ValueError:
        return 8000


def host_ip() -> str:
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


def base_url() -> str:
    return f"http://{host_ip()}:{http_port()}"


def sanitize_filename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    cleaned = "".join("_" if c in _ILLEGAL_CHARS else c for c in base).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"invalid filename: {name!r}")
    return cleaned


def is_under_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def download_roots(workspace_dir: Path | str, comms_dir: Path | str) -> list[str]:
    """Absolute roots searched for GET /files/{name} (realpath, de-duped)."""
    roots: list[str] = []
    for path in (workspace_dir, comms_dir, Path(comms_dir) / "payloads", Path(comms_dir) / "audit"):
        try:
            os.makedirs(path, exist_ok=True)
            real = os.path.realpath(str(path))
        except OSError:
            continue
        if real not in roots:
            roots.append(real)
    return roots


def resolve_download(
    name: str,
    *,
    workspace_dir: Path | str,
    comms_dir: Path | str,
) -> str:
    """Resolve a downloadable file under workspace or %TEMP%/3dsmax-mcp."""
    safe = sanitize_filename(name)
    last_error: Exception | None = None
    for root in download_roots(workspace_dir, comms_dir):
        try:
            target = os.path.realpath(os.path.join(root, safe))
        except OSError as exc:
            last_error = exc
            continue
        if not is_under_root(target, root):
            continue
        if os.path.isfile(target):
            return target
    if last_error is not None:
        raise ValueError(str(last_error)) from last_error
    raise FileNotFoundError(f"{safe!r} not found in workspace or %TEMP%/3dsmax-mcp")


def file_download_url(
    path: str | os.PathLike[str] | None,
    *,
    workspace_dir: Path | str,
    comms_dir: Path | str,
) -> str | None:
    """Return GET /files/{basename} when *path* is under a served root."""
    if not path:
        return None
    try:
        real = os.path.realpath(str(path))
    except OSError:
        return None
    if not os.path.isfile(real):
        return None
    for root in download_roots(workspace_dir, comms_dir):
        if is_under_root(real, root):
            return f"{base_url()}/files/{quote(os.path.basename(real), safe='')}"
    return None


def iter_served_files(
    workspace_dir: Path | str,
    comms_dir: Path | str,
) -> Iterable[dict]:
    """Unique basenames under all download roots (first root wins on clash)."""
    seen: set[str] = set()
    base = base_url()
    for root in download_roots(workspace_dir, comms_dir):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            if name in seen:
                continue
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            seen.add(name)
            yield {
                "name": name,
                "size": os.path.getsize(full),
                "local_path": full,
                "url": f"{base}/files/{quote(name, safe='')}",
                "root": root,
            }
