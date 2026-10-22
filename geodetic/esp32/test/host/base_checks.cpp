#include "functions/Station_Lock.h"
#include "functions/Unicore_Output.h"
#include "functions/Base_Sample.h"
#include "functions/Device_Token.h"
#include "functions/ubx_cmd_builder.h"
#include <cassert>
#include <iostream>
#include <string>

using namespace UbxCmdBuilder;

void checkPackets(const Command &bytes) {
    size_t offset = 0;
    while (offset < bytes.size()) {
        assert(bytes.size() - offset >= 8);
        assert(bytes[offset] == 0xB5 && bytes[offset + 1] == 0x62);
        const size_t size = bytes[offset + 4] | (size_t(bytes[offset + 5]) << 8);
        assert(offset + size + 8 <= bytes.size());
        uint8_t a = 0, b = 0;
        for (size_t i = offset + 2; i < offset + size + 6; ++i) { a += bytes[i]; b += a; }
        assert(bytes[offset + size + 6] == a && bytes[offset + size + 7] == b);
        offset += size + 8;
    }
}

int main(int argc, char **argv) {
    const auto tokenValid = [](const std::string &token) {
        return isDeviceBearerToken(token.data(), token.size());
    };
    assert(tokenValid("dt_" + std::string(40, 'a')));
    assert(tokenValid("dt_" + std::string(64, 'Z')));
    assert(tokenValid("dt_" + std::string(38, 'a') + "_-"));
    assert(!tokenValid("dt_" + std::string(39, 'a')));
    assert(!tokenValid("dt_" + std::string(65, 'a')));
    assert(!tokenValid("DT_" + std::string(40, 'a')));
    assert(!tokenValid("dt_" + std::string(39, 'a') + "!"));
    assert(!tokenValid("dt_" + std::string(39, 'a') + "\n"));
    assert(!isDeviceBearerToken(nullptr, 43));
    assert(freshRtkSample(5000, 4000, "RTK_FIXED"));
    assert(freshRtkSample(5000, 4000, "RTK_FLOAT"));
    assert(!freshRtkSample(5000, 0, "RTK_FIXED"));
    assert(!freshRtkSample(5000, 1000, "RTK_FIXED"));
    assert(!freshRtkSample(5000, 4000, "GPS_FIX"));
    assert(freshRtkSample(100, UINT32_MAX - 100, "RTK_FIXED"));
    StationLock lock;
    const int second[] = {2};
    assert(lock.setScoped(second, 1));
    assert(!lock.locked() && !lock.locked(1) && lock.locked(2));
    StationLock rebooted;
    rebooted.restore(lock.all, lock.servers);
    assert(!rebooted.locked(1) && rebooted.locked(2));
    const int invalid[] = {3};
    assert(!lock.setScoped(invalid, 1) && lock.locked(2));
    const int both[] = {1, 2};
    assert(lock.setScoped(both, 2) && !lock.locked() && lock.locked(1) && lock.locked(2));
    lock.restore(true, 0);
    assert(lock.locked() && lock.locked(1) && lock.locked(2));
    assert(lock.setScoped(nullptr, 0) && !lock.locked(1) && !lock.locked(2));
    lock.restore(false, 255);
    assert(lock.locked() && lock.locked(1) && lock.locked(2));

    std::string ascii = "rtcm1074 com3 1\r\n";
    routeUnicoreOutput(reinterpret_cast<uint8_t *>(&ascii[0]), ascii.size(), 2);
    assert(ascii == "rtcm1074 com2 1\r\n");
    std::string other = "config com3 115200\r\n";
    routeUnicoreOutput(reinterpret_cast<uint8_t *>(&other[0]), other.size(), 2);
    assert(other == "config com3 115200\r\n");
    for (size_t length = 0; length < ascii.size(); ++length) {
        auto shortened = ascii.substr(0, length);
        routeUnicoreOutput(reinterpret_cast<uint8_t *>(shortened.data()), length, 2);
    }
    double height = NAN;
    assert(ggaEllipsoidHeight("40", "M", "-25", "M", height) && height == 15);
    assert(ggaEllipsoidHeight("40", "M", "25", "M", height) && height == 65);
    assert(ggaEllipsoidHeight("0", "M", "0", "M", height) && height == 0);
    for (const char *bad : {"", "bad", "25bad", "nan", "inf", "1e999"}) {
        assert(!ggaEllipsoidHeight("40", "M", bad, "M", height));
        assert(!ggaEllipsoidHeight(bad, "M", "25", "M", height));
    }
    assert(!ggaEllipsoidHeight("40", "F", "25", "M", height));
    assert(!ggaEllipsoidHeight("40", "M", "25", "F", height));
    assert(!ggaEllipsoidHeight("40", "M", nullptr, "M", height));
    GnssOptions usb, uart;
    uart.ports = {"UART1"};
    for (auto type : {GnssSensorType::Ublox, GnssSensorType::Unicore}) {
        const auto survey = buildBaseSurveyInCommand(300, 1.0, type, uart);
        const auto fixed = buildBaseFixedLlaCommand(10.75, 106.5, 12.5, 0.01, type, uart);
        const auto explicitHeight = buildBaseFixedLlaCommand(10.75, 106.5, 12.5, 0.01, type, uart, AltitudeReference::Ellipsoid);
        const auto rover = buildRoverCommand(type);
        assert(!survey.empty() && !fixed.empty() && !rover.empty());
        if (type == GnssSensorType::Ublox) {
            assert(fixed == explicitHeight);
            assert(fixed[0][18] == 0xe2 && fixed[0][19] == 4 && fixed[0][24] == 0); // h = 12.5 m
            assert(survey[0][8] == 1 && fixed[0][8] == 2 && fixed[0][9] == 1);
            assert(survey[0][30] == 44 && survey[0][31] == 1);
            assert(rover[1][8] == 0);
            for (const auto &commands : {survey, fixed, rover}) for (const auto &packet : commands) checkPackets(packet);
        } else {
            const auto text = commandListToBytes(fixed);
            const std::string output(text.begin(), text.end());
            assert(output.find("mode base 10.7500000000 106.5000000000 12.5000\r\n") != std::string::npos);
            assert(output.find("undulation") == std::string::npos);
            const auto textHeight = commandListToBytes(explicitHeight);
            const std::string outputHeight(textHeight.begin(), textHeight.end());
            const auto undulation = outputHeight.find("config undulation 0.0\r\n");
            assert(undulation != std::string::npos && undulation < outputHeight.find("mode base "));
            assert(outputHeight.find("12.5000\r\n") != std::string::npos);
            assert(output.find("com3") == std::string::npos);
            assert(output.find("rtcm1074 com2 1") != std::string::npos);
        }
    }
    // Exercise the same H+N input through both actual receiver command formats.
    assert(ggaEllipsoidHeight("40", "M", "-25", "M", height));
    const auto ubloxHeight = buildBaseFixedLlaCommand(10.75, 106.5, height, .01, GnssSensorType::Ublox, uart, AltitudeReference::Ellipsoid)[0];
    int32_t centimetres = 0;
    for (int i = 0; i < 4; ++i) centimetres |= int32_t(ubloxHeight[18 + i]) << (8 * i);
    assert(centimetres == 1500);
    const auto unicoreHeight = buildBaseFixedLlaCommand(10.75, 106.5, height, .01, GnssSensorType::Unicore, uart, AltitudeReference::Ellipsoid);
    double separation = -25, configuredHeight = NAN;
    for (const auto &command : unicoreHeight) {
        const std::string line(command.begin(), command.end());
        if (line == "config undulation 0.0\r\n") separation = 0;
        if (line.rfind("mode base ", 0) == 0) {
            double lat, lon, inputHeight;
            assert(sscanf(line.c_str(), "mode base %lf %lf %lf", &lat, &lon, &inputHeight) == 3);
            configuredHeight = inputHeight + separation;
        }
    }
    assert(configuredHeight == 15);
    for (const String method : {String("FIXED_LLA"), String("SURVEY_IN")}) {
        const auto legacy = commandListToBytes(buildGeotekLteUnicoreConfig(method, 60, 10.75, 106.5, 12.5));
        const auto ellipsoid = commandListToBytes(buildGeotekLteUnicoreConfig(method, 60, 10.75, 106.5, 12.5, AltitudeReference::Ellipsoid));
        const std::string legacyText(legacy.begin(), legacy.end()), text(ellipsoid.begin(), ellipsoid.end());
        assert(legacyText.find("undulation") == std::string::npos);
        if (method == "SURVEY_IN") assert(legacy == ellipsoid);
        else {
            const auto undulation = text.find("config undulation 0.0\r\n");
            assert(undulation != std::string::npos && undulation < text.find("mode base "));
        }
    }
    auto output = buildUbloxOutputConfigCommand(usb);
    routeUbloxOutput(output.data(), output.size());
    checkPackets(output);
    assert(output == buildUbloxOutputConfigCommand(uart));

    // Optional stdin bridge lets the Python check use real CORS command bytes.
    if (argc > 1) {
        const bool ublox = std::string(argv[1]) == "Ublox";
        std::string hex;
        while (std::cin >> hex) {
            Command packet;
            for (size_t i = 0; i < hex.size(); i += 2) packet.push_back(std::stoul(hex.substr(i, 2), nullptr, 16));
            if (ublox) {
                routeUbloxOutput(packet.data(), packet.size());
                checkPackets(packet);
            } else {
                routeUnicoreOutput(packet.data(), packet.size(), 2);
            }
            for (uint8_t byte : packet) printf("%02x", byte);
            printf("\n");
        }
    } else {
        std::cout << "PASS: both receiver command sets, checksums, UART routing, scoped/persistent locks\n";
    }
}
