import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import crud, models, schemas
from app.routers import users
from app.websocket import ConnectionManager


class StationScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_viewer_scope_shrinks_when_parent_loses_a_station(self):
        viewer = SimpleNamespace(parent_id=7, allowed_bases='["EDIT", "VIEW"]')
        parent = SimpleNamespace(role="coordinator", is_active=True)
        with patch.object(crud, "get_user_by_id", new=AsyncMock(return_value=parent)), \
             patch.object(crud, "get_coordinator_visible_base_serials", new=AsyncMock(return_value={"VIEW"})):
            self.assertEqual(
                await crud.get_viewer_visible_base_serials(object(), object(), viewer),
                {"VIEW"},
            )

    async def test_viewer_scope_fails_closed_when_parent_is_inactive(self):
        viewer = SimpleNamespace(parent_id=7, allowed_bases='["EDIT"]')
        parent = SimpleNamespace(role="coordinator", is_active=False)
        with patch.object(crud, "get_user_by_id", new=AsyncMock(return_value=parent)):
            self.assertEqual(await crud.get_viewer_visible_base_serials(object(), object(), viewer), set())

    async def test_child_viewer_loses_rover_when_parent_loses_station(self):
        viewer = SimpleNamespace(id=20, role="viewer", parent_id=7, allowed_rovers='["rover"]',
                                 can_view_rovers=True, can_create_rovers=True)
        rover = SimpleNamespace(username="rover", created_by_user_id=20, base_serial="EDIT", mountpoints="EDIT.MP")
        result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [rover]))
        auth_db = SimpleNamespace(execute=AsyncMock(return_value=result))
        with patch.object(crud, "_viewer_rover_scope", new=AsyncMock(return_value=({"VIEW"}, {"VIEW.MP"}))):
            self.assertEqual(await crud.get_visible_rovers_for_user(auth_db, object(), viewer), [])
            self.assertFalse(await crud.check_rover_management_permission(rover, viewer, object(), auth_db))

    async def test_rover_mountpoints_must_match_selected_station(self):
        device = SimpleNamespace(service_config={"server1_enabled": True, "mountpoint1": "VIEW.MP"})
        result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [device]))
        db = SimpleNamespace(execute=AsyncMock(return_value=result))
        viewer = SimpleNamespace(role="viewer", extra_mountpoints=None)
        self.assertTrue(await crud.rover_mountpoints_within_scope(db, viewer, ["VIEW"], "view.mp"))
        self.assertFalse(await crud.rover_mountpoints_within_scope(db, viewer, ["VIEW"], "EDIT.MP"))

    async def test_notification_includes_children_of_affected_coordinators(self):
        result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [20, 21]))
        db = SimpleNamespace(execute=AsyncMock(return_value=result))
        with patch.object(users.ui_manager, "broadcast", new=AsyncMock()) as broadcast:
            await users._notify_station_scope_changed(db, {7})
        notified = {call.args[0]["data"]["user_id"] for call in broadcast.await_args_list}
        self.assertEqual(notified, {7, 20, 21})

    async def test_admin_create_saves_edit_view_and_create_users_permission(self):
        created = models.User(id=10, username="coord", hashed_password="hash", role="coordinator",
                              is_active=True, can_view_rovers=False, can_create_rovers=False,
                              can_create_users=True, view_only_bases='["VIEW"]', created_at=1)
        device = SimpleNamespace(user_id=7)
        auth_db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: []))))
        devices_db = SimpleNamespace(commit=AsyncMock())
        data = schemas.UserCreate(username="coord", password="123456", role="coordinator",
                                  assigned_devices=["EDIT"], view_only_bases=["VIEW"], can_create_users=True)
        with patch.object(crud, "get_user_by_username", new=AsyncMock(return_value=None)), \
             patch.object(crud, "create_user", new=AsyncMock(return_value=created)) as create, \
             patch.object(crud, "get_device_by_serial", new=AsyncMock(return_value=device)), \
             patch.object(crud, "log_system_event", new=AsyncMock()), \
             patch.object(users.auth, "get_password_hash", new=AsyncMock(return_value="hash")), \
             patch.object(users.auth, "get_user_permissions", return_value=[]), \
             patch.object(users.ui_manager, "broadcast", new=AsyncMock()):
            response = await users.create_user(data, auth_db, devices_db,
                                               SimpleNamespace(id=1, username="admin", role="admin"))
        self.assertTrue(create.await_args.kwargs["can_create_users"])
        self.assertEqual(create.await_args.kwargs["view_only_bases"], ["VIEW"])
        self.assertEqual(device.user_id, 10)
        self.assertTrue(response.can_create_users)

    def test_assignment_notification_invalidates_websocket_scope(self):
        manager = ConnectionManager()
        socket = object()
        manager.connection_contexts[socket] = {"role": "viewer", "user_id": 20, "allowed_serials": {"EDIT"}}
        self.assertFalse(manager._can_receive(socket, {"type": "user_assignment_updated", "data": {"user_id": 21}}))
        self.assertTrue(manager._can_receive(socket, {"type": "user_assignment_updated", "data": {"user_id": 20}}))
        self.assertFalse(manager._can_receive(socket, {"type": "status_update", "data": {"serial": "EDIT"}}))


if __name__ == "__main__":
    unittest.main()
