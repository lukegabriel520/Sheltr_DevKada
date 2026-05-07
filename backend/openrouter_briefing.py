"""OpenRouter-hosted LLM: concise English briefing grounded on Sheltr facts."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import math
import requests

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _clamp_home_briefing_en(text: str, *, max_sentences: int = 2, max_chars: int = 280) -> str:
    """Keep home briefing to a short mobile card: at most two sentences and a length cap."""
    s = (text or "").strip()
    if not s:
        return s
    parts = re.split(r"(?<=[.!?])\s+", s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return (s[: max_chars - 1] + "…") if len(s) > max_chars else s
    out = " ".join(parts[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


def fallback_home_briefing_en(facts: dict[str, Any]) -> str:
    """Deterministic two-sentence briefing when OpenRouter is unavailable (same length target as LLM)."""
    wx = facts.get("weather_summary") if isinstance(facts.get("weather_summary"), dict) else {}
    temp = wx.get("temperature_c")
    hum = wx.get("humidity_pct")
    rain = wx.get("precipitation_mm_now")
    bits: list[str] = []
    if isinstance(temp, (int, float)) and math.isfinite(float(temp)):
        bits.append(f"{float(temp):.1f}°C")
    if isinstance(hum, (int, float)) and math.isfinite(float(hum)):
        bits.append(f"{round(float(hum))}% humidity")
    if isinstance(rain, (int, float)) and math.isfinite(float(rain)):
        bits.append(f"{float(rain):.1f} mm rain")
    if bits:
        s1 = f"At your location: {', '.join(bits)}."
    else:
        s1 = "Weather-readings for this pin are not available yet."

    avg = facts.get("static_hazard_point_scores_sample_average")
    if isinstance(avg, (int, float)) and math.isfinite(float(avg)):
        a = float(avg)
        band = "lower" if a <= 0.4 else "moderate" if a <= 0.7 else "higher"
        risk = f"The flood model scores this area as {band} overlap risk for the active scenario."
    else:
        risk = "Flood-overlap scores are not available for this pin."

    evac_name = facts.get("nearest_open_evacuation_center_name")
    evac_km = facts.get("nearest_open_evacuation_center_km")
    extra = ""
    if (
        isinstance(evac_name, str)
        and evac_name.strip()
        and isinstance(evac_km, (int, float))
        and math.isfinite(float(evac_km))
    ):
        extra = f" Nearest open center is {evac_name.strip()} (~{float(evac_km):.2f} km)"

    s2 = risk.rstrip(".")
    if extra:
        s2 = f"{s2}{extra.strip()}."
    else:
        s2 = f"{s2}."

    return _clamp_home_briefing_en(f"{s1} {s2}")


def _call_openrouter_json(
    *,
    system: str,
    user_content: str,
    x_title: str,
) -> dict[str, Any]:
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    model = (
        os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini"
    ).strip()
    referer = (os.environ.get("OPENROUTER_HTTP_REFERER") or "https://sheltr.local").strip()

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": x_title,
        },
        json={
            "model": model,
            "temperature": 0.35,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=45,
    )
    resp.raise_for_status()
    body = resp.json()
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError("OpenRouter missing choices")

    content = (((choices[0].get("message") or {}).get("content")) or "").strip()

    parsed = _parse_json_response(content)
    if not parsed:
        raise RuntimeError(f"Could not parse model JSON from: {content[:500]}")

    en = _trim_to_three_sentences(parsed.get("en", "").strip())
    return {
        "en": en,
        "fil": "",
        "model": model,
        "raw_error": None,
    }


def slim_route_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop huge GeoJSON blobs; keep numerical context for grounding."""
    if not isinstance(payload, dict):
        return {}
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    stats = payload.get("route_stats") if isinstance(payload.get("route_stats"), dict) else {}
    allowed_route_keys = {
        "distance_km",
        "duration_minutes",
        "safety_score",
        "flood_risk",
        "flood_overlap_fraction",
        "routing_mode",
        "eta_model",
        "confidence_score",
        "confidence_label",
        "destination_center_elevation",
        "destination_center_name",
        "destination_center_capacity",
        "destination_center_is_open",
        "elevation_penalty",
        "route_min_elevation_m",
        "route_avg_elevation_m",
        "flood_scoring_available",
        "nearest_waterway",
    }
    allowed_stats_keys = {
        "routing_engine",
        "routing_mode",
        "flood_scenario",
        "flood_overlap_hit_count",
        "flood_overlap_samples",
        "flood_overlap_weighted_severity",
        "flood_overlap_max_severity_weight",
        "flood_overlap_var_counts",
        "flood_overlap_haz_counts",
        "confidence_score",
        "confidence_label",
        "confidence_components",
        "elevations_notes",
        "river_closest_sample_m",
        "river_median_min_distance_m",
        "waterway_near_route_fraction_50m",
        "waterway_near_route_fraction_100m",
        "waterway_near_route_fraction_150m",
        "waterways_loaded",
        "reroute_attempts",
        "reroute_max",
        "overlay_feature_count",
        "exclude_polygons_used",
        "weather_data_age_seconds",
        "overlay_data_age_seconds",
        "warnings_summary",
    }
    slim_route = {k: route[k] for k in allowed_route_keys if k in route}
    slim_stats = {k: stats[k] for k in allowed_stats_keys if k in stats}
    warn = payload.get("warning")
    slim: dict[str, Any] = {
        "route": slim_route,
        "route_stats": slim_stats,
        "status": payload.get("status"),
    }
    if isinstance(warn, str) and warn.strip():
        slim["warnings_summary"] = warn.strip()[:1200]
    return slim


