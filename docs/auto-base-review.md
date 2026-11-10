# Auto Base result review

Auto Base has two source modes. In nearest mode, the ESP32 enters Rover first
and reports its GGA position. The backend selects the nearest eligible CORS
source from the device owner's visible Rover accounts and sends that NTRIP
profile to the device. After RTK_FIXED quality and sample checks pass, firmware
applies Fixed LLA and verifies receiver readback automatically. A failed check
does not install the candidate. In manual mode, the operator enters the NTRIP
profile, reviews the raw and resulting LLH, then approves it with the Fixed LLA
apply button. Receiver readback is compared with saved coordinates; matching
coordinates show success, actual differences show horizontal/height errors,
and missing or stale readbacks remain unverified.

The firmware workflow stops its temporary correction connection and restores
the previous Base position after sampling errors or cancellation. A new or
unconfigured station remains in Rover mode while the backend waits for GGA and
selects a CORS source. If restoration or receiver verification fails, the
candidate is not reported as applied. Auto Base temporarily interrupts Base
operation while collecting Rover samples. Reports persist locally across
restarts.

## Location and conversion

The calculation follows the supplied `ITRF2vn2000.txt`: decode the current
session's RTCM 1021 seven parameters and optional 1023 central-four residuals;
convert ITRF LLH to ECEF, transform to VN2000 BLH, project to NEh, apply optional
`resN/resE/resHS`, then run the reference inverse projection and fixed national
inverse seven parameters to obtain local LLH. This is not a national geoid model.

Conversion defaults to enabled for every caster host. An explicit boolean
`enable_itrf_vn2000_transform: false` preserves raw averaged receiver LLH.
No IP address determines the reference-frame policy. Missing session 1021
parameters fail conversion rather than silently using a default datum.

Projection priority is current-session RTCM 1025, then a complete explicit
`l0_deg`/`k0` pair, then a server provincial catalog lookup. Both M3 (0.9999) and
M6 (0.9996) are supported; the provided scale is never replaced by an inferred
UTM zone. The form offers optional manual L0/k0 for sources without 1025.
The backend identifies the province from the boundary snapshot and returns a
TM-3 provincial projection only when all matching historical regions share one
central meridian. Merged provinces with different meridians return options and
require manual selection or RTCM 1025 instead of guessing.

Python and ESP32 skip the backend lookup entirely when 1025 or manual parameters
are present, and when conversion is explicitly disabled. Python uses the existing
HTTP endpoint only for fallback. ESP32 uses the existing correlated MQTT/WebSocket
ACK and waits at most 30 seconds only when fallback is needed. Request/boot/device
and echoed LLH checks remain in place. Lookup failure cannot block a conversion
that already has its projection parameters.

The report contains input/output LLH, source mountpoint without credentials,
province, projection source, received message counts and parameters. Results
matching the submitted Fixed LLA retain that report in the saved configuration.
The obsolete APPLY_VN2000_PROVINCE shortcut no longer writes Python receivers.

ESP32 uses the same review contract and projection priority. Its portable
1021/1023/1025 decoder reads CRC-valid frames from the temporary rover CORS
socket, preserves fragmented input, and resets parameters at each new session.
Projection 1025 records with a nonzero natural-origin latitude are not used by
the current TM formula. GGA heights use h=H+N; invalid or missing N or non-metre
units make samples unusable. The obsolete APPLY_VN2000_PROVINCE shortcut does
not write either receiver implementation.

The previous Base coordinates (including numeric strings from the UI) are
restored before publishing `ready_for_review`. Reports fit the existing
persisted progress field and survive restart. If receiver/transport restoration
fails or the report exceeds memory capacity, the workflow reports failure and
does not publish a ready candidate.

Deploy the server, generated frontend assets and matching agent together.
Agents advertise `system_info.auto_base_review_supported` and
`system_info.auto_base_nearest_supported`. Configure routes reject unsupported
Auto Base modes for older agents with HTTP 409; the form explains the required
update. HTML asset revisions are updated with the matching UI. Existing running
stations need a reboot or a nearest-mode request to enter Rover and start the
new selection flow.

## Verification

- Python simulated receiver/cancellation/readback/status/height tests.
- Real province snapshots: Hanoi, merged Ho Chi Minh City, outside boundaries,
  polygon edge and API validation.
- Browser checks: one start button, results and province display, no write on
  filling Fixed LLA, explicit submit, matching/mismatching/stale readbacks, and
  desktop/mobile layouts.
- Native ESP32 checks (ASan/UBSan): CRC errors, truncated frames, byte-wise
  fragmentation, reset between sessions, decoded 1021/1023/1025 and numerical
  parity with Python; GGA h=H+N and invalid-height rejection.
- Native control ACK/handshake checks: real server reply through firmware ACK
  handler and workflow matcher; stale/wrong-device/wrong-LLH rejection and
  initial RTCM byte preservation.
- PlatformIO ESP32 build. Hardware and production behavior require deployment
  and real receiver readback; those are not established by these checks.

The UI displays VN-2000 as N/E/h in metres, including the saved report after
Fixed LLA is applied. Here h is ellipsoidal height, not national orthometric H.
Nested lists in sync comparisons use structural equality, so JSON object key
order cannot create a false configuration mismatch.

ESP32 native checks:

