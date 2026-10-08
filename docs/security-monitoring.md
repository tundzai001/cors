# Security monitoring

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
