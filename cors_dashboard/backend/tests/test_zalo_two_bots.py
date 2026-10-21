"""Two bots must never share authorization, workflow state or deliveries."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models, zalo_assistant, zalo_bot_service as bots, zalo_routes


class TwoBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_hydrates_own_password_workflow_before_logging(self):
        service = bots.ZaloBotService("tb")
        workflow = {"flow": "rover_create", "step": "password", "data": {}}
        payload = {"message": {"chat": {"id": "42"}, "from": {"id": "member"}, "text": "private-password"}}
        with patch.object(bots.zalo_repository, "load_workflow", AsyncMock(return_value=workflow)) as load, patch.object(
            bots, "ZALO_AVAILABLE", False
        ):
            with self.assertRaises(RuntimeError):
                await service.process_webhook_payload(payload)
        load.assert_awaited_once_with("tb:42", "member")
        event = service.get_debug_events()["events"][0]
        self.assertTrue(event["sensitive_input"])
        self.assertEqual(event["summary"]["text"], "<redacted>")
        self.assertNotIn("private-password", str(event))

    async def test_sdk_replies_and_background_sends_use_own_policy_and_raw_id(self):
        for key, stored in (("tun", "42"), ("tb", "tb:42")):
            service = bots.ZaloBotService(key)
            send = AsyncMock(return_value={"ok": True})
            service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
            service._install_send_trace()
            policy = AsyncMock(return_value={"active": True, "mode": "standard"})
            with patch.object(bots.zalo_repository, "chat_delivery_policy", policy):
                await service.app.bot.send_message(chat_id="42", text="SDK reply")
                await service.app.bot.send_message(stored, "background alert")
            self.assertEqual([call.args[0] for call in policy.await_args_list], [stored, stored])
            self.assertEqual(send.await_args_list[0].kwargs["chat_id"], "42")
            self.assertEqual(send.await_args_list[1].args[0], "42")
            update = SimpleNamespace(message=SimpleNamespace(chat_id="42", from_user=SimpleNamespace(id="42")))
            self.assertEqual(service._get_chat_id(update), stored)
            self.assertEqual(service._get_chat_type(update), "private")
        with self.assertRaises(ValueError):
            await self._reject_other_bot()

    async def _reject_other_bot(self):
        service = bots.ZaloBotService()
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        service._install_send_trace()
        await service.app.bot.send_message(chat_id="tb:42", text="wrong bot")

    async def test_same_chat_has_separate_permissions_workflows_and_notification_reservations(self):
        engine = create_engine("sqlite:///:memory:")
        for model in (models.User, models.ZaloChatBinding, models.ZaloNotificationPreference,
                      models.ZaloWorkflowState, models.ZaloDeliveryLog):
            model.__table__.create(engine)
        self.addCleanup(engine.dispose)
        with Session(engine, expire_on_commit=False) as store:
            for ident, chat_id, role in ((1, "42", "group_admin"), (2, "tb:42", "group_public")):
                store.add(models.ZaloChatBinding(id=ident, chat_id=chat_id, chat_type="group",
                    binding_type=role, created_at=1, last_seen_at=1, is_active=True,
                    station_scope_mode="all", allowed_bases=[]))
                store.add(models.ZaloNotificationPreference(binding_id=ident, enabled=True,
                    min_severity="critical", scopes={}, updated_at=1))
            store.commit()
            db = SimpleNamespace(execute=AsyncMock(side_effect=store.execute),
                commit=AsyncMock(side_effect=store.commit), add=store.add)
            @asynccontextmanager
            async def scope():
                yield db
            repo = zalo_assistant.ZaloAssistantRepository()
            with patch.object(zalo_assistant, "auth_session_scope", scope):
                tun = await repo.resolve_principal("42", "member", "group")
                tb = await repo.resolve_principal("tb:42", "member", "group")
                self.assertTrue(tun.is_admin)
                self.assertFalse(tb.is_admin)
                await repo.save_workflow("42", "member", "config_set", "1", {"bot": "tun"})
                await repo.save_workflow("tb:42", "member", "config_set", "2", {"bot": "tb"})
                self.assertEqual((await repo.load_workflow("42", "member"))["data"]["bot"], "tun")
                self.assertEqual((await repo.load_workflow("tb:42", "member"))["data"]["bot"], "tb")
                tun_targets = await repo.reserve_notification_targets("event", "critical", set(), bot_key="tun")
                tb_targets = await repo.reserve_notification_targets("event", "critical", set(), bot_key="tb")
                self.assertEqual([target["chat_id"] for target in tun_targets], ["42"])
                self.assertEqual([target["chat_id"] for target in tb_targets], ["tb:42"])
                self.assertEqual(len(store.execute(select(models.ZaloDeliveryLog)).scalars().all()), 2)
            rows = store.execute(select(models.ZaloChatBinding).order_by(models.ZaloChatBinding.id)).scalars().all()
            self.assertEqual([repo.binding_dict(row)["bot_key"] for row in rows], ["tun", "tb"])
            self.assertEqual([repo.binding_dict(row)["chat_id"] for row in rows], ["42", "42"])

    async def test_cors_change_alerts_only_visit_own_subscriptions(self):
        for key, expected in (("tun", 1), ("tb", 2)):
            service = bots.ZaloBotService(key)
            service.app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=True)))
            visited = []
            @asynccontextmanager
            async def transition(binding_id, status):
                visited.append(binding_id)
                yield {"send": True, "sent": False, "policy": {}}
            with patch.object(bots.zalo_repository, "cors_subscriptions", AsyncMock(return_value=[
                {"id": 1, "chat_id": "42"}, {"id": 2, "chat_id": "tb:42"}
            ])), patch.object(bots.zalo_repository, "cors_transition", transition), patch(
                "app.reference_network.get_network_status", AsyncMock(return_value={"status": "offline"})
            ):
                await service._notify_cors_changes_once()
            self.assertEqual(visited, [expected])

    async def test_start_stop_both_and_reject_duplicate_tokens(self):
        services = {key: SimpleNamespace(_bot_setting=lambda suffix, key=key: key,
            start=AsyncMock(), stop=AsyncMock()) for key in ("tun", "tb")}
        with patch.object(bots, "zalo_bot_services", services):
            await bots.start_zalo_bots(strict=True)
            await bots.stop_zalo_bots()
            for service in services.values():
                service.start.assert_awaited_once_with(strict=True)
                service.stop.assert_awaited_once()
            services["tb"]._bot_setting = lambda suffix: "tun"
            with self.assertRaises(RuntimeError):
                await bots.start_zalo_bots(strict=True)

    def test_webhook_uses_matching_bot_and_secret(self):
        app = FastAPI()
        app.include_router(zalo_routes.router)
        with TestClient(app) as client, patch.object(zalo_routes.settings, "ENABLE_ZALO_BOT", True), patch.object(
            zalo_routes.settings, "ZALO_WEBHOOK_SECRET", "tun-secret"
        ), patch.object(zalo_routes.settings, "ZALO_TB_WEBHOOK_SECRET", "tb-secret"), patch.object(
            bots.zalo_bot_services["tb"], "process_webhook_payload", AsyncMock(return_value={"trace_id": "tb", "outbound_count": 0})
        ) as tb, patch.object(bots.zalo_bot_service, "process_webhook_payload", AsyncMock()) as tun:
            self.assertEqual(client.post("/api/zalo/webhook/tb", json={}, headers={"X-Bot-Api-Secret-Token": "tun-secret"}).status_code, 401)
            self.assertEqual(client.post("/api/zalo/webhook/tb", json={}, headers={"X-Bot-Api-Secret-Token": "tb-secret"}).status_code, 200)
            self.assertEqual(client.post("/api/zalo/webhook/unknown", json={}).status_code, 404)
            tb.assert_awaited_once_with({})
            tun.assert_not_awaited()
