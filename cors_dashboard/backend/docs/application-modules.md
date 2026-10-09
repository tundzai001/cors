# Backend module ownership

`app.main:app` remains the server entry point. It registers routers, middleware,
exception handlers and health endpoints. Small NTRIP/integration, admin security
and license endpoints remain here. Router includes remain in their original
order; SEO routes precede SPA fallbacks, and device routes precede generic paths.

- `lifecycle.py`: startup, bot callbacks, task creation and shutdown.
- `station_runtime.py`: telemetry queues, buffers, heartbeat workers and Pi/UI WebSockets.
- `ntrip_control.py`: RTCM sessions, mountpoint lookup, wake/stop control and session workers.
- `caster_monitor.py`, `caster_status.py`, `caster_cache.py`: caster polling, status notifications and shared cache.
- `device_command_routes.py`: station configuration, reset, scoped locks and bulk/template operations.
- `operations_routes.py`, `export_routes.py`: operations, alerts, events and spreadsheet exports.
- `rover_accounts_routes.py`, `rover_tools_routes.py`, `rover_history_routes.py`: rover accounts, bulk tools and history.
- `auth_routes.py`, `zalo_routes.py`, `map_routes.py`: authentication, Zalo and map APIs.
- `public_pages.py`, `frontend_routes.py`: SEO pages, SPA responses and allowed public asset types.
- `runtime_config.py`, `station_config.py`, `station_scope.py`: shared configuration and station access rules.

Mutable queues, buffers and locks have one owner; consumers import those objects.
Feature modules do not import `main`, and importing the app does not start its lifespan.
No database migration or agent update is required for this refactor.

`tests/test_application_wiring.py` checks the pre-refactor route/middleware order
and shared object identity. Existing access-control and configuration tests still
exercise their production definitions from the modules that now own them.
