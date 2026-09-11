"""Plugin dialog / menu capture + OCR click (standalone prototype)."""

from __future__ import annotations

from typing import Any

from .ocr_client import DEFAULT_OCR_BASE, health, recognize

__all__ = [
    "DEFAULT_OCR_BASE",
    "cleanup_goskin_lists",
    "click_dialog_button",
    "click_menu_path",
    "confirm_goskin_start",
    "ensure_goskin_ready",
    "health",
    "recognize",
    "recognize_dialog",
    "run_goskin_auto",
    "run_goskin_skin",
]


def __getattr__(name: str) -> Any:
    if name in {"click_dialog_button", "recognize_dialog", "click_menu_path"}:
        from . import click_button

        return getattr(click_button, name)
    if name in {
        "ensure_goskin_ready",
        "run_goskin_skin",
        "run_goskin_auto",
        "cleanup_goskin_lists",
        "detect_goskin_needs_cleanup",
        "confirm_goskin_start",
        "build_start_confirmation",
    }:
        from . import goskin_flow

        return getattr(goskin_flow, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name}")
