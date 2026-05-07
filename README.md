# Sheltr

To the CodeKada Hackathon organizers, mentors, and judges, thank you for the opportunity and for reviewing our entry.

## Repository overview
Sheltr is a Metro Manila evacuation support system made up of three parts: an Expo app for users, a Flask API for routing and hazard scoring, and geospatial data for flood and storm-surge hazards. The app requests routes and safety data from the API. The API selects a route, scores its flood exposure, and returns safety guidance and nearby evacuation centers. The data folder contains the flood and storm-surge GeoJSON layers and river linework used by the scoring logic.

## Basis and calibration
All hazard policy constants are calibrated exclusively to Typhoon Carina (July 2024), the only recent and clearly documented typhoon that produced significant Metro Manila flooding during this project’s timeframe. No other typhoon events or flood incidents were used to calibrate weights, thresholds, or labels.

## Data layers and sources
Flood polygons are stored in data/MetroManila_Flood_5year.json, data/MetroManila_Flood_25year.json, and data/MetroManila_Flood_100year.json. Storm-surge polygons are stored in data/MetroManila_StormSurge_SSA1.json through data/MetroManila_StormSurge_SSA4.json. NCR river linework is stored in data/NCR_Rivers_Clipped.json. These layers are GeoJSON and can be prepared or inspected in QGIS or other GIS tools.

## Routing and hazard scoring flow
1. The client sends origin, destination, and an optional flood_scenario (auto, sts, typhoon, super_typhoon).
2. The API fetches weather and river data from Open-Meteo and selects a flood map and storm-surge overlay.
3. Valhalla routing is used when available; otherwise a straight-line fallback is used.
4. The route geometry is densified and sampled. Each sample is checked against flood and storm-surge polygons.
5. Overlap metrics are computed and passed to the deterministic policy in data/sheltr_route_policy.json.
6. The policy produces a flood risk, safety score, confidence score, and a go/caution/no_go decision.

## Parameters and weights used in the policy
All weights and thresholds below come from data/sheltr_route_policy.json unless noted.

Flood and storm-surge class weights
- Flood class weights by Var level: Var 1 = 1.0, Var 2 = 0.7, Var 3 = 0.45.
- Storm-surge class weights by HAZ level: HAZ 1 = 1.0, HAZ 2 = 0.7, HAZ 3 = 0.45.
- Flood map severity multipliers by map code: 5-year = 1.0, 25-year = 1.5, 100-year = 2.5.
- Storm-surge severity multipliers by SSA severity: 0 = 1.0, 1 = 1.0, 2 = 1.0, 3 = 1.5, 4 = 2.5.

Flood probability and safety score
- Weighted severity amplifier: 3.0.
- Amplifier cap: 1.0.
- Overlap floor minimum: 0.10.
- Overlap floor factor: max_severity_weight times 0.18.
- Minimum safety score: 0.08.
- Flood probability is the amplified weighted severity, with a floor when any overlap is detected.
- Flood risk equals flood probability plus elevation penalty, then clamped to 0–1.
- Elevation penalties are not implemented in this repository, so elevation_penalty is always 0.0.
- Safety score equals 1 minus flood risk, clamped to the minimum safety score.

Confidence score components
- Routing source: graph route yes 0.45, graph route no 0.10.
- Flood overlay loaded: yes 0.20, no 0.05.
- Scenario source: auto 0.10, manual 0.07.
- Storm overlay error: no error 0.05, error 0.0.
- Data freshness: missing 0.05, fresh within 120 seconds 0.10, warm within 600 seconds 0.07, stale 0.03.
- Reroute health: attempted but failed -0.10, overlap remaining -0.05, healthy 0.0.
- Waterway layer loaded: 0.02.
- Confidence label thresholds: high at 0.85 and above, moderate at 0.60 and above, low at 0.35 and above, otherwise very_low.

Go, caution, and no_go decision thresholds
- no_go if overlap fraction is at least 0.35.
- no_go if max severity weight is at least 2.5.
- no_go if there is no graph route and only a straight-line fallback was used.
- no_go if reroute attempts are exhausted and overlap still remains.
- no_go if confidence is below 0.35.
- caution if overlap fraction is at least 0.10.
- caution if confidence is below 0.60.
- caution if max severity weight is at least 1.0.
- go when no no_go or caution rules are triggered.

