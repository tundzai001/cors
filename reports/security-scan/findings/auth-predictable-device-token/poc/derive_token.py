#!/usr/bin/env python3
"""Offline probe for the legacy serial-derived device token algorithm.

This script is intentionally network-free. It uses only synthetic identifiers and
does not read application configuration, credentials, or local device state.
"""

from __future__ import annotations

import math


def generate_pi_license_base(serial_number: str) -> str:
    suffix = serial_number[-10:]
    normalized = "".join(
        char.upper() if "a" <= char.lower() <= "z" else char for char in suffix
    )
    decimal_ordinals = "".join(str(ord(char)) for char in normalized)
    return decimal_ordinals[:12].ljust(12, "0")


def get_license_code_from_string(string_number: str) -> str:
    length = len(string_number)
    if length == 0:
        return ""

    arr = [0] * (length + 1)
    running = float(string_number) * length
    exponent = length
    arr[0] = int(running * math.pow(10.0, -float(exponent)))

    for index in range(1, length):
        running -= float(arr[index - 1]) / math.pow(10.0, -float(exponent))
        exponent -= 1
        arr[index] = int(running * math.pow(10, -float(exponent)))

    arr[length] = (arr[length - 2] + arr[length - 1]) / 2 + 1
    averages = [0.0] * (length + 1)
    averages[0] = float(arr[0])
    for index in range(1, length + 1):
        averages[index] = (averages[index - 1] + float(arr[index])) / 2.0

    result = ""
    for index in range(length):
        term_a = averages[index + 1] * math.exp(-0.2)
        term_b = math.log(arr[length], 10) * math.pow(arr[index + 1], 0.2)
        result += str(int(round(term_a + term_b)))
    return result


def derive_token(serial_number: str) -> tuple[str, str]:
    base = generate_pi_license_base(serial_number)
    return base, get_license_code_from_string(base)


def main() -> None:
    synthetic_serials = {
        "A": "LAB-A-00000000ABCDEF1234",
        "B": "LAB-B-99999999ABCDEF1234",
        "C": "LAB-C-00000000ABCDEF9999",
    }
    derived = {label: derive_token(serial) for label, serial in synthetic_serials.items()}

    print("Offline serial-derived token probe (synthetic identifiers only)")
    for label, serial in synthetic_serials.items():
        base, token = derived[label]
        print(f"{label}: serial={serial} base={base} token={token}")

    repeat = derive_token(synthetic_serials["A"])
    deterministic = repeat == derived["A"]
    same_suffix_collision = derived["A"] == derived["B"]
    truncation_collision = derived["A"] == derived["C"]

    print(f"deterministic_repeat={str(deterministic).lower()}")
    print(f"same_last_10_collision={str(same_suffix_collision).lower()}")
    print(f"different_last_10_truncation_collision={str(truncation_collision).lower()}")

    if not (deterministic and same_suffix_collision and truncation_collision):
        raise SystemExit("probe failed: legacy derivation properties were not reproduced")
    print("PASS: public identifier material alone determines the legacy bearer value")


if __name__ == "__main__":
    main()
