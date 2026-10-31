#include "functions/Backend_Agent.h"
#include "Backend_Agent_Internal.h"
#include "Backend_Agent_Websocket.h"

#include "Prog_Config.h"
#include "functions/MQTT_Manager.h"
#include "functions/NTRIP_Handler_IP.h"
#include "functions/RTCM_Receiver.h"
#include "functions/Device_Token.h"
#include "functions/Unicore_Identification.h"
#include "hardware/Connection_type.h"
#include "hardware/Sim_handler.h"
#include "hardware/Wifi_handler.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>
#include <vector>
#include <ctype.h>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <cmath>

#if !defined(NATIVE_BUILD)
#include <esp_random.h>
#include <esp_system.h>
#include <mbedtls/sha256.h>

using namespace BackendAgentInternal;
#endif

extern Preferences prefs;
extern PubSubClient mqtt;
extern SemaphoreHandle_t tcpStreamMutex;
extern SemaphoreHandle_t rtcmBufferMutex;

namespace BackendAgentInternal {

constexpr size_t MAX_NMEA_PACKET = 512;
constexpr size_t MAX_RTCM_PACKET = 1030;
constexpr size_t NMEA_QUEUE_LENGTH = 16;
constexpr size_t RTCM_QUEUE_LENGTH = 4;

struct DataPacket {
    uint16_t length = 0;
    uint8_t data[MAX_RTCM_PACKET] = {};
};

struct NmeaPacket {
    uint16_t length = 0;
    uint8_t data[MAX_NMEA_PACKET] = {};
};

String epochString() {
    const time_t now = time(nullptr);
    if (now > 1700000000) {
        return String(static_cast<unsigned long>(now));
    }
    // Before clock synchronization, report uptime seconds rather than Unix time.
    return String(static_cast<unsigned long>(millis() / 1000UL));
}

bool jsonBool(JsonObjectConst object, const String &key, bool fallback) {
    const JsonVariantConst value = object[key.c_str()];
    if (value.isNull()) {
        return fallback;
    }
    if (value.is<bool>()) {
        return value.as<bool>();
    }
    if (value.is<const char *>()) {
        const String text = value.as<const char *>();
        if (text.equalsIgnoreCase("true") || text == "1" || text.equalsIgnoreCase("on")) {
            return true;
        }
        if (text.equalsIgnoreCase("false") || text == "0" || text.equalsIgnoreCase("off")) {
            return false;
        }
    }
    return value.as<int>() != 0;
}

String jsonString(JsonObjectConst object, const String &key, const String &fallback) {
    const JsonVariantConst value = object[key.c_str()];
    if (value.isNull()) {
        return fallback;
    }
    return value.as<String>();
}

void copyJsonObject(JsonDocument &target, const char *key, const String &serialized) {
    DynamicJsonDocument source(CONFIG_DOCUMENT_SIZE);
    if (deserializeJson(source, serialized) != DeserializationError::Ok || !source.is<JsonObject>()) {
        target.createNestedObject(key);
        return;
    }
    JsonObject destination = target.createNestedObject(key);
    destination.set(source.as<JsonObjectConst>());
}

String urlEncode(const String &value) {
    static constexpr char hexDigits[] = "0123456789ABCDEF";
    String encoded;
    encoded.reserve(value.length() + 8);
    for (size_t i = 0; i < value.length(); ++i) {
        const uint8_t c = static_cast<uint8_t>(value[i]);
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            encoded += static_cast<char>(c);
        } else {
            encoded += '%';
            encoded += hexDigits[(c >> 4) & 0x0F];
            encoded += hexDigits[c & 0x0F];
        }
    }
    return encoded;
}