## Static thresholds and constants used by the backend
Service area and geometry limits
- Metro Manila bounding box: latitude 14.0 to 15.2, longitude 120.0 to 122.0.
- Flood route sampling: target spacing 30 meters, max samples 600, and a hard cap of 2,500 line vertices for flood polygon operations.
- Hazard point scoring in flood_index.py: 0.82 if inside a flood polygon, 0.45 if within a 0.0008 degree buffer (approximately 89 meters in latitude and about 86 meters in longitude at 14.6 degrees latitude in Metro Manila; longitude conversion varies with latitude), 0.08 otherwise.

Weather and flood map selection
- Flood map thresholds for 3-hour and 24-hour rain in millimeters:
  - 100-year map: 80 mm in 3 hours or 150 mm in 24 hours.
  - 25-year map: 50 mm in 3 hours or 100 mm in 24 hours.
  - 5-year map: 20 mm in 3 hours or 50 mm in 24 hours.
- River escalation: if river_discharge divided by river_discharge_p75 exceeds 1.2, map severity is escalated one level.
- Var level thresholds for 3-hour rain in millimeters:
  - 5-year: Var 1 at 20, Var 2 at 15, Var 3 at 10.
  - 25-year: Var 1 at 50, Var 2 at 40, Var 3 at 30.
  - 100-year: Var 1 at 80, Var 2 at 65, Var 3 at 50.

Storm-surge selection
- Onshore wind window: 200 to 340 degrees.
- SSA1 wind speed: 40 km/h.
- SSA2 wind speed: 50 km/h.
- SSA3 wind speed: 70 km/h.
- SSA4 wind speed: 90 km/h or pressure at or below 980 hPa.
- Storm-surge overlays map SSA1 to SSA4 to the matching GeoJSON files.

Scenario mapping and overlays
- Manual scenario mapping to flood maps: sts maps to 5-year, typhoon maps to 25-year, super_typhoon maps to 100-year.
- Manual scenario storm-surge pairing: sts forces SSA1, typhoon forces SSA3, super_typhoon forces SSA4 if live weather does not already select a higher severity.
- Flood overlay display modes: 0 auto, 1 show Var 3 only, 2 show Var 2 and 3, 3 show Var 1, 2, and 3.
- Storm-surge display mode allows HAZ 1, 2, and 3.
- Auto overlays clip features to a 12 km radius around the user. Manual scenario clipping uses 48 km and is configurable via SHELTR_MANUAL_SCENARIO_OVERLAY_RADIUS_KM.

Rerouting and exclusions
- Max reroute attempts: 4 by default via ROUTE_FLOOD_REROUTE_MAX.
- Blocker radius: 65 meters by default via ROUTE_FLOOD_BLOCK_RADIUS_M.
- Blocker spacing: 220 meters by default via ROUTE_FLOOD_BLOCK_SPACING_M.
- Blockers per pass: 12 by default via ROUTE_FLOOD_BLOCKERS_PER_PASS.
- Maximum exclude polygon perimeter budget: 9,000 meters by default, derived from 90 percent of a 10,000 meter hard limit.
- Alternate route scoring uses overlap_penalty_ratio 1.8 and max_detour_ratio 2.5.

Caching and freshness
- Weather cache TTL: 180 seconds.
- Flood overlay cache TTL: 180 seconds.
- Confidence freshness thresholds: 120 seconds for fresh, 600 seconds for warm.

River proximity metrics
- River sampling stride: every 3rd route coordinate.
- Distance buckets reported at 50 m, 100 m, and 150 m.

## Model results and what they mean
The metrics below are provided by the team’s external evaluation using Typhoon Carina labeled hazard data. The dataset size, split strategy, validation methodology, and evaluation scripts are not stored in this repository, so the table reflects results supplied outside the codebase.
| Metric | Typhoon Scenario | Super Typhoon Scenario | Significance |
| --- | --- | --- | --- |
| Recall | 0.87 | 0.90 | Good ability to catch hazards in Typhoon Carina labeled data. |
| Precision | 0.77 | 0.76 | 77 percent of Typhoon Carina typhoon hazard alerts are correct; 76 percent of Typhoon Carina super typhoon alerts are correct. |
| Accuracy | 0.73 | 0.73 | Overall correctness, with hazard presence or absence classified correctly about 73 percent of the time. |
| Flip Rate (consistency of predictions) | 0.00 | 0.00 | No flips, or 0 percent, across different sampling resolutions. |
