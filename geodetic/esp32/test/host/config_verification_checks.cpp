#include "functions/Ubx_Config_Verification.h"
#include "functions/Ubx_Reply.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>

int main() {
    uint8_t expected[40] = {}, actual[40] = {};
    expected[2] = actual[2] = 2;
    expected[3] = actual[3] = 1;
    expected[16] = actual[16] = uint8_t(-12);
    expected[20] = actual[20] = 100;
    assert(UbxConfigVerification::fixedMatches(expected, actual, 40));
    for (size_t offset : {size_t(2), size_t(3), size_t(4), size_t(8), size_t(12),
                          size_t(16), size_t(17), size_t(18), size_t(20), size_t(23)}) {
        actual[offset] ^= 2;
        assert(!UbxConfigVerification::fixedMatches(expected, actual, 40));
        actual[offset] ^= 2;
    }
    actual[19] = 99; // Reserved bytes cannot cause a false mismatch.
    assert(UbxConfigVerification::fixedMatches(expected, actual, 40));
    actual[20] += 1; // 0.1 mm: one conversion quantization step.
    assert(UbxConfigVerification::fixedMatches(expected, actual, 40));
    actual[20] -= 1;
    assert(!UbxConfigVerification::fixedMatches(expected, actual, 39));
    // Independent keys from UBX-22008968: Galileo 1097 and BeiDou 1127 UART1.
    const uint8_t records[] = {0x19, 0x03, 0x91, 0x20, 1, 0xD7, 0x02, 0x91, 0x20, 0};
    uint8_t payload[] = {1, 0, 0, 0, 0xD7, 0x02, 0x91, 0x20, 0, 0x19, 0x03, 0x91, 0x20, 1};
    uint8_t values[2] = {};
    assert(UbxConfigVerification::validExpected(records, sizeof(records)));
    assert(UbxConfigVerification::outputValues(records, sizeof(records), payload, sizeof(payload), values));
    assert(values[0] == 1 && values[1] == 0); // Response order need not match the request.
    payload[13] = 0;
    assert(UbxConfigVerification::outputValues(records, sizeof(records), payload, sizeof(payload), values));
    assert(values[0] != records[4]); // A valid reply can still contain a mismatch.
    payload[1] = 1;
    assert(!UbxConfigVerification::outputValues(records, sizeof(records), payload, sizeof(payload), values));
    payload[1] = 0;
    assert(!UbxConfigVerification::outputValues(records, sizeof(records), payload, sizeof(payload) - 1, values));
    payload[9] = 0xD7; payload[10] = 0x02; // Duplicate BeiDou, missing Galileo.
    assert(!UbxConfigVerification::outputValues(records, sizeof(records), payload, sizeof(payload), values));
    uint8_t invalid[] = {0xFF, 0xFF, 0x91, 0x20, 1};
    assert(!UbxConfigVerification::validExpected(invalid, sizeof(invalid)));
    assert(!UbxConfigVerification::validExpected(records, 9));
    uint8_t tooMany[205] = {};
    assert(!UbxConfigVerification::validExpected(tooMany, sizeof(tooMany)));
    puts("PASS: Fixed LLA/accuracy mismatch, reordered values, missing/duplicate keys, wrong layer and bounded parsing");
}
