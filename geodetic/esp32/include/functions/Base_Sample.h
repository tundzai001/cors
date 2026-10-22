#pragma once
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cmath>

// NMEA GGA reports H and N separately, both in metres. Do not guess missing N.
inline bool ggaEllipsoidHeight(const char *height, const char *heightUnit,
                               const char *separation, const char *separationUnit,
                               double &result) {
    if (!height || !*height || !separation || !*separation
        || !heightUnit || strcmp(heightUnit, "M") != 0
        || !separationUnit || strcmp(separationUnit, "M") != 0) return false;
    char *heightEnd = nullptr;
    char *separationEnd = nullptr;
    const double h = strtod(height, &heightEnd);
    const double n = strtod(separation, &separationEnd);
    if (*heightEnd || *separationEnd || !std::isfinite(h) || !std::isfinite(n)
        || !std::isfinite(h + n)) return false;
    result = h + n;
    return true;
}

inline bool freshRtkSample(uint32_t now, uint32_t lastGga, const char *fix) {
    return lastGga != 0 && static_cast<uint32_t>(now - lastGga) <= 3000U
        && (strcmp(fix, "RTK_FIXED") == 0 || strcmp(fix, "RTK_FLOAT") == 0);
}
