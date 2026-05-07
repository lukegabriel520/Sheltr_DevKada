import AsyncStorage from '@react-native-async-storage/async-storage';

type CacheEnvelope<T> = {
  savedAt: number;
  ttlMs: number | null;
  data: T;
};

export type CachedPayload<T> = {
  data: T;
  savedAt: number;
  isExpired: boolean;
};

export type CachedUserLocation = {
  latitude: number;
  longitude: number;
  savedAt: number;
};

const CACHE_PREFIX = 'sheltr:cache:';
const USER_LOCATION_KEY = 'sheltr:user-location';

function keyFor(cacheKey: string): string {
  return `${CACHE_PREFIX}${cacheKey}`;
}

function roundCoord(value: number, digits: number = 4): string {
  return value.toFixed(digits);
}

export function latLngCacheKey(prefix: string, latitude: number, longitude: number): string {
  return `${prefix}:${roundCoord(latitude)}:${roundCoord(longitude)}`;
}

export function routeCacheKey(
  prefix: string,
  start: { latitude: number; longitude: number },
  end: { latitude: number; longitude: number }
): string {
  return `${prefix}:${roundCoord(start.latitude, 5)}:${roundCoord(start.longitude, 5)}:${roundCoord(
    end.latitude,
    5
  )}:${roundCoord(end.longitude, 5)}`;
}

export async function setCachedValue<T>(cacheKey: string, data: T, ttlMs: number | null): Promise<void> {
  const envelope: CacheEnvelope<T> = {
    savedAt: Date.now(),
    ttlMs: typeof ttlMs === 'number' ? ttlMs : null,
    data,
  };
  await AsyncStorage.setItem(keyFor(cacheKey), JSON.stringify(envelope));
}

export async function getCachedValue<T>(
  cacheKey: string,
  options?: { allowExpired?: boolean }
): Promise<CachedPayload<T> | null> {
  const raw = await AsyncStorage.getItem(keyFor(cacheKey));
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as CacheEnvelope<T>;
    const savedAt = typeof parsed.savedAt === 'number' ? parsed.savedAt : 0;
    const ttlMs = typeof parsed.ttlMs === 'number' ? parsed.ttlMs : null;
    const isExpired = ttlMs == null ? false : Date.now() > savedAt + ttlMs;
    if (isExpired && !options?.allowExpired) {
      return null;
    }
    return { data: parsed.data, savedAt, isExpired };
  } catch {
    return null;
  }
}

export async function writeCachedUserLocation(latitude: number, longitude: number): Promise<void> {
  const payload: CachedUserLocation = {
    latitude,
    longitude,
    savedAt: Date.now(),
  };
  await AsyncStorage.setItem(USER_LOCATION_KEY, JSON.stringify(payload));
}

export async function readCachedUserLocation(): Promise<CachedUserLocation | null> {
  const raw = await AsyncStorage.getItem(USER_LOCATION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as CachedUserLocation;
    if (
      typeof parsed.latitude !== 'number' ||
      typeof parsed.longitude !== 'number' ||
      !Number.isFinite(parsed.latitude) ||
      !Number.isFinite(parsed.longitude)
    ) {
      return null;
    }
    return {
      latitude: parsed.latitude,
      longitude: parsed.longitude,
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : 0,
    };
  } catch {
    return null;
  }
}
