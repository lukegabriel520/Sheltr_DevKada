# Sheltr policy and constants

This document captures the full, reproducible list of policy parameters, weights, and static thresholds used by Sheltr. It is the authoritative reference for hazard scoring and route decision logic.

## Policy sources
- `data/sheltr_route_policy.json`: tunable weights and thresholds.
- `backend/route_policy.py`: deterministic policy application.
- `backend/wagamama.py`: route sampling, overlay selection, and hazard scoring.
- `backend/flood_index.py`: point-level flood hazard heuristics.

## Policy inputs
The policy expects the following inputs (from `backend/wagamama.py`):
- `weighted_severity`: route-weighted overlap severity.
- `max_severity_weight`: peak per-sample weight along the route.
- `overlap_hit_count`: count of samples inside hazard polygons.
- `flood_overlap_fraction`: fraction of samples inside hazard polygons.
- `flood_scoring_available`: whether overlay data is loaded.
- `used_graph_route`: whether Valhalla routing succeeded.
- `flood_overlay_loaded`: whether overlay data is loaded.
- `scenario_normalized`: `auto`, `sts`, `typhoon`, `super_typhoon`, or null.
- `storm_overlay_error`: whether storm overlay validation failed.
- `weather_data_age_seconds`: age of weather data.
- `exclude_attempted` / `exclude_used`: reroute status flags.
- `reroute_attempts` / `reroute_max`: reroute loop counters.
- `waterways_loaded`: whether river linework is loaded.

## Weights and multipliers (from `data/sheltr_route_policy.json`)
Flood and storm-surge class weights
- Flood class weights by Var level: Var 1 = 1.0, Var 2 = 0.7, Var 3 = 0.45.
- Storm-surge class weights by HAZ level: HAZ 1 = 1.0, HAZ 2 = 0.7, HAZ 3 = 0.45.
- Flood map severity multipliers by map code: 5-year = 1.0, 25-year = 1.5, 100-year = 2.5.
- Storm-surge severity multipliers by SSA severity: 0 = 1.0, 1 = 1.0, 2 = 1.0, 3 = 1.5, 4 = 2.5.

Flood probability and safety score
- Weighted severity amplifier: 3.0.
- Amplifier cap: 1.0.
- Overlap floor minimum: 0.10.
- Overlap floor factor: `max_severity_weight × 0.18`.
- Minimum safety score: 0.08.
- Flood probability is the amplified weighted severity, with a floor when any overlap is detected.
- Flood risk equals flood probability plus elevation penalty, then clamped to 0–1.
- Elevation penalties are not implemented in this repository, so `elevation_penalty` is always 0.0.
- Safety score equals 1 minus flood risk, clamped to the minimum safety score.

Confidence score components
- Routing source: graph route yes 0.45, graph route no 0.10.
- Flood overlay loaded: yes 0.20, no 0.05.
- Scenario source: auto 0.10, manual 0.07.
- Storm overlay error: no error 0.05, error 0.0.
- Data freshness: missing 0.05, fresh within 120 seconds 0.10, warm within 600 seconds 0.07, stale 0.03.
- Reroute health: attempted but failed -0.10, overlap remaining -0.05, healthy 0.0.
- Waterway layer loaded: 0.02.
- Confidence label thresholds: high ≥ 0.85, moderate ≥ 0.60, low ≥ 0.35, otherwise very_low.

Go, caution, and no_go decision thresholds
- no_go if overlap fraction ≥ 0.35.
- no_go if max severity weight ≥ 2.5.
- no_go if there is no graph route and only a straight-line fallback was used.
- no_go if reroute attempts are exhausted and overlap still remains.
- no_go if confidence < 0.35.
- caution if overlap fraction ≥ 0.10.
- caution if confidence < 0.60.
- caution if max severity weight ≥ 1.0.
- go when no no_go or caution rules are triggered.

