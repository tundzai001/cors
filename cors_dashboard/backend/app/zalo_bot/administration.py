"""Administration behavior for the Zalo service."""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
import unicodedata
from typing import Optional
from .. import auth
from ..database import settings
from ..zalo_assistant import payload_fingerprint, zalo_repository
from .common import ACTION_PERMISSIONS, DANGEROUS_ACTIONS, SERVICE_CONFIG_CHOICES

logger = logging.getLogger("app.zalo_bot_service")


class AdministrationMixin:
    async def _require_admin_operator(
        self, update: Update, permission: Optional[str] = None
    ) -> Optional[tuple[str, str, ZaloPrincipal]]:
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            subject = "Nhóm" if self._get_chat_type(update) == "group" else "Cuộc trò chuyện này"
            instruction = "/register <mã>" if self._get_chat_type(update) == "group" else "/link <mã>"
            await update.message.reply_text(f"❌ {subject} chưa được liên kết. Dùng {instruction}.")
            return None
        if permission and not principal.allows(permission):
            await update.message.reply_text(
                f"⛔ Tài khoản {principal.username or principal.binding_type} không có quyền thực hiện chức năng này."
            )
            return None
        if not self._admin_action_cb:
            await update.message.reply_text("❌ Kênh điều khiển admin chưa sẵn sàng.")
            return None
        return chat_id, sender_id, principal

    @staticmethod
    async def _reply_text_chunks(update: Update, text: str, limit: int = 1200) -> None:
        remaining = str(text)
        while remaining:
            if len(remaining) <= limit:
                chunk, remaining = remaining, ""
            else:
                split_at = remaining.rfind("\n", 0, limit)
                if split_at < limit // 2:
                    split_at = limit
                chunk, remaining = remaining[:split_at], remaining[split_at:].lstrip("\n")
            await update.message.reply_text(chunk)
            if remaining:
                await asyncio.sleep(0.08)

    async def _admin_read(self, update: Update, action: str, payload: Dict[str, Any]) -> bool:
        operator = await self._require_admin_operator(update, ACTION_PERMISSIONS.get(action))
        if not operator:
            return False
        payload.update({"chat_id": operator[0], "sender_id": operator[1]})
        payload.update(self._principal_payload(operator[2]))
        try:
            result = await self._admin_action_cb(action, payload)
        except ValueError as exc:
            logger.info("Zalo admin input not found action=%s: %s", action, exc)
            await update.message.reply_text(f"❌ {exc}")
            return False
        except Exception as exc:
            logger.exception("Zalo admin read failed action=%s: %s", action, exc, exc_info=True)
            await update.message.reply_text(f"❌ Không thể đọc dữ liệu lúc này: {exc}")
            return False
        try:
            msg = str(result.get("message") or "✅ Hoàn tất.")

            # --- Interactive Object Menu & Context Storage ---
            ctx_target = result.get("context_target")
            ctx_type = result.get("context_type")
            ctx_name = result.get("context_name")
            if ctx_target and ctx_type:
                menu = ""
                if ctx_type == "station":
                    menu = (
                        f"\n\n👇 BẠN MUỐN LÀM GÌ TIẾP THEO? (Gõ phím số)\n"
                        f"[1] ⚙️ Sửa cấu hình\n"
                        f"[2] 📍 Cập nhật tọa độ\n"
                        f"[3] ⚠️ Khôi phục mặc định (Reset)\n"
                        f"[4] 🔒 Khóa trạm\n"
                        f"[5] 🗑️ Xóa trạm"
                    )
                elif ctx_type == "rover":
                    menu = (
                        f"\n\n👇 BẠN MUỐN LÀM GÌ TIẾP THEO? (Gõ phím số)\n"
                        f"[1] ✏️ Sửa thông số (Mountpoint, Mật khẩu...)\n"
                        f"[2] ⏳ Gia hạn thời gian\n"
                        f"[3] 🚫 Khóa (Block) rover\n"
                        f"[4] 🔓 Mở khóa (Unblock) rover\n"
                        f"[5] 🗑️ Xóa rover"
                    )
                msg += menu
                self._conversation_states[self._conversation_key(update)] = {
                    "flow": "object_context",
                    "step": "idle",
                    "data": {
                        "target": ctx_target,
                        "type": ctx_type,
                        "name": ctx_name,
                    },
                    "expires_at": time.time() + 300  # 5 minutes context expiry
                }

            await self._reply_text_chunks(update, msg)
            return True
        except Exception as exc:
            logger.exception(
                "Zalo outbound delivery failed after data was prepared action=%s: %s",
                action, exc, exc_info=True,
            )
            return False

    @staticmethod
    def _device_search_key(value):
        normalized = unicodedata.normalize("NFD", str(value or "").lower())
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        return "".join(char for char in ascii_text if char.isalnum())

    def _device_search_needles(self, wanted):
        normalized = unicodedata.normalize("NFD", wanted)
        ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
        stop_words = {
            "xem", "tim", "kiem", "tra", "cuu", "giup", "toi", "cho", "minh",
            "thong", "tin", "chi", "tiet", "tram", "rover", "tai", "khoan",
        }
        tokens = [token for token in re.findall(r"[a-z0-9_.-]+", ascii_text) if token not in stop_words]
        needles = {self._device_search_key(wanted)}
        needles.update(self._device_search_key(token) for token in tokens)
        return {needle for needle in needles if len(needle) >= 2}

    def _score_local_device(self, device, needles):
        name_key = self._device_search_key(device.get("name") or "")
        serial_key = self._device_search_key(device.get("serial") or "")
        score = 0
        for needle in needles:
            if needle == name_key:
                score += 10
            if needle == serial_key:
                score += 10
            if len(needle) >= 10 and needle.isdigit() and needle == serial_key:
                score += 20
            if len(needle) >= 3:
                if needle != name_key and name_key and needle in name_key:
                    score += 3
                if needle != serial_key and serial_key and needle in serial_key:
                    score += 3
        return score

    def _rank_local_devices(self, devices_list, needles):
        scored_devices = []
        for device in devices_list:
            score = self._score_local_device(device, needles)
            if score > 0:
                scored_devices.append((device, score))
        return scored_devices

    def _suggest_local_device(self, identifier, wanted, devices_list):
        label_lookup = {}
        for device in devices_list:
            key = self._device_search_key(device.get("name") or device.get("serial") or "")
            if key:
                label_lookup[key] = f"trạm {device.get('name') or device.get('serial')}"
        suggestion_keys = difflib.get_close_matches(
            self._device_search_key(wanted), list(label_lookup), n=5, cutoff=0.35
        )
        if not suggestion_keys:
            return None, f"Không tìm thấy trạm '{identifier}'."
        suggestions = ", ".join(label_lookup[key] for key in suggestion_keys)
        return None, f"Không tìm thấy trạm '{identifier}'. Có phải bạn muốn: {suggestions}?"

    def _find_device_locally(self, identifier: str, devices_list: List[dict]) -> tuple[Optional[dict], Optional[str]]:
        wanted = str(identifier or "").strip().lower()
        if not wanted:
            return None, "Bạn chưa nhập tên hoặc serial trạm."
        scored_devices = self._rank_local_devices(devices_list, self._device_search_needles(wanted))
        if not scored_devices:
            return self._suggest_local_device(identifier, wanted, devices_list)
        scored_devices.sort(key=lambda item: -item[1])
        top_dev, best_score = scored_devices[0]
        second_score = scored_devices[1][1] if len(scored_devices) > 1 else -1
        if best_score > second_score:
            return top_dev, None
        choices = ", ".join(f"trạm {device.get('name') or device.get('serial')}" for device, _ in scored_devices[:5])
        return None, f"Có nhiều trạm khớp '{identifier}': {choices}."

    def _station_action_preview(self, action, payload, device_name, device_serial, preview):
        if action == "station_delete":
            return f"XÓA VĨNH VIỄN trạm {device_name} ({device_serial}) khỏi hệ thống"
        if action == "station_lock":
            return f"KHÓA trạm {device_name} ({device_serial})"
        if action == "station_unlock":
            return f"MỞ KHÓA trạm {device_name} ({device_serial})"
        if action == "station_reset":
            return f"KHỞI ĐỘNG LẠI NGUỒN trạm {device_name} ({device_serial})"
        if action == "coord_set":
            return (
                f"Trạm: {device_name} ({device_serial})\n"
                f"Vĩ độ (Lat): {payload.get('lat')}\nKinh độ (Lon): {payload.get('lon')}\n"
                f"Độ cao ellipsoid (Alt): {payload.get('alt')} m\nĐộ chính xác: {payload.get('accuracy')} m"
            )
        if action == "config_set":
            changes = dict(payload.get("changes") or {})
            config_labels = {field: label for field, label in SERVICE_CONFIG_CHOICES.values()}
            change_lines = [
                f"• {config_labels.get(field, field)}: {self._human_value(field, value)}"
                for field, value in changes.items()
            ]
            return f"Sửa cấu hình trạm {device_name} ({device_serial})\n" + "\n".join(change_lines)
        return preview

    async def _resolve_station_admin_action(self, update, action, payload, preview):
        station_actions = {"station_delete", "station_lock", "station_unlock", "station_reset", "coord_set", "config_set"}
        station_query = payload.get("station")
        if action not in station_actions or not station_query or not self._get_devices_cb:
            return preview, False
        try:
            devices_list = await self._get_devices_cb()
            device, error_msg = self._find_device_locally(station_query, devices_list)
            if error_msg:
                await update.message.reply_text(f"❌ {error_msg}")
                return preview, True
            if not device:
                await update.message.reply_text(f"❌ Không tìm thấy trạm '{station_query}'.")
                return preview, True

            device_name = device.get("name") or device.get("serial")
            device_serial = device.get("serial")
            if action == "station_delete":
                exact_matches = {
                    str(device.get("name") or "").strip().lower(),
                    str(device.get("serial") or "").strip().lower(),
                }
                if str(station_query).strip().lower() not in exact_matches:
                    await update.message.reply_text(
                        f"❌ Ý của bạn là trạm '{device_name}'?\n"
                        f"Để tránh xóa nhầm, vui lòng gõ chính xác: xóa trạm {device_name} (hoặc {device_serial})"
                    )
                    return preview, True

            payload["station"] = device_serial
            return self._station_action_preview(action, payload, device_name, device_serial, preview), False
        except Exception as exc:
            logger.exception("Error resolving device in _queue_admin_action: %s", exc, exc_info=True)
            return preview, False

    async def _queue_admin_action(
        self,
        update: Update,
        action: str,
        payload: Dict[str, Any],
        preview: str,
    ) -> None:
        permission = ACTION_PERMISSIONS.get(action)
        operator = await self._require_admin_operator(update, permission)
        if not operator:
            return
        if not bool(getattr(settings, "ZALO_ENABLE_WRITE_ACTIONS", False)):
            await update.message.reply_text(
                "⚠️ Bot đang ở chế độ chỉ đọc theo feature flag ZALO_ENABLE_WRITE_ACTIONS=false. "
                "Mình đã hiểu thao tác nhưng chưa tạo mã xác nhận."
            )
            return

        preview, abort = await self._resolve_station_admin_action(update, action, payload, preview)
        if abort:
            return

        principal = operator[2]
        payload.update(self._principal_payload(principal))
        risk_level = "dangerous" if action in DANGEROUS_ACTIONS else "standard"
        target = next((
            str(payload.get(key)).strip() for key in (
                "station", "username", "ip_address", "target", "serial",
            ) if payload.get(key) not in (None, "")
        ), None)
        code, _ = await zalo_repository.create_pending_action(
            chat_id=operator[0], sender_id=operator[1], user_id=principal.user_id,
            action=action, payload=payload, preview=preview, risk_level=risk_level,
            target_identifier=target, state_fingerprint=payload_fingerprint(payload),
        )
        confirm_command = f"/confirm {code}"
        if risk_level == "dangerous" and target:
            confirm_command += f" {target}"
        await update.message.reply_text(
            f"⚠️ **XÁC NHẬN THAO TÁC**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Bạn đang yêu cầu:\n"
            f"**{preview}**\n\n"
            f"👉 Để tiếp tục, vui lòng copy và gửi lại lệnh dưới đây:\n"
            f"{confirm_command}\n\n"
            f"🚫 Để hủy bỏ, gửi lệnh:\n"
            f"/cancel {code}\n"
            f"*(Lệnh tự động hủy sau 2 phút)*"
        )