```sh
python3 geodetic/esp32/test/host/check_vn2000.py
python3 geodetic/esp32/test/host/check_auto_base_location.py
python3 geodetic/esp32/test/host/check_gga_position.py
python3 geodetic/esp32/test/host/check_gnss_stream.py
python3 geodetic/esp32/test/host/check_rover_gga.py
```

The ACK native test uses the ArduinoJson dependency installed by PlatformIO.
Numerical agreement is a software regression check, not independent surveying
accuracy or validation of a live VNGEONET caster/physical antenna.

## Reference values and displayed precision

For raw LLH `21.0849880000, 105.7866608046, -9.7862`, the fixed national
7-parameter model with central meridian 105 and k0=0.9996 reproduces the
operator's software values: NEh `2331863.4065, 581516.0764, 15.2749`.
The supplied CORS 1021 parameters and 1023 residuals reproduce the logged
NEh `2331863.6117, 581515.6492, 16.9987`. These are different transformations,
not a numerical mismatch between Python and ESP32. This code retains the
requested CORS-parameter chain; no switch to the fixed-only model is inferred.
Calculation intermediates, averaged samples, NEh, calculated LLH, datum parameters
and the displayed central meridian retain their full double precision. Python
uses round-trip decimal representations and ESP32 uses 17 significant digits in
audit strings, preserving values across MQTT/status/DB serialization. Protocol
field resolution remains unchanged; removing application rounding cannot recover
precision absent from RTCM or NMEA input.

The report separates input_coord, calculated_coord and output_coord. Only the
u-blox receiver output is quantized to nine angular decimal digits and four
height decimal digits. Unicore output retains the calculated double. The UI
shows raw LLH, calculated LLH and receiver output separately; historical reports
without calculated_coord explicitly show that field as unavailable.


## ESP32 cleanup after a temporary Rover session

Auto Base restores the saved Base mode on success, failure and cancellation.
Survey-In snapshots the effective receiver duration before entering Rover and
restores that duration (for example 120 seconds at startup, even when the saved
UI preference is 1800 seconds), with saved accuracy and output options; Fixed LLA
restores its saved coordinates. A receiver mode readback must confirm the
restored mode before source publishing resumes. If restoration/readback fails,
the Base startup state becomes an error and source streams remain blocked.
An unconfigured receiver remains in Rover while awaiting a nearest-source
selection; successful auto-apply configures Fixed LLA before Base output resumes.

Rover diagnostics distinguish `cors_rx` (raw caster bytes), `uart_tx` (bytes
accepted by the ESP32 UART driver), and `rtcm_frames` (CRC-valid RTCM frames).
These counts are scoped to the temporary CORS receive session, not GNSS output.
A short UART write aborts the workflow with a UART-specific error. A closed
CORS socket logs the counters. UART acceptance does not prove the GNSS receiver
has decoded the corrections or reached RTK FIX; the GGA fix status remains the
receiver's evidence for that.

## Projection provenance and RTCM 1025 evidence

The UI identifies the selected projection source (`rtcm1025`, `manual`, or
`server_catalog`). Its 3°/6° label is inferred from k0=0.9999/0.9996; the label
does not identify a province and is not a separate zone-width field decoded
from 1025. Existing priority remains RTCM 1025, then manual L0/k0, then the
server province catalog when 1025 is absent.

Both ESP32 and the Python agent include `processor`, `zone_width_source`, and
`rtcm1025` evidence in the conversion report. Evidence includes raw signed
35-bit LoNO, unsigned 30-bit SNO, projection type, decoded L0/k0, false offsets,
frame byte length and the first 32 bytes in hex. The ordinary 31-byte 1025 frame
is retained in full; a longer frame is explicitly marked truncated. The UI
shows decoded and selected values separately, with the formulas used. Logs also
record raw fields and hex. This permits checking the original packet rather
than treating the source label as proof of a correct decoder.

Older reports are not reconstructed: the UI explicitly says that original raw
fields/frame were not saved. A new Auto Base run on updated firmware or agent
is required to audit a real CORS session. Fixed M3/M6 packets are checked against
both production decoders by `check_projection_audit.py`, including the actual
ESP32 report serialization and its JSON memory budget. These fixtures do not
prove which parameters an earlier unrecorded CORS packet contained.

## Navigation drafts and province display

An active Auto Base workflow takes precedence over the saved Survey-In method
when the form opens. CORS host, port, username/password, source list, selected
mountpoint and manual projection are retained separately for each station in
this tab's JavaScript memory. They are not written to browser persistent storage;
reloading the page clears these drafts. Switching stations or remounting the form
does not send receiver commands. Late source-list replies are ignored after a
station/input change or unmount.

The review UI independently asks the existing `/api/coordinates/auto-base-location`
endpoint for the province when it is missing from the report, even if CORS 1025
already supplied the projection. Replies must echo the exact submitted raw LLH.
The returned province is displayed and included in an approved Fixed LLA report;
its provincial L0/k0 does not replace the projection selected by the CORS session.
Loading, mismatched replies, lookup errors and outside-boundary results are
shown explicitly. Province identification and projection selection are separate.

ESP32 workflow progress merging owns its destination JSON keys. Borrowing keys
from a temporary ArduinoJson document caused an ASan-reproduced use-after-free
at serialization, which can corrupt status JSON/UTF-8. The host regression checks
repeated nested reports containing Vietnamese text with ASan/UBSan enabled.
