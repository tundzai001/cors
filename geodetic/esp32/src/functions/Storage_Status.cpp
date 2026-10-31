#include "functions/Storage_Status.h"
#include "Backend_Agent_Internal.h"
#include "hardware/Wifi_Credentials.h"
#include <Preferences.h>
#include <nvs.h>
#include <esp_partition.h>
#include <esp_ota_ops.h>

namespace {
size_t obsoleteWifiKeys() {
    nvs_handle_t handle;
    if (nvs_open("wifi_config", NVS_READONLY, &handle) != ESP_OK) return 0;
    size_t count = 0;
    for (const char *key : {"profiles", "ap_pass"}) {
        size_t length = 0;
        if (nvs_get_str(handle, key, nullptr, &length) == ESP_OK) ++count;
    }
    nvs_close(handle);
    return count;
}
void partitionStatus(JsonArray partitions, const char *label) {
    JsonObject item = partitions.createNestedObject();
    item["id"] = label;
    nvs_stats_t stats{};
    const esp_err_t result = nvs_get_stats(label, &stats);
    item["available"] = result == ESP_OK;
    if (result != ESP_OK) return;
    item["used_entries"] = stats.used_entries;
    item["free_entries"] = stats.free_entries;
    item["total_entries"] = stats.total_entries;
    item["namespaces"] = stats.namespace_count;
    const esp_partition_t *partition = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_NVS, label);
    if (partition) item["size_bytes"] = partition->size;
}
}

void appendStorageStatus(JsonObject system) {
    JsonObject memory = system["memory"];
    memory["minimum_free_heap_bytes"] = ESP.getMinFreeHeap();
    memory["largest_free_block_bytes"] = ESP.getMaxAllocHeap();
    JsonObject disk = system["disk"];
    const esp_partition_t *app = esp_ota_get_running_partition();
    if (app) disk["app_capacity_bytes"] = app->size;
    // Physical flash also holds OTA/NVS/data partitions; it is not one free pool.
    disk["firmware_bytes"] = disk["used_bytes"];
    JsonObject nvs = system.createNestedObject("nvs");
    nvs["cleanup_supported"] = true;
    nvs["sampled_at_ms"] = millis();
    partitionStatus(nvs.createNestedArray("partitions"), "nvs");
    partitionStatus(nvs["partitions"].as<JsonArray>(), "agent_state");
    JsonArray groups = nvs.createNestedArray("cleanup_groups");
    JsonObject wifi = groups.createNestedObject();
    wifi["id"] = "unused_wifi";
    wifi["keys"] = obsoleteWifiKeys();
    JsonObject history = groups.createNestedObject();
    history["id"] = "auto_base_history";
    history["keys"] = 0;
    nvs_handle_t handle;
    const char *partition = BackendAgentInternal::backendPreferencesPartition();
    const char *space = strcmp(partition, "nvs") == 0 ? "myPrefs" : "backend";
    if (nvs_open_from_partition(partition, space, NVS_READONLY, &handle) == ESP_OK) {
        size_t count = 0, length = 0;
        for (const char *key : {"PROGRESS_JSON"}) {
            if (nvs_get_str(handle, key, nullptr, &length) == ESP_OK && length > 3) ++count;
        }
        history["keys"] = count;
        nvs_close(handle);
    }
}

bool clearUnusedWifi(String &error) {
    nvs_handle_t handle;
    esp_err_t opened = nvs_open("wifi_config", NVS_READONLY, &handle);
    if (opened == ESP_ERR_NVS_NOT_FOUND) return true;
    if (opened != ESP_OK) { error = "Cannot open obsolete Wi-Fi storage"; return false; }
    nvs_close(handle);
    opened = nvs_open("wifi_config", NVS_READWRITE, &handle);
    if (opened != ESP_OK) { error = "Cannot update obsolete Wi-Fi storage"; return false; }
    esp_err_t erased = ESP_OK;
    for (const char *key : {"profiles", "ap_pass"}) {
        const esp_err_t result = nvs_erase_key(handle, key);
        if (result != ESP_OK && result != ESP_ERR_NVS_NOT_FOUND) erased = result;
    }
    const esp_err_t committed = erased == ESP_OK ? nvs_commit(handle) : erased;
    nvs_close(handle);
    if (committed != ESP_OK) error = "Cannot clear obsolete Wi-Fi storage";
    return committed == ESP_OK;
}

bool saveMigratedWifi(const String &ssid, const String &password) {
    if (!validWifiSsid(ssid) || !validWifiPassword(password)) return false;
    Preferences active;
    if (!active.begin("myPrefs", false)) return false;
    active.putString("WIFI_SSID", ssid);
    active.putString("WIFI_PASS", password);
    const bool saved = active.getString("WIFI_SSID", "") == ssid
        && active.getString("WIFI_PASS", "") == password;
    active.end();
    if (!saved) return false;
    String error;
    return clearUnusedWifi(error);
}
