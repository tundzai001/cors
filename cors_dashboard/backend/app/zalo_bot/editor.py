"""Editor behavior for the Zalo service."""
from __future__ import annotations

import re
import time
from .common import (ACTION_PERMISSIONS, ROVER_BOOLEAN_FIELDS, ROVER_UPDATE_CHOICES,
                     SERVICE_BOOLEAN_FIELDS, SERVICE_CONFIG_CHOICES)
from ..zalo_assistant import zalo_repository

class EditorMixin:
    @staticmethod
    def _parse_key_values(values: List[str]) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        i = 0
        while i < len(values):
            item = values[i]
            if "=" in item:
                key, val = item.split("=", 1)
                parsed[key.strip()] = val.strip()
                i += 1
            else:
                key = item.strip()
                if i + 1 < len(values):
                    val = values[i + 1].strip()
                    if "=" not in val:
                        parsed[key] = val
                        i += 2
                        continue
                raise ValueError(f"Không thể tự động suy luận giá trị cho tham số '{key}'. Hãy dùng dạng 'khoá=giá trị' hoặc 'khoá giá_trị'.")

        # Smart keyword mapping
        mapping = {
            "port": "port1", "cổng": "port1", "cong": "port1",
            "mk": "password1", "pass": "password1", "password": "password1",
            "mật khẩu": "password1", "mat khau": "password1",
            "tk": "username1", "user": "username1",
            "tài khoản": "username1", "tai khoan": "username1",
            "mountpoint": "mountpoint1", "mp": "mountpoint1",
            "server": "serverhost1", "host": "serverhost1",
            "s2_port": "port2", "s2_mk": "password2", "s2_mp": "mountpoint2"
        }
        smart_parsed = {}
        for k, v in parsed.items():
            k_lower = k.lower().replace("_", " ")
            mapped_k = mapping.get(k_lower, k_lower.replace(" ", "_"))
            if mapped_k in SERVICE_CONFIG_CHOICES:
                mapped_k = SERVICE_CONFIG_CHOICES[mapped_k][0]
            if mapped_k in ROVER_CONFIG_CHOICES:
                mapped_k = ROVER_CONFIG_CHOICES[mapped_k][0]
            smart_parsed[mapped_k] = v
        return smart_parsed

    @staticmethod
    def _format_choice_menu(choices: Dict[str, tuple[str, str]]) -> str:
        return "\n".join(f"{number}. {label}" for number, (_, label) in choices.items())

    @staticmethod
    def _split_list_value(value: Any) -> List[str]:
        return list(dict.fromkeys(
            part.strip() for part in re.split(r"[;,\n]+", str(value or "")) if part.strip()
        ))

    @staticmethod
    def _human_value(field: str, value: Any) -> str:
        from ..zalo_bot_service import ZaloBotService
        if any(part in field.lower() for part in ("password", "secret", "token")):
            return "Đã thiết lập" if value else "Chưa thiết lập"
        if isinstance(value, bool):
            if field in {"stream_on_demand", "server1_stream_on_demand", "server2_stream_on_demand"}:
                return "NGỦ KHI KHÔNG CÓ ROVER" if value else "LUÔN TRUYỀN DỮ LIỆU"
            if field == "show_position":
                return "CHO PHÉP HIỂN THỊ VỊ TRÍ" if value else "ẨN VỊ TRÍ"
            if field == "is_shared_with_managers":
                return "CÓ CHIA SẺ" if value else "KHÔNG CHIA SẺ"
            return "BẬT" if value else "TẮT"
        if value in (None, ""):
            return "Chưa đặt"
        if field == "mountpoints":
            return ", ".join(ZaloBotService._split_list_value(value)) or "Không có"
        return str(value)

    @staticmethod
    def _boolean_option_labels(field: str) -> tuple[str, str]:
        if field in {"stream_on_demand", "server1_stream_on_demand", "server2_stream_on_demand"}:
            return "Ngủ khi không có rover", "Luôn truyền dữ liệu"
        if field == "show_position":
            return "Cho phép hiển thị vị trí rover", "Ẩn vị trí rover"
        if field == "rtcm_cycle_enabled":
            return "Bật tự ngắt RTCM theo chu kỳ", "Tắt tự ngắt RTCM theo chu kỳ"
        if field == "is_shared_with_managers":
            return "Chia sẻ rover với quản lý", "Không chia sẻ rover với quản lý"
        return "Bật chức năng này", "Tắt chức năng này"

    def _format_editor_menu(self, flow: str, data: Dict[str, Any]) -> str:
        choices = SERVICE_CONFIG_CHOICES if flow == "config_set" else ROVER_UPDATE_CHOICES
        current = data.get("current") or {}
        pending = data.get("changes") or {}
        lines = ["Chọn mục cần sửa:"]
        for number, (field, label) in choices.items():
            value = pending[field] if field in pending else current.get(field)
            marker = " ✏️" if field in pending else ""
            lines.append(f"{number}. {label}: [{self._human_value(field, value)}]{marker}")
        lines.extend((
            "",
            "Gửi số mục muốn sửa, hoặc:",
            "• xong — xem lại và tạo mã xác nhận",
            "• /cancel — hủy toàn bộ",
        ))
        return "\n".join(lines)

    async def _load_editor_context(
        self, update: Update, flow: str, target: str, state: Optional[Dict[str, Any]] = None
    ) -> None:
        action = "config_edit_context" if flow == "config_set" else "rover_edit_context"
        target_key = "station" if flow == "config_set" else "username"
        principal = await self._resolve_principal(update)
        required_permission = ACTION_PERMISSIONS[action]
        if not principal.is_linked or not principal.allows(required_permission):
            raise PermissionError("Tài khoản hiện tại không có quyền mở trình chỉnh sửa này")
        request = {target_key: target}
        request.update(self._principal_payload(principal))
        result = await self._admin_action_cb(action, request)
        if state is None:
            key = self._conversation_key(update)
            state = {
                "flow": flow,
                "step": "field",
                "data": {},
                "expires_at": time.time() + 600,
            }
            self._conversation_states[key] = state
            self._schedule_conversation_expiry(key)
        state["step"] = "field"
        state["data"] = {
            target_key: result.get(target_key) or target,
            "current": dict(result.get("current") or {}),
            "changes": {},
            "available_mountpoints": list(result.get("available_mountpoints") or []),
        }
        await zalo_repository.save_workflow(
            self._get_chat_id(update), self._get_sender_id(update),
            flow, str(state["step"]), dict(state["data"]),
            max(60, int(state.get("expires_at", time.time() + 600) - time.time())),
        )
        title = "cấu hình trạm" if flow == "config_set" else "rover"
        await update.message.reply_text(
            f"✅ Đã tải {title} hiện tại của {result.get('label') or target}.\n\n"
            + self._format_editor_menu(flow, state["data"])
        )

    async def _finish_editor(self, update: Update, flow: str, data: Dict[str, Any]) -> None:
        changes = dict(data.get("changes") or {})
        if not changes:
            await update.message.reply_text("Bạn chưa thay đổi mục nào. Hãy chọn một mục hoặc gõ /cancel.")
            return
        target_key = "station" if flow == "config_set" else "username"
        action = "config_set" if flow == "config_set" else "rover_update"
        choices = SERVICE_CONFIG_CHOICES if flow == "config_set" else ROVER_UPDATE_CHOICES
        labels = {field: label for field, label in choices.values()}
        summary = [f"{labels.get(field, field)}: {self._human_value(field, value)}" for field, value in changes.items()]
        self._conversation_states.pop(self._conversation_key(update), None)
        await self._queue_admin_action(
            update, action, {target_key: data[target_key], "changes": changes},
            f"{('Trạm' if flow == 'config_set' else 'Rover')}: {data[target_key]}\n"
            + "\n".join(f"• {line}" for line in summary),
        )

    async def _apply_editor_field_choice(
        self, update: Update, flow: str, state: Dict[str, Any], answer: str
    ) -> None:
        data = state["data"]
        choices = SERVICE_CONFIG_CHOICES if flow == "config_set" else ROVER_UPDATE_CHOICES
        if answer.lower() in {"xong", "done", "save", "lưu"}:
            await self._finish_editor(update, flow, data)
            return
        selected = choices.get(answer)
        if not selected:
            await update.message.reply_text(
                "Mục không hợp lệ. Hãy gửi đúng số trong danh sách hoặc gõ “xong”.\n\n"
                + self._format_editor_menu(flow, data)
            )
            return
        field, label = selected
        data["current_field"] = field
        current_value = data.get("changes", {}).get(field, data.get("current", {}).get(field))
        boolean_fields = SERVICE_BOOLEAN_FIELDS if flow == "config_set" else ROVER_BOOLEAN_FIELDS
        if field in boolean_fields:
            state["step"] = "boolean_value"
            enabled_label, disabled_label = self._boolean_option_labels(field)
            await update.message.reply_text(
                f"{label}\nHiện tại: [{self._human_value(field, current_value)}]\n\n"
                f"1. {enabled_label}\n2. {disabled_label}\n0. Quay lại"
            )
            return
        if flow == "rover_update" and field == "mountpoints":
            state["step"] = "mountpoint_action"
            await update.message.reply_text(
                f"Mountpoint đang được phép:\n{self._human_value(field, current_value)}\n\n"
                "1. Thêm mountpoint\n2. Xóa mountpoint\n3. Thay toàn bộ danh sách\n"
                "4. Xóa hết mountpoint\n0. Quay lại"
            )
            return
        state["step"] = "value"
        await update.message.reply_text(
            f"{label}\nHiện tại: [{self._human_value(field, current_value)}]\n"
            "Nhập giá trị mới, hoặc gõ “giữ” để quay lại."
        )
