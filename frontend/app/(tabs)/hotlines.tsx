// app/(tabs)/hotlines.tsx
import React, { useCallback, useEffect, useState } from 'react';
import {
  StyleSheet,
  View,
  Pressable,
  ScrollView,
  Modal,
  Image,
  ActivityIndicator,
  Alert,
  StatusBar as RNStatusBar,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import * as Location from 'expo-location';
import * as Linking from 'expo-linking';

import { ThemedText } from '@/components/themed-text';
import { ExternalLink } from '@/components/external-link';
import { apiService, SosCreateResponse } from '@/services/api';
import { readCachedUserLocation } from '@/services/offlineCache';
import { useColorScheme } from '@/hooks/use-color-scheme';
import { mkPalette } from '@/constants/sheltrTheme';

const TABBAR_HEIGHT = 64;
const TABBAR_BOTTOM_MARGIN = 24;
const BOTTOM_CLEARANCE = TABBAR_HEIGHT + TABBAR_BOTTOM_MARGIN + 48;

/* ---- helpers ---- */
/** Philippine emergency short codes — dial without a leading country code. */
const PH_SHORT_CODES = new Set(['911', '117', '136', '143', '161', '122', '1623']);

const telHref = (raw: string) => {
  const digits = (raw || '').replace(/[^\d+]/g, '');
  if (PH_SHORT_CODES.has(digits)) return `tel:${digits}`;
  if (/^0\d{7,}$/.test(digits)) return `tel:+63${digits.slice(1)}`;
  if (/^[2-9]\d{6,}$/.test(digits)) return `tel:+632${digits}`;
  if (/^09\d{8}$/.test(digits)) return `tel:+63${digits.slice(1)}`;
  if (/^\+?\d+$/.test(digits)) return `tel:${digits.startsWith('+') ? digits : `+${digits}`}`;
  return `tel:${digits}`;
};

/* ---- data ---- */
const NATIONAL_CARDS = [
  {
    title: 'Philippine National Police (PNP)',
    items: [
      { label: 'Complaints & emergencies', number: '(02) 722-0650' },
      { label: 'Anti-Cybercrime (CRU)', number: '(02) 8414-1560' },
    ],
  },
  {
    title: 'Metro Manila Development Authority',
    items: [
      { label: 'Hotline', number: '136' },
      { label: 'Traffic Info', number: '(02) 882-4156' },
    ],
  },
  {
    title: 'Bureau of Fire Protection NCR',
    items: [
      { label: '', number: '(02) 426-0219' },
      { label: '', number: '(02) 426-3812' },
      { label: '', number: '(02) 426-0246' },
    ],
  },
  {
    title: 'Medical Emergencies',
    items: [
      { label: 'Red Cross', number: '143' },
      { label: 'Emergency', number: '911' },
    ],
  },
  {
    title: 'Department of the Interior and Local Government',
    items: [{ label: 'Citizens\' assistance desk', number: '(02) 8925-0343' }],
  },
];

const LOCAL_GROUPS = [
  {
    city: 'Marikina',
    items: [
      { label: 'Rescue 161', number: '161' },
      { label: 'City Hall', number: '(02) 8646-0462' },
    ],
  },
  {
    city: 'Manila',
    items: [
      { label: 'PNP', number: '117' },
      { label: 'Fire Dept.', number: '(02) 8527-1405' },
    ],
  },
  {
    city: 'Quezon City',
    items: [
      { label: 'Helpline (24/7)', number: '122' },
      { label: 'Emergency Ops', number: '0977-031-2892' },
      { label: 'Medical & Rescue', number: '0947-884-7498' },
    ],
  },
  {
    city: 'Pasig',
    items: [
      { label: 'DRRMO', number: '8643-0000' },
      { label: 'Fire', number: '8641-2815' },
      { label: 'Police', number: '8477-7953' },
      { label: 'General Hospital', number: '8643-3333' },
    ],
  },
  {
    city: 'Taguig',
    items: [
      { label: 'Police Emergency', number: '1623' },
      { label: 'Fire', number: '(02) 542-3695' },
    ],
  },
];

type HotlineItem = { label: string; number: string };

/* ---- screen ---- */
export default function HotlinesScreen() {
  const scheme = useColorScheme();
  const isDark = scheme === 'dark';
  const p = mkPalette(isDark);

  const [tab, setTab] = useState<'national' | 'local'>('national');
  const [sosBusy, setSosBusy] = useState(false);
  const [sosModalVisible, setSosModalVisible] = useState(false);
  const [sosSession, setSosSession] = useState<SosCreateResponse | null>(null);
  const [sosHotlineLabel, setSosHotlineLabel] = useState('');

  const createSos = useCallback(async (item: HotlineItem, ownerLabel: string) => {
    setSosBusy(true);
    try {
      const cached = await readCachedUserLocation();
      let lat = cached?.latitude ?? null;
      let lng = cached?.longitude ?? null;
      try {
        const perm = await Location.requestForegroundPermissionsAsync();
        if (perm.status === 'granted') {
          const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
          lat = loc.coords.latitude;
          lng = loc.coords.longitude;
        }
      } catch { /* keep cached fallback */ }
      if (typeof lat !== 'number' || typeof lng !== 'number') {
        Alert.alert('Location needed', 'Open the Map tab first so Sheltr can cache your location.');
        return;
      }
      const payload = await apiService.createSosSession({
        hotline_name: ownerLabel,
        hotline_number: item.number,
        latitude: lat,
        longitude: lng,
      });
      setSosHotlineLabel(`${ownerLabel} · ${item.number}`);
      setSosSession(payload);
      setSosModalVisible(true);
    } catch (err) {
      Alert.alert('SOS QR unavailable', err instanceof Error ? err.message : 'Could not create SOS QR.');
    } finally {
      setSosBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!sosModalVisible || !sosSession?.session_id) return;
    let active = true;
    const beat = async () => {
      try {
        const cached = await readCachedUserLocation();
        let lat = cached?.latitude ?? null;
        let lng = cached?.longitude ?? null;
        try {
          const perm = await Location.requestForegroundPermissionsAsync();
          if (perm.status === 'granted') {
            const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
            lat = loc.coords.latitude;
            lng = loc.coords.longitude;
          }
        } catch { /* fallback */ }
        if (!active || typeof lat !== 'number' || typeof lng !== 'number') return;
        await apiService.heartbeatSosSession({ session_id: sosSession.session_id, latitude: lat, longitude: lng });
      } catch (err) {
        console.warn('SOS heartbeat failed:', err);
      }
    };
    beat();
    const id = setInterval(beat, 5000);
    return () => { active = false; clearInterval(id); };
  }, [sosModalVisible, sosSession?.session_id]);

  const topPad = Platform.OS === 'android' ? (RNStatusBar.currentHeight ?? 0) + 8 : 8;

  return (
    <LinearGradient colors={[p.bg1, p.bg2]} style={styles.root}>
      <RNStatusBar
        barStyle={isDark ? 'light-content' : 'dark-content'}
        backgroundColor="transparent"
        translucent
      />
      <SafeAreaView style={{ flex: 1 }} edges={['left', 'right', 'bottom']}>
        <ScrollView
          style={{ flex: 1 }}
          showsVerticalScrollIndicator={false}
          contentContainerStyle={{ paddingBottom: BOTTOM_CLEARANCE, paddingTop: topPad }}
        >
          <View style={styles.headerWrap}>
            <View>
              <ThemedText style={[styles.headerKicker, { color: p.muted }]}>Emergency</ThemedText>
              <ThemedText style={[styles.headerTitle, { color: p.text }]}>Hotlines</ThemedText>
            </View>
          </View>

          <Pressable
            onPress={() => Linking.openURL(telHref('911'))}
            accessibilityRole="button"
            accessibilityLabel="Call nationwide emergency number 911"
            style={[styles.hero911, { backgroundColor: p.evacCardBg }]}
          >
            <ThemedText style={[styles.hero911Kicker, { color: p.accent }]}>Nationwide emergency</ThemedText>
            <ThemedText style={[styles.hero911Digits, { color: '#FFFFFF' }]}>911</ThemedText>
            <ThemedText style={[styles.hero911Body, { color: 'rgba(255,255,255,0.82)' }]}>
              Police, fire, ambulance, and EMS — free from any phone.
            </ThemedText>
            <View style={[styles.hero911Cta, { backgroundColor: p.accent }]}>
              <ThemedText style={[styles.hero911CtaText, { color: '#FFFFFF' }]}>Tap to call now</ThemedText>
            </View>
          </Pressable>

          <View style={styles.tabBar}>
            {(['national', 'local'] as const).map((t) => (
              <Pressable key={t} onPress={() => setTab(t)} style={styles.tabHit}>
                <ThemedText
                  style={[
                    styles.tabBarLabel,
                    { color: tab === t ? p.text : p.muted },
                    tab === t && { fontWeight: '800' },
                  ]}
                >
                  {t === 'national' ? 'National' : 'Local'}
                </ThemedText>
                <View style={[styles.tabUnderline, { backgroundColor: tab === t ? p.accent : 'transparent' }]} />
              </Pressable>
            ))}
          </View>

          <View style={styles.sectionList}>
            {(tab === 'national' ? NATIONAL_CARDS : LOCAL_GROUPS).map((group, gi) => (
              <View key={'title' in group ? group.title : group.city} style={gi > 0 ? styles.sectionSpacer : undefined}>
                <ThemedText style={[styles.sectionHeading, { color: p.muted }]}>
                  {'title' in group ? group.title : group.city}
                </ThemedText>
                {group.items.map((item, i) => (
                  <View
                    key={i}
                    style={[
                      styles.hotlineRow,
                      i > 0 && { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: p.cardBorder },
                    ]}
                  >
                    <View style={styles.hotlineMain}>
                      {item.label ? (
                        <ThemedText style={[styles.hotlineLabel, { color: p.muted }]}>{item.label}</ThemedText>
                      ) : null}
                      <ExternalLink href={telHref(item.number) as any}>
                        <ThemedText style={[styles.hotlineNumber, { color: p.text }]}>{item.number}</ThemedText>
                      </ExternalLink>
                    </View>
                    <View style={styles.hotlineActions}>
                      <Pressable
                        style={[styles.pillGhost, { backgroundColor: p.chip, borderColor: p.chipBorder }]}
                        onPress={() => Linking.openURL(telHref(item.number))}
                      >
                        <ThemedText style={[styles.pillGhostText, { color: p.text }]}>Call</ThemedText>
                      </Pressable>
                      <Pressable
                        style={[styles.pillAccent, { backgroundColor: p.accent }]}
                        onPress={() => createSos(item, 'title' in group ? group.title : group.city)}
                        disabled={sosBusy}
                      >
                        <ThemedText style={[styles.pillAccentText, { color: '#FFFFFF' }]}>SOS QR</ThemedText>
                      </Pressable>
                    </View>
                  </View>
                ))}
              </View>
            ))}
          </View>
        </ScrollView>
      </SafeAreaView>

      {/* SOS modal */}
      <Modal visible={sosModalVisible} transparent animationType="fade" onRequestClose={() => setSosModalVisible(false)}>
        <View style={styles.modalBackdrop}>
          <View style={[styles.sosCard, { backgroundColor: p.card, borderColor: p.cardBorder }]}>
            <ThemedText style={[styles.sosTitle, { color: p.text }]}>SOS QR Ready</ThemedText>
            <ThemedText style={[styles.sosSub, { color: p.muted }]}>{sosHotlineLabel}</ThemedText>
            {sosBusy ? (
              <View style={{ paddingVertical: 32 }}>
                <ActivityIndicator color={p.accent} />
              </View>
            ) : sosSession ? (
              <>
                <Image
                  source={{ uri: `https://api.qrserver.com/v1/create-qr-code/?size=280x280&data=${encodeURIComponent(sosSession.qr_payload)}` }}
                  style={[styles.sosQr, { backgroundColor: '#fff' }]}
                />
                <ThemedText style={[styles.sosCode, { color: p.muted }]}>Session · {sosSession.session_id.slice(0, 8)}…</ThemedText>
                <ThemedText style={[styles.sosHint, { color: p.muted }]}>
                  Hotline can scan this QR to open the Sheltr rescue feed for live tracking.
                </ThemedText>
                <View style={styles.sosActions}>
                  <Pressable
                    style={[styles.sosPrimaryBtn, { backgroundColor: p.accent }]}
                    onPress={() => Linking.openURL(sosSession.rescue_url)}
                  >
                    <ThemedText style={[styles.sosPrimaryBtnText, { color: '#FFFFFF' }]}>Open rescue feed</ThemedText>
                  </Pressable>
                  <Pressable
                    style={[styles.sosSecondaryBtn, { backgroundColor: p.chip, borderColor: p.cardBorder }]}
                    onPress={() => setSosModalVisible(false)}
                  >
                    <ThemedText style={[styles.sosSecondaryBtnText, { color: p.text }]}>Close</ThemedText>
                  </Pressable>
                </View>
              </>
            ) : null}
          </View>
        </View>
      </Modal>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  headerWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
    marginBottom: 4,
  },
  headerKicker: {
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  headerTitle: {
    fontSize: 26,
    fontWeight: '900',
    lineHeight: 30,
  },
  hero911: {
    marginHorizontal: 16,
    marginBottom: 22,
    borderRadius: 18,
    paddingHorizontal: 22,
    paddingVertical: 22,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.12,
    shadowRadius: 14,
    elevation: 5,
  },
  hero911Kicker: {
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 1.2,
    marginBottom: 10,
  },
  hero911Digits: {
    fontSize: 54,
    fontWeight: '900',
    fontVariant: ['tabular-nums'],
    letterSpacing: -2,
    lineHeight: 56,
    marginBottom: 10,
  },
  hero911Body: {
    fontSize: 13,
    lineHeight: 19,
    fontWeight: '500',
    marginBottom: 18,
  },
  hero911Cta: {
    borderRadius: 999,
    paddingVertical: 13,
    alignItems: 'center',
    alignSelf: 'stretch',
  },
  hero911CtaText: {
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 0.3,
  },
  tabBar: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginBottom: 16,
    gap: 8,
  },
  tabHit: {
    flex: 1,
    alignItems: 'center',
    paddingTop: 6,
    paddingBottom: 2,
  },
  tabBarLabel: {
    fontSize: 14,
    fontWeight: '600',
  },
  tabUnderline: {
    marginTop: 10,
    height: 3,
    width: '100%',
    borderRadius: 2,
  },
  sectionList: {
    paddingHorizontal: 16,
    paddingBottom: 12,
  },
  sectionSpacer: {
    marginTop: 26,
  },
  sectionHeading: {
    fontSize: 11,
    fontWeight: '800',
    textTransform: 'uppercase',
    letterSpacing: 0.9,
    marginBottom: 12,
  },
  hotlineRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    gap: 12,
  },
  hotlineMain: {
    flex: 1,
    minWidth: 0,
  },
  hotlineLabel: { fontSize: 11, fontWeight: '600', marginBottom: 3 },
  hotlineNumber: { fontSize: 17, fontWeight: '700', fontVariant: ['tabular-nums'] },
  hotlineActions: { flexDirection: 'row', gap: 8, flexShrink: 0 },
  pillGhost: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 9,
    justifyContent: 'center',
  },
  pillGhostText: { fontSize: 12, fontWeight: '700' },
  pillAccent: {
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 9,
    justifyContent: 'center',
  },
  pillAccentText: { fontSize: 12, fontWeight: '700' },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(15,23,42,0.55)',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
  },
  sosCard: {
    width: '100%',
    maxWidth: 400,
    borderRadius: 18,
    borderWidth: 1,
    padding: 20,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.15,
    shadowRadius: 24,
    elevation: 12,
  },
  sosTitle: { fontSize: 20, fontWeight: '900' },
  sosSub: { fontSize: 12, marginTop: 3, textAlign: 'center' },
  sosQr: { width: 220, height: 220, borderRadius: 12, marginTop: 14 },
  sosCode: { fontSize: 11, fontWeight: '700', marginTop: 8 },
  sosHint: { fontSize: 12, lineHeight: 18, textAlign: 'center', marginTop: 6, paddingHorizontal: 10 },
  sosActions: { width: '100%', gap: 8, marginTop: 14 },
  sosPrimaryBtn: {
    borderRadius: 999,
    paddingVertical: 13,
    alignItems: 'center',
  },
  sosPrimaryBtnText: { fontSize: 15, fontWeight: '800' },
  sosSecondaryBtn: {
    borderRadius: 999,
    borderWidth: 1,
    paddingVertical: 12,
    alignItems: 'center',
  },
  sosSecondaryBtnText: { fontSize: 14, fontWeight: '700' },
});
