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


def local_fallback_workspace() -> Path:
    """Per-machine temp workspace used only when no shared workspace is set."""
    return Path(tempfile.gettempdir()) / "3dsmax-mcp" / "workspace"


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
    effective = resolve_workspace_dir()
    return {
        "shared_configured": shared is not None,
        "shared_workspace": str(shared) if shared is not None else None,
        "effective_workspace": str(effective),
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
    """New PNG path under the shared workspace, or None if not configured.

    Returned with forward slashes for MAXScript. Caller must only use this when
    Max can write the same path (UNC/share mounted on the Max host).
    """
    shared = get_shared_workspace()
    if shared is None:
        return None
    ensure_workspace_dir(shared)
    name = f"{prefix}_{uuid4().hex}.png"
    return str((shared / name).resolve()).replace("\\", "/")
