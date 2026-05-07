"""
Sheltr API — Valhalla (Stadia Maps) routing with flood-hazard overlay scoring,
alternates selection, optional exclusions, Supabase-backed shelters, caching, and limits.
"""

from __future__ import annotations

import gzip
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from centers_provider import CentersProvider
from flood_index import FloodHazardIndex
from notification_templates import (
    ai_meta_for_band,
    build_onboarding_items,
    build_scripted_for_band,
    load_notification_templates,
    resolve_risk_band,
    risk_level_for_band,
    utc_now,
)
from openrouter_briefing import (
    fallback_home_briefing_en,
    request_home_briefing,
    request_notification_briefing,
    request_route_briefing,
)
from river_proximity import waterways_metadata
from valhalla_stadia import get_stadia_api_key
from wagamama import (
    build_route_response,
    flood_overlay_snapshot,
    resolve_effective_user_location,
    weather_snapshot,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

app = Flask(__name__)
# Strip whitespace from JSON responses; combined with after_request gzip this
# meaningfully reduces bandwidth for /evacuation-centers, /route, and similar.
try:
    app.json.compact = True  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
CORS(app)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[os.environ.get("SHELTR_DEFAULT_RATE", "180 per minute")],
    storage_uri=os.environ.get("SHELTR_LIMITER_STORAGE", "memory://"),
)

BUILD_REVISION = os.environ.get("SHELTR_BUILD_REVISION", "dev")
CENTER_PHOTOS_DIR = Path(
    os.environ.get("SHELTR_CENTER_PHOTOS_DIR", str(ROOT / "assets" / "evacuation-centers"))
)
CENTER_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SOS_SESSION_TTL_MINUTES = int(os.environ.get("SHELTR_SOS_SESSION_TTL_MINUTES", "120"))
SOS_SESSIONS: dict[str, dict[str, Any]] = {}


# --- Evacuation centers CSV (fallback; Supabase preferred via CentersProvider) ---
evac_centers_csv = pd.DataFrame()
csv_paths = [
    ROOT / "data" / "evacuation_centers.csv",
    Path("data/evacuation_centers.csv"),
    Path(__file__).resolve().parent / "data" / "evacuation_centers.csv",
]
for csv_path in csv_paths:
    try:
        if csv_path.is_file():
            evac_centers_csv = pd.read_csv(csv_path)
            logger.info("Loaded %s evacuation centers from %s", len(evac_centers_csv), csv_path)
            break
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read %s: %s", csv_path, e)

if evac_centers_csv.empty:
    logger.warning("No evacuation CSV; Supabase or empty responses only.")

if not evac_centers_csv.empty:
    evac_centers_csv = evac_centers_csv.dropna(subset=["latitude", "longitude"])
    evac_centers_csv["latitude"] = pd.to_numeric(evac_centers_csv["latitude"], errors="coerce")
    evac_centers_csv["longitude"] = pd.to_numeric(evac_centers_csv["longitude"], errors="coerce")
    evac_centers_csv = evac_centers_csv.dropna(subset=["latitude", "longitude"])

centers_provider = CentersProvider(evac_centers_csv)

# --- Flood layer ---
_flood_path = os.environ.get("FLOOD_GEOJSON_PATH") or str(ROOT / "data" / "MetroManila_Flood_25year.json")
flood_index = FloodHazardIndex(_flood_path)

try:
    flood_index.available()
except Exception as e:  # noqa: BLE001
    logger.warning("Flood index preload failed: %s", e)

_notification_templates_cache: dict[str, Any] | None = None


def _notification_templates() -> dict[str, Any]:
    """Cached JSON templates; set SHELTR_NOTIFICATION_TEMPLATES_HOT_RELOAD=1 to reread on each request."""
    global _notification_templates_cache
    if os.environ.get("SHELTR_NOTIFICATION_TEMPLATES_HOT_RELOAD", "").strip().lower() in ("1", "true", "yes"):
        return load_notification_templates()
    if _notification_templates_cache is None:
        _notification_templates_cache = load_notification_templates()
    return _notification_templates_cache


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cleanup_sos_sessions(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    ttl = timedelta(minutes=max(1, SOS_SESSION_TTL_MINUTES))
    stale: list[str] = []
    for sid, item in SOS_SESSIONS.items():
        updated_raw = item.get("updated_at") or item.get("created_at")
        try:
            updated_at = datetime.fromisoformat(str(updated_raw))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            stale.append(sid)
            continue
        if current - updated_at > ttl:
            stale.append(sid)
    for sid in stale:
        SOS_SESSIONS.pop(sid, None)


def _sos_public_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": item.get("session_id"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "rescue_url": item.get("rescue_url"),
        "hotline_name": item.get("hotline_name"),
        "hotline_number": item.get("hotline_number"),
        "rescuee": item.get("rescuee"),
        "rescuer": item.get("rescuer"),
    }


