"""Conversation behavior for the Zalo service."""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from ..background_tasks import schedule_background
from .. import auth
from ..zalo_assistant import zalo_repository
from .common import (_COMMAND_DISPATCH, _NLP_INTENT_PATTERNS, _SyntheticContext,
                     _is_cors_status_question, _normalize_to_ascii)

logger = logging.getLogger("app.zalo_bot_service")
NEGATIVE_ANSWER = "không"
BACK_COMMANDS = {"0", "quay lại", "quay lai", "back"}


class ConversationMixin:
    def _conversation_key(self, update: Update) -> str:
        return f"{self._get_chat_id(update)}:{self._get_sender_id(update)}"

    async def _hydrate_conversation_state(self, chat_id: Any, sender_id: Any) -> Optional[Dict[str, Any]]:
        chat_id = str(chat_id or "")
        sender_id = str(sender_id or "")
        if not chat_id or not sender_id or chat_id == "unknown" or sender_id == "unknown":
            return None
        key = f"{chat_id}:{sender_id}"
        persisted = await zalo_repository.load_workflow(chat_id, sender_id)
        if persisted:
            self._conversation_states[key] = persisted
        else:
            # DB is authoritative so a cancel handled by another worker also
            # clears stale process-local state here.
            self._conversation_states.pop(key, None)
        return persisted

    def _is_sensitive_conversation_input(self, chat_id: Any, sender_id: Any) -> bool:
        state = self._conversation_states.get(f"{chat_id}:{sender_id}")
        if not state:
            return False
        if state.get("flow") == "rover_create" and state.get("step") == "password":
            return True
        if state.get("flow") in {"config_set", "rover_update"} and state.get("step") == "value":
            field = str((state.get("data") or {}).get("current_field") or "").lower()
            return any(part in field for part in ("password", "secret", "token"))
        return False

    @staticmethod
    def _redact_payload_message(payload: Any) -> Any:
        safe_payload = copy.deepcopy(payload)
        root = safe_payload.get("result", safe_payload) if isinstance(safe_payload, dict) else None
        if isinstance(root, dict):
            for message_key in ("message", "edited_message"):
                message = root.get(message_key)
                if isinstance(message, dict) and isinstance(message.get("text"), str):
                    message["text"] = "<redacted>"
        return safe_payload

    def _clean_conversation_answer(self, update: Update, text: str) -> str:
        answer = str(text or "").strip()
        prefix = self._chat_mention_prefixes.get(self._get_chat_id(update), "").strip()
        if prefix and answer.lower().startswith(prefix.lower()):
            answer = answer[len(prefix):].strip()
        return answer

    def _schedule_conversation_expiry(self, key: str) -> None:
        async def expire_conversation() -> None:
            await asyncio.sleep(600)
            state = self._conversation_states.get(key)
            if state and state.get("expires_at", 0) <= time.time():
                self._conversation_states.pop(key, None)

        schedule_background(expire_conversation())

    async def _start_notification_onboarding(
        self,
        update: Update,
        binding_id: int,
        binding_type: str,
    ) -> None:
        key = self._conversation_key(update)
        binding = await zalo_repository.get_binding(self._get_chat_id(update))
        user = await zalo_repository.get_user(binding.user_id) if binding else None

        group_locked = binding_type in {"group_admin", "group_public"}
        user_locked = bool(user and getattr(user, "station_scope_mode", "all") == "selected")
        scope_locked = group_locked or user_locked

        if group_locked:
            mode = str(getattr(binding, "station_scope_mode", None) or "all")
            values = list(getattr(binding, "allowed_bases", None) or [])
        elif user_locked:
            mode = "selected"
            values = list(getattr(user, "allowed_bases", None) or [])
        else:
            mode = "all"
            values = []

        locked_scope = {"mode": mode, "values": values}
        workflow_data = {
            "binding_id": int(binding_id),
            "binding_type": binding_type,
            "scope_locked": scope_locked,
            "scopes": locked_scope if scope_locked else {"mode": "all"},
        }
        self._conversation_states[key] = {
            "flow": "notification_setup",
            "step": "severity",
            "data": workflow_data,
            "expires_at": time.time() + 600,
        }
        self._schedule_conversation_expiry(key)
        await zalo_repository.save_workflow(
            self._get_chat_id(update),
            self._get_sender_id(update),
            "notification_setup",
            "severity",
            workflow_data,
            600,
        )
        default_note = (
            "\nNhóm PUBLIC mặc định không nhận cảnh báo nội bộ; bạn vẫn có thể bật nếu muốn."
            if binding_type == "group_public" else ""
        )
        await update.message.reply_text(
            f"Mình sẽ thiết lập cảnh báo cho chat này trong {'2' if scope_locked else '3'} bước."
            f"{default_note}\n\n"
            f"1/{'2' if scope_locked else '3'} — Mức cảnh báo muốn nhận:\n"
            "1. Chỉ cảnh báo nghiêm trọng\n"
            "2. Cảnh báo nghiêm trọng + cảnh báo thường\n"
            "3. Tắt thông báo, chỉ dùng bot để tra cứu\n\n"
            "Gõ /cancel để thoát."
        )

    async def _start_wizard(
        self,
        update: Update,
        flow: str,
        step: str,
        prompt: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        flow_permissions = {
            "station_read": auth.Permission.VIEW_DEVICES,
            "config_read": auth.Permission.VIEW_CONFIG,
            "rover_read": auth.Permission.MANAGE_CASTER,
            "coord": auth.Permission.EDIT_COORDINATES,
            "config_set": auth.Permission.EDIT_SERVICE_CONFIG,
            "rover_update": auth.Permission.MANAGE_CASTER,
            "rover_create": auth.Permission.MANAGE_CASTER,
            "rover_delete": auth.Permission.MANAGE_CASTER,
            "rover_renew": auth.Permission.MANAGE_CASTER,
            "rover_block": auth.Permission.MANAGE_CASTER,
            "rover_unblock": auth.Permission.MANAGE_CASTER,
            "station_power": auth.Permission.MANAGE_USERS,
            "station_delete": auth.Permission.DELETE_DEVICE,
        }
        if not await self._require_admin_operator(update, flow_permissions.get(flow)):
            return
        key = self._conversation_key(update)
        self._conversation_states[key] = {
            "flow": flow,
            "step": step,
            "data": data or {},
            "expires_at": time.time() + 600,
        }

        self._schedule_conversation_expiry(key)
        await zalo_repository.save_workflow(
            self._get_chat_id(update), self._get_sender_id(update), flow, step, data or {}, 600,
        )
        await update.message.reply_text(f"{prompt}\n\nGõ /cancel để thoát.")

    @staticmethod
    def _detect_natural_language_intent(text: str):
        """Detect Vietnamese natural language intent from free text.

        Returns (command_name, args_list) or None.
        """
        if _is_cors_status_question(text):
            return "cors", []
        ascii_text = _normalize_to_ascii(text)
        for pattern, command in _NLP_INTENT_PATTERNS:
            m = pattern.match(ascii_text)
            if m:
                raw_args = (m.group(1) or "").strip() if m.lastindex else ""
                args = raw_args.split() if raw_args else []
                return command, args
        return None

    async def _try_dispatch_intent(self, update: Update, text: str) -> bool:
        """Detect a command from free text (no '/' prefix) and dispatch it.

        Returns True if a command was dispatched, False otherwise.
        """
        words = text.split()
        if not words:
            return False
        if _is_cors_status_question(text) or self._is_cors_followup(update, text):
            await self._cors_cmd(update, None)
            return True

        # Strategy 1: first word is a known command name (e.g. "stationdelete Geotek")
        first_word = words[0].lower().lstrip("/")
        if first_word in _COMMAND_DISPATCH:
            handler = getattr(self, _COMMAND_DISPATCH[first_word], None)
            if handler:
                ctx = _SyntheticContext(words[1:])
                logger.info(
                    "Smart dispatch: detected bare command '%s' input=%s",
                    first_word, self._redact_command_secret(text),
                )
                await handler(update, ctx)
                return True

        # Strategy 2: Vietnamese NLP intent detection
        intent = self._detect_natural_language_intent(text)
        if intent:
            command, args = intent
            handler = getattr(self, _COMMAND_DISPATCH.get(command, ""), None)
            if handler:
                logger.info(
                    "Smart dispatch: NLP detected input=%s -> /%s",
                    self._redact_command_secret(text), command,
                )
                await handler(update, _SyntheticContext(args))
                return True

        return False

    async def _conversation_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        key = self._conversation_key(update)
        await self._hydrate_conversation_state(
            self._get_chat_id(update), self._get_sender_id(update)
        )
        try:
            await self._conversation_message_impl(update, context)
        finally:
            state = self._conversation_states.get(key)
            if state:
                await zalo_repository.save_workflow(
                    self._get_chat_id(update), self._get_sender_id(update),
                    str(state.get("flow") or ""), str(state.get("step") or ""),
                    dict(state.get("data") or {}),
                    max(60, int(state.get("expires_at", time.time() + 600) - time.time())),
                )
            else:
                await zalo_repository.delete_workflow(
                    self._get_chat_id(update), self._get_sender_id(update)
                )

    async def _handle_object_context(self, update, key, state, answer):
        data = state["data"]
        target = data.get("target")
        ctx_type = data.get("type")
        lowered = answer.strip().lower()

        # 1. Handle numbered menu choices
        if lowered in {"1", "2", "3", "4", "5"}:
            self._conversation_states.pop(key, None)
            handlers = {
                "station": {
                    "1": self._configset_cmd,
                    "2": self._coord_cmd,
                    "3": self._stationreset_cmd,
                    "4": self._stationlock_cmd,
                    "5": self._stationdelete_cmd,
                },
                "rover": {
                    "1": self._roverupdate_cmd,
                    "2": self._roverrenew_cmd,
                    "3": self._roverblock_cmd,
                    "4": self._roverunblock_cmd,
                    "5": self._roverdelete_cmd,
                },
            }
            handler = handlers.get(ctx_type, {}).get(lowered)
            if handler:
                await handler(update, _SyntheticContext([target]))
            return True

        # 2. Handle natural language quick edits (One-line parsing)
        # Try to see if the user typed something like 'port 2101 mk 123'
        try:
            parsed = self._parse_key_values(answer.split())
            if parsed:
                self._conversation_states.pop(key, None)
                args = [target] + answer.split()
                handler = {
                    "station": self._configset_cmd,
                    "rover": self._roverupdate_cmd,
                }.get(ctx_type)
                if handler:
                    await handler(update, _SyntheticContext(args))
                return True
        except ValueError:
            pass # Not a key-value assignment, fall through

        # 3. Fallback: User typed something completely unrelated to the context
        # Clear context and re-evaluate as a fresh intent
        self._conversation_states.pop(key, None)
        if await self._try_dispatch_intent(update, answer):
            return True
        principal = await self._resolve_principal(update)
        if principal.is_linked and principal.allows(auth.Permission.VIEW_DEVICES):
            await self._admin_read(update, "smart_search", {"query": answer})
        else:
            await update.message.reply_text("Không tìm thấy lệnh hoặc bạn chưa liên kết tài khoản.")
        return True

    async def _handle_notification_setup(self, update, key, state, answer):
        step = state.get("step")
        if step == "scope":
            return await self._handle_notification_scope(update, state, answer)
        if step == "scope_values":
            return await self._handle_notification_scope_values(update, state, answer)
        handler = {
            "severity": self._handle_notification_severity,
            "quiet": self._handle_notification_quiet,
        }.get(step)
        return await handler(update, key, state, answer) if handler else False

    async def _handle_notification_severity(self, update, key, state, answer):
        data = state["data"]
        selection = self._parse_notification_severity(answer)
        if selection is None:
            await update.message.reply_text("Chọn 1, 2 hoặc 3 để mình đặt mức cảnh báo.")
            return True
        enabled, min_severity = selection
        data.update({"enabled": enabled, "min_severity": min_severity})
        if not enabled:
            await self._disable_notification_setup(update, key, data)
            return True
        if data.get("scope_locked"):
            await self._prompt_locked_notification_scope(update, state, data)
            return True
        state["step"] = "scope"
        await update.message.reply_text(
            "2/3 — Phạm vi cảnh báo:\n"
            "1. Tất cả trạm/rover trong phạm vi được xem\n"
            "2. Chỉ một số trạm/rover\n"
            "3. Không nhận cảnh báo nội bộ"
        )
        return True

    async def _disable_notification_setup(self, update, key, data):
        binding_id = int(data["binding_id"])
        await zalo_repository.set_notification_preference(
            binding_id,
            enabled=False,
            min_severity="critical",
            scopes=dict(data.get("scopes") or {"mode": "all"}),
            quiet_start=None,
            quiet_end=None,
            timezone="Asia/Saigon",
            critical_override=True,
        )
        await zalo_repository.complete_onboarding(binding_id)
        self._conversation_states.pop(key, None)
        await update.message.reply_text(
            "✅ Đã tắt thông báo cho chat này. Phạm vi truy cập được cấp vẫn giữ nguyên khi tra cứu.\n\n"
            "Gõ /menu để bắt đầu dùng bot."
        )

    async def _prompt_locked_notification_scope(self, update, state, data):
        state["step"] = "quiet"
        scopes = data.get("scopes") or {}
        scope_mode = str(scopes.get("mode") or "all")
        scope_text = {"all": "tất cả trạm", "none": "không có trạm"}.get(
            scope_mode, f"{len(scopes.get('values') or [])} trạm admin đã chọn"
        )
        locked_by = "group" if data.get("binding_type") in {"group_admin", "group_public"} else "tài khoản"
        await update.message.reply_text(
            f"Phạm vi cảnh báo được khóa theo {locked_by}: {scope_text}.\n\n"
            "2/2 — Giờ im lặng:\n"
            "1. Không đặt giờ im lặng\n"
            "2. 22:00-07:00\n"
            "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
        )

    async def _handle_notification_quiet(self, update, key, state, answer):
        data = state["data"]
        valid, quiet_start, quiet_end = self._parse_quiet_hours(answer)
        if not valid:
            await update.message.reply_text("Giờ im lặng cần dạng HH:MM-HH:MM, ví dụ 22:00-07:00.")
            return True
        binding_id = int(data["binding_id"])
        await zalo_repository.set_notification_preference(
            binding_id,
            enabled=bool(data.get("enabled", True)),
            min_severity=str(data.get("min_severity") or "critical"),
            scopes=dict(data.get("scopes") or {"mode": "all"}),
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            timezone="Asia/Saigon",
            critical_override=True,
        )
        await zalo_repository.complete_onboarding(binding_id)
        self._conversation_states.pop(key, None)
        severity = "tắt" if not data.get("enabled", True) else data.get("min_severity")
        quiet = f"{quiet_start}-{quiet_end}" if quiet_start and quiet_end else "không đặt"
        scopes = data.get("scopes") or {}
        scope_mode = str(scopes.get("mode") or "all")
        scope_text = {"all": "tất cả trạm", "none": "không có trạm"}.get(
            scope_mode, f"{len(scopes.get('values') or [])} trạm được chọn"
        )
        await update.message.reply_text(
            f"✅ Đã lưu thiết lập cảnh báo.\n"
            f"• Mức nhận: {severity}\n"
            f"• Phạm vi: {scope_text}\n"
            f"• Giờ im lặng: {quiet}\n\n"
            "Gõ /menu để bắt đầu dùng bot."
        )
        return True


    def _parse_notification_severity(self, answer):
        lowered = answer.lower()
        if lowered in {"1", "critical", "nghiem trong", "nghiêm trọng"}:
            return True, "critical"
        if lowered in {"2", "warning", "canh bao", "cảnh báo"}:
            return True, "warning"
        if lowered in {"3", "off", "tat", "tắt", "khong", NEGATIVE_ANSWER}:
            return False, "critical"
        return None

    def _parse_quiet_hours(self, answer):
        lowered = answer.lower()
        if lowered in {"1", "khong", NEGATIVE_ANSWER, "none", "no"}:
            return True, None, None
        if lowered in {"2", "dem", "đêm", "night"}:
            return True, "22:00", "07:00"
        match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*", answer)
        if not match:
            return False, None, None
        return True, match.group(1), match.group(2)

    async def _handle_notification_scope(self, update, state, answer):
        data = state["data"]
        lowered = answer.lower()
        if lowered in {"1", "all", "tat ca", "tất cả"}:
            data["scopes"] = {"mode": "all"}
            state["step"] = "quiet"
        elif lowered in {"2", "chon", "chọn", "selected"}:
            state["step"] = "scope_values"
            await update.message.reply_text(
                "Nhập danh sách tên trạm hoặc username rover, cách nhau bằng dấu phẩy hoặc dấu chấm phẩy."
            )
            return True
        elif lowered in {"3", "none", "off", "tat", "tắt", "khong", NEGATIVE_ANSWER}:
            data["enabled"] = False
            data["scopes"] = {"mode": "none"}
            state["step"] = "quiet"
        else:
            await update.message.reply_text("Chọn 1, 2 hoặc 3 cho phạm vi cảnh báo.")
            return True
        await update.message.reply_text(
            "3/3 — Giờ im lặng:\n"
            "1. Không đặt giờ im lặng\n"
            "2. 22:00-07:00\n"
            "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
        )
        return True

    async def _handle_notification_scope_values(self, update, state, answer):
        data = state["data"]
        selected = self._split_list_value(answer)
        if not selected:
            await update.message.reply_text("Danh sách chưa hợp lệ. Ví dụ: HCM01; rover01")
            return True
        data["scopes"] = {"mode": "selected", "values": selected}
        state["step"] = "quiet"
        await update.message.reply_text(
            "3/3 — Giờ im lặng:\n"
            "1. Không đặt giờ im lặng\n"
            "2. 22:00-07:00\n"
            "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
        )
        return True

    async def _handle_station_config_read(self, update, key, state, answer):
        flow = state["flow"]
        success = await self._admin_read(
            update, "station" if flow == "station_read" else "config", {"station": answer}
        )
        if success:
            self._conversation_states.pop(key, None)
        else:
            await update.message.reply_text("Bạn có thể nhập lại tên trạm, hoặc gõ /cancel để thoát.")
        return True

    async def _handle_rover_read(self, update, key, answer):
        success = await self._admin_read(update, "rover", {"username": answer})
        if success:
            self._conversation_states.pop(key, None)
        else:
            await update.message.reply_text("Bạn có thể nhập lại username rover, hoặc gõ /cancel để thoát.")
        return True

    async def _handle_coordinate_flow(self, update, key, state, answer):
        step = state["step"]
        data = state["data"]
        if step == "station":
            data["station"] = answer
            state["step"] = "lat"
            await update.message.reply_text("2/5 — Nhập vĩ độ (Lat), ví dụ 21.028511:")
            return True
        if step in {"lat", "lon", "alt", "accuracy"}:
            try:
                value = float(answer.replace(",", "."))
            except ValueError:
                await update.message.reply_text("❌ Giá trị phải là số. Vui lòng nhập lại:")
                return True
            if step == "lat" and not -90 <= value <= 90:
                await update.message.reply_text("❌ Vĩ độ (Lat) phải từ -90 đến 90.")
                return True
            if step == "lon" and not -180 <= value <= 180:
                await update.message.reply_text("❌ Kinh độ (Lon) phải từ -180 đến 180.")
                return True
            data[step] = value
            next_steps = {
                "lat": ("lon", "3/5 — Nhập kinh độ (Lon):"),
                "lon": ("alt", "4/5 — Nhập độ cao ellipsoid (Alt), đơn vị mét:"),
                "alt": ("accuracy", "5/5 — Nhập độ chính xác, mét (ví dụ 0.01):"),
            }
            if step in next_steps:
                state["step"], prompt = next_steps[step]
                await update.message.reply_text(prompt)
                return True
            self._conversation_states.pop(key, None)
            await self._queue_admin_action(
                update, "coord_set", data,
                f"Trạm: {data['station']}\n"
                f"Vĩ độ (Lat): {data['lat']}\nKinh độ (Lon): {data['lon']}\n"
                f"Độ cao ellipsoid (Alt): {data['alt']} m\nĐộ chính xác: {data['accuracy']} m",
            )
            return True
        return False

    async def _handle_config_editor(self, update, key, state, answer):
        flow = state["flow"]
        step = state["step"]
        data = state["data"]
        if step == target_key:
            try:
                await self._load_editor_context(update, flow, answer, state)
            except Exception as exc:
                self._conversation_states.pop(key, None)
                await update.message.reply_text(f"❌ Không tải được dữ liệu hiện tại: {exc}")
            return True
        if step == "field":
            await self._apply_editor_field_choice(update, flow, state, answer)
            return True

        field = str(data.get("current_field") or "")
        current_value = data.get("changes", {}).get(field, data.get("current", {}).get(field))
        if step == "boolean_value":
            return await self._handle_editor_boolean_value(update, state, answer, flow, field)
        if step == "mountpoint_action":
            return await self._handle_editor_mountpoint_action(update, state, answer, flow, current_value)
        if step in {"mountpoint_add", "mountpoint_remove"}:
            return await self._handle_editor_mountpoint_selection(update, state, answer, flow, current_value)
        if step == "mountpoint_replace":
            return await self._handle_editor_mountpoint_replace(update, state, answer, flow)
        if step == "value":
            return await self._handle_editor_value(update, state, answer, flow, field)
        return False

    async def _handle_editor_boolean_value(self, update, state, answer, flow, field):
        data = state["data"]
        if answer in BACK_COMMANDS:
            state["step"] = "field"
            await update.message.reply_text(self._format_editor_menu(flow, data))
            return True
        if answer in {"1", "bật", "bat", "on", "true"}:
            new_value = True
        elif answer in {"2", "tắt", "tat", "off", "false"}:
            new_value = False
        else:
            enabled_label, disabled_label = self._boolean_option_labels(field)
            await update.message.reply_text(
                f"Chọn 1 ({enabled_label}), 2 ({disabled_label}) hoặc 0 (quay lại)."
            )
            return True
        data["changes"][field] = new_value
        state["step"] = "field"
        await update.message.reply_text(
            f"✅ Đã chọn [{self._human_value(field, new_value)}]. Chưa áp dụng cho tới khi xác nhận.\n\n"
            + self._format_editor_menu(flow, data)
        )
        return True

    async def _handle_editor_mountpoint_action(self, update, state, answer, flow, current_value):
        data = state["data"]
        current_mountpoints = self._split_list_value(current_value)
        if answer in BACK_COMMANDS:
            state["step"] = "field"
            await update.message.reply_text(self._format_editor_menu(flow, data))
            return True
        if answer == "1":
            current_lower = {item.lower() for item in current_mountpoints}
            options = [
                item for item in data.get("available_mountpoints", [])
                if str(item).lower() not in current_lower
            ]
            data["selection_options"] = options
            state["step"] = "mountpoint_add"
            option_text = "\n".join(f"[ ] {i}. {item}" for i, item in enumerate(options, 1))
            await update.message.reply_text(
                "Chọn mountpoint cần THÊM bằng số (có thể chọn nhiều: 1 3 5).\n"
                + (option_text or "Không còn mountpoint gợi ý từ hệ thống.")
                + "\n\nBạn cũng có thể nhập tên khác, nhiều tên cách nhau bằng dấu ;"
            )
            return True
        if answer == "2":
            if not current_mountpoints:
                await update.message.reply_text("Danh sách hiện đang trống; không có gì để xóa.")
                return True
            data["selection_options"] = current_mountpoints
            state["step"] = "mountpoint_remove"
            await update.message.reply_text(
                "Chọn mountpoint cần XÓA bằng số (có thể chọn nhiều: 1 3):\n"
                + "\n".join(f"[✓] {i}. {item}" for i, item in enumerate(current_mountpoints, 1))
            )
            return True
        if answer == "3":
            state["step"] = "mountpoint_replace"
            await update.message.reply_text(
                "Nhập toàn bộ danh sách mountpoint mới, cách nhau bằng dấu ;\n"
                "Ví dụ: HCM01;HN01"
            )
            return True
        if answer == "4":
            data["changes"]["mountpoints"] = ""
            state["step"] = "field"
            await update.message.reply_text(
                "✅ Đã đánh dấu xóa toàn bộ mountpoint. Chưa áp dụng cho tới khi xác nhận.\n\n"
                + self._format_editor_menu(flow, data)
            )
            return True
        await update.message.reply_text("Chọn từ 0 đến 4 theo danh sách.")
        return True

    async def _handle_editor_mountpoint_selection(self, update, state, answer, flow, current_value):
        data = state["data"]
        options = list(data.get("selection_options") or [])
        number_tokens = re.findall(r"\d+", answer)
        selected = [
            options[int(token) - 1] for token in number_tokens
            if 1 <= int(token) <= len(options)
        ]
        if step == "mountpoint_add" and not selected:
            selected = self._split_list_value(answer)
        if not selected:
            await update.message.reply_text("Mình chưa nhận được lựa chọn hợp lệ. Hãy gửi số trong danh sách.")
            return True
        current_mountpoints = self._split_list_value(current_value)
        if step == "mountpoint_add":
            new_list = list(dict.fromkeys(current_mountpoints + selected))
        else:
            removed = {item.lower() for item in selected}
            new_list = [item for item in current_mountpoints if item.lower() not in removed]
        data["changes"]["mountpoints"] = ";".join(new_list)
        data.pop("selection_options", None)
        state["step"] = "field"
        await update.message.reply_text(
            f"✅ Danh sách mới: {', '.join(new_list) or 'Không có'}\n"
            "Chưa áp dụng cho tới khi xác nhận.\n\n"
            + self._format_editor_menu(flow, data)
        )
        return True

    async def _handle_editor_mountpoint_replace(self, update, state, answer, flow):
        data = state["data"]
        new_list = self._split_list_value(answer)
        if not new_list:
            await update.message.reply_text("Danh sách không hợp lệ. Nếu muốn xóa hết, quay lại và chọn mục 4.")
            return True
        data["changes"]["mountpoints"] = ";".join(new_list)
        state["step"] = "field"
        await update.message.reply_text(
            f"✅ Danh sách mới: {', '.join(new_list)}\n\n" + self._format_editor_menu(flow, data)
        )
        return True

    async def _handle_editor_value(self, update, state, answer, flow, field):
        data = state["data"]
        if answer.lower() in {"giữ", "giu", *BACK_COMMANDS}:
            state["step"] = "field"
            await update.message.reply_text(self._format_editor_menu(flow, data))
            return True
        data["changes"][field] = answer
        state["step"] = "field"
        await update.message.reply_text(
            f"✅ Đã ghi nhận giá trị mới: {self._human_value(field, answer)}\n"
            "Chưa áp dụng cho tới khi xác nhận.\n\n"
            + self._format_editor_menu(flow, data)
        )
        return True


    async def _handle_rover_create(self, update, key, state, answer):
        step = state["step"]
        data = state["data"]
        prompts = {
            "station": ("station", "username", "2/5 — Nhập username rover:"),
            "username": ("username", "password", "3/5 — Nhập password rover:"),
            "password": ("password", "expiry_days", "4/5 — Nhập số ngày sử dụng (ví dụ 365):"),
        }
        if step in prompts:
            field, next_step, prompt = prompts[step]
            data[field] = answer
            state["step"] = next_step
            await update.message.reply_text(prompt)
            return True
        if step == "expiry_days":
            try:
                days = int(answer)
                if not 1 <= days <= 3650:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Số ngày phải từ 1 đến 3650.")
                return True
            data["expiry_days"] = days
            state["step"] = "mountpoint"
            await update.message.reply_text("5/5 — Nhập mountpoint, hoặc gõ “mặc định” để lấy từ trạm:")
            return True
        if step == "mountpoint":
            data["mountpoint"] = None if answer.lower() in {"mặc định", "mac dinh", "default", "bo qua", "bỏ qua"} else answer
            self._conversation_states.pop(key, None)
            await self._queue_admin_action(
                update, "rover_create", data,
                f"Tạo rover {data['username']} tại trạm {data['station']}\n"
                f"Hạn: {data['expiry_days']} ngày\nMountpoint: {data['mountpoint'] or 'mặc định'}",
            )
            return True
        return False

    async def _handle_rover_delete_or_unblock(self, update, key, state, answer):
        flow = state["flow"]
        self._conversation_states.pop(key, None)
        action = "rover_delete" if flow == "rover_delete" else "rover_unblock"
        await self._queue_admin_action(
            update, action, {"username": answer},
            f"{'XÓA' if flow == 'rover_delete' else 'MỞ CHẶN'} rover {answer}",
        )
        return True

    async def _handle_rover_renew(self, update, key, state, answer):
        step = state["step"]
        data = state["data"]
        if step == "username":
            data["username"] = answer
            state["step"] = "days"
            await update.message.reply_text("2/2 — Nhập số ngày gia hạn (1–3650):")
            return True
        try:
            days = int(answer)
            if not 1 <= days <= 3650:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Số ngày phải từ 1 đến 3650.")
            return True
        data["expiry_days"] = days
        self._conversation_states.pop(key, None)
        await self._queue_admin_action(
            update, "rover_renew", data,
            f"Gia hạn rover {data['username']} thêm {days} ngày",
        )
        return True

    async def _handle_rover_block(self, update, key, state, answer):
        step = state["step"]
        data = state["data"]
        if step == "username":
            data["username"] = answer
            state["step"] = "reason"
            await update.message.reply_text("2/2 — Nhập lý do chặn:")
            return True
        data["reason"] = answer
        self._conversation_states.pop(key, None)
        await self._queue_admin_action(
            update, "rover_block", data,
            f"CHẶN rover {data['username']}\nLý do: {answer}",
        )
        return True

    async def _handle_station_power_or_delete(self, update, key, state, answer):
        flow = state["flow"]
        data = state["data"]
        self._conversation_states.pop(key, None)
        data["station"] = answer
        if flow == "station_delete":
            await self._queue_admin_action(
                update, "station_delete", {"station": answer},
                f"XÓA VĨNH VIỄN trạm {answer} khỏi hệ thống",
            )
        else:
            await self._queue_admin_action(
                update, data["action"], {"station": answer}, f"{data['label']} trạm {answer}"
            )
        return True

    async def _handle_idle_conversation(self, update):
        answer = self._clean_conversation_answer(update, getattr(update.message, "text", ""))
        if not answer:
            return
        if await self._try_dispatch_intent(update, answer):
            return
        principal = await self._resolve_principal(update)
        if principal.is_linked and principal.allows(auth.Permission.VIEW_DEVICES):
            await self._admin_read(update, "smart_search", {"query": answer})
        else:
            await update.message.reply_text(
                "Mình chưa thể tra cứu vì cuộc trò chuyện chưa được liên kết. "
                "Chat cá nhân dùng /link <mã trên dashboard>; group dùng /register <mã group>."
            )

    async def _conversation_message_impl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        key = self._conversation_key(update)
        state = self._conversation_states.get(key)
        if not state:
            await self._handle_idle_conversation(update)
            return
        if state.get("expires_at", 0) <= time.time():
            self._conversation_states.pop(key, None)
            await update.message.reply_text("⌛ Phiên thao tác đã hết hạn. Hãy chọn lại lệnh từ /menu.")
            return
        if not await self._require_admin_operator(update):
            self._conversation_states.pop(key, None)
            return

        try:
            await self._maybe_await(update.message.reply_action("typing"))
        except Exception:
            pass

        answer = self._clean_conversation_answer(update, getattr(update.message, "text", ""))
        if not answer:
            await update.message.reply_text("Mình chưa nhận được nội dung. Vui lòng nhập lại.")
            return

        # --- Smart Global Cancel ---
        if answer.lower() in {"hủy", "huy", "thoát", "thoat", "dừng", "dung", "hủy thao tác", "huy thao tac"}:
            self._conversation_states.pop(key, None)
            await update.message.reply_text("✅ Đã hủy thao tác hiện tại.")
            return

        flow = state["flow"]

        flow_handlers = {
            "object_context": self._handle_object_context,
            "notification_setup": self._handle_notification_setup,
            "station_read": self._handle_station_config_read,
            "config_read": self._handle_station_config_read,
            "coord": self._handle_coordinate_flow,
            "config_set": self._handle_config_editor,
            "rover_update": self._handle_config_editor,
            "rover_create": self._handle_rover_create,
            "rover_delete": self._handle_rover_delete_or_unblock,
            "rover_unblock": self._handle_rover_delete_or_unblock,
            "rover_renew": self._handle_rover_renew,
            "rover_block": self._handle_rover_block,
            "station_power": self._handle_station_power_or_delete,
            "station_delete": self._handle_station_power_or_delete,
        }
        if flow == "rover_read" and await self._handle_rover_read(update, key, answer):
            return
        handler = flow_handlers.get(flow)
        if handler and await handler(update, key, state, answer):
            return


        self._conversation_states.pop(key, None)
        await update.message.reply_text("❌ Phiên thao tác không hợp lệ. Hãy bắt đầu lại từ /menu.")
