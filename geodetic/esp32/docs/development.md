# Firmware development and upstream review

## Backend agent ownership

`include/functions/Backend_Agent.h` defines the public agent interface. Its
implementation is split by responsibility under `src/functions/`:

| File | Responsibility |
| --- | --- |
| `Backend_Agent.cpp` | Agent startup, transport lifecycle and data queues |
| `Backend_Agent_Storage.cpp` | NVS migration, saved identity/configuration and persistence |
| `Backend_Agent_Startup.cpp` | Receiver mode restoration and startup configuration |
| `Backend_Agent_Commands.cpp` | Backend command validation, execution and results |
| `Backend_Agent_Workflows.cpp` | Auto Base, reference checks and GNSS command exchanges |
| `Backend_Agent_Gnss.cpp` | Periodic receiver readback and saved Survey-In results |
| `Backend_Agent_Status.cpp` | Status and health payloads |
| `Backend_Agent_Projection.cpp` | Coordinate transformation and province projections |
| `Backend_Agent_Websocket.cpp` | RFC6455 handshake, framing and TCP reconnects |

`Backend_Agent_Internal.h` and `Backend_Agent_Websocket.h` are implementation
headers. Keep transport framing out of command handling and status formatting.
Survey-In status uses receiver readback; the removed GGA polling state must not
be reintroduced to make RTCM-only mode depend on NMEA.

## Firmware ownership

- `src/main.cpp`: hardware initialization, task startup and worker loops.
- `src/hardware/Network_Manager.cpp`: boot connectivity, Wi-Fi/SIM handover,
  recovery timing and transport client selection. Its MQTT-side helpers run
  with `tcpStreamMutex` held; maintenance acquires that mutex itself.
- `src/functions/Gnss_Detection.cpp`: receiver identity probes and baud scan.
- `src/functions/NTRIP_Handler_IP.cpp`: caster endpoints, authentication,
  correction streaming and temporary rover sessions.

The unused `bootstrapUM980`/`bootstrapUblox` UART configuration paths were
removed. Receiver startup is owned by `Backend_Agent_Startup.cpp`; do not add
another configuration path inside NTRIP. Write-only disconnect counters were
also removed. Moved NVS, receiver detection and network handover bodies were
compared against the previous source; command bytes, saved keys, probe timing
and socket shutdown order are preserved.

## Verify a cleanup

Build the firmware as described in [base-4g.md](base-4g.md#kiểm-tra):

```sh
pio run -e tdm240x_dev_general
git diff --check
```

These checks cover software behavior. They do not establish antenna position
accuracy or replace verification on the receiver. A structural cleanup should
preserve command bytes, reply handling, NVS keys and MQTT payloads.

## Prepare an upstream change

Fetch the current `long-in-aitogy/esp32-um-ubx-base` `develop` branch and start
the review branch there. The cleanup prepared on 27 October 2026 uses
`6af5a260c9b8f62ad9296d50b01dc66c57a2092e` as its base; fetch again before
publishing because `develop` can advance.

Keep deployment defaults from upstream when transferring firmware from CORS.
Local Wi-Fi credentials and test mountpoints are not firmware fixes. Transfer
intentional command-port routing changes separately from those defaults.
Do not copy `.pio`, `compile_commands.json` or editor databases into the patch.

Review the firmware-only diff against the actual target branch. Separate
mechanical moves from behavior changes when preparing commits. A merge
conflict records overlapping edits; resolve it against the current code and
rerun the checks rather than replacing the target file with an old snapshot.

PR #103 already merged the previous CORS synchronization. PR #66 still points
to an older branch with overlapping changes. The new cleanup is prepared on a
separate branch; changing or closing that PR is a separate repository action.

## Main review — 31 October 2026

Fetched `long-in-aitogy/esp32-um-ubx-base/main` and reviewed its current tip,
`844c63598f8d33a597c7ba8a07decd59e176dd0f` (7 October 2026, #88).
Its final change replaces compile-time GNSS chip selection with runtime
selection, adds `GNSS SET CHIP`, and persists `GNSS_MDL_TYPE`. These paths
already exist in this CORS firmware under `GnssCmdBuilder`,
`handleGnssBaseCommand`, `handleGnssSetCommand`, and receiver detection.
The earlier firmware sync at `180c3aa` also contains this exact main tip.
No incoming main changes remain to port at this review; retain CORS receiver
detection, command validation, routing, ACK checks and saved settings.

Continued cleanup in this CORS working tree: share service JSON conversion
between backend agent and NTRIP, and share base command finalization within
each receiver implementation. JSON keys, fallback rules, command order and
delays are preserved. Shared JSON conversions were verified during development.