@app.before_request
def _assign_request_id() -> None:
    g.request_id = str(uuid.uuid4())


# Gzip-compressible JSON content types and a minimum payload size (bytes) below
# which compression is not worth the CPU/latency. Tuned for Railway free tier:
# CPU is cheap relative to outbound bandwidth, so we compress most JSON >1 KB.
_GZIP_MIN_BYTES = 1024
_GZIP_CONTENT_TYPES = (
    "application/json",
    "application/geo+json",
    "text/plain",
    "text/html",
    "text/css",
    "application/javascript",
)


def _maybe_gzip_response(response):
    """Compress JSON-ish payloads when the client advertises gzip support.

    Skips:
    - already-encoded responses (e.g. binary images, partial content)
    - small bodies where the gzip framing overhead dominates
    - direct-passthrough or streaming responses
    """
    try:
        if response.direct_passthrough:
            return response
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if response.headers.get("Content-Encoding"):
            return response
        accept = (request.headers.get("Accept-Encoding") or "").lower()
        if "gzip" not in accept:
            return response
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith(_GZIP_CONTENT_TYPES):
            return response
        data = response.get_data()
        if not data or len(data) < _GZIP_MIN_BYTES:
            return response
        compressed = gzip.compress(data, compresslevel=5)
        if len(compressed) >= len(data):
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        vary = response.headers.get("Vary")
        if not vary:
            response.headers["Vary"] = "Accept-Encoding"
        elif "accept-encoding" not in vary.lower():
            response.headers["Vary"] = f"{vary}, Accept-Encoding"
    except Exception:  # noqa: BLE001
        # Never let compression break a response.
        return response
    return response


@app.after_request
def _trace_headers(response):
    response.headers["X-Sheltr-Request-Id"] = getattr(g, "request_id", "")
    response.headers["X-Sheltr-Build"] = BUILD_REVISION
    return _maybe_gzip_response(response)


_API_KEY_PROTECTED_PATHS = frozenset(
    {
        "/route",
        "/route-briefing",
        "/home-briefing",
        "/flood-risk",
        "/risk",
        "/risk-context",
        "/weather",
        "/notifications",
        "/flood-overlay",
        "/sos-session",
        "/sos-session/heartbeat",
    }
)


def _require_api_key() -> None:
    expected = os.environ.get("SHELTR_API_KEY")
    if not expected:
        return
    path = request.path or ""
    if path not in _API_KEY_PROTECTED_PATHS and not path.startswith("/sos-session/"):
        return
    got = (request.headers.get("X-Sheltr-Key") or "").strip()
    auth = request.headers.get("Authorization") or ""
    if not got and auth.lower().startswith("bearer "):
        got = auth.split(" ", 1)[1].strip()
    if got != expected:
        abort(401)


def validate_coordinates(lat: float, lng: float) -> bool:
    return (
        14.0 <= lat <= 15.2
        and 120.0 <= lng <= 122.0
        and isinstance(lat, (int, float))
        and isinstance(lng, (int, float))
    )


def sanitize_input(data: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, float) and math.isnan(value):
            sanitized[key] = None
            continue
        if isinstance(value, str):
            sanitized[key] = re.sub(r'[<>"\']', "", value.strip())
        else:
            sanitized[key] = value
    return sanitized


def _finite_or_none(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


_DEFAULT_UNLABELED_EDGE_RISK = 0.5


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _risk_with_unlabeled_fallback(risk: float | None) -> float:
    if risk is None:
        return _DEFAULT_UNLABELED_EDGE_RISK
    return _clamp01(risk)


def _fetch_open_meteo_current(lat: float, lng: float, timeout: int = 12) -> dict[str, Any]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        "&current=temperature_2m,relative_humidity_2m,precipitation"
        "&hourly=precipitation_probability,precipitation"
        "&forecast_hours=6"
    )
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    cur = data.get("current") or {}
    hourly = data.get("hourly") or {}
    probs = hourly.get("precipitation_probability") or []
    next_prob = float(probs[0]) if probs and isinstance(probs[0], (int, float)) else None
    return {
        "temperature_c": cur.get("temperature_2m"),
        "humidity_pct": cur.get("relative_humidity_2m"),
        "precipitation_mm": cur.get("precipitation"),
        "next_hour_precip_probability_pct": next_prob,
        "source": "open_meteo",
    }


