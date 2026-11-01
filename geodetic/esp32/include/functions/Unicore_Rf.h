#pragma once
#include "functions/Unicore_Log.h"
#include <cstdlib>
#include <cctype>

// N4 R1.4: AGCA (10 signed values), FREQJAMSTATUSA (3 CW pairs), JAMSTATUSA (1 pair).
struct UnicoreRf {
    int values[10] = {};
    unsigned kind = 0; // 1=AGC, 2=per-frequency CW, 3=overall CW
    bool decode(const char *line, size_t length) {
        if (!line || length > 383 || !validUnicoreLogChecksum(line, length)
            || length < 9 || line[length - 9] != '*') return false;
        for (size_t i = length - 8; i < length; ++i)
            if (!std::isxdigit(static_cast<unsigned char>(line[i]))) return false;
        kind = !std::strncmp(line, "#AGCA,", 6) ? 1
            : !std::strncmp(line, "#FREQJAMSTATUSA,", 16) ? 2
            : !std::strncmp(line, "#JAMSTATUSA,", 12) ? 3 : 0;
        if (!kind) return false;
        const char *start = std::strchr(line, ';'), *end = line + length - 9;
        if (!start || start >= end) return false;
        ++start;
        if (kind != 1) {
            start = std::strchr(start, ',');
            if (!start || start >= end) return false;
            ++start;
        }
        const size_t count = kind == 1 ? 10 : kind == 2 ? 8 : 4;
        for (size_t i = 0; i < count; ++i) {
            if (start >= end) return false;
            char *tail = nullptr;
            const long value = std::strtol(start, &tail, 10);
            if (tail == start || value < (kind == 1 ? -1 : 0) || value > 255) return false;
            while (tail < end && std::isspace(static_cast<unsigned char>(*tail))) ++tail;
            if (i + 1 < count ? (tail >= end || *tail != ',') : tail != end) return false;
            values[i] = static_cast<int>(value);
            start = tail + 1;
        }
        const size_t pairs = kind == 2 ? 3 : 1;
        if (kind != 1) for (size_t i = 0; i < pairs; ++i)
            if (values[i*2] < 0 || values[i*2+1] < 0 || values[i*2+1] > 2) return false;
        return true;
    }
};
