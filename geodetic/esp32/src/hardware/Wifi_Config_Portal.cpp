#include "hardware/Wifi_Config_Portal.h"
#include "hardware/Wifi_Config_Page.h"
#include "hardware/Wifi_Profiles.h"
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <atomic>

namespace {
const IPAddress portalIp(192, 168, 4, 1);
WebServer server(portalIp, 80); // Do not expose configuration on the STA interface.
std::atomic<bool> configChanged{false};
String apSsid, csrfToken;
uint32_t lastScanMs = 0;

String randomSecret() {
    char value[33];
    snprintf(value, sizeof(value), "%08lx%08lx%08lx%08lx",
             static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()),
             static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()));
    return value;
}
void headers() {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.sendHeader("Referrer-Policy", "no-referrer");
    server.sendHeader("Content-Security-Policy", "default-src 'none'; script-src 'nonce-" + csrfToken
        + "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
}
void sendJson(int code, JsonDocument &doc) {
    String body;
    serializeJson(doc, body);
    headers();
    server.send(code, "application/json; charset=utf-8", body);
}
void fail(int code, const String &message) {
    DynamicJsonDocument doc(384);
    doc["error"] = message;
    sendJson(code, doc);
}
bool authorize(bool requireToken = true) {
    const IPAddress peer = server.client().remoteIP();
    const String host = server.hostHeader();
    if (peer[0] != 192 || peer[1] != 168 || peer[2] != 4
        || (host != "192.168.4.1" && host != "192.168.4.1:80")) {
        fail(403, "Chỉ truy cập qua Wi-Fi cấu hình của ESP.");
        return false;
    }
    if (requireToken && server.header("X-Config-Token") != csrfToken) {
        fail(403, "Phiên cấu hình đã đổi. Hãy tải lại trang.");
        return false;
    }
    return true;
}
void state() {
    if (!authorize()) return;
    DynamicJsonDocument doc(1536);
    doc["ap_ssid"] = apSsid;
    doc["wifi_connected"] = WiFi.status() == WL_CONNECTED;
    doc["wifi_ssid"] = WiFi.status() == WL_CONNECTED ? WiFi.SSID() : String();
    JsonArray networks = doc.createNestedArray("networks");
    const WifiProfiles profiles = loadWifiProfiles();
    for (size_t i = 0; i < profiles.count; ++i) {
        JsonObject n = networks.createNestedObject();
        n["ssid"] = profiles.entries[i].ssid;
        n["secured"] = !profiles.entries[i].password.isEmpty();
    }
    sendJson(200, doc);
}
void scan() {
    if (!authorize()) return;
    int found = WiFi.scanComplete();
    if (server.arg("start") == "1" && found != WIFI_SCAN_RUNNING) {
        const uint32_t now = millis();
        if (lastScanMs && now - lastScanMs < 10000UL) {
            fail(429, "Vui lòng chờ 10 giây giữa hai lần quét.");
            return;
        }
        lastScanMs = now ? now : 1;
        WiFi.scanDelete();
        found = WiFi.scanNetworks(true);
    }
    DynamicJsonDocument doc(3072);
    if (found == WIFI_SCAN_RUNNING) {
        doc["pending"] = true;
        sendJson(202, doc);
        return;
    }
    if (found < 0) { fail(503, "Không quét được mạng. Hãy thử lại hoặc nhập tên thủ công."); return; }
    doc["pending"] = false;
    JsonArray networks = doc.createNestedArray("networks");
    for (int i = 0; i < found && networks.size() < 16; ++i) {
        const String ssid = WiFi.SSID(i);
        if (ssid.isEmpty() || ssid == apSsid) continue;
        bool duplicate = false;
        for (JsonObjectConst n : networks)
            if (n["ssid"] == ssid) { duplicate = true; break; }
        if (duplicate) continue;
        JsonObject n = networks.createNestedObject();
        n["ssid"] = ssid;
        n["rssi"] = WiFi.RSSI(i);
        n["open"] = WiFi.encryptionType(i) == WIFI_AUTH_OPEN;
    }
    sendJson(200, doc);
    WiFi.scanDelete();
}
void changeNetwork(bool forget) {
    if (!authorize()) return;
    const String body = server.arg("plain");
    DynamicJsonDocument doc(768);
    if (body.length() > 512 || deserializeJson(doc, body.c_str()) != DeserializationError::Ok
        || !doc.is<JsonObject>() || !doc["ssid"].is<const char *>()) {
        fail(400, "Thông tin mạng không hợp lệ.");
        return;
    }
    String error;
    bool saved = false;
    if (forget) saved = forgetWifiProfile(doc["ssid"].as<String>(), error);
    else if (doc["password"].is<const char *>() && doc["open"].is<bool>())
        saved = saveWifiProfile(doc["ssid"].as<String>(), doc["password"].as<String>(), doc["open"].as<bool>(), error);
    else error = "Thông tin mật khẩu không hợp lệ.";
    if (!saved) { fail(400, error); return; }
    configChanged.store(true);
    doc.clear();
    doc["saved"] = true;
    sendJson(200, doc);
}
void serve(void *) {
    for (;;) {
        server.handleClient();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
}

bool beginWifiConfigPortal() {
    WiFi.persistent(false);
    if (!WiFi.mode(WIFI_STA)) return false;
    Preferences store;
    if (!store.begin("wifi_config", false)) return false;
    String password = store.isKey("ap_pass") ? store.getString("ap_pass", "") : String();
    if (password.isEmpty()) {
        password = randomSecret().substring(0, 16);
        if (store.putString("ap_pass", password) != password.length()) { store.end(); return false; }
    }
    store.end();
    char name[24];
    snprintf(name, sizeof(name), "AITOGY-%06lX", static_cast<unsigned long>((ESP.getEfuseMac() >> 24) & 0xFFFFFF));
    apSsid = name;
    csrfToken = randomSecret();
    if (!WiFi.softAPConfig(portalIp, portalIp, IPAddress(255,255,255,0))
        || !WiFi.softAP(apSsid.c_str(), password.c_str(), 1, false, 2)) {
        WiFi.softAPdisconnect(true);
        return false;
    }
    // Local commissioning only: never send the AP key to MQTT, HTTP or health logs.
    Serial.printf("[WIFI CONFIG] SSID: %s | Mat khau rieng: %s | http://192.168.4.1\n", apSsid.c_str(), password.c_str());
    const char *keys[] = {"X-Config-Token"};
    server.collectHeaders(keys, 1);
    server.on("/", HTTP_GET, [] {
        if (!authorize(false)) return;
        String page(FPSTR(WIFI_CONFIG_PAGE));
        page.replace("%TOKEN%", csrfToken);
        headers();
        server.send(200, "text/html; charset=utf-8", page);
    });
    server.on("/api/state", HTTP_GET, state);
    server.on("/api/scan", HTTP_GET, scan);
    server.on("/api/save", HTTP_POST, [] { changeNetwork(false); });
    server.on("/api/forget", HTTP_POST, [] { changeNetwork(true); });
    server.onNotFound([] { fail(404, "Không tìm thấy trang."); });
    server.begin();
    if (xTaskCreate(serve, "WiFi Config", 8192, nullptr, 1, nullptr) != pdPASS) {
        server.stop();
        return false;
    }
    return true;
}

bool wifiConfigUpdatePending() { return configChanged.load(); }
bool takeWifiConfigUpdate() { return configChanged.exchange(false); }
