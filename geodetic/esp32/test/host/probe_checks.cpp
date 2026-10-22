// c++ -std=c++17 -I include test/host/probe_checks.cpp -o /tmp/gnss-probe-checks
#include "functions/Gnss_Probe_Validation.h"
#include "functions/Unicore_Output.h"
#include <cassert>
#include <cstdio>
#include <vector>

std::string sentence(const std::string &body, bool legacy = false) {
    uint8_t checksum = legacy ? '$' : 0;
    for (unsigned char c : body) checksum ^= c;
    char tail[8];
    snprintf(tail, sizeof(tail), "*%02X\r\n", checksum);
    return "$" + body + tail;
}

std::vector<uint8_t> ubxVersion(size_t payloadLength) {
    std::vector<uint8_t> frame(payloadLength + 8, 0);
    frame[0] = 0xB5; frame[1] = 0x62; frame[2] = 0x0A; frame[3] = 0x04;
    frame[4] = payloadLength & 0xFF; frame[5] = payloadLength >> 8;
    uint8_t a = 0, b = 0;
    for (size_t i = 2; i < payloadLength + 6; ++i) { a += frame[i]; b += a; }
    frame[payloadLength + 6] = a; frame[payloadLength + 7] = b;
    return frame;
}

int main() {
    const auto poll = ubxVersion(0);
    assert(!isUbxVersionReply(poll.data(), poll.size()));
    for (size_t payloadLength : {40, 70, 100}) {
        auto reply = ubxVersion(payloadLength);
        assert(isUbxVersionReply(reply.data(), reply.size()));
        assert(!isUbxVersionReply(reply.data(), reply.size() - 1));
        auto stream = poll;
        stream.insert(stream.end(), reply.begin(), reply.end());
        assert(isUbxVersionReply(stream.data(), stream.size()));
        reply.back() ^= 1;
        assert(!isUbxVersionReply(reply.data(), reply.size()));
    }
    const auto badLength = ubxVersion(41);
    assert(!isUbxVersionReply(badLength.data(), badLength.size()));

    for (bool legacy : {false, true}) {
        assert(isUnicoreVersionReply(sentence("command,VERSION,response: OK", legacy)));
        assert(isUnicoreVersionReply(sentence("command,versiona,response: OK", legacy)));
    }
    assert(!isUnicoreVersionReply("VERSION\r\n"));
    assert(!isUnicoreVersionReply(sentence("command,VERSION,response: PARSING FAILD")));
    assert(!isUnicoreVersionReply(sentence("command,NOTVERSION,response: OK")));
    auto corrupt = sentence("command,VERSION,response: OK");
    corrupt[15] = 'X';
    assert(!isUnicoreVersionReply(corrupt));
    const auto nmea = sentence("GNGGA,120000,1045.0,N,10630.0,E,1,12,1.0,5.0,M");
    assert(validGnssAscii(nmea));
    assert(!isUnicoreVersionReply(nmea));
    assert(!isUnicoreVersionReply("\xB5\x62\x0A\x04"));
    assert(!validGnssAscii("$command,VERSION,response: OK*GG"));
    assert(!validGnssAscii("$command,VERSION,response: OK*"));
    std::string output = "rtcm1074 com3 1\r\n";
    routeUnicoreOutput(reinterpret_cast<uint8_t *>(&output[0]), output.size(), 1);
    assert(output == "rtcm1074 com1 1\r\n");
    std::string config = "config com2 115200\r\n";
    routeUnicoreOutput(reinterpret_cast<uint8_t *>(&config[0]), config.size(), 1);
    assert(config == "config com2 115200\r\n");
    puts("PASS: UBX poll echo rejected, version responses validated; Unicore checksums, replies and output routing");
}
