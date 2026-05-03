// app/notifications.tsx
import React, { useMemo, useState, useLayoutEffect, useCallback } from 'react';
import {
  StyleSheet,
  View,
  Pressable,
  FlatList,
  Modal,
  StatusBar,
  Alert,
  Platform,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter, useNavigation } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import { ThemedText } from '@/components/themed-text';
import { IconSymbol } from '@/components/ui/icon-symbol';
import { notificationService, Notification } from '@/services/notifications';

type AppIconProps = { name: string; size?: number; color?: string };
function AppIcon({ name, size = 18, color = '#000' }: AppIconProps) {
  if (Platform.OS === 'ios') {
    return <IconSymbol name={name as any} size={size as any} color={color as any} />;
  }
  const map: Record<string, string> = {
    'chevron.left': 'chevron-back',
    ellipsis: 'ellipsis-vertical',
    'checkmark.circle.fill': 'checkmark-circle',
    'trash.fill': 'trash',
    checkmark: 'checkmark',
    'exclamationmark.triangle.fill': 'warning',
  };
  return <Ionicons name={(map[name] || 'ellipse') as any} size={size as any} color={color as any} />;
}

type ScreenNotification = {
  id: string;
  title: string;
  body: string;
  fullText: string;
  timeAgo: string;
  severity: 'warning' | 'danger' | 'info';
  read: boolean;
};

function fromService(n: Notification): ScreenNotification {
  let severity: ScreenNotification['severity'] = 'info';
  if (n.priority === 'critical' || n.priority === 'high') severity = 'danger';
  else if (n.priority === 'medium') severity = 'warning';
  const ts = n.timestamp;
  const diffMin = Math.max(0, Math.round((Date.now() - ts.getTime()) / 60000));
  const timeAgo =
    diffMin < 1 ? 'Just now' : diffMin < 60 ? `${diffMin} min ago` : `${Math.round(diffMin / 60)} hr ago`;
  return {
    id: n.id,
    title: n.title,
    body: n.message,
    fullText: n.fullMessage ?? n.message,
    timeAgo,
    severity,
    read: n.read,
  };
}

const TABBAR_CLEARANCE = 100;
const OUTLINE = '#E6EEF5';
const ICON_COLOR = '#0B3D5B';
const ACCENT_BLUE = '#0B5AA2';
const NEUTRAL = '#64748B';

