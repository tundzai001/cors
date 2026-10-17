# Security monitoring

The same session card has an Active view and a History view. History includes
expired and revoked sessions, sorted by login time and ID descending. The
`history_page` query selects server-side pages of 20 rows; zero or an omitted
value keeps the active-only feed. Revoked and expired rows show their state
without live session controls. These are recorded login sessions, not failed
login attempts or a per-request activity log.

The session table reports recent authenticated activity, not all unexpired login
tokens or WebSocket counts. Visible authenticated pages send a heartbeat every
15 seconds. Sessions with no authenticated activity for 45 seconds disappear
from the table without revoking the login. Background tabs stop sending presence
heartbeats; returning to the page or recovering connectivity sends one immediately.
Multiple tabs using the same login session still occupy one database row.

Deploy the backend and frontend together, then reload existing browser pages so
they start sending heartbeats. Stored timestamps from the earlier timezone bug
are not shifted or guessed: a fresh authenticated request replaces them.

Presence regression checks: `node scripts/session_presence_selftest.js` and
`python3 -m unittest tests.test_session_control` from `cors_dashboard/backend`.

The traffic chart counts HTTP requests entering `MonitoringMiddleware`, including denied requests and dashboard polling. Each point represents one completed second; an idle second is zero. MQTT messages, WebSocket frames, and RTCM bytes are not HTTP requests and are not included. The IP counter covers HTTP callers seen in the last five minutes (up to 10,000 tracked callers per process).

The security snapshot combines database login/admin blocks, Security Gate bans from Redis (or its fallback), and temporary HTTP rate limits. Overlapping sources count each IP once; permanent bans take precedence. Remaining time is recalculated at read time, including for cached database snapshots.

Recent block history retains up to 200 event groups for one hour in the current backend process. Repeated denials with the same IP/source/reason within 60 seconds update the count and last-seen time. An event stays in history after the temporary block expires. This history and traffic counters reset on restart and are not aggregated across multiple backend workers. Existing persistent bans retain their storage behavior.

Unblock clears the database block, Redis/fallback ban, HTTP limit counter, and Security Gate violation score. It does not erase recent history. This update changes observability and unblock consistency; it does not change configured blocking thresholds.

Deploy the backend and built frontend, then restart the backend and refresh Security. No Alembic migration is required.

Focused checks:

```sh
python3 -m unittest cors_dashboard.backend.tests.test_security_observability
node scripts/security_dashboard_selftest.js
```
