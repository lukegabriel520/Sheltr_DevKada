"""Scratchpad module for future backend work."""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from centers_provider import CentersProvider
from river_proximity import river_metrics_for_route_coords, nearest_waterway_to_point
from route_policy import apply_policy, compute_class_weights, load_policy
from valhalla_stadia import get_stadia_api_key, request_route_candidates

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


# Simple local test switch for weather JSON spoofing.
# Flip via env (SHELTR_WEATHER_SPOOF=1) only when you want the backend to
# return fake precipitation values below. Defaults to OFF for production safety.
WEATHER_SPOOF_ENABLED = _env_truthy("SHELTR_WEATHER_SPOOF", False)
WEATHER_SPOOF_PRECIPITATION_MM = 69.0
WEATHER_SPOOF_RAIN_3H_MM: float | None = 17.0
WEATHER_SPOOF_RAIN_24H_MM: float | None = 55.0
RIVER_SPOOF_ENABLED = _env_truthy("SHELTR_RIVER_SPOOF", False)
RIVER_SPOOF_DISCHARGE: float | None = None
RIVER_SPOOF_DISCHARGE_MEAN: float | None = None
RIVER_SPOOF_DISCHARGE_P75: float | None = None
USER_LOCATION_SPOOF_ENABLED = _env_truthy("SHELTR_USER_LOCATION_SPOOF", False)
USER_LOCATION_SPOOF_LATITUDE = 14.5947949
USER_LOCATION_SPOOF_LONGITUDE = 120.9918336

# Flood overlay display mode for the map tab.
# 0 = auto (derived from precipitation accumulation logic)
# 1 = show Var 3 only (weakest)
# 2 = show Var 3 + 2
# 3 = show Var 3 + 2 + 1 (all, weak -> strong)
FLOOD_OVERLAY_DISPLAY_MODE = 0
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FLOOD_OVERLAY_5YEAR_PATH = DATA_DIR / "MetroManila_Flood_5year.json"
FLOOD_OVERLAY_25YEAR_PATH = DATA_DIR / "MetroManila_Flood_25year.json"
FLOOD_OVERLAY_100YEAR_PATH = DATA_DIR / "MetroManila_Flood_100year.json"
STORM_SURGE_SSA1_PATH = DATA_DIR / "MetroManila_StormSurge_SSA1.json"
STORM_SURGE_SSA2_PATH = DATA_DIR / "MetroManila_StormSurge_SSA2.json"
STORM_SURGE_SSA3_PATH = DATA_DIR / "MetroManila_StormSurge_SSA3.json"
STORM_SURGE_SSA4_PATH = DATA_DIR / "MetroManila_StormSurge_SSA4.json"
DEFAULT_WEATHER_LATITUDE = 14.6042
DEFAULT_WEATHER_LONGITUDE = 120.9822
ROUTE_DEBUG_EXCLUDED_GEOJSON_PATH = Path(
    os.environ.get(
        "ROUTE_DEBUG_EXCLUDED_GEOJSON_PATH",
        str(Path(__file__).resolve().parent / "debug_excluded_polygons.geojson"),
    )
)

# Flood-state thresholds (computeFloodState logic)
FLOOD_MAP_100YR_RAIN_3H_MM = 80.0
FLOOD_MAP_100YR_RAIN_24H_MM = 150.0
FLOOD_MAP_25YR_RAIN_3H_MM = 50.0
FLOOD_MAP_25YR_RAIN_24H_MM = 100.0
FLOOD_MAP_5YR_RAIN_3H_MM = 20.0
FLOOD_MAP_5YR_RAIN_24H_MM = 50.0
FLOOD_RIVER_RATIO_ESCALATION = 1.2

