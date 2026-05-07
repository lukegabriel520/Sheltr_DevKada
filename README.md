# Sheltr

Metro Manila evacuation support: **Expo** app + **Flask** API + **Supabase** (evacuation centers) + **Stadia/Valhalla** routing + **flood hazard** scoring on routes.

## Requirements

- Node.js (for `frontend/`)
- Python 3.11+ (for `backend/`)

Create a **repo root** `.env` with at least the keys your environment needs (see Deploy sections below). Expo reads it via `frontend/app.config.js`.

## Local development

Use **two terminals** from the repo root (the folder that contains `README.md`, `frontend/`, `backend/`).

### API

**Windows (PowerShell)** — venv in repo root avoids `backend/Lib` shadowing the Python standard library:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
python backend/safe_server.py
```

If a folder **`backend\Lib`** exists (accidental stdlib copy), **delete it** before running Python from `backend/`.

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
python backend/safe_server.py
```

Sanity check: open `http://127.0.0.1:5000/health` — JSON should load.

### App (Expo)

```bash
cd frontend
npm install
npx expo start
```

For a **physical device**, set `EXPO_PUBLIC_API_URL` in the repo `.env` to a URL the phone can reach (e.g. your PC’s LAN IP and port 5000, or your deployed API HTTPS URL). Restart Expo after changing `.env`.

### If something fails

| Symptom | What to do |
|--------|------------|
| `encodings` / `Lib` errors | Delete `backend/Lib` if present; run Python from repo root with the venv above. |
| `Network request failed` / routing errors | Fix `EXPO_PUBLIC_API_URL`; confirm `/health` from a browser on the client network. |
| Windows Firewall | Allow inbound **TCP 5000** (API) and **TCP 8081** (Expo Metro) on Private networks as needed. |
| Supabase empty | App can use CSV fallback; optional: apply `supabase/migrations/` + `supabase/seed_evacuation_centers.sql`. |

## Repo layout

| Path | Role |
|------|------|
| `frontend/` | Expo Router app |
| `backend/` | Flask: routes, Stadia client, flood GeoJSON scoring |
| `data/` | Policy JSON, rivers, notification templates; flood GeoJSON per `Dockerfile` copy |
| `supabase/migrations/` | PostGIS schema + RPC |
| `Dockerfile` | Production API image (Railway / Docker) |
| `railway.json` | Railway healthcheck + Dockerfile builder |
| `frontend/vercel.json` | Vercel static web export (`dist/`) |

## Deploy (Railway API + Vercel web)

Deploy **in this order** so the frontend can point at a stable API URL.

### 1) Railway (Flask API)

1. Push this repo to GitHub (ensure `data/` includes the GeoJSON / assets your build expects; see `backend/wagamama.py` paths or your own env overrides).
2. In [Railway](https://railway.app): **New project** → **Deploy from GitHub** → select the repo.
3. Railway builds from the repo-root **`Dockerfile`**. Default command: `python backend/safe_server.py` with **`USE_WAITRESS=1`**; listen on **`PORT`**.

| Variable | Purpose |
|----------|---------|
| `STADIA_API_KEY` | Stadia / Valhalla routing (or `EXPO_PUBLIC_STADIA_API_KEY` name parity in some setups) |
| `EXPO_PUBLIC_SUPABASE_URL` / `EXPO_PUBLIC_SUPABASE_ANON_KEY` | Centers + DB (backend reads these) |
| `OPENROUTER_API_KEY` | Optional: AI briefings |
| `SHELTR_API_KEY` | Optional: require `X-Sheltr-Key` from clients (`EXPO_PUBLIC_SHELTR_API_KEY` on clients must match) |

5. Add a public **HTTPS** domain; verify `https://YOUR_HOST/health`.

**OOM on Railway:** try `WAITRESS_THREADS=2` or `1`, or raise memory. Optional: `WAGAMAMA_DEBUG_LOGS=0`.

### 2) Vercel (Expo web static export)

1. Import repo; **Root Directory** = **`frontend`**.
2. `frontend/vercel.json` sets `build:web` and output **`dist`**.
3. Set `EXPO_PUBLIC_API_URL` to your **Railway HTTPS** base (no trailing slash), plus Supabase/Stadia keys as needed.

4. **If Railway has `SHELTR_API_KEY` set:** add **`EXPO_PUBLIC_SHELTR_API_KEY`** on Vercel with the **same** value. Without it, the web app can still load **evacuation centers**, but **weather**, **area briefing**, **map flood overlay**, and **routing** calls that require the key will fail (often showing “Failed to fetch” or empty weather).

**Native / Expo Go:** use the same **`EXPO_PUBLIC_API_URL`** as the Railway API (not the Vercel URL unless you only ship web).

### 3) Mobile installable app (Expo Application Services)

The **API is not inside the APK/IPA** — you still host the Flask API (e.g. Railway). The store build embeds the React Native UI and **public** env vars (`EXPO_PUBLIC_*`) from your **repo root `.env`** at build time (via `frontend/app.config.js`).

1. Ensure production **`EXPO_PUBLIC_API_URL`** is **`https://your-api-host…`** (no trailing slash). Set **EAS Secrets** or build with a machine that has `.env`; never commit secrets.
2. From **`frontend/`**:

   ```bash
   npm install
   npx eas-cli@latest login
   npx eas-cli@latest build:configure
   ```

   Linking adds an **`extra.eas.projectId`** entry in `app.config.js` (commit that change once created).

3. Builds:
   - **Test APK (direct install):** `npm run build:android:apk` — profile **`preview`** produces an **APK** (`eas.json`).
   - **Google Play:** `npm run build:android:play` — profile **`production`** produces an **AAB** (Play requires AAB for new apps).
   - **iOS (TestFlight / App Store):** `npm run build:ios` — requires [Apple Developer Program](https://developer.apple.com/programs/).

4. **Signing:** EAS manages Android/iOS credentials in the cloud by default — installs from Play or signed APKs are **not “corrupted”**; they are normal signed binaries.

5. **Package names:** `app.json` uses **`com.sheltr.app`** for Android `package` and iOS `bundleIdentifier`. Change both if you need a unique id before publishing.

## Supabase

Apply `supabase/migrations/001_evacuation_centers.sql`, then optionally run `supabase/seed_evacuation_centers.sql` for dev data.
