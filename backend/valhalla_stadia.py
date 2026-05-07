"""Request driving routes from Stadia Maps Valhalla HTTP API (retries, alternates, exclusions)."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from polyline6 import decode_polyline6

logger = logging.getLogger(__name__)

STADIA_ROUTE_URL = "https://api.stadiamaps.com/route/v1"


def get_stadia_api_key() -> str | None:
    return os.environ.get("STADIA_API_KEY") or os.environ.get("EXPO_PUBLIC_STADIA_API_KEY")


def _stadia_session() -> requests.Session:
    retries = Retry(
        total=int(os.environ.get("STADIA_HTTP_RETRIES", "3")),
        connect=3,
        read=3,
        backoff_factor=float(os.environ.get("STADIA_HTTP_BACKOFF", "0.45")),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("POST",),
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retries, pool_connections=4, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION = _stadia_session()


def _shape_from_trip(trip: dict[str, Any]) -> str:
    enc = trip.get("shape") or ""
    if not enc:
        legs = trip.get("legs") or []
        if legs and isinstance(legs[0], dict) and legs[0].get("shape"):
            enc = legs[0]["shape"]
    return enc if isinstance(enc, str) else ""


def _trip_to_result(
    trip: dict[str, Any],
    *,
    label: str,
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
) -> dict[str, Any] | None:
    enc = _shape_from_trip(trip)
    coords = decode_polyline6(enc)
    summary = trip.get("summary") or {}
    distance_km = float(summary.get("length") or 0)
    duration_s = float(summary.get("time") or 0)
    if not coords:
        coords = [[origin_lng, origin_lat], [dest_lng, dest_lat]]
    return {
        "coordinates": coords,
        "distance_km": distance_km,
        "duration_seconds": duration_s,
        "raw_trip": trip,
        "label": label,
    }


def _iter_trips_from_response(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    main = data.get("trip")
    if isinstance(main, dict):
        out.append(("primary", main))
    for i, alt in enumerate(data.get("alternates") or []):
        if isinstance(alt, dict):
            t = alt.get("trip")
            if isinstance(t, dict):
                out.append((f"alternate_{i + 1}", t))
            elif alt.get("shape") or alt.get("summary"):
                out.append((f"alternate_{i + 1}", alt))
    return out


def request_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    *,
    costing: str = "auto",
    timeout: int = 35,
    alternates: int = 0,
    exclude_polygons: list[list[list[float]]] | None = None,
    exclude_locations: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    """
    Returns dict with keys: coordinates, distance_km, duration_seconds, raw_trip,
    plus optional candidates[] when alternates > 0.
    """
    cands = request_route_candidates(
        origin_lat,
        origin_lng,
        dest_lat,
        dest_lng,
        costing=costing,
        timeout=timeout,
        alternates=alternates,
        exclude_polygons=exclude_polygons,
        exclude_locations=exclude_locations,
    )
    if not cands:
        raise RuntimeError("Valhalla returned no trips")
    best = cands[0]
    out: dict[str, Any] = {
        "coordinates": best["coordinates"],
        "distance_km": best["distance_km"],
        "duration_seconds": best["duration_seconds"],
        "raw_trip": best["raw_trip"],
    }
    if len(cands) > 1:
        out["candidates"] = cands
    return out


def request_route_candidates(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    *,
    costing: str = "auto",
    timeout: int = 35,
    alternates: int = 0,
    exclude_polygons: list[list[list[float]]] | None = None,
    exclude_locations: list[dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Returns ordered trip candidates (primary + alternates) from one Valhalla call."""
    api_key = get_stadia_api_key()
    if not api_key:
        raise RuntimeError("Missing STADIA_API_KEY or EXPO_PUBLIC_STADIA_API_KEY")

    url = f"{STADIA_ROUTE_URL}?api_key={api_key}"
    body: dict[str, Any] = {
        "locations": [
            {"lat": origin_lat, "lon": origin_lng, "type": "break"},
            {"lat": dest_lat, "lon": dest_lng, "type": "break"},
        ],
        "costing": costing,
        "units": "kilometers",
    }
    if alternates > 0:
        body["alternates"] = int(alternates)
    if exclude_polygons:
        body["exclude_polygons"] = exclude_polygons
    if exclude_locations:
        body["exclude_locations"] = exclude_locations

    resp = _SESSION.post(url, json=body, timeout=timeout)
    if not resp.ok:
        body_preview = (resp.text or "").strip().replace("\n", " ")
        if len(body_preview) > 700:
            body_preview = body_preview[:700] + "..."
        raise RuntimeError(f"Stadia route failed HTTP {resp.status_code}: {body_preview}")
    data = resp.json()

    trips = _iter_trips_from_response(data)
    results: list[dict[str, Any]] = []
    for label, trip in trips:
        r = _trip_to_result(
            trip,
            label=label,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=dest_lat,
            dest_lng=dest_lng,
        )
        if r:
            results.append(r)
    return results
