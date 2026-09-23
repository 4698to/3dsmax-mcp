"""Audit log for important MCP tool calls.

Writes one JSONL line per audited tool into ``%TEMP%/3dsmax-mcp/audit/YYYYMMDD.jsonl``.
Optional ``user_id`` is bound via ContextVar (stripped from tool kwargs by the
structured-tool wrapper). Scene path is cached and refreshed best-effort.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_USER_ID: ContextVar[str | None] = ContextVar("maxmcp_audit_user_id", default=None)
_SCENE_PATH: ContextVar[str | None] = ContextVar("maxmcp_audit_scene_path", default=None)
_SCENE_PATH_KNOWN: ContextVar[bool] = ContextVar("maxmcp_audit_scene_known", default=False)

_USER_ID_MAX = 128
_SCENE_PATH_MAX = 512
_ARG_STR_MAX = 512
_REDACT_KEYS = frozenset(
    {
        "data_b64",
        "png_b64",
        "user_id",
        "password",
        "token",
        "secret",
        "api_key",
    }
)


def audit_dir() -> Path:
    root = Path(os.environ.get("MAXMCP_COMMS_DIR") or (Path(tempfile.gettempdir()) / "3dsmax-mcp"))
    return root / "audit"


def audit_enabled() -> bool:
    raw = (os.environ.get("MAXMCP_AUDIT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def normalize_user_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _USER_ID_MAX:
        text = text[:_USER_ID_MAX]
    return text


def bind_user_id(value: Any) -> Token:
    return _USER_ID.set(normalize_user_id(value))


def reset_user_id(token: Token) -> None:
    _USER_ID.reset(token)


def get_user_id() -> str | None:
    return _USER_ID.get()


def note_scene_path(path: str | None) -> None:
    """Update the cached scene path (call after load_scene / manage_scene)."""
    if path is None or not str(path).strip():
        _SCENE_PATH.set(None)
        _SCENE_PATH_KNOWN.set(True)
        return
    text = str(path).replace("\\", "/").strip()
    if len(text) > _SCENE_PATH_MAX:
        text = text[:_SCENE_PATH_MAX]
    _SCENE_PATH.set(text)
    _SCENE_PATH_KNOWN.set(True)


def clear_scene_path_cache() -> None:
    _SCENE_PATH.set(None)
    _SCENE_PATH_KNOWN.set(False)


def get_cached_scene_path() -> str | None:
    if not _SCENE_PATH_KNOWN.get():
        return None
    return _SCENE_PATH.get()


def summarize_args(args: tuple[Any, ...] | list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe argument summary; redacts secrets and truncates long strings."""
    out: dict[str, Any] = {}
    if args:
        out["_args"] = [_summarize_value(v) for v in args]
    for key, value in kwargs.items():
        low = str(key).lower()
        if low in _REDACT_KEYS or low.endswith("_b64") or low.endswith("_base64"):
            out[str(key)] = {"redacted": True, "size": _value_size(value)}
            continue
        out[str(key)] = _summarize_value(value)
    return out


def _value_size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return len(str(value))


def _summarize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _ARG_STR_MAX:
            return value[:_ARG_STR_MAX] + f"...({len(value)} chars)"
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"type": "bytes", "size": len(value)}
    if isinstance(value, dict):
        return summarize_args((), {str(k): v for k, v in list(value.items())[:40]})
    if isinstance(value, (list, tuple)):
        items = list(value)
        head = [_summarize_value(v) for v in items[:20]]
        if len(items) > 20:
            head.append(f"...(+{len(items) - 20})")
        return head
    text = repr(value)
    if len(text) > _ARG_STR_MAX:
        return text[:_ARG_STR_MAX] + "..."
    return text


def is_audited(tool_name: str) -> bool:
    try:
        from maxmcp.server import _AUDIT_TOOLS
    except Exception:
        return False
    return tool_name in _AUDIT_TOOLS


def audit_reason_for(tool_name: str) -> str:
    try:
        from maxmcp.server import _AUDIT_EXTRA, _DESTRUCTIVE_TOOLS
    except Exception:
        return "extra"
    if tool_name in _DESTRUCTIVE_TOOLS:
        return "destructive"
    if tool_name in _AUDIT_EXTRA:
        return "extra"
    return "extra"


def resolve_scene_path(*, query_max: bool = True) -> str | None:
    """Return cached scene path, optionally refreshing from Max once."""
    if _SCENE_PATH_KNOWN.get():
        return _SCENE_PATH.get()
    if not query_max:
        return None
    path = _query_scene_path_from_max()
    note_scene_path(path)
    return path


def _query_scene_path_from_max() -> str | None:
    try:
        from maxmcp.server import client
    except Exception:
        return None
    script = """(
        local p = maxFilePath
        local n = maxFileName
        if p == undefined or n == undefined or n == "" then ""
        else (p as string) + (n as string)
    )"""
    try:
        response = client.send_command(script, timeout=2.0)
        raw = response.get("result", "")
        text = str(raw).strip().strip('"')
        if not text:
            return None
        return text.replace("\\", "/")
    except Exception:
        return None


def audit_maybe(
    tool_name: str,
    args: tuple[Any, ...] | list[Any],
    kwargs: dict[str, Any],
    envelope: dict[str, Any] | None,
    *,
    transport: dict[str, Any] | None = None,
) -> Path | None:
    """Append one audit line when *tool_name* is audited. Never raises."""
    if not audit_enabled() or not tool_name or not is_audited(tool_name):
        return None
    try:
        now = datetime.now().astimezone()
        date_s = now.strftime("%Y-%m-%d")
        record: dict[str, Any] = {
            "ts": now.isoformat(timespec="milliseconds"),
            "date": date_s,
            "user_id": get_user_id(),
            "tool": tool_name,
            "audit_reason": audit_reason_for(tool_name),
            "scene_path": resolve_scene_path(query_max=True),
            "ok": bool(envelope.get("ok")) if isinstance(envelope, dict) else None,
            "elapsed_ms": (envelope or {}).get("elapsed_ms") if isinstance(envelope, dict) else None,
            "args": summarize_args(tuple(args), dict(kwargs)),
            "error": None,
            "transport": _slim_transport(transport or (envelope or {}).get("transport")),
        }
        if isinstance(envelope, dict) and envelope.get("ok") is False:
            err = envelope.get("error")
            if isinstance(err, dict):
                record["error"] = {
                    "code": err.get("code"),
                    "message": _summarize_value(err.get("message")),
                }
            else:
                record["error"] = _summarize_value(err)

        path = audit_dir() / f"{now.strftime('%Y%m%d')}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)
        return None


def _slim_transport(transport: Any) -> dict[str, Any] | None:
    if not isinstance(transport, dict) or not transport:
        return None
    slim: dict[str, Any] = {}
    for key in (
        "transport",
        "target_pid",
        "target_pipe",
        "target_source",
        "pinned",
        "fallback_error",
    ):
        if key in transport and transport[key] is not None:
            slim[key] = transport[key]
    return slim or None
