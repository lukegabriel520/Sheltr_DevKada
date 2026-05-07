import * as Location from 'expo-location';

import { apiService, BackendNotification } from './api';

export interface Notification {
  id: string;
  title: string;
  message: string;
  type: 'flood_alert' | 'weather_warning' | 'evacuation_order' | 'safety_update';
  priority: 'low' | 'medium' | 'high' | 'critical';
  timestamp: Date;
  read: boolean;
  fullMessage?: string;
}

function fromBackend(item: BackendNotification): Notification {
  return {
    ...item,
    timestamp: new Date(item.timestamp),
    fullMessage: item.fullMessage ?? item.message,
  };
}

export const notificationService = {
  async getNotifications(coords?: { latitude: number; longitude: number }): Promise<Notification[]> {
    try {
      let latitude = coords?.latitude;
      let longitude = coords?.longitude;

      if (latitude == null || longitude == null) {
        const { status } = await Location.requestForegroundPermissionsAsync();
        if (status !== 'granted') {
          return this.getHardcodedNotifications();
        }
        const position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
        latitude = position.coords.latitude;
        longitude = position.coords.longitude;
      }

      const payload = await apiService.getNotifications(latitude, longitude);
      if (Array.isArray(payload.items) && payload.items.length > 0) {
        return payload.items.map(fromBackend);
      }
    } catch (error) {
      console.warn('notificationService backend path:', error);
    }

    return this.getHardcodedNotifications();
  },

  getHardcodedNotifications(): Notification[] {
    const now = new Date();
    return [
      {
        id: 'system_1',
        title: 'Sheltr is ready',
        message: 'Your evacuation map and hotlines are available offline where cached.',
        fullMessage:
          'Thank you for using Sheltr. Remember: this app supports your planning, but official warnings always come from PAGASA and your local government.',
        type: 'safety_update',
        priority: 'low',
        timestamp: new Date(now.getTime() - 2 * 60 * 60 * 1000),
        read: true,
      },
      {
        id: 'system_2',
        title: 'Location helps routing',
        message: 'When GPS is on, Sheltr can suggest safer road routes and nearby shelters.',
        fullMessage:
          'Location is used on your device to find routes and briefings. You can turn it off anytime in system settings.',
        type: 'safety_update',
        priority: 'low',
        timestamp: new Date(now.getTime() - 1 * 60 * 60 * 1000),
        read: false,
      },
    ];
  },

  async markAsRead(notificationId: string): Promise<void> {
    console.log(`Marking notification ${notificationId} as read`);
  },

  async markAllAsRead(): Promise<void> {
    console.log('Marking all notifications as read');
  },
};
