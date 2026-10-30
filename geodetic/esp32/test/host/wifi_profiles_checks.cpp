#include "hardware/Wifi_Profiles.h"
#include <cassert>
#include <iostream>

int main() {
    assert(validWifiSsid("Hotspot điện thoại"));
    assert(validWifiSsid(String(32, 'a')));
    assert(!validWifiSsid(""));
    assert(!validWifiSsid(String(33, 'a')));
    assert(!validWifiSsid("bad\nssid"));
    assert(!validWifiSsid(String("abc\0def", 7)));
    assert(validWifiPassword("")); // Explicit open-network choice is checked by saveWifiProfile.
    assert(!validWifiPassword("1234567"));
    assert(validWifiPassword("12345678"));
    assert(validWifiPassword(String(63, 'a')));
    assert(!validWifiPassword(String(64, 'a')));
    assert(!validWifiPassword("12345678\n"));
    assert(!validWifiPassword(String("1234\0005678", 9)));
    std::cout << "PASS: Wi-Fi credential lengths, UTF-8 SSID, control/NUL rejection\n";
}