String tokenFingerprint(const String &token) {
    if (token.isEmpty()) {
        return String();
    }

#if !defined(NATIVE_BUILD)
    uint8_t digest[32] = {};
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    if (mbedtls_sha256_starts_ret(&context, 0) == 0
        && mbedtls_sha256_update_ret(&context,
                                     reinterpret_cast<const unsigned char *>(token.c_str()),
                                     token.length()) == 0
        && mbedtls_sha256_finish_ret(&context, digest) == 0) {
        mbedtls_sha256_free(&context);
        char result[17] = {};
        for (size_t i = 0; i < 8; ++i) {
            snprintf(result + (i * 2), 3, "%02x", digest[i]);
        }
        return String(result);
    }
    mbedtls_sha256_free(&context);
#endif

    // Fallback fingerprint when SHA-256 support is absent or hashing fails.
    uint32_t hash = 2166136261UL;
    for (size_t i = 0; i < token.length(); ++i) {
        hash ^= static_cast<uint8_t>(token[i]);
        hash *= 16777619UL;
    }
    char result[17] = {};
    snprintf(result, sizeof(result), "%08lx%08lx",
             static_cast<unsigned long>(hash),
             static_cast<unsigned long>(hash ^ 0xA5A5A5A5UL));
    return String(result);
}

int base64Value(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

size_t decodeBase64(const String &encoded, uint8_t *output, size_t outputCapacity) {
    uint32_t value = 0;
    int bits = -8;
    size_t outputLength = 0, symbols = 0, padding = 0;
    for (size_t i = 0; i < encoded.length(); ++i) {
        const char c = encoded[i];
        if (c == '\r' || c == '\n' || c == ' ' || c == '\t') continue;
        if (c == '=') {
            if (++padding > 2) return 0;
            continue;
        }
        const int decoded = base64Value(c);
        if (decoded < 0 || padding) return 0;
        ++symbols;
        value = (value << 6) | static_cast<uint32_t>(decoded);
        bits += 6;
        if (bits >= 0) {
            if (outputLength >= outputCapacity) return 0;
            output[outputLength++] = static_cast<uint8_t>((value >> bits) & 0xFF);
            bits -= 8;
        }
    }
    // Accept padded and unpadded input, but never a partial or ambiguous byte.
    if (symbols % 4 == 1 || (padding && (symbols + padding) % 4 != 0)
        || (value & ((1U << (bits + 8)) - 1U))) return 0;
    return outputLength;
}

bool delayMarker(const uint8_t *data, size_t length, uint32_t &delayMs) {
    if (length < 9 || memcmp(data, "$DELAY_", 7) != 0 || data[length - 1] != '$') {
        return false;
    }
    uint32_t value = 0;
    for (size_t i = 7; i + 1 < length; ++i) {
        if (data[i] < '0' || data[i] > '9') {
            return false;
        }
        value = value * 10UL + static_cast<uint32_t>(data[i] - '0');
        if (value > 10000UL) {
            value = 10000UL;
        }
    }
    delayMs = value;
    return true;
}

String licenseCodeFromBase(const String &base) {
    const int length = static_cast<int>(base.length());
    if (length < 2 || length > 31) return String();

    std::vector<int> parts(static_cast<size_t>(length), 0);
    std::vector<double> smoothed(static_cast<size_t>(length) + 1U, 0.0);
    double number = atof(base.c_str());
    if (!std::isfinite(number)) return String();

    number *= length;
    int remainingDigits = length;
    parts[0] = static_cast<int>(number * pow(10.0, -static_cast<double>(remainingDigits)));
    for (int index = 1; index < length; ++index) {
        number -= static_cast<double>(parts[index - 1])
            / pow(10.0, -static_cast<double>(remainingDigits));
        --remainingDigits;
        parts[index] = static_cast<int>(number * pow(10.0, -static_cast<double>(remainingDigits)));
    }
    // Keep the averaged final part in floating point; truncation changes the license code.
    const double finalPart = (parts[length - 2] + parts[length - 1]) / 2.0 + 1.0;
    smoothed[0] = static_cast<double>(parts[0]);

    String output;
    output.reserve(static_cast<size_t>(length) * 2U);
    const double logarithm = log10(finalPart);
    for (int index = 0; index < length; ++index) {
        const double nextPart = index + 1 == length
            ? finalPart : static_cast<double>(parts[index + 1]);
        smoothed[index + 1] = (smoothed[index] + nextPart) / 2.0;
        const double first = smoothed[index + 1] * exp(-0.2);
        const double second = logarithm * pow(nextPart, 0.2);
        // Use round-to-nearest, ties-to-even under the default rounding mode.
        output += String(static_cast<long>(std::nearbyint(first + second)));
    }
    return output;
}

String licenseBaseForSerial(const String &serial) {
    const int start = serial.length() > 10
        ? static_cast<int>(serial.length() - 10U) : 0;
    String base;
    for (int index = start; index < static_cast<int>(serial.length()); ++index) {
        char character = serial[static_cast<unsigned int>(index)];
        if (character >= 'a' && character <= 'z') character -= 'a' - 'A';
        base += String(static_cast<int>(static_cast<unsigned char>(character)));
    }
    if (base.length() > 12) base = base.substring(0, 12);
    while (base.length() < 12) base += '0';
    return base;
}

bool licenseMatchesSerial(const String &serial, const String &token) {
    if (serial.isEmpty() || token.isEmpty()) return false;
    return isDeviceBearerToken(token.c_str(), token.length())
        || token == licenseCodeFromBase(licenseBaseForSerial(serial));
}

} // namespace BackendAgentInternal

