"""Refactoring preserves route priority and shared runtime state without startup."""
import json
from pathlib import Path
import unittest

from app.main import app
from app import caster_cache, caster_monitor, lifecycle, operations_routes
from app import device_command_routes, station_runtime


def registered_routes(router):
    items = []
    for route in router.routes:
        # FastAPI versions with deferred include_router expansion.
        included = getattr(route, 'original_router', None)
        if included is not None:
            items.extend(registered_routes(included))
        else:
            items.append({
                'type': type(route).__name__,
                'path': getattr(route, 'path', None),
                'methods': sorted(getattr(route, 'methods', []) or []),
                'name': getattr(route, 'name', None),
            })
    return items


class ApplicationWiringTests(unittest.TestCase):
    def test_routes_websockets_assets_and_middleware_keep_original_order(self):
        expected = json.loads(Path(__file__).with_name('application_routes.json').read_text())
        self.assertEqual(registered_routes(app.router), expected['routes'])
        actual_middleware = [[entry.cls.__name__, entry.kwargs] for entry in app.user_middleware]
        self.assertEqual(actual_middleware, expected['middleware'])

    def test_workers_commands_and_operations_share_single_state_owners(self):
        self.assertIs(lifecycle.latest_device_telemetry_cache, station_runtime.latest_device_telemetry_cache)
        self.assertIs(device_command_routes.latest_device_telemetry_cache, station_runtime.latest_device_telemetry_cache)
        self.assertIs(device_command_routes.device_update_buffer, station_runtime.device_update_buffer)
        self.assertIs(device_command_routes.device_update_buffer_lock, station_runtime.device_update_buffer_lock)
        self.assertIs(device_command_routes.pending_ui_status_updates, station_runtime.pending_ui_status_updates)
        self.assertIs(caster_monitor.global_caster_cache_lock, caster_cache.global_caster_cache_lock)
        self.assertIs(operations_routes.global_caster_cache_lock, caster_cache.global_caster_cache_lock)
        self.assertIs(lifecycle.enqueue_ui_broadcast, station_runtime.enqueue_ui_broadcast)


if __name__ == '__main__':
    unittest.main()
