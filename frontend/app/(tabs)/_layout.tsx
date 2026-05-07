import React from "react";
import { StyleSheet } from "react-native";
import { Tabs } from "expo-router";
import { HapticTab } from "@/components/haptic-tab";
import { Ionicons } from "@expo/vector-icons";

const makeIcon =
  (focusedName: any, outlineName: any, size = 24) =>
  ({ color, focused }: { color: string; focused: boolean }) =>
    <Ionicons name={focused ? focusedName : outlineName} size={size} color={color} />;

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarButton: HapticTab,
        tabBarActiveTintColor: "#0c2d48",           
        tabBarInactiveTintColor: "rgba(0,0,0,0.35)", 
        tabBarLabelStyle: styles.tabLabel,
        tabBarItemStyle: styles.tabItem,
        tabBarLabelPosition: "below-icon",

        tabBarStyle: [
          styles.tabBar,
          {
            backgroundColor: "#F8FBFF",
            borderColor: "#DDEAF7",
          },
        ],
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: makeIcon("home", "home-outline"),
        }}
      />
      <Tabs.Screen
        name="map"
        options={{
          title: "Map",
          tabBarIcon: makeIcon("map", "map-outline"),
        }}
      />
      <Tabs.Screen
        name="hotlines"
        options={{
          title: "Hotlines",
          tabBarIcon: makeIcon("call", "call-outline"),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    position: "absolute",
    bottom: 24,
    left: 0,
    right: 0,
    marginHorizontal: 24, 
    height: 64,
    borderRadius: 20,
    borderTopWidth: 0,
    borderWidth: 1,
    overflow: "hidden",

    // soft floating shadow
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.06,
    shadowRadius: 16,
    elevation: 3,
  },
  tabItem: {
    justifyContent: "center",
    alignItems: "center",
    paddingTop: 2, 
  },
  tabLabel: {
    fontSize: 12,
    marginTop: 2,
    fontWeight: "600",
  },
});