namespace {
    WiFiClient backendWebsocketWifiClient;
    TinyGsmClient backendWebsocketGsmClient(modem, 3);
}

BackendAgent backendAgent;

void BackendAgent::begin() {
    if (!autoBaseLocationQueue_) autoBaseLocationQueue_ = xQueueCreate(1, sizeof(AutoBaseLocationReply));
    // Image verification uses shared bootloader mappings; do it once before tasks.
    firmwareSize_ = ESP.getSketchSize();
    loadPreferences();
    ensureDefaultServiceConfig();

    if (!nmeaQueue_) {
        nmeaQueue_ = xQueueCreate(NMEA_QUEUE_LENGTH, sizeof(NmeaPacket));
    }
#if BACKEND_PUBLISH_RAW_RTCM
    if (!rtcmQueue_) {
        rtcmQueue_ = xQueueCreate(RTCM_QUEUE_LENGTH, sizeof(DataPacket));
    }
#endif
#if BACKEND_WEBSOCKET_ENABLED
    if (!websocket_) {
        websocket_ = new BackendAgentWebsocket(this);
    }
#endif
    // Initialize the WebSocket endpoint with the restored device identity and token.
    setupWebsocket();
    lastStatusMs_ = 0;
    lastControlCheckMs_ = millis();
    statusDirty_ = true;
    workflowCancelRequested_ = false;
    workflowTask_ = nullptr;
}

void BackendAgent::setDetectedChipIfUnknown(const String &chipType) {
    if ((sensorType_ == "UNKNOWN" || sensorType_.isEmpty()) && (chipType == "Ublox" || chipType == "Unicorecomm")) {
        sensorType_ = chipType;
        saveStatePreferences();
        Serial.printf("[GNSS] Nhan dien chip thu dong: %s (da luu NVS)\n", sensorType_.c_str());
        markStatusDirty();
    }
}

