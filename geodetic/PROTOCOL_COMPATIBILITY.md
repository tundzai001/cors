# Agent and backend compatibility

The default device WebSocket endpoint is
`wss://cors.aitogy.com/ws/pi/{serial}`. Its certificate covers
`cors.aitogy.com`, not the legacy MQTT hostname `aitogy.click`.
MQTT configuration remains independent. Certificate and hostname checks remain
enabled. If an installation sets `BACKEND_WS_HOST=aitogy.click`, change that
override to `cors.aitogy.com`; the new default cannot override an environment
setting.

## Rollout

1. Deploy the backend command dispatcher and license compatibility changes
   together. Restart the backend so every command carries `command_id`,
   `issued_at` and `expires_at`.
2. Replace and restart the universal and relay agents with V2.2.6. Existing
   configuration, serial and license files stay in place.
3. Confirm fresh MQTT status and, for universal agents, a successful WebSocket
   connection. A broker publish acknowledgement confirms MQTT delivery, not
   that the backend has applied a command.

Old agents ignore extra command metadata. Updated agents reject expired,
replayed, retained or metadata-free commands; upgrading the backend first avoids
the mixed-version command rejection. Do not remove this validation to work
around an old backend. Rejection logs include timestamps to distinguish missing
metadata from clock skew without printing command credentials.

Universal V2.2.6 accepts modern `dt_` tokens and an existing legacy license only
when it matches the original serial-bound algorithm. Backend WebSocket
authentication still compares the supplied credential with the stored device
credential. MQTT fingerprint reconciliation accepts both supported formats.
Rename commands do not start GNSS services without a valid local license.

License generation and deployment reuse an existing valid credential. This
keeps older agents working and makes a resend safe to repeat. Newly provisioned
devices receive random `dt_` tokens; upgrade old universal agents before issuing
their first new token. Invalid credentials are not treated as compatible.

## Verification and rollback

Run `python3 -m unittest discover -s geodetic/tests` and the backend
`tests.test_license_sync` suite. The compatibility checks cover legacy and
modern credentials, preserved resends, default WSS configuration, command
freshness, replay rejection and MQTT-to-WebSocket fallback.

This change does not rewrite existing credentials or require a database
migration. Keep the backend envelope support if rolling an agent back to an
older version. Older universal agents can reuse their existing legacy license;
they cannot read a newly issued `dt_` credential. Avoid rotating credentials
during rollback.
