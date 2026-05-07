# Sheltr

To the CodeKada Hackathon organizers, mentors, and judges, thank you for the opportunity and for taking the time to review our entry.

## Overview
Sheltr is a disaster evacuation support project for Metro Manila that helps people find safer routes and nearby evacuation centers during storms and flooding. The app shows hazard-aware routing, center information, and simple safety guidance so people can make faster, clearer decisions when conditions change.

## Repository analysis in plain terms
This repository contains a mobile and web app plus a small backend service that powers map routing and safety scoring. The app asks the backend for a route, the backend checks how risky each segment is using flood and storm-surge maps, and the app then shows a safer path and nearby evacuation centers. The project focuses on helping people avoid high-risk areas while still getting to shelter quickly.

## Tech stack in brief
The user-facing app is built with Expo so it can run on phones and the web with the same codebase. The backend is a Python Flask API that handles requests, talks to routing services, and scores hazards. Evacuation centers are stored in Supabase, which is a hosted PostgreSQL database with geospatial features. Routing uses the Stadia or Valhalla engines to compute paths. Hazard layers are stored as GeoJSON files, and geospatial scoring uses Python libraries like Shapely and Pandas. These map layers can be prepared or inspected in GIS software such as QGIS. The project can be deployed with Railway for the API and Vercel for the web build.

## Model results and what they mean
| Metric | Typhoon | Super Typhoon | Significance |
| --- | --- | --- | --- |
| Recall | 0.87 | 0.90 | Good ability to catch hazards. |
| Precision | 0.77 | 0.76 | Decent 76% trustworthiness of a hazard alert. |
| Accuracy | 0.73 | 0.73 | Decent overall correctness of the model. |
| Flip Rate (consistency of predictions) | 0.000 | 0.000 | Excellent stability across different sampling resolutions. |

In everyday terms, the model is good at catching dangerous areas and stays consistent across map resolutions, while the alerts are reasonably trustworthy for guiding safer routing.