bool BackendAgent::detectGnssChip(Stream &stream, bool force) {
    if (!force && sensorType_ != "UNKNOWN" && !sensorType_.isEmpty()) {
        return true;
    }

    while (stream.available()) stream.read();

    // Probe identity without changing the receiver's output configuration.
    stream.print("VERSIONA\r\n");
    UnicoreIdentification identity;
    uint32_t startMs = millis();
    bool foundUnicore = false;
    while (millis() - startMs < 1000 && !foundUnicore) {
        while (stream.available()) {
            if (identity.feed(static_cast<uint8_t>(stream.read()))) {
                foundUnicore = true;
                break;
            }
        }
        delay(10);
    }

    if (foundUnicore) {
        sensorType_ = "Unicorecomm";
        saveStatePreferences();
        Serial.printf("[GNSS] => Phat hien chip: Unicorecomm (phan hoi: %s)\n",
                      identity.line);
        markStatusDirty();
        return true;
    }

    // 2. Active probe: U-blox (ZED-F9P, NEO-M8P...) via UBX-MON-VER
    while (stream.available()) stream.read();
    const uint8_t ubxMonVer[] = {0xB5, 0x62, 0x0A, 0x04, 0x00, 0x00, 0x0E, 0x34};
    stream.write(ubxMonVer, sizeof(ubxMonVer));

    startMs = millis();
    uint8_t ubxMatch = 0;
    bool foundUblox = false;
    while (millis() - startMs < 400) {
        while (stream.available()) {
            uint8_t b = static_cast<uint8_t>(stream.read());
            if (ubxMatch == 0 && b == 0xB5) ubxMatch = 1;
            else if (ubxMatch == 1 && b == 0x62) ubxMatch = 2;
            else if (ubxMatch == 2 && b == 0x0A) ubxMatch = 3;
            else if (ubxMatch == 3 && b == 0x04) {
                foundUblox = true;
                break;
            } else {
                ubxMatch = (b == 0xB5) ? 1 : 0;
            }
        }
        if (foundUblox) break;
        // Stop early after 150 ms if no partial UBX header or unread data remains.
        if (millis() - startMs > 150 && ubxMatch == 0 && !stream.available()) break;
        delay(10);
    }

    if (foundUblox) {
        sensorType_ = "Ublox";
        saveStatePreferences();
        Serial.println("[GNSS] => Phat hien chip: Ublox (ZED-F9P qua UBX-MON-VER)");
        markStatusDirty();
        return true;
    }

    // 3. Identify u-blox from a proprietary PUBX sentence in the incoming stream.
    startMs = millis();
    String passiveStr = "";
    while (millis() - startMs < 300) {
        while (stream.available()) {
            uint8_t b = static_cast<uint8_t>(stream.read());
            if (b >= 32 && b <= 126) passiveStr += static_cast<char>(b);
            if (passiveStr.length() >= 512) break;
        }
        if (passiveStr.indexOf("$PUBX") >= 0) {
            sensorType_ = "Ublox";
            saveStatePreferences();
            Serial.println("[GNSS] => Phat hien chip: Ublox (qua $PUBX NMEA)");
            markStatusDirty();
            return true;
        }
        if (millis() - startMs > 150 && passiveStr.isEmpty() && !stream.available()) break;
        delay(10);
    }

    return false;
}

void BackendAgent::setAgentState(const String &state) {
    agentState_ = state;
    agentState_.toLowerCase();
    markStatusDirty();
}

String BackendAgent::normalAgentState() const {
    if (stationLock_.all) return "locked";
    if (!licenseValid_) return "awaiting_license";
    return provisioned_ ? "online" : "unprovisioned";
}

String BackendAgent::serviceConfigJson() const { return serviceConfig_; }
String BackendAgent::baseConfigJson() const { return baseConfig_; }
String BackendAgent::licenseToken() const { return licenseToken_; }

String BackendAgent::statusTopic() const {
    return String("pi/devices/") + serialNumber_ + "/status";
}

String BackendAgent::rawDataTopic() const {
    return String("pi/devices/") + serialNumber_ + "/raw_data";
}

String BackendAgent::commandTopic() const {
    return String("pi/devices/") + serialNumber_ + "/command";
}

String BackendAgent::controlAckTopic() const {
    return String("pi/devices/") + serialNumber_ + "/control_ack";
}

bool BackendAgent::effectiveStreamActive(const JsonObjectConst &service,
                                         uint8_t serverId,
                                         bool failOpen) {
    if (serverId < 1 || serverId > 2) return false;
    const String prefix = String("server") + String(serverId);
    const bool enabled = jsonBool(service, prefix + "_enabled", false);
    if (!enabled) return false;
    const bool globalOnDemand = jsonBool(service, "stream_on_demand", false);
    const bool onDemand = jsonBool(service, prefix + "_stream_on_demand", globalOnDemand);
    const bool active = jsonBool(service, prefix + "_stream_active",
                                 jsonBool(service, "stream_active", !onDemand));
    return failOpen || !onDemand || active;
}

bool BackendAgent::mqttConnected() const {
    return mqttConnected_ && mqtt.connected();
}

bool BackendAgent::websocketConnected() const {
    return websocketConnected_ && websocket_ && websocket_->connected();
}

