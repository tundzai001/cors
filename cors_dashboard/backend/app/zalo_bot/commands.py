"""Commands behavior for the Zalo service."""
from __future__ import annotations

import difflib
import logging
from .. import auth
from ..zalo_assistant import zalo_repository
from .common import BOT_COMMANDS, ROVER_UPDATE_CHOICES, SERVICE_CONFIG_CHOICES

logger = logging.getLogger("app.zalo_bot_service")


class CommandsMixin:
    async def _stations_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._admin_read(update, "stations", {"query": " ".join(context.args).strip()})

    async def _station_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(
                update, "station_read", "station",
                "Bạn chưa nhập tên trạm. Hãy trả lời bằng tên trạm.\n"
                "Ví dụ: HCM-01 (tương đương /station HCM-01)",
            )
            return
        await self._admin_read(update, "station", {"station": " ".join(context.args).strip()})

    async def _config_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "config_read", "station", "Nhập tên trạm cần xem cấu hình:")
            return
        await self._admin_read(update, "config", {"station": " ".join(context.args).strip()})

    async def _rovers_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._admin_read(update, "rovers", {"query": " ".join(context.args).strip()})

    async def _rover_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_read", "username", "Nhập username rover cần xem:")
            return
        await self._admin_read(update, "rover", {"username": context.args[0]})

    async def _coord_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "coord", "station", "1/5 — Nhập tên trạm cần sửa tọa độ:")
            return
        if len(context.args) < 4:
            if len(context.args) == 1:
                await self._start_wizard(
                    update, "coord", "lat",
                    f"2/5 — Nhập vĩ độ (Lat) cho trạm {context.args[0]}:",
                    data={"station": context.args[0]}
                )
            else:
                await update.message.reply_text("Thiếu tham số. Gõ riêng /coord để bot hướng dẫn từng bước.")
            return
        try:
            payload = {
                "station": context.args[0],
                "lat": float(context.args[1]),
                "lon": float(context.args[2]),
                "alt": float(context.args[3]),
                "accuracy": float(context.args[4]) if len(context.args) > 4 else 0.01,
            }
        except ValueError:
            await update.message.reply_text("❌ Tọa độ/độ chính xác phải là số.")
            return
        if not (-90 <= payload["lat"] <= 90 and -180 <= payload["lon"] <= 180):
            await update.message.reply_text("❌ Vĩ độ (Lat) hoặc kinh độ (Lon) ngoài phạm vi hợp lệ.")
            return
        await self._queue_admin_action(
            update,
            "coord_set",
            payload,
            f"Trạm: {payload['station']}\n"
            f"Vĩ độ (Lat): {payload['lat']}\nKinh độ (Lon): {payload['lon']}\n"
            f"Độ cao ellipsoid (Alt): {payload['alt']} m\nĐộ chính xác: {payload['accuracy']} m",
        )

    async def _configset_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "config_set", "station", "1/4 — Nhập tên trạm cần sửa cấu hình:")
            return
        if len(context.args) == 1:
            if not await self._require_admin_operator(update):
                return
            try:
                await self._load_editor_context(update, "config_set", context.args[0])
            except Exception as exc:
                await update.message.reply_text(f"❌ Không tải được cấu hình hiện tại: {exc}")
            return
        try:
            changes = self._parse_key_values(context.args[1:])
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return
        config_labels = {field: label for field, label in SERVICE_CONFIG_CHOICES.values()}
        change_lines = [
            f"• {config_labels.get(field, field)}: {self._human_value(field, value)}"
            for field, value in changes.items()
        ]
        await self._queue_admin_action(
            update,
            "config_set",
            {"station": context.args[0], "changes": changes},
            f"Trạm: {context.args[0]}\n" + "\n".join(change_lines),
        )

    async def _rovercreate_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_create", "station", "1/5 — Nhập tên trạm cấp rover:")
            return
        if len(context.args) < 3:
            await update.message.reply_text(
                "Thiếu tham số. Gõ riêng /rovercreate để bot hướng dẫn từng bước."
            )
            return
        try:
            days = int(context.args[3]) if len(context.args) > 3 else 365
        except ValueError:
            await update.message.reply_text("❌ days phải là số nguyên.")
            return
        payload = {
            "station": context.args[0],
            "username": context.args[1],
            "password": context.args[2],
            "expiry_days": days,
            "mountpoint": context.args[4] if len(context.args) > 4 else None,
        }
        await self._queue_admin_action(
            update,
            "rover_create",
            payload,
            f"Tạo rover {payload['username']} tại {payload['station']}\nHạn: {days} ngày\nMountpoint: {payload['mountpoint'] or 'mặc định'}",
        )

    async def _roverupdate_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_update", "username", "1/4 — Nhập username rover cần sửa:")
            return
        if len(context.args) == 1:
            if not await self._require_admin_operator(update):
                return
            try:
                await self._load_editor_context(update, "rover_update", context.args[0])
            except Exception as exc:
                await update.message.reply_text(f"❌ Không tải được rover hiện tại: {exc}")
            return
        try:
            changes = self._parse_key_values(context.args[1:])
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return
        rover_labels = {field: label for field, label in ROVER_UPDATE_CHOICES.values()}
        change_lines = [
            f"• {rover_labels.get(field, field)}: {self._human_value(field, value)}"
            for field, value in changes.items()
        ]
        await self._queue_admin_action(
            update,
            "rover_update",
            {"username": context.args[0], "changes": changes},
            f"Cập nhật rover {context.args[0]}\n" + "\n".join(change_lines),
        )

    async def _roverdelete_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_delete", "username", "Nhập username rover cần XÓA:")
            return
        await self._queue_admin_action(
            update,
            "rover_delete",
            {"username": context.args[0]},
            f"XÓA rover: {context.args[0]} (khỏi DB và caster)",
        )

    async def _roverrenew_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_renew", "username", "1/2 — Nhập username rover cần gia hạn:")
            return
        if len(context.args) < 2:
            if len(context.args) == 1:
                await self._start_wizard(
                    update, "rover_renew", "days",
                    f"2/2 — Nhập số ngày gia hạn cho rover {context.args[0]}:",
                    data={"username": context.args[0]}
                )
            else:
                await update.message.reply_text("Thiếu tham số. Gõ riêng /roverrenew để bot hướng dẫn từng bước.")
            return
        try:
            days = int(context.args[1])
            if not 1 <= days <= 3650:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ days phải từ 1 đến 3650.")
            return
        await self._queue_admin_action(
            update, "rover_renew",
            {"username": context.args[0], "expiry_days": days},
            f"Gia hạn rover {context.args[0]} thêm {days} ngày tính từ hiện tại",
        )

    async def _roverblock_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_block", "username", "1/2 — Nhập username rover cần chặn:")
            return
        reason = " ".join(context.args[1:]).strip() or "Blocked via Zalo admin"
        await self._queue_admin_action(
            update, "rover_block",
            {"username": context.args[0], "reason": reason},
            f"CHẶN rover {context.args[0]}\nLý do: {reason}",
        )

    async def _roverunblock_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(update, "rover_unblock", "username", "Nhập username rover cần mở chặn:")
            return
        await self._queue_admin_action(
            update, "rover_unblock",
            {"username": context.args[0], "mountpoint": context.args[1] if len(context.args) > 1 else None},
            f"MỞ CHẶN rover {context.args[0]}",
        )

    async def _queue_station_power_action(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, label: str
    ) -> None:
        if not context.args:
            await self._start_wizard(
                update, "station_power", "station", f"Nhập tên trạm cần {label.lower()}:",
                {"action": action, "label": label},
            )
            return
        await self._queue_admin_action(
            update, action, {"station": context.args[0]}, f"{label} trạm {context.args[0]}",
        )

    async def _stationlock_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._queue_station_power_action(update, context, "station_lock", "KHÓA")

    async def _stationunlock_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._queue_station_power_action(update, context, "station_unlock", "MỞ KHÓA")

    async def _stationreset_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._queue_station_power_action(
            update, context, "station_reset", "KHỞI ĐỘNG LẠI NGUỒN"
        )

    async def _stationdelete_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._start_wizard(
                update, "station_delete", "station",
                "⚠️ Nhập chính xác tên trạm cần XÓA VĨNH VIỄN:",
            )
            return
        station = " ".join(context.args).strip()
        await self._queue_admin_action(
            update, "station_delete", {"station": station},
            f"XÓA VĨNH VIỄN trạm {station} khỏi hệ thống",
        )

    async def _confirm_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        operator = await self._require_admin_operator(update)
        if not operator:
            return
        if not context.args:
            await update.message.reply_text(
                "💡 Hướng dẫn xác nhận:\n"
                "• Thao tác thường: /confirm <mã>\n"
                "• Thao tác nguy hiểm: /confirm <mã> <tên-chính-xác>"
            )
            return
        code = context.args[0].strip().upper()
        typed_target = " ".join(context.args[1:]).strip() or None
        try:
            pending, payload = await zalo_repository.claim_pending_action(
                code, operator[0], operator[1], typed_target
            )
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return
        payload = dict(payload)
        payload.update({"chat_id": operator[0], "sender_id": operator[1]})
        payload.update(self._principal_payload(operator[2]))
        # This is only progress feedback.  A transient Zalo delivery timeout
        # must never prevent an already-claimed mutation from running.
        try:
            await update.message.reply_text("⏳ Đang thực hiện...")
        except Exception as exc:
            logger.warning(
                "Could not deliver Zalo mutation progress action=%s: %s",
                pending.action, exc,
            )
        try:
            result = await self._admin_action_cb(pending.action, payload)
        except Exception as exc:
            logger.exception("Zalo admin mutation failed action=%s: %s", pending.action, exc, exc_info=True)
            await zalo_repository.finish_pending_action(pending.id, False)
            try:
                if isinstance(exc, ValueError):
                    await update.message.reply_text(f"❌ {exc}")
                else:
                    await update.message.reply_text("❌ Hệ thống gặp sự cố khi xử lý yêu cầu. Vui lòng thử lại sau.")
            except Exception as send_exc:
                logger.exception("Could not deliver Zalo mutation failure: %s", send_exc)
            return
        await zalo_repository.finish_pending_action(pending.id, True)
        try:
            await self._reply_text_chunks(update, str(result.get("message") or "✅ Hoàn tất."))
        except Exception as exc:
            logger.exception(
                "Zalo mutation succeeded but result delivery failed action=%s: %s",
                pending.action, exc, exc_info=True,
            )

    async def _cancel_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        operator = await self._require_admin_operator(update)
        if not operator:
            return
        if not context.args:
            key = self._conversation_key(update)
            if self._conversation_states.pop(key, None):
                await zalo_repository.delete_workflow(operator[0], operator[1])
                await update.message.reply_text("✅ Đã hủy phiên thao tác hiện tại.")
            else:
                await update.message.reply_text("Không có phiên nhập liệu đang hoạt động. Nếu muốn hủy mã xác nhận, gõ: /cancel <mã>")
            return
        code = context.args[0].strip().upper()
        if await zalo_repository.cancel_pending_action(code, operator[0], operator[1]):
            await update.message.reply_text("✅ Đã hủy thao tác.")
        else:
            await update.message.reply_text("❌ Không tìm thấy thao tác để hủy.")

    async def _logout_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        if self._get_chat_type(update) == "group":
            await update.message.reply_text(
                "Group không thể bị ngắt bởi một thành viên. Admin quản lý liên kết group tại Dashboard → Zalo Bot."
            )
            return
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            await update.message.reply_text("Chat cá nhân này hiện chưa liên kết với tài khoản dashboard.")
            return
        confirmation = str(context.args[0] if context.args else "").strip().lower()
        if confirmation not in {"confirm", "xacnhan", "xácnhận", "yes"}:
            await update.message.reply_text(
                f"⚠️ Chat đang liên kết với tài khoản {principal.username}.\n"
                "Gõ /logout confirm để xóa hoàn toàn liên kết. Cấu hình cảnh báo, phiên và mã xác nhận đang chờ cũng sẽ bị xóa."
            )
            return
        if not await zalo_repository.unlink_private_chat(chat_id, sender_id):
            await update.message.reply_text("❌ Không thể ngắt liên kết này.")
            return
        self.forget_chat(chat_id)
        await update.message.reply_text(
            "✅ Đã xóa hoàn toàn liên kết tài khoản dashboard khỏi chat Zalo này.\n"
            "Muốn liên kết lại, tạo mã mới trên dashboard rồi dùng /link <mã>."
        )

    async def _unknown_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info("Unknown Zalo command chat_id=%s sender_id=%s", self._get_chat_id(update), self._get_sender_id(update))
        raw_text = str(getattr(update.message, "text", "") or "").strip()
        command = raw_text.split()[0].lstrip("/").split("@", 1)[0].lower() if raw_text else ""
        suggestion = difflib.get_close_matches(command, BOT_COMMANDS, n=1, cutoff=0.55)
        if suggestion:
            await update.message.reply_text(
                f"❌ Không có lệnh /{command}. Có phải bạn muốn dùng /{suggestion[0]}?\n"
                "Gõ /menu để xem toàn bộ chức năng."
            )
            return
        await update.message.reply_text(
            "❌ Bot chưa hiểu lệnh này. Gõ /menu để chọn chức năng hoặc /help để xem cách sử dụng."
        )

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        chat_type = self._get_chat_type(update)
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            await self._public_menu_cmd(update, context)
            return
        logger.info(
            "Zalo /start chat_id=%s sender_id=%s chat_type=%s binding=%s role=%s",
            chat_id, sender_id, chat_type, principal.binding_type, principal.role,
        )

        if chat_type == "group":
            if principal.binding_type == "group_admin":
                text = (
                    "👋 Bot CORS Aitogy đã có mặt trong nhóm và đang hoạt động.\n"
                    "Nhóm đã có quyền ADMIN. Gõ /menu để mở menu quản trị."
                )
            elif principal.binding_type == "group_public":
                text = (
                    "👋 Bot CORS Aitogy đã có mặt trong nhóm và đang hoạt động.\n"
                    "Nhóm đã có quyền tra cứu PUBLIC. Gõ /help để xem cách dùng."
                )
            else:
                text = (
                    "👋 Bot CORS Aitogy đã có mặt trong nhóm và đang hoạt động.\n"
                    "Để kích hoạt quyền cho nhóm, hãy nhắc tên bot kèm lệnh "
                    "/register <mã>."
                )
        else:
            text = (
                "👋 Bot CORS Aitogy đã sẵn sàng trong cuộc trò chuyện cá nhân này.\n"
                f"Đang liên kết với tài khoản {principal.username or '-'} ({principal.role or '-'}). "
                "Gõ /menu để xem đúng menu theo quyền của bạn."
            )
        await update.message.reply_text(text)

    async def _public_menu_cmd(self, update: Update, context):
        chat_id = self._get_chat_id(update)
        if chat_id in self._revoked_chat_ids or not await zalo_repository.remember_cors_chat(
            chat_id, self._get_chat_type(update), self._get_display_name(update)
        ):
            return
        token = self._cors_outbound.set("reply")
        try:
            await update.message.reply_text(
                "📋 MENU CÔNG KHAI · CORS CỤC\n"
                "Không cần liên kết tài khoản.\n\n"
                "• Hỏi tình trạng Cục: /cors\n"
                "• Hoặc nhắn tự nhiên: “tình trạng Cục”, “Cục sập chưa”, "
                "“cors cục hnay đo được k mọi người”.\n"
                "• Máy cứ FLOAT: hỏi để khoanh vùng mạng, hiệu chỉnh hoặc nhiễu động.\n"
                "• Điện ly Hà Nội chi tiết: xem TEC và địa từ Kp mới nhất.\n"
                "• /menu hoặc /help — Xem hướng dẫn này.\n\n"
                "Trong nhóm, nhắc @bot kèm câu hỏi nếu Zalo yêu cầu.\n"
                "Có thể hỏi “ở Tuyên Quang đo được không” để xem trạng thái Cục và tầng điện ly khu vực."
            )
        finally:
            self._cors_outbound.reset(token)

    async def _help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not (await self._resolve_principal(update)).is_linked:
            await self._public_menu_cmd(update, context)
            return
        text = (
            "💡 HƯỚNG DẪN BOT CORS\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• /cors hoặc /cors cục đang thế nào: hỏi tình trạng CORS Cục, không cần liên kết.\n"
            "• /menu: mở danh sách chức năng.\n"
            "• Có thể gõ đủ lệnh: /station HCM-01.\n"
            "• Nếu thiếu tham số, bot sẽ hỏi tiếp từng bước.\n"
            "• Nếu gõ sai tên trạm, bot sẽ đưa ra tên gần đúng.\n"
            "• Trong nhóm, hãy nhắc tên bot trước lệnh nếu Zalo yêu cầu.\n"
            "• /cancel: hủy phiên nhập liệu hoặc thao tác đang chờ.\n\n"
            "• Chat cá nhân dùng /logout confirm để xóa hẳn liên kết.\n\n"
            "Lệnh thay đổi/xóa dữ liệu luôn yêu cầu /confirm <mã>."
        )
        await update.message.reply_text(text)

    @staticmethod
    def _menu_scope_label(principal) -> str:
        if principal.station_scope_mode == "selected":
            return f"Phạm vi: {len(principal.allowed_bases)} trạm được admin chọn"
        if principal.station_scope_mode == "none":
            return "Phạm vi: không có trạm"
        if principal.binding_type == "private":
            return "Phạm vi: theo đúng tài khoản web"
        return "Phạm vi: tất cả trạm"

    @staticmethod
    def _menu_read_actions(principal) -> list[str]:
        lines = [
            "📡 TRA CỨU",
            "/status — Tổng quan các trạm bạn được xem",
            "/stations — Danh sách trạm",
            "/station <tên> — Chi tiết, tọa độ Lat/Lon/Alt và vệ tinh",
        ]
        if principal.allows(auth.Permission.MANAGE_CASTER):
            lines.extend((
                "/rovers — Danh sách rover",
                "/rover <user> — Chi tiết rover: caster, mountpoint, hạn dùng",
            ))
        if principal.allows(auth.Permission.VIEW_CONFIG):
            lines.append("/config <tên> — Xem cấu hình trạm")
        return lines

    @staticmethod
    def _menu_write_actions(principal) -> list[str]:
        lines = []
        if principal.allows(auth.Permission.EDIT_COORDINATES):
            lines.append("/coord <tên> — Sửa tọa độ")
        if principal.allows(auth.Permission.EDIT_SERVICE_CONFIG):
            lines.append("/configset <tên> — Sửa cấu hình bằng menu từng mục")
        if principal.allows(auth.Permission.MANAGE_CASTER):
            lines.extend((
                "/rovercreate — Tạo rover",
                "/roverupdate <user> — Sửa rover: thêm/xóa/thay mountpoint, bật/tắt rõ nghĩa",
                "/roverrenew <user> — Gia hạn rover",
                "/roverblock <user> — Chặn rover",
                "/roverunblock <user> — Mở chặn rover",
                "/roverdelete <user> — Xóa rover",
            ))
        if principal.allows(auth.Permission.MANAGE_USERS):
            lines.extend(("/stationlock <tên> — Khóa trạm", "/stationunlock <tên> — Mở khóa trạm"))
        if principal.allows(auth.Permission.DELETE_DEVICE):
            lines.extend(("/stationreset <tên> — Khởi động lại trạm", "/stationdelete <tên> — Xóa trạm"))
        return lines

    @staticmethod
    def _menu_system_actions(principal, write_lines) -> list[str]:
        lines = ["🔔 HỆ THỐNG"]
        if principal.allows(auth.Permission.VIEW_CONFIG):
            lines.append("/errors — Cảnh báo hiện tại")
        lines.append("/notifications — Sửa cảnh báo của chat này")
        if principal.binding_type == "private":
            lines.append("/logout — Xóa liên kết tài khoản khỏi chat này")
        if write_lines:
            lines.append("/confirm <mã> — Xác nhận thao tác")
        lines.extend((
            "/cancel — Hủy phiên hoặc mã xác nhận",
            "/help — Hướng dẫn cách dùng",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "Bạn cũng có thể nhắn tự nhiên: “tdbd01”, “xem rover abc”, “trạm offline”.",
        ))
        return lines

    async def _menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            await self._public_menu_cmd(update, context)
            return
        lines = [
            "🎛️ MENU BOT CORS",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"Quyền hiện tại: {principal.username or principal.binding_type} ({principal.role or 'public'})",
            self._menu_scope_label(principal),
            "",
        ]
        lines.extend(self._menu_read_actions(principal))
        write_lines = self._menu_write_actions(principal)
        if write_lines:
            lines.extend(("", "🛠️ THAO TÁC CẦN XÁC NHẬN", *write_lines))
        lines.extend(("", *self._menu_system_actions(principal, write_lines)))
        await update.message.reply_text("\n".join(lines))

    async def _link_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        chat_type = self._get_chat_type(update)
        if chat_type == "group":
            await update.message.reply_text(
                "Group dùng /register <mã group>. Chat cá nhân mới dùng /link <mã tạo trong dashboard>."
            )
            return
        if not context.args:
            await update.message.reply_text(
                "Dùng: /link <mã>\n"
                "Mã này lấy trong dashboard: mục Zalo Bot. Mã OTP gồm 6 chữ số, dùng một lần và hết hạn sau 30 giây."
            )
            return
        try:
            binding, user = await zalo_repository.consume_link_code(
                context.args[0], chat_id, sender_id, self._get_display_name(update)
            )
        except ValueError as exc:
            logger.info("Zalo link failed chat_id=%s sender_id=%s: %s", chat_id, sender_id, exc)
            await update.message.reply_text(f"❌ {exc}")
            return

        self._revoked_chat_ids.discard(str(chat_id))

        await update.message.reply_text(
            f"✅ Đã liên kết chat cá nhân với tài khoản dashboard {user.username} ({user.role}).\n"
            "Từ giờ bot dùng đúng quyền và phạm vi của tài khoản này."
        )
        await self._start_notification_onboarding(update, int(binding.id), str(binding.binding_type))

    async def _notifications_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            await update.message.reply_text(
                "Chat này chưa được liên kết. Chat cá nhân dùng /link <mã>; group dùng /register <mã group>."
            )
            return
        binding = await zalo_repository.get_binding(self._get_chat_id(update))
        if not binding:
            await update.message.reply_text("❌ Không tìm thấy cấu hình chat để sửa thông báo.")
            return
        await self._start_notification_onboarding(update, int(binding.id), str(binding.binding_type))

    async def _register_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        chat_type = self._get_chat_type(update)
        logger.info(
            "DEBUG REGISTER: chat_id=%s, sender_id=%s, chat_type=%s",
            chat_id, sender_id, chat_type,
        )

        if len(context.args) == 0:
            if chat_type == "private":
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Chat cá nhân dùng /link <mã>.\n"
                        "Vào dashboard → Zalo Bot → Tạo mã liên kết, rồi gửi mã đó cho bot."
                    ),
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Sử dụng trong group: /register <mã liên kết>\n"
                        "Admin tạo OTP 30 giây tại Dashboard → Zalo Bot, chọn trước Group admin/public và phạm vi trạm."
                    ),
                )
            return

        token = context.args[0]
        if chat_type != "group":
            await self._link_cmd(update, context)
            return

        try:
            binding, _ = await zalo_repository.consume_group_link_code(
                token, chat_id, sender_id, self._get_display_name(update)
            )
        except ValueError as exc:
            logger.info(
                "Zalo group register failed chat_id=%s sender_id=%s: %s",
                chat_id, sender_id, exc,
            )
            await context.bot.send_message(chat_id=chat_id, text=f"❌ {exc}")
            return
        role = "admin" if binding.binding_type == "group_admin" else "public"
        self._revoked_chat_ids.discard(str(chat_id))
        if binding.station_scope_mode == "all":
            scope_text = "\nPhạm vi: tất cả trạm."
        elif binding.station_scope_mode == "none":
            scope_text = "\nPhạm vi: không có trạm."
        else:
            scope_text = f"\nPhạm vi: {len(binding.allowed_bases or [])} trạm được admin chọn."
        await context.bot.send_message(
            chat_id=chat_id,
            text=self._registration_success_text(role, chat_type) + scope_text,
        )
        await self._start_notification_onboarding(update, int(binding.id), str(binding.binding_type))

    async def _status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._admin_read(update, "overview", {})
            return
        await self._admin_read(update, "station", {"station": " ".join(context.args).strip()})

    async def _errors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._admin_read(update, "alerts", {})
