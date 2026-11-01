#include "functions/Ublox_Readback.h"
#include "functions/Ubx_Reply.h"
#include "functions/Ubx_Config_Verification.h"
#include "functions/RTCM_Receiver.h"
#include "Prog_Config.h"
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

extern SemaphoreHandle_t rtcmBufferMutex;
extern SemaphoreHandle_t gnssSerialMutex;

namespace {

bool query(uint8_t cls, uint8_t id, UbxReply &reply, String &error,
           const uint8_t *payload = nullptr, size_t length = 0) {
    if (length > 4 + UbxConfigVerification::MAX_KEYS * 4) {
        error = "u-blox query exceeds readback limit";
        return false;
    }
    if (!rtcmBufferMutex || xSemaphoreTake(rtcmBufferMutex, pdMS_TO_TICKS(MUTEX_TIMEOUT_MS)) != pdTRUE) {
        error = "GNSS command port is busy";
        return false;
    }
    if (!gnssSerialMutex || xSemaphoreTake(gnssSerialMutex, pdMS_TO_TICKS(MUTEX_TIMEOUT_MS)) != pdTRUE) {
        xSemaphoreGive(rtcmBufferMutex);
        error = "GNSS reader is busy";
        return false;
    }
    while (Serial1.available()) bufferGnssQueryByte(static_cast<uint8_t>(Serial1.read()));
    uint8_t poll[12 + UbxConfigVerification::MAX_KEYS * 4] = {0xB5,0x62,cls,id,uint8_t(length),0};
    if (length) std::memcpy(poll + 6, payload, length);
    uint8_t a = 0, b = 0;
    for (size_t i = 2; i < length + 6; ++i) { a += poll[i]; b += a; }
    poll[length + 6] = a; poll[length + 7] = b;
    Serial.printf("[GNSS][READBACK][TX] UBX %02X/%02X; baud=%lu\n", cls, id,
                  static_cast<unsigned long>(Serial1.baudRate()));
    const bool written = Serial1.write(poll, length + 8) == length + 8;
    Serial1.flush();
    const uint32_t started = millis();
    bool matched = false;
    uint32_t receivedBytes = 0;
    while (written && millis() - started < 3000UL && !matched) {
        while (Serial1.available()) {
            const uint8_t value = static_cast<uint8_t>(Serial1.read());
            bufferGnssQueryByte(value);
            ++receivedBytes;
            if (reply.feed(value) && reply.frame[2] == cls && reply.frame[3] == id) {
                matched = true;
                break;
            }
        }
        if (!matched) vTaskDelay(pdMS_TO_TICKS(5));
    }
    xSemaphoreGive(gnssSerialMutex);
    xSemaphoreGive(rtcmBufferMutex);
    const char *result = !written ? "WRITE_FAILED" : matched ? "OK" : "TIMEOUT";
    Serial.printf("[GNSS][READBACK][RX] UBX %02X/%02X; bytes=%lu; elapsed_ms=%lu; reply=%s\n",
                  cls, id, static_cast<unsigned long>(receivedBytes),
                  static_cast<unsigned long>(millis() - started), result);
#if PROGRAM_DEBUG
    if (matched) {
        Serial.printf("[GNSS][READBACK][RAW] UBX %02X/%02X payload=", cls, id);
        for (size_t i = 6; i < reply.length - 2; ++i) Serial.printf("%02X", reply.frame[i]);
        Serial.println();
    }
#endif
    if (!matched) error = written ? "u-blox base readback timeout" : "u-blox query write failed";
    return matched;
}

} // namespace

bool UbloxReadback::readBaseStatus(UbxBaseStatus &status, String &error) {
    UbxReply mode;
    if (!query(0x06, 0x71, mode, error)) return false;
    if (!status.tmode(mode.frame + 6, mode.length - 8)) {
        error = "Invalid CFG-TMODE3 response";
        return false;
    }
#if PROGRAM_DEBUG
    Serial.printf("[GNSS][READBACK][CONFIG] mode=%u; min_duration=%lus; accuracy_limit=%.4fm\n",
                  static_cast<unsigned>(status.mode), static_cast<unsigned long>(status.targetSeconds),
                  UbxBaseStatus::u32(mode.frame + 6 + 28) * 0.0001);
#endif
    if (status.mode != 1) return true;
    UbxReply survey;
    if (!query(0x01, 0x3B, survey, error)) return false;
    if (!status.svin(survey.frame + 6, survey.length - 8)) {
        error = "Invalid NAV-SVIN response";
        return false;
    }
    return true;
}

bool UbloxReadback::readTmode(uint8_t *payload, String &error) {
    UbxReply reply;
    if (!query(0x06, 0x71, reply, error)) return false;
    if (reply.length != 48 || reply.frame[6] != 0) {
        error = "Invalid CFG-TMODE3 response";
        return false;
    }
    std::memcpy(payload, reply.frame + 6, 40);
    return true;
}

bool UbloxReadback::readOutputValues(const uint8_t *expected, size_t length,
                                    uint8_t *values, String &error) {
    if (!UbxConfigVerification::validExpected(expected, length)) {
        error = "Invalid output verification keys";
        return false;
    }
    uint8_t payload[4 + UbxConfigVerification::MAX_KEYS * 4] = {}; // v0, RAM, position 0
    for (size_t i = 0; i < length / 5; ++i) std::memcpy(payload + 4 + i * 4, expected + i * 5, 4);
    UbxReply reply;
    if (!query(0x06, 0x8B, reply, error, payload, 4 + length / 5 * 4)) return false;
    if (!UbxConfigVerification::outputValues(expected, length, reply.frame + 6, reply.length - 8, values)) {
        error = "Incomplete or invalid CFG-VALGET response";
        return false;
    }
    return true;
}
