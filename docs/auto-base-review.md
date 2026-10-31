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
configured backend origin. The server uses the existing 34-province GeoJSON and
63-region projection catalog. Province identity comes from polygon containment,
not representative province coordinates or a nearest-longitude approximation.
When merged regions have different meridians, the response lists the options
and leaves the meridian unresolved. Current administrative identity does not
establish a unique historical projection region.

The Python agent no longer contains province tables or performs external
reverse geocoding. It preserves LLH as reported by the receiver for ordinary
casters. The configure routes enable the existing ITRF/VN-2000/local-LLH chain
for the explicitly recognized VNGEONET endpoints (`14.238.1.125`, `vngeonet.vn`).
This host policy is explicit; arbitrary caster addresses are not reference-frame
identification. Raw LLH remains preserved in the report.

Transformation parameters are isolated to the active correction session and
counted only from CRC-valid RTCM frames. Conversion requires that session's
1021 parameters. It uses 1025 projection parameters when available, otherwise a
unique server catalog projection or explicit projection override. Unresolved
projection, invalid output, and failed conversions produce errors instead of
silently substituting raw LLH. 1023 residual processing and forward/inverse
formulas retain the existing algorithm derived from `ITRF2vn2000.txt`; this
change is not a new accuracy certification or a national height/geoid model.

The report contains input/output LLH, source mountpoint without credentials,
province, projection source, received message counts and parameters. Results
matching the submitted Fixed LLA retain that report in the saved configuration.
The obsolete APPLY_VN2000_PROVINCE shortcut no longer writes Python receivers.

ESP32 supports returning raw sampling results for review and restoring its
previous Base position. It does not implement the Python server location lookup
or session-based VNGEONET datum decoding; requests for that conversion return an
explicit unsupported error rather than applying built-in datum parameters.

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
- PlatformIO ESP32 build. Hardware and production behavior require deployment
  and real receiver readback; those are not established by these checks.