## Static thresholds and constants (from backend logic)
Service area and geometry limits
- Metro Manila bounding box: latitude 14.0 to 15.2, longitude 120.0 to 122.0.
- Flood route sampling: target spacing 30 m, max samples 600, and a hard cap of 2,500 line vertices for flood polygon operations.
- Hazard point scoring in `flood_index.py`: 0.82 if inside a flood polygon, 0.45 if within a 0.0008-degree buffer, 0.08 otherwise.

Weather and flood map selection
- Flood map thresholds for 3-hour and 24-hour rain in millimeters:
- 100-year map: 80 mm in 3 hours or 150 mm in 24 hours.
- 25-year map: 50 mm in 3 hours or 100 mm in 24 hours.
- 5-year map: 20 mm in 3 hours or 50 mm in 24 hours.
- River escalation: if `river_discharge / river_discharge_p75` exceeds 1.2, map severity escalates one level.
- Var level thresholds for 3-hour rain in millimeters:
- 5-year: Var 1 at 20, Var 2 at 15, Var 3 at 10.
- 25-year: Var 1 at 50, Var 2 at 40, Var 3 at 30.
- 100-year: Var 1 at 80, Var 2 at 65, Var 3 at 50.

Storm-surge selection
- Onshore wind window: 200° to 340°.
- SSA1 wind speed: 40 km/h.
- SSA2 wind speed: 50 km/h.
- SSA3 wind speed: 70 km/h.
- SSA4 wind speed: 90 km/h or pressure ≤ 980 hPa.
- Storm-surge overlays map SSA1 to SSA4 to the matching GeoJSON files.

Scenario mapping and overlays
- Manual scenario mapping to flood maps: sts → 5-year, typhoon → 25-year, super_typhoon → 100-year.
- Manual scenario storm-surge pairing: sts forces SSA1, typhoon forces SSA3, super_typhoon forces SSA4 if live weather does not already select a higher severity.
- Flood overlay display modes: 0 auto, 1 show Var 3 only, 2 show Var 2 and 3, 3 show Var 1, 2, and 3.
- Storm-surge display mode allows HAZ 1, 2, and 3.
- Auto overlays clip features to a 12 km radius around the user. Manual scenario clipping uses 48 km and is configurable.

Rerouting and exclusions
- Max reroute attempts: 4 by default via `ROUTE_FLOOD_REROUTE_MAX`.
- Blocker radius: 65 m via `ROUTE_FLOOD_BLOCK_RADIUS_M`.
- Blocker spacing: 220 m via `ROUTE_FLOOD_BLOCK_SPACING_M`.
- Blockers per pass: 12 via `ROUTE_FLOOD_BLOCKERS_PER_PASS`.
- Maximum exclude polygon perimeter budget: computed at runtime as 90% of `STADIA_EXCLUDE_CIRCUMFERENCE_LIMIT_M` (10,000 m), resulting in 9,000 m when no override is set.
- Alternate route scoring uses `overlap_penalty_ratio = 1.8` and `max_detour_ratio = 2.5`.

Caching and freshness
- Weather cache TTL: 180 seconds.
- Flood overlay cache TTL: 180 seconds.
- Confidence freshness thresholds: 120 seconds for fresh, 600 seconds for warm.

River proximity metrics
- River sampling stride: every 3rd route coordinate.
- Distance buckets reported at 50 m, 100 m, and 150 m.

## Environment overrides (non-exhaustive)
- `SHELTR_API_KEY`: API key for protected endpoints.
- `STADIA_API_KEY`: required for Valhalla routing.
- `OPENROUTER_API_KEY`: optional for AI briefings.
- `SHELTR_ROUTE_POLICY_PATH`: override policy JSON path.
- `SHELTR_MANUAL_SCENARIO_OVERLAY_RADIUS_KM`: manual overlay clip radius.
- `ROUTE_FLOOD_REROUTE_MAX`: reroute attempts cap.
- `ROUTE_FLOOD_BLOCK_RADIUS_M`: reroute blocker radius.
- `ROUTE_FLOOD_BLOCK_SPACING_M`: reroute blocker spacing.
- `ROUTE_FLOOD_BLOCKERS_PER_PASS`: blockers per reroute pass.
