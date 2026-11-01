#pragma once
#include <Arduino.h>
#include "functions/Ubx_Base_Status.h"

namespace UbloxReadback {
bool readBaseStatus(UbxBaseStatus &status, String &error);
bool readTmode(uint8_t *payload, String &error);
bool readOutputValues(const uint8_t *expected, size_t length, uint8_t *values, String &error);
bool readBaud(uint8_t port, uint32_t &baud, String &error);
}
