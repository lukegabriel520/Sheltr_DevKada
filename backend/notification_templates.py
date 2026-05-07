"""Load Sheltr notification copy and risk thresholds from JSON (no hardcoded strings in safe_server)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "sheltr_notification_templates.json"


def _templates_path() -> Path:
    import os

    raw = (os.environ.get("SHELTR_NOTIFICATION_TEMPLATES_PATH") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (ROOT / p).resolve()
    return DEFAULT_PATH


def load_notification_templates() -> dict[str, Any]:
    path = _templates_path()
    if not path.is_file():
        raise FileNotFoundError(f"Missing notification templates: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Templates root must be an object")
    bands = data.get("risk_bands")
    if not isinstance(bands, list) or not bands:
        raise ValueError("Templates must include non-empty risk_bands")
    return data


def resolve_risk_band(avg_risk: float, templates: dict[str, Any]) -> dict[str, Any]:
    """Pick the highest band whose min_average_risk threshold is met (bands ordered high→low in file)."""
    bands = templates.get("risk_bands")
    if not isinstance(bands, list):
        raise ValueError("risk_bands invalid")
    ordered = sorted(
        [b for b in bands if isinstance(b, dict)],
        key=lambda b: float(b.get("min_average_risk", -1.0)),
        reverse=True,
    )
    for band in ordered:
        try:
            lo = float(band.get("min_average_risk", -1.0))
        except (TypeError, ValueError):
            continue
        if avg_risk >= lo:
            return band
    return ordered[-1]


def build_onboarding_items(templates: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    raw = templates.get("onboarding_when_no_risk_data")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        hours_ago = item.get("hours_ago")
        try:
            h = float(hours_ago) if hours_ago is not None else 0.0
        except (TypeError, ValueError):
            h = 0.0
        ts = (now - timedelta(hours=h)).isoformat()
        out.append(
            {
                "id": str(item.get("id", "onboarding")),
                "title": str(item.get("title", "")),
                "message": str(item.get("message", "")),
                "fullMessage": str(item.get("fullMessage", "")),
                "type": str(item.get("type", "safety_update")),
                "priority": str(item.get("priority", "low")),
                "timestamp": ts,
                "read": bool(item.get("read", False)),
            }
        )
    return out


def build_scripted_for_band(band: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    raw = band.get("scripted_notifications")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        m = item.get("minutes_ago")
        try:
            mins = float(m) if m is not None else 0.0
        except (TypeError, ValueError):
            mins = 0.0
        ts = (now - timedelta(minutes=mins)).isoformat()
        out.append(
            {
                "id": str(item.get("id", "scripted")),
                "title": str(item.get("title", "")),
                "message": str(item.get("message", "")),
                "fullMessage": str(item.get("fullMessage", "")),
                "type": str(item.get("type", "safety_update")),
                "priority": str(item.get("priority", "low")),
                "timestamp": ts,
                "read": bool(item.get("read", False)),
            }
        )
    return out


def risk_level_for_band(band: dict[str, Any]) -> str:
    return str(band.get("risk_level") or "low")


def ai_meta_for_band(band: dict[str, Any]) -> dict[str, str]:
    meta = band.get("ai_notification_meta")
    if not isinstance(meta, dict):
        return {"type": "safety_update", "priority": "low"}
    return {
        "type": str(meta.get("type", "safety_update")),
        "priority": str(meta.get("priority", "low")),
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