void BackendAgent::publishStatus(bool force) {
    if (serialNumber_.isEmpty()) return;
    if (statusDirty_) force = true;
    if (!force && millis() - lastStatusMs_ < BACKEND_STATUS_INTERVAL_MS) {
        return;
    }
    statusDirty_ = false;
    if (statusSequence_ == 0) controlPlaneArmedMs_ = millis();
    lastStatusMs_ = millis();
    ++statusSequence_;
    const String payload = buildStatusPayload();
    if (mqttConnected()) {
        const bool sent = mqtt.publish(statusTopic().c_str(), payload.c_str(), true);
        if (!sent || !statusPublishConfirmed_) {
            Serial.printf("[BACKEND][MQTT] STATUS %s topic=%s bytes=%u\n",
                          sent ? "SENT" : "FAILED", statusTopic().c_str(),
                          static_cast<unsigned>(payload.length()));
        }
        statusPublishConfirmed_ = sent;
    }
    if (websocketConnected()) {
        DynamicJsonDocument message(256);
        message["type"] = "status_update";
        // The payload is JSON we just serialized; borrow it until sendText returns.
        message["payload"] = serialized(payload.c_str());
        String websocketPayload;
        serializeJson(message, websocketPayload);
        websocket_->sendText(websocketPayload);
    }
}

String BackendAgent::lwtPayload() const {
    return buildLwtPayload();
}

void BackendAgent::onMqttConnected() {
    mqttConnected_ = true;
    statusPublishConfirmed_ = false;
    Serial.println("[BACKEND][MQTT] Kenh MQTT san sang nhan lenh.");
    publishStatus(true);
}

void BackendAgent::onNetworkChanged() {
    onMqttDisconnected();
    statusDirty_ = true;
    refreshWebsocket();
}

void BackendAgent::onMqttDisconnected() {
    mqttConnected_ = false;
}

void BackendAgent::enqueueNmea(const uint8_t *data, size_t length) {
    if (!nmeaQueue_ || !data || length == 0) return;
    NmeaPacket packet;
    packet.length = static_cast<uint16_t>(min(length, MAX_NMEA_PACKET - 1));
    memcpy(packet.data, data, packet.length);
    packet.data[packet.length] = 0;
    if (xQueueSend(nmeaQueue_, &packet, 0) != pdTRUE) {
        NmeaPacket discarded;
        xQueueReceive(nmeaQueue_, &discarded, 0);
        xQueueSend(nmeaQueue_, &packet, 0);
    }
}

void BackendAgent::enqueueRtcm(const uint8_t *data, size_t length) {
#if BACKEND_PUBLISH_RAW_RTCM
    if (!rtcmQueue_ || !data || length == 0 || length > MAX_RTCM_PACKET) return;
    DataPacket packet;
    packet.length = static_cast<uint16_t>(length);
    memcpy(packet.data, data, packet.length);
    if (xQueueSend(rtcmQueue_, &packet, 0) != pdTRUE) {
        DataPacket discarded;
        xQueueReceive(rtcmQueue_, &discarded, 0);
        xQueueSend(rtcmQueue_, &packet, 0);
    }
#else
    (void)data;
    (void)length;
#endif
}

void BackendAgent::drainDataQueues() {
    NmeaPacket nmea;
    while (nmeaQueue_ && xQueueReceive(nmeaQueue_, &nmea, 0) == pdTRUE) {
        bool delivered = false;
        if (mqttConnected()) {
            delivered = mqtt.publish(rawDataTopic().c_str(), nmea.data, nmea.length);
        }
        if (!delivered && websocketConnected()) {
            DynamicJsonDocument message(1024);
            message["type"] = "nmea_update";
            message["payload"] = reinterpret_cast<const char *>(nmea.data);
            String serialized;
            serializeJson(message, serialized);
            delivered = websocket_->sendText(serialized);
        }
        if (!delivered) {
            // Preserve telemetry across a short transport outage; the queue
            // has a bounded size and enqueueNmea() drops the oldest item when
            // it is full.
            xQueueSendToFront(nmeaQueue_, &nmea, 0);
            break;
        }
    }

#if BACKEND_PUBLISH_RAW_RTCM
    DataPacket rtcm;
    while (rtcmQueue_ && xQueueReceive(rtcmQueue_, &rtcm, 0) == pdTRUE) {
        if (mqttConnected()) {
            if (!mqtt.publish(rawDataTopic().c_str(), rtcm.data, rtcm.length)) {
                xQueueSendToFront(rtcmQueue_, &rtcm, 0);
                break;
            }
        } else {
            xQueueSendToFront(rtcmQueue_, &rtcm, 0);
            break;
        }
    }
#endif
}

