"""Deterministic Sheltr route policy.

This module is intentionally pure: given a precomputed ``features`` dict and a
``policy`` dict (loaded from ``data/sheltr_route_policy.json``), it returns the
same numeric outputs every time. It performs no I/O, no LLM calls, and no
external network requests. The goal is to make Sheltr's risk + go/no-go logic
auditable: every constant lives in JSON, every formula is a small function
here, and replacing the policy file is the only knob operators need to tune.

Inputs (``features``) are produced upstream from real measurements (overlay
polygon overlap, route geometry, river layer, weather/data ages). The policy
then converts those features into:

* ``flood_risk`` and ``safety_score`` (relative ranking signal),
* ``confidence_score`` and ``confidence_label`` (data-quality signal), and
* a ``decision`` block with ``go``/``caution``/``no_go`` plus reasons (gate).

When you load a *new* policy file you get *new* outputs, but for the same
inputs + policy version this function is reproducible. That is what we mean by
"deterministic" — it is the layer where Carina-style labels will eventually
calibrate the constants in the JSON, not the formulas in code.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = ROOT / "data" / "sheltr_route_policy.json"


def _resolve_policy_path() -> Path:
    raw = os.environ.get("SHELTR_ROUTE_POLICY_PATH")
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (ROOT / candidate).resolve()
        return candidate
    return DEFAULT_POLICY_PATH


def load_policy() -> dict[str, Any]:
    """Load the policy JSON (no caching here — let the caller cache)."""
    path = _resolve_policy_path()
    if not path.is_file():
        raise FileNotFoundError(f"Sheltr route policy missing: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Route policy root must be a JSON object")
    if "policy_version" not in data:
        raise ValueError("Route policy is missing 'policy_version'")
    return data


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _safe_get(d: dict[str, Any], key: str, default: Any) -> Any:
    if isinstance(d, dict) and key in d:
        return d[key]
    return default


def _lookup_class_weight(class_weights: dict[str, Any], class_value: int | None, fallback: float) -> float:
    if class_value is None:
        return fallback
    raw = class_weights.get(str(int(class_value)))
    try:
        return float(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _lookup_severity_multiplier(multipliers: dict[str, Any], code: int | None, fallback: float) -> float:
    if code is None:
        return fallback
    raw = multipliers.get(str(int(code)))
    try:
        return float(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


def compute_class_weights(
    *,
    policy: dict[str, Any],
    map_code: int | None,
    storm_severity: int | None,
) -> dict[str, dict[int, float]]:
    """Compute per-class flood/storm-surge weights from policy + scenario.

    These are the multipliers used during overlap accumulation upstream
    (e.g. ``_route_flood_metrics``). Pulling them out of code into policy
    means scenario sensitivity is data-driven, not hardcoded.
    """
    scoring = _safe_get(policy, "scoring", {}) or {}
    flood_map_mults = _safe_get(scoring, "flood_map_severity_multipliers", {}) or {}
    surge_map_mults = _safe_get(scoring, "storm_surge_severity_multipliers", {}) or {}
    flood_class_weights = _safe_get(scoring, "var_class_weights", {}) or {}
    storm_class_weights = _safe_get(scoring, "haz_class_weights", {}) or {}

    flood_mult = _lookup_severity_multiplier(flood_map_mults, map_code, 1.0)
    storm_mult = _lookup_severity_multiplier(surge_map_mults, storm_severity, 1.0)

    flood_weights: dict[int, float] = {}
    for level in (1, 2, 3):
        base = _lookup_class_weight(flood_class_weights, level, 0.45 if level == 3 else 0.7 if level == 2 else 1.0)
        flood_weights[level] = base * flood_mult

    storm_weights: dict[int, float] = {}
    for level in (1, 2, 3):
        base = _lookup_class_weight(storm_class_weights, level, 0.45 if level == 3 else 0.7 if level == 2 else 1.0)
        storm_weights[level] = base * storm_mult

    return {"flood": flood_weights, "storm_surge": storm_weights}


def compute_flood_risk(
    *,
    policy: dict[str, Any],
    weighted_severity: float,
    max_severity_weight: float,
    overlap_hit_count: int,
    elevation_penalty: float,
    flood_scoring_available: bool,
) -> tuple[float, float, float]:
    """Return ``(flood_probability, flood_risk, safety_score)``.

    * ``flood_probability`` is the amplified overlap-derived component only.
    * ``flood_risk`` adds the elevation penalty and clamps to [0, 1].
    * ``safety_score`` is ``1 - flood_risk`` clamped to ``min_safety_score``.
    """
    scoring = _safe_get(policy, "scoring", {}) or {}
    safety_cfg = _safe_get(policy, "safety", {}) or {}
    amp = float(_safe_get(scoring, "weighted_severity_amplifier", 3.0))
    amp_cap = float(_safe_get(scoring, "weighted_severity_amplifier_cap", 1.0))
    floor_min = float(_safe_get(scoring, "overlap_floor_min", 0.10))
    floor_factor = float(_safe_get(scoring, "overlap_floor_max_severity_factor", 0.18))
    min_safety = float(_safe_get(safety_cfg, "min_safety_score", 0.08))

    if not flood_scoring_available:
        flood_probability = 0.0
    else:
        amplified = min(amp_cap, max(0.0, weighted_severity) * amp)
        floor = 0.0
        if overlap_hit_count > 0:
            floor = max(floor_min, max(0.0, max_severity_weight) * floor_factor)
        flood_probability = max(amplified, floor)

    flood_risk = _clamp01(flood_probability + max(0.0, float(elevation_penalty)))
    safety_score = round(max(min_safety, 1.0 - flood_risk), 4)
    return round(flood_probability, 4), flood_risk, safety_score


def compute_confidence(
    *,
    policy: dict[str, Any],
    used_graph_route: bool,
    flood_overlay_loaded: bool,
    scenario_normalized: str | None,
    storm_overlay_error: bool,
    weather_data_age_seconds: float | None,
    exclude_attempted: bool,
    exclude_used: bool,
    overlap_remaining: bool,
    waterways_loaded: bool,
) -> dict[str, Any]:
    cfg = _safe_get(policy, "confidence", {}) or {}
    components_cfg = _safe_get(cfg, "components", {}) or {}
    fresh_cfg = _safe_get(cfg, "data_freshness", {}) or {}
    reroute_cfg = _safe_get(cfg, "reroute_health", {}) or {}
    labels_cfg = _safe_get(cfg, "labels", {}) or {}

    components: dict[str, float] = {}
    components["graph_route"] = float(
        components_cfg.get("graph_route_yes", 0.45) if used_graph_route else components_cfg.get("graph_route_no", 0.10)
    )
    components["flood_overlay_loaded"] = float(
        components_cfg.get("flood_overlay_loaded_yes", 0.20)
        if flood_overlay_loaded
        else components_cfg.get("flood_overlay_loaded_no", 0.05)
    )
    is_auto_scenario = scenario_normalized in (None, "auto")
    components["scenario_source"] = float(
        components_cfg.get("scenario_auto", 0.10) if is_auto_scenario else components_cfg.get("scenario_manual", 0.07)
    )
    components["no_storm_overlay_error"] = (
        0.0 if storm_overlay_error else float(components_cfg.get("no_storm_overlay_error", 0.05))
    )

    fresh_score = float(fresh_cfg.get("missing_score", 0.05))
    if isinstance(weather_data_age_seconds, (int, float)) and math.isfinite(float(weather_data_age_seconds)):
        wage = float(weather_data_age_seconds)
        fresh_within = float(fresh_cfg.get("fresh_within_seconds", 120.0))
        warm_within = float(fresh_cfg.get("warm_within_seconds", 600.0))
        if wage <= fresh_within:
            fresh_score = float(fresh_cfg.get("fresh_score", 0.10))
        elif wage <= warm_within:
            fresh_score = float(fresh_cfg.get("warm_score", 0.07))
        else:
            fresh_score = float(fresh_cfg.get("stale_score", 0.03))
    components["data_freshness"] = fresh_score

    if exclude_attempted and not exclude_used:
        components["reroute_health"] = float(reroute_cfg.get("attempted_but_failed_penalty", -0.10))
    elif overlap_remaining:
        components["reroute_health"] = float(reroute_cfg.get("overlap_remaining_penalty", -0.05))
    else:
        components["reroute_health"] = float(reroute_cfg.get("healthy_score", 0.0))

    components["waterway_layer"] = (
        float(components_cfg.get("waterway_layer_loaded", 0.02)) if waterways_loaded else 0.0
    )

    raw = sum(components.values())
    score = _clamp01(raw)

    high_min = float(labels_cfg.get("high_min", 0.85))
    moderate_min = float(labels_cfg.get("moderate_min", 0.6))
    low_min = float(labels_cfg.get("low_min", 0.35))
    if score >= high_min:
        label = "high"
    elif score >= moderate_min:
        label = "moderate"
    elif score >= low_min:
        label = "low"
    else:
        label = "very_low"

    return {
        "components": {k: round(v, 4) for k, v in components.items()},
        "score": round(score, 4),
        "label": label,
    }


def evaluate_decision(
    *,
    policy: dict[str, Any],
    flood_overlap_fraction: float | None,
    max_severity_weight: float,
    used_graph_route: bool,
    overlap_hit_count: int,
    reroute_attempts: int,
    reroute_max: int,
    confidence_score: float,
) -> dict[str, Any]:
    decision_cfg = _safe_get(policy, "decision", {}) or {}
    no_go_cfg = _safe_get(decision_cfg, "go_no_go", {}) or {}
    caution_cfg = _safe_get(decision_cfg, "caution_if", {}) or {}
    labels_cfg = _safe_get(decision_cfg, "labels", {}) or {}

    overlap_fraction = float(flood_overlap_fraction or 0.0)

    no_go_overlap = float(no_go_cfg.get("block_if_overlap_fraction_at_least", 0.35))
    no_go_severity = float(no_go_cfg.get("block_if_max_severity_weight_at_least", 2.5))
    block_no_graph = bool(no_go_cfg.get("block_if_no_graph_route", True))
    block_reroute_exhausted = bool(no_go_cfg.get("block_if_reroute_exhausted_with_overlap", True))
    block_low_conf = float(no_go_cfg.get("block_if_confidence_below", 0.35))

    caution_overlap = float(caution_cfg.get("overlap_fraction_at_least", 0.10))
    caution_conf = float(caution_cfg.get("confidence_below", 0.60))
    caution_severity = float(caution_cfg.get("max_severity_weight_at_least", 1.0))

    no_go_reasons: list[str] = []
    if block_no_graph and not used_graph_route:
        no_go_reasons.append("no_graph_route_only_straight_line")
    if overlap_fraction >= no_go_overlap:
        no_go_reasons.append(f"overlap_fraction_{overlap_fraction:.2f}_at_least_{no_go_overlap:.2f}")
    if max_severity_weight >= no_go_severity:
        no_go_reasons.append(f"max_severity_{max_severity_weight:.2f}_at_least_{no_go_severity:.2f}")
    if block_reroute_exhausted and reroute_max > 0 and reroute_attempts >= reroute_max and overlap_hit_count > 0:
        no_go_reasons.append("reroute_exhausted_with_overlap")
    if confidence_score < block_low_conf:
        no_go_reasons.append(f"confidence_{confidence_score:.2f}_below_{block_low_conf:.2f}")

    caution_reasons: list[str] = []
    if overlap_fraction >= caution_overlap:
        caution_reasons.append(f"overlap_fraction_{overlap_fraction:.2f}_at_least_{caution_overlap:.2f}")
    if confidence_score < caution_conf:
        caution_reasons.append(f"confidence_{confidence_score:.2f}_below_{caution_conf:.2f}")
    if max_severity_weight >= caution_severity:
        caution_reasons.append(f"max_severity_{max_severity_weight:.2f}_at_least_{caution_severity:.2f}")

    if no_go_reasons:
        status = labels_cfg.get("no_go", "no_go")
        reasons = no_go_reasons
    elif caution_reasons:
        status = labels_cfg.get("caution", "caution")
        reasons = caution_reasons
    else:
        status = labels_cfg.get("go", "go")
        reasons = []

    return {
        "status": status,
        "reasons": reasons,
        "thresholds": {
            "no_go": {
                "overlap_fraction_at_least": no_go_overlap,
                "max_severity_weight_at_least": no_go_severity,
                "confidence_below": block_low_conf,
                "block_if_no_graph_route": block_no_graph,
                "block_if_reroute_exhausted_with_overlap": block_reroute_exhausted,
            },
            "caution": {
                "overlap_fraction_at_least": caution_overlap,
                "confidence_below": caution_conf,
                "max_severity_weight_at_least": caution_severity,
            },
        },
    }


def apply_policy(features: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Pure policy application: deterministic risk, confidence, and decision.

    ``features`` is expected to contain at least the following keys (all
    optional but recommended):

    * ``weighted_severity``: float, route-weighted overlap severity.
    * ``max_severity_weight``: float, peak per-sample weight along route.
    * ``overlap_hit_count``: int, count of route samples inside hazard polygons.
    * ``flood_overlap_fraction``: float | None, raw overlap fraction.
    * ``flood_scoring_available``: bool.
    * ``used_graph_route``: bool.
    * ``flood_overlay_loaded``: bool.
    * ``scenario_normalized``: str | None.
    * ``storm_overlay_error``: bool.
    * ``weather_data_age_seconds``: float | None.
    * ``exclude_attempted``, ``exclude_used``: bool.
    * ``reroute_attempts``, ``reroute_max``: int.
    * ``waterways_loaded``: bool.

    Missing keys fall back to neutral defaults so partial pipelines still work.
    """

    weighted_severity = float(features.get("weighted_severity") or 0.0)
    max_severity_weight = float(features.get("max_severity_weight") or 0.0)
    overlap_hit_count = int(features.get("overlap_hit_count") or 0)
    flood_overlap_fraction = features.get("flood_overlap_fraction")
    flood_scoring_available = bool(features.get("flood_scoring_available", False))
    used_graph_route = bool(features.get("used_graph_route", False))
    flood_overlay_loaded = bool(features.get("flood_overlay_loaded", False))
    scenario_normalized = features.get("scenario_normalized")
    storm_overlay_error = bool(features.get("storm_overlay_error", False))
    weather_data_age_seconds = features.get("weather_data_age_seconds")
    exclude_attempted = bool(features.get("exclude_attempted", False))
    exclude_used = bool(features.get("exclude_used", False))
    reroute_attempts = int(features.get("reroute_attempts") or 0)
    reroute_max = int(features.get("reroute_max") or 0)
    waterways_loaded = bool(features.get("waterways_loaded", False))

    elevation_penalty = 0.0

    flood_probability, flood_risk, safety_score = compute_flood_risk(
        policy=policy,
        weighted_severity=weighted_severity,
        max_severity_weight=max_severity_weight,
        overlap_hit_count=overlap_hit_count,
        elevation_penalty=elevation_penalty,
        flood_scoring_available=flood_scoring_available,
    )

    confidence = compute_confidence(
        policy=policy,
        used_graph_route=used_graph_route,
        flood_overlay_loaded=flood_overlay_loaded,
        scenario_normalized=scenario_normalized,
        storm_overlay_error=storm_overlay_error,
        weather_data_age_seconds=weather_data_age_seconds,
        exclude_attempted=exclude_attempted,
        exclude_used=exclude_used,
        overlap_remaining=bool(overlap_hit_count > 0 and not exclude_attempted),
        waterways_loaded=waterways_loaded,
    )

    decision = evaluate_decision(
        policy=policy,
        flood_overlap_fraction=flood_overlap_fraction,
        max_severity_weight=max_severity_weight,
        used_graph_route=used_graph_route,
        overlap_hit_count=overlap_hit_count,
        reroute_attempts=reroute_attempts,
        reroute_max=reroute_max,
        confidence_score=confidence["score"],
    )

    return {
        "policy_version": str(policy.get("policy_version", "unknown")),
        "elevation_penalty": round(elevation_penalty, 4),
        "flood_probability": flood_probability,
        "flood_risk": round(flood_risk, 4),
        "safety_score": safety_score,
        "confidence_score": confidence["score"],
        "confidence_label": confidence["label"],
        "confidence_components": confidence["components"],
        "decision": decision,
    }
