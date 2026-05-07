// app/(tabs)/index.tsx
import React, { useEffect, useRef, useState } from 'react';
import {
  StyleSheet,
  View,
  Image,
  Pressable,
  ActivityIndicator,
  StatusBar as RNStatusBar,
  Platform,
  Dimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import * as Location from 'expo-location';
import * as SystemUI from 'expo-system-ui';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { IconSymbol } from '@/components/ui/icon-symbol';
import { apiService } from '@/services/api';
import { notificationService, Notification } from '@/services/notifications';
import { readCachedUserLocation, writeCachedUserLocation } from '@/services/offlineCache';

/* ----------------- Map HTML ----------------- */
function makeMapHTML(user: { lat: number; lon: number } | null) {
  const lat = user?.lat ?? 14.5995;
  const lon = user?.lon ?? 120.9842;
  return `<!DOCTYPE html><html><head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>
    html,body,#map{height:100%;margin:0;background:#ffffff}
    #map{border-radius:16px;overflow:hidden}
    .user-marker{background:#3B82F6;width:16px;height:16px;border-radius:50%;border:3px solid #fff;box-shadow:0 0 0 4px rgba(59,130,246,.3)}
  </style>
</head><body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    var map=L.map('map',{zoomControl:true,attributionControl:false}).setView([${lat},${lon}],15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
    var marker=L.marker([${lat},${lon}],{icon:L.divIcon({className:'user-marker',iconSize:[16,16],iconAnchor:[8,8]})}).addTo(map);

    window.fromRN = function(data){
      if(data && data.type==='centerMap' && data.lat && data.lon){
        map.setView([data.lat, data.lon], 14);
      }
    };
  </script>
</body></html>`;
}

/* ----------------- Constants ----------------- */
const { height: SCREEN_HEIGHT } = Dimensions.get('window');
const COMPACT = SCREEN_HEIGHT < 700;

const BORDER = '#EAF0F6';
const HEADER_BORDER = '#DDEAF7';
const ICON_COLOR = '#0B3D5B';
const TABBAR_HEIGHT = 64;
const TABBAR_BOTTOM_MARGIN = 24;
const EXTRA_CUSHION = 16;
const BOTTOM_CLEARANCE = TABBAR_HEIGHT + TABBAR_BOTTOM_MARGIN + EXTRA_CUSHION;

/* ----------------- Header ----------------- */
function HeaderCard({ notifCount }: { notifCount: number }) {
  const router = useRouter();

  return (
    <ThemedView
      style={[
        styles.headerCard,
        COMPACT && { marginTop: 6, paddingVertical: 10, paddingHorizontal: 12 },
      ]}
    >
      <View style={styles.headerRow}>
        {/* Logo */}
        <Image
          source={require('../../assets/images/Sheltr.png')}
          style={[styles.logo, COMPACT && { height: 28 }]}
          resizeMode="contain"
        />

        {/* Notifications (routes to /notifications) */}
        <Pressable
          onPress={() => router.push('/notifications')}
          style={styles.iconBtn}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel="Open notifications"
        >
          {Platform.OS === 'ios' ? (
            <IconSymbol name="bell.fill" size={18} color="#0B3D5B" />
          ) : (
            <Ionicons name="notifications" size={20} color="#0B3D5B" />
          )}
          <View style={styles.badge}>
            <ThemedText style={styles.badgeText}>{notifCount}</ThemedText>
          </View>
        </Pressable>
      </View>
    </ThemedView>
  );
}

/* ----------------- Weather Card ----------------- */
type WeatherCardProps = {
  dateTime: string;
  temp: number | string;
  humidity: string | number;
  precipitation: string | number;
  loading: boolean;
  floodRiskLevel?: 'low' | 'moderate' | 'high' | null;
  backendConnected?: boolean;
};
function WeatherCard({ dateTime, temp, humidity, precipitation, loading, floodRiskLevel, backendConnected }: WeatherCardProps) {
  return (
    <ThemedView
      style={[styles.card, styles.weatherCard, COMPACT && styles.weatherCardCompact]}
    >
      <View style={styles.weatherHeader}>
        <ThemedText style={[styles.sectionLabel, COMPACT && { marginBottom: 8 }]}>
          Weather Update
        </ThemedText>
        {backendConnected && floodRiskLevel && (
          <View
            style={[
              styles.riskIndicator,
              floodRiskLevel === 'high'
                ? styles.riskHigh
                : floodRiskLevel === 'moderate'
                  ? styles.riskModerate
                  : styles.riskLow,
            ]}
          >
            <Ionicons 
              name={floodRiskLevel === 'high' ? 'warning' : floodRiskLevel === 'moderate' ? 'alert-circle' : 'checkmark-circle'} 
              size={14} 
              color="#fff" 
            />
            <ThemedText style={styles.riskText}>
              {floodRiskLevel === 'high' ? 'High Risk' : floodRiskLevel === 'moderate' ? 'Moderate Risk' : 'Low Risk'}
            </ThemedText>
          </View>
        )}
      </View>

      <View style={[styles.metricRow, { marginBottom: COMPACT ? 6 : 10 }]}>
        <Ionicons name="calendar" size={18} color={ICON_COLOR} />
        <ThemedText style={styles.metricText}>{dateTime}</ThemedText>
      </View>

      <View
        style={[
          styles.tempRow,
          COMPACT && { marginVertical: 4, minHeight: 46 },
        ]}
      >
        <Ionicons
          name="thermometer"
          size={26}
          color={ICON_COLOR}
          style={[styles.tempIcon, COMPACT && { marginBottom: 4 }]}
        />
        <ThemedText
          style={[styles.tempText, COMPACT && { fontSize: 38, lineHeight: 42 }]}
        >
          {typeof temp === 'number' ? Math.round(temp) : temp}°C
        </ThemedText>
      </View>

      <View style={styles.row}>
        <View
          style={[
            styles.smallCard,
            { marginRight: 12 },
            COMPACT && { padding: 9 },
          ]}
        >
          <Ionicons name="water" size={18} color={ICON_COLOR} />
          <View style={{ marginLeft: 8 }}>
            <ThemedText style={styles.miniLabel}>Humidity</ThemedText>
            <ThemedText style={styles.miniValue}>{humidity}</ThemedText>
          </View>
        </View>

        <View style={[styles.smallCard, COMPACT && { padding: 9 }]}>
          <Ionicons name="rainy" size={18} color={ICON_COLOR} />
          <View style={{ marginLeft: 8 }}>
            <ThemedText style={styles.miniLabel}>Rain</ThemedText>
            <ThemedText style={styles.miniValue}>{precipitation}</ThemedText>
          </View>
        </View>
      </View>

      {loading && (
        <View style={{ marginTop: 8, alignItems: 'center' }}>
          <ActivityIndicator />
        </View>
      )}
    </ThemedView>
  );
}

/* ----------------- Main Screen ----------------- */
export default function HomeScreen() {
  const webRef = useRef<any>(null);
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [wx, setWx] = useState<{ temperature: number | null; humidity: number | null; precipitation: number | null }>({
    temperature: null,
    humidity: null,
    precipitation: null,
  });
  const [loading, setLoading] = useState(true);
  const [backendConnected, setBackendConnected] = useState(false);
  const [floodRiskLevel, setFloodRiskLevel] = useState<'low' | 'moderate' | 'high' | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  useEffect(() => {
    SystemUI.setBackgroundColorAsync('#ffffff');
  }, []);

  useEffect(() => {
    (async () => {
      const persisted = await readCachedUserLocation();
      if (persisted) {
        setCoords({ lat: persisted.latitude, lon: persisted.longitude });
      }

      const { status } = await Location.requestForegroundPermissionsAsync();
      let activePos =
        persisted != null
          ? { lat: persisted.latitude, lon: persisted.longitude }
          : null;

      if (status === 'granted') {
        try {
          const loc = await Location.getCurrentPositionAsync({
            accuracy: Location.Accuracy.Balanced,
          });
          activePos = { lat: loc.coords.latitude, lon: loc.coords.longitude };
          setCoords(activePos);
          await writeCachedUserLocation(activePos.lat, activePos.lon);
        } catch (err) {
          console.warn('Could not read current location, using cached location if available:', err);
        }
      }

      const pos = activePos;

      const notificationData = pos
        ? await notificationService.getNotifications({
            latitude: pos.lat,
            longitude: pos.lon,
          })
        : await notificationService.getNotifications();
      setNotifications(notificationData);

      const healthCheck = await apiService.checkHealth();
      const backendReachable = healthCheck.status !== 'error' && healthCheck.status !== 'unreachable';
      setBackendConnected(backendReachable);

      if (pos) {
        try {
          const weatherData = await apiService.getWeatherData(pos.lat, pos.lon);
          setWx({
            temperature: weatherData.temperature,
            humidity: weatherData.humidity,
            precipitation: weatherData.precipitation,
          });

          const floodRisk = await apiService.getFloodRisk(pos.lat, pos.lon);
          if (floodRisk.length > 0) {
            const avgRisk = floodRisk.reduce((sum, r) => sum + r.pred_prob_unsafe, 0) / floodRisk.length;

            // Weather-card badge should reflect current weather conditions,
            // not just static area susceptibility.
            const rainNowMm = Math.max(0, Number(weatherData.precipitation ?? 0));
            const rainProbPct = Math.max(
              0,
              Number(
                weatherData.precipitation_probability ??
                  weatherData.hourly?.precipitation_probability?.[0] ??
                  0
              )
            );

            const rainSignal =
              Math.max(
                rainNowMm >= 7 ? 1 : rainNowMm >= 2 ? 0.6 : rainNowMm >= 0.2 ? 0.3 : 0,
                Math.min(1, rainProbPct / 100)
              );

            let weatherAdjustedRisk = avgRisk;
            if (rainSignal < 0.15) {
              // Dry/very low rain likelihood: keep badge at low.
              weatherAdjustedRisk = Math.min(weatherAdjustedRisk, 0.39);
            } else if (rainSignal < 0.4) {
              // Light/uncertain rain: cap at moderate.
              weatherAdjustedRisk = Math.min(weatherAdjustedRisk, 0.69);
            }

            if (weatherAdjustedRisk > 0.7) setFloodRiskLevel('high');
            else if (weatherAdjustedRisk > 0.4) setFloodRiskLevel('moderate');
            else setFloodRiskLevel('low');
          } else {
            setFloodRiskLevel(null);
          }
        } catch (error) {
          console.error('Error fetching Sheltr backend data:', error);
          setWx({
            temperature: null,
            humidity: null,
            precipitation: null,
          });
          setFloodRiskLevel(null);
        }
      } else {
        setWx({
          temperature: null,
          humidity: null,
          precipitation: null,
        });
        setFloodRiskLevel(null);
      }

      setLoading(false);
    })();
  }, []);

  const temp = wx.temperature ?? '--';
  const humidity = wx.humidity != null ? `${wx.humidity}%` : '--';
  const precipitation = wx.precipitation != null ? `${wx.precipitation} mm` : '0 mm';
  const dateTime = new Date().toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  const topSpacer =
    Platform.OS === 'android' ? (RNStatusBar.currentHeight ?? 0) + 4 : 6;

  const handleCenterToMe = () => {
    if (coords && webRef.current) {
      const js = `window.fromRN && window.fromRN(${JSON.stringify({
        type: 'centerMap',
        lat: coords.lat,
        lon: coords.lon,
      })}); true;`;
      webRef.current?.injectJavaScript?.(js);
    }
  };

  return (
    <SafeAreaView style={styles.root} edges={['left', 'right', 'bottom']}>
      <RNStatusBar barStyle="dark-content" backgroundColor="#ffffff" />
      <View
        style={[
          styles.content,
          {
            paddingTop: topSpacer,
            paddingBottom: BOTTOM_CLEARANCE,
          },
        ]}
      >
        <HeaderCard notifCount={notifications.filter(n => !n.read).length} />

        <WeatherCard
          dateTime={dateTime}
          temp={temp}
          humidity={humidity}
          precipitation={precipitation}
          loading={loading}
          floodRiskLevel={floodRiskLevel}
          backendConnected={backendConnected}
        />

        <ThemedView
          style={[
            styles.card,
            styles.mapCard,
            { marginBottom: 18, paddingVertical: COMPACT ? 14 : 16 },
          ]}
        >
          <ThemedText
            style={[styles.sectionLabel, COMPACT && { marginBottom: 8 }]}
          >
            Live User Map
          </ThemedText>

          <View style={styles.mapWrap}>
            <WebView
              ref={webRef}
              source={{ html: makeMapHTML(coords) }}
              style={styles.map}
              originWhitelist={['*']}
              javaScriptEnabled
              domStorageEnabled
              setSupportMultipleWindows={false}
              scrollEnabled={false}
            />
            <Pressable
              onPress={handleCenterToMe}
              style={styles.centerFab}
              accessibilityRole="button"
              accessibilityLabel="Center map to my location"
            >
              <Ionicons name="locate" size={18} color="#0B3D5B" />
            </Pressable>
          </View>
        </ThemedView>

        <View style={{ height: 6 }} />
      </View>
    </SafeAreaView>
  );
}

/* ----------------- Styles ----------------- */
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#ffffff' },
  content: { flex: 1, backgroundColor: '#ffffff' },

  headerCard: {
    marginHorizontal: 16,
    marginTop: 0,
    backgroundColor: '#F8FBFF',
    borderRadius: 16,
    paddingVertical: 14,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: HEADER_BORDER,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  logo: { width: 120, height: 32 },

  iconBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: '#FFF',
    borderWidth: 1,
    borderColor: HEADER_BORDER,
    alignItems: 'center',
    justifyContent: 'center',
  },
  badge: {
    position: 'absolute',
    top: -2,
    right: -2,
    minWidth: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: '#FEE2E2',
    borderWidth: 1,
    borderColor: '#FCA5A5',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 3,
  },
  badgeText: {
    fontSize: 10,
    lineHeight: 10,
    color: '#9F1239',
    fontWeight: '700',
    textAlign: 'center',
    includeFontPadding: false,
    textAlignVertical: 'center',
  },

  card: {
    marginHorizontal: 16,
    marginTop: 10,
    backgroundColor: '#ffffff',
    borderRadius: 16,
    paddingVertical: 20,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: BORDER,
  },
  weatherCard: {
    marginTop: 8,
    paddingVertical: 14,
  },
  weatherCardCompact: {
    marginTop: 8,
    paddingVertical: 12,
  },
  mapCard: {
    flex: 1,
    marginTop: 8,
    minHeight: COMPACT ? 220 : 260,
  },
  sectionLabel: {
    fontSize: 12,
    letterSpacing: 0.6,
    color: '#9AA4AE',
    fontWeight: '800',
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  metricRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  metricText: { fontWeight: '700', color: '#0B3D5B' },

  tempRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'flex-end',
    marginVertical: 6,
  },
  tempIcon: { marginRight: 6, marginBottom: 6 },
  tempText: {
    fontSize: 44,
    fontWeight: '900',
    color: '#0B3D5B',
    lineHeight: 48,
  },
  row: { flexDirection: 'row' },
  smallCard: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#F8FAFC',
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: BORDER,
  },
  miniLabel: { fontSize: 12, color: '#6B7280' },
  miniValue: { fontSize: 16, fontWeight: '800', color: '#0B3D5B' },

  mapWrap: {
    flex: 1,
    minHeight: COMPACT ? 160 : 190,
    marginTop: 5,
    borderRadius: 16,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: BORDER,
    position: 'relative',
  },
  map: { flex: 1, width: '100%', backgroundColor: '#ffffff' },

  centerFab: {
    position: 'absolute',
    right: 12,
    bottom: 12,
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#DDEAF7',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.1,
    shadowRadius: 6,
    elevation: 3,
  },
  centerButtonText: { color: '#ffffff', fontWeight: '700', fontSize: 14 },

  // Flood risk indicator
  weatherHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  riskIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    gap: 4,
  },
  riskLow: {
    backgroundColor: '#10B981',
  },
  riskModerate: {
    backgroundColor: '#F59E0B',
  },
  riskHigh: {
    backgroundColor: '#EF4444',
  },
  riskText: {
    color: '#ffffff',
    fontSize: 10,
    fontWeight: '700',
  },
});