def request_route_briefing(route_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Calls OpenRouter. Returns::
        { "en": str, "model": str, "raw_error": Optional[str] }
    """
    facts = slim_route_payload(route_payload)
    system = (
        "You write short, direct route assessments for people evacuating in Metro Manila. "
        "Use ONLY the JSON facts — never invent road names, closures, water depths, or orders not in the data. "
        "sentence 1: state plainly what the route numbers mean (safety score, flood overlap, distance). "
        "sentence 2: point out the single biggest specific risk or advantage drawn from the actual data values — "
        "if nearest_waterway has distance_m and direction use them; if routing_mode is degraded say the line is approximate. "
        "sentence 3: give ONE concrete, data-driven reason why the person should or should not take this route — "
        "base it entirely on the actual scores and status, not generic advice; "
        "only recommend following or avoiding if the numbers clearly support it. "
        "If destination_center_capacity is present, add a brief clause (still within the 3 sentences, same word cap) "
        "that it is the listed approximate shelter size in people, not live occupancy or available space right now — "
        "so it only hints how large the site is on paper. "
        "If destination_center_is_open is false, mention that the destination is listed closed. "
        "Do NOT mention PAGASA, MMDA, LGU, or barangay officials — that is obvious. "
        "Do NOT give generic preparedness tips. Keep total under 95 words."
    )
    user_content = (
        "Facts JSON:\n```json\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n```\n\n"
        "Reply with ONLY valid JSON on one line: "
        '{"en":"<3 concise English sentences as described>"} '
        "No markdown fences."
    )
    return _call_openrouter_json(
        system=system,
        user_content=user_content,
        x_title="Sheltr route briefing",
    )


def request_home_briefing(facts: dict[str, Any]) -> dict[str, Any]:
    """
    Home / current-location briefing (no planned road route JSON).
    """
    system = (
        "You write very short home-screen guidance for Metro Manila residents. "
        "There is NO turn-by-turn route in the facts unless explicitly present. "
        "Use ONLY the JSON facts — do not invent street names, landmarks, flood depth, or official orders. "
        "Sentence 1: state current conditions (temperature, humidity, rain) as plain facts. "
        "Sentence 2: state the flood-risk level from the facts in concrete terms; "
        "if nearest_open_evacuation_center_name is present, name it with distance_km; otherwise omit any shelter. "
        "Hard rules: exactly TWO English sentences; total under 240 characters; no bullet points; "
        "no third sentence; end each sentence with proper punctuation. "
        "Do not mention PAGASA, MMDA, barangay, LGU, or advisories — only state what the data shows."
    )
    user_content = (
        "Facts JSON:\n```json\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n```\n\n"
        'Reply with ONLY valid JSON on one line: {"en":"<exactly two concise English sentences as specified>"} '
        "No markdown fences."
    )
    raw = _call_openrouter_json(
        system=system,
        user_content=user_content,
        x_title="Sheltr home briefing",
    )
    en = raw.get("en")
    if isinstance(en, str):
        raw = {**raw, "en": _clamp_home_briefing_en(en)}
    return raw


def request_notification_briefing(facts: dict[str, Any]) -> dict[str, str]:
    """
    Generates a short, localized AI notification.
    """
    system = (
        "You write concise, calm evacuation push notifications for Metro Manila residents. "
        "Use ONLY the provided facts. Prioritize safety and practical preparedness. "
        "Always urge following PAGASA and LGU orders. "
        "Do not invent facts, weather conditions, or official orders. "
        "Output ONLY valid JSON matching this exact schema: "
        '{"title": "<max 5 words>", "message": "<max 15 words>", "fullMessage": "<1-3 sentences detailed advice>"}'
    )
    user_content = (
        "Facts JSON:\n```json\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n```\n\n"
        "Reply with ONLY valid JSON on one line. No markdown fences."
    )

    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    model = (os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini").strip()
    referer = (os.environ.get("OPENROUTER_HTTP_REFERER") or "https://sheltr.local").strip()

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": "Sheltr Notification",
        },
        json={
            "model": model,
            "temperature": 0.35,
            "max_tokens": 250,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=20,
    )
    resp.raise_for_status()
    choices = resp.json().get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter missing choices")

    content = (((choices[0].get("message") or {}).get("content")) or "").strip()
    cleaned = content
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return {
                "title": str(obj.get("title", "Sheltr Update")),
                "message": str(obj.get("message", "Monitor local updates.")),
                "fullMessage": str(obj.get("fullMessage", "Keep monitoring PAGASA and LGU channels.")),
            }
    except Exception:
        pass
    raise RuntimeError("Could not parse AI notification")


def _parse_json_response(text: str) -> dict[str, str] | None:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            en = obj.get("en") or obj.get("english")
            if isinstance(en, str):
                return {"en": en}
            if isinstance(obj.get("message"), str):
                return {"en": str(obj.get("message"))}
    except json.JSONDecodeError:
        pass
    return None


def _trim_to_three_sentences(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return content
    parts = re.split(r"(?<=[.!?])\s+", content)
    sentences = [p.strip() for p in parts if p and p.strip()]
    if len(sentences) <= 3:
        return content
    return " ".join(sentences[:3]).strip()
