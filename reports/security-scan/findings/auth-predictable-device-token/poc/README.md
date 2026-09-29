# Offline deterministic-token probe

This probe reproduces the legacy device-token derivation with synthetic serial
identifiers. It demonstrates that the output is deterministic and that the
normalization/truncation steps create collisions. It performs no network I/O,
does not import the application, and does not read local configuration or
credential files.

## Run

From the disclosure report directory:

```sh
cd poc
python derive_token.py
```

Expected output:

```text
Offline serial-derived token probe (synthetic identifiers only)
A: serial=LAB-A-00000000ABCDEF1234 base=656667686970 token=783222233545
B: serial=LAB-B-99999999ABCDEF1234 base=656667686970 token=783222233545
C: serial=LAB-C-00000000ABCDEF9999 base=656667686970 token=783222233545
deterministic_repeat=true
same_last_10_collision=true
different_last_10_truncation_collision=true
PASS: public identifier material alone determines the legacy bearer value
```

The three values are synthetic and are not credentials for any real device.
There is no cleanup step because the probe does not create files or change
state.

## What a fixed implementation should change

The probe will continue to reproduce the historical algorithm after the product
is fixed; that is intentional. A fixed product should generate an independent,
cryptographically random WebSocket credential, so its stored authentication
token must not equal `derive_token(serial)`. Two rotations for the same serial
must also produce distinct values, and the old value must stop authenticating.

