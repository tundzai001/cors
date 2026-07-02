import json
import time
import asyncio
import logging
import os
import tempfile
from pathlib import Path
from hmac import compare_digest
from typing import Optional, Dict, List, Set, Callable, Awaitable
from datetime import datetime

from .database import settings

try:
    from zalo_bot import Update
    from zalo_bot.ext import ApplicationBuilder, CommandHandler, ContextTypes
    ZALO_AVAILABLE = True
except ImportError:
    ZALO_AVAILABLE = False

logger = logging.getLogger(__name__)

class ZaloBotService:
    def __init__(self):
        self.app = None
        self.chats = {}
        self.groups = {}
        configured_chats_file = str(getattr(settings, "ZALO_CHATS_FILE", "") or "").strip()
        self.CHATS_FILE = configured_chats_file or str(
            Path(__file__).resolve().parent.parent / "zalo_chats.json"
        )
        configured_groups_file = str(getattr(settings, "ZALO_GROUPS_FILE", "") or "").strip()
        self.GROUPS_FILE = configured_groups_file or str(
            Path(__file__).resolve().parent.parent / "zalo_groups.json"
        )
        
        self.prev_statuses = {}
        self.prev_alerts = set()
        
        self._get_devices_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        self._get_alerts_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        
        self._task = None
        self._polling_task = None

    def set_callbacks(self, get_devices: Callable, get_alerts: Callable):
        self._get_devices_cb = get_devices
        self._get_alerts_cb = get_alerts

    def _load_registry(self, path: str, label: str) -> dict:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError(f"Zalo {label} file must contain a JSON object")
                registry = {
                    str(chat_id): role
                    for chat_id, role in loaded.items()
                    if role in ("admin", "public")
                }
                logger.info("Loaded %d Zalo %s registration(s)", len(registry), label)
                return registry
            except Exception as exc:
                logger.error(
                    "Cannot load Zalo %s registrations from %s: %s",
                    label,
                    path,
                    exc,
                )
        return {}

    def _save_registry(self, path: str, registry: dict):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=str(target.parent), delete=False
            ) as temp_file:
                json.dump(
                    registry,
                    temp_file,
                    ensure_ascii=False,
                    indent=4,
                    sort_keys=True,
                )
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = temp_file.name
            os.replace(temp_path, target)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def _load_chats(self):
        self.chats = self._load_registry(self.CHATS_FILE, "direct chat")

    def _load_groups(self):
        self.groups = self._load_registry(self.GROUPS_FILE, "group")

    def _save_chats(self):
        self._save_registry(self.CHATS_FILE, self.chats)

    def _save_groups(self):
        self._save_registry(self.GROUPS_FILE, self.groups)

    def get_role(self, chat_id: str, is_group: bool = False) -> Optional[str]:
        registry = self.groups if is_group else self.chats
        return registry.get(str(chat_id))

    def get_admin_chats(self) -> List[str]:
        direct_admins = [cid for cid, role in self.chats.items() if role == "admin"]
        group_admins = [cid for cid, role in self.groups.items() if role == "admin"]
        return list(dict.fromkeys(direct_admins + group_admins))

    async def start(self):
        try:
            if not ZALO_AVAILABLE or not settings.ENABLE_ZALO_BOT or not settings.ZALO_BOT_TOKEN:
                logger.warning("Zalo Bot is disabled or missing requirements/token.")
                return

            self._load_chats()
            self._load_groups()
            logger.info("Initializing Zalo Bot Service...")
            
            # Build app
            self.app = ApplicationBuilder().token(settings.ZALO_BOT_TOKEN).build()
            
            # The Zalo SDK dispatcher stops at the first matching handler. An
            # ALL debug handler here would consume every command before the
            # command handlers below can process it.
            try:
                from zalo_bot.ext import MessageHandler, filters
                has_message_handler = True
            except ImportError:
                has_message_handler = False

            if hasattr(self.app, "add_error_handler"):
                self.app.add_error_handler(self._global_error_handler)

            # Add handlers
            self.app.add_handler(CommandHandler("start", self._start_cmd))
            self.app.add_handler(CommandHandler("help", self._help_cmd))
            self.app.add_handler(CommandHandler("register", self._register_cmd))
            self.app.add_handler(CommandHandler("status", self._status_cmd))
            self.app.add_handler(CommandHandler("errors", self._errors_cmd))

            if has_message_handler:
                self.app.add_handler(MessageHandler(filters.COMMAND, self._unknown_cmd))
            
            # Start polling as a background task
            logger.info("Starting polling loop...")
            
            async def safe_polling_loop():
                while True:
                    try:
                        # _polling_loop is the async method that handles the polling cycle
                        # It initializes the bot and runs get_update in a loop
                        await self.app._polling_loop()
                    except asyncio.CancelledError:
                        logger.info("Zalo Bot polling loop cancelled.")
                        break
                    except Exception as e:
                        logger.error(f"Zalo Bot polling loop crashed: {e}. Restarting in 10s...", exc_info=True)
                        await asyncio.sleep(10)
            
            self._polling_task = asyncio.create_task(safe_polling_loop())
            
            if settings.ENABLE_ZALO_ERROR_ALERTS:
                self._task = asyncio.create_task(self._error_check_loop())
                
            logger.info("Zalo Bot Service started successfully.")
        except Exception as e:
            logger.error(f"FATAL ZALO BOT ERROR: {e}", exc_info=True)

    async def _global_error_handler(self, update, context):
        chat_id = self._get_chat_id(update) if update else "unknown"
        logger.error(
            "Unhandled Zalo handler error in chat_id=%s: %s",
            chat_id,
            context.error,
            exc_info=context.error,
        )

    async def stop(self):
        if self.app:
            logger.info("Stopping Zalo Bot Service...")
            background_tasks = [
                task for task in (self._task, self._polling_task) if task is not None
            ]
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            self._task = None
            self._polling_task = None
            try:
                if hasattr(self.app, 'stop'):
                    if asyncio.iscoroutinefunction(self.app.stop):
                        await self.app.stop()
                    else:
                        self.app.stop()
            except Exception as e:
                logger.error(f"Error stopping Zalo Bot: {e}")

    def _get_chat_id(self, update: Update) -> str:
        """Get the chat ID from an update (works for both user and group chats)."""
        message = getattr(update, 'message', None)
        chat = getattr(message, 'chat', None)
        chat_id = getattr(chat, 'id', None)
        if chat_id is not None:
            return str(chat_id)
        return "unknown"

    def _is_group_chat(self, update: Update) -> bool:
        """Check if the update is from a group chat."""
        if hasattr(update, 'is_group') and update.is_group:
            return True
        if update.message and update.message.chat:
            chat_type = getattr(update.message.chat, 'type', None) or getattr(update.message.chat, 'chat_type', None)
            if chat_type and str(chat_type).upper() in ("GROUP", "SUPERGROUP"):
                return True
        if update.message and getattr(update.message, 'message_type', '') == "GROUP_MESSAGE":
            return True
        return False

    async def _send_to_chat(self, chat_id: str, text: str, update: Update = None):
        """Send a message to a chat."""
        try:
            message = getattr(update, 'message', None) if update else None
            if message and hasattr(message, 'reply_text'):
                await message.reply_text(text)
                return
            await self.app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}", exc_info=True)

    async def _unknown_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = str(getattr(getattr(update, 'message', None), 'text', '') or '')
        command = text.strip().split(maxsplit=1)[0] if text.strip() else "unknown"
        logger.info("Unknown Zalo command in chat_id=%s: %s", self._get_chat_id(update), command)
        await update.message.reply_text("❌ Lệnh không tồn tại. Vui lòng gõ /help để xem danh sách lệnh được hỗ trợ.")

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Chào bạn! Đây là Bot giám sát hệ thống CORS Aitogy.\nGõ /help để xem danh sách lệnh.")

    async def _help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        role = self.get_role(chat_id, self._is_group_chat(update))
        
        if role == "admin":
            text = (
                "📋 LỆNH BOT CORS (ADMIN)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/status          — Tổng quan tất cả trạm\n"
                "/status <tên>    — Xem chi tiết 1 trạm\n"
                "/errors          — Danh sách lỗi hệ thống\n"
                "/register <mã>   — Đăng ký lại quyền chat\n"
                "/help            — Trợ giúp\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔔 Bạn có quyền Admin và sẽ nhận cảnh báo hệ thống."
            )
        else:
            text = (
                "📋 LỆNH BOT CORS\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/status <tên>    — Xem thông tin trạm\n"
                "/register <mã>   — Đăng ký quyền tra cứu\n"
                "/help            — Trợ giúp"
            )
        await update.message.reply_text(text)

    async def _register_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = self._get_chat_id(update)
            sender_id = str(update.message.from_user.id) if update.message and update.message.from_user else "unknown"
            is_group = self._is_group_chat(update)
            logger.info(
                "Zalo registration request: chat_id=%s sender_id=%s is_group=%s",
                chat_id,
                sender_id,
                is_group,
            )

            if chat_id == "unknown":
                logger.error("Cannot determine chat_id from update")
                return

            if not context.args or len(context.args) == 0:
                await self._send_to_chat(chat_id, "Sử dụng: /register <token>", update)
                return

            token = str(context.args[0]).strip()
            admin_token = str(settings.ZALO_ADMIN_GROUP_TOKEN or "").strip()
            public_token = str(settings.ZALO_PUBLIC_GROUP_TOKEN or "").strip()
            registry = self.groups if is_group else self.chats
            save_registry = self._save_groups if is_group else self._save_chats
            target_name = "NHÓM" if is_group else "CHAT"
            if admin_token and compare_digest(token, admin_token):
                registry[chat_id] = "admin"
                save_registry()
                await self._send_to_chat(chat_id, f"✅ Đăng ký {target_name} với quyền ADMIN thành công! Sẽ nhận cảnh báo lỗi hệ thống.", update)
            elif public_token and compare_digest(token, public_token):
                registry[chat_id] = "public"
                save_registry()
                await self._send_to_chat(chat_id, f"✅ Đăng ký {target_name} với quyền PUBLIC thành công! Có thể tra cứu trạng thái trạm.", update)
            else:
                await self._send_to_chat(chat_id, "❌ Token không hợp lệ!", update)
        except Exception as e:
            logger.error(f"Error in _register_cmd: {e}", exc_info=True)
            try:
                chat_id = self._get_chat_id(update)
                if chat_id != "unknown":
                    await self._send_to_chat(chat_id, f"❌ Lỗi hệ thống khi đăng ký: {e}", update)
            except:
                pass

    async def _status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = self._get_chat_id(update)
            role = self.get_role(chat_id, self._is_group_chat(update))

            if not role:
                await update.message.reply_text("❌ Bạn chưa đăng ký. Hãy dùng lệnh /register <token>")
                return

            if not self._get_devices_cb:
                await update.message.reply_text("❌ Hệ thống chưa sẵn sàng.")
                return

            devices = await self._get_devices_cb()

            if not context.args:
                if role != "admin":
                    await update.message.reply_text("❌ Bạn chỉ có thể tra cứu trạm cụ thể. Ví dụ: /status HCM-01")
                    return

                total = len(devices)
                online = len([d for d in devices if d.get("status") not in ("offline", "unknown")])
                offline = total - online
                text = (
                    f"📊 TỔNG QUAN HỆ THỐNG CORS\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🟢 Online: {online}/{total}\n"
                    f"🔴 Offline: {offline}/{total}\n"
                )
                await update.message.reply_text(text)
                return

            station_name = " ".join(context.args).lower()
            station = next((d for d in devices if station_name in str(d.get("name", "")).lower() or station_name in str(d.get("serial", "")).lower()), None)

            if not station:
                await update.message.reply_text(f"❌ Không tìm thấy trạm nào khớp với '{station_name}'")
                return

            name = station.get("name", "Unknown")
            status_val = station.get("status", "unknown")
            status_icon = "🔴" if status_val == "offline" else "🟢"

            if role == "admin":
                serial = station.get("serial", "N/A")
                bps = station.get("latest_throughput_bps", 0)
                if bps is None: bps = 0
                mountpoint = station.get("mountpoint", "N/A")

                text = (
                    f"📡 Trạm: {name} (SN: {serial})\n"
                    f"{status_icon} Trạng thái: {status_val.capitalize()}\n"
                    f"📶 Throughput: {bps:,} bps\n"
                    f"🔗 Mountpoint: {mountpoint}\n"
                )

                if self._get_alerts_cb:
                    alerts = await self._get_alerts_cb()
                    device_alerts = [a for a in alerts if a.get("device_serial") == serial]
                    if device_alerts:
                        text += "\n⚠️ CẢNH BÁO HIỆN TẠI:\n"
                        for a in device_alerts:
                            text += f"• {a.get('title', 'Lỗi không xác định')}\n"
            else:
                lat = station.get("latitude", "N/A")
                lon = station.get("longitude", "N/A")
                bps = station.get("latest_throughput_bps", 0)
                if bps is None: bps = 0

                text = (
                    f"📡 Trạm: {name}\n"
                    f"{status_icon} Trạng thái: {status_val.capitalize()}\n"
                    f"📍 Tọa độ: {lat}, {lon}\n"
                    f"📶 Throughput: {bps:,} bps\n"
                )

            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in _status_cmd: {e}", exc_info=True)
            try:
                await update.message.reply_text(f"❌ Lỗi hệ thống khi tra cứu trạng thái: {e}")
            except:
                pass

    async def _errors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat_id = self._get_chat_id(update)
            role = self.get_role(chat_id, self._is_group_chat(update))

            if role != "admin":
                await update.message.reply_text("❌ Lệnh này chỉ dành cho Admin.")
                return

            if not self._get_alerts_cb:
                return

            alerts = await self._get_alerts_cb()
            if not alerts:
                await update.message.reply_text("✅ Hệ thống đang hoạt động ổn định, không có cảnh báo nào.")
                return

            critical = [a for a in alerts if a.get("severity") == "critical"]
            warning = [a for a in alerts if a.get("severity") == "warning"]
            info = [a for a in alerts if a.get("severity") == "info"]

            text = "🔍 LỖI HỆ THỐNG HIỆN TẠI\n━━━━━━━━━━━━━━━━━━━━━━\n"

            if critical:
                text += f"\n🔴 Critical ({len(critical)}):\n"
                for a in critical:
                    text += f"• {a.get('rover_username') or a.get('device_serial')}: {a.get('title')}\n"

            if warning:
                text += f"\n🟡 Warning ({len(warning)}):\n"
                for a in warning:
                    text += f"• {a.get('rover_username') or a.get('device_serial')}: {a.get('title')}\n"

            if info:
                text += f"\n🔵 Info ({len(info)}):\n"
                for a in info:
                    text += f"• {a.get('rover_username') or a.get('device_serial')}: {a.get('title')}\n"

            await update.message.reply_text(text)
        except Exception as e:
            logger.error(f"Error in _errors_cmd: {e}", exc_info=True)
            try:
                await update.message.reply_text(f"❌ Lỗi hệ thống khi tải cảnh báo: {e}")
            except:
                pass


    async def _error_check_loop(self):
        while True:
            try:
                admin_chats = self.get_admin_chats()
                if not admin_chats or not self._get_devices_cb or not self._get_alerts_cb:
                    await asyncio.sleep(120)
                    continue
                    
                devices = await self._get_devices_cb()
                alerts = await self._get_alerts_cb()
                
                # Detect changes
                newly_offline = []
                newly_online = []
                new_alerts = []

                current_statuses = {d.get("serial"): d.get("status", "unknown") for d in devices}
                current_alerts_map = {a.get("id"): a for a in alerts}
                current_alerts_ids = set(current_alerts_map.keys())

                if self.prev_statuses:
                    for serial, status in current_statuses.items():
                        prev_status = self.prev_statuses.get(serial)
                        if prev_status and prev_status != status:
                            if status == "offline":
                                newly_offline.append(next((d for d in devices if d.get("serial") == serial), {}))
                            elif prev_status == "offline" and status != "offline":
                                newly_online.append(next((d for d in devices if d.get("serial") == serial), {}))

                if self.prev_alerts:
                    new_alert_ids = current_alerts_ids - self.prev_alerts
                    for aid in new_alert_ids:
                        new_alerts.append(current_alerts_map[aid])

                self.prev_statuses = current_statuses
                self.prev_alerts = current_alerts_ids
                
                if newly_offline or newly_online or new_alerts:
                    now_str = datetime.now().strftime("%H:%M %d/%m/%Y")
                    text = f"🚨 CẢNH BÁO HỆ THỐNG — {now_str}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    
                    if newly_offline:
                        text += f"\n❌ MỚI OFFLINE ({len(newly_offline)}):\n"
                        for d in newly_offline:
                            text += f"• {d.get('name')} (SN: {d.get('serial')})\n"
                            
                    if new_alerts:
                        text += f"\n⚠️ LỖI MỚI ({len(new_alerts)}):\n"
                        for a in new_alerts:
                            name = a.get('rover_username') or a.get('device_serial') or "Hệ thống"
                            text += f"• {name}: {a.get('title')}\n"
                            
                    if newly_online:
                        text += f"\n✅ PHỤC HỒI ({len(newly_online)}):\n"
                        for d in newly_online:
                            text += f"• {d.get('name')} đã online trở lại\n"
                            
                    online = len([s for s in current_statuses.values() if s not in ("offline", "unknown")])
                    offline = len([s for s in current_statuses.values() if s == "offline"])
                    
                    text += f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    text += f"📊 {online} online | {offline} offline"
                    
                    for chat_id in admin_chats:
                        try:
                            await self.app.bot.send_message(chat_id=chat_id, text=text)
                        except Exception as e:
                            logger.error(f"Failed to send alert to {chat_id}: {e}")

            except Exception as e:
                logger.error(f"Error in Zalo error_check_loop: {e}")
                
            await asyncio.sleep(120)

zalo_bot_service = ZaloBotService()
