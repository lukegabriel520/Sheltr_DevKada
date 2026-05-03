// app/(tabs)/map.tsx
import { ThemedText } from '@/components/themed-text';
import { IconSymbol } from '@/components/ui/icon-symbol';
import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  StyleSheet,
  View,
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import { apiService, EvacuationCenter, FloodOverlayResponse, FloodScenario } from '@/services/api';
import { loadEvacuationCenters, resolveNearestOpenCenter } from '@/services/evacuationCenters';
import { readCachedUserLocation, writeCachedUserLocation } from '@/services/offlineCache';

type MapMode = 'nearest' | 'browse';

type FloodOverlayColor = {
  fill: string;
  stroke: string;
};
type CachedCoords = { lat: number; lng: number };
let lastCachedUserCoords: CachedCoords | null = null;

// Edit these colors to tune the severity look on the flood overlay polygons.
const FLOOD_OVERLAY_COLORS: Record<string, FloodOverlayColor> = {
  '1': { fill: '#EF4444', stroke: '#B91C1C' }, // Var 1: high flood
  '2': { fill: '#FB923C', stroke: '#C2410C' }, // Var 2: moderate flood warning
  '3': { fill: '#FACC15', stroke: '#CA8A04' }, // Var 3: lower flood warning
  default: { fill: '#94A3B8', stroke: '#64748B' },
};

// Storm-surge overlays (HAZ): high=1, medium=2, low=3.
const STORM_SURGE_OVERLAY_COLORS: Record<string, FloodOverlayColor> = {
  '1': { fill: '#0F766E', stroke: '#115E59' }, // HAZ 1: high storm surge
  '2': { fill: '#14B8A6', stroke: '#0F766E' }, // HAZ 2: medium storm surge
  '3': { fill: '#67E8F9', stroke: '#0891B2' }, // HAZ 3: low storm surge
  default: { fill: '#94A3B8', stroke: '#64748B' },
};

const FLOOD_SCENARIO_OPTIONS: Array<{ id: FloodScenario; label: string }> = [
  { id: 'auto', label: 'Auto' },
  { id: 'sts', label: 'STS' },
  { id: 'typhoon', label: 'TY' },
  { id: 'super_typhoon', label: 'STY' },
];

function AppIcon({ name, size, color }: { name: string; size: number; color: string }) {
  if (Platform.OS === 'ios') {
    return <IconSymbol name={name as any} size={size} color={color} />;
  }
  const map: Record<string, any> = {
    'chevron.left': 'chevron-back',
    'bell.fill': 'notifications',
    house: 'home',
    'location.north.line': 'navigate',
  };
  return <Ionicons name={map[name] ?? name} size={size} color={color} />;
}

