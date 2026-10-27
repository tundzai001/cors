#pragma once
#include <cstdint>
#include <cstring>

inline bool freshRtkSample(uint32_t now, uint32_t lastGga, const char *fix) {
    return lastGga != 0 && static_cast<uint32_t>(now - lastGga) <= 3000U
        && strcmp(fix, "RTK_FIXED") == 0;
}

inline const char *unicoreSurveyState(uint32_t now, uint32_t lastGga,
                                     uint32_t count, uint32_t baseline, const char *fix) {
    if (count == baseline || lastGga == 0 || static_cast<uint32_t>(now - lastGga) > 15000U)
        return "awaiting_gga";
    return std::strcmp(fix, "BASE_FIXED") == 0 ? "base_ready" : "waiting_base";
}
