# Sheltr API for Railway / Docker hosts (Flask + shapely/pandas; no DEM/rasterio).
# Repo layout: backend/, data/ at /app (matches safe_server ROOT = parent of backend/).

FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY data/ /app/data/

RUN mkdir -p /app/assets/evacuation-centers

ENV USE_WAITRESS=1
# Limit concurrent workers (each /route can spike memory while scoring flood overlays + Valhalla)
ENV WAITRESS_THREADS=1
EXPOSE 5000

CMD ["python", "backend/safe_server.py"]