def _build_combined_hint(*, hazard_avg: float | None, weather: dict[str, Any]) -> str:
    next_prob = weather.get("next_hour_precip_probability_pct")
    rain_score = 0.0
    if isinstance(next_prob, (int, float)):
        rain_score = max(rain_score, float(next_prob) / 100.0)

    if hazard_avg is None:
        return (
            "Hazard map data is not loaded on this server, so Sheltr cannot score flood-prone areas here. "
            "Follow PAGASA and your LGU for official warnings."
        )

    if hazard_avg >= 0.55 and rain_score >= 0.45:
        return (
            "Heavy rain is in the forecast and you appear near mapped flood-prone areas. "
            "Avoid low-lying roads and follow official advisories."
        )
    if hazard_avg >= 0.55:
        return (
            "You appear near mapped flood-prone areas. "
            "Plan routes carefully and avoid crossing floodwater; this is not a live inundation map."
        )
    if rain_score >= 0.55:
        return (
            "Rain is likely soon. Streets can flood quickly in Metro Manila; "
            "check the nearest evacuation centers and official advisories even if hazard scores look moderate."
        )
    return (
        "Conditions look relatively calmer in this quick check, but weather changes fast. "
        "Keep monitoring PAGASA and local government channels during storms."
    )


def _is_open_center(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "f", "no", "closed")
    return True


def _normalize_center_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    return token


def _extract_photo_order(stem_token: str) -> int:
    m = re.search(r"[_-](\d{1,2})$", stem_token)
    if not m:
        return 999
    try:
        return int(m.group(1))
    except ValueError:
        return 999


# --- Center-photo index cache -------------------------------------------------
# `/evacuation-centers` returns ~50 rows; the previous implementation called
# `Path.iterdir()` for every row -> O(rows*files) disk scans per request.
# Build the index once and refresh only when the directory mtime changes.
_PHOTO_INDEX_LOCK = threading.Lock()
_PHOTO_INDEX_CACHE: dict[str, list[tuple[int, str]]] = {}
_PHOTO_INDEX_DIR_KEY: tuple[str, float, int] | None = None
# Cap cache freshness checks to 5s in case the directory is missing/unstable.
_PHOTO_INDEX_RECHECK_SECONDS = float(
    os.environ.get("SHELTR_CENTER_PHOTOS_RECHECK_S", "5")
)
_PHOTO_INDEX_LAST_CHECK: float = 0.0


def _photo_dir_signature() -> tuple[str, float, int] | None:
    try:
        if not CENTER_PHOTOS_DIR.is_dir():
            return None
        st = CENTER_PHOTOS_DIR.stat()
        return (str(CENTER_PHOTOS_DIR), st.st_mtime, getattr(st, "st_ino", 0))
    except OSError:
        return None


def _photo_index() -> dict[str, list[tuple[int, str]]]:
    """Return a cached map of `normalized_token -> [(order, filename), ...]`."""
    global _PHOTO_INDEX_CACHE, _PHOTO_INDEX_DIR_KEY, _PHOTO_INDEX_LAST_CHECK
    now = time.time()
    if (
        _PHOTO_INDEX_DIR_KEY is not None
        and (now - _PHOTO_INDEX_LAST_CHECK) < _PHOTO_INDEX_RECHECK_SECONDS
    ):
        return _PHOTO_INDEX_CACHE
    sig = _photo_dir_signature()
    with _PHOTO_INDEX_LOCK:
        _PHOTO_INDEX_LAST_CHECK = now
        if sig == _PHOTO_INDEX_DIR_KEY and _PHOTO_INDEX_CACHE:
            return _PHOTO_INDEX_CACHE
        if sig is None:
            _PHOTO_INDEX_DIR_KEY = None
            _PHOTO_INDEX_CACHE = {}
            return _PHOTO_INDEX_CACHE
        index: dict[str, list[tuple[int, str]]] = {}
        try:
            for path in CENTER_PHOTOS_DIR.iterdir():
                if not path.is_file():
                    continue
                if path.suffix.lower() not in CENTER_PHOTO_EXTENSIONS:
                    continue
                stem_token = _normalize_center_token(path.stem)
                if not stem_token:
                    continue
                index.setdefault(stem_token, []).append(
                    (_extract_photo_order(stem_token), path.name)
                )
        except OSError as exc:
            logger.warning("Could not scan center photos directory: %s", exc)
            return _PHOTO_INDEX_CACHE
        for token, items in index.items():
            items.sort(key=lambda x: (x[0], x[1].lower()))
        _PHOTO_INDEX_CACHE = index
        _PHOTO_INDEX_DIR_KEY = sig
        return _PHOTO_INDEX_CACHE


