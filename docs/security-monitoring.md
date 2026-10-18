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
`cors.aitogy.com` servers, tests the configuration and requests a reload. Other
virtual hosts retain their configuration. On test/reload failure the original
configuration is restored. It sets `NGINX_SECURITY_PREFIX` and
`BACKEND_HOST=127.0.0.1` in the deployed backend's `.env`; restart `server.py`.
Loopback binding prevents clients bypassing Nginx through public port 8000.
Devices must connect through the public Nginx HTTPS/WSS routes rather than 8000.

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
