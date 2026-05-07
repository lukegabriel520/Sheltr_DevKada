"""Load Metro Manila flood hazard GeoJSON and score points or routes."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, box, shape
from shapely.prepared import prep
from shapely.validation import make_valid

logger = logging.getLogger(__name__)

# Metro Manila service bbox (matches safe_server.validate_coordinates)
MM_MIN_LAT, MM_MAX_LAT = 14.0, 15.2
MM_MIN_LNG, MM_MAX_LNG = 120.0, 122.0
_MM_BOX = box(MM_MIN_LNG, MM_MIN_LAT, MM_MAX_LNG, MM_MAX_LAT)

_MAX_LINE_VERTICES = 2500


def _clip_coords_to_bbox(coords: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for c in coords:
        if len(c) < 2:
            continue
        lng, lat = float(c[0]), float(c[1])
        if MM_MIN_LNG <= lng <= MM_MAX_LNG and MM_MIN_LAT <= lat <= MM_MAX_LAT:
            out.append([lng, lat])
    return out


def _decimate(coords: list[list[float]], max_pts: int) -> list[list[float]]:
    if len(coords) <= max_pts:
        return coords
    step = max(1, len(coords) // max_pts)
    return coords[::step]


class FloodHazardIndex:
    """Uses official flood polygons (e.g. 25-year hazard) for conservative heuristics."""

    def __init__(self, geojson_path: str | Path | None) -> None:
        self._path = Path(geojson_path) if geojson_path else None
        self._lock = threading.Lock()
        self._prepared_geoms: list[Any] = []
        self._geoms: list[Any] = []
        self._loaded = False
        self._error: str | None = None
        self._feature_count = 0
        self._revision: str | None = None

    def _compute_revision(self) -> str | None:
        if not self._path or not self._path.is_file():
            return None
        try:
            st = self._path.stat()
            raw = f"{self._path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()
            return hashlib.sha256(raw).hexdigest()[:16]
        except OSError:
            return None

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self._path or not self._path.is_file():
                logger.warning("Flood GeoJSON not found; flood scoring disabled.")
                self._error = "no_file"
                return
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    gj = json.load(f)
                feats = gj.get("features") or []
                geoms: list[Any] = []
                for feat in feats:
                    geom_raw = feat.get("geometry") if isinstance(feat, dict) else None
                    if not geom_raw:
                        continue
                    try:
                        g = shape(geom_raw)
                    except Exception:  # noqa: BLE001
                        continue
                    if g.is_empty:
                        continue
                    # Fast path first; repair only if later operations fail.
                    try:
                        _ = g.area
                    except Exception:  # noqa: BLE001
                        try:
                            g = make_valid(g)
                        except Exception:  # noqa: BLE001
                            g = g.buffer(0)
                    if g.is_empty:
                        continue
                    geoms.append(g)

                if not geoms:
                    self._error = "empty"
                    return
                clipped: list[Any] = []
                for g in geoms:
                    try:
                        cg = g.intersection(_MM_BOX)
                    except Exception:  # noqa: BLE001
                        try:
                            cg = g.buffer(0).intersection(_MM_BOX)
                        except Exception:  # noqa: BLE001
                            continue
                    if not cg.is_empty:
                        clipped.append(cg)
                if not clipped:
                    self._error = "empty_after_clip"
                    return
                self._geoms = clipped
                self._prepared_geoms = [prep(g) for g in clipped]
                self._feature_count = len(geoms)
                self._revision = self._compute_revision()
                self._error = None
                logger.info("Loaded flood hazard polygons: %s features", len(geoms))
            except Exception as e:  # noqa: BLE001
                logger.exception("Failed to load flood GeoJSON: %s", e)
                self._error = str(e)

    def available(self) -> bool:
        self._load()
        return len(self._prepared_geoms) > 0

    def metadata(self) -> dict[str, Any]:
        self._load()
        return {
            "flood_layer_loaded": len(self._prepared_geoms) > 0,
            "flood_layer_error": self._error,
            "flood_feature_count": self._feature_count,
            "flood_data_revision": self._revision,
            "flood_geojson_path": str(self._path) if self._path else None,
        }

    def point_unsafe_probability(self, lng: float, lat: float) -> float | None:
        """0–1 heuristic inside hazard layer; None if hazard data unavailable (do not guess)."""
        self._load()
        if not self._prepared_geoms:
            return None
        pt = Point(lng, lat)
        for prepared in self._prepared_geoms:
            if prepared.contains(pt):
                return 0.82
        try:
            for geom in self._geoms:
                if geom.buffer(0.0008).contains(pt):
                    return 0.45
        except Exception:  # noqa: BLE001
            pass
        return 0.08

    def route_flood_fraction(self, coords: list[list[float]], samples: int = 140) -> float:
        """Fraction of path length inside hazard polygons (equal arc-length samples)."""
        self._load()
        if not self._prepared_geoms or len(coords) < 2:
            return 0.0
        clipped = _clip_coords_to_bbox(coords)
        if len(clipped) < 2:
            return 0.0
        thin = _decimate(clipped, _MAX_LINE_VERTICES)
        line = LineString([(c[0], c[1]) for c in thin])
        if line.is_empty or line.length == 0:
            return 0.0
        length = float(line.length)
        if samples < 8:
            samples = 8
        inside = 0
        for i in range(samples):
            t = i / max(samples - 1, 1)
            pt = line.interpolate(min(length, t * length))
            if any(prepared.contains(pt) for prepared in self._prepared_geoms):
                inside += 1
        return inside / samples

    def build_exclude_polygon_rings(
        self,
        origin_lng: float,
        origin_lat: float,
        dest_lng: float,
        dest_lat: float,
        *,
        max_rings: int = 2,
        max_ring_vertices: int = 48,
    ) -> list[list[list[float]]]:
        """
        Small simplified rings along the O–D corridor for Valhalla exclude_polygons.
        Returns [] if hazard data missing or intersection would be too large (avoids unroutable requests).
        """
        self._load()
        if not self._geoms:
            return []

        ln = LineString([(origin_lng, origin_lat), (dest_lng, dest_lat)])
        buf = ln.buffer(0.0028)
        try:
            intersected = [g.intersection(buf) for g in self._geoms if g.intersects(buf)]
            intersected = [g for g in intersected if not g.is_empty]
            if not intersected:
                return []
            inter = intersected[0]
            for g in intersected[1:]:
                inter = inter.union(g)
        except Exception:  # noqa: BLE001
            return []
        if inter.is_empty:
            return []

        if inter.area > 0.012:
            logger.info("Hazard intersection with O–D buffer too large; skipping exclude_polygons.")
            return []

        def collect_polygons(geom: Any) -> list[Any]:
            acc: list[Any] = []
            if geom.geom_type == "Polygon":
                acc.append(geom)
            elif geom.geom_type == "MultiPolygon":
                acc.extend(list(geom.geoms))
            elif hasattr(geom, "geoms"):
                for g in geom.geoms:
                    acc.extend(collect_polygons(g))
            return acc

        polys = collect_polygons(inter)

        polys.sort(key=lambda p: float(p.area), reverse=True)
        rings: list[list[list[float]]] = []
        for poly in polys[:max_rings]:
            simp = poly.simplify(0.00022, preserve_topology=True)
            if simp.is_empty or simp.geom_type != "Polygon":
                continue
            ext = list(simp.exterior.coords)
            if len(ext) > max_ring_vertices:
                simp = poly.simplify(0.00055, preserve_topology=True)
                if simp.geom_type == "Polygon":
                    ext = list(simp.exterior.coords)
            if len(ext) < 4:
                continue
            ring = [[float(x[0]), float(x[1])] for x in ext]
            if ring[0] != ring[-1]:
                ring.append(list(ring[0]))
            rings.append(ring)
        return rings