void BackendAgent::setupWebsocket() {
#if BACKEND_WEBSOCKET_ENABLED
    if (!websocket_) return;
    const String host = readPreference("BACKEND_HOST", BACKEND_HOST);
    prefs.begin("myPrefs", true);
    const uint16_t port = prefs.getUShort("BACKEND_PORT", BACKEND_WEBSOCKET_PORT);
    prefs.end();
    const String path = String("/ws/pi/") + serialNumber_
        + (licenseToken_.isEmpty() ? String() : String("?token=") + urlEncode(licenseToken_));
    if (isWifiConnection()) {
        websocket_->begin(host, port, path, backendWebsocketWifiClient);
    } else {
        websocket_->begin(host, port, path, backendWebsocketGsmClient);
    }
#endif
}

void BackendAgent::refreshWebsocket() {
    if (!websocket_) return;
    websocket_->disconnect(false);
    setupWebsocket();
}

void BackendAgent::loopWebsocket() {
#if BACKEND_WEBSOCKET_ENABLED
    if (websocket_) websocket_->loop();
#endif
}

void BackendAgent::evaluateControlPlaneFailover() {
    if (millis() - lastControlCheckMs_ < 2000UL) return;
    lastControlCheckMs_ = millis();
    if (!licenseValid_ || !provisioned_) {
        controlPlaneFailOpen_ = false;
        recoveryStartedMs_ = 0;
        return;
    }
    if (statusSequence_ <= 0) return;

    const uint32_t now = millis();
    const bool mqttStale = lastMqttAckMs_ == 0
        ? now - controlPlaneArmedMs_ >= BACKEND_CONTROL_STALE_MS
        : now - lastMqttAckMs_ >= BACKEND_CONTROL_STALE_MS;
    const bool websocketStale = lastWebsocketAckMs_ == 0
        ? now - controlPlaneArmedMs_ >= BACKEND_CONTROL_STALE_MS
        : now - lastWebsocketAckMs_ >= BACKEND_CONTROL_STALE_MS;

    if (!controlPlaneFailOpen_ && mqttStale && websocketStale) {
        controlPlaneFailOpen_ = true;
        failOpenEntrySequence_ = statusSequence_;
        recoveryStartedMs_ = 0;
        Serial.println("[BACKEND][FAIL-SAFE] MQTT va WebSocket ACK deu stale; mo stream RTCM.");
        setupNTRIP();
        connectNTRIP();
        return;
    }

    if (controlPlaneFailOpen_) {
        const bool freshAck = (lastMqttAckSequence_ > failOpenEntrySequence_)
            || (lastWebsocketAckSequence_ > failOpenEntrySequence_);
        if (freshAck && (!mqttStale || !websocketStale)) {
            if (recoveryStartedMs_ == 0) recoveryStartedMs_ = now;
            if (now - recoveryStartedMs_ >= BACKEND_CONTROL_RECOVERY_MS) {
                controlPlaneFailOpen_ = false;
                recoveryStartedMs_ = 0;
                Serial.println("[BACKEND][FAIL-SAFE] Backend ACK da tro lai; phuc hoi stream config.");
                setupNTRIP();
                connectNTRIP();
            }
        } else {
            recoveryStartedMs_ = 0;
        }
    }
}

