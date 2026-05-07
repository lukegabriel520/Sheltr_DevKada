// app/(tabs)/hotlines.jsx
import React, { useState } from 'react';
import { StyleSheet, View, Pressable, ScrollView } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { ExternalLink } from '@/components/external-link';
import { IconSymbol } from '@/components/ui/icon-symbol';
import { Fonts } from '@/constants/theme';

/* ------------------------------ Bottom clearance ------------------------------ */
const TABBAR_HEIGHT = 50;
const TABBAR_BOTTOM_MARGIN = 20;
const EXTRA_CUSHION = 20;
const BOTTOM_CLEARANCE = TABBAR_HEIGHT + TABBAR_BOTTOM_MARGIN + EXTRA_CUSHION;

/* -------------------------------- Helpers -------------------------------- */
const telHref = (raw: string) => {
  const digits = (raw || '').replace(/[^\d+]/g, '');
  if (/^0\d{7,}$/.test(digits)) return `tel:+63${digits.slice(1)}`;
  if (/^[2-9]\d{6,}$/.test(digits)) return `tel:+632${digits}`;
  if (/^09\d{8}$/.test(digits)) return `tel:+63${digits.slice(1)}`;
  if (/^\+?\d+$/.test(digits)) return `tel:${digits.startsWith('+') ? digits : `+${digits}`}`;
  return `tel:${digits}`;
};

type PillProps = { label: string; active: boolean; onPress: () => void };
function Pill({ label, active, onPress }: PillProps) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.pill, active && styles.pillActive]}
      android_ripple={{ color: 'rgba(0,0,0,0.08)', borderless: true }}
      accessibilityRole="button"
      accessibilityState={{ selected: !!active }}
    >
      <ThemedText style={[styles.pillText, active && styles.pillTextActive]}>
        {label}
      </ThemedText>
    </Pressable>
  );
}

type PhoneLinkProps = { number: string; label: string };
function PhoneLink({ number, label }: PhoneLinkProps) {
  return (
    <View style={styles.phoneRow}>
      <ThemedText style={styles.phoneLabel}>{label || ''}</ThemedText>
  <ExternalLink href={telHref(number) as any}>
        <ThemedText style={styles.phoneNumber}>{number}</ThemedText>
      </ExternalLink>
    </View>
  );
}

/* --------------------------------- Data ---------------------------------- */
const NATIONAL_CARDS = [
  {
    title: 'Philippine National Police (PNP)',
    items: [
      { label: 'Hotline:', number: '(02) 722-0650' },
      { label: 'Anti-Cybercrime Group:', number: '(02) 722-0413' },
    ],
  },
  {
    title: 'Metro Manila Development Authority (MMDA)',
    items: [
      { label: 'Hotline:', number: '136' },
      { label: 'Traffic and Travel Information:', number: '(02) 882-4156 to 77 (local 333)' },
    ],
  },
  {
    title: 'Fire Department (Bureau of Fire Protection NCR)',
    items: [
      { label: '', number: '(02) 426-0219' },
      { label: '', number: '(02) 426-3812' },
      { label: '', number: '(02) 426-0246' },
    ],
  },
  {
    title: 'Medical Emergencies',
    items: [
      { label: 'Red Cross:', number: '143' },
      { label: 'Emergency:', number: '911' },
    ],
  },
];

const LOCAL_GROUPS = [
  {
    city: 'Marikina',
    items: [
      { label: 'Marikina City Rescue 161:', number: '161' },
      { label: 'DILG Marikina Hotline:', number: '8925' },
      { label: 'Marikina City Hall:', number: '8646-0306, 8646-0462' },
    ],
  },
  {
    city: 'Manila',
    items: [
      { label: 'Philippine National Police:', number: '117' },
      { label: 'Manila Fire Department:', number: '(02) 8527-1405' },
    ],
  },
  {
    city: 'Quezon City',
    items: [
      { label: 'QC Helpline 122 (24/7):', number: '122' },
      { label: 'Emergency Ops Center:', number: '0977-031-2892 (Globe), 0947-885-9929 (Smart)' },
      { label: 'Medical & Rescue:', number: '0947-884-7498 (Smart)' },
    ],
  },
  {
    city: 'Pasig',
    items: [
      { label: 'Pasig City DRRMO Emergency:', number: '8643-0000' },
      { label: 'Fire:', number: '8641-2815' },
      { label: 'Police:', number: '8477-7953' },
      { label: "Children's Hospital:", number: '8643-2222' },
      { label: 'General Hospital:', number: '8643-3333, 8642-7379' },
    ],
  },
  {
    city: 'Taguig',
    items: [
      { label: 'Police Emergency:', number: '1623 or 117' },
      { label: 'Fire:', number: '(02) 542-3695 / 8642-9882' },
    ],
  },
];

