import { BACKEND_BASE_URL } from './config';
import {
  getCachedValue,
  latLngCacheKey,
  routeCacheKey,
  setCachedValue,
} from './offlineCache';

export interface WeatherData {
  temperature: number | null;
  humidity: number | null;
  precipitation: number | null;
  precipitation_probability?: number | null;
  daily_rain_sum?: number | null;
  daily_precipitation_hours?: number | null;
  next_6h_precipitation_sum?: number | null;
  overlay_var_level?: number | null;
  time?: string | null;
  timezone?: string | null;
  hourly?: {
    time: string[];
    precipitation: number[];
    precipitation_probability?: number[];
    soil_moisture_0_7cm?: number[];
    soil_moisture_7_28cm?: number[];
    temperature_2m: number[];
    relative_humidity_2m: number[];
  };
  daily?: {
    time: string[];
    rain_sum: number[];
    precipitation_hours: number[];
  };
}

export interface FloodRiskSample {
  pred_prob_unsafe: number;
  lat?: number;
  lon?: number;
}

export interface FloodOverlayFeature {
  type: 'Feature';
  geometry: {
    type: string;
    coordinates: unknown;
  } | null;
  properties: {
    Var?: number;
    HAZ?: number;
    overlay_type?: 'flood' | 'storm_surge' | string;
    ssa_level?: number;
    [key: string]: unknown;
  };
}

export interface FloodOverlayResponse {
  type: 'FeatureCollection';
  features: FloodOverlayFeature[];
  display_mode?: number;
  display_vars?: number[];
  display_haz?: number[];
  storm_surge_active?: boolean;
  storm_surge_severity?: number;
  storm_surge_overlay?: string | null;
}

export type FloodScenario = 'auto' | 'sts' | 'typhoon' | 'super_typhoon';

export interface RouteResponse {
  route: {
    coordinates: [number, number][];
    distance_km: number;
    duration_minutes: number | null;
    safety_score: number;
    flood_risk: number;
    flood_overlap_fraction?: number | null;
    cost_function: string;
    cost_matrix_total?: number;
    destination_center_safety_score?: number | null;
    destination_center_elevation?: number | null;
  };
  route_stats?: {
    selected_cost_matrix_total?: number;
    selected_flood_probability?: number;
    flood_overlap_fraction?: number | null;
    cost_matrix_formula?: string;
    river_closest_sample_m?: number | null;
    waterway_near_route_fraction_100m?: number | null;
    waterways_loaded?: boolean;
    [key: string]: unknown;
  };
  segments: unknown[];
  warning?: string;
  status?: string;
}

export interface RouteBriefingResponse {
  fil: string;
  en: string;
  model?: string;
  raw_error?: string | null;
}

export interface EvacuationCenter {
  id?: number;
  name: string;
  latitude: number;
  longitude: number;
  capacity?: number;
  safety_score?: number;
  type?: string;
  is_open?: boolean;
  summary?: string;
  distance?: number;
  elevation?: number;
}

export interface HealthResponse {
  healthy: boolean;
  status?: string;
  stadia_configured?: boolean;
  flood_layer_loaded?: boolean;
  flood_layer_error?: string;
  evacuation_centers?: number;
  openrouter_configured?: boolean;
  waterways_index?: { line_segment_count?: number; error?: string | null };
}

export interface BackendNotification {
  id: string;
  title: string;
  message: string;
  type: 'flood_alert' | 'weather_warning' | 'evacuation_order' | 'safety_update';
  priority: 'low' | 'medium' | 'high' | 'critical';
  timestamp: string;
  read: boolean;
  fullMessage?: string;
}

export interface NotificationsResponse {
  items: BackendNotification[];
  risk_level?: 'low' | 'moderate' | 'high' | null;
  average_risk?: number | null;
}

const CACHE_TTL = {
  routeMs: 1000 * 60 * 60 * 6,
  weatherMs: 1000 * 60 * 30,
  floodRiskMs: 1000 * 60 * 20,
  floodOverlayMs: 1000 * 60 * 60 * 12,
  evacuationCentersMs: 1000 * 60 * 60 * 24,
  nearestCenterMs: 1000 * 60 * 30,
  notificationsMs: 1000 * 60 * 15,
} as const;

const CACHE_KEY = {
  evacuationCenters: 'evacuation-centers',
  floodOverlayGlobal: 'flood-overlay:global',
} as const;

function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371.0;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dphi = ((lat2 - lat1) * Math.PI) / 180;
  const dlmb = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlmb / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(a)));
}

class ApiService {
  private baseUrl: string;