const generateMapHTML = (
  centers: EvacuationCenter[],
  userLocation: { lat: number; lng: number } | null,
  route: [number, number][] | null,
  floodOverlay: FloodOverlayResponse | null,
  floodVarColors: Record<string, FloodOverlayColor>,
  stormHazColors: Record<string, FloodOverlayColor>
) => `<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  html,body,#map{height:100%;margin:0}
  .custom-marker{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.3)}
  .user-dot{background:#3B82F6;width:16px;height:16px;border-radius:50%;border:3px solid #fff;box-shadow:0 0 0 4px rgba(59,130,246,.3)}
  .user-arrow-wrap{
    width:28px;height:28px;border-radius:14px;background:#fff;border:1px solid rgba(11,61,91,.2);
    display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.2)
  }
  .user-arrow{
    width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;
    border-bottom:13px solid #3B82F6;transform-origin:50% 70%;
  }
  .popup-title{font-size:14px;font-weight:700;color:#0B3D5B;margin-bottom:6px}
  .popup-info{font-size:12px;color:#6B7280}
</style>
</head><body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  const map = L.map('map',{zoomControl:false,attributionControl:false})
    .setView([${userLocation ? userLocation.lat : 14.5378},${userLocation ? userLocation.lng : 121.0475}],16);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
  let userMarker = null;
  function makeUserIcon(heading){
    if (typeof heading === 'number' && isFinite(heading) && heading >= 0){
      return L.divIcon({
        className:'user-marker',
        html:'<div class="user-arrow-wrap"><div class="user-arrow" style="transform: rotate('+heading+'deg)"></div></div>',
        iconSize:[28,28],
        iconAnchor:[14,14]
      });
    }
    return L.divIcon({
      className:'user-marker',
      html:'<div class="user-dot"></div>',
      iconSize:[16,16],
      iconAnchor:[8,8]
    });
  }
  function upsertUser(lat,lng,heading){
    const icon = makeUserIcon(heading);
    if(!userMarker){
      userMarker = L.marker([lat,lng],{icon}).addTo(map);
      return;
    }
    userMarker.setLatLng([lat,lng]);
    userMarker.setIcon(icon);
  }

  const floodOverlay = ${JSON.stringify(floodOverlay)};
  const floodVarColors = ${JSON.stringify(floodVarColors)};
  const stormHazColors = ${JSON.stringify(stormHazColors)};
  if (floodOverlay && Array.isArray(floodOverlay.features) && floodOverlay.features.length > 0) {
    map.createPane('floodOverlayPane');
    map.getPane('floodOverlayPane').style.zIndex = '420';
    L.geoJSON(floodOverlay, {
      pane: 'floodOverlayPane',
      style: (feature) => {
        const props = feature && feature.properties ? feature.properties : {};
        const overlayType = String(props.overlay_type || '');
        const v = Number(props.Var);
        const h = Number(props.HAZ);
        let palette = floodVarColors.default;
        if (overlayType === 'storm_surge' || (!Number.isFinite(v) && Number.isFinite(h))) {
          palette = Number.isFinite(h) ? stormHazColors[String(h)] || stormHazColors.default : stormHazColors.default;
        } else {
          palette = Number.isFinite(v) ? floodVarColors[String(v)] || floodVarColors.default : floodVarColors.default;
        }
        return {
          color: palette.stroke,
          fillColor: palette.fill,
          fillOpacity: 0.45,
          weight: 1.2,
        };
      },
    }).addTo(map);
  }

  (${JSON.stringify(centers)}).forEach(c=>{
    const score = Number(c.safety_score);
    const fill = Number.isFinite(score)
      ? (score >= 0.9 ? '#22C55E' : score >= 0.7 ? '#FACC15' : '#EF4444')
      : '#10B981';
    const m=L.marker([c.latitude,c.longitude],{
      icon:L.divIcon({
        className:'custom-marker',
        html: \`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="32" height="32">
            <circle cx="12" cy="12" r="11" fill="\${fill}" stroke="white" stroke-width="2"/>
            <path d="M6 15v-3l6-4 6 4v3" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <rect x="9" y="12" width="6" height="6" fill="white" stroke="none"/>
          </svg>\`,
        iconSize:[36,36],
        iconAnchor:[18,18]
      })
    }).addTo(map);
    m.bindPopup('<div class="popup-title">'+c.name+'</div><div class="popup-info">Capacity: '+(c.capacity||'N/A')+' people</div>');
    m.on('click',()=>window.ReactNativeWebView.postMessage(JSON.stringify({type:'centerSelected',center:c})));
  });

  const user = ${userLocation ? JSON.stringify(userLocation) : 'null'};
  if (user){
    upsertUser(user.lat,user.lng,null);
  }

  const routeLine = ${JSON.stringify(route)};
  if (routeLine && routeLine.length > 0) {
    const latlngs = routeLine.map(c => [c[1], c[0]]);
    L.polyline(latlngs, {color: '#16A34A', weight: 5, opacity: 0.95}).addTo(map);
    map.fitBounds(latlngs);
  }

  window.fromRN = function(data){
    if(!data || typeof data !== 'object') return;
    if(data.type==='centerMap' && Number.isFinite(data.lat) && Number.isFinite(data.lon)){
      const zoom = Number.isFinite(data.zoom) ? data.zoom : 16;
      map.setView([data.lat, data.lon], zoom);
      return;
    }
    if(data.type==='updateUser' && Number.isFinite(data.lat) && Number.isFinite(data.lon)){
      upsertUser(data.lat, data.lon, Number.isFinite(data.heading) ? data.heading : null);
      return;
    }
  }

</script>
</body></html>`;

type RouteMeta = {
  distance_km: number;
  duration_minutes: number | null;
  flood_risk: number;
  flood_overlap_fraction?: number | null;
  safety_score: number;
  cost_matrix_total?: number;
  destination_center_safety_score?: number | null;
  destination_center_elevation?: number | null;
  warning?: string;
  reroute_attempts?: number;
  reroute_max?: number;
  overlap_points_per_pass?: number[];
};

function formatDuration(minutes: number | null | undefined): string {
  if (typeof minutes !== 'number' || !Number.isFinite(minutes) || minutes <= 0) {
    return 'ETA unavailable';
  }
  return `~${Math.round(minutes)} min drive`;
}

function formatCapacity(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 'Not listed';
  return `~${Math.round(value).toLocaleString()} people`;
}

function formatOpenStatus(value: boolean | undefined): string {
  if (value === false) return 'Temporarily closed';
  if (value === true) return 'Open';
  return 'Status not confirmed';
}

function toRouteMeta(
  routeData: Awaited<ReturnType<typeof apiService.calculateRoute>>,
  center: EvacuationCenter | null
): RouteMeta {
  const attempts =
    typeof routeData.route_stats?.reroute_attempts === 'number'
      ? routeData.route_stats.reroute_attempts
      : undefined;
  const maxAttempts =
    typeof routeData.route_stats?.reroute_max === 'number'
      ? routeData.route_stats.reroute_max
      : undefined;
  const overlapPasses = Array.isArray(routeData.route_stats?.overlap_points_per_pass)
    ? routeData.route_stats?.overlap_points_per_pass.filter((v): v is number => typeof v === 'number')
    : undefined;
  return {
    distance_km: routeData.route.distance_km,
    duration_minutes: routeData.route.duration_minutes,
    flood_risk: routeData.route.flood_risk,
    flood_overlap_fraction:
      routeData.route.flood_overlap_fraction ??
      (typeof routeData.route_stats?.flood_overlap_fraction === 'number'
        ? routeData.route_stats.flood_overlap_fraction
        : null),
    safety_score: routeData.route.safety_score,
    cost_matrix_total:
      routeData.route.cost_matrix_total ??
      (typeof routeData.route_stats?.selected_cost_matrix_total === 'number'
        ? routeData.route_stats.selected_cost_matrix_total
        : undefined),
    destination_center_safety_score:
      routeData.route.destination_center_safety_score ??
      (typeof center?.safety_score === 'number' && Number.isFinite(center.safety_score)
        ? center.safety_score
        : null),
    destination_center_elevation:
      routeData.route.destination_center_elevation ??
      (typeof center?.elevation === 'number' && Number.isFinite(center.elevation)
        ? center.elevation
        : null),
    warning: routeData.warning,
    reroute_attempts: attempts,
    reroute_max: maxAttempts,
    overlap_points_per_pass: overlapPasses,
  };
}