def _center_photo_urls(center_name: Any) -> list[str]:
    if not isinstance(center_name, str) or not center_name.strip():
        return []
    base = _normalize_center_token(center_name)
    if not base:
        return []
    index = _photo_index()
    if not index:
        return []
    matches: list[tuple[int, str]] = []
    base_prefix_us = f"{base}_"
    base_prefix_dash = f"{base}-"
    for token, items in index.items():
        if token == base or token.startswith(base_prefix_us) or token.startswith(base_prefix_dash):
            matches.extend(items)
    if not matches:
        return []
    matches.sort(key=lambda x: (x[0], x[1].lower()))
    host = request.host_url.rstrip("/")
    return [f"{host}/assets/evac-centers/{quote(name)}" for _, name in matches[:2]]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _list_evacuation_centers() -> list[dict[str, Any]]:
    df = centers_provider.get_dataframe()
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        row = sanitize_input(row)
        lat = _finite_or_none(row.get("latitude"))
        lng = _finite_or_none(row.get("longitude"))
        if lat is None or lng is None or not validate_coordinates(lat, lng):
            continue
        row["latitude"] = lat
        row["longitude"] = lng
        row["photos"] = _center_photo_urls(row.get("name"))
        out.append(row)
    return out


def _nearest_evacuation_center(latitude: float, longitude: float, *, open_only: bool = True) -> dict[str, Any] | None:
    centers = _list_evacuation_centers()
    best: dict[str, Any] | None = None
    best_km = float("inf")
    for center in centers:
        if open_only and not _is_open_center(center.get("is_open")):
            continue
        lat = float(center["latitude"])
        lng = float(center["longitude"])
        distance_km = haversine_km(latitude, longitude, lat, lng)
        if distance_km < best_km:
            best = center
            best_km = distance_km
    if best is None:
        return None
    return {**best, "distance": round(best_km, 3)}


def _flood_risk_samples(latitude: float, longitude: float) -> list[dict[str, Any]]:
    offsets = [
        (0.0, 0.0),
        (0.002, 0.0),
        (-0.002, 0.0),
        (0.0, 0.002),
        (0.0, -0.002),
    ]
    results = []
    layer_ok = flood_index.available()
    for dlat, dlng in offsets:
        hazard_prob = flood_index.point_unsafe_probability(longitude + dlng, latitude + dlat) if layer_ok else None
        prob = _risk_with_unlabeled_fallback(hazard_prob)
        results.append(
            {
                "lat": latitude + dlat,
                "lon": longitude + dlng,
                "pred_prob_unsafe": prob,
                "hazard_data_status": "ok" if layer_ok else "unavailable",
                "ml_smoothing_applied": False,
            }
        )
    return results


