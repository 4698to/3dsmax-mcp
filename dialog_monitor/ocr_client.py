"""HTTP client for the remote PaddleOCR / PP-OCRv6 service."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_OCR_BASE = "http://192.168.139.130:8000"


class OcrError(RuntimeError):
    """OCR service request or response failure."""


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def health(ocr_base: str = DEFAULT_OCR_BASE, timeout: float = 10.0) -> dict[str, Any]:
    """GET /v1/ocr/health → status payload."""
    url = _join(ocr_base, "/v1/ocr/health")
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            import json

            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OcrError(f"OCR health HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise OcrError(f"OCR health unreachable at {url}: {exc}") from exc


def _image_to_data_uri(image: str | Path | bytes, mime: str | None = None) -> str:
    if isinstance(image, (str, Path)):
        path = Path(image)
        raw = path.read_bytes()
        if mime is None:
            guessed, _ = mimetypes.guess_type(str(path))
            mime = guessed or "image/png"
    else:
        raw = image
        mime = mime or "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def recognize(
    image: str | Path | bytes,
    *,
    ocr_base: str = DEFAULT_OCR_BASE,
    timeout: float = 60.0,
    mime: str | None = None,
) -> list[dict[str, Any]]:
    """POST /v1/ocr and return flattened lines: [{text, score, box}, ...]."""
    import json

    data_uri = _image_to_data_uri(image, mime=mime)
    payload = {
        "input": [
            {
                "type": "image_url",
                "image_url": {"url": data_uri},
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    url = _join(ocr_base, "/v1/ocr")
    req = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise OcrError(f"OCR HTTP {exc.code}: {err_body}") from exc
    except URLError as exc:
        raise OcrError(f"OCR unreachable at {url}: {exc}") from exc

    lines: list[dict[str, Any]] = []
    for item in result.get("data") or []:
        for line in item.get("lines") or []:
            text = line.get("text")
            if text is None:
                continue
            lines.append(
                {
                    "text": str(text),
                    "score": float(line.get("score") or 0.0),
                    "box": line.get("box") or [],
                }
            )
        # Fallback: split aggregated text when lines missing
        if not (item.get("lines")) and item.get("text"):
            for part in str(item["text"]).splitlines():
                part = part.strip()
                if part:
                    lines.append({"text": part, "score": 0.0, "box": []})
    return lines