function pointInRing(lng: number, lat: number, ring: [number, number][]): boolean {
  if (!Array.isArray(ring) || ring.length < 4) return false;
  let inside = false;
  let j = ring.length - 1;
  for (let i = 0; i < ring.length; i += 1) {
    const xi = ring[i][0];
    const yi = ring[i][1];
    const xj = ring[j][0];
    const yj = ring[j][1];
    const intersects =
      (yi > lat) !== (yj > lat) &&
      lng < ((xj - xi) * (lat - yi)) / ((yj - yi) || Number.EPSILON) + xi;
    if (intersects) inside = !inside;
    j = i;
  }
  return inside;
}

function pointInsideGeometry(lng: number, lat: number, geometry: FloodOverlayResponse['features'][number]['geometry']): boolean {
  if (!geometry || !geometry.type || !geometry.coordinates) return false;
  if (geometry.type === 'Polygon' && Array.isArray(geometry.coordinates)) {
    const outer = geometry.coordinates[0];
    if (Array.isArray(outer)) {
      return pointInRing(lng, lat, outer as [number, number][]);
    }
    return false;
  }
  if (geometry.type === 'MultiPolygon' && Array.isArray(geometry.coordinates)) {
    for (const polygon of geometry.coordinates as unknown[]) {
      if (!Array.isArray(polygon) || polygon.length === 0) continue;
      const outer = polygon[0];
      if (Array.isArray(outer) && pointInRing(lng, lat, outer as [number, number][])) {
        return true;
      }
    }
  }
  return false;
}

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const webViewRef = useRef<WebView>(null);
  const locationRef = useRef<Location.LocationObject | null>(null);
  const locationWatchRef = useRef<Location.LocationSubscription | null>(null);
  const [location, setLocation] = useState<Location.LocationObject | null>(null);
  const [cachedCoords, setCachedCoords] = useState<CachedCoords | null>(lastCachedUserCoords);
  const [evacuationCenters, setEvacuationCenters] = useState<EvacuationCenter[]>([]);
  const [route, setRoute] = useState<[number, number][] | null>(null);
  const [mapMode, setMapMode] = useState<MapMode>('nearest');
  const [floodScenario, setFloodScenario] = useState<FloodScenario>('auto');
  const [layerMenuOpen, setLayerMenuOpen] = useState(false);
  const layerDropdownAnim = useRef(new Animated.Value(0)).current;
  const [booting, setBooting] = useState(true);
  const [routingBusy, setRoutingBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summaryCenter, setSummaryCenter] = useState<EvacuationCenter | null>(null);
  const [lastRouteInfo, setLastRouteInfo] = useState<RouteMeta | null>(null);
  const [floodOverlay, setFloodOverlay] = useState<FloodOverlayResponse | null>(null);
  const [activeRouteDestinationKey, setActiveRouteDestinationKey] = useState<string | null>(null);
  const [activeRouteDestinationCoords, setActiveRouteDestinationCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [pendingRouteDestinationKey, setPendingRouteDestinationKey] = useState<string | null>(null);
  const advisoryOpacity = useRef(new Animated.Value(0)).current;
  const advisoryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAdvisoryKeyRef = useRef<string | null>(null);
  const [advisoryVisible, setAdvisoryVisible] = useState(false);

  const destinationKey = useCallback((lat: number, lng: number) => `${lat.toFixed(5)},${lng.toFixed(5)}`, []);

  const emitUserToMap = useCallback(
    (lat: number, lng: number, heading: number | null) => {
      const js = `window.fromRN && window.fromRN(${JSON.stringify({
        type: 'updateUser',
        lat,
        lon: lng,
        heading,
      })}); true;`;
      webViewRef.current?.injectJavaScript?.(js);
    },
    []
  );

  useEffect(() => {
    Animated.timing(layerDropdownAnim, {
      toValue: layerMenuOpen ? 1 : 0,
      duration: 180,
      useNativeDriver: true,
    }).start();
  }, [layerMenuOpen, layerDropdownAnim]);

  const handleRouteTo = useCallback(
    async (
      lat: number,
      lng: number,
      center?: EvacuationCenter | null,
      opts?: { force?: boolean; openSummary?: boolean }
    ) => {
      const live = locationRef.current;
      const origin = live
        ? { latitude: live.coords.latitude, longitude: live.coords.longitude }
        : location
          ? { latitude: location.coords.latitude, longitude: location.coords.longitude }
        : cachedCoords
          ? { latitude: cachedCoords.lat, longitude: cachedCoords.lng }
          : null;
      if (!origin) {
        Alert.alert('Location Required', 'Please enable location services to calculate routes.');
        return;
      }

      const targetKey = destinationKey(lat, lng);
      if (!opts?.force && route && activeRouteDestinationKey === targetKey) {
        return;
      }

      try {
        setPendingRouteDestinationKey(targetKey);
        setRoutingBusy(true);
        const routeData = await apiService.calculateRoute(
          origin,
          { latitude: lat, longitude: lng },
          undefined,
          floodScenario
        );
        if (routeData.route && routeData.route.coordinates) {
          setRoute(routeData.route.coordinates as [number, number][]);
          const resolved =
            center ||
            evacuationCenters.find(
              (c) =>
                Math.abs(c.latitude - lat) < 1e-5 && Math.abs(c.longitude - lng) < 1e-5
            ) ||
            null;
          setLastRouteInfo(toRouteMeta(routeData, resolved));
          if (opts?.openSummary) {
            setSummaryCenter(resolved);
          }
          setActiveRouteDestinationKey(targetKey);
          setActiveRouteDestinationCoords({ lat, lng });
        } else {
          throw new Error('Invalid route response');
        }
      } catch (e) {
        console.error('Error calculating route:', e);
        Alert.alert(
          'Route Calculation Failed',
          'Unable to calculate a road route. Check your internet connection and that the Sheltr server is running.'
        );
      } finally {
        setRoutingBusy(false);
        setPendingRouteDestinationKey(null);
      }
    },
    [
      location,
      cachedCoords,
      evacuationCenters,
      floodScenario,
      destinationKey,
      route,
      activeRouteDestinationKey,
    ]
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const persisted = await readCachedUserLocation();
      if (!cancelled && persisted) {
        const next = { lat: persisted.latitude, lng: persisted.longitude };
        lastCachedUserCoords = next;
        setCachedCoords(next);
      }
      const { status } = await Location.requestForegroundPermissionsAsync();
      let origin: { latitude: number; longitude: number } | null = persisted
        ? { latitude: persisted.latitude, longitude: persisted.longitude }
        : null;

      if (status === 'granted') {
        try {
          const lastKnown = await Location.getLastKnownPositionAsync({
            maxAge: 1000 * 60 * 60 * 24,
          });
          if (!cancelled && lastKnown) {
            locationRef.current = lastKnown;
            setLocation(lastKnown);
            const next = { lat: lastKnown.coords.latitude, lng: lastKnown.coords.longitude };
            lastCachedUserCoords = next;
            setCachedCoords(next);
            origin = { latitude: next.lat, longitude: next.lng };
            await writeCachedUserLocation(next.lat, next.lng);
            emitUserToMap(
              next.lat,
              next.lng,
              Number.isFinite(lastKnown.coords.heading) ? lastKnown.coords.heading : null
            );
          }
        } catch (err) {
          console.warn('Could not read last known location:', err);
        }
        try {
          const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          if (cancelled) return;
          locationRef.current = loc;
          setLocation(loc);
          const next = { lat: loc.coords.latitude, lng: loc.coords.longitude };
          lastCachedUserCoords = next;
          setCachedCoords(next);
          origin = { latitude: next.lat, longitude: next.lng };
          await writeCachedUserLocation(next.lat, next.lng);
          emitUserToMap(
            next.lat,
            next.lng,
            Number.isFinite(loc.coords.heading) ? loc.coords.heading : null
          );
        } catch (err) {
          console.warn('Could not read current location:', err);
        }
        try {
          locationWatchRef.current = await Location.watchPositionAsync(
            {
              accuracy: Location.Accuracy.Balanced,
              timeInterval: 3000,
              distanceInterval: 8,
              mayShowUserSettingsDialog: true,
            },
            async (loc) => {
              if (cancelled) return;
              locationRef.current = loc;
              const next = { lat: loc.coords.latitude, lng: loc.coords.longitude };
              lastCachedUserCoords = next;
              await writeCachedUserLocation(next.lat, next.lng);
              emitUserToMap(
                next.lat,
                next.lng,
                Number.isFinite(loc.coords.heading) ? loc.coords.heading : null
              );
            }
          );
        } catch (err) {
          console.warn('Could not start location watcher:', err);
        }
      } else if (!origin) {
        setError('Location permission is needed for live routing.');
      }

      try {
        const centers = await loadEvacuationCenters();
        if (cancelled) return;
        setEvacuationCenters(centers);
        setError(null);

        if (!origin) {
          setBooting(false);
          return;
        }
        const nearest = await resolveNearestOpenCenter(origin, centers);
        if (nearest) {
          setRoutingBusy(true);
          try {
            const routeData = await apiService.calculateRoute(origin, {
              latitude: nearest.latitude,
              longitude: nearest.longitude,
            }, undefined, floodScenario);
            if (cancelled) return;
            if (routeData.route?.coordinates) {
              setRoute(routeData.route.coordinates as [number, number][]);
              setLastRouteInfo(toRouteMeta(routeData, nearest));
              setSummaryCenter(nearest);
              setActiveRouteDestinationKey(destinationKey(nearest.latitude, nearest.longitude));
              setActiveRouteDestinationCoords({ lat: nearest.latitude, lng: nearest.longitude });
            }
          } finally {
            if (!cancelled) setRoutingBusy(false);
          }
        }
      } catch (err) {
        console.error(err);
        if (!cancelled) {
          setEvacuationCenters([]);
          setError('Could not load evacuation centers. Check the Sheltr API.');
        }
      }
      if (!cancelled) setBooting(false);
    })();
    return () => {
      cancelled = true;
      if (locationWatchRef.current) {
        locationWatchRef.current.remove();
        locationWatchRef.current = null;
      }
    };
  }, [emitUserToMap, destinationKey]);

  useEffect(() => {
    const target = activeRouteDestinationCoords;
    if (!target) return;
    if (!locationRef.current && !location && !cachedCoords) return;
    handleRouteTo(target.lat, target.lng, null, { force: true, openSummary: false }).catch(() => {
      /* no-op */
    });
  }, [floodScenario]);

  useEffect(() => {
    const sourceCoords = location
      ? { latitude: location.coords.latitude, longitude: location.coords.longitude }
      : cachedCoords
        ? { latitude: cachedCoords.lat, longitude: cachedCoords.lng }
        : null;
    if (!sourceCoords) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const overlay = await apiService.getFloodOverlay(
          sourceCoords.latitude,
          sourceCoords.longitude,
          floodScenario
        );
        if (!cancelled) setFloodOverlay(overlay);
      } catch (err) {
        console.warn('Could not load flood overlay polygons:', err);
        if (!cancelled) setFloodOverlay(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [location, cachedCoords, floodScenario]);

  const userLoc = location
    ? { lat: location.coords.latitude, lng: location.coords.longitude }
    : cachedCoords;
  const userInFloodPolygon = useMemo(() => {
    if (!userLoc || !floodOverlay?.features?.length) return false;
    for (const feature of floodOverlay.features) {
      if (pointInsideGeometry(userLoc.lng, userLoc.lat, feature.geometry)) {
        return true;
      }
    }
    return false;
  }, [userLoc, floodOverlay]);
  const routeMaxedButStillFlooded = useMemo(() => {
    if (!lastRouteInfo) return false;
    const attempts = lastRouteInfo.reroute_attempts;
    const maxAttempts = lastRouteInfo.reroute_max;
    const overlaps = lastRouteInfo.overlap_points_per_pass;
    const lastOverlap = overlaps && overlaps.length > 0 ? overlaps[overlaps.length - 1] : 0;
    const warningText = (lastRouteInfo.warning || '').toLowerCase();
    const byStats =
      typeof attempts === 'number' &&
      typeof maxAttempts === 'number' &&
      attempts >= maxAttempts &&
      typeof lastOverlap === 'number' &&
      lastOverlap > 0;
    const byWarning =
      warningText.includes('max reroute passes') ||
      warningText.includes('reroute failed after adaptive retries') ||
      warningText.includes('no path could be found');
    return byStats || byWarning;
  }, [lastRouteInfo]);

  const safetyAdvisories = useMemo(() => {
    const messages: string[] = [];
    if (userInFloodPolygon) {
      messages.push('You are in a flood-risk area. If roads look unsafe, stay put or move to higher ground.');
    }
    if (routeMaxedButStillFlooded) {
      messages.push("No safer route found. Stay where you are for now and wait for official guidance.");
    }
    return messages;
  }, [userInFloodPolygon, routeMaxedButStillFlooded]);
  const advisoryMessage = useMemo(() => {
    const routeWarning = safetyAdvisories.find((m) => m.toLowerCase().includes('no safer route'));
    return routeWarning || safetyAdvisories[0] || null;
  }, [safetyAdvisories]);
  const advisoryKey = useMemo(() => {
    if (!advisoryMessage) return null;
    return [
      advisoryMessage,
      activeRouteDestinationKey || 'none',
      lastRouteInfo?.warning || '',
      String(lastRouteInfo?.reroute_attempts ?? 0),
      String(lastRouteInfo?.reroute_max ?? 0),
    ].join('|');
  }, [advisoryMessage, activeRouteDestinationKey, lastRouteInfo]);

  useEffect(() => {
    if (!advisoryKey || !advisoryMessage) {
      setAdvisoryVisible(false);
      advisoryOpacity.setValue(0);
      return;
    }
    if (lastAdvisoryKeyRef.current === advisoryKey) {
      return;
    }
    lastAdvisoryKeyRef.current = advisoryKey;
    if (advisoryTimerRef.current) {
      clearTimeout(advisoryTimerRef.current);
      advisoryTimerRef.current = null;
    }
    setAdvisoryVisible(true);
    advisoryOpacity.setValue(1);
    advisoryTimerRef.current = setTimeout(() => {
      Animated.timing(advisoryOpacity, {
        toValue: 0,
        duration: 350,
        useNativeDriver: true,
      }).start(() => {
        setAdvisoryVisible(false);
      });
    }, 5000);
  }, [advisoryKey, advisoryMessage, advisoryOpacity]);

  useEffect(() => {
    return () => {
      if (advisoryTimerRef.current) {
        clearTimeout(advisoryTimerRef.current);
        advisoryTimerRef.current = null;
      }
    };
  }, []);

  const mapHTML = generateMapHTML(
    evacuationCenters,
    userLoc,
    route,
    floodOverlay,
    FLOOD_OVERLAY_COLORS,
    STORM_SURGE_OVERLAY_COLORS
  );
  const routingForNewTarget =
    pendingRouteDestinationKey != null &&
    pendingRouteDestinationKey !== activeRouteDestinationKey;
  const statusMessage = booting
    ? 'Preparing your evacuation map…'
    : routingBusy && (!route || routingForNewTarget)
      ? 'Finding a road route…'
      : null;
  const STATUS_BLOCK = 60;
  const ADVISORY_BLOCK = 56;
  const BASE_FLOATING_BOTTOM = 104;
  const statusLift = statusMessage ? STATUS_BLOCK : 0;
  const advisoryLift = advisoryVisible ? ADVISORY_BLOCK : 0;

  const onBrowseMode = () => {
    setMapMode('browse');
    setRoute(null);
    setLastRouteInfo(null);
    setSummaryCenter(null);
    setActiveRouteDestinationKey(null);
    setActiveRouteDestinationCoords(null);
    setPendingRouteDestinationKey(null);
  };

  const onNearestMode = async () => {
    const live = locationRef.current;
    const origin = live
      ? { latitude: live.coords.latitude, longitude: live.coords.longitude }
      : location
        ? { latitude: location.coords.latitude, longitude: location.coords.longitude }
      : cachedCoords
        ? { latitude: cachedCoords.lat, longitude: cachedCoords.lng }
        : null;
    if (!origin || !evacuationCenters.length) return;
    setMapMode('nearest');
    const nearest = await resolveNearestOpenCenter(origin, evacuationCenters);
    if (nearest) {
      await handleRouteTo(nearest.latitude, nearest.longitude, nearest, { openSummary: true });
    }
  };

  const onCenterToMe = useCallback(() => {
    const live = locationRef.current;
    const point = live
      ? { lat: live.coords.latitude, lng: live.coords.longitude, heading: live.coords.heading }
      : userLoc
        ? { lat: userLoc.lat, lng: userLoc.lng, heading: null }
        : null;
    if (!point) return;
    const js = `window.fromRN && window.fromRN(${JSON.stringify({
      type: 'centerMap',
      lat: point.lat,
      lon: point.lng,
      zoom: 16,
    })}); true;`;
    webViewRef.current?.injectJavaScript?.(js);
    emitUserToMap(
      point.lat,
      point.lng,
      Number.isFinite(point.heading as number) ? (point.heading as number) : null
    );
  }, [emitUserToMap, userLoc]);

  return (
    <View style={styles.container}>
      <WebView
        ref={webViewRef}
        source={{ html: mapHTML }}
        style={styles.map}
        originWhitelist={['*']}
        javaScriptEnabled
        domStorageEnabled
        onMessage={(e) => {
          try {
            const msg = JSON.parse(e.nativeEvent.data);
            if (msg.type === 'centerSelected' && msg.center) {
              const c = msg.center as EvacuationCenter;
              handleRouteTo(c.latitude, c.longitude, c, { openSummary: true });
            }
          } catch {
            /* ignore */
          }
        }}
      />

      {statusMessage && (
        <View
          style={[
            styles.statusBar,
            { bottom: Math.max(insets.bottom, 0) + BASE_FLOATING_BOTTOM },
          ]}
          pointerEvents="none"
        >
          <ActivityIndicator size="small" color="#0B3D5B" />
          <ThemedText style={styles.statusText}>{statusMessage}</ThemedText>
        </View>
      )}

      <View style={[styles.topRow, { top: insets.top + 8 }]}>
        <Pressable
          onPress={() => setLayerMenuOpen((v) => !v)}
          style={[styles.iconBtn, layerMenuOpen && styles.modeBtnActive]}
          accessibilityLabel="Toggle flood layer options"
        >
          <Ionicons name="layers" size={22} color={layerMenuOpen ? '#fff' : '#0B3D5B'} />
        </Pressable>
        <View style={[styles.headerCenter, styles.headerCenterCard]}>
          <ThemedText style={styles.headerTitle}>Evacuation Map</ThemedText>
          {error && <ThemedText style={styles.errorText}>{error}</ThemedText>}
        </View>
        <View style={styles.iconBtnGhost} />
      </View>

      <View style={[styles.modeBar, { top: insets.top + 8 }]}>
        <Pressable
          onPress={onBrowseMode}
          style={[styles.modeBtn, mapMode === 'browse' && styles.modeBtnActive]}
          accessibilityLabel="Show all evacuation centers"
        >
          <Ionicons name="home" size={24} color={mapMode === 'browse' ? '#fff' : '#0B3D5B'} />
        </Pressable>
        <Pressable
          onPress={onNearestMode}
          style={[styles.modeBtn, mapMode === 'nearest' && styles.modeBtnActive]}
          accessibilityLabel="Route to nearest evacuation center"
        >
          <Ionicons name="navigate" size={24} color={mapMode === 'nearest' ? '#fff' : '#0B3D5B'} />
        </Pressable>
      </View>

      <View
        style={[
          styles.centerFabWrap,
          {
            bottom:
              insets.bottom + BASE_FLOATING_BOTTOM + statusLift + advisoryLift,
          },
        ]}
      >
        <Pressable
          onPress={onCenterToMe}
          style={styles.modeBtn}
          accessibilityLabel="Center map to current location"
        >
          <Ionicons name="locate" size={22} color="#0B3D5B" />
        </Pressable>
      </View>

      {layerMenuOpen && (
        <Animated.View
          style={[
            styles.layerDropdownWrap,
            {
              top: insets.top + 58,
              opacity: layerDropdownAnim,
              transform: [
                {
                  translateY: layerDropdownAnim.interpolate({
                    inputRange: [0, 1],
                    outputRange: [-10, 0],
                  }),
                },
                {
                  scale: layerDropdownAnim.interpolate({
                    inputRange: [0, 1],
                    outputRange: [0.96, 1],
                  }),
                },
              ],
            },
          ]}
          pointerEvents={layerMenuOpen ? 'auto' : 'none'}
        >
          <View style={styles.layerDropdownPanel}>
            {FLOOD_SCENARIO_OPTIONS.map((opt) => (
              <Pressable
                key={opt.id}
                onPress={() => {
                  setFloodScenario(opt.id);
                  setLayerMenuOpen(false);
                }}
                style={[
                  styles.layerDropdownItem,
                  floodScenario === opt.id && styles.layerDropdownItemActive,
                ]}
              >
                <ThemedText
                  style={[
                    styles.layerDropdownText,
                    floodScenario === opt.id && styles.layerDropdownTextActive,
                  ]}
                >
                  {opt.label}
                </ThemedText>
              </Pressable>
            ))}
          </View>
        </Animated.View>
      )}

      {advisoryVisible && advisoryMessage && (
        <Animated.View
          style={[
            styles.safetyBanner,
            {
              bottom: insets.bottom + BASE_FLOATING_BOTTOM + statusLift,
              opacity: advisoryOpacity,
            },
          ]}
        >
          <View style={styles.safetyRow}>
            <Ionicons name="warning" size={14} color="#92400E" />
            <ThemedText style={styles.safetyText} numberOfLines={2}>
              {advisoryMessage}
            </ThemedText>
          </View>
        </Animated.View>
      )}

      <Modal
        visible={!!summaryCenter}
        transparent
        animationType="slide"
        onRequestClose={() => setSummaryCenter(null)}
      >
        <Pressable style={styles.sheetBackdrop} onPress={() => setSummaryCenter(null)}>
          <Pressable style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 16) }]} onPress={(e) => e.stopPropagation()}>
            <View style={styles.sheetGrab} />
            <ThemedText style={styles.sheetTitle}>{summaryCenter?.name}</ThemedText>
            {lastRouteInfo && (
              <ThemedText style={styles.sheetMeta}>
                About {lastRouteInfo.distance_km.toFixed(1)} km away · {formatDuration(lastRouteInfo.duration_minutes)}
              </ThemedText>
            )}
            <ScrollView style={styles.sheetScroll} showsVerticalScrollIndicator={false}>
              <View style={styles.metricRow}>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Route safety</ThemedText>
                  <ThemedText style={styles.metricValue}>
                    {lastRouteInfo ? `${Math.round(lastRouteInfo.safety_score * 100)}%` : 'N/A'}
                  </ThemedText>
                </View>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Flood-prone parts</ThemedText>
                  <ThemedText style={styles.metricValue}>
                    {lastRouteInfo?.flood_overlap_fraction != null
                      ? `${Math.round(lastRouteInfo.flood_overlap_fraction * 100)}%`
                      : 'Unknown'}
                  </ThemedText>
                </View>
              </View>
              <View style={styles.metricRow}>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Capacity</ThemedText>
                  <ThemedText style={styles.metricValue}>
                    {formatCapacity(
                      typeof summaryCenter?.capacity === 'number' ? summaryCenter.capacity : null
                    )}
                  </ThemedText>
                </View>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Center status</ThemedText>
                  <ThemedText style={styles.metricValue}>
                    {formatOpenStatus(summaryCenter?.is_open)}
                  </ThemedText>
                </View>
              </View>
              <View style={styles.metricRow}>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Type</ThemedText>
                  <ThemedText style={styles.metricValue}>{summaryCenter?.type || 'Not listed'}</ThemedText>
                </View>
                <View style={styles.metricChip}>
                  <ThemedText style={styles.metricLabel}>Elevation</ThemedText>
                  <ThemedText style={styles.metricValue}>
                    {lastRouteInfo?.destination_center_elevation != null
                      ? `${Math.round(lastRouteInfo.destination_center_elevation)} m`
                      : 'Not listed'}
                  </ThemedText>
                </View>
              </View>
              {lastRouteInfo?.warning ? (
                <ThemedText style={styles.sheetWarn}>{lastRouteInfo.warning}</ThemedText>
              ) : null}
              <ThemedText style={styles.sheetSection}>About this evacuation center</ThemedText>
              <ThemedText style={styles.sheetBody}>
                {summaryCenter?.summary ||
                  'No description is stored yet for this site. Follow local authorities during an emergency.'}
              </ThemedText>
            </ScrollView>
            <Pressable style={styles.sheetClose} onPress={() => setSummaryCenter(null)}>
              <ThemedText style={styles.sheetCloseText}>Close</ThemedText>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const OUTLINE = '#E6EEF5';