def _average_flood_risk(samples: list[dict[str, Any]]) -> float | None:
    values = [
        float(item["pred_prob_unsafe"])
        for item in samples
        if isinstance(item.get("pred_prob_unsafe"), (int, float))
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _home_briefing_facts(latitude: float, longitude: float) -> dict[str, Any]:
    """Small factual JSON for the home-screen LLM briefing (OpenRouter)."""
    wx = weather_snapshot(latitude, longitude)
    slim_wx: dict[str, Any] = {
        "temperature_c": wx.get("temperature"),
        "humidity_pct": wx.get("humidity"),
        "precipitation_mm_now": wx.get("precipitation"),
        "rain_3h_mm": wx.get("rain_3h"),
        "rain_24h_mm": wx.get("rain_24h"),
        "flood_map_label": wx.get("flood_map_label"),
        "overlay_var_level": wx.get("overlay_var_level"),
        "storm_surge_active": wx.get("storm_surge_active"),
        "storm_surge_severity": wx.get("storm_surge_severity"),
    }
    samples = _flood_risk_samples(latitude, longitude)
    avg = _average_flood_risk(samples)
    nearest = _nearest_evacuation_center(latitude, longitude, open_only=True)

    evac_km = None
    evac_name = None
    if nearest is not None:
        evac_km = nearest.get("distance")
        evac_name = nearest.get("name")

    facts: dict[str, Any] = {
        "screen": "home",
        "location": {"latitude": latitude, "longitude": longitude},
        "weather_summary": slim_wx,
        "static_hazard_layer_available": flood_index.available(),
        "static_hazard_point_scores_sample_average": round(avg, 4) if avg is not None else None,
        "static_hazard_note": (
            "Point scores derive from Sheltr hazard polygons sampled near the pin; "
            "not measured street flood depth."
        ),
        "nearest_open_evacuation_center_km": evac_km,
        "nearest_open_evacuation_center_name": evac_name,
    }

    wf = wx.get("weather_fetched_at")
    if isinstance(wf, str) and wf.strip():
        facts["weather_fetched_at"] = wf.strip()[:48]
    w_age = wx.get("weather_data_age_seconds")
    if isinstance(w_age, (int, float)) and math.isfinite(float(w_age)):
        facts["weather_data_age_seconds"] = round(float(w_age), 2)

    return facts


def _notifications_payload(latitude: float, longitude: float) -> dict[str, Any]:
    now = utc_now()
    try:
        templates = _notification_templates()
    except Exception as exc:  # noqa: BLE001
        logger.error("Notification templates unavailable: %s", exc)
        return {"items": [], "risk_level": None, "average_risk": None, "templates_error": str(exc)[:200]}

    try:
        samples = _flood_risk_samples(latitude, longitude)
        avg_risk = _average_flood_risk(samples)
        if avg_risk is None:
            return {
                "items": build_onboarding_items(templates, now),
                "risk_level": None,
                "average_risk": None,
            }
        band = resolve_risk_band(float(avg_risk), templates)
        risk_level = risk_level_for_band(band)
        ai_meta = ai_meta_for_band(band)

        ai_notification = None
        if os.environ.get("OPENROUTER_API_KEY"):
            try:
                wx = {}
                try:
                    wx = _fetch_open_meteo_current(latitude, longitude)
                except Exception as we:
                    logger.warning("Open-Meteo failed for notifications: %s", we)
                facts = {
                    "average_flood_risk_score": round(float(avg_risk), 4),
                    "risk_level": risk_level,
                    "weather": wx,
                    "note": "Not live inundation. Provide practical guidance.",
                }
                res = request_notification_briefing(facts)
                ai_notification = {
                    "id": "ai_local_report",
                    "title": res["title"],
                    "message": res["message"],
                    "fullMessage": res["fullMessage"],
                    "type": ai_meta["type"],
                    "priority": ai_meta["priority"],
                    "timestamp": now.isoformat(),
                    "read": False,
                }
            except Exception as e:
                logger.warning("AI notification generation failed: %s", e)

        items = build_scripted_for_band(band, now)
        if ai_notification:
            items = [ai_notification, *items]

        return {
            "items": items,
            "risk_level": risk_level,
            "average_risk": round(float(avg_risk), 4),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notifications payload fell back to onboarding: %s", exc)
        return {
            "items": build_onboarding_items(templates, now),
            "risk_level": None,
            "average_risk": None,
        }


def _parse_lat_lng() -> tuple[float, float]:
    try:
        latitude = float(request.args.get("latitude", ""))
        longitude = float(request.args.get("longitude", ""))
    except ValueError as exc:
        raise ValueError("Invalid latitude/longitude") from exc
    if not validate_coordinates(latitude, longitude):
        raise ValueError("Coordinates outside supported Metro Manila bbox")
    return latitude, longitude


def _parse_open_only() -> bool:
    raw = (request.args.get("open_only") or "1").strip().lower()
    return raw not in ("0", "false", "no")


@app.route("/health", methods=["GET"])
@limiter.exempt
def health_check():
    stadia = bool(get_stadia_api_key())
    flood_ok = flood_index.available()
    fm = flood_index.metadata()
    df = centers_provider.get_dataframe()
    centers_count = int(len(df)) if not df.empty else 0
    supabase_configured = bool(
        (os.environ.get("SUPABASE_URL") or os.environ.get("EXPO_PUBLIC_SUPABASE_URL"))
        and (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY")
            or os.environ.get("EXPO_PUBLIC_SUPABASE_ANON_KEY")
        )
    )
    routing_engine_available = stadia
    shelters_available = centers_count > 0
    healthy = routing_engine_available and shelters_available and flood_ok
    degraded: list[str] = []
    if not routing_engine_available:
        degraded.append("routing_engine_unavailable_straight_line_only")
    if not flood_ok:
        degraded.append("flood_scoring_unavailable")
    if not shelters_available:
        degraded.append("evacuation_centers_unavailable")
    if supabase_configured and evac_centers_csv.empty and centers_count == 0:
        degraded.append("supabase_configured_but_empty")

    centers_meta = centers_provider.cache_metadata()
    wm = waterways_metadata()
    body: dict[str, Any] = {
        "healthy": healthy,
        "status": "healthy" if healthy else "degraded",
        "routing_engine_available": routing_engine_available,
        "flood_scoring_available": flood_ok,
        "stadia_configured": stadia,
        "flood_layer_loaded": flood_ok,
        "evacuation_centers": centers_count,
        "supabase_configured": supabase_configured,
        "degraded": degraded,
        "build_revision": BUILD_REVISION,
        "api_version": 2,
        "flood_feature_count": fm.get("flood_feature_count"),
        "flood_data_revision": fm.get("flood_data_revision"),
        "flood_geojson_path": fm.get("flood_geojson_path"),
        "flood_layer_error": fm.get("flood_layer_error"),
        "centers_cache": centers_meta,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "waterways_index": wm,
        "openrouter_configured": bool((os.environ.get("OPENROUTER_API_KEY") or "").strip()),
    }
    return jsonify(body)


@app.route("/evacuation-centers", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_EVAC", "120 per minute"))
def get_evacuation_centers():
    _require_api_key()
    try:
        centers = _list_evacuation_centers()
        if not centers:
            return jsonify({"error": "Evacuation centers data not available"}), 503
        return jsonify(centers)
    except Exception as e:  # noqa: BLE001
        logger.exception("evacuation-centers: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/nearest-evacuation", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_NEAREST_EVAC", "120 per minute"))
def get_nearest_evacuation():
    _require_api_key()
    try:
        latitude, longitude = _parse_lat_lng()
        effective_lat, effective_lng = resolve_effective_user_location(latitude, longitude)
        center = _nearest_evacuation_center(effective_lat, effective_lng, open_only=_parse_open_only())
        if center is None:
            return jsonify({"error": "No evacuation center found"}), 404
        center = {
            **center,
            "requested_latitude": latitude,
            "requested_longitude": longitude,
            "effective_latitude": effective_lat,
            "effective_longitude": effective_lng,
        }
        return jsonify(center)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("nearest-evacuation: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/flood-risk", methods=["GET"])
@app.route("/risk", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_FLOOD", "90 per minute"))
def flood_risk():
    _require_api_key()
    try:
        latitude, longitude = _parse_lat_lng()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_flood_risk_samples(latitude, longitude))


@app.route("/risk-context", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_RISK", "90 per minute"))
def risk_context():
    _require_api_key()
    try:
        lat, lng = _parse_lat_lng()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    probs: list[float | None] = []
    if flood_index.available():
        for dlat, dlng in ((0, 0), (0.002, 0), (-0.002, 0), (0, 0.002), (0, -0.002)):
            p = flood_index.point_unsafe_probability(lng + dlng, lat + dlat)
            probs.append(p)
    hazard_avg = None
    numeric = [p for p in probs if isinstance(p, (int, float))]
    if numeric:
        hazard_avg = sum(float(x) for x in numeric) / len(numeric)

    try:
        weather = _fetch_open_meteo_current(lat, lng)
    except Exception as e:  # noqa: BLE001
        logger.warning("Open-Meteo failed: %s", e)
        weather = {"source": "open_meteo", "error": "unavailable"}

    hint = _build_combined_hint(hazard_avg=hazard_avg, weather=weather)
    return jsonify(
        {
            "hazard_point_average": hazard_avg,
            "hazard_data_available": flood_index.available(),
            "weather": weather,
            "combined_hint": hint,
            "not_official_alert": True,
        }
    )


@app.route("/weather", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_WEATHER", "120 per minute"))
def weather_proxy():
    _require_api_key()
    try:
        latitude, longitude = _parse_lat_lng()
        return jsonify(weather_snapshot(latitude, longitude))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("weather: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/flood-overlay", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_FLOOD_OVERLAY", "30 per minute"))
def get_flood_overlay():
    _require_api_key()
    try:
        lat_raw = (request.args.get("latitude") or "").strip()
        lng_raw = (request.args.get("longitude") or "").strip()
        scenario = (request.args.get("scenario") or "").strip() or None
        lat = _finite_or_none(lat_raw) if lat_raw else None
        lng = _finite_or_none(lng_raw) if lng_raw else None
        if (lat is None) != (lng is None):
            return jsonify({"error": "Both latitude and longitude are required when passing location"}), 400
        if lat is not None and lng is not None and not validate_coordinates(lat, lng):
            return jsonify({"error": "Coordinates outside supported Metro Manila bbox"}), 400
        snapshot = flood_overlay_snapshot(latitude=lat, longitude=lng, scenario_override=scenario)
        return jsonify(snapshot)
    except Exception as e:  # noqa: BLE001
        logger.exception("flood-overlay: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/assets/evac-centers/<path:filename>", methods=["GET"])
@limiter.exempt
def get_evac_center_photo(filename: str):
    safe_name = Path(filename).name
    ext = Path(safe_name).suffix.lower()
    if ext not in CENTER_PHOTO_EXTENSIONS:
        return jsonify({"error": "Unsupported file type"}), 400
    if not CENTER_PHOTOS_DIR.is_dir():
        return jsonify({"error": "Center photo directory not found"}), 404
    file_path = CENTER_PHOTOS_DIR / safe_name
    if not file_path.is_file():
        return jsonify({"error": "Center photo not found"}), 404
    return send_from_directory(str(CENTER_PHOTOS_DIR), safe_name, conditional=True)


@app.route("/notifications", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_NOTIFICATIONS", "90 per minute"))
def get_notifications():
    _require_api_key()
    try:
        latitude, longitude = _parse_lat_lng()
        return jsonify(_notifications_payload(latitude, longitude))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("notifications: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/sos-session", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_SOS_CREATE", "30 per minute"))
def create_sos_session():
    _require_api_key()
    try:
        _cleanup_sos_sessions()
        payload = sanitize_input(request.get_json() or {})
        lat = _finite_or_none(payload.get("latitude"))
        lng = _finite_or_none(payload.get("longitude"))
        if lat is None or lng is None:
            return jsonify({"error": "latitude and longitude required"}), 400
        if not validate_coordinates(lat, lng):
            return jsonify({"error": "Coordinates outside supported Metro Manila bbox"}), 400
        hotline_name = str(payload.get("hotline_name") or "Emergency Hotline").strip()[:120]
        hotline_number = str(payload.get("hotline_number") or "").strip()[:60]
        session_id = uuid.uuid4().hex[:10]
        rescue_url = f"sheltr://rescue?sos_session={quote(session_id)}"
        now_iso = _utc_iso()
        entry = {
            "session_id": session_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "rescue_url": rescue_url,
            "hotline_name": hotline_name,
            "hotline_number": hotline_number,
            "rescuee": {
                "latitude": lat,
                "longitude": lng,
                "heading": _finite_or_none(payload.get("heading")),
                "accuracy_m": _finite_or_none(payload.get("accuracy_m")),
                "timestamp": now_iso,
            },
            "rescuer": None,
        }
        SOS_SESSIONS[session_id] = entry
        return jsonify(
            {
                "session_id": session_id,
                "created_at": now_iso,
                "rescue_url": rescue_url,
                "qr_payload": rescue_url,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("sos-session(create): %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/sos-session/heartbeat", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_SOS_HEARTBEAT", "120 per minute"))
def heartbeat_sos_session():
    _require_api_key()
    try:
        _cleanup_sos_sessions()
        payload = sanitize_input(request.get_json() or {})
        session_id = str(payload.get("session_id") or "").strip().lower()
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        entry = SOS_SESSIONS.get(session_id)
        if not entry:
            return jsonify({"error": "SOS session not found"}), 404
        lat = _finite_or_none(payload.get("latitude"))
        lng = _finite_or_none(payload.get("longitude"))
        if lat is None or lng is None:
            return jsonify({"error": "latitude and longitude required"}), 400
        if not validate_coordinates(lat, lng):
            return jsonify({"error": "Coordinates outside supported Metro Manila bbox"}), 400
        now_iso = _utc_iso()
        entry["rescuee"] = {
            "latitude": lat,
            "longitude": lng,
            "heading": _finite_or_none(payload.get("heading")),
            "accuracy_m": _finite_or_none(payload.get("accuracy_m")),
            "timestamp": now_iso,
        }
        entry["updated_at"] = now_iso
        return jsonify(_sos_public_payload(entry))
    except Exception as e:  # noqa: BLE001
        logger.exception("sos-session(heartbeat): %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/sos-session/<string:session_id>", methods=["GET"])
@limiter.limit(os.environ.get("SHELTR_RATE_SOS_READ", "120 per minute"))
def get_sos_session(session_id: str):
    _require_api_key()
    try:
        _cleanup_sos_sessions()
        sid = session_id.strip().lower()
        entry = SOS_SESSIONS.get(sid)
        if not entry:
            return jsonify({"error": "SOS session not found"}), 404
        return jsonify(_sos_public_payload(entry))
    except Exception as e:  # noqa: BLE001
        logger.exception("sos-session(get): %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/sos-session/<string:session_id>/claim", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_SOS_CLAIM", "60 per minute"))
def claim_sos_session(session_id: str):
    _require_api_key()
    try:
        _cleanup_sos_sessions()
        sid = session_id.strip().lower()
        entry = SOS_SESSIONS.get(sid)
        if not entry:
            return jsonify({"error": "SOS session not found"}), 404
        payload = sanitize_input(request.get_json() or {})
        name = str(payload.get("name") or "").strip()[:120]
        hotline = str(payload.get("hotline") or "").strip()[:60]
        now_iso = _utc_iso()
        entry["rescuer"] = {
            "name": name or None,
            "hotline": hotline or None,
            "claimed_at": now_iso,
        }
        entry["updated_at"] = now_iso
        return jsonify(_sos_public_payload(entry))
    except Exception as e:  # noqa: BLE001
        logger.exception("sos-session(claim): %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/route-briefing", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_BRIEFING", "20 per minute"))
def post_route_briefing():
    _require_api_key()
    try:
        payload = sanitize_input(request.get_json() or {})
        if not isinstance(payload, dict) or not payload:
            return jsonify({"error": "JSON body required"}), 400
        result = request_route_briefing(payload)
        return jsonify(result)
    except RuntimeError as exc:
        logger.warning("route-briefing: %s", exc)
        return jsonify({"error": str(exc)}), 503
    except (requests.HTTPError, requests.RequestException) as exc:
        logger.warning("route-briefing upstream: %s", exc)
        return jsonify({"error": "openrouter_request_failed"}), 502
    except Exception as e:  # noqa: BLE001
        logger.exception("route-briefing: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/home-briefing", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_HOME_BRIEFING", "20 per minute"))
def post_home_briefing():
    _require_api_key()
    try:
        payload = sanitize_input(request.get_json() or {})
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON body required"}), 400
        lat = _finite_or_none(payload.get("latitude"))
        lng = _finite_or_none(payload.get("longitude"))
        if lat is None or lng is None:
            return jsonify({"error": "latitude and longitude required"}), 400
        if not validate_coordinates(lat, lng):
            return jsonify({"error": "Coordinates outside supported Metro Manila bbox"}), 400
        eff_lat, eff_lng = resolve_effective_user_location(lat, lng)
        facts = _home_briefing_facts(eff_lat, eff_lng)
        facts["requested_location"] = {"latitude": lat, "longitude": lng}
        facts["effective_location"] = {"latitude": eff_lat, "longitude": eff_lng}
        try:
            result = request_home_briefing(facts)
            return jsonify(result)
        except (RuntimeError, requests.HTTPError, requests.RequestException) as exc:
            logger.warning("home-briefing: LLM unavailable, using fallback (%s)", exc)
            return jsonify(
                {
                    "en": fallback_home_briefing_en(facts),
                    "fil": "",
                    "model": "sheltr-fallback",
                    "raw_error": None,
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("home-briefing: %s", e)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/route", methods=["POST"])
@limiter.limit(os.environ.get("SHELTR_RATE_ROUTE", "45 per minute"))
def get_route():
    _require_api_key()
    try:
        data = sanitize_input(request.get_json() or {})
        response = build_route_response(
            data=data,
            centers_provider=centers_provider,
            validate_coordinates_fn=validate_coordinates,
        )
        return jsonify(response)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as e:  # noqa: BLE001
        logger.exception("get_route: %s", e)
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("USE_WAITRESS", "").strip().lower() in ("1", "true", "yes"):
        from waitress import serve

        raw_threads = (os.environ.get("WAITRESS_THREADS") or "").strip()
        if raw_threads:
            threads = max(1, min(32, int(raw_threads)))
        else:
            # Fewer threads on small hosts (e.g. Railway 512MB–1GB) to avoid OOM during routing + flood scoring.
            threads = 4
        logger.info("Starting Sheltr API with Waitress on 0.0.0.0:%s (threads=%s)", port, threads)
        serve(app, host="0.0.0.0", port=port, threads=threads)
    else:
        # threaded=True: /home-briefing can block on OpenRouter for tens of seconds; without
        # threads other tabs/requests (e.g. /risk) stall or see connection resets on Windows.
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
