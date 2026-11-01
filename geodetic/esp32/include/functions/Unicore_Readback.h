#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace UnicoreReadback {
bool readRfLog(unsigned kind, String &response, String &error);
bool readMode(String &mode, String &error);
bool readBestPosition(JsonObject position, String &error);
bool readLogList(String &response, String &error);
const char *modeKind(const String &mode);
} // namespace UnicoreReadback
