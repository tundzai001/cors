#pragma once
#include "functions/Unicore_Log.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace UnicoreConfigVerification {
struct Log { char name[24] = {}; uint8_t port = 0; double period = 0; };

inline bool parseLogs(const char *text, size_t length, std::vector<Log> &logs) {
    logs.clear();
    if (!text || length > 4096 || std::strlen(text) != length || std::strncmp(text, "#UNILOGLIST,", 12)) return false;
    const char *cursor = std::strchr(text, ';'), *end = std::strrchr(text, '*');
    const bool hasChecksum = end != nullptr;
    if (hasChecksum) {
        if (!validUnicoreLogChecksum(text, length)) return false;
    } else {
        if (length < 2 || (std::strcmp(text + length - 2, "\n\n")
            && (length < 4 || std::strcmp(text + length - 4, "\r\n\r\n")))) return false;
        end = text + length;
        for (const char *c = text; c < end; ++c)
            if ((static_cast<unsigned char>(*c) < 32 && *c != '\r' && *c != '\n' && *c != '\t')
                || static_cast<unsigned char>(*c) >= 127) return false;
    }
    if (!cursor || !end || cursor >= end) return false;
    ++cursor;
    long count = -1;
    while (cursor < end) {
        if (!hasChecksum) {
            while (cursor < end && (*cursor == '\r' || *cursor == '\n' || *cursor == ' ' || *cursor == '\t')) ++cursor;
            if (cursor < end && *cursor != '<') return false;
        }
        while (cursor < end && (*cursor == '\r' || *cursor == '\n' || *cursor == ' ' || *cursor == '<')) ++cursor;
        if (cursor == end) break;
        const char *lineEnd = cursor;
        while (lineEnd < end && *lineEnd != '\r' && *lineEnd != '\n') ++lineEnd;
        if (lineEnd - cursor > 159) return false;
        char line[160] = {};
        std::memcpy(line, cursor, lineEnd - cursor);
        cursor = lineEnd;
        if (count < 0) {
            char *tail = nullptr;
            count = std::strtol(line, &tail, 10);
            while (tail && *tail == ' ') ++tail;
            if (!tail || tail == line || *tail || count < 0 || count > 64) return false;
            logs.reserve(count);
            continue;
        }
        if (logs.size() >= static_cast<size_t>(count)) return false;
        char tokens[6][24] = {};
        const int fields = std::sscanf(line, "%23s %23s %23s %23s %23s %23s",
                                       tokens[0], tokens[1], tokens[2], tokens[3], tokens[4], tokens[5]);
        for (auto &token : tokens)
            for (char &c : token) if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
        const int start = std::strcmp(tokens[0], "LOG") == 0 ? 1 : 0;
        if (fields - start < 3 || fields - start > 4) return false;
        const bool portFirst = std::strncmp(tokens[start], "COM", 3) == 0;
        const char *port = tokens[start + (portFirst ? 0 : 1)];
        const char *name = tokens[start + (portFirst ? 1 : 0)];
        if (std::strlen(port) != 4 || std::strncmp(port, "COM", 3) || port[3] < '1' || port[3] > '3'
            || std::strlen(name) < 2 || name[0] < 'A' || name[0] > 'Z') return false;
        for (const char *c = name; *c; ++c) if (!(*c >= 'A' && *c <= 'Z') && !(*c >= '0' && *c <= '9')) return false;
        Log log;
        std::strcpy(log.name, name);
        log.port = port[3] - '0';
        const char *trigger = tokens[start + 2];
        if (!std::strcmp(trigger, "ONCE")) {
            if (fields - start != 3) return false;
        } else {
            const bool ontime = !std::strcmp(trigger, "ONTIME");
            if (fields - start != (ontime ? 4 : 3)) return false;
            char *tail = nullptr;
            log.period = std::strtod(tokens[start + (ontime ? 3 : 2)], &tail);
            if (!tail || tail == tokens[start + (ontime ? 3 : 2)] || *tail
                || !std::isfinite(log.period) || log.period <= 0) return false;
        }
        for (const Log &existing : logs)
            if (existing.port == log.port && !std::strcmp(existing.name, log.name)) return false;
        logs.push_back(log);
    }
    return count >= 0 && logs.size() == static_cast<size_t>(count);
}
} // namespace UnicoreConfigVerification