const SOFT_SHADOW = {
  shadowColor: '#000',
  shadowOffset: { width: 0, height: 4 },
  shadowOpacity: 0.06,
  shadowRadius: 8,
  elevation: 2,
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  map: { flex: 1 },
  statusBar: {
    position: 'absolute',
    left: 24,
    right: 24,
    minHeight: 44,
    borderRadius: 14,
    backgroundColor: 'rgba(255,255,255,0.94)',
    borderWidth: 1,
    borderColor: '#E6EEF5',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.06,
    shadowRadius: 10,
    elevation: 2,
    zIndex: 11,
  },
  statusText: {
    fontWeight: '700',
    color: '#0B3D5B',
    fontSize: 13,
  },
  topRow: {
    position: 'absolute',
    left: 12,
    right: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    zIndex: 10,
  },
  headerCenter: {
    alignItems: 'center',
    maxWidth: '55%',
  },
  headerCenterCard: {
    backgroundColor: 'rgba(255,255,255,0.92)',
    borderWidth: 1,
    borderColor: '#E6EEF5',
    borderRadius: 14,
    paddingVertical: 8,
    paddingHorizontal: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  iconBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: OUTLINE,
    alignItems: 'center',
    justifyContent: 'center',
    ...SOFT_SHADOW,
  },
  iconBtnGhost: {
    width: 42,
    height: 42,
  },
  headerTitle: {
    fontWeight: '800',
    fontSize: 16,
    color: '#0B3D5B',
  },
  errorText: {
    fontSize: 11,
    color: '#DC2626',
    marginTop: 2,
    textAlign: 'center',
  },
  modeBar: {
    position: 'absolute',
    right: 12,
    flexDirection: 'column',
    gap: 10,
    zIndex: 12,
  },
  centerFabWrap: {
    position: 'absolute',
    left: 24,
    zIndex: 12,
  },
  modeBtn: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: OUTLINE,
    alignItems: 'center',
    justifyContent: 'center',
    ...SOFT_SHADOW,
  },
  modeBtnActive: {
    backgroundColor: '#0B3D5B',
    borderColor: '#0B3D5B',
  },
  layerDropdownWrap: {
    position: 'absolute',
    left: 12,
    width: 90,
    zIndex: 14,
  },
  layerDropdownPanel: {
    borderRadius: 16,
    borderWidth: 1,
    borderColor: '#DDEAF7',
    backgroundColor: 'rgba(255,255,255,0.94)',
    paddingVertical: 6,
    paddingHorizontal: 6,
    gap: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 10,
    elevation: 3,
  },
  layerDropdownItem: {
    minHeight: 32,
    borderRadius: 12,
    backgroundColor: 'rgba(11,61,91,0.06)',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 8,
  },
  layerDropdownItemActive: {
    backgroundColor: 'rgba(11,90,162,0.95)',
    shadowColor: '#0B5AA2',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.28,
    shadowRadius: 6,
    elevation: 2,
  },
  layerDropdownText: {
    fontSize: 11,
    fontWeight: '800',
    color: '#0B3D5B',
    textAlign: 'center',
  },
  layerDropdownTextActive: {
    color: '#FFFFFF',
  },
  disclaimer: {
    position: 'absolute',
    left: 16,
    right: 16,
    fontSize: 10,
    color: '#64748B',
    textAlign: 'center',
    zIndex: 11,
  },
  safetyBanner: {
    position: 'absolute',
    left: 24,
    right: 24,
    backgroundColor: '#FEF3C7',
    borderWidth: 1,
    borderColor: '#F59E0B',
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    zIndex: 12,
  },
  safetyRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
  },
  safetyText: {
    flex: 1,
    color: '#78350F',
    fontSize: 12,
    lineHeight: 17,
    fontWeight: '700',
  },
  sheetBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(15,23,42,0.35)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingTop: 10,
    maxHeight: '72%',
  },
  sheetGrab: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#E2E8F0',
    marginBottom: 12,
  },
  sheetTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: '#0B3D5B',
    marginBottom: 8,
  },
  sheetMeta: {
    fontSize: 13,
    color: '#475569',
    marginBottom: 10,
  },
  metricRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 8,
  },
  metricChip: {
    flex: 1,
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#E2E8F0',
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
  },
  metricLabel: {
    fontSize: 11,
    color: '#64748B',
  },
  metricValue: {
    marginTop: 2,
    fontSize: 13,
    fontWeight: '800',
    color: '#0B3D5B',
  },
  sheetWarn: {
    fontSize: 12,
    color: '#B45309',
    marginTop: 4,
    marginBottom: 10,
    lineHeight: 18,
  },
  sheetScroll: {
    maxHeight: 320,
    marginBottom: 12,
  },
  sheetSection: {
    fontSize: 12,
    fontWeight: '800',
    color: '#64748B',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
    marginBottom: 6,
  },
  sheetBody: {
    fontSize: 15,
    lineHeight: 22,
    color: '#334155',
  },
  sheetClose: {
    backgroundColor: '#0B3D5B',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  sheetCloseText: {
    color: '#fff',
    fontWeight: '800',
    fontSize: 15,
  },
});
