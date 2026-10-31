#pragma once
#include <ArduinoJson.h>
#include <Arduino.h>

void appendStorageStatus(JsonObject system);
bool clearUnusedWifi(String &error);
bool saveMigratedWifi(const String &ssid, const String &password);
