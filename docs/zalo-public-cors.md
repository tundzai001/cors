# Public CORS Cục command and per-chat pause

`/cors` and `/cors cục đang thế nào` work in private chats and groups without
sign-in, linking or OTP. Mention the bot before the command when required by
Zalo. The command uses the same cached website-health probe as the map and
reports online, a service error, or unknown with a Vietnam-local check time.
It does not attest that individual stations are broadcasting RTCM.

First-time callers are recorded as `cors_public` chat bindings so an admin can
identify and pause them. This type grants no dashboard permissions, station
access or automatic internal operational alerts. Existing bindings retain their role
and scope; registering a public chat does not overwrite an existing binding.
The string column already supports the new value, so no schema migration is
required. Existing protected commands keep their authorization checks.

In Zalo Bot administration, **Dừng gửi** sets the chat binding inactive and
**Cho gửi** reactivates it. Paused chats cannot receive command replies or
automatic alerts through the shared sender. The webhook also stops dispatching
commands from paused chats. The public command never reactivates a paused
binding. Use pause to retain this policy; deleting a binding removes its record.

Focused validation:

```sh
# From cors_dashboard/backend with test database configuration:
python3 -m unittest tests.test_zalo_public_cors tests.test_reference_network
# From the repository root:
node scripts/zalo_settings_selftest.js
```

Tests mock the Zalo SDK and outbound sender. Deployment still needs the SDK and
configured bot runtime; validation sends no real Zalo messages.

## Per-chat delivery modes

Admins can select **Chỉ CORS Cục**, **Theo quyền hiện có**, or **Dừng gửi**.
The CORS-only mode is stored in notification preference scopes without replacing
the original chat binding, user, station scope or alert preferences. Switching
back restores the original authorized behavior. Both the permission resolver
and shared sender enforce CORS-only mode; internal base/rover alerts are excluded.
Public callers still require linking before they can select dashboard behavior.

**Tự báo khi Cục đổi Online / Báo lỗi** subscribes only the selected chat to
website-health changes. A separate bot task checks every 30 seconds, regardless
of the internal operational-alert toggle. The first successful observation sets
a baseline silently; later online/error transitions send one status message.
Unknown probe results do not trigger a false outage alert. Per-chat state survives
restarts. A row lock serializes delivery across workers; suppressed or failed
sends do not advance the saved state. Paused chats are excluded and the sender
rechecks current policy before delivery.

A loaded page without the login form counts as an error. Network/TLS failures
remain unknown because the page could not be inspected. This same rule drives
map status, `/cors` replies and automatic CORS alerts. No live Zalo delivery was
performed during development validation.


## Contextual measurement replies

Public answers now distinguish a short status request, a province measurement
question, an ionosphere detail request, and FLOAT diagnosis. FLOAT guidance
branches on service availability and geomagnetic storms; it asks for correction
age, satellites and baseline distance rather than repeating a generic FIX check.
Future questions explicitly describe the observations as current, not a forecast.

NOAA GloTEC regional estimates are usable for up to 90 minutes, matching the
existing service freshness limit. Observations older than 30 minutes, or returned
from fallback cache, are labelled delayed with their observation time. Older or
invalid observations are omitted from the current regional evidence. Observation
age is separate from field risk: a delayed model alone does not produce a caution
or postponement recommendation. Quiet Kp replies are explicitly limited to
source availability and geomagnetic conditions, not overall RTK suitability.
Fetch failures no longer mutate the original cached GeoJSON metadata.

A shared five-minute cache fetches NOAA's estimated planetary Kp from
`https://services.swpc.noaa.gov/json/planetary_k_index_1m.json`. Records must be
within 30 minutes of UTC now and Kp must be finite within 0–9. Failed requests
return no index, with a one-minute retry cooldown; they never reuse an old quiet
index. Data requests run concurrently, and existing paused-chat and Base access
controls still apply. No Zalo traffic is sent by the tests.

Operational guidance uses Kp >=4 for caution and >=7 for recommending postponing
high-accuracy work. These are application recommendations, not NOAA guarantees
of RTK performance. GloTEC TEC alone does not measure scintillation, receiver
accuracy, or the availability of a particular station's RTCM. The model's coarse
regional cells remain labelled estimates. Public replies never show the probe
URL, login form details or internal Base information.

Sources: [NOAA GloTEC](https://www.spaceweather.gov/products/glotec) and
[NOAA space weather scales](https://www.spaceweather.gov/noaa-scales-explanation).


Short questions now receive a direct answer with compact evidence; only detail
requests include the full TEC comparison. A bare province name is a public lookup,
including a follow-up such as “còn Hà Nội”. The last province is remembered for
20 minutes per chat and sender, with a maximum of 512 process-local entries. A
follow-up measurement/FLOAT question reuses that province; `/cors` or a national
question resets it. Revoking a chat clears this context. This memory grants no
Base permissions and does not share one group member's location with another.
