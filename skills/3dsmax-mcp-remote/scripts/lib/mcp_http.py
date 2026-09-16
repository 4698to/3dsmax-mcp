# -*- coding: utf-8 -*-
"""Stdlib HTTP client for 3dsmax-mcp streamable-http (no maxmcp / requests)."""

from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen


PROTOCOL_VERSION = "2025-03-26"


def resolve_mcp_url(cli_url: str | None = None) -> str:
    """Resolve MCP URL from --url or MAXMCP_URL. Never invent localhost."""
    raw = (cli_url or os.environ.get("MAXMCP_URL") or "").strip()
    if not raw:
        raise ValueError(
            "MCP URL not set. Pass --url or set MAXMCP_URL to the same "
            "streamable-http endpoint as the IDE MCP client (mcp.json). "
            "Do not invent http://127.0.0.1:8000/mcp unless that is configured."
        )
    return raw.rstrip("/")


def http_base_from_mcp_url(mcp_url: str) -> str:
    """http://host:8000/mcp -> http://host:8000"""
    parsed = urlparse(mcp_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid MAXMCP_URL: {mcp_url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


class McpHttpError(RuntimeError):
    pass


class McpHttpSession:
    """One MCP streamable-http session (initialize once, then tools/call)."""

    def __init__(
        self,
        mcp_url: str | None = None,
        *,
        client_name: str = "3dsmax-mcp-remote-skill",
        timeout: float = 600.0,
    ) -> None:
        try:
            self.mcp_url = resolve_mcp_url(mcp_url)
            self.http_base = http_base_from_mcp_url(self.mcp_url)
        except ValueError as exc:
            raise McpHttpError(str(exc)) from exc
        self.timeout = float(timeout)
        self.client_name = client_name
        self.session_id: str | None = None
        self._next_id = 1
        self._initialized = False

    def _alloc_id(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    def _post_rpc(self, payload: dict[str, Any]) -> Any:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        req = Request(self.mcp_url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid and not self.session_id:
                    self.session_id = sid
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise McpHttpError(f"HTTP {exc.code} from MCP: {detail[:500]}") from exc
        except URLError as exc:
            raise McpHttpError(f"MCP unreachable at {self.mcp_url}: {exc}") from exc

        if not raw.strip():
            return None
        for line in raw.splitlines():
            s = line.strip()
            if s.startswith(b"data:"):
                return json.loads(s[5:].strip().decode("utf-8"))
        return json.loads(raw.decode("utf-8"))

    def ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": self._alloc_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": "1.0"},
                },
            }
        )
        self._post_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.ensure_initialized()
        obj = self._post_rpc(
            {
                "jsonrpc": "2.0",
                "id": self._alloc_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if obj is None:
            raise McpHttpError(f"empty response for tools/call {name}")
        if "error" in obj:
            raise McpHttpError(f"JSON-RPC error for {name}: {obj['error']}")
        return unwrap_tool_result(obj)

    def upload_file_multipart(self, path: Path, *, field_name: str = "file") -> dict[str, Any]:
        """POST /files/upload (multipart). Returns JSON with name/local_path/url."""
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        boundary = f"----MaxMcpBoundary{uuid.uuid4().hex}"
        filename = path.name
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data = path.read_bytes()
        preamble = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode("utf-8")
        epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = preamble + data + epilogue
        url = urljoin(self.http_base + "/", "files/upload")
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise McpHttpError(f"upload HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise McpHttpError(f"upload unreachable: {exc}") from exc

    def download_url(self, url: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = Request(url, headers={"Accept": "*/*"}, method="GET")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                dest.write_bytes(resp.read())
        except HTTPError as exc:
            raise McpHttpError(f"download HTTP {exc.code}: {url}") from exc
        except URLError as exc:
            raise McpHttpError(f"download failed: {exc}") from exc
        return dest

    def download_file_name(self, file_name: str, dest: Path) -> Path:
        url = urljoin(self.http_base + "/", f"files/{file_name}")
        return self.download_url(url, dest)


def unwrap_tool_result(obj: dict[str, Any]) -> Any:
    """Unwrap tools/call JSON-RPC into structured tool payload."""
    res = obj.get("result")
    if not isinstance(res, dict):
        return res
    if res.get("structuredContent") is not None:
        return res["structuredContent"]
    content = res.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            text = first.get("text") or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return res


def coerce_payload(value: Any) -> Any:
    """If tool returned a JSON string (e.g. list_instances), parse it."""
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return value
    if isinstance(value, dict) and "result" in value and "ok" in value:
        inner = value.get("result")
        if isinstance(inner, str):
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                return value
        return value
    return value


def envelope_ok(value: Any) -> Any:
    """Return result from {ok, result} envelope or the value itself."""
    if isinstance(value, dict) and "ok" in value:
        if not value.get("ok"):
            raise McpHttpError(json.dumps(value, ensure_ascii=False)[:1000])
        return value.get("result", value)
    return value