export default function NotificationsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation();

  useLayoutEffect(() => {
    navigation.setOptions({ headerShown: false });
  }, [navigation]);

  const [items, setItems] = useState<ScreenNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [menu, setMenu] = useState<{ open: boolean; id: string | null }>({ open: false, id: null });
  const [detail, setDetail] = useState<ScreenNotification | null>(null);

  const load = useCallback(async (opts?: { quiet?: boolean }) => {
    if (opts?.quiet) setRefreshing(true);
    else setLoading(true);
    try {
      const raw = await notificationService.getNotifications();
      setItems(raw.map(fromService));
    } catch (e) {
      console.error(e);
      setItems(notificationService.getHardcodedNotifications().map(fromService));
    } finally {
      if (opts?.quiet) setRefreshing(false);
      else setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  type SelectedMap = { [id: string]: boolean };
  const [selectMode, setSelectMode] = useState<boolean>(false);
  const [selected, setSelected] = useState<SelectedMap>({});

  const openMenu = (id: string) => setMenu({ open: true, id });
  const closeMenu = () => setMenu({ open: false, id: null });

  const markAsRead = (id: string | null) => {
    if (!id) return;
    setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
    closeMenu();
  };

  const deleteItem = (id: string | null) => {
    if (!id) return;
    Alert.alert('Delete notification?', 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          setItems((prev) => prev.filter((n) => n.id !== id));
          closeMenu();
        },
      },
    ]);
  };

  const toggleRead = (id: string) => {
    setItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: !n.read } : n)));
  };

  const isSelected = (id: string) => !!selected[id];
  const toggleSelected = (id: string) => setSelected((p) => ({ ...p, [id]: !p[id] }));
  const clearSelection = () => setSelected({});
  const selectedIds = useMemo(() => Object.keys(selected).filter((k) => selected[k]), [selected]);

  const markSelectedRead = () => {
    if (!selectedIds.length) return;
    setItems((prev) => prev.map((n) => (selected[n.id] ? { ...n, read: true } : n)));
    clearSelection();
    setSelectMode(false);
  };
  const deleteSelected = () => {
    if (!selectedIds.length) return;
    Alert.alert('Delete selected?', 'This cannot be undone.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: () => {
          setItems((prev) => prev.filter((n) => !selected[n.id]));
          clearSelection();
          setSelectMode(false);
        },
      },
    ]);
  };

  const renderSeverityIcon = (sev: string) => {
    const color = sev === 'danger' ? '#BE123C' : sev === 'warning' ? '#F59E0B' : ACCENT_BLUE;
    return (
      <View
        style={[
          styles.iconWrapper,
          { backgroundColor: color + '1A', borderColor: color + '33' },
        ]}
      >
        <AppIcon name="exclamationmark.triangle.fill" size={18} color={color} />
      </View>
    );
  };

  const renderItem = ({ item }: { item: ScreenNotification }) => {
    const selectedStyle = selectMode && isSelected(item.id) ? { borderColor: ACCENT_BLUE } : null;

    return (
      <Pressable
        style={[styles.card, selectedStyle]}
        onPress={() => {
          if (selectMode) toggleSelected(item.id);
          else setDetail(item);
        }}
        onLongPress={() => {
          if (!selectMode) setSelectMode(true);
          toggleSelected(item.id);
        }}
      >
        <View style={styles.cardHeader}>
          {selectMode ? (
            <View style={[styles.checkbox, isSelected(item.id) && styles.checkboxChecked]}>
              {isSelected(item.id) && <AppIcon name="checkmark" size={12} color="#FFFFFF" />}
            </View>
          ) : (
            renderSeverityIcon(item.severity)
          )}

          <View style={{ flex: 1 }}>
            <ThemedText
              style={[styles.cardTitle, item.read && { color: '#6B7280' }]}
              numberOfLines={1}
            >
              {item.title}
            </ThemedText>
            <ThemedText style={styles.cardBody} numberOfLines={3}>
              {item.body}
            </ThemedText>
            <ThemedText style={styles.cardTime}>{item.timeAgo}</ThemedText>
          </View>

          {!selectMode && (
            <Pressable onPress={() => openMenu(item.id)} hitSlop={10}>
              <AppIcon name="ellipsis" size={18} color="#94A3B8" />
            </Pressable>
          )}
        </View>
      </Pressable>
    );
  };

  const HEADER_RESERVED = insets.top + 56;

  return (
    <SafeAreaView style={styles.safeArea} edges={['top', 'left', 'right']}>
      <StatusBar barStyle="dark-content" backgroundColor="#FFFFFF" />

      <View style={[styles.topRow, { top: insets.top + 8 }]}>
        <Pressable onPress={() => router.back()} style={styles.iconBtn} hitSlop={8}>
          <AppIcon name="chevron.left" size={20} color={ICON_COLOR} />
        </Pressable>

        <ThemedText style={styles.headerTitle}>Notifications</ThemedText>

        <Pressable
          onPress={() => {
            if (selectMode) clearSelection();
            setSelectMode(!selectMode);
          }}
          style={styles.editPill}
          hitSlop={8}
        >
          <ThemedText style={styles.editPillText}>{selectMode ? 'Done' : 'Edit'}</ThemedText>
        </Pressable>
      </View>

      <View style={{ height: HEADER_RESERVED }} />

      <ThemedText style={styles.disclaimer}>
        Briefings are informational only. For official alerts, follow PAGASA and your local government.
      </ThemedText>

      {loading ? (
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={ACCENT_BLUE} />
          <ThemedText style={styles.loadingLabel}>Preparing localized updates…</ThemedText>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => i.id}
          renderItem={renderItem}
          contentContainerStyle={[
            styles.listContent,
            { paddingBottom: TABBAR_CLEARANCE + 60 },
          ]}
          showsVerticalScrollIndicator={false}
          extraData={{ selectMode, selected }}
          onRefresh={() => load({ quiet: true })}
          refreshing={refreshing}
        />
      )}

      {selectMode && (
        <View style={[styles.bulkBarBottom, { paddingBottom: Math.max(insets.bottom, 10) }]}>
          <Pressable style={[styles.bulkBtn, styles.bulkPrimary]} onPress={markSelectedRead}>
            <AppIcon name="checkmark.circle.fill" size={16} color="#FFFFFF" />
            <ThemedText style={styles.bulkPrimaryText}>Mark as read</ThemedText>
          </Pressable>
          <Pressable style={[styles.bulkBtn, styles.bulkNeutral]} onPress={deleteSelected}>
            <AppIcon name="trash.fill" size={16} color="#FFFFFF" />
            <ThemedText style={styles.bulkNeutralText}>Delete</ThemedText>
          </Pressable>
        </View>
      )}

      <Modal visible={menu.open} transparent animationType="none" onRequestClose={closeMenu}>
        <View style={styles.menuContainer}>
          <View style={styles.menuCard}>
            <Pressable style={styles.menuItem} onPress={() => markAsRead(menu.id)}>
              <AppIcon name="checkmark.circle.fill" size={16} color={ACCENT_BLUE} />
              <ThemedText style={styles.menuText}>Mark as read</ThemedText>
            </Pressable>
            <Pressable
              style={[styles.menuItem, { borderTopWidth: 1, borderTopColor: '#E5E7EB' }]}
              onPress={() => deleteItem(menu.id)}
            >
              <AppIcon name="trash.fill" size={16} color={NEUTRAL} />
              <ThemedText style={[styles.menuText, { color: NEUTRAL }]}>Delete</ThemedText>
            </Pressable>
          </View>
        </View>
      </Modal>

      <Modal visible={!!detail} transparent animationType="slide" onRequestClose={() => setDetail(null)}>
        <Pressable style={styles.detailBackdrop} onPress={() => setDetail(null)}>
          <Pressable
            style={[styles.detailCard, { paddingBottom: Math.max(insets.bottom, 20) }]}
            onPress={(e) => e.stopPropagation()}
          >
            <ThemedText style={styles.detailTitle}>{detail?.title}</ThemedText>
            <ScrollView style={styles.detailScroll}>
              <ThemedText style={styles.detailBody}>{detail?.fullText}</ThemedText>
            </ScrollView>
            <View style={styles.detailActions}>
              <Pressable
                style={styles.detailSecondary}
                onPress={() => {
                  if (detail) toggleRead(detail.id);
                  setDetail(null);
                }}
              >
                <ThemedText style={styles.detailSecondaryText}>Mark read / unread</ThemedText>
              </Pressable>
              <Pressable style={styles.detailPrimary} onPress={() => setDetail(null)}>
                <ThemedText style={styles.detailPrimaryText}>Close</ThemedText>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const SOFT_SHADOW = {
  shadowColor: '#000',
  shadowOffset: { width: 0, height: 4 },
  shadowOpacity: 0.05,
  shadowRadius: 8,
  elevation: 2,
};

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: '#FFFFFF' },

  topRow: {
    position: 'absolute',
    left: 12,
    right: 12,
    zIndex: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  iconBtn: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: '#FFF',
    borderWidth: 1,
    borderColor: OUTLINE,
    alignItems: 'center',
    justifyContent: 'center',
    ...SOFT_SHADOW,
  },
  headerTitle: {
    flex: 1,
    textAlign: 'center',
    fontSize: 16,
    fontWeight: '800',
    color: ICON_COLOR,
  },
  editPill: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 999,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: OUTLINE,
    ...SOFT_SHADOW,
  },
  editPillText: { fontSize: 13, fontWeight: '800', color: ICON_COLOR },

  disclaimer: {
    marginHorizontal: 16,
    marginBottom: 8,
    fontSize: 11,
    lineHeight: 15,
    color: '#64748B',
  },

  loadingBox: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 12,
  },
  loadingLabel: { color: ICON_COLOR, fontWeight: '600' },

  listContent: { paddingHorizontal: 16 },

  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#E6EEF5',
    ...SOFT_SHADOW,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'flex-start' },

  iconWrapper: {
    width: 38,
    height: 38,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },

  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 6,
    backgroundColor: '#FFFFFF',
    borderWidth: 2,
    borderColor: '#CBD5E1',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 8,
    marginRight: 12,
  },
  checkboxChecked: { backgroundColor: ACCENT_BLUE, borderColor: ACCENT_BLUE },

  cardTitle: { fontSize: 16, fontWeight: '800', color: '#0B3D5B', marginBottom: 4 },
  cardBody: { color: '#475569', fontSize: 14, lineHeight: 18 },
  cardTime: { marginTop: 6, fontSize: 12, color: '#9CA3AF' },

  bulkBarBottom: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: TABBAR_CLEARANCE,
    backgroundColor: '#FFFFFF',
    borderRadius: 999,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: OUTLINE,
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 8,
    ...SOFT_SHADOW,
  },
  bulkBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 999,
    marginTop: 5,
  },
  bulkPrimary: { backgroundColor: ACCENT_BLUE },
  bulkNeutral: { backgroundColor: NEUTRAL },
  bulkPrimaryText: { color: '#FFFFFF', fontWeight: '800' },
  bulkNeutralText: { color: '#FFFFFF', fontWeight: '800' },

  menuContainer: {
    flex: 1,
    justifyContent: 'flex-start',
    alignItems: 'flex-end',
    paddingTop: 80,
    paddingRight: 16,
  },
  menuCard: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    width: 180,
    ...SOFT_SHADOW,
    borderWidth: 1,
    borderColor: '#EEF2F7',
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 14,
  },
  menuText: { marginLeft: 8, fontSize: 14, color: '#111827', fontWeight: '600' },

  detailBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(15,23,42,0.4)',
    justifyContent: 'flex-end',
  },
  detailCard: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingTop: 16,
    maxHeight: '85%',
  },
  detailTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: ICON_COLOR,
    marginBottom: 12,
  },
  detailScroll: { maxHeight: 420, marginBottom: 16 },
  detailBody: { fontSize: 15, lineHeight: 22, color: '#334155' },
  detailActions: { gap: 10 },
  detailPrimary: {
    backgroundColor: ACCENT_BLUE,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  detailPrimaryText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  detailSecondary: {
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: OUTLINE,
  },
  detailSecondaryText: { color: ICON_COLOR, fontWeight: '700' },
});
