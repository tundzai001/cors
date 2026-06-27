import json
import time
import asyncio
import logging
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
        self.CHATS_FILE = "zalo_chats.json"
        
        self.prev_statuses = {}
        self.prev_alerts = set()
        
        self._get_devices_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        self._get_alerts_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        
        self._task = None

    def set_callbacks(self, get_devices: Callable, get_alerts: Callable):
        self._get_devices_cb = get_devices
        self._get_alerts_cb = get_alerts

    def _load_chats(self):
        import os
        if os.path.exists(self.CHATS_FILE):
            try:
                with open(self.CHATS_FILE, 'r') as f:
                    self.chats = json.load(f)
            except:
                self.chats = {}

    def _save_chats(self):
        with open(self.CHATS_FILE, 'w') as f:
            json.dump(self.chats, f, indent=4)

    def get_role(self, chat_id: str) -> Optional[str]:
        return self.chats.get(str(chat_id))

    def get_admin_chats(self) -> List[str]:
        return [cid for cid, role in self.chats.items() if role == "admin"]

    async def start(self):
        try:
            if not ZALO_AVAILABLE or not settings.ENABLE_ZALO_BOT or not settings.ZALO_BOT_TOKEN:
                logger.warning("Zalo Bot is disabled or missing requirements/token.")
                return

            self._load_chats()
            logger.info("Initializing Zalo Bot Service...")
            
            # Build app
            self.app = ApplicationBuilder().token(settings.ZALO_BOT_TOKEN).build()
            
            # Import filters and MessageHandler safely
            try:
                from zalo_bot.ext import MessageHandler, filters
                has_message_handler = True
            except ImportError:
                has_message_handler = False
            
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
                        if hasattr(self.app, '_polling_loop') and asyncio.iscoroutinefunction(self.app._polling_loop):
                            await self.app._polling_loop()
                        elif asyncio.iscoroutinefunction(self.app.run_polling):
                            await self.app.run_polling()
                        else:
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, self.app.run_polling)
                    except Exception as e:
                        logger.error(f"Zalo Bot polling loop crashed: {e}. Restarting in 5s...", exc_info=True)
                        await asyncio.sleep(5)
            
            asyncio.create_task(safe_polling_loop())
            
            if settings.ENABLE_ZALO_ERROR_ALERTS:
                self._task = asyncio.create_task(self._error_check_loop())
                
            logger.info("Zalo Bot Service started successfully.")
        except Exception as e:
            logger.error(f"FATAL ZALO BOT ERROR: {e}", exc_info=True)

    async def stop(self):
        if self.app:
            logger.info("Stopping Zalo Bot Service...")
            if self._task:
                self._task.cancel()
            try:
                if hasattr(self.app, 'stop'):
                    if asyncio.iscoroutinefunction(self.app.stop):
                        await self.app.stop()
                    else:
                        self.app.stop()
            except Exception as e:
                logger.error(f"Error stopping Zalo Bot: {e}")

    def _get_chat_id(self, update: Update) -> str:
        if hasattr(update, 'effective_chat') and update.effective_chat:
            return str(update.effective_chat.id)
        if hasattr(update, 'message') and update.message:
            msg = update.message
            if hasattr(msg, 'chat_id'): return str(msg.chat_id)
            if hasattr(msg, 'chat') and hasattr(msg.chat, 'id'): return str(msg.chat.id)
        if hasattr(update, 'sender_id'):
            return str(update.sender_id)
        return "unknown"

    async def _unknown_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"DEBUG UPDATE: {update.to_dict() if hasattr(update, 'to_dict') else update}")
        await update.message.reply_text("❌ Lệnh không tồn tại. Vui lòng gõ /help để xem danh sách lệnh được hỗ trợ.")

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"DEBUG UPDATE: {update.to_dict() if hasattr(update, 'to_dict') else update}")
        await update.message.reply_text("Chào bạn! Đây là Bot giám sát hệ thống CORS Aitogy.\nGõ /help để xem danh sách lệnh.")

    async def _help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        role = self.get_role(chat_id)
        
        if role == "admin":
            text = (
                "📋 LỆNH BOT CORS (ADMIN)\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "/status          — Tổng quan tất cả trạm\n"
                "/status <tên>    — Xem chi tiết 1 trạm\n"
                "/errors          — Danh sách lỗi hệ thống\n"
                "/register        — Đăng ký lại nhóm\n"
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
        logger.info(f"DEBUG REGISTER UPDATE: {update.to_dict() if hasattr(update, 'to_dict') else update}")
        chat_id = str(update.effective_chat.id)
        sender_id = str(update.message.from_user.id) if update.message and update.message.from_user else "unknown"
        logger.info(f"DEBUG REGISTER: chat_id={chat_id}, sender_id={sender_id}")
        
        if len(context.args) == 0:
            await context.bot.send_message(chat_id=chat_id, text="Sử dụng: /register <token_nhóm>")
            return
            
        token = context.args[0]
        if token == settings.ZALO_ADMIN_GROUP_TOKEN:
            self.chats[chat_id] = "admin"
            self._save_chats()
            await context.bot.send_message(chat_id=chat_id, text="✅ Đăng ký NHÓM ADMIN thành công! Sẽ nhận cảnh báo lỗi hệ thống.")
        elif token == settings.ZALO_PUBLIC_GROUP_TOKEN:
            self.chats[chat_id] = "public"
            self._save_chats()
            await context.bot.send_message(chat_id=chat_id, text="✅ Đăng ký NHÓM PUBLIC thành công! Có thể tra cứu trạng thái trạm.")
        else:
            await context.bot.send_message(chat_id=chat_id, text="❌ Token không hợp lệ!")

    async def _status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        role = self.get_role(chat_id)
        
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

    async def _errors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        role = self.get_role(chat_id)
        
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
