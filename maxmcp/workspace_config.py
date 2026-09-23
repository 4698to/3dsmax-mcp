"""Shared workspace path from max_instances.ini / env.

When configured, every Max host and the Python MCP host must be able to
read/write the same directory (typically a UNC share). When not configured,
there is no shared workspace — captures stay on the Max machine's local temp
and cross-machine OCR/file handoff requires another transport.
"""

from __future__ import annotations

import configparser
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional
from uuid import uuid4

ENV_WORKSPACE = "MAXMCP_WORKSPACE"
INSTANCES_INI_NAME = "max_instances.ini"

logger = logging.getLogger(__name__)


def _instances_config_candidates() -> list[Path]:
    """Same search order as instance_manager (avoid circular import)."""
    paths: list[Path] = []
    env_path = os.environ.get("MAXMCP_INSTANCES_FILE", "").strip()
    if env_path:
        paths.append(Path(env_path))
    paths.append(Path.cwd() / INSTANCES_INI_NAME)
    pkg_dir = Path(__file__).resolve().parent
    paths.append(pkg_dir.parent / INSTANCES_INI_NAME)
    paths.append(pkg_dir / INSTANCES_INI_NAME)
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    paths.append(Path(local) / "3dsmax-mcp" / INSTANCES_INI_NAME)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _read_workspace_path_from_ini(path: Path) -> Optional[str]:
    """Return workspace path string from [workspace] path= / shared_path=."""
    parser = configparser.ConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return None
    except (OSError, configparser.Error, UnicodeError) as exc:
        logger.warning("Could not read workspace from %s: %s", path, exc)
        return None
    if not parser.has_section("workspace"):
        return None
    for key in ("path", "shared_path", "dir"):
        raw = parser.get("workspace", key, fallback="").strip()
        if raw:
            return raw
    return None


def discover_shared_workspace() -> Optional[Path]:
    """Resolve the shared workspace directory, or None if not configured.

    Priority:
      1. MAXMCP_WORKSPACE env (non-empty)
      2. First max_instances.ini in the search path with [workspace] path=
    """
    env = (os.environ.get(ENV_WORKSPACE) or "").strip()
    if env:
        return Path(env)

    for path in _instances_config_candidates():
        if not path.is_file():
            continue
        raw = _read_workspace_path_from_ini(path)
        if raw:
            logger.info("Shared workspace from %s: %s", path, raw)
            return Path(raw)
    return None


@lru_cache(maxsize=1)
def get_shared_workspace() -> Optional[Path]:
    """Cached shared workspace path, or None when no shared workspace exists."""
    return discover_shared_workspace()


def clear_workspace_cache() -> None:
    get_shared_workspace.cache_clear()
    shared_workspace_if_valid.cache_clear()


def local_comms_dir() -> Path:
    """Per-machine ``%TEMP%\\3dsmax-mcp`` (Max and Python each have their own)."""
    return Path(tempfile.gettempdir()) / "3dsmax-mcp"


def local_fallback_workspace() -> Path:
    """Per-machine temp workspace used only when no shared workspace is set."""
    return local_comms_dir() / "workspace"


@lru_cache(maxsize=1)
def shared_workspace_if_valid() -> Optional[Path]:
    """Configured shared workspace that exists and is writable, else None.

    Max should still *write* into ``%TEMP%\\3dsmax-mcp`` first (CJK UNC often
    breaks ``save bmp``). Callers then copy into this directory when valid.
    """
    shared = get_shared_workspace()
    if shared is None:
        return None
    try:
        shared.mkdir(parents=True, exist_ok=True)
        probe = shared / f".mcp_ws_probe_{uuid4().hex}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return shared
    except OSError as exc:
        logger.warning("Shared workspace not usable (%s): %s", shared, exc)
        return None


