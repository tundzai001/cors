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