/* -------------------------------- Screen --------------------------------- */
export default function HotlinesScreen() {
  const [tab, setTab] = useState('national');

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.scrollView}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingBottom: BOTTOM_CLEARANCE }}
      >
        <View style={styles.content}>
          {/* Header */}
          <View style={styles.titleWrap}>
            <ThemedText style={styles.title}>Emergency Hotlines</ThemedText>
          </View>

          {/* Tabs */}
          <View style={styles.tabsContainer}>
            <Pill label="National" active={tab === 'national'} onPress={() => setTab('national')} />
            <Pill label="Local" active={tab === 'local'} onPress={() => setTab('local')} />
          </View>

          {/* 911 Banner */}
          <View style={styles.banner}>
            <View style={styles.bannerLeft}>
              <ThemedText style={styles.bannerTitle}>National Hotline</ThemedText>
              <ThemedText style={styles.bannerSubtitle}>For all emergencies:</ThemedText>
            </View>
            <View style={styles.bannerRight}>
              <IconSymbol name="phone.fill" size={18} color="#0B5AA2" />
              <ExternalLink href={telHref('911') as any}>
                <ThemedText style={styles.bannerNumber}>911</ThemedText>
              </ExternalLink>
            </View>
          </View>

          {/* Cards */}
          {tab === 'national' ? (
            <View style={styles.cardsContainer}>
              {NATIONAL_CARDS.map((card) => (
                <View key={card.title} style={styles.card}>
                  <ThemedText style={styles.cardTitle}>{card.title}</ThemedText>
                  {card.items.map((item, i) => (
                    <View key={i} style={styles.cardItem}>
                      {item.label ? (
                        <ThemedText style={styles.itemLabel}>• {item.label} </ThemedText>
                      ) : null}
                      <ExternalLink href={telHref(item.number) as any}>
                        <ThemedText style={styles.itemNumber}>{item.number}</ThemedText>
                      </ExternalLink>
                    </View>
                  ))}
                </View>
              ))}
            </View>
          ) : (
            <View style={styles.cardsContainer}>
              {LOCAL_GROUPS.map((group) => (
                <View key={group.city} style={styles.card}>
                  <ThemedText style={styles.cityTitle}>{group.city}</ThemedText>
                  {group.items.map((item, i) => (
                    <View key={i} style={styles.cardItem}>
                      <ThemedText style={styles.itemLabel}>• {item.label} </ThemedText>
                      <ExternalLink href={telHref(item.number) as any}>
                        <ThemedText style={styles.itemNumber}>{item.number}</ThemedText>
                      </ExternalLink>
                    </View>
                  ))}
                </View>
              ))}
            </View>
          )}

          <View style={{ height: 8 }} />
        </View>
      </ScrollView>
    </View>
  );
}

/* -------------------------------- Styles --------------------------------- */
const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFFFFF',
  },
  scrollView: {
    flex: 1,
  },
  content: {
    paddingTop: 60,
    paddingHorizontal: 20,
  },

  /* Enhanced Header */
  titleWrap: {
    paddingVertical: 14,
    paddingHorizontal: 14,
    marginBottom: 20,
  },
  title: {
    fontSize: 26,
    lineHeight: 35,
    fontWeight: '800',
    color: '#0B3D5B',
    textAlign: 'center',
    letterSpacing: 0.2,
    fontFamily: Fonts.rounded,
  },

  // Tabs
  tabsContainer: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 20,
  },
  pill: {
    flex: 1,
    paddingVertical: 10,
    paddingHorizontal: 24,
    borderRadius: 25,
    backgroundColor: '#F3F4F6',
    alignItems: 'center',
    justifyContent: 'center',
  },
  pillActive: {
    backgroundColor: '#0B3D5B',
  },
  pillText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#6B7280',
    fontFamily: Fonts.rounded,
  },
  pillTextActive: {
    color: '#FFFFFF',
  },

  // Banner
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#FFF1F2',
    borderRadius: 12,
    padding: 16,
    marginBottom: 20,
    borderLeftWidth: 4,
    borderLeftColor: '#FB7185',
    borderWidth: 1,
    borderColor: '#FECDD3',
  },
  bannerLeft: {
    flex: 1,
  },
  bannerTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: '#0B3D5B',
    marginBottom: 2,
    fontFamily: Fonts.rounded,
  },
  bannerSubtitle: {
    fontSize: 13,
    color: '#6B7280',
  },
  bannerRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  bannerNumber: {
    fontSize: 24,
    fontWeight: '900',
    color: '#9F1239',
    fontFamily: Fonts.rounded,
  },

  // Cards
  cardsContainer: {
    gap: 16,
  },
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.05,
    shadowRadius: 8,
    elevation: 2,
  },
  cardTitle: {
    fontSize: 17,
    fontWeight: '700',
    color: '#0B3D5B',
    marginBottom: 12,
    fontFamily: Fonts.rounded,
  },
  cityTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#0B3D5B',
    marginBottom: 12,
    fontFamily: Fonts.rounded,
  },
  cardItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 8,
    flexWrap: 'wrap',
  },
  itemLabel: {
    fontSize: 14,
    color: '#374151',
    lineHeight: 20,
  },
  itemNumber: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0B3D5B',
    lineHeight: 20,
  },

  phoneRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  phoneLabel: {
    fontSize: 14,
    color: '#374151',
  },
  phoneNumber: {
    fontSize: 14,
    fontWeight: '600',
    color: '#0B3D5B',
  },
});
