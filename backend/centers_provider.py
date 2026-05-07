"""Evacuation centers: prefer Supabase REST, fall back to CSV (pandas)."""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

CENTERS_CACHE_TTL_SECONDS = max(0.0, float(os.environ.get("CENTERS_CACHE_TTL_SECONDS", "60")))


def _supabase_rest_config() -> tuple[str, str] | None:
    url = (
        os.environ.get("SUPABASE_URL")
        or os.environ.get("EXPO_PUBLIC_SUPABASE_URL")
        or ""
    ).rstrip("/")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("EXPO_PUBLIC_SUPABASE_ANON_KEY")
        or ""
    ).strip()
    if not url or not key:
        return None
    return url, key


def _fetch_supabase_centers(url: str, key: str) -> pd.DataFrame | None:
    endpoint = f"{url}/rest/v1/evacuation_centers"
    params = {
        "select": "id,name,latitude,longitude,capacity,safety_score,type,is_open,summary,elevation",
        "order": "id.asc",
    }
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    try:
        r = requests.get(endpoint, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("Supabase evacuation_centers fetch failed: %s", e)
        return None
    if not isinstance(rows, list) or not rows:
        return None
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row.get("latitude"))
            lng = float(row.get("longitude"))
        except (TypeError, ValueError):
            continue
        normalized.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "latitude": lat,
                "longitude": lng,
                "capacity": row.get("capacity"),
                "safety_score": row.get("safety_score"),
                "type": row.get("type"),
                "is_open": row.get("is_open"),
                "summary": row.get("summary"),
                "elevation": row.get("elevation"),
            }
        )
    if not normalized:
        return None
    return pd.DataFrame(normalized)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _to_bool_sql(value: str) -> bool | None:
    v = value.strip().lower()
    if v in ("true", "t", "1"):
        return True
    if v in ("false", "f", "0"):
        return False
    return None


def _load_seed_lookup() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    seed_path = ROOT / "supabase" / "seed_evacuation_centers.sql"
    if not seed_path.is_file():
        return out

    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line.startswith("('"):
                    continue
                tuple_text = line.rstrip(",;")
                if tuple_text.startswith("(") and tuple_text.endswith(")"):
                    tuple_text = tuple_text[1:-1]
                fields = next(csv.reader([tuple_text], delimiter=",", quotechar="'", skipinitialspace=True))
                if len(fields) < 9:
                    continue
                name = fields[0].strip()
                if not name:
                    continue
                key = name.casefold()
                out[key] = {
                    "name": name,
                    "latitude": float(fields[1]),
                    "longitude": float(fields[2]),
                    "capacity": int(float(fields[3])),
                    "safety_score": float(fields[4]),
                    "type": fields[5].strip() or None,
                    "is_open": _to_bool_sql(fields[6]),
                    "summary": fields[7].strip() or None,
                    "elevation": float(fields[8]) if fields[8].strip() else None,
                }
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not parse seed_evacuation_centers.sql: %s", e)
        out = {}

    return out


def load_centers_from_csv(csv_df: pd.DataFrame) -> pd.DataFrame:
    if csv_df.empty:
        return csv_df
    df = csv_df.copy()

    seed_lookup = _load_seed_lookup()
    if "name" in df.columns and seed_lookup:
        for i, row in df.iterrows():
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            seed = seed_lookup.get(name.casefold())
            if not seed:
                continue
            if "safety_score" not in df.columns or _is_missing(row.get("safety_score")):
                df.at[i, "safety_score"] = seed.get("safety_score")
            if "elevation" not in df.columns or _is_missing(row.get("elevation")):
                df.at[i, "elevation"] = seed.get("elevation")
            if "summary" not in df.columns or _is_missing(row.get("summary")):
                df.at[i, "summary"] = seed.get("summary")
            if "type" not in df.columns or _is_missing(row.get("type")):
                df.at[i, "type"] = seed.get("type")
            if "is_open" not in df.columns or _is_missing(row.get("is_open")):
                df.at[i, "is_open"] = seed.get("is_open")

    if "type" not in df.columns and "fclass" in df.columns:
        df["type"] = df["fclass"]
    if "type" in df.columns and "fclass" in df.columns:
        df["type"] = df["type"].fillna(df["fclass"])
    if "is_open" not in df.columns:
        df["is_open"] = True
    else:
        df["is_open"] = df["is_open"].fillna(True)
    if "summary" not in df.columns:
        df["summary"] = None
    else:
        df["summary"] = df["summary"].where(pd.notna(df["summary"]), None)
    if "elevation" not in df.columns:
        df["elevation"] = None
    if "safety_score" in df.columns:
        df["safety_score"] = pd.to_numeric(df["safety_score"], errors="coerce")
    if "elevation" in df.columns:
        df["elevation"] = pd.to_numeric(df["elevation"], errors="coerce")
    return df


class CentersProvider:
    """Single source for API responses; Supabase preferred, CSV fallback.

    Includes an in-memory TTL cache so high-traffic endpoints
    (`/evacuation-centers`, `/route`, `/nearest-evacuation`) do not hit Supabase
    on every request. TTL is configurable via `CENTERS_CACHE_TTL_SECONDS`.
    """

    def __init__(self, csv_df: pd.DataFrame) -> None:
        self._csv = load_centers_from_csv(csv_df)
        self._cache_lock = threading.Lock()
        self._cache_value: pd.DataFrame | None = None
        self._cache_stored_at: float = 0.0
        self._cache_source: str | None = None

    def _cache_alive(self) -> bool:
        if CENTERS_CACHE_TTL_SECONDS <= 0:
            return False
        if self._cache_value is None:
            return False
        return (time.time() - self._cache_stored_at) <= CENTERS_CACHE_TTL_SECONDS

    def _store_cache(self, value: pd.DataFrame, source: str) -> None:
        with self._cache_lock:
            self._cache_value = value
            self._cache_stored_at = time.time()
            self._cache_source = source

    def get_dataframe(self) -> pd.DataFrame:
        if self._cache_alive():
            return self._cache_value  # type: ignore[return-value]

        cfg = _supabase_rest_config()
        if not cfg:
            self._store_cache(self._csv, "csv")
            return self._csv
        url, key = cfg

        remote = _fetch_supabase_centers(url, key)
        if remote is not None and not remote.empty:
            logger.info("Evacuation centers loaded from Supabase (%s rows)", len(remote))
            self._store_cache(remote, "supabase")
            return remote
        if not self._csv.empty:
            logger.info("Supabase empty/unavailable; using CSV fallback (%s rows)", len(self._csv))
            self._store_cache(self._csv, "csv_fallback")
            return self._csv
        empty = pd.DataFrame()
        self._store_cache(empty, "empty")
        return empty

    def cache_metadata(self) -> dict[str, Any]:
        return {
            "source": self._cache_source,
            "ttl_seconds": CENTERS_CACHE_TTL_SECONDS,
            "stored_at_epoch": round(self._cache_stored_at, 3) if self._cache_stored_at else None,
            "data_age_seconds": round(time.time() - self._cache_stored_at, 3)
            if self._cache_stored_at
            else None,
            "rows": int(len(self._cache_value)) if self._cache_value is not None else 0,
        }