  constructor(baseUrl: string = BACKEND_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private sheltrAuthHeaders(): Record<string, string> {
    const key = process.env.EXPO_PUBLIC_SHELTR_API_KEY?.trim();
    if (!key) return {};
    return { 'X-Sheltr-Key': key };
  }

  private validateCoordinates(latitude: number, longitude: number): void {
    if (typeof latitude !== 'number' || typeof longitude !== 'number') {
      throw new Error('Latitude and longitude must be numbers');
    }
    if (latitude < -90 || latitude > 90) {
      throw new Error('Latitude must be between -90 and 90');
    }
    if (longitude < -180 || longitude > 180) {
      throw new Error('Longitude must be between -180 and 180');
    }
  }

  private async fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...this.sheltrAuthHeaders(),
        ...(init?.headers ?? {}),
      },
      ...init,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(
        `HTTP error! status: ${response.status}, message: ${(errorData as any).error || 'Unknown error'}`
      );
    }

    return (await response.json()) as T;
  }

  private async withOfflineCache<T>(
    cacheKey: string,
    ttlMs: number,
    fetcher: () => Promise<T>
  ): Promise<T> {
    try {
      const fresh = await fetcher();
      await setCachedValue(cacheKey, fresh, ttlMs);
      return fresh;
    } catch (error) {
      const cached = await getCachedValue<T>(cacheKey, { allowExpired: true });
      if (cached) {
        console.warn(`Using cached response for ${cacheKey}`, { isExpired: cached.isExpired });
        return cached.data;
      }
      throw error;
    }
  }

  private nearestCenterFromList(
    centers: EvacuationCenter[],
    latitude: number,
    longitude: number,
    openOnly: boolean
  ): EvacuationCenter | null {
    let best: EvacuationCenter | null = null;
    let bestKm = Number.POSITIVE_INFINITY;
    for (const center of centers) {
      if (openOnly && center.is_open === false) continue;
      if (!Number.isFinite(center.latitude) || !Number.isFinite(center.longitude)) continue;
      const d = haversineKm(latitude, longitude, center.latitude, center.longitude);
      if (d < bestKm) {
        bestKm = d;
        best = center;
      }
    }
    return best ? { ...best, distance: Number(bestKm.toFixed(3)) } : null;
  }

  async calculateRoute(
    start: { latitude: number; longitude: number },
    end: { latitude: number; longitude: number },
    _profile?: string,
    floodScenario: FloodScenario = 'auto'
  ): Promise<RouteResponse> {
    this.validateCoordinates(start.latitude, start.longitude);
    this.validateCoordinates(end.latitude, end.longitude);
    const cacheKey = routeCacheKey(`route:${floodScenario}`, start, end);
    return this.withOfflineCache<RouteResponse>(cacheKey, CACHE_TTL.routeMs, () =>
      this.fetchJson<RouteResponse>('/route', {
        method: 'POST',
        body: JSON.stringify({
          origin: [start.longitude, start.latitude],
          destination: [end.longitude, end.latitude],
          flood_scenario: floodScenario,
        }),
      })
    );
  }

  /** Server-side OpenRouter briefing; not cached. Pass the full `/route` JSON body. */
  async routeBriefing(routePayload: object): Promise<RouteBriefingResponse> {
    return this.fetchJson<RouteBriefingResponse>('/route-briefing', {
      method: 'POST',
      body: JSON.stringify(routePayload),
    });
  }

  async getEvacuationCenters(): Promise<EvacuationCenter[]> {
    return this.withOfflineCache<EvacuationCenter[]>(
      CACHE_KEY.evacuationCenters,
      CACHE_TTL.evacuationCentersMs,
      () => this.fetchJson<EvacuationCenter[]>('/evacuation-centers', { method: 'GET' })
    );
  }

  async getNearestEvacuationCenter(
    latitude: number,
    longitude: number,
    openOnly: boolean = true
  ): Promise<EvacuationCenter | null> {
    this.validateCoordinates(latitude, longitude);
    const query = `?latitude=${encodeURIComponent(String(latitude))}&longitude=${encodeURIComponent(
      String(longitude)
    )}&open_only=${openOnly ? '1' : '0'}`;
    const cacheKey = latLngCacheKey(`nearest-evac:${openOnly ? 'open' : 'all'}`, latitude, longitude);
    try {
      const response = await fetch(`${this.baseUrl}/nearest-evacuation${query}`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json', ...this.sheltrAuthHeaders() },
      });
      if (response.status === 404) {
        await setCachedValue<EvacuationCenter | null>(cacheKey, null, CACHE_TTL.nearestCenterMs);
        return null;
      }
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(`Nearest evacuation request failed: ${response.status} ${(err as any).error || ''}`);
      }
      const nearest = (await response.json()) as EvacuationCenter;
      await setCachedValue<EvacuationCenter | null>(cacheKey, nearest, CACHE_TTL.nearestCenterMs);
      return nearest;
    } catch (error) {
      const cachedNearest = await getCachedValue<EvacuationCenter | null>(cacheKey, {
        allowExpired: true,
      });
      if (cachedNearest) {
        console.warn(`Using cached nearest center for ${cacheKey}`, { isExpired: cachedNearest.isExpired });
        return cachedNearest.data;
      }
      const centersCached = await getCachedValue<EvacuationCenter[]>(CACHE_KEY.evacuationCenters, {
        allowExpired: true,
      });
      if (centersCached?.data?.length) {
        return this.nearestCenterFromList(centersCached.data, latitude, longitude, openOnly);
      }
      throw error;
    }
  }

  async checkHealth(): Promise<HealthResponse> {
    try {
      const response = await fetch(`${this.baseUrl}/health`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json', ...this.sheltrAuthHeaders() },
      });
      if (!response.ok) {
        return { healthy: false, status: 'unreachable' };
      }
      const data = (await response.json()) as Record<string, unknown>;
      return {
        healthy: typeof data.healthy === 'boolean' ? data.healthy : response.ok,
        status: typeof data.status === 'string' ? data.status : undefined,
        stadia_configured: data.stadia_configured as boolean | undefined,
        flood_layer_loaded: data.flood_layer_loaded as boolean | undefined,
        flood_layer_error: typeof data.flood_layer_error === 'string' ? data.flood_layer_error : undefined,
        evacuation_centers: typeof data.evacuation_centers === 'number' ? data.evacuation_centers : undefined,
      };
    } catch (error) {
      console.error('Error checking health:', error);
      return { healthy: false, status: 'error' };
    }
  }

  async getWeatherData(latitude: number, longitude: number): Promise<WeatherData> {
    this.validateCoordinates(latitude, longitude);
    const cacheKey = latLngCacheKey('weather', latitude, longitude);
    return this.withOfflineCache<WeatherData>(cacheKey, CACHE_TTL.weatherMs, () =>
      this.fetchJson<WeatherData>(
        `/weather?latitude=${encodeURIComponent(String(latitude))}&longitude=${encodeURIComponent(
          String(longitude)
        )}`,
        { method: 'GET' }
      )
    );
  }

  async getFloodRisk(latitude: number, longitude: number): Promise<FloodRiskSample[]> {
    this.validateCoordinates(latitude, longitude);
    const cacheKey = latLngCacheKey('flood-risk', latitude, longitude);
    return this.withOfflineCache<FloodRiskSample[]>(cacheKey, CACHE_TTL.floodRiskMs, () =>
      this.fetchJson<FloodRiskSample[]>(
        `/risk?latitude=${encodeURIComponent(String(latitude))}&longitude=${encodeURIComponent(
          String(longitude)
        )}`,
        { method: 'GET' }
      )
    );
  }

  async getFloodOverlay(
    latitude?: number,
    longitude?: number,
    floodScenario: FloodScenario = 'auto'
  ): Promise<FloodOverlayResponse> {
    if (typeof latitude === 'number' && typeof longitude === 'number') {
      this.validateCoordinates(latitude, longitude);
      const cacheKey = latLngCacheKey(`flood-overlay:${floodScenario}`, latitude, longitude);
      const scenarioQuery =
        floodScenario === 'auto'
          ? ''
          : `&scenario=${encodeURIComponent(floodScenario)}`;
      return this.withOfflineCache<FloodOverlayResponse>(cacheKey, CACHE_TTL.floodOverlayMs, () =>
        this.fetchJson<FloodOverlayResponse>(
          `/flood-overlay?latitude=${encodeURIComponent(String(latitude))}&longitude=${encodeURIComponent(
            String(longitude)
          )}${scenarioQuery}`,
          { method: 'GET' }
        )
      );
    }
    return this.withOfflineCache<FloodOverlayResponse>(
      `${CACHE_KEY.floodOverlayGlobal}:${floodScenario}`,
      CACHE_TTL.floodOverlayMs,
      () =>
        this.fetchJson<FloodOverlayResponse>(
          `/flood-overlay${floodScenario === 'auto' ? '' : `?scenario=${encodeURIComponent(floodScenario)}`}`,
          { method: 'GET' }
        )
    );
  }

  async getNotifications(latitude: number, longitude: number): Promise<NotificationsResponse> {
    this.validateCoordinates(latitude, longitude);
    const cacheKey = latLngCacheKey('notifications', latitude, longitude);
    return this.withOfflineCache<NotificationsResponse>(cacheKey, CACHE_TTL.notificationsMs, () =>
      this.fetchJson<NotificationsResponse>(
        `/notifications?latitude=${encodeURIComponent(String(latitude))}&longitude=${encodeURIComponent(
          String(longitude)
        )}`,
        { method: 'GET' }
      )
    );
  }
}

export const apiService = new ApiService();
export default apiService;
