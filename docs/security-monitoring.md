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


## Blocking before Python with Nginx (Windows)

The admin application block returns HTTP 403 after a request reaches Python. An
Nginx block closes the client connection with 444 in the CORS virtual host before
proxying to Python. Nginx still receives the TCP/TLS connection and request headers;
this is not a claim that packets stop reaching the host. Filtering traffic before
the host requires an upstream network firewall. Existing upgraded WebSockets are
not terminated by a graceful reload.

Install once on the Windows server, from the deployed repository root:

```powershell
python scripts/configure_nginx_security.py --prefix "C:/nginx-1.28.0" --backend "C:/inetpub/wwwroot/cors/cors_dashboard/backend"
```

The installer backs up `conf/nginx.conf`, provisions the generated IP include,
adds the geo lookup in `http` and a 444 guard only to the HTTP/HTTPS
`base.aitogy.com` servers, tests the configuration and requests a reload. Other
virtual hosts retain their configuration. On test/reload failure the original
configuration is restored. It sets `NGINX_SECURITY_PREFIX` and
`BACKEND_HOST=127.0.0.1` in the deployed backend's `.env`; restart `server.py`.
Loopback binding prevents clients bypassing Nginx through public port 8000.
Devices must connect through the public Nginx HTTPS/WSS routes rather than 8000.

AiCaster on a separate server must also use the public proxy. Its wake endpoints
are `https://base.aitogy.com/api/ntrip/wake-station` and
`https://base.aitogy.com/api/ntrip/wake-listen`. Rebuild and deploy AiCaster after
updating these endpoints; restarting the Python backend does not update the
caster executable. Keep `BACKEND_HOST=127.0.0.1` on the backend server.

The dedicated control token is embedded in AiCaster's
`PointNav/CASTER/Configs/Driver.cs`; the backend `.env` value of
`NTRIP_CONTROL_TOKEN` must match it. Rebuild and deploy the caster when rotating
the token, update the backend `.env`, and restart both processes. The caster
sends `X-NTRIP-Control-Token` over HTTPS and needs no local `.env` file.
Without a token, the backend only accepts the actual source IP of a configured
caster. Check that `CASTER_HOST` resolves to that server's outbound public IP and
that the local proxy preserves the client IP. Do not disable control authentication.
The caster logs HTTP failures and connection failures without printing the token.

New Security Gate permanent bans are persisted with creation time, violation
count and last path. Manual bans include the administrator in the reason. The
shared permanent-ban method also updates the HTTP gate. Startup restores database
permanent bans into the application gate, including when Redis is disabled.
Legacy Redis-only bans without an original timestamp remain labelled unknown;
no timestamp is invented for them.

A ten-second background sync exports permanent database/application bans to
`conf/cors-blocked-ips.conf`, writes it atomically, runs `nginx -t`, and requests
`nginx -s reload` only when the content changes. Unblocking removes the IP on the
next sync. Temporary login/HTTP rate limits retain their existing application
expiry behavior and are not exported to Nginx. Exact IPv4/IPv6 addresses only;
invalid values and loopback/unspecified addresses fail publication. A failed
command restores the previous include and reports a sync error; application
blocks remain in effect. No subprocess command uses a shell.

The dashboard shows the ban start, permanent expiry policy, reason and whether a
Nginx reload has been requested. A successful reload command only confirms that
the signal was sent, not remote enforcement. Validate on deployment from a test
client IP: block it, wait for sync, confirm its connection closes and its requests
no longer reach backend logs; verify an unrelated client and unblock/retry. Do
not use an untrusted forwarded header as the Nginx geo address. This setup uses
the direct Nginx TCP peer; a future CDN requires a separately trusted real-IP
configuration.

Focused checks:

```sh
# From cors_dashboard/backend with test database configuration:
python3 -m unittest tests.test_nginx_security tests.test_security_observability
# From repository root:
node scripts/security_dashboard_selftest.js
```

References: [Nginx 444](https://nginx.org/en/docs/http/request_processing.html),
[Nginx configuration testing/reload](https://nginx.org/en/docs/switches.html).

## First-party Analytics

Administrators view Analytics inside `/security`, under the traffic overview.
Legacy `/analytics` links redirect to `/security#traffic`. The 7/28/90-day
report includes today and uses calendar days in Asia/Ho_Chi_Minh (UTC+7). It shows
page views, distinct browser visitors, successful login sessions, daily trends, top 20
pages, browser/device/OS breakdowns, and visitors active in the last 30 minutes.
Breakdown percentages use page views. The realtime bars bucket each view's most
recent signal; they are not a historical count of everyone active each minute.

The tracker runs on the public SPA and shared authenticated pages. It records
initial loads and URL changes, strips queries and fragments except `/?view=map`,
and sends a heartbeat every 30 seconds while visible. A UUID stored in localStorage
identifies a browser profile; it does not identify a person or signed-in account.
Clearing storage, another browser, private mode, browser privacy controls, bots
without JavaScript and blocked signals affect counts. Storage unavailable means
collection is skipped. Known bot User-Agents are excluded, but the public endpoint
is not a bot-proof source. Analytics does not store IPs, account names, query
strings or full User-Agents. The API Keys gate is excluded; the authorized document
records the actual visit. Reloading creates a new view; heartbeats and retries of
the same view UUID do not. A failed initial write retries on the next signal.

`analytics_page_views` lives in the auth database and survives backend restarts,
with shared results across workers. The existing startup compatibility check adds
the table and indexes idempotently. Alembic revision `20261028_analytics` provides
the same additive upgrade. Deploy backend and built frontend together and restart.
Existing login/session data is unchanged. To roll back the application, leave this
additive table in place; the previous application ignores it. Alembic downgrade
removes analytics data and requires an explicit decision to discard those records.
No automatic retention/deletion job is installed. Historical Google Analytics
data is not imported; collection starts with this deployment.

Brave detection uses client hints or `navigator.brave.isBrave()` before login and
API requests. Its display hint survives backend normalization and a heartbeat
updates existing sessions after refreshing the page. Detection failure falls back
to User-Agent; legacy sessions without a Brave signal cannot be corrected by
guessing. Browser names are display information and never authorization evidence.

Focused checks:

```sh
# From cors_dashboard/backend:
python -m unittest tests.test_analytics tests.test_client_device tests.test_session_control tests.test_application_wiring
# From repository root:
node scripts/session_device_selftest.js
node scripts/session_presence_selftest.js
node scripts/analytics_tracker_selftest.js
```


### Login history and measurement coverage

The successful-login card reads `user_sessions.created_at` in the selected UTC+7
calendar window. Each saved session is one login, including sessions later revoked
or expired. It is not a unique-account count, and neither page views nor heartbeat
requests increase it. The small caption reports distinct account IDs for those
logins. Do not add activity-log rows or rotated text-log lines to this count: they
can represent the same login already stored as a session.

The chart switches between login sessions and page views. Login history predates
the first-party page tracker. Both series return `null` for days before their first
recorded event; the chart leaves these intervals blank and the data table shows
an em dash. A zero represents a measured day with no matching events. The first
recorded timestamps are displayed above the metrics so a 28-day selector cannot
imply that page traffic was measured before the tracker existed. An empty store
has no known daily coverage. The API keeps its original `totals` fields and adds
separate `logins` metadata to preserve compatibility with cached clients.
