"""Distance from routed polylines to NCR clipped waterway LineStrings."""

from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, shape
from shapely.ops import nearest_points

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RIVERS_GEOJSON_PATH = ROOT / "data" / "NCR_Rivers_Clipped.json"

_LOCK = threading.Lock()
_LINES: list[LineString] | None = None
_LOAD_ERROR: str | None = None


def _deg_distance_to_meters(lon: float, lat: float, deg_distance: float) -> float:
    return float(deg_distance) * 111_320.0 * max(0.25, math.cos(math.radians(lat)))


def _ensure_loaded() -> None:
    global _LINES, _LOAD_ERROR
    if _LINES is not None:
        return
    with _LOCK:
        if _LINES is not None:
            return
        if not RIVERS_GEOJSON_PATH.is_file():
            _LOAD_ERROR = "rivers_file_missing"
            _LINES = []
            return
        try:
            with open(RIVERS_GEOJSON_PATH, "r", encoding="utf-8") as f:
                gj = json.load(f)
            feats = gj.get("features") if isinstance(gj, dict) else None
            if not isinstance(feats, list):
                _LOAD_ERROR = "rivers_invalid_geojson"
                _LINES = []
                return
            lines: list[LineString] = []
            for feat in feats:
                if not isinstance(feat, dict):
                    continue
                geom_d = feat.get("geometry")
                if not isinstance(geom_d, dict):
                    continue
                try:
                    g = shape(geom_d)
                except Exception:  # noqa: BLE001
                    continue
                if g.geom_type == "LineString" and len(g.coords) >= 2:
                    lines.append(LineString([(float(c[0]), float(c[1])) for c in g.coords]))
                elif g.geom_type == "MultiLineString":
                    for part in g.geoms:
                        if len(part.coords) >= 2:
                            lines.append(LineString([(float(c[0]), float(c[1])) for c in part.coords]))

            _LINES = [ln for ln in lines if not ln.is_empty and ln.length > 0]
            if not _LOAD_ERROR:
                _LOAD_ERROR = None
            if not _LINES:
                _LOAD_ERROR = "rivers_no_lines"
            logger.info("Loaded %s waterway segments from %s", len(_LINES), RIVERS_GEOJSON_PATH)
        except Exception as exc:  # noqa: BLE001
            logger.warning("River layer load failed: %s", exc)
            _LOAD_ERROR = str(exc)[:240]
            _LINES = []


def waterways_available() -> bool:
    _ensure_loaded()
    return bool(_LINES)


def waterways_metadata() -> dict[str, Any]:
    _ensure_loaded()
    return {
        "path": str(RIVERS_GEOJSON_PATH.resolve()),
        "line_segment_count": len(_LINES or []),
        "error": _LOAD_ERROR,
    }


def river_metrics_for_route_coords(
    coords: list[list[float]],
    *,
    sample_stride: int = 3,
) -> dict[str, Any]:
    """
    Proximity from route samples to nearest waterway polylines (degree distance scaled to metres).
    Supplementary signal for correlations and grounding; hazard polygons remain primary.
    """
    _ensure_loaded()
    lines = _LINES or []
    if not coords or len(coords) < 2 or not lines:
        return {
            "waterways_loaded": bool(lines),
            "waterway_load_error": _LOAD_ERROR if not lines else None,
            "samples": 0,
            "river_closest_sample_m": None,
            "river_median_min_distance_m": None,
            "fraction_within_50m": None,
            "fraction_within_100m": None,
            "fraction_within_150m": None,
        }

    sampled: list[tuple[float, float]] = []
    for i in range(0, len(coords), max(1, sample_stride)):
        c = coords[i]
        try:
            sampled.append((float(c[0]), float(c[1])))
        except (TypeError, ValueError, IndexError):
            continue
    try:
        last = coords[-1]
        last_pt = (float(last[0]), float(last[1]))
        if sampled and sampled[-1] != last_pt:
            sampled.append(last_pt)
    except (TypeError, ValueError, IndexError):
        pass

    if len(sampled) < 2:
        return {
            "waterways_loaded": True,
            "samples": len(sampled),
            "waterway_load_error": _LOAD_ERROR,
            "river_closest_sample_m": None,
            "river_median_min_distance_m": None,
            "fraction_within_50m": None,
            "fraction_within_100m": None,
            "fraction_within_150m": None,
        }

    mins_m: list[float] = []
    within50 = within100 = within150 = 0

    for lon, lat in sampled:
        pt = Point(lon, lat)
        best = float("inf")
        for ln in lines:
            try:
                d_m = _deg_distance_to_meters(lon, lat, pt.distance(ln))
                if d_m < best:
                    best = d_m
            except Exception:  # noqa: BLE001
                continue
        if math.isfinite(best):
            mins_m.append(best)
            if best <= 50:
                within50 += 1
            if best <= 100:
                within100 += 1
            if best <= 150:
                within150 += 1

    if not mins_m:
        return {
            "waterways_loaded": True,
            "samples": len(sampled),
            "waterway_load_error": _LOAD_ERROR,
            "river_closest_sample_m": None,
            "river_median_min_distance_m": None,
            "fraction_within_50m": 0.0,
            "fraction_within_100m": 0.0,
            "fraction_within_150m": 0.0,
        }

    n = len(mins_m)
    sorted_m = sorted(mins_m)
    median = sorted_m[n // 2]
    return {
        "waterways_loaded": True,
        "waterway_load_error": _LOAD_ERROR,
        "samples": n,
        "river_closest_sample_m": round(min(mins_m), 1),
        "river_median_min_distance_m": round(float(median), 1),
        "fraction_within_50m": round(within50 / n, 4),
        "fraction_within_100m": round(within100 / n, 4),
        "fraction_within_150m": round(within150 / n, 4),
    }

def nearest_waterway_to_point(lat: float, lon: float) -> dict[str, Any]:
    _ensure_loaded()
    lines = _LINES or []
    if not lines:
        return {"distance_m": None, "direction": None, "point": None}

    pt = Point(lon, lat)
    best_dist = float("inf")
    best_point = None

    for ln in lines:
        try:
            p1, p2 = nearest_points(pt, ln)
            d_m = _deg_distance_to_meters(lon, lat, pt.distance(p2))
            if d_m < best_dist:
                best_dist = d_m
                best_point = (p2.x, p2.y)
        except Exception:
            continue
            
    if best_point is None:
        return {"distance_m": None, "direction": None, "point": None}

    dx = best_point[0] - lon
    dy = best_point[1] - lat
    # dx is longitude diff, dy is latitude diff
    # scale dx by cos(lat) to make it proportional
    dx_scaled = dx * math.cos(math.radians(lat))
    angle = math.degrees(math.atan2(dy, dx_scaled))
    if angle < 0:
        angle += 360
        
    directions = ["East", "Northeast", "North", "Northwest", "West", "Southwest", "South", "Southeast"]
    idx = round(angle / 45) % 8
    compass = directions[idx]

    return {
        "distance_m": round(best_dist),
        "direction": compass,
        "point": {"lat": best_point[1], "lon": best_point[0]}
    }