VAR_5YR_L1_RAIN_3H_MM = 20.0
VAR_5YR_L2_RAIN_3H_MM = 15.0
VAR_5YR_L3_RAIN_3H_MM = 10.0
VAR_25YR_L1_RAIN_3H_MM = 50.0
VAR_25YR_L2_RAIN_3H_MM = 40.0
VAR_25YR_L3_RAIN_3H_MM = 30.0
VAR_100YR_L1_RAIN_3H_MM = 80.0
VAR_100YR_L2_RAIN_3H_MM = 65.0
VAR_100YR_L3_RAIN_3H_MM = 50.0
STADIA_EXCLUDE_CIRCUMFERENCE_LIMIT_M = 10_000.0
STORM_SURGE_SSA1_WIND_KMH = 40.0
STORM_SURGE_SSA2_WIND_KMH = 50.0
STORM_SURGE_SSA3_WIND_KMH = 70.0
STORM_SURGE_SSA4_WIND_KMH = 90.0
STORM_SURGE_SSA4_PRESSURE_HPA = 980.0
STORM_SURGE_ONSHORE_MIN_DEG = 200.0
STORM_SURGE_ONSHORE_MAX_DEG = 340.0
STORM_SURGE_SPOOF_ENABLED = _env_truthy("SHELTR_STORM_SURGE_SPOOF", False)
STORM_SURGE_SPOOF_WIND_SPEED_KMH: float | None = 75
STORM_SURGE_SPOOF_WIND_DIRECTION_DEG: float | None = 260
STORM_SURGE_SPOOF_PRESSURE_MSL_HPA: float | None = 1005
WAGAMAMA_DEBUG_LOGS = os.environ.get("WAGAMAMA_DEBUG_LOGS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Defaults tuned for Railway free-tier: weather + flood overlay change slowly,
# so cache them for ~3 minutes by default. Each cache key is a ~110 m bin so
# users in the same neighborhood share one snapshot. Override via env.
WEATHER_CACHE_TTL_SECONDS = max(0.0, float(os.environ.get("WEATHER_CACHE_TTL_SECONDS", "180")))
FLOOD_OVERLAY_CACHE_TTL_SECONDS = max(
    0.0, float(os.environ.get("FLOOD_OVERLAY_CACHE_TTL_SECONDS", "180"))
)


class _TTLCache:
    """Tiny thread-safe in-memory cache used for weather/flood-overlay snapshots.

    Stores `(stored_at_epoch, value)` per key. Returning a tuple of value plus
    timestamp lets callers expose data freshness to clients.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._store: dict[Any, tuple[float, Any]] = {}

    @property
    def ttl(self) -> float:
        return self._ttl

    def get(self, key: Any) -> tuple[Any, float] | None:
        if self._ttl <= 0.0:
            return None
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            stored_at, value = entry
            if time.time() - stored_at > self._ttl:
                self._store.pop(key, None)
                return None
            return value, stored_at

    def set(self, key: Any, value: Any, max_size: int = 500) -> float:
        stored_at = time.time()
        if self._ttl <= 0.0:
            return stored_at
        with self._lock:
            self._store[key] = (stored_at, value)
            if len(self._store) > max_size:
                now = time.time()
                expired = [k for k, (t, _) in self._store.items() if now - t > self._ttl]
                for k in expired:
                    self._store.pop(k, None)
                if len(self._store) > max_size:
                    to_remove = list(self._store.keys())[: len(self._store) - int(max_size * 0.8)]
                    for k in to_remove:
                        self._store.pop(k, None)
        return stored_at

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_WEATHER_CACHE = _TTLCache(WEATHER_CACHE_TTL_SECONDS)
_FLOOD_OVERLAY_CACHE = _TTLCache(FLOOD_OVERLAY_CACHE_TTL_SECONDS)

_ROUTE_POLICY_CACHE: dict[str, Any] | None = None


def _route_policy() -> dict[str, Any]:
    """Cached route policy. Reload on each request when SHELTR_ROUTE_POLICY_HOT_RELOAD is truthy."""
    global _ROUTE_POLICY_CACHE
    if os.environ.get("SHELTR_ROUTE_POLICY_HOT_RELOAD", "").strip().lower() in ("1", "true", "yes"):
        return load_policy()
    if _ROUTE_POLICY_CACHE is None:
        _ROUTE_POLICY_CACHE = load_policy()
    return _ROUTE_POLICY_CACHE


def _now_epoch() -> float:
    return time.time()


def _epoch_to_iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _data_age_seconds(stored_at: float) -> float:
    return max(0.0, time.time() - stored_at)


def _debug_log(message: str, *args: Any) -> None:
    if WAGAMAMA_DEBUG_LOGS:
        logger.info(message, *args)


def _load_flood_overlay_geojson(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Flood overlay GeoJSON root is not an object")
    return data


def _load_geojson_feature_collection(
    path: Path, *, label: str, required_numeric_property: str
) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{label}: GeoJSON root is not an object")
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"{label}: GeoJSON type must be FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError(f"{label}: features must be an array")
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            raise ValueError(f"{label}: feature[{idx}] is not an object")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"{label}: feature[{idx}].geometry is missing")
        gtype = geometry.get("type")
        if gtype not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"{label}: feature[{idx}] geometry must be Polygon or MultiPolygon")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"{label}: feature[{idx}] properties must be an object")
        numeric_value = _finite_or_none(properties.get(required_numeric_property))
        if numeric_value is None:
            raise ValueError(
                f"{label}: feature[{idx}] properties.{required_numeric_property} must be numeric"
            )
    return data


def _select_flood_overlay_path_by_map(map_code: int) -> tuple[Path, str]:
    if map_code == 100:
        return FLOOD_OVERLAY_100YEAR_PATH, "100-year"
    if map_code == 25:
        return FLOOD_OVERLAY_25YEAR_PATH, "25-year"
    return FLOOD_OVERLAY_5YEAR_PATH, "5-year"


def _normalize_flood_scenario(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if token in ("", "auto", "automatic"):
        return "auto"
    if token in ("sts", "severe_tropical_storm", "severe_storm", "5", "5_year"):
        return "sts"
    if token in ("typhoon", "25", "25_year"):
        return "typhoon"
    if token in ("super_typhoon", "super", "100", "100_year"):
        return "super_typhoon"
    return None


def _scenario_to_map_code(scenario: str | None) -> int | None:
    if scenario == "sts":
        return 5
    if scenario == "typhoon":
        return 25
    if scenario == "super_typhoon":
        return 100
    return None


def _select_storm_surge_overlay_path_by_level(level: int) -> tuple[Path, str]:
    if level >= 4:
        return STORM_SURGE_SSA4_PATH, "SSA4"
    if level == 3:
        return STORM_SURGE_SSA3_PATH, "SSA3"
    if level == 2:
        return STORM_SURGE_SSA2_PATH, "SSA2"
    return STORM_SURGE_SSA1_PATH, "SSA1"


def _allowed_overlay_vars(mode: int) -> set[int]:
    # Var stacking behavior (Var 1 strongest, Var 3 weakest):
    # mode 1 -> show Var 3 only
    # mode 2 -> show Var 3 + 2
    # mode 3 -> show Var 3 + 2 + 1
    if mode == 1:
        return {3}
    if mode == 2:
        return {2, 3}
    if mode == 3:
        return {1, 2, 3}
    return set()


def _allowed_storm_surge_haz(mode: int) -> set[int]:
    return {1, 2, 3}


def _auto_display_mode_from_varlevel(var_level: int) -> int:
    # var_level semantics: 1 strongest, 3 weakest
    # display_mode semantics: 1 weakest stack, 3 strongest stack
    if var_level == 1:
        return 3
    if var_level == 2:
        return 2
    if var_level == 3:
        return 1
    return 0


def _auto_display_mode_from_storm_severity(severity: int) -> int:
    if severity >= 3:
        return 3
    if severity == 2:
        return 2
    if severity == 1:
        return 1
    return 0


def _finite_or_none(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _resolve_user_location(latitude: float, longitude: float) -> tuple[float, float]:
    if USER_LOCATION_SPOOF_ENABLED:
        return float(USER_LOCATION_SPOOF_LATITUDE), float(USER_LOCATION_SPOOF_LONGITUDE)
    return latitude, longitude


def resolve_effective_user_location(latitude: float, longitude: float) -> tuple[float, float]:
    return _resolve_user_location(latitude, longitude)


def _pick(values: list[Any], idx: int) -> Any:
    if 0 <= idx < len(values):
        return values[idx]
    return None


def _nearest_hour_index(times: list[Any], timezone_name: str) -> int:
    if not times:
        return 0
    now_local = datetime.now(ZoneInfo(timezone_name)).replace(minute=0, second=0, microsecond=0)
    now_naive = now_local.replace(tzinfo=None)
    best_idx = 0
    best_delta = None
    for idx, raw_time in enumerate(times):
        if not isinstance(raw_time, str):
            continue
        try:
            point = datetime.fromisoformat(raw_time)
        except ValueError:
            continue
        if point.tzinfo is not None:
            point = point.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
        delta = abs((point - now_naive).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_idx = idx
    return best_idx


def _today_daily_index(days: list[Any], timezone_name: str) -> int:
    if not days:
        return 0
    today = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    for idx, d in enumerate(days):
        if d == today:
            return idx
    return 0


def _sum_window(values: list[Any], start: int, size: int) -> float:
    total = 0.0
    end = min(len(values), max(start, 0) + size)
    for i in range(max(start, 0), end):
        v = _finite_or_none(values[i])
        if v is not None:
            total += v
    return total


def _sum_trailing_window(values: list[Any], end_idx: int, size: int) -> float:
    if size <= 0:
        return 0.0
    start = max(0, end_idx - size + 1)
    return _sum_window(values, start, end_idx - start + 1)


def _fetch_open_meteo_flood_daily(latitude: float, longitude: float) -> dict[str, Any]:
    url = (
        "https://flood-api.open-meteo.com/v1/flood"
        f"?latitude={latitude}&longitude={longitude}"
        "&daily=river_discharge,river_discharge_mean,river_discharge_p75"
        "&models=seamless_v4"
        "&timezone=Asia%2FSingapore"
        "&past_days=92"
        "&forecast_days=92"
    )
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def _fetch_open_meteo_storm_hourly(latitude: float, longitude: float) -> dict[str, Any]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&timezone=Asia%2FSingapore"
        "&hourly=windspeed_10m,winddirection_10m,pressure_msl"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def _is_onshore_wind(direction_deg: float | None) -> bool:
    if direction_deg is None:
        return False
    d = direction_deg % 360.0
    if STORM_SURGE_ONSHORE_MIN_DEG <= STORM_SURGE_ONSHORE_MAX_DEG:
        return STORM_SURGE_ONSHORE_MIN_DEG <= d <= STORM_SURGE_ONSHORE_MAX_DEG
    return d >= STORM_SURGE_ONSHORE_MIN_DEG or d <= STORM_SURGE_ONSHORE_MAX_DEG


def _compute_flood_state(
    rain3h: float | None,
    rain24h: float | None,
    river_discharge: float | None,
    river_p75: float | None,
) -> dict[str, Any]:
    map_code = 0
    base_reason = "below_rain_thresholds"
    if (
        (rain3h is not None and rain3h >= FLOOD_MAP_100YR_RAIN_3H_MM)
        or (rain24h is not None and rain24h >= FLOOD_MAP_100YR_RAIN_24H_MM)
    ):
        map_code = 100
        base_reason = "rain_trigger_100yr"
    elif (
        (rain3h is not None and rain3h >= FLOOD_MAP_25YR_RAIN_3H_MM)
        or (rain24h is not None and rain24h >= FLOOD_MAP_25YR_RAIN_24H_MM)
    ):
        map_code = 25
        base_reason = "rain_trigger_25yr"
    elif (
        (rain3h is not None and rain3h >= FLOOD_MAP_5YR_RAIN_3H_MM)
        or (rain24h is not None and rain24h >= FLOOD_MAP_5YR_RAIN_24H_MM)
    ):
        map_code = 5
        base_reason = "rain_trigger_5yr"

    river_ratio: float | None = None
    river_escalated = False
    if (
        river_discharge is not None
        and river_p75 is not None
        and river_p75 > 0
    ):
        river_ratio = river_discharge / river_p75
        if river_ratio > FLOOD_RIVER_RATIO_ESCALATION:
            if map_code == 5:
                map_code = 25
                river_escalated = True
            elif map_code == 25:
                map_code = 100
                river_escalated = True

    var_level = 0
    if map_code == 5 and rain3h is not None:
        if rain3h >= VAR_5YR_L1_RAIN_3H_MM:
            var_level = 1
        elif rain3h >= VAR_5YR_L2_RAIN_3H_MM:
            var_level = 2
        elif rain3h >= VAR_5YR_L3_RAIN_3H_MM:
            var_level = 3
    elif map_code == 25 and rain3h is not None:
        if rain3h >= VAR_25YR_L1_RAIN_3H_MM:
            var_level = 1
        elif rain3h >= VAR_25YR_L2_RAIN_3H_MM:
            var_level = 2
        elif rain3h >= VAR_25YR_L3_RAIN_3H_MM:
            var_level = 3
    elif map_code == 100 and rain3h is not None:
        if rain3h >= VAR_100YR_L1_RAIN_3H_MM:
            var_level = 1
        elif rain3h >= VAR_100YR_L2_RAIN_3H_MM:
            var_level = 2
        elif rain3h >= VAR_100YR_L3_RAIN_3H_MM:
            var_level = 3

    map_path, map_label = _select_flood_overlay_path_by_map(map_code)
    debug_reason = (
        f"This map ({map_label}) and var level ({var_level}) are used because "
        f"rain3h={rain3h}, rain24h={rain24h}, river_discharge={river_discharge}, "
        f"river_p75={river_p75}, river_ratio={river_ratio}, "
        f"base_reason={base_reason}, river_escalated={river_escalated}."
    )
    return {
        "map": map_code,
        "map_label": map_label,
        "map_path": str(map_path),
        "varLevel": var_level,
        "river_ratio": river_ratio,
        "river_escalated": river_escalated,
        "base_reason": base_reason,
        "debug_reason": debug_reason,
    }


def _compute_storm_surge_state(
    wind_speed_kmh: float | None,
    wind_direction_deg: float | None,
    pressure_msl_hpa: float | None,
) -> dict[str, Any]:
    onshore = _is_onshore_wind(wind_direction_deg)
    severity = 0
    reason = "offshore_or_below_threshold"
    if onshore:
        if (
            (wind_speed_kmh is not None and wind_speed_kmh >= STORM_SURGE_SSA4_WIND_KMH)
            or (pressure_msl_hpa is not None and pressure_msl_hpa <= STORM_SURGE_SSA4_PRESSURE_HPA)
        ):
            severity = 4
            reason = "ssa4_wind_or_pressure_trigger"
        elif wind_speed_kmh is not None and wind_speed_kmh >= STORM_SURGE_SSA3_WIND_KMH:
            severity = 3
            reason = "ssa3_wind_trigger"
        elif wind_speed_kmh is not None and wind_speed_kmh >= STORM_SURGE_SSA2_WIND_KMH:
            severity = 2
            reason = "ssa2_wind_trigger"
        elif wind_speed_kmh is not None and wind_speed_kmh >= STORM_SURGE_SSA1_WIND_KMH:
            severity = 1
            reason = "ssa1_wind_trigger"

    active = severity > 0
    path: str | None = None
    label: str | None = None
    if active:
        ssa_path, ssa_label = _select_storm_surge_overlay_path_by_level(severity)
        path = str(ssa_path)
        label = ssa_label
    debug_reason = (
        "Storm surge "
        + ("active" if active else "inactive")
        + f" because windspeed_10m={wind_speed_kmh}, winddirection_10m={wind_direction_deg}, "
        + f"pressure_msl={pressure_msl_hpa}, onshore={onshore}, reason={reason}."
    )
    return {
        "active": active,
        "severity": severity,
        "ssa_label": label,
        "ssa_path": path,
        "onshore": onshore,
        "reason": reason,
        "debug_reason": debug_reason,
    }


def _flood_overlay_cache_key(
    lat: float, lng: float, scenario: str | None
) -> tuple[float, float, str]:
    return (round(lat, 3), round(lng, 3), scenario or "auto")


def _augment_overlay_with_freshness(
    payload: dict[str, Any],
    stored_at: float,
    *,
    from_cache: bool,
    weather_age_seconds: float | None,
    weather_fetched_at: str | None,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["fetched_at"] = _epoch_to_iso(stored_at)
    enriched["fetched_at_epoch"] = round(stored_at, 3)
    enriched["data_age_seconds"] = round(_data_age_seconds(stored_at), 3)
    enriched["cache_ttl_seconds"] = FLOOD_OVERLAY_CACHE_TTL_SECONDS
    enriched["from_cache"] = bool(from_cache)
    if weather_age_seconds is not None:
        enriched["weather_data_age_seconds"] = round(float(weather_age_seconds), 3)
    if weather_fetched_at:
        enriched["weather_fetched_at"] = weather_fetched_at
    return enriched


def flood_overlay_snapshot(
    latitude: float | None = None,
    longitude: float | None = None,
    scenario_override: str | None = None,
) -> dict[str, Any]:
    req_lat = _finite_or_none(latitude) or DEFAULT_WEATHER_LATITUDE
    req_lng = _finite_or_none(longitude) or DEFAULT_WEATHER_LONGITUDE
    lat, lng = _resolve_user_location(req_lat, req_lng)
    normalized_scenario = _normalize_flood_scenario(scenario_override)
    cache_key = _flood_overlay_cache_key(lat, lng, normalized_scenario)
    cached = _FLOOD_OVERLAY_CACHE.get(cache_key)
    weather = weather_snapshot(lat, lng)
    weather_age = _finite_or_none(weather.get("data_age_seconds"))
    weather_iso = weather.get("fetched_at") if isinstance(weather.get("fetched_at"), str) else None
    if cached is not None:
        cached_payload, stored_at = cached
        return _augment_overlay_with_freshness(
            cached_payload,
            stored_at,
            from_cache=True,
            weather_age_seconds=weather_age,
            weather_fetched_at=weather_iso,
        )
    payload = _flood_overlay_snapshot_compute(
        lat=lat,
        lng=lng,
        weather=weather,
        normalized_scenario=normalized_scenario,
    )
    stored_at = _FLOOD_OVERLAY_CACHE.set(cache_key, payload)
    return _augment_overlay_with_freshness(
        payload,
        stored_at,
        from_cache=False,
        weather_age_seconds=weather_age,
        weather_fetched_at=weather_iso,
    )


def _flood_overlay_snapshot_compute(
    *,
    lat: float,
    lng: float,
    weather: dict[str, Any],
    normalized_scenario: str | None,
) -> dict[str, Any]:
    forced_map_code = _scenario_to_map_code(normalized_scenario)
    map_code = forced_map_code or int(_finite_or_none(weather.get("flood_map")) or 5)
    selected_overlay_path, selected_overlay_name = _select_flood_overlay_path_by_map(map_code)
    source = _load_flood_overlay_geojson(selected_overlay_path)
    overlay_logic: dict[str, Any]
    flood_display_mode = 0
    storm_display_mode = 1
    if forced_map_code is not None:
        flood_display_mode = 3
        overlay_logic = {
            "mode_override": "manual_scenario_override",
            "source": "manual_scenario_override",
            "scenario_override": normalized_scenario,
        }
    elif FLOOD_OVERLAY_DISPLAY_MODE in (1, 2, 3):
        flood_display_mode = FLOOD_OVERLAY_DISPLAY_MODE
        overlay_logic = {
            "mode_override": FLOOD_OVERLAY_DISPLAY_MODE,
            "source": "manual_override",
        }
    else:
        flood_display_mode = int(weather.get("overlay_display_mode") or weather.get("overlay_var_level") or 0)
        storm_display_mode = _auto_display_mode_from_storm_severity(
            int(_finite_or_none(weather.get("storm_surge_severity")) or 0)
        )
        logic = weather.get("overlay_logic")
        overlay_logic = logic if isinstance(logic, dict) else {}
    storm_severity = int(_finite_or_none(weather.get("storm_surge_severity")) or 0)
    storm_active = bool(weather.get("storm_surge_active")) and storm_severity > 0
    if forced_map_code is not None:
        # Manual scenario: always pair the chosen flood map with a coastal
        # storm-surge reference layer so coastline context stays visible even
        # when live weather does not report an active surge.
        manual_surge_severity = {
            "sts": 1,
            "typhoon": 3,
            "super_typhoon": 4,
        }.get(normalized_scenario or "", 0)
        if manual_surge_severity > 0:
            storm_severity = max(storm_severity, manual_surge_severity)
            storm_active = True
    storm_overlay_path: Path | None = None
    storm_overlay_name: str | None = None
    storm_overlay: dict[str, Any] | None = None
    storm_error: str | None = None
    if storm_active:
        storm_overlay_path, storm_overlay_name = _select_storm_surge_overlay_path_by_level(storm_severity)
        try:
            storm_overlay = _load_geojson_feature_collection(
                storm_overlay_path,
                label=storm_overlay_name or "storm_surge",
                required_numeric_property="HAZ",
            )
        except Exception as exc:  # noqa: BLE001
            storm_error = str(exc)
            storm_overlay = None
            _debug_log("Storm surge overlay validation failed: %s", storm_error)
    overlay_logic = {
        **overlay_logic,
        "flood_display_mode": flood_display_mode,
        "storm_display_mode": storm_display_mode,
        "flood_overlay_map_selection": {
            "selected_map": selected_overlay_name,
            "selected_path": str(selected_overlay_path),
            "map_code": map_code,
            "scenario_override": normalized_scenario,
            "debug_reason": weather.get("flood_state_debug_reason"),
        },
        "storm_surge_overlay_selection": {
            "active": storm_active,
            "severity": storm_severity,
            "selected_map": storm_overlay_name,
            "selected_path": str(storm_overlay_path) if storm_overlay_path else None,
            "debug_reason": weather.get("storm_surge_debug_reason"),
            "error": storm_error,
        },
    }
    _debug_log(
        "Flood overlay snapshot: flood_map=%s storm_map=%s flood_mode=%s storm_mode=%s flood_path=%s storm_path=%s",
        selected_overlay_name,
        storm_overlay_name,
        flood_display_mode,
        storm_display_mode,
        selected_overlay_path,
        str(storm_overlay_path) if storm_overlay_path else None,
    )
    raw_features = source.get("features") or []
    allowed_vars = _allowed_overlay_vars(flood_display_mode)
    allowed_haz = _allowed_storm_surge_haz(storm_display_mode)
    features: list[dict[str, Any]] = []

    # Bounding box around the user: tight radius for auto (payload size); wider when the user
    # explicitly picks STS/TY/STY so hazard polygons are visible across more of NCR.
    try:
        manual_radius_km = float(os.environ.get("SHELTR_MANUAL_SCENARIO_OVERLAY_RADIUS_KM", "48"))
    except (TypeError, ValueError):
        manual_radius_km = 48.0
    if not math.isfinite(manual_radius_km) or manual_radius_km <= 0:
        manual_radius_km = 48.0
    clip_radius_km = manual_radius_km if forced_map_code is not None else 12.0
    lat_margin = clip_radius_km / 111.0
    lng_margin = clip_radius_km / (111.0 * math.cos(math.radians(lat))) if math.cos(math.radians(lat)) != 0 else clip_radius_km / 111.0
    min_lat, max_lat = lat - lat_margin, lat + lat_margin
    min_lng, max_lng = lng - lng_margin, lng + lng_margin

    def _in_bbox(geom: dict[str, Any]) -> bool:
        coords = geom.get("coordinates")
        if not coords or not isinstance(coords, list):
            return False
        min_geom_lng = float("inf")
        max_geom_lng = float("-inf")
        min_geom_lat = float("inf")
        max_geom_lat = float("-inf")

        def _walk(node: Any) -> None:
            nonlocal min_geom_lng, max_geom_lng, min_geom_lat, max_geom_lat
            if isinstance(node, list):
                if len(node) >= 2 and not isinstance(node[0], list):
                    lon = _finite_or_none(node[0])
                    lat_v = _finite_or_none(node[1])
                    if lon is None or lat_v is None:
                        return
                    min_geom_lng = min(min_geom_lng, lon)
                    max_geom_lng = max(max_geom_lng, lon)
                    min_geom_lat = min(min_geom_lat, lat_v)
                    max_geom_lat = max(max_geom_lat, lat_v)
                    return
                for child in node:
                    _walk(child)

        _walk(coords)
        if not (
            math.isfinite(min_geom_lng)
            and math.isfinite(max_geom_lng)
            and math.isfinite(min_geom_lat)
            and math.isfinite(max_geom_lat)
        ):
            return False
        intersects_lng = not (max_geom_lng < min_lng or min_geom_lng > max_lng)
        intersects_lat = not (max_geom_lat < min_lat or min_geom_lat > max_lat)
        return intersects_lng and intersects_lat

    for feature in raw_features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        raw_var = properties.get("Var")
        try:
            var_level = int(raw_var)
        except (TypeError, ValueError):
            continue
        if var_level not in allowed_vars:
            continue
        if not _in_bbox(geometry):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    **properties,
                    "Var": var_level,
                    "overlay_type": "flood",
                },
            }
        )
    if storm_overlay and isinstance(storm_overlay.get("features"), list):
        for feature in storm_overlay.get("features") or []:
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                continue
            haz = _finite_or_none(properties.get("HAZ"))
            if haz is None:
                continue
            haz_level = int(haz)
            if haz_level not in allowed_haz:
                continue
            if not _in_bbox(geometry):
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        **properties,
                        "HAZ": haz_level,
                        "overlay_type": "storm_surge",
                        "ssa_level": storm_severity,
                    },
                }
            )
    # Draw weak-to-strong so stronger Var 1 is rendered on top where polygons overlap.
    def _feature_sort_key(feature: dict[str, Any]) -> tuple[int, int]:
        props = feature.get("properties") if isinstance(feature, dict) else {}
        if not isinstance(props, dict):
            return (0, 0)
        overlay_type = str(props.get("overlay_type") or "flood")
        if overlay_type == "storm_surge":
            try:
                haz_level = int(props.get("HAZ"))
            except (TypeError, ValueError):
                haz_level = 3
            return (1, -haz_level)
        try:
            var_level = int(props.get("Var"))
        except (TypeError, ValueError):
            var_level = 3
        return (0, -var_level)
    features.sort(key=_feature_sort_key)

    return {
        "type": "FeatureCollection",
        "features": features,
        "display_mode": flood_display_mode,
        "storm_display_mode": storm_display_mode,
        "display_vars": sorted(allowed_vars),
        "display_haz": sorted(allowed_haz),
        "overlay_logic": overlay_logic,
        "overlay_map": selected_overlay_name,
        "overlay_map_path": str(selected_overlay_path),
        "scenario_override": normalized_scenario,
        "storm_surge_active": storm_active,
        "storm_surge_severity": storm_severity,
        "storm_surge_overlay": storm_overlay_name,
        "storm_surge_overlay_path": str(storm_overlay_path) if storm_overlay_path else None,
        "storm_surge_error": storm_error,
        "debug_reason": weather.get("flood_state_debug_reason"),
    }


def _weather_cache_key(lat: float, lng: float) -> tuple[float, float]:
    # Round so all users in the same ~110 m bin share a cached snapshot.
    return (round(lat, 3), round(lng, 3))


def _augment_weather_with_freshness(
    payload: dict[str, Any], stored_at: float, *, from_cache: bool
) -> dict[str, Any]:
    """Attach freshness metadata to a weather snapshot payload.

    A copy is returned so the cached object stays immutable across callers."""
    fresh_age = _data_age_seconds(stored_at)
    enriched = dict(payload)
    enriched["fetched_at"] = _epoch_to_iso(stored_at)
    enriched["fetched_at_epoch"] = round(stored_at, 3)
    enriched["data_age_seconds"] = round(fresh_age, 3)
    enriched["cache_ttl_seconds"] = WEATHER_CACHE_TTL_SECONDS
    enriched["from_cache"] = bool(from_cache)
    return enriched


def weather_snapshot(latitude: float, longitude: float) -> dict[str, Any]:
    req_lat = _finite_or_none(latitude) or DEFAULT_WEATHER_LATITUDE
    req_lng = _finite_or_none(longitude) or DEFAULT_WEATHER_LONGITUDE
    lat, lng = _resolve_user_location(req_lat, req_lng)
    cache_key = _weather_cache_key(lat, lng)
    cached = _WEATHER_CACHE.get(cache_key)
    if cached is not None:
        cached_payload, stored_at = cached
        return _augment_weather_with_freshness(cached_payload, stored_at, from_cache=True)
    payload = _weather_snapshot_uncached(lat, lng, req_lat=req_lat, req_lng=req_lng)
    stored_at = _WEATHER_CACHE.set(cache_key, payload)
    return _augment_weather_with_freshness(payload, stored_at, from_cache=False)


def _weather_snapshot_uncached(
    lat: float,
    lng: float,
    *,
    req_lat: float,
    req_lng: float,
) -> dict[str, Any]:
    timezone_name = "Asia/Singapore"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lng}"
        "&timezone=Asia%2FSingapore"
        "&hourly=precipitation,temperature_2m,relative_humidity_2m"
        "&daily=rain_sum,precipitation_hours"
        "&past_days=7"
        "&forecast_days=7"
    )
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}

    times = hourly.get("time") or []
    precipitations = hourly.get("precipitation") or []
    temps = hourly.get("temperature_2m") or []
    humidities = hourly.get("relative_humidity_2m") or []

    daily_times = daily.get("time") or []
    daily_rain_sums = daily.get("rain_sum") or []
    daily_precip_hours = daily.get("precipitation_hours") or []

    snapshot_idx = _nearest_hour_index(times, timezone_name)
    daily_idx = _today_daily_index(daily_times, timezone_name)

    selected_precipitation = _pick(precipitations, snapshot_idx)
    selected_daily_rain_sum = _pick(daily_rain_sums, daily_idx)
    selected_daily_precip_hours = _pick(daily_precip_hours, daily_idx)
    rain_3h = _sum_trailing_window(precipitations, snapshot_idx, 3)
    rain_24h = _finite_or_none(selected_daily_rain_sum)
    if rain_24h is None:
        rain_24h = _sum_trailing_window(precipitations, snapshot_idx, 24)

    hourly_precipitations = list(precipitations)
    daily_rain_sums_mut = list(daily_rain_sums)
    daily_precip_hours_mut = list(daily_precip_hours)
    if WEATHER_SPOOF_ENABLED:
        selected_precipitation = WEATHER_SPOOF_PRECIPITATION_MM
        if 0 <= snapshot_idx < len(hourly_precipitations):
            hourly_precipitations[snapshot_idx] = WEATHER_SPOOF_PRECIPITATION_MM
        rain_3h = _sum_trailing_window(hourly_precipitations, snapshot_idx, 3)
        rain_24h = _sum_trailing_window(hourly_precipitations, snapshot_idx, 24)
        if WEATHER_SPOOF_RAIN_3H_MM is not None:
            rain_3h = WEATHER_SPOOF_RAIN_3H_MM
        if WEATHER_SPOOF_RAIN_24H_MM is not None:
            rain_24h = WEATHER_SPOOF_RAIN_24H_MM

    flood_api_daily: dict[str, Any] | None = None
    flood_api_error: str | None = None
    river_discharge: float | None = None
    river_discharge_mean: float | None = None
    river_discharge_p75: float | None = None
    flood_day_time: str | None = None
    try:
        flood_api_data = _fetch_open_meteo_flood_daily(lat, lng)
        daily_flood = flood_api_data.get("daily") or {}
        flood_times = daily_flood.get("time") or []
        river_vals = daily_flood.get("river_discharge") or []
        river_mean_vals = daily_flood.get("river_discharge_mean") or []
        river_p75_vals = daily_flood.get("river_discharge_p75") or []
        flood_idx = _today_daily_index(flood_times, timezone_name)
        river_discharge = _finite_or_none(_pick(river_vals, flood_idx))
        river_discharge_mean = _finite_or_none(_pick(river_mean_vals, flood_idx))
        river_discharge_p75 = _finite_or_none(_pick(river_p75_vals, flood_idx))
        flood_day_time = _pick(flood_times, flood_idx)
        flood_api_daily = {
            "time": flood_times,
            "river_discharge": river_vals,
            "river_discharge_mean": river_mean_vals,
            "river_discharge_p75": river_p75_vals,
        }
    except Exception as e:  # noqa: BLE001
        flood_api_error = str(e)

    storm_api_hourly: dict[str, Any] | None = None
    storm_api_error: str | None = None
    storm_hour_time: str | None = None
    wind_speed_10m_kmh: float | None = None
    wind_direction_10m_deg: float | None = None
    pressure_msl_hpa: float | None = None
    try:
        storm_api_data = _fetch_open_meteo_storm_hourly(lat, lng)
        storm_hourly = storm_api_data.get("hourly") or {}
        storm_times = storm_hourly.get("time") or []
        wind_speeds = storm_hourly.get("windspeed_10m") or []
        wind_directions = storm_hourly.get("winddirection_10m") or []
        pressures = storm_hourly.get("pressure_msl") or []
        storm_idx = _nearest_hour_index(storm_times, timezone_name)
        wind_speed_10m_kmh = _finite_or_none(_pick(wind_speeds, storm_idx))
        wind_direction_10m_deg = _finite_or_none(_pick(wind_directions, storm_idx))
        pressure_msl_hpa = _finite_or_none(_pick(pressures, storm_idx))
        storm_hour_time = _pick(storm_times, storm_idx)
        storm_api_hourly = {
            "time": storm_times,
            "windspeed_10m": wind_speeds,
            "winddirection_10m": wind_directions,
            "pressure_msl": pressures,
        }
    except Exception as e:  # noqa: BLE001
        storm_api_error = str(e)

    if RIVER_SPOOF_ENABLED:
        if RIVER_SPOOF_DISCHARGE is not None:
            river_discharge = float(RIVER_SPOOF_DISCHARGE)
        if RIVER_SPOOF_DISCHARGE_MEAN is not None:
            river_discharge_mean = float(RIVER_SPOOF_DISCHARGE_MEAN)
        if RIVER_SPOOF_DISCHARGE_P75 is not None:
            river_discharge_p75 = float(RIVER_SPOOF_DISCHARGE_P75)
        _debug_log(
            "River spoof applied: discharge=%s mean=%s p75=%s",
            river_discharge,
            river_discharge_mean,
            river_discharge_p75,
        )

    if STORM_SURGE_SPOOF_ENABLED:
        if STORM_SURGE_SPOOF_WIND_SPEED_KMH is not None:
            wind_speed_10m_kmh = float(STORM_SURGE_SPOOF_WIND_SPEED_KMH)
        if STORM_SURGE_SPOOF_WIND_DIRECTION_DEG is not None:
            wind_direction_10m_deg = float(STORM_SURGE_SPOOF_WIND_DIRECTION_DEG)
        if STORM_SURGE_SPOOF_PRESSURE_MSL_HPA is not None:
            pressure_msl_hpa = float(STORM_SURGE_SPOOF_PRESSURE_MSL_HPA)
        _debug_log(
            "Storm spoof applied: wind_kmh=%s direction_deg=%s pressure_hpa=%s",
            wind_speed_10m_kmh,
            wind_direction_10m_deg,
            pressure_msl_hpa,
        )

    flood_state = _compute_flood_state(
        _finite_or_none(rain_3h),
        _finite_or_none(rain_24h),
        river_discharge,
        river_discharge_p75,
    )
    storm_state = _compute_storm_surge_state(
        wind_speed_10m_kmh,
        wind_direction_10m_deg,
        pressure_msl_hpa,
    )
    _debug_log(
        "Flood state: map=%s var=%s rain3h=%s rain24h=%s river=%s p75=%s ratio=%s reason=%s",
        flood_state.get("map_label"),
        flood_state.get("varLevel"),
        round(rain_3h, 3),
        round(rain_24h, 3),
        river_discharge,
        river_discharge_p75,
        flood_state.get("river_ratio"),
        flood_state.get("debug_reason"),
    )
    _debug_log(
        "Storm surge state: severity=%s active=%s ssa=%s onshore=%s reason=%s",
        storm_state.get("severity"),
        storm_state.get("active"),
        storm_state.get("ssa_label"),
        storm_state.get("onshore"),
        storm_state.get("debug_reason"),
    )

    overlay_var_level = int(flood_state.get("varLevel") or 0)
    overlay_display_mode = _auto_display_mode_from_varlevel(overlay_var_level)
    if FLOOD_OVERLAY_DISPLAY_MODE in (1, 2, 3):
        overlay_var_level = FLOOD_OVERLAY_DISPLAY_MODE
        overlay_display_mode = FLOOD_OVERLAY_DISPLAY_MODE

    return {
        "temperature": _pick(temps, snapshot_idx),
        "humidity": _pick(humidities, snapshot_idx),
        "precipitation": selected_precipitation,
        "rain_3h": round(rain_3h, 3),
        "rain_24h": round(rain_24h, 3),
        "daily_rain_sum": selected_daily_rain_sum,
        "daily_precipitation_hours": selected_daily_precip_hours,
        "overlay_var_level": overlay_var_level,
        "overlay_display_mode": overlay_display_mode,
        "flood_map": flood_state.get("map"),
        "flood_map_label": flood_state.get("map_label"),
        "flood_map_path": flood_state.get("map_path"),
        "flood_state_debug_reason": flood_state.get("debug_reason"),
        "storm_surge_active": storm_state.get("active"),
        "storm_surge_severity": storm_state.get("severity"),
        "storm_surge_label": storm_state.get("ssa_label"),
        "storm_surge_path": storm_state.get("ssa_path"),
        "storm_surge_onshore": storm_state.get("onshore"),
        "storm_surge_debug_reason": storm_state.get("debug_reason"),
        "wind_speed_10m_kmh": wind_speed_10m_kmh,
        "wind_direction_10m_deg": wind_direction_10m_deg,
        "pressure_msl_hpa": pressure_msl_hpa,
        "river_discharge": river_discharge,
        "river_discharge_mean": river_discharge_mean,
        "river_discharge_p75": river_discharge_p75,
        "river_ratio": flood_state.get("river_ratio"),
        "river_time": flood_day_time,
        "overlay_logic": {
            "mode_override": FLOOD_OVERLAY_DISPLAY_MODE,
            "rain_3h_mm": round(rain_3h, 3),
            "rain_24h_mm": round(rain_24h, 3),
            "flood_state": flood_state,
            "debug_reason": flood_state.get("debug_reason"),
            "storm_surge": storm_state,
            "thresholds": {
                "map_100_year": {
                    "rain_3h_mm": FLOOD_MAP_100YR_RAIN_3H_MM,
                    "rain_24h_mm": FLOOD_MAP_100YR_RAIN_24H_MM,
                },
                "map_25_year": {
                    "rain_3h_mm": FLOOD_MAP_25YR_RAIN_3H_MM,
                    "rain_24h_mm": FLOOD_MAP_25YR_RAIN_24H_MM,
                },
                "map_5_year": {
                    "rain_3h_mm": FLOOD_MAP_5YR_RAIN_3H_MM,
                    "rain_24h_mm": FLOOD_MAP_5YR_RAIN_24H_MM,
                },
                "river_ratio_escalation": FLOOD_RIVER_RATIO_ESCALATION,
                "storm_surge": {
                    "ssa1_wind_kmh": STORM_SURGE_SSA1_WIND_KMH,
                    "ssa2_wind_kmh": STORM_SURGE_SSA2_WIND_KMH,
                    "ssa3_wind_kmh": STORM_SURGE_SSA3_WIND_KMH,
                    "ssa4_wind_kmh": STORM_SURGE_SSA4_WIND_KMH,
                    "ssa4_pressure_hpa": STORM_SURGE_SSA4_PRESSURE_HPA,
                    "onshore_min_deg": STORM_SURGE_ONSHORE_MIN_DEG,
                    "onshore_max_deg": STORM_SURGE_ONSHORE_MAX_DEG,
                },
                "var_5yr_rain_3h": {
                    "l3": VAR_5YR_L3_RAIN_3H_MM,
                    "l2": VAR_5YR_L2_RAIN_3H_MM,
                    "l1": VAR_5YR_L1_RAIN_3H_MM,
                },
                "var_25yr_rain_3h": {
                    "l3": VAR_25YR_L3_RAIN_3H_MM,
                    "l2": VAR_25YR_L2_RAIN_3H_MM,
                    "l1": VAR_25YR_L1_RAIN_3H_MM,
                },
                "var_100yr_rain_3h": {
                    "l3": VAR_100YR_L3_RAIN_3H_MM,
                    "l2": VAR_100YR_L2_RAIN_3H_MM,
                    "l1": VAR_100YR_L1_RAIN_3H_MM,
                },
            },
            "spoof": {
                "enabled": WEATHER_SPOOF_ENABLED,
                "precipitation_mm": WEATHER_SPOOF_PRECIPITATION_MM,
                "rain_3h_mm": WEATHER_SPOOF_RAIN_3H_MM,
                "rain_24h_mm": WEATHER_SPOOF_RAIN_24H_MM,
                "river_enabled": RIVER_SPOOF_ENABLED,
                "river_discharge": RIVER_SPOOF_DISCHARGE,
                "river_discharge_mean": RIVER_SPOOF_DISCHARGE_MEAN,
                "river_discharge_p75": RIVER_SPOOF_DISCHARGE_P75,
                "storm_enabled": STORM_SURGE_SPOOF_ENABLED,
                "storm_wind_speed_kmh": STORM_SURGE_SPOOF_WIND_SPEED_KMH,
                "storm_wind_direction_deg": STORM_SURGE_SPOOF_WIND_DIRECTION_DEG,
                "storm_pressure_msl_hpa": STORM_SURGE_SPOOF_PRESSURE_MSL_HPA,
            },
            "flood_api_error": flood_api_error,
            "storm_api_error": storm_api_error,
        },
        "time": times[snapshot_idx] if 0 <= snapshot_idx < len(times) else None,
        "daily_time": daily_times[daily_idx] if 0 <= daily_idx < len(daily_times) else None,
        "timezone": data.get("timezone"),
        "hourly": {
            "time": times,
            "precipitation": hourly_precipitations,
            "temperature_2m": temps,
            "relative_humidity_2m": humidities,
        },
        "daily": {
            "time": daily_times,
            "rain_sum": daily_rain_sums_mut,
            "precipitation_hours": daily_precip_hours_mut,
        },
        "flood_daily": flood_api_daily,
        "storm_hourly": storm_api_hourly,
        "storm_time": storm_hour_time,
        "source": "open_meteo",
        "user_location_spoof": {
            "enabled": USER_LOCATION_SPOOF_ENABLED,
            "requested_latitude": req_lat,
            "requested_longitude": req_lng,
            "effective_latitude": lat,
            "effective_longitude": lng,
        },
    }


def _ring_bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]
    return (min(lons), max(lons), min(lats), max(lats))

def _ring_perimeter_m(ring: list[list[float]]) -> float:
    if len(ring) < 2:
        return 0.0
    total = 0.0
    for i in range(len(ring) - 1):
        a = ring[i]
        b = ring[i + 1]
        total += _haversine_km(a[1], a[0], b[1], b[0]) * 1000.0
    return total


def _normalize_ring(raw_ring: Any, *, max_points: int) -> list[list[float]] | None:
    if not isinstance(raw_ring, list):
        return None
    pts: list[list[float]] = []
    for p in raw_ring:
        if not isinstance(p, list) or len(p) < 2:
            continue
        lon = _finite_or_none(p[0])
        lat = _finite_or_none(p[1])
        if lon is None or lat is None:
            continue
        pts.append([lon, lat])
    if len(pts) < 4:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])

    max_points = int(max_points)
    if max_points > 2 and len(pts) > max_points:
        unique = pts[:-1]
        stride = max(1, math.ceil(len(unique) / (max_points - 1)))
        sampled = unique[::stride]
        if sampled[-1] != unique[-1]:
            sampled.append(unique[-1])
        if sampled[0] != sampled[-1]:
            sampled.append(sampled[0])
        pts = sampled
    return pts


def _iter_outer_rings_from_geometry(geometry: Any) -> list[Any]:
    if not isinstance(geometry, dict):
        return []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon" and isinstance(coords, list) and coords:
        return [coords[0]]
    if gtype == "MultiPolygon" and isinstance(coords, list):
        out: list[Any] = []
        for poly in coords:
            if isinstance(poly, list) and poly:
                out.append(poly[0])
        return out
    return []


def _rings_to_feature_collection(rings: list[list[list[float]]]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for idx, ring in enumerate(rings):
        if not ring:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"idx": idx + 1},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [ring],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _write_excluded_geojson_debug(
    *,
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    barrier_ring: list[list[float]],
    exclude_polygons: list[list[list[float]]],
    attempt_mode: str,
    explicit_error: str | None,
    final_route_coords: list[list[float]],
    meta: dict[str, Any] | None = None,
) -> None:
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "properties": {"role": "origin"},
            "geometry": {"type": "Point", "coordinates": [origin_lng, origin_lat]},
        },
        {
            "type": "Feature",
            "properties": {"role": "destination"},
            "geometry": {"type": "Point", "coordinates": [dest_lng, dest_lat]},
        },
    ]
    if barrier_ring:
        features.append(
            {
                "type": "Feature",
                "properties": {"role": "user_barrier"},
                "geometry": {"type": "Polygon", "coordinates": [barrier_ring]},
            }
        )
    if final_route_coords:
        features.append(
            {
                "type": "Feature",
                "properties": {"role": "final_route"},
                "geometry": {"type": "LineString", "coordinates": final_route_coords},
            }
        )
    for idx, ring in enumerate(exclude_polygons):
        if not ring:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"role": "exclude_polygon_preprocessed", "idx": idx + 1},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )

    payload = {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "attempt_mode": attempt_mode,
            "explicit_error": explicit_error,
            "exclude_polygon_count": len(exclude_polygons),
            "route_request": {
                "origin": {"lat": origin_lat, "lng": origin_lng},
                "destination": {"lat": dest_lat, "lng": dest_lng},
            },
            **(meta or {}),
        },
    }
    ROUTE_DEBUG_EXCLUDED_GEOJSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ROUTE_DEBUG_EXCLUDED_GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _straight_line_route(
    origin_lng: float, origin_lat: float, dest_lng: float, dest_lat: float
) -> dict[str, Any]:
    distance_km = _haversine_km(origin_lat, origin_lng, dest_lat, dest_lng)
    coords = [[origin_lng, origin_lat], [dest_lng, dest_lat]]
    duration_minutes = (distance_km / 25.0) * 60.0
    return {
        "coordinates": coords,
        "distance_km": distance_km,
        "duration_seconds": duration_minutes * 60.0,
    }


def _valhalla_alternates() -> int:
    try:
        return max(0, min(4, int(os.environ.get("VALHALLA_ALTERNATES", "3"))))
    except ValueError:
        return 3


def _request_candidates(
    *,
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    alternates: int,
    exclude_polygons: list[list[list[float]]] | None,
    exclude_locations: list[dict[str, float]] | None,
) -> list[dict[str, Any]]:
    return request_route_candidates(
        origin_lat,
        origin_lng,
        dest_lat,
        dest_lng,
        alternates=alternates,
        exclude_polygons=exclude_polygons,
        exclude_locations=exclude_locations,
    )


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    if n < 4:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) if (yj - yi) != 0 else 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lng, max_lng, min_lat, max_lat = bbox
    return min_lng <= lon <= max_lng and min_lat <= lat <= max_lat


def _collect_overlay_rings(overlay: dict[str, Any]) -> list[dict[str, Any]]:
    features = overlay.get("features")
    if not isinstance(features, list):
        return []
    rings: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        props = feature.get("properties")
        var_level: int | None = None
        haz_level: int | None = None
        overlay_type = "flood"
        if isinstance(props, dict):
            raw_var = props.get("Var")
            try:
                var_level = int(raw_var)
            except (TypeError, ValueError):
                var_level = None
            raw_haz = props.get("HAZ")
            try:
                haz_level = int(raw_haz)
            except (TypeError, ValueError):
                haz_level = None
            raw_overlay_type = props.get("overlay_type")
            if isinstance(raw_overlay_type, str) and raw_overlay_type.strip():
                overlay_type = raw_overlay_type.strip()
        for raw_ring in _iter_outer_rings_from_geometry(geometry):
            ring = _normalize_ring(raw_ring, max_points=0)
            if not ring:
                continue
            rings.append(
                {
                    "ring": ring,
                    "bbox": _ring_bbox(ring),
                    "var": var_level,
                    "haz": haz_level,
                    "overlay_type": overlay_type,
                }
            )
    return rings


def _densify_route_coords(
    coords: list[list[float]], target_spacing_m: float = 30.0, max_samples: int = 800
) -> list[list[float]]:
    """Insert intermediate samples between coords so flood-overlap sampling is dense
    even when Valhalla returns sparse polylines or we fall back to straight lines."""
    if not coords or len(coords) < 2:
        return list(coords or [])
    target_spacing_m = max(5.0, float(target_spacing_m))
    samples: list[list[float]] = []
    samples.append([float(coords[0][0]), float(coords[0][1])])
    for i in range(1, len(coords)):
        prev = coords[i - 1]
        curr = coords[i]
        try:
            plon, plat = float(prev[0]), float(prev[1])
            clon, clat = float(curr[0]), float(curr[1])
        except (TypeError, ValueError, IndexError):
            continue
        seg_km = _haversine_km(plat, plon, clat, clon)
        seg_m = seg_km * 1000.0
        if seg_m <= target_spacing_m:
            samples.append([clon, clat])
            continue
        steps = int(math.ceil(seg_m / target_spacing_m))
        steps = max(1, min(steps, 80))
        for s in range(1, steps + 1):
            t = s / steps
            samples.append([plon + (clon - plon) * t, plat + (clat - plat) * t])
        if len(samples) >= max_samples:
            break
    if len(samples) > max_samples:
        stride = max(1, len(samples) // max_samples)
        samples = samples[::stride]
        last = [float(coords[-1][0]), float(coords[-1][1])]
        if samples[-1] != last:
            samples.append(last)
    return samples


def _route_flood_metrics(
    coords: list[list[float]], overlay_rings: list[dict[str, Any]],
    map_code: int = 5,
    storm_severity: int = 0,
    policy: dict[str, Any] | None = None,
    *,
    target_spacing_m: float = 30.0,
    max_samples: int = 600,
    compound_mode: str = "max",
) -> dict[str, Any]:
    """Compute true flood-polygon overlap metrics for the chosen route.

    Returns weighted severity, raw overlap fraction, and per-class counts so
    safety_score can reflect actual collisions rather than a hardcoded 0.

    Class weights come from ``route_policy.compute_class_weights`` so the
    severity calibration lives in ``data/sheltr_route_policy.json``."""
    metrics = {
        "samples": 0,
        "hit_count": 0,
        "overlap_fraction": 0.0,
        "weighted_severity": 0.0,
        "max_severity_weight": 0.0,
        "flood_hit_count": 0,
        "storm_hit_count": 0,
        "var_counts": {1: 0, 2: 0, 3: 0},
        "haz_counts": {1: 0, 2: 0, 3: 0},
    }
    if not coords or not overlay_rings:
        return metrics
    samples = _densify_route_coords(coords, target_spacing_m=target_spacing_m, max_samples=max_samples)
    total = len(samples)
    if total < 1:
        return metrics
    if policy is None:
        try:
            policy = _route_policy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Route policy unavailable, falling back to defaults: %s", exc)
            policy = {}
    weights_bundle = compute_class_weights(
        policy=policy,
        map_code=int(map_code) if map_code is not None else None,
        storm_severity=int(storm_severity) if storm_severity is not None else None,
    )
    flood_weights = weights_bundle["flood"]
    storm_weights = weights_bundle["storm_surge"]
    weighted_sum = 0.0
    max_weight = 0.0
    hit_count = 0
    flood_hits = 0
    storm_hits = 0
    var_counts = {1: 0, 2: 0, 3: 0}
    haz_counts = {1: 0, 2: 0, 3: 0}
    for sample in samples:
        try:
            lon = float(sample[0])
            lat = float(sample[1])
        except (TypeError, ValueError, IndexError):
            continue
        best_weight = 0.0
        sum_weight = 0.0  # used only when compound_mode == "sum"
        best_overlay = ""
        best_var: int | None = None
        best_haz: int | None = None
        for ov in overlay_rings:
            bbox = ov.get("bbox")
            ring = ov.get("ring")
            if not isinstance(bbox, tuple) or not isinstance(ring, list):
                continue
            if not _point_in_bbox(lon, lat, bbox):
                continue
            if not _point_in_ring(lon, lat, ring):
                continue
            overlay_type = str(ov.get("overlay_type") or "flood")
            try:
                var_level = int(ov.get("var")) if ov.get("var") is not None else None
            except (TypeError, ValueError):
                var_level = None
            try:
                haz_level = int(ov.get("haz")) if ov.get("haz") is not None else None
            except (TypeError, ValueError):
                haz_level = None
            if overlay_type == "storm_surge":
                weight = storm_weights.get(haz_level or 3, 0.45)
            else:
                weight = flood_weights.get(var_level or 3, 0.45)
            sum_weight += weight
            if weight > best_weight:
                best_weight = weight
                best_overlay = overlay_type
                best_var = var_level
                best_haz = haz_level
        if best_weight <= 0.0:
            continue
        # compound_mode "sum": cap at amplifier_cap so a single sample with two
        # overlapping layers (e.g. flood + surge) can score above one layer alone.
        # "max" keeps current behaviour (peak severity per sample).
        contribution = sum_weight if compound_mode == "sum" else best_weight
        contribution = min(contribution, 1.5)  # safety cap, ~1.5x base
        hit_count += 1
        weighted_sum += contribution
        if contribution > max_weight:
            max_weight = contribution
        if best_overlay == "storm_surge":
            storm_hits += 1
            if best_haz in haz_counts:
                haz_counts[best_haz] += 1
        else:
            flood_hits += 1
            if best_var in var_counts:
                var_counts[best_var] += 1

    metrics["samples"] = total
    metrics["hit_count"] = hit_count
    metrics["overlap_fraction"] = hit_count / total
    metrics["weighted_severity"] = weighted_sum / total
    metrics["max_severity_weight"] = max_weight
    metrics["flood_hit_count"] = flood_hits
    metrics["storm_hit_count"] = storm_hits
    metrics["var_counts"] = var_counts
    metrics["haz_counts"] = haz_counts
    return metrics


def _route_overlap_hits(
    route_coords: list[list[float]], overlay_rings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if not route_coords or not overlay_rings:
        return hits
    for idx, coord in enumerate(route_coords):
        if not isinstance(coord, list) or len(coord) < 2:
            continue
        lon = _finite_or_none(coord[0])
        lat = _finite_or_none(coord[1])
        if lon is None or lat is None:
            continue
        for ov in overlay_rings:
            bbox = ov.get("bbox")
            ring = ov.get("ring")
            if not isinstance(bbox, tuple) or not isinstance(ring, list):
                continue
            if not _point_in_bbox(lon, lat, bbox):
                continue
            if _point_in_ring(lon, lat, ring):
                hits.append(
                    {
                        "index": idx,
                        "lon": lon,
                        "lat": lat,
                        "var": ov.get("var"),
                        "haz": ov.get("haz"),
                        "overlay_type": ov.get("overlay_type"),
                    }
                )
                break
    return hits


def _build_blocker_circle(lon: float, lat: float, radius_m: float, *, steps: int = 10) -> list[list[float]]:
    steps = max(6, int(steps))
    dlat = radius_m / 111_000.0
    dlon = radius_m / (111_000.0 * max(0.25, math.cos(math.radians(lat))))
    ring: list[list[float]] = []
    for i in range(steps):
        ang = (2.0 * math.pi * i) / steps
        plat = lat + dlat * math.sin(ang)
        plon = lon + dlon * math.cos(ang)
        ring.append([round(plon, 6), round(plat, 6)])
    ring.append(ring[0])
    return ring


def _sum_exclude_perimeter_m(polygons: list[list[list[float]]]) -> float:
    total = 0.0
    for ring in polygons:
        total += _ring_perimeter_m(ring)
    return total


def _is_far_enough_from_points(
    *,
    lat: float,
    lon: float,
    existing_points: list[tuple[float, float]],
    min_spacing_m: float,
) -> bool:
    for ex_lat, ex_lon in existing_points:
        if _haversine_km(lat, lon, ex_lat, ex_lon) * 1000.0 < min_spacing_m:
            return False
    return True


def _pick_best_candidate(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not cands:
        return None
    return min(
        cands,
        key=lambda c: (
            float(c.get("duration_seconds", float("inf"))),
            float(c.get("distance_km", float("inf"))),
        ),
    )


def _pick_safest_candidate(
    cands: list[dict[str, Any]],
    overlay_rings: list[dict[str, Any]] | None,
    *,
    overlap_penalty_ratio: float = 1.8,
    map_code: int = 5,
    storm_severity: int = 0,
    max_detour_ratio: float = 2.5,
) -> dict[str, Any] | None:
    """Rank Valhalla alternates by combined (duration, weighted flood overlap).

    Picks the candidate with the lowest flood-weighted score. A strong
    overlap_penalty_ratio (default 1.8) ensures routes with significantly
    less flood exposure are preferred even when they are somewhat longer,
    but candidates that are excessively long relative to the fastest route
    are excluded via max_detour_ratio so the reroute stays reasonable.
    Falls back to pure duration ranking when overlay data is missing.
    """
    if not cands:
        return None
    if not overlay_rings:
        return _pick_best_candidate(cands)

    durations = [
        float(c.get("duration_seconds", float("inf")))
        for c in cands
        if math.isfinite(float(c.get("duration_seconds", float("inf"))))
    ]
    baseline = min(durations) if durations else 0.0
    baseline = max(baseline, 60.0)
    max_duration = baseline * max_detour_ratio

    def score(cand: dict[str, Any]) -> tuple[float, float, float]:
        duration = float(cand.get("duration_seconds", float("inf")))
        if not math.isfinite(duration):
            return (float("inf"), float("inf"), float("inf"))
        # Exclude absurdly long detours — too far is not a valid reroute
        if duration > max_duration:
            return (float("inf"), float("inf"), duration)
        coords = cand.get("coordinates") or []
        metrics = _route_flood_metrics(coords, overlay_rings, map_code=map_code, storm_severity=storm_severity)
        weighted = float(metrics.get("weighted_severity") or 0.0)
        overlap_penalty = weighted * baseline * float(overlap_penalty_ratio)
        return (duration + overlap_penalty, weighted, duration)

    best = min(cands, key=score)
    # If the best score is infinite (all candidates exceed detour limit), fall
    # back to pure-duration ranking so we always return something.
    if not math.isfinite(score(best)[0]):
        return _pick_best_candidate(cands)
    return best


def _match_destination_center(
    centers_provider: CentersProvider, dest_lat: float, dest_lng: float
) -> dict[str, Any] | None:
    try:
        df = centers_provider.get_dataframe()
    except Exception:  # noqa: BLE001
        return None
    if df.empty:
        return None

    best: dict[str, Any] | None = None
    best_km = float("inf")
    exact_tolerance = 1e-5
    for row in df.to_dict(orient="records"):
        lat = _finite_or_none(row.get("latitude"))
        lng = _finite_or_none(row.get("longitude"))
        if lat is None or lng is None:
            continue
        if abs(lat - dest_lat) <= exact_tolerance and abs(lng - dest_lng) <= exact_tolerance:
            return {**row, "distance_to_destination_km": 0.0, "match_type": "exact_coords"}
        d_km = _haversine_km(dest_lat, dest_lng, lat, lng)
        if d_km < best_km:
            best_km = d_km
            best = row

    if best is None or best_km > 8.0:
        return None
    return {**best, "distance_to_destination_km": round(best_km, 4), "match_type": "nearest_coords"}


def build_route_response(
    *,
    data: dict[str, Any],
    centers_provider: CentersProvider,
    validate_coordinates_fn: Callable[[float, float], bool],
) -> dict[str, Any]:
    if not data:
        raise ValueError("No data provided")
    if "origin" not in data or "destination" not in data:
        raise ValueError("Missing required fields: 'origin' and 'destination'")

    origin = data["origin"]
    destination = data["destination"]
    if not isinstance(origin, list) or not isinstance(destination, list):
        raise ValueError("Coordinates must be arrays [longitude, latitude]")
    if len(origin) != 2 or len(destination) != 2:
        raise ValueError("Coordinates must have exactly 2 values")

    try:
        origin_lng, origin_lat = float(origin[0]), float(origin[1])
        dest_lng, dest_lat = float(destination[0]), float(destination[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("Coordinates must be numeric [longitude, latitude]") from exc

    requested_origin_lat = origin_lat
    requested_origin_lng = origin_lng
    origin_lat, origin_lng = _resolve_user_location(origin_lat, origin_lng)
    normalized_scenario = _normalize_flood_scenario(
        data.get("flood_scenario") if isinstance(data, dict) else None
    )

    if not validate_coordinates_fn(origin_lat, origin_lng):
        raise ValueError("Origin coordinates are outside supported area")
    if not validate_coordinates_fn(dest_lat, dest_lng):
        raise ValueError("Destination coordinates are outside supported area")

    warning_parts: list[str] = []
    fb = _straight_line_route(origin_lng, origin_lat, dest_lng, dest_lat)
    coords = fb["coordinates"]
    distance_km = float(fb["distance_km"])
    duration_minutes: float | None = None
    source = "fallback_straight_line"
    routing_mode = "straight_line_only"
    eta_model = "unavailable_not_drive_time"
    chosen_label = "primary"
    flood_overlap_fraction: float | None = None
    selected_flood_probability = 0.0
    selected_cost_matrix_total = distance_km * 1000.0
    selected_ml_smoothing_applied = False
    alternates_used = 0
    used_graph_route = False
    exclude_attempted = False
    exclude_used = False
    exclude_polygon_count = 0
    overlay_var_level: int | None = None
    overlay_feature_count = 0
    excluded_geojson: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    preprocessing_meta: dict[str, Any] = {}
    explicit_exclude_error: str | None = None
    storm_overlay_error: str | None = None
    final_route_coords: list[list[float]] = []
    exclude_polygons: list[list[list[float]]] = []
    barrier_ring: list[list[float]] = []
    overlay_rings: list[dict[str, Any]] = []
    overlay_ring_count_by_type: dict[str, int] = {}
    weather_data_age_seconds: float | None = None
    weather_fetched_at_iso: str | None = None
    overlay_data_age_seconds: float | None = None
    overlay_fetched_at_iso: str | None = None
    overlay_from_cache: bool | None = None

    destination_center = _match_destination_center(centers_provider, dest_lat, dest_lng)
    destination_center_safety = None
    destination_center_elevation = None
    destination_center_name: str | None = None
    destination_center_capacity: int | None = None
    destination_center_is_open: bool | None = None
    if destination_center is not None:
        destination_center_safety = _finite_or_none(destination_center.get("safety_score"))
        destination_center_elevation = _finite_or_none(destination_center.get("elevation"))
        dn = destination_center.get("name")
        if isinstance(dn, str) and dn.strip():
            destination_center_name = dn.strip()
        cap = destination_center.get("capacity")
        if cap is not None and cap != "":
            try:
                destination_center_capacity = int(float(cap))
            except (TypeError, ValueError):
                destination_center_capacity = None
        io = destination_center.get("is_open")
        if isinstance(io, bool):
            destination_center_is_open = io
        elif io is not None:
            try:
                destination_center_is_open = bool(int(io))
            except (TypeError, ValueError):
                destination_center_is_open = None

    stadia_ok = bool(get_stadia_api_key())
    if stadia_ok:
        alt_n = _valhalla_alternates()
        cands: list[dict[str, Any]] = []
        reroute_attempts = 0
        reroute_max = max(0, int(os.environ.get("ROUTE_FLOOD_REROUTE_MAX", "4")))
        blocker_radius_m = max(15.0, float(os.environ.get("ROUTE_FLOOD_BLOCK_RADIUS_M", "65")))
        blocker_spacing_m = max(30.0, float(os.environ.get("ROUTE_FLOOD_BLOCK_SPACING_M", "220")))
        blockers_per_pass = max(1, int(os.environ.get("ROUTE_FLOOD_BLOCKERS_PER_PASS", "12")))
        budget_raw_text = os.environ.get("ROUTE_EXCLUDE_MAX_CIRCUMFERENCE_M")
        if budget_raw_text is None:
            circumference_budget_raw = STADIA_EXCLUDE_CIRCUMFERENCE_LIMIT_M * 0.9
        else:
            try:
                circumference_budget_raw = float(budget_raw_text)
            except ValueError:
                circumference_budget_raw = STADIA_EXCLUDE_CIRCUMFERENCE_LIMIT_M * 0.9
        circumference_budget_m = (
            float("inf") if circumference_budget_raw <= 0 else max(300.0, circumference_budget_raw)
        )
        overlap_counts_per_pass: list[int] = []
        blockers_added_per_pass: list[int] = []
        skipped_by_spacing_total = 0
        skipped_by_budget_total = 0
        reroute_query_attempts = 0
        adaptive_backoff_rounds = 0
        blocker_points: list[tuple[float, float]] = []

        try:
            overlay = flood_overlay_snapshot(
                latitude=origin_lat,
                longitude=origin_lng,
                scenario_override=normalized_scenario,
            )
            mode_num = _finite_or_none(overlay.get("display_mode"))
            overlay_var_level = int(mode_num) if mode_num is not None else None
            features = overlay.get("features")
            if isinstance(features, list):
                overlay_feature_count = len(features)
            raw_storm_error = overlay.get("storm_surge_error")
            if isinstance(raw_storm_error, str) and raw_storm_error.strip():
                storm_overlay_error = raw_storm_error.strip()
                warning_parts.append(
                    f"Storm surge overlay is active but invalid ({storm_overlay_error[:220]})."
                )
            overlay_rings = _collect_overlay_rings(overlay)
            for ring_item in overlay_rings:
                key = str(ring_item.get("overlay_type") or "flood")
                overlay_ring_count_by_type[key] = overlay_ring_count_by_type.get(key, 0) + 1
            weather_data_age_seconds = _finite_or_none(overlay.get("weather_data_age_seconds"))
            weather_fetched_at_iso = (
                overlay.get("weather_fetched_at")
                if isinstance(overlay.get("weather_fetched_at"), str)
                else None
            )
            overlay_data_age_seconds = _finite_or_none(overlay.get("data_age_seconds"))
            overlay_fetched_at_iso = (
                overlay.get("fetched_at")
                if isinstance(overlay.get("fetched_at"), str)
                else None
            )
            overlay_from_cache = bool(overlay.get("from_cache"))
        except Exception as e:  # noqa: BLE001
            warning_parts.append(f"Flood overlay read failed ({str(e)[:220]}).")

        try:
            cands = _request_candidates(
                origin_lat=origin_lat,
                origin_lng=origin_lng,
                dest_lat=dest_lat,
                dest_lng=dest_lng,
                alternates=alt_n,
                exclude_polygons=None,
                exclude_locations=None,
            )
        except Exception as e:  # noqa: BLE001
            explicit_exclude_error = str(e)
            warning_parts.append(
                f"Valhalla routing failed ({str(e)[:220]}). Using straight-line fallback."
            )
            cands = []

        if cands:
            # Initial selection prefers safer alternates when overlay rings exist;
            # the iterative reroute loop below still tightens this further.
            best = _pick_safest_candidate(
                cands, overlay_rings,
                map_code=overlay.get("flood_overlay_map_selection", {}).get("map_code", 5),
                storm_severity=overlay.get("storm_surge_overlay_selection", {}).get("severity", 0),
            ) or cands[0]
            coords = best["coordinates"]
            final_route_coords = list(coords)
            distance_km = float(best.get("distance_km", distance_km))
            duration_minutes = float(best.get("duration_seconds", 0.0)) / 60.0
            selected_flood_probability = 0.0
            selected_cost_matrix_total = distance_km * 1000.0
            selected_ml_smoothing_applied = False
            alternates_used = max(0, len(cands) - 1)
            chosen_label = str(best.get("label", "primary"))
            routing_mode = "graph_route"
            eta_model = "valhalla"
            used_graph_route = True
            source = "stadia_valhalla"

            if overlay_rings:
                while reroute_attempts <= reroute_max:
                    current_hits = _route_overlap_hits(coords, overlay_rings)
                    overlap_counts_per_pass.append(len(current_hits))
                    if not current_hits:
                        break

                    exclude_attempted = True
                    if reroute_attempts >= reroute_max:
                        warning_parts.append(
                            "Route still overlaps active flood polygons after max reroute passes."
                        )
                        break

                    selected_hits: list[dict[str, Any]] = []
                    for hit in current_hits:
                        if len(selected_hits) >= blockers_per_pass:
                            break
                        lat = float(hit["lat"])
                        lon = float(hit["lon"])
                        if not _is_far_enough_from_points(
                            lat=lat,
                            lon=lon,
                            existing_points=blocker_points,
                            min_spacing_m=blocker_spacing_m,
                        ):
                            skipped_by_spacing_total += 1
                            continue
                        if not _is_far_enough_from_points(
                            lat=lat,
                            lon=lon,
                            existing_points=[(float(x["lat"]), float(x["lon"])) for x in selected_hits],
                            min_spacing_m=blocker_spacing_m,
                        ):
                            skipped_by_spacing_total += 1
                            continue
                        selected_hits.append(hit)

                    if not selected_hits:
                        warning_parts.append(
                            "Route overlaps flood map, but no additional spaced blocker points were available."
                        )
                        break

                    base_perimeter_m = _sum_exclude_perimeter_m(exclude_polygons)
                    added_now = 0
                    reroute_success = False
                    successful_cands: list[dict[str, Any]] = []
                    adaptive_error: str | None = None

                    for radius_scale in (1.0, 0.75, 0.5):
                        candidate_rings: list[list[list[float]]] = []
                        candidate_points: list[tuple[float, float]] = []
                        trial_perimeter_m = base_perimeter_m
                        for hit in selected_hits:
                            hit_lon = float(hit["lon"])
                            hit_lat = float(hit["lat"])
                            ring = _build_blocker_circle(
                                hit_lon,
                                hit_lat,
                                blocker_radius_m * radius_scale,
                            )
                            ring_perim_m = _ring_perimeter_m(ring)
                            if (
                                not math.isinf(circumference_budget_m)
                                and trial_perimeter_m + ring_perim_m > circumference_budget_m
                            ):
                                skipped_by_budget_total += 1
                                continue
                            candidate_rings.append(ring)
                            candidate_points.append((hit_lat, hit_lon))
                            trial_perimeter_m += ring_perim_m

                        if not candidate_rings:
                            continue

                        take_n = len(candidate_rings)
                        while take_n > 0:
                            trial_polygons = exclude_polygons + candidate_rings[:take_n]
                            reroute_query_attempts += 1
                            try:
                                cands = _request_candidates(
                                    origin_lat=origin_lat,
                                    origin_lng=origin_lng,
                                    dest_lat=dest_lat,
                                    dest_lng=dest_lng,
                                    alternates=alt_n,
                                    exclude_polygons=trial_polygons,
                                    exclude_locations=None,
                                )
                                if cands:
                                    exclude_polygons = trial_polygons
                                    blocker_points.extend(candidate_points[:take_n])
                                    added_now = take_n
                                    successful_cands = cands
                                    reroute_success = True
                                    break
                                adaptive_error = "Valhalla reroute returned no route."
                            except Exception as e:  # noqa: BLE001
                                adaptive_error = str(e)
                            adaptive_backoff_rounds += 1
                            take_n //= 2
                        if reroute_success:
                            break

                    blockers_added_per_pass.append(added_now)
                    exclude_polygon_count = len(exclude_polygons)
                    exclude_used = exclude_polygon_count > 0
                    if not reroute_success:
                        if added_now == 0 and adaptive_error is None:
                            warning_parts.append(
                                "Route overlaps flood map, but exclude polygon circumference budget was exhausted."
                            )
                        else:
                            explicit_exclude_error = adaptive_error
                            warning_parts.append(
                                f"Reroute failed after adaptive retries ({str(adaptive_error)[:220]}). Keeping previous route."
                            )
                        break

                    reroute_attempts += 1
                    cands = successful_cands
                    map_code_val = overlay.get("flood_overlay_map_selection", {}).get("map_code", 5)
                    storm_sev_val = overlay.get("storm_surge_overlay_selection", {}).get("severity", 0)
                    best = _pick_safest_candidate(
                        cands, overlay_rings,
                        map_code=map_code_val,
                        storm_severity=storm_sev_val,
                    ) or cands[0]
                    coords = best["coordinates"]
                    final_route_coords = list(coords)
                    distance_km = float(best.get("distance_km", distance_km))
                    duration_minutes = float(best.get("duration_seconds", 0.0)) / 60.0
                    alternates_used = max(0, len(cands) - 1)
                    chosen_label = str(best.get("label", "primary"))
                    source = "stadia_valhalla_iterative_overlap_blockers"

                    # Early stop: if all candidates still have heavy flood exposure,
                    # further rerouting through crowded polygons won't help.
                    best_metrics = _route_flood_metrics(
                        list(coords), overlay_rings, map_code=map_code_val, storm_severity=storm_sev_val
                    )
                    best_weighted = float(best_metrics.get("weighted_severity") or 0.0)
                    if best_weighted > 0.75 and reroute_attempts >= 2:
                        warning_parts.append(
                            "Area is heavily flooded — all candidate routes still pass through flood polygons. "
                            "Keeping best available route."
                        )
                        break
            else:
                if overlay_feature_count > 0:
                    warning_parts.append("Active flood overlay had no usable polygon rings for overlap checks.")

        else:
            warning_parts.append("Valhalla returned no routes.")

        preprocessing_meta = {
            "overlay_feature_count": overlay_feature_count,
            "overlay_ring_count": len(overlay_rings),
            "overlay_ring_count_by_type": overlay_ring_count_by_type,
            "reroute_max": reroute_max,
            "reroute_attempts": reroute_attempts,
            "reroute_query_attempts": reroute_query_attempts,
            "adaptive_backoff_rounds": adaptive_backoff_rounds,
            "overlap_points_per_pass": overlap_counts_per_pass,
            "blockers_added_per_pass": blockers_added_per_pass,
            "blocker_radius_m": blocker_radius_m,
            "blocker_spacing_m": blocker_spacing_m,
            "blockers_per_pass": blockers_per_pass,
            "exclude_polygon_count": len(exclude_polygons),
            "exclude_total_circumference_m": round(_sum_exclude_perimeter_m(exclude_polygons), 2),
            "exclude_budget_m": None if math.isinf(circumference_budget_m) else round(circumference_budget_m, 2),
            "skipped_by_spacing_total": skipped_by_spacing_total,
            "skipped_by_budget_total": skipped_by_budget_total,
        }
        _debug_log(
            (
                "Route preprocess: overlay_features=%s overlay_rings=%s reroutes=%s "
                "reroute_queries=%s backoff_rounds=%s overlaps=%s blockers=%s total_excludes=%s ring_types=%s"
            ),
            overlay_feature_count,
            len(overlay_rings),
            reroute_attempts,
            reroute_query_attempts,
            adaptive_backoff_rounds,
            overlap_counts_per_pass,
            blockers_added_per_pass,
            len(exclude_polygons),
            overlay_ring_count_by_type,
        )
        excluded_geojson = _rings_to_feature_collection(exclude_polygons)
        try:
            _write_excluded_geojson_debug(
                origin_lat=origin_lat,
                origin_lng=origin_lng,
                dest_lat=dest_lat,
                dest_lng=dest_lng,
                barrier_ring=barrier_ring,
                exclude_polygons=exclude_polygons,
                attempt_mode="iterative_route_overlap_blockers",
                explicit_error=explicit_exclude_error,
                final_route_coords=final_route_coords,
                meta=preprocessing_meta,
            )
        except Exception as e:  # noqa: BLE001
            warning_parts.append(
                f"Excluded polygons debug write failed ({str(e)[:220]})."
            )
    else:
        warning_parts.append("Stadia API key is missing. Using straight-line fallback.")

    if not used_graph_route:
        warning_parts.append(
            "Road routing service was unavailable, so a straight line is shown. "
            "This is not turn-by-turn navigation. Drive-time ETA is omitted; distance is geodesic only."
        )

    try:
        active_policy = _route_policy()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Route policy load failed; using empty policy: %s", exc)
        active_policy = {}
    flood_metrics = _route_flood_metrics(coords, overlay_rings, policy=active_policy)
    flood_scoring = bool(overlay_rings)
    if flood_scoring:
        flood_overlap_fraction = float(flood_metrics.get("overlap_fraction") or 0.0)
    river_bundle = river_metrics_for_route_coords(coords)
    try:
        if len(coords) >= 2:
            mid = coords[len(coords) // 2]
            mw_lon, mw_lat = float(mid[0]), float(mid[1])
        else:
            mw_lat, mw_lon = origin_lat, origin_lng
    except (TypeError, ValueError, IndexError):
        mw_lat, mw_lon = origin_lat, origin_lng
    nearest_waterway = nearest_waterway_to_point(mw_lat, mw_lon)

    policy_features = {
        "weighted_severity": float(flood_metrics.get("weighted_severity") or 0.0),
        "max_severity_weight": float(flood_metrics.get("max_severity_weight") or 0.0),
        "overlap_hit_count": int(flood_metrics.get("hit_count") or 0),
        "flood_overlap_fraction": flood_overlap_fraction,
        "flood_scoring_available": flood_scoring,
        "used_graph_route": used_graph_route,
        "flood_overlay_loaded": flood_scoring,
        "scenario_normalized": normalized_scenario,
        "storm_overlay_error": bool(storm_overlay_error),
        "weather_data_age_seconds": weather_data_age_seconds,
        "exclude_attempted": exclude_attempted,
        "exclude_used": exclude_used,
        "reroute_attempts": int(preprocessing_meta.get("reroute_attempts") or 0),
        "reroute_max": int(preprocessing_meta.get("reroute_max") or 0),
        "waterways_loaded": bool(river_bundle.get("waterways_loaded")),
    }
    policy_result = apply_policy(policy_features, active_policy)
    elevation_penalty = float(policy_result.get("elevation_penalty") or 0.0)
    selected_flood_probability = float(policy_result.get("flood_probability") or 0.0)
    flood_risk = float(policy_result.get("flood_risk") or 0.0)
    safety_score = float(policy_result.get("safety_score") or 0.08)
    confidence_score = float(policy_result.get("confidence_score") or 0.0)
    confidence_label = str(policy_result.get("confidence_label") or "very_low")
    confidence_components = dict(policy_result.get("confidence_components") or {})
    decision = dict(policy_result.get("decision") or {})
    policy_version_str = str(policy_result.get("policy_version") or "unknown")

    cost_parts = [source]
    if exclude_used:
        cost_parts.append("exclude_polygons_iterative")
    elif exclude_attempted:
        cost_parts.append("exclude_polygons_iterative_failed")
    if alternates_used > 0:
        cost_parts.append(f"alternates={alternates_used}")
    cost_parts.append("distance_only")
    if chosen_label and chosen_label != "primary":
        cost_parts.append(f"picked:{chosen_label}")
    cost_function = "+".join(cost_parts)

    route_stats = {
        "routing_engine": source,
        "routing_mode": routing_mode,
        "alternates_considered": alternates_used,
        "exclude_polygons_attempted": exclude_attempted,
        "exclude_polygons_used": exclude_used,
        "exclude_polygon_count": exclude_polygon_count,
        "exclude_error": explicit_exclude_error,
        "overlay_var_level": overlay_var_level,
        "overlay_feature_count": overlay_feature_count,
        "excluded_polygons_geojson_feature_count": len(excluded_geojson.get("features", [])),
        "excluded_polygons_geojson_path": str(ROUTE_DEBUG_EXCLUDED_GEOJSON_PATH),
        "flood_scenario": normalized_scenario or "auto",
        "storm_surge_overlay_error": storm_overlay_error,
        **preprocessing_meta,
        "flood_overlap_fraction": (
            round(flood_overlap_fraction, 4) if flood_overlap_fraction is not None else None
        ),
        "flood_overlap_samples": int(flood_metrics.get("samples") or 0),
        "flood_overlap_hit_count": int(flood_metrics.get("hit_count") or 0),
        "flood_overlap_var_counts": dict(flood_metrics.get("var_counts") or {}),
        "flood_overlap_haz_counts": dict(flood_metrics.get("haz_counts") or {}),
        "flood_overlap_weighted_severity": round(
            float(flood_metrics.get("weighted_severity") or 0.0), 4
        ),
        "flood_overlap_max_severity_weight": round(
            float(flood_metrics.get("max_severity_weight") or 0.0), 4
        ),
        "selected_flood_probability": round(selected_flood_probability, 4),
        "selected_cost_matrix_total": round(selected_cost_matrix_total, 2),
        "cost_matrix_formula": "distance_m",
        "ml_smoothing_applied": selected_ml_smoothing_applied,
        "elevation_penalty": round(elevation_penalty, 4),
        "elevation_samples": 0,
        "route_min_elevation_m": None,
        "route_avg_elevation_m": None,
        "route_max_elevation_m": None,
        "elevation_raster_path": None,
        "elevation_data_error": None,
        "destination_center_match_type": destination_center.get("match_type")
        if destination_center
        else None,
        "user_location_spoof_enabled": USER_LOCATION_SPOOF_ENABLED,
        "requested_origin_latitude": requested_origin_lat,
        "requested_origin_longitude": requested_origin_lng,
        "effective_origin_latitude": origin_lat,
        "effective_origin_longitude": origin_lng,
        "weather_data_age_seconds": (
            round(weather_data_age_seconds, 3) if weather_data_age_seconds is not None else None
        ),
        "weather_fetched_at": weather_fetched_at_iso,
        "overlay_data_age_seconds": (
            round(overlay_data_age_seconds, 3) if overlay_data_age_seconds is not None else None
        ),
        "overlay_fetched_at": overlay_fetched_at_iso,
        "overlay_from_cache": overlay_from_cache,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "confidence_components": {k: round(v, 4) for k, v in confidence_components.items()},
        "waterways_loaded": river_bundle.get("waterways_loaded"),
        "waterway_load_error": river_bundle.get("waterway_load_error"),
        "river_closest_sample_m": river_bundle.get("river_closest_sample_m"),
        "river_median_min_distance_m": river_bundle.get("river_median_min_distance_m"),
        "waterway_near_route_fraction_50m": river_bundle.get("fraction_within_50m"),
        "waterway_near_route_fraction_100m": river_bundle.get("fraction_within_100m"),
        "waterway_near_route_fraction_150m": river_bundle.get("fraction_within_150m"),
        "waterway_samples": river_bundle.get("samples"),
    }

    response: dict[str, Any] = {
        "route": {
            "coordinates": coords,
            "distance_km": round(distance_km, 3),
            "duration_minutes": None if duration_minutes is None else round(duration_minutes, 1),
            "safety_score": safety_score,
            "flood_risk": flood_risk,
            "flood_overlap_fraction": round(flood_overlap_fraction, 4)
            if flood_scoring and flood_overlap_fraction is not None
            else None,
            "flood_scoring_available": flood_scoring,
            "cost_function": cost_function,
            "cost_matrix_total": round(selected_cost_matrix_total, 2),
            "routing_mode": routing_mode,
            "eta_model": eta_model,
            "alternates_considered": alternates_used,
            "exclude_polygons_retry": exclude_used,
            "elevation_penalty": round(elevation_penalty, 4),
            "route_min_elevation_m": None,
            "route_avg_elevation_m": None,
            "route_max_elevation_m": None,
            "destination_center_safety_score": destination_center_safety,
            "destination_center_elevation": destination_center_elevation,
            "destination_center_name": destination_center_name,
            "destination_center_capacity": destination_center_capacity,
            "destination_center_is_open": destination_center_is_open,
            "confidence_score": confidence_score,
            "confidence_label": confidence_label,
            "weather_data_age_seconds": (
                round(weather_data_age_seconds, 3) if weather_data_age_seconds is not None else None
            ),
            "overlay_data_age_seconds": (
                round(overlay_data_age_seconds, 3) if overlay_data_age_seconds is not None else None
            ),
            "river_closest_sample_m": river_bundle.get("river_closest_sample_m"),
            "waterway_near_route_fraction_100m": river_bundle.get("fraction_within_100m"),
            "nearest_waterway": nearest_waterway,
            "decision": decision,
            "policy_version": policy_version_str,
        },
        "route_stats": {
            **route_stats,
            "decision": decision,
            "policy_version": policy_version_str,
        },
        "excluded_polygons_geojson": excluded_geojson,
        "segments": [],
        "status": "success" if routing_mode == "graph_route" else "degraded_straight_line",
    }
    if routing_mode == "straight_line_only":
        response["route"]["straight_line_distance_km"] = round(distance_km, 3)
        response["route"]["heuristic_transit_minutes_if_clear_roads"] = round(
            (distance_km / 25.0) * 60.0, 1
        )
    warning = " ".join(x.strip() for x in warning_parts if x and x.strip())
    if warning:
        response["warning"] = warning
    _debug_log(
        "Route result: mode=%s engine=%s exclude_used=%s warning=%s",
        routing_mode,
        source,
        exclude_used,
        warning,
    )
    return response
