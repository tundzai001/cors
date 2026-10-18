from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import models, zalo_assistant, zalo_bot_service as bot_module


class PublicCorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value={}))
        patcher.start()
        self.addCleanup(patcher.stop)

        kp_patcher = patch('app.zalo_cors_report.geomagnetic_observation', AsyncMock(return_value=None))
        kp_patcher.start()
        self.addCleanup(kp_patcher.stop)

    async def test_cors_only_sender_blocks_other_content(self):
        service = bot_module.ZaloBotService()
        send = AsyncMock(return_value={"ok": True})
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        service._install_send_trace()
        policy = {"active": True, "mode": "cors_only", "cors_notify_changes": True}
        with patch.object(bot_module.zalo_repository, "chat_delivery_policy", AsyncMock(return_value=policy)):
            await service.app.bot.send_message(chat_id="chat", text="base alert")
            send.assert_not_awaited()
            token = service._cors_outbound.set("reply")
            try:
                await service.app.bot.send_message(chat_id="chat", text="CORS Cục status")
            finally:
                service._cors_outbound.reset(token)
        send.assert_awaited_once()

        token = service._cors_outbound.set({"kind":"alert", "policy":policy})
        try:
            with patch.object(bot_module.zalo_repository, "chat_delivery_policy", AsyncMock(side_effect=AssertionError("Nested auth session"))):
                await service.app.bot.send_message(chat_id="chat", text="CORS Cục changed")
        finally:
            service._cors_outbound.reset(token)
        self.assertEqual(send.await_count, 2, "Locked change delivery reuses its policy without another database session")

    async def test_automatic_cors_alerts_only_on_changes_and_ignore_unknown(self):
        service = bot_module.ZaloBotService()
        send = AsyncMock(return_value={"ok": True})
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        saved = {"status": None}
        @asynccontextmanager
        async def transition(binding_id, status):
            delivery = {"send": saved["status"] is not None and saved["status"] != status, "sent": False, "policy": {"active":True,"mode":"cors_only","cors_notify_changes":True}}
            yield delivery
            if saved["status"] is None or delivery["sent"]:
                saved["status"] = status
        async def targets():
            return [{"id":1, "chat_id":"chat", "last_status":saved["status"]}]
        with patch.object(bot_module.zalo_repository, "cors_subscriptions", AsyncMock(side_effect=targets)), patch.object(
            bot_module.zalo_repository, "cors_transition", transition
        ):
            for status in ("online", "online", "offline", "unknown", "offline", "online"):
                with patch("app.reference_network.get_network_status", AsyncMock(return_value={"status":status,"checked_at":1792244308})):
                    await service._notify_cors_changes_once()
        self.assertEqual(send.await_count, 2)
        self.assertIn("đang gián đoạn", send.await_args_list[0].kwargs["text"])
        self.assertIn("đang hoạt động", send.await_args_list[1].kwargs["text"])

    async def test_failed_or_suppressed_cors_alert_does_not_advance_saved_state(self):
        service = bot_module.ZaloBotService()
        send = AsyncMock(side_effect=[RuntimeError('send failed'), None])
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        deliveries = []
        @asynccontextmanager
        async def transition(binding_id, status):
            delivery = {"send": True, "sent": False, "policy": {"active":True,"mode":"cors_only","cors_notify_changes":True}}
            deliveries.append(delivery)
            yield delivery
        with patch.object(bot_module.zalo_repository, "cors_subscriptions", AsyncMock(return_value=[{"id":1,"chat_id":"chat","last_status":"online"}])), patch.object(
            bot_module.zalo_repository, "cors_transition", transition
        ), patch("app.reference_network.get_network_status", AsyncMock(return_value={"status":"offline","checked_at":1792244308})):
            await service._notify_cors_changes_once()
            await service._notify_cors_changes_once()
        self.assertTrue(all(not delivery["sent"] for delivery in deliveries))

    async def test_cors_only_preserves_and_restores_original_admin_scope(self):
        engine = create_engine("sqlite:///:memory:")
        for table in (models.User.__table__, models.ZaloChatBinding.__table__, models.ZaloNotificationPreference.__table__):
            table.create(engine)
        try:
            with Session(engine, expire_on_commit=False) as store:
                user = models.User(id=1, username="admin", role="admin", hashed_password="unused", created_at=1, is_active=True)
                binding = models.ZaloChatBinding(id=1, chat_id="admin-chat", chat_type="private", binding_type="private", user_id=1, sender_id="sender", created_at=1, last_seen_at=1, is_active=True)
                pref = models.ZaloNotificationPreference(binding_id=1, enabled=True, min_severity="critical", scopes={"mode":"selected","values":["BASE1"]}, updated_at=1)
                store.add_all([user, binding, pref]); store.commit()
                db = SimpleNamespace(execute=AsyncMock(side_effect=store.execute), commit=AsyncMock(side_effect=store.commit), get=AsyncMock(side_effect=store.get), refresh=AsyncMock(side_effect=store.refresh), add=store.add)
                @asynccontextmanager
                async def scope():
                    yield db
                repo = zalo_assistant.ZaloAssistantRepository()
                with patch.object(zalo_assistant, "auth_session_scope", scope):
                    await repo.update_binding_admin(1, {"delivery_mode":"cors_only","cors_notify_changes":True}, 1, True)
                    self.assertEqual(binding.binding_type, "private")
                    self.assertEqual(binding.user_id, 1)
                    self.assertTrue(pref.enabled)
                    self.assertEqual(pref.scopes["values"], ["BASE1"])
                    principal = await repo.resolve_principal("admin-chat", "sender", "private")
                    self.assertFalse(principal.permissions)
                    self.assertEqual(len(await repo.cors_subscriptions()), 1)
                    async with repo.cors_transition(1, "online") as delivery:
                        self.assertFalse(delivery["send"])
                    self.assertEqual(pref.scopes["cors_last_status"], "online")
                    async with repo.cors_transition(1, "offline") as delivery:
                        self.assertTrue(delivery["send"])
                    self.assertEqual(pref.scopes["cors_last_status"], "online", "Suppressed delivery keeps the previous state")
                    async with repo.cors_transition(1, "offline") as delivery:
                        delivery["sent"] = True
                    self.assertEqual(pref.scopes["cors_last_status"], "offline")
                    async with repo.cors_transition(1, "offline") as delivery:
                        self.assertFalse(delivery["send"])
                    binding.is_active = False
                    store.commit()
                    self.assertEqual(await repo.cors_subscriptions(), [])
                    binding.is_active = True
                    store.commit()
                    await repo.update_binding_admin(1, {"delivery_mode":"standard"}, 1, True)
                    self.assertEqual(await repo.cors_subscriptions(), [])
                    principal = await repo.resolve_principal("admin-chat", "sender", "private")
                    self.assertTrue(principal.is_admin)
        finally:
            engine.dispose()

    def update(self, text="/cors cục đang thế nào", chat_type="group"):
        return SimpleNamespace(message=SimpleNamespace(text=text, chat_id="test-chat",
            chat=SimpleNamespace(id="test-chat", type=chat_type, title="Test group"),
            from_user=SimpleNamespace(id="test-sender"), reply_text=AsyncMock()))

    async def test_public_reply_reports_online_error_and_unknown_without_linking(self):
        service = bot_module.ZaloBotService()
        for state, expected in (("online", "đang hoạt động"), ("offline", "đang gián đoạn"), ("unknown", "Chưa xác định được")):
            update = self.update()
            with patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=True)), patch(
                "app.reference_network.get_network_status", AsyncMock(return_value={"status": state, "checked_at": 1792244308})
            ):
                await service._cors_cmd(update, None)
            text = update.message.reply_text.await_args.args[0]
            self.assertIn(expected, text)
            self.assertIn("Tầng điện ly", text)
            for hidden in ("đăng nhập", "form", "14.238", "trang dịch vụ"):
                self.assertNotIn(hidden, text)

    async def test_paused_caller_does_not_probe_or_reply(self):
        service, update = bot_module.ZaloBotService(), self.update()
        with patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=False)), patch(
            "app.reference_network.get_network_status", AsyncMock()) as probe:
            await service._cors_cmd(update, None)
        probe.assert_not_awaited()
        update.message.reply_text.assert_not_awaited()

    async def test_webhook_public_calls_reply_without_sdk_dispatch_or_base_permissions(self):
        cases = ("/cors", "/cors cục đang thế nào", "@Bot Cors manager /cors",
                 "@Bot Cors manager /cors cục đang thế nào", "/cors@Bot", "/CORS",
                 "\u200b/cors", "/cors\u200b cục", "@Bot Cors manager helo", "tình trạng Cục",
                 "@Bot Cors manager hà nội đo được ko", "ở Tuyên Quang đo được k", "tầng điện ly hôm nay", "corsc cục hnay đo được k mọi người", "cors cuc hnay do dc k mn",
                 "cuc sap chua", "CORS Cục đang thế nào?", "kiểm tra VNGEONET",
                 "Cục còn sống không", "14.238.1.125 đang online không?")
        for text in cases:
            with self.subTest(text=text):
                service = bot_module.ZaloBotService()
                service._hydrate_conversation_state = AsyncMock(return_value=None)
                service.app = SimpleNamespace(bot=object(), process_update=AsyncMock())
                principal = zalo_assistant.ZaloPrincipal("test-chat", "test-sender", "group", None, None, None, None, frozenset(), False)
                updates = []
                def parse(payload, bot):
                    update = self.update(payload["message"]["text"])
                    updates.append(update)
                    return update
                with patch.object(bot_module, "ZALO_AVAILABLE", True), patch.object(bot_module.settings, "ENABLE_ZALO_BOT", True), patch.object(
                    bot_module, "Update", SimpleNamespace(de_json=parse), create=True
                ), patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=False)), patch.object(
                    bot_module.zalo_repository, "resolve_principal", AsyncMock(return_value=principal)
                ), patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=True)) as remember, patch(
                    "app.reference_network.get_network_status", AsyncMock(return_value={"status":"online","checked_at":1792244308})
                ):
                    result = await service.process_webhook_payload({"message": {"text": text}})
                self.assertEqual(result["status"], "dispatched")
                service.app.process_update.assert_not_awaited()
                remember.assert_awaited_once_with("test-chat", "group", "Test group")
                reply = updates[0].message.reply_text
                reply.assert_awaited_once()
                self.assertIn("đang hoạt động", reply.await_args.args[0])
                self.assertNotIn("/register", reply.await_args.args[0])

    async def test_natural_cors_questions_use_same_handler_in_chat_dispatch(self):
        service = bot_module.ZaloBotService()
        with patch.object(service, "_cors_cmd", AsyncMock()) as handler:
            for text in ("tình trạng cục", "cuc loi khong", "status Cục", "Cục chạy chưa", "Cục sao rồi", "cục dùng được không", "ở Tuyên Quang máy cứ FLOAT", "địa từ hôm nay thế nào", "khó FIX quá", "Hà Nội", "còn Tuyên Quang"):
                self.assertTrue(await service._try_dispatch_intent(self.update(text), text))
                self.assertEqual(service._detect_natural_language_intent(text), ("cors", []))
            self.assertEqual(handler.await_count, 11)
        for text in ("base đang thế nào", "rover online không", "rover Cục đang lỗi", "xóa rover cuc", "/station cuc", "@Bot Cors manager kiểm tra base Hà Nội"):
            self.assertFalse(bot_module._is_cors_status_question(text), text)

    async def test_public_menu_only_lists_public_health_and_passes_cors_delivery_policy(self):
        for command in ("/menu", "@Bot Cors manager /menu", "/help", "/start"):
            with self.subTest(command=command):
                service = bot_module.ZaloBotService()
                send = AsyncMock(return_value={"ok":True})
                service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send), process_update=AsyncMock())
                service._install_send_trace()
                service._hydrate_conversation_state = AsyncMock(return_value=None)
                principal = zalo_assistant.ZaloPrincipal("test-chat", "test-sender", "group", None, None, None, None, frozenset(), False)
                def parse(payload, bot):
                    update = self.update(payload["message"]["text"])
                    update.message.reply_text = lambda text: bot.send_message(chat_id="test-chat", text=text)
                    return update
                with patch.object(bot_module, "ZALO_AVAILABLE", True), patch.object(bot_module.settings, "ENABLE_ZALO_BOT", True), patch.object(
                    bot_module, "Update", SimpleNamespace(de_json=parse), create=True
                ), patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=False)), patch.object(
                    bot_module.zalo_repository, "resolve_principal", AsyncMock(return_value=principal)
                ), patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=True)), patch.object(
                    bot_module.zalo_repository, "chat_delivery_policy", AsyncMock(return_value={"active":True,"mode":"cors_only","cors_notify_changes":False})
                ), patch("app.reference_network.get_network_status", AsyncMock()) as probe:
                    result = await service.process_webhook_payload({"message":{"text":command}})
                self.assertEqual(result["status"], "dispatched")
                send.assert_awaited_once()
                menu = send.await_args.kwargs["text"]
                self.assertIn("MENU CÔNG KHAI", menu)
                self.assertIn("/cors", menu)
                self.assertNotIn("/register", menu)
                self.assertNotIn("/station", menu)
                self.assertNotIn("/rover", menu)
                probe.assert_not_awaited()
                service.app.process_update.assert_not_awaited()

    async def test_public_menu_handlers_work_for_unlinked_private_chat_and_respect_pause(self):
        service = bot_module.ZaloBotService()
        principal = zalo_assistant.ZaloPrincipal("test-chat", "test-sender", "private", None, None, None, None, frozenset(), False)
        with patch.object(service, "_resolve_principal", AsyncMock(return_value=principal)), patch.object(
            bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=True)
        ):
            for handler in (service._menu_cmd, service._help_cmd, service._start_cmd):
                update = self.update("/menu", "private")
                await handler(update, None)
                self.assertIn("MENU CÔNG KHAI", update.message.reply_text.await_args.args[0])
        update = self.update("/menu")
        with patch.object(bot_module.zalo_repository, "remember_cors_chat", AsyncMock(return_value=False)):
            await service._public_menu_cmd(update, None)
        update.message.reply_text.assert_not_awaited()

    async def test_webhook_still_rejects_protected_base_command_in_unlinked_group(self):
        service, update = bot_module.ZaloBotService(), self.update("/status")
        service._hydrate_conversation_state = AsyncMock(return_value=None)
        service.app = SimpleNamespace(bot=object(), process_update=AsyncMock())
        principal = zalo_assistant.ZaloPrincipal("test-chat", "test-sender", "group", None, None, None, None, frozenset(), False)
        with patch.object(bot_module, "ZALO_AVAILABLE", True), patch.object(bot_module.settings, "ENABLE_ZALO_BOT", True), patch.object(
            bot_module, "Update", SimpleNamespace(de_json=lambda payload, bot: update), create=True
        ), patch.object(bot_module.zalo_repository, "is_chat_paused", AsyncMock(return_value=False)), patch.object(
            bot_module.zalo_repository, "resolve_principal", AsyncMock(return_value=principal)
        ), patch.object(service, "_cors_cmd", AsyncMock()) as public:
            result = await service.process_webhook_payload({"message": {"text": "/status"}})
        self.assertEqual(result["status"], "rejected_unlinked_group")
        service.app.process_update.assert_not_awaited()
        public.assert_not_awaited()
        self.assertIn("CORS Cục dùng công khai", update.message.reply_text.await_args.args[0])

    async def test_outbound_pause_blocks_all_messages_and_resume_sends(self):
        service = bot_module.ZaloBotService()
        send = AsyncMock(return_value={"ok": True})
        service.app = SimpleNamespace(bot=SimpleNamespace(send_message=send))
        service._install_send_trace()
        with patch.object(bot_module.zalo_repository, "chat_delivery_policy", AsyncMock(return_value={"active":False,"mode":"standard","cors_notify_changes":False})):
            await service.app.bot.send_message(chat_id="test-chat", text="alert")
        send.assert_not_awaited()
        with patch.object(bot_module.zalo_repository, "chat_delivery_policy", AsyncMock(return_value={"active":True,"mode":"standard","cors_notify_changes":False})):
            await service.app.bot.send_message(chat_id="test-chat", text="reply")
        send.assert_awaited_once()

    async def test_public_chat_is_remembered_without_permissions_and_never_unmutes(self):
        engine = create_engine("sqlite:///:memory:")
        for table in (models.User.__table__, models.ZaloChatBinding.__table__, models.ZaloNotificationPreference.__table__):
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

    async def test_region_followups_are_scoped_to_sender_and_chat_and_expire(self):
        service = bot_module.ZaloBotService()
        async def ask(text, sender='test-sender', chat='test-chat'):
            update = self.update(text)
            update.message.from_user.id = sender
            update.message.chat_id = chat
            update.message.chat.id = chat
            await service._cors_cmd(update, None)
        with patch.object(bot_module.zalo_repository, 'remember_cors_chat', AsyncMock(return_value=True)), patch(
            'app.reference_network.get_network_status', AsyncMock(return_value={'status':'online'})
        ), patch('app.zalo_cors_report.report_text', AsyncMock(return_value='reply')) as report:
            await ask('Hà Nội')
            await ask('đo được không')
            self.assertIn('Hà Nội', report.await_args.args[1])
            await ask('đo được không', sender='another')
            self.assertNotIn('Hà Nội', report.await_args.args[1])
            await ask('đo được không', chat='another-chat')
            self.assertNotIn('Hà Nội', report.await_args.args[1])
            await ask('/cors')
            await ask('đo được không')
            self.assertNotIn('Hà Nội', report.await_args.args[1])
            await ask('Hà Nội')
            key = service._conversation_key(self.update())
            service._cors_regions[key] = ('Hà Nội', 0)
            await ask('đo được không')
            self.assertNotIn('Hà Nội', report.await_args.args[1])
            await ask('Hà Nội')
            service.forget_chat('test-chat')
            self.assertNotIn(key, service._cors_regions)
