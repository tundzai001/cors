#include "Prog_Config.h"
#include "hardware/Wifi_handler.h"
#include "hardware/Wifi_Credentials.h"
#include "functions/Storage_Status.h"
#include <Preferences.h>
#include <ArduinoJson.h>

namespace {
bool connectWifi(const String &ssid, const String &password, bool (*validateConnection)()) {
    if (!validWifiSsid(ssid) || !validWifiPassword(password)) return false;
    WiFi.disconnect(false, false);
    WiFi.begin(ssid.c_str(), password.c_str());
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < 6000UL) delay(100);
    if (WiFi.status() == WL_CONNECTED && (!validateConnection || validateConnection())) {
        Serial.println("[WIFI] Ket noi thanh cong! IP: " + WiFi.localIP().toString());
        return true;
    }
    WiFi.disconnect(false, false);
    return false;
}
}

bool setupWiFi(bool (*validateConnection)()) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    // Upgrade only: select a working old profile before retiring the list.
    Preferences legacy;
    String profiles;
    if (legacy.begin("wifi_config", true)) {
        profiles = legacy.getString("profiles", "");
        legacy.end();
    }
    if (!profiles.isEmpty()) {
        DynamicJsonDocument doc(3072);
        if (deserializeJson(doc, profiles) == DeserializationError::Ok && doc.is<JsonArray>()) {
            for (JsonObjectConst profile : doc.as<JsonArrayConst>()) {
                const String ssid = profile["ssid"] | "";
                const String password = profile["password"] | "";
                if (connectWifi(ssid, password, validateConnection)) {
                    if (!saveMigratedWifi(ssid, password))
                        Serial.println("[WIFI] Mang dang hoat dong; chua luu xong chuyen doi NVS.");
                    return true;
                }
            }
        }
    }
    Preferences store;
    if (!store.begin("myPrefs", true)) return false;
    const String ssid = store.getString("WIFI_SSID", WIFI_SSID);
    const String password = store.getString("WIFI_PASS", WIFI_PASSWORD);
    store.end();
    const bool connected = connectWifi(ssid, password, validateConnection);
    if (!connected) Serial.println("[WIFI] Khong ket noi duoc mang da cau hinh.");
    return connected;
}
