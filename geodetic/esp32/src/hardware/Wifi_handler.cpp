#include "Prog_Config.h"
#include "hardware/Wifi_handler.h"
#include "hardware/Wifi_Profiles.h"
#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/semphr.h>

namespace {
// Separate Preferences objects prevent HTTP and network tasks sharing an open handle.
SemaphoreHandle_t storeMutex = xSemaphoreCreateMutex();
struct StoreLock {
    bool held = storeMutex && xSemaphoreTake(storeMutex, pdMS_TO_TICKS(1000)) == pdTRUE;
    ~StoreLock() { if (held) xSemaphoreGive(storeMutex); }
};

WifiProfiles readProfiles() {
    WifiProfiles profiles;
    Preferences store;
    if (store.begin("wifi_config", true)) {
        const bool configured = store.isKey("profiles");
        const String saved = configured ? store.getString("profiles", "[]") : String("[]");
        store.end();
        if (configured) {
            DynamicJsonDocument doc(3072);
            if (deserializeJson(doc, saved.c_str()) == DeserializationError::Ok && doc.is<JsonArray>()) {
                for (JsonObjectConst entry : doc.as<JsonArrayConst>()) {
                    WifiProfile profile{entry["ssid"] | "", entry["password"] | ""};
                    if (validWifiSsid(profile.ssid) && validWifiPassword(profile.password)
                        && profiles.count < WIFI_PROFILE_LIMIT)
                        profiles.entries[profiles.count++] = profile;
                }
            }
            return profiles;
        }
    }
    // Import the existing configured network without changing its credentials.
    Preferences legacy;
    if (legacy.begin("myPrefs", true)) {
        WifiProfile profile{legacy.getString("WIFI_SSID", WIFI_SSID),
                            legacy.getString("WIFI_PASS", WIFI_PASSWORD)};
        legacy.end();
        if (validWifiSsid(profile.ssid) && validWifiPassword(profile.password)) {
            profiles.entries[0] = profile;
            profiles.count = 1;
        }
    }
    return profiles;
}

bool writeProfiles(const WifiProfiles &profiles, String &error) {
    DynamicJsonDocument doc(3072);
    JsonArray array = doc.to<JsonArray>();
    for (size_t i = 0; i < profiles.count; ++i) {
        JsonObject entry = array.createNestedObject();
        entry["ssid"] = profiles.entries[i].ssid;
        entry["password"] = profiles.entries[i].password;
    }
    String saved;
    serializeJson(doc, saved);
    Preferences store;
    if (!store.begin("wifi_config", false)) {
        error = "Không mở được bộ nhớ Wi-Fi.";
        return false;
    }
    // One NVS value makes profile updates atomic across power interruption.
    const bool ok = store.putString("profiles", saved) == saved.length();
    store.end();
    if (!ok) error = "Không lưu được mạng. Vui lòng thử lại.";
    return ok;
}
}

WifiProfiles loadWifiProfiles() {
    StoreLock lock;
    return lock.held ? readProfiles() : WifiProfiles{};
}

bool saveWifiProfile(const String &ssid, const String &password, bool open, String &error) {
    StoreLock lock;
    if (!lock.held) { error = "Bộ nhớ đang bận. Vui lòng thử lại."; return false; }
    if (!validWifiSsid(ssid)) { error = "Tên mạng phải có 1–32 byte, không chứa ký tự điều khiển."; return false; }
    WifiProfiles profiles = readProfiles();
    size_t existing = profiles.count;
    for (size_t i = 0; i < profiles.count; ++i)
        if (profiles.entries[i].ssid == ssid) { existing = i; break; }
    String secret = open ? String() : password;
    if (!open && secret.isEmpty() && existing < profiles.count)
        secret = profiles.entries[existing].password;
    if ((!open && secret.isEmpty()) || !validWifiPassword(secret)) {
        error = "Mật khẩu Wi-Fi phải có 8–63 byte, hoặc chọn mạng không mật khẩu.";
        return false;
    }
    if (existing == profiles.count && profiles.count == WIFI_PROFILE_LIMIT) {
        error = "Đã lưu 8 mạng. Hãy quên một mạng trước khi thêm.";
        return false;
    }
    if (existing == profiles.count) ++profiles.count;
    // The network just saved is tried first, without duplicating its SSID.
    for (size_t i = existing; i > 0; --i) profiles.entries[i] = profiles.entries[i - 1];
    profiles.entries[0] = {ssid, secret};
    return writeProfiles(profiles, error);
}

bool forgetWifiProfile(const String &ssid, String &error) {
    StoreLock lock;
    if (!lock.held) { error = "Bộ nhớ đang bận. Vui lòng thử lại."; return false; }
    WifiProfiles profiles = readProfiles();
    for (size_t i = 0; i < profiles.count; ++i) {
        if (profiles.entries[i].ssid != ssid) continue;
        for (size_t j = i + 1; j < profiles.count; ++j) profiles.entries[j - 1] = profiles.entries[j];
        --profiles.count;
        return writeProfiles(profiles, error);
    }
    error = "Mạng này không còn trong danh sách.";
    return false;
}

bool setupWiFi(bool (*validateConnection)()) {
    const WifiProfiles profiles = loadWifiProfiles();
    WiFi.persistent(false);
    // Keep AP+STA only when the protected configuration AP started successfully.
    WiFi.mode((WiFi.getMode() & WIFI_AP) ? WIFI_AP_STA : WIFI_STA);
    for (size_t i = 0; i < profiles.count; ++i) {
        const WifiProfile &profile = profiles.entries[i];
        WiFi.disconnect(false, false); // Keep the configuration AP running.
        Serial.printf("[WIFI] Dang thu mang: %s\n", profile.ssid.c_str());
        WiFi.begin(profile.ssid.c_str(), profile.password.c_str());
        const uint32_t started = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - started < 6000UL) delay(100);
        if (WiFi.status() != WL_CONNECTED) continue;
        if (validateConnection && !validateConnection()) {
            Serial.println("[WIFI] Mang chua truy cap duoc dich vu; thu mang tiep theo.");
            continue;
        }
        Serial.println("[WIFI] Ket noi thanh cong! IP: " + WiFi.localIP().toString());
        return true;
    }
    WiFi.disconnect(false, false);
    Serial.println("[WIFI] Chua ket noi duoc mang da luu; Wi-Fi cau hinh van bat.");
    return false;
}
