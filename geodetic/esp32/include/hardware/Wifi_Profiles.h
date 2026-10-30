#pragma once
#include <Arduino.h>
#include <array>
#include <cstddef>

constexpr size_t WIFI_PROFILE_LIMIT = 8;
struct WifiProfile { String ssid, password; };
struct WifiProfiles {
    std::array<WifiProfile, WIFI_PROFILE_LIMIT> entries;
    size_t count = 0;
};

inline bool validWifiSsid(const String &ssid) {
    if (ssid.isEmpty() || ssid.length() > 32) return false;
    for (size_t i = 0; i < ssid.length(); ++i)
        if (static_cast<uint8_t>(ssid[i]) < 32 || ssid[i] == 127) return false;
    return true;
}
inline bool validWifiPassword(const String &password) {
    if (password.isEmpty()) return true; // Only explicitly selected open networks.
    if (password.length() < 8 || password.length() > 63) return false;
    for (size_t i = 0; i < password.length(); ++i)
        if (static_cast<uint8_t>(password[i]) < 32 || password[i] == 127) return false;
    return true;
}

WifiProfiles loadWifiProfiles();
bool saveWifiProfile(const String &ssid, const String &password, bool open, String &error);
bool forgetWifiProfile(const String &ssid, String &error);
