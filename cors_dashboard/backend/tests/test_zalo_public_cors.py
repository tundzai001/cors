from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import models, zalo_assistant, zalo_bot_service as bot_module


class PublicCorsTests(unittest.IsolatedAsyncioTestCase):
    def update(self, text="/cors cục đang thế nào", chat_type="group"):
        return SimpleNamespace(message=SimpleNamespace(text=text, chat_id="test-chat",
            chat=SimpleNamespace(id="test-chat", type=chat_type, title="Test group"),
            from_user=SimpleNamespace(id="test-sender"), reply_text=AsyncMock()))

    async def test_public_reply_reports_online_error_and_unknown_without_linking(self):
        service = bot_module.ZaloBotService()
        for state, expected in (("online", "đang online"), ("offline", "đang báo lỗi"), ("unknown", "Chưa kiểm tra được")):
            update = self.update()
            with patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=True)), patch(
                "app.reference_network.get_network_status", AsyncMock(return_value={"status": state, "checked_at": 1792244308})
            ):
                await service._cors_cmd(update, None)
            text = update.message.reply_text.await_args.args[0]
            self.assertIn(expected, text)
            self.assertIn("RTCM từng trạm", text)

    async def test_paused_caller_does_not_probe_or_reply(self):
        service, update = bot_module.ZaloBotService(), self.update()
        with patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=False)), patch(
            "app.reference_network.get_network_status", AsyncMock()) as probe:
            await service._cors_cmd(update, None)
        probe.assert_not_awaited()
        update.message.reply_text.assert_not_awaited()

    async def test_webhook_allows_cors_in_unlinked_group_but_not_admin_commands(self):
        for command, expected in (("/cors cục đang thế nào", "dispatched"), ("/status", "rejected_unlinked_group")):
            service, update = bot_module.ZaloBotService(), self.update(command)
            service._hydrate_conversation_state = AsyncMock(return_value=None)
            service.app = SimpleNamespace(bot=object(), process_update=AsyncMock())
            principal = zalo_assistant.ZaloPrincipal("test-chat", "test-sender", "group", None, None, None, None, frozenset(), False)
            with patch.object(bot_module, "ZALO_AVAILABLE", True), patch.object(bot_module.settings, "ENABLE_ZALO_BOT", True), patch.object(
                bot_module, "Update", SimpleNamespace(de_json=lambda payload, bot: update), create=True
            ), patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=False)), patch.object(
                bot_module.zalo_repository, "resolve_principal", AsyncMock(return_value=principal)
            ):
                result = await service.process_webhook_payload({"message": {"text": command}})
            self.assertEqual(result["status"], expected)
            self.assertEqual(service.app.process_update.await_count, int(expected == "dispatched"))

    async def test_outbound_pause_blocks_all_messages_and_resume_sends(self):
        service = bot_module.ZaloBotService()
        send = AsyncMock(return_value={"ok": True})
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        service._install_send_trace()
        with patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=True)):
            await service.app.bot.send_message(chat_id="test-chat", text="alert")
        send.assert_not_awaited()
        with patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=False)):
            await service.app.bot.send_message(chat_id="test-chat", text="reply")
        send.assert_awaited_once()

    async def test_public_chat_is_remembered_without_permissions_and_never_unmutes(self):
        engine = create_engine("sqlite:///:memory:")
        for table in (models.User.__table__, models.ZaloChatBinding.__table__):
            table.create(engine)
        try:
            with Session(engine, expire_on_commit=False) as store:
                db = SimpleNamespace(execute=AsyncMock(side_effect=store.execute), commit=AsyncMock(side_effect=store.commit))
                @asynccontextmanager
                async def scope():
                    yield db
                repo = zalo_assistant.ZaloAssistantRepository()
                with patch.object(zalo_assistant, "auth_session_scope", scope):
                    self.assertTrue(await repo.remember_cors_chat("public-group", "group", "Public group"))
                    principal = await repo.resolve_principal("public-group", "member", "group")
                    self.assertFalse(principal.is_linked)
                    self.assertFalse(principal.permissions)
                    binding = store.query(models.ZaloChatBinding).one()
                    self.assertEqual(binding.station_scope_mode, "none")
                    binding.is_active = False
                    store.commit()
                    self.assertFalse(await repo.remember_cors_chat("public-group", "group"))
                    self.assertTrue(await repo.is_chat_paused("public-group"))
                    self.assertEqual(store.query(models.ZaloChatBinding).count(), 1)
        finally:
            engine.dispose()