def copy_to_shared_if_valid(src: Path | str, dest_name: Optional[str] = None) -> Optional[Path]:
    """Copy *src* into the shared workspace when that path is valid.

    Returns the destination path, or None if not configured / not writable /
    source missing. Does not delete the original (Max local temp stays).
    """
    shared = shared_workspace_if_valid()
    if shared is None:
        return None
    source = Path(src)
    if not source.is_file():
        return None
    name = dest_name or source.name
    dest = shared / name
    try:
        import shutil

        shutil.copy2(source, dest)
        return dest
    except OSError as exc:
        logger.warning("Could not copy %s -> %s: %s", source, dest, exc)
        return None


def resolve_workspace_dir() -> Path:
    """Directory used by file-transfer tools.

    Shared workspace when configured; otherwise a local TEMP fallback
    (same-machine only — not a cross-host share).
    """
    shared = get_shared_workspace()
    if shared is not None:
        return shared
    return local_fallback_workspace()


def workspace_info() -> dict:
    """Structured status for tools / diagnostics."""
    shared = get_shared_workspace()
    valid = shared_workspace_if_valid() if shared is not None else None
    effective = resolve_workspace_dir()
    return {
        "shared_configured": shared is not None,
        "shared_valid": valid is not None,
        "shared_workspace": str(shared) if shared is not None else None,
        "effective_workspace": str(effective),
        "local_comms": str(local_comms_dir()),
        "source": (
            "MAXMCP_WORKSPACE"
            if (os.environ.get(ENV_WORKSPACE) or "").strip()
            else ("max_instances.ini" if shared is not None else "local_temp_fallback")
        ),
    }


def ensure_workspace_dir(path: Optional[Path] = None) -> Path:
    root = path if path is not None else resolve_workspace_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def next_shared_capture_path(prefix: str = "dialog_monitor") -> Optional[str]:
    """Destination PNG under a *valid* shared workspace, or None.

    Max should write the original into ``%TEMP%\\3dsmax-mcp`` then copy here.
    Returned with forward slashes for MAXScript.
    """
    shared = shared_workspace_if_valid()
    if shared is None:
        return None
    name = f"{prefix}_{uuid4().hex}.png"
    return str((shared / name).resolve()).replace("\\", "/")


# ---------------------------------------------------------------------------
# External OCR service (dialog_monitor / GoSkin)
# ---------------------------------------------------------------------------

ENV_OCR_BASE = "MAXMCP_OCR_BASE"
DEFAULT_OCR_BASE_FALLBACK = "http://192.168.139.130:8000"


def _read_ocr_base_from_ini(path: Path) -> Optional[str]:
    """Return OCR base URL from [ocr] base= / url= / endpoint=."""
    parser = configparser.ConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            return None
    except (OSError, configparser.Error, UnicodeError) as exc:
        logger.warning("Could not read OCR config from %s: %s", path, exc)
        return None
    if not parser.has_section("ocr"):
        return None
    for key in ("base", "url", "endpoint", "ocr_base"):
        raw = parser.get("ocr", key, fallback="").strip()
        if raw:
            return raw.rstrip("/")
    return None


def discover_ocr_base() -> tuple[str, str]:
    """Resolve OCR HTTP base URL and its source label.

    Priority:
      1. MAXMCP_OCR_BASE env (non-empty)
      2. First max_instances.ini with [ocr] base=
      3. Built-in default
    """
    env = (os.environ.get(ENV_OCR_BASE) or "").strip()
    if env:
        return env.rstrip("/"), ENV_OCR_BASE

    for path in _instances_config_candidates():
        if not path.is_file():
            continue
        raw = _read_ocr_base_from_ini(path)
        if raw:
            logger.info("OCR base from %s: %s", path, raw)
            return raw, str(path)

    return DEFAULT_OCR_BASE_FALLBACK, "builtin_default"


@lru_cache(maxsize=1)
def get_ocr_base() -> str:
    """Cached OCR service base URL (no trailing slash)."""
    base, _source = discover_ocr_base()
    return base


def clear_ocr_cache() -> None:
    get_ocr_base.cache_clear()


def ocr_info() -> dict:
    """Structured OCR endpoint status for tools / diagnostics."""
    base, source = discover_ocr_base()
    return {
        "ocr_base": base,
        "source": source,
        "health_url": f"{base}/v1/ocr/health",
        "recognize_url": f"{base}/v1/ocr",
    }