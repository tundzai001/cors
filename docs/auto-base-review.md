# Auto Base result review

Auto Base now measures a candidate position. It does not install that candidate.
The operator starts one sampling workflow, reviews the raw and resulting LLH,
then uses the result to fill Fixed LLA. Submitting Fixed LLA sends the receiver
configuration. The receiver readback is compared with the saved coordinates;
matching coordinates show success, actual differences show horizontal/height
errors, and missing or stale readbacks remain unverified.

The Python workflow stops its temporary correction connection and restores the
previous Base position after sampling, errors, or cancellation. A restore marker
survives an interrupted run. If restoration fails, the candidate is not reported
as ready. Stations with no previous fixed coordinates remain in Rover mode until
Fixed LLA is applied. Auto Base still temporarily interrupts Base operation while
collecting Rover samples. Candidate results persist locally across restarts.

## Location and conversion

Python sends its sampled LLH to `POST /api/coordinates/auto-base-location` on its
configured backend origin. The server identifies the current province using the
existing 34-province GeoJSON. Auto Base uses six-degree UTM zones (k0=0.9996):
zone 48 has central meridian 105 degrees; zone 49 has central meridian 111
degrees. Server fallback selects the zone by longitude, independently of
province mergers. The historical TM-3 catalog remains unchanged for the
coordinate conversion tool and is not used by this Auto Base workflow.

The Python agent no longer contains province tables or performs external
reverse geocoding. It preserves LLH as reported by the receiver for ordinary
casters. The configure routes enable the existing ITRF/VN-2000/local-LLH chain
only when the submitted host is exactly `14.238.1.125`. Other hosts, including
`vngeonet.vn`, retain averaged LLH without a datum conversion.
This host policy is explicit; arbitrary caster addresses are not reference-frame
identification. Raw LLH remains preserved in the report.

Transformation parameters are isolated to the active correction session and
counted only from CRC-valid RTCM frames. Conversion requires that session's
1021 parameters. It uses 1025 projection parameters when available, otherwise a
server-provided six-degree projection or explicit six-degree override. A
1025/manual scale different from 0.9996 fails with an instruction to select an
M6 mountpoint; it is never silently treated as M6. Unresolved
projection, invalid output, and failed conversions produce errors instead of
silently substituting raw LLH. 1023 residual processing and forward/inverse
formulas retain the existing algorithm derived from `ITRF2vn2000.txt`; this
change is not a new accuracy certification or a national height/geoid model.

The report contains input/output LLH, source mountpoint without credentials,
province, projection source, received message counts and parameters. Results
matching the submitted Fixed LLA retain that report in the saved configuration.
The obsolete APPLY_VN2000_PROVINCE shortcut no longer writes Python receivers.

ESP32 uses the same review contract and the same exact-IP policy. Its portable
1021/1023/1025 decoder reads only CRC-valid frames from the temporary rover CORS
socket, preserves fragmented input, and resets parameters at each new session.
There is no built-in province table or nearest-meridian fallback. The obsolete
APPLY_VN2000_PROVINCE command no longer writes the ESP32 receiver either.

After averaging, ESP32 stops the rover socket and places a correlated location
request in its status progress. The backend resolves the province/UTM context
and returns it in the existing MQTT/WebSocket control ACK. The device checks
its serial and boot ID, then passes bounded replies through a one-element RTOS
queue to the workflow, where the request ID and echoed LLH must match. The
workflow waits at most 30 seconds and observes cancellation. No separate HTTP
port, TLS connection or extra GSM socket is required. Lookup failure blocks
VNGEONET conversion; ordinary casters can still return averaged LLH with the
lookup failure recorded.

For VNGEONET, the ESP32 requires session 1021 parameters and either valid 1025
six-degree projection parameters or a server UTM fallback. It retains the
reference central-four 1023 residual algorithm and inverse local-LLH constants,
and reports both VN-2000 NEh and the final LLH. Projection 1025 records with a
nonzero natural-origin latitude are not used by the TM formula. GGA heights use
h=H+N; missing/invalid N or non-metre units make the sample unusable. Neither
this height convention nor the datum transformation supplies national geoid H.

The previous Base coordinates (including numeric strings from the UI) are
restored before publishing `ready_for_review`. Reports fit the existing
persisted progress field and survive restart. If receiver/transport restoration
fails or the report exceeds memory capacity, the workflow reports failure and
does not publish a ready candidate.

Deploy the server, generated frontend assets and matching agent together.
Agents advertise `system_info.auto_base_review_supported`. Configure routes reject
Auto Base for older agents with HTTP 409; the form explains the required update.
Old agents do not implement the review contract. HTML asset revisions were
updated to invalidate cached forms/readback logic. This work does not update
running stations automatically.

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
