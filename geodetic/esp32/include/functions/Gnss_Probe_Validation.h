#pragma once
#include <cstdint>
#include <cctype>
#include <string>

inline bool validGnssAscii(const std::string &line) {
    const size_t star = line.find('*');
    if (line.empty() || (line[0] != '$' && line[0] != '#') || star == std::string::npos) return false;
    size_t end = line.size();
    while (end && std::isspace(static_cast<unsigned char>(line[end - 1]))) --end;
    const size_t digits = end - star - 1;
    if (digits != 2 && digits != 8) return false;
    uint32_t received = 0;
    for (size_t i = star + 1; i < end; ++i) {
        const char c = static_cast<char>(std::toupper(static_cast<unsigned char>(line[i])));
        const int digit = c >= '0' && c <= '9' ? c - '0' : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
        if (digit < 0) return false;
        received = (received << 4) | digit;
    }
    uint8_t xorChecksum = 0;
    uint32_t crc = 0;
    for (size_t i = 1; i < star; ++i) {
        const uint8_t value = static_cast<uint8_t>(line[i]);
        xorChecksum ^= value;
        crc ^= value;
        for (uint8_t bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320U : 0);
    }
    // Older Unicore firmware includes the sentence prefix in its XOR checksum.
    return digits == 8 ? line[0] == '#' && received == crc
        : received == xorChecksum || received == (xorChecksum ^ static_cast<uint8_t>(line[0]));
}

inline bool isUnicoreVersionReply(const std::string &line) {
    if (!validGnssAscii(line)) return false;
    std::string upper = line.substr(0, line.find('*'));
    for (char &c : upper) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (upper.compare(0, 9, "#VERSION,") == 0 || upper.compare(0, 10, "#VERSIONA,") == 0) return true;
    return (upper.compare(0, 17, "$COMMAND,VERSION,") == 0
        || upper.compare(0, 18, "$COMMAND,VERSIONA,") == 0)
        && upper.find("RESPONSE: OK") != std::string::npos;
}

// MON-VER responses contain version fields; the zero-payload poll is not a reply.
inline bool isUbxVersionReply(const uint8_t *data, size_t length)
  {
    for (size_t start = 0; start + 8 <= length; ++start) {
      if (data[start] != 0xB5 || data[start + 1] != 0x62 ||
          data[start + 2] != 0x0A || data[start + 3] != 0x04) {
        continue;
      }

      const size_t payloadLength =
          static_cast<size_t>(data[start + 4]) |
          (static_cast<size_t>(data[start + 5]) << 8);
      const size_t frameLength = payloadLength + 8;
      if (payloadLength < 40 || (payloadLength - 40) % 30 != 0 ||
          start + frameLength > length) {
        continue;
      }

      uint8_t checksumA = 0;
      uint8_t checksumB = 0;
      for (size_t i = start + 2; i < start + 6 + payloadLength; ++i) {
        checksumA = static_cast<uint8_t>(checksumA + data[i]);
        checksumB = static_cast<uint8_t>(checksumB + checksumA);
      }

      if (checksumA == data[start + frameLength - 2] &&
          checksumB == data[start + frameLength - 1]) {
        return true;
      }
    }
    return false;
  }