void BackendAgent::loop() {
    if (!websocket_) setupWebsocket();
    loopWebsocket();
    drainDataQueues();
    evaluateControlPlaneFailover();

    // Retry an unidentified receiver every 8 seconds while no base workflow is running.
    if (sensorType_ == "UNKNOWN" || sensorType_.isEmpty()) {
        const uint32_t now = millis();
        if (now - lastChipProbeMs_ >= 8000UL && !workflowActive()) {
            lastChipProbeMs_ = now;
            if (rtcmBufferMutex && xSemaphoreTake(rtcmBufferMutex, pdMS_TO_TICKS(MUTEX_TIMEOUT_MS)) == pdTRUE) {
                if (gnssSerialMutex && xSemaphoreTake(gnssSerialMutex, pdMS_TO_TICKS(MUTEX_TIMEOUT_MS)) == pdTRUE) {
                    detectGnssChip(Serial1);
                    xSemaphoreGive(gnssSerialMutex);
                }
                xSemaphoreGive(rtcmBufferMutex);
            }
        }
    }

    if (!baseStartupAttempted_ && sensorType_ != "UNKNOWN" && !sensorType_.isEmpty())
        initializeBaseAtBoot(GnssCmdBuilder::sensorTypeFromString(sensorType_));

    pollGnssStatus();
    publishStatus(false);
}

void BackendAgent::handleControlAck(const String &source, const String &message, bool retained) {
    if (retained) return;
    DynamicJsonDocument doc(4096);
    if (deserializeJson(doc, message) != DeserializationError::Ok) return;
    if (doc["type"] != "control_plane_ack") return;
    if (doc["status_boot_id"].as<String>() != statusBootId_) return;
    if (doc["serial"].as<String>() != serialNumber_) return;
    const int32_t sequence = doc["status_sequence"] | -1;
    if (sequence < 0) return;
    JsonObjectConst location = doc["auto_base_location"].as<JsonObjectConst>();
    if (autoBaseLocationQueue_ && workflowActive() && !location.isNull()
        && measureJson(location) < sizeof(AutoBaseLocationReply::json)) {
        AutoBaseLocationReply reply;
        serializeJson(location, reply.json, sizeof(reply.json));
        xQueueOverwrite(autoBaseLocationQueue_, &reply);
    }
    if (source == "mqtt") {
        if (sequence > lastMqttAckSequence_) {
            lastMqttAckSequence_ = sequence;
            lastMqttAckMs_ = millis();
        }
    } else {
        if (sequence > lastWebsocketAckSequence_) {
            lastWebsocketAckSequence_ = sequence;
            lastWebsocketAckMs_ = millis();
        }
    }
}

void BackendAgent::onMqttMessage(const char *topic, const uint8_t *payload, size_t length) {
    if (!topic || !payload || length == 0) return;
    const String topicString(topic);
    String message;
    message.reserve(length + 1);
    for (size_t i = 0; i < length; ++i) message += static_cast<char>(payload[i]);

    if (topicString == controlAckTopic()) {
        handleControlAck("mqtt", message, false);
        return;
    }
    if (topicString == commandTopic()
        || topicString == String("pi/devices/") + serialNumber_ + "/commands"
        || topicString == String("pi/device/") + serialNumber_ + "/command") {
        handleCommand("mqtt", message);
    }
}

void BackendAgent::handleWebsocketText(const String &message) {
    DynamicJsonDocument envelope(1024);
    if (deserializeJson(envelope, message) != DeserializationError::Ok) return;
    if (envelope["type"] == "control_plane_ack") {
        handleControlAck("websocket", message, false);
        return;
    }
    handleCommand("websocket", message);
}

void BackendAgent::publishTextFallback(const String &message) {
    if (websocketConnected()) websocket_->sendText(message);
}

bool backendDeviceLocked(uint8_t serverId) { return serverId == 0 ? backendAgent.isLocked() : backendAgent.isServerLocked(serverId); }
bool backendControlPlaneFailOpen() { return backendAgent.controlPlaneFailOpen(); }
bool backendDeviceProvisioned() { return backendAgent.isProvisioned(); }
bool backendLicenseValid() { return backendAgent.licenseValid(); }
bool backendBaseStartupConfigured() { return backendAgent.baseStartupConfigured(); }
