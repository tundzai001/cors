from __future__ import annotations

import json
import time
import asyncio
import logging
import inspect
import contextvars
import unicodedata
import hashlib
import uuid
import copy
import re
import difflib
from collections import deque
from typing import Optional, Dict, List, Set, Callable, Awaitable, Any
from datetime import datetime
from zoneinfo import ZoneInfo

from .database import settings
from . import auth
from .zalo_assistant import ZaloPrincipal, payload_fingerprint, zalo_repository

try:
    from zalo_bot import Update
    from zalo_bot.ext import ApplicationBuilder, CommandHandler, ContextTypes
    ZALO_AVAILABLE = True
except ImportError:
    ZALO_AVAILABLE = False

logger = logging.getLogger(__name__)
ZALO_TRACE_VERSION = "20261017-public-menu-v13"

BOT_COMMANDS = (
    "start", "menu", "help", "cors", "register", "link", "notifications", "status", "errors", "stations", "station",
    "config", "configset", "coord", "stationlock", "stationunlock", "stationreset",
    "stationdelete", "rovers", "rover", "rovercreate", "roverupdate", "roverdelete",
    "roverrenew", "roverblock", "roverunblock", "confirm", "cancel", "logout", "unlink",
)

# ---------------------------------------------------------------------------
# Smart dispatch: map command names to handler method names
# ---------------------------------------------------------------------------
_COMMAND_DISPATCH = {
    "cors": "_cors_cmd",
    "start": "_start_cmd",
    "help": "_help_cmd",
    "menu": "_menu_cmd",
    "register": "_register_cmd",
    "link": "_link_cmd",
    "notifications": "_notifications_cmd",
    "status": "_status_cmd",
    "errors": "_errors_cmd",
    "stations": "_stations_cmd",
    "station": "_station_cmd",
    "config": "_config_cmd",
    "configset": "_configset_cmd",
    "coord": "_coord_cmd",
    "rovers": "_rovers_cmd",
    "rover": "_rover_cmd",
    "rovercreate": "_rovercreate_cmd",
    "roverupdate": "_roverupdate_cmd",
    "roverdelete": "_roverdelete_cmd",
    "roverrenew": "_roverrenew_cmd",
    "roverblock": "_roverblock_cmd",
    "roverunblock": "_roverunblock_cmd",
    "stationlock": "_stationlock_cmd",
    "stationunlock": "_stationunlock_cmd",
    "stationreset": "_stationreset_cmd",
    "stationdelete": "_stationdelete_cmd",
    "confirm": "_confirm_cmd",
    "cancel": "_cancel_cmd",
    "logout": "_logout_cmd",
    "unlink": "_logout_cmd",
}


def _normalize_to_ascii(text: str) -> str:
    """Strip Vietnamese diacritics and lowercase for NLP matching."""
    text = text.lower().replace("đ", "d").strip()
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _is_cors_status_question(text: str) -> bool:
    """Recognize public Cục health questions with or without Vietnamese accents."""
    normalized = _normalize_to_ascii(text).lstrip("\u200b\u200c\u200d\ufeff").strip()
    if normalized.startswith("/"):
        return False  # Explicit commands keep their own permissions and arguments.
    words = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    if re.search(r"\b(?:base|rover)\b", words):
        return False
    target = (re.search(r"\b(?:cuc|vngeonet)\b", words)
              or "14.238.1.125" in normalized
              or re.match(r"^cors\b", words))
    if not target:
        return False
    if words in {"cuc", "cors", "cors cuc", "vngeonet"}:
        return True
    return bool(re.search(
        r"\b(?:tinh trang|tinh hinh|trang thai|the nao|ra sao|sap|song|chet|loi|"
        r"on|online|offline|hoat dong|ket noi|chay|kiem tra|check|status|mat|ngat|"
        r"sao roi|bi gi|co van de|su co|dung (?:duoc|dc|dk)|"
        r"do (?:duoc|dc|dk|on|khong|ko|k)|co do|ok|down|up)\b",
        words,
    ))


# ---------------------------------------------------------------------------
# Vietnamese NLP intent patterns
# Each tuple: (compiled_regex, command_name)
# Patterns are matched against ASCII-normalized (diacritic-free) input.
# Order matters: more specific patterns first.
# ---------------------------------------------------------------------------
_NLP_INTENT_PATTERNS = [
    # === Rover: dangerous / mutating (Put Rover explicit matches first) ===
    (re.compile(r"^(?:xoa(?:\s+bo)?|delete)\s+rover\s+(.+)$", re.I), "roverdelete"),
    (re.compile(r"^(?:tao|create|them(?:\s+moi)?)\s+rover(?:\s+(.+))?$", re.I), "rovercreate"),
    (re.compile(r"^(?:sua|update|cap\s*nhat|chinh\s*sua)(?:\s+(?:cau\s*hinh|config))?\s+rover\s+(.+)$", re.I), "roverupdate"),
    (re.compile(r"^(?:gia\s*han(?:\s+them)?|renew)\s+rover\s+(.+)$", re.I), "roverrenew"),
    (re.compile(r"^(?:chan|block|khoa)\s+rover\s+(.+)$", re.I), "roverblock"),
    (re.compile(r"^(?:bo\s*chan|unblock|mo\s*chan|go\s*chan)\s+rover\s+(.+)$", re.I), "roverunblock"),

    # === Station: dangerous / mutating (Make "tram" optional but exclude "rover" matches) ===
    (re.compile(r"^(?:xoa(?:\s+bo)?|delete)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationdelete"),
    (re.compile(r"^(?:khoa|lock)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationlock"),
    (re.compile(r"^(?:mo\s*khoa|unlock|go\s*khoa)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationunlock"),
    (re.compile(r"^(?:reset|khoi\s*dong\s*lai|restart)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "stationreset"),

    # === Station: config / coord ===
    (re.compile(r"^(?:sua|chinh\s*sua|thay\s*doi|configset)(?:\s+(?:cau\s*hinh|config))?(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "configset"),
    (re.compile(r"^(?:xem|kiem\s*tra|doc|hien\s*thi)?\s*(?:cau\s*hinh|config)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "config"),
    (re.compile(r"^(?:dat\s+)?(?:toa\s*do|coord(?:inates?)?)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "coord"),

    # === Station: read ===
    (re.compile(r"^(?:xem|thong\s*tin|chi\s*tiet|info|kiem\s*tra)(?:\s+(?:tram|station))?\s+(?!rover\s)(.+)$", re.I), "station"),
    (re.compile(r"^(?:tram|station)\s+(?!rover\s)(.+)$", re.I), "station"),
    (re.compile(r"^(?:danh\s*sach\s*(?:tram|station)|cac\s*tram|list\s*station)s?(?:\s+(.*))?$", re.I), "stations"),

    # === Rover: read ===
    (re.compile(r"^(?:xem|thong\s*tin|info|kiem\s*tra|cau\s*hinh|config)\s+rover\s+(.+)$", re.I), "rover"),
    (re.compile(r"^rover\s+(.+)$", re.I), "rover"),
    (re.compile(r"^(?:danh\s*sach\s*rover|cac\s*rover|list\s*rover)s?(?:\s+(.*))?$", re.I), "rovers"),

    # === General (no args or optional args) ===
    (re.compile(r"^(?:trang\s*thai|tinh\s*hinh|status|tong\s*quan|overview|toan\s*canh)$", re.I), "status"),
    (re.compile(r"^(?:loi|canh\s*bao|error|warning|alert|bao\s*loi)s?$", re.I), "errors"),
    (re.compile(r"^(?:tro\s*giup|huong\s*dan|help|ho\s*tro)$", re.I), "help"),
    (re.compile(r"^menu$", re.I), "menu"),
    (re.compile(r"^(?:thong\s*bao|notification)s?$", re.I), "notifications"),
    (re.compile(r"^(?:huy|cancel)(?:\s+(.+))?$", re.I), "cancel"),
    (re.compile(r"^(?:xac\s*nhan|confirm)(?:\s+(.+))?$", re.I), "confirm"),
    (re.compile(r"^(?:dang\s*xuat|logout|ngat\s*ket\s*noi)(?:\s+(.+))?$", re.I), "logout"),
]


class _SyntheticContext:
    """Minimal context for dispatching commands detected without '/' prefix."""
    def __init__(self, args: List[str]):
        self.args = args

SERVICE_CONFIG_CHOICES = {
    "1": ("server1_enabled", "Kích hoạt Server 1"),
    "2": ("server1_stream_on_demand", "S1: Ngủ khi không có rover"),
    "3": ("serverhost1", "S1: Địa chỉ máy chủ"),
    "4": ("port1", "S1: Cổng kết nối"),
    "5": ("mountpoint1", "S1: Mountpoint"),
    "6": ("username1", "S1: Tên đăng nhập"),
    "7": ("password1", "S1: Mật khẩu"),
    "8": ("ntrip_version1", "S1: Phiên bản NTRIP"),
    "9": ("server2_enabled", "Kích hoạt Server 2"),
    "10": ("server2_stream_on_demand", "S2: Ngủ khi không có rover"),
    "11": ("serverhost2", "S2: Địa chỉ máy chủ"),
    "12": ("port2", "S2: Cổng kết nối"),
    "13": ("mountpoint2", "S2: Mountpoint"),
    "14": ("username2", "S2: Tên đăng nhập"),
    "15": ("password2", "S2: Mật khẩu"),
    "16": ("ntrip_version2", "S2: Phiên bản NTRIP"),
    "17": ("reconnectioninterval", "Thời gian kết nối lại"),
    "18": ("rtcm_enabled", "Kích hoạt nhận RTCM"),
    "19": ("rtcmserver1", "RTCM: Địa chỉ máy chủ"),
    "20": ("rtcmport1", "RTCM: Cổng kết nối"),
    "21": ("rtcmmountpoint1", "RTCM: Mountpoint nguồn"),
    "22": ("rtcmusername1", "RTCM: Tên đăng nhập"),
    "23": ("rtcmpassword1", "RTCM: Mật khẩu"),
    "24": ("rtcmntripversion1", "RTCM: Phiên bản NTRIP"),
}

ROVER_UPDATE_CHOICES = {
    "1": ("password", "Password"),
    "2": ("mountpoints", "Mountpoints được phép"),
    "3": ("expiry_days", "Số ngày gia hạn"),
    "4": ("max_connections", "Số kết nối tối đa"),
    "5": ("show_position", "Hiển thị vị trí"),
    "6": ("rtcm_cycle_enabled", "Bật/tắt RTCM cycle"),
    "7": ("rtcm_disconnect_interval_seconds", "Chu kỳ ngắt RTCM (giây)"),
    "8": ("rtcm_disconnect_duration_seconds", "Thời gian ngắt RTCM (giây)"),
    "9": ("is_shared_with_managers", "Chia sẻ với quản lý"),
}

SERVICE_BOOLEAN_FIELDS = {
    "server1_enabled", "server1_stream_on_demand", "server2_enabled",
    "server2_stream_on_demand", "stream_on_demand", "rtcm_enabled",
}
ROVER_BOOLEAN_FIELDS = {"show_position", "rtcm_cycle_enabled", "is_shared_with_managers"}

ACTION_PERMISSIONS = {
    "overview": auth.Permission.VIEW_DEVICES,
    "alerts": auth.Permission.VIEW_CONFIG,
    "smart_search": auth.Permission.VIEW_DEVICES,
    "stations": auth.Permission.VIEW_DEVICES,
    "station": auth.Permission.VIEW_DEVICES,
    "config": auth.Permission.VIEW_CONFIG,
    "config_edit_context": auth.Permission.EDIT_SERVICE_CONFIG,
    "coord_set": auth.Permission.EDIT_COORDINATES,
    "config_set": auth.Permission.EDIT_SERVICE_CONFIG,
    "station_lock": auth.Permission.MANAGE_USERS,
    "station_unlock": auth.Permission.MANAGE_USERS,
    "station_reset": auth.Permission.DELETE_DEVICE,
    "station_delete": auth.Permission.DELETE_DEVICE,
    "rovers": auth.Permission.MANAGE_CASTER,
    "rover": auth.Permission.MANAGE_CASTER,
    "rover_edit_context": auth.Permission.MANAGE_CASTER,
    "rover_create": auth.Permission.MANAGE_CASTER,
    "rover_update": auth.Permission.MANAGE_CASTER,
    "rover_delete": auth.Permission.MANAGE_CASTER,
    "rover_renew": auth.Permission.MANAGE_CASTER,
    "rover_block": auth.Permission.MANAGE_CASTER,
    "rover_unblock": auth.Permission.MANAGE_CASTER,
}

DANGEROUS_ACTIONS = {
    "station_delete", "station_reset", "rover_delete", "rover_block",
    "security_block_ip", "security_disable", "license_bypass", "station_rename_serial",
}

class ZaloBotService:
    def __init__(self):
        self.app = None
        self.dispatcher = None
        self.chats = {}
        self.CHATS_FILE = "zalo_chats.json"

        self.prev_statuses = {}
        self.prev_alerts = set()
        self._status_snapshot_ready = False
        self._alert_snapshot_ready = False

        self._get_devices_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        self._get_alerts_cb: Optional[Callable[[], Awaitable[List[dict]]]] = None
        self._admin_action_cb: Optional[Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
        self._get_binding_scopes_cb: Optional[Callable[[], Awaitable[Dict[int, Set[str]]]]] = None
        self._pending_admin_actions: Dict[str, Dict[str, Any]] = {}
        self._conversation_states: Dict[str, Dict[str, Any]] = {}
        self._chat_mention_prefixes: Dict[str, str] = {}
        self._revoked_chat_ids: Set[str] = set()
        
        self._task = None
        self._polling_task = None
        self._webhook_setup_task = None
        self._cors_status_task = None
        self._cors_outbound = contextvars.ContextVar("zalo_cors_outbound", default=None)
        self._debug_events = deque(maxlen=100)
        self._active_trace_id = contextvars.ContextVar("zalo_trace_id", default=None)
        self._allow_revoked_outbound = contextvars.ContextVar(
            "zalo_allow_revoked_outbound", default=False
        )
        self._processed_msg_ids: Dict[str, float] = {}

    def set_callbacks(
        self, get_devices: Callable, get_alerts: Callable,
        admin_action: Optional[Callable] = None,
        get_binding_scopes: Optional[Callable] = None,
    ):
        self._get_devices_cb = get_devices
        self._get_alerts_cb = get_alerts
        self._admin_action_cb = admin_action
        self._get_binding_scopes_cb = get_binding_scopes

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

    def forget_chat(self, chat_id: str) -> None:
        """Remove every process-local trace of a revoked DB binding."""
        chat_id = str(chat_id)
        removed_legacy = self.chats.pop(chat_id, None) is not None
        for key in list(self._conversation_states):
            if key == chat_id or key.startswith(f"{chat_id}:"):
                self._conversation_states.pop(key, None)
        for key in list(self._pending_admin_actions):
            pending = self._pending_admin_actions.get(key) or {}
            if str(pending.get("chat_id") or "") == chat_id:
                self._pending_admin_actions.pop(key, None)
        self._chat_mention_prefixes.pop(chat_id, None)
        if removed_legacy:
            self._save_chats()

    async def _resolve_principal(self, update: Update) -> ZaloPrincipal:
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        chat_type = self._get_chat_type(update)
        principal = await zalo_repository.resolve_principal(chat_id, sender_id, chat_type)
        if principal.is_linked:
            return principal

        return principal

    @staticmethod
    def _principal_payload(principal: ZaloPrincipal) -> Dict[str, Any]:
        return {
            "actor_user_id": principal.user_id,
            "actor_username": principal.username,
            "actor_role": principal.role,
            "binding_type": principal.binding_type,
            "permissions": sorted(principal.permissions),
            "station_scope_mode": principal.station_scope_mode,
            "allowed_bases": sorted(principal.allowed_bases),
        }

    async def _maybe_await(self, value):
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _is_zalo_timeout(exc: BaseException) -> bool:
        current: Optional[BaseException] = exc
        for _ in range(6):
            if current is None:
                break
            name = type(current).__name__.lower()
            module = type(current).__module__.lower()
            if "timeout" in name or "timedout" in name or ("httpx" in module and "timeout" in str(current).lower()):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _redact_command_secret(text: str) -> str:
        text = re.sub(r"(?i)(/register)(\s+)\S+", r"\1\2<redacted>", text)
        text = re.sub(r"(?i)(/link)(\s+)\S+", r"\1\2<redacted>", text)
        text = re.sub(
            r"(?i)(/rovercreate\s+\S+\s+\S+\s+)\S+",
            r"\1<redacted>",
            text,
        )
        return re.sub(
            r"(?i)((?:[A-Za-z0-9_]*password[A-Za-z0-9_]*|passwd|secret[A-Za-z0-9_]*|token[A-Za-z0-9_]*|pass|mk|s2_mk)\s*(?:=|\s)\s*)(\"[^\"]*\"|'[^']*'|\S+)",
            r"\1<redacted>",
            text,
        )

    @staticmethod
    def _safe_debug_value(value: Any, depth: int = 0) -> Any:
        """Keep webhook diagnostics useful without leaking credentials."""
        if depth > 8:
            return "<max-depth>"
        if isinstance(value, dict):
            safe = {}
            for key, item in value.items():
                key_text = str(key)
                if any(part in key_text.lower() for part in ("token", "secret", "authorization", "password")):
                    safe[key_text] = "<redacted>"
                elif key_text.lower() in {"text", "content", "message_text"} and isinstance(item, str):
                    safe[key_text] = ZaloBotService._redact_command_secret(item)
                else:
                    safe[key_text] = ZaloBotService._safe_debug_value(item, depth + 1)
            return safe
        if isinstance(value, (list, tuple)):
            return [ZaloBotService._safe_debug_value(item, depth + 1) for item in value[:50]]
        if isinstance(value, str):
            return value if len(value) <= 1000 else value[:1000] + "...<truncated>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:1000]

    @staticmethod
    def _first_nested_value(data: Any, wanted_keys: Set[str]) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).lower() in wanted_keys and value not in (None, "", [], {}):
                    return value
            for value in data.values():
                found = ZaloBotService._first_nested_value(value, wanted_keys)
                if found not in (None, "", [], {}):
                    return found
        elif isinstance(data, list):
            for value in data:
                found = ZaloBotService._first_nested_value(value, wanted_keys)
                if found not in (None, "", [], {}):
                    return found
        return None

    def _summarize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        root = payload.get("result", payload)
        root = root if isinstance(root, dict) else {}
        message = root.get("message") or root.get("edited_message") or {}
        message = message if isinstance(message, dict) else {}
        chat = message.get("chat") or root.get("chat") or {}
        chat = chat if isinstance(chat, dict) else {}
        sender = message.get("from") or message.get("sender") or root.get("sender") or {}
        sender = sender if isinstance(sender, dict) else {}
        return {
            "msg_id": message.get("msg_id") or message.get("message_id") or root.get("msg_id") or root.get("message_id"),
            "event": self._first_nested_value(payload, {"event", "event_name", "update_type", "type"}),
            "chat_id": chat.get("id") or self._first_nested_value(payload, {"chat_id", "group_id", "thread_id"}),
            "chat_type": chat.get("type") or self._first_nested_value(payload, {"chat_type", "thread_type"}),
            "sender_id": sender.get("id") or self._first_nested_value(payload, {"sender_id", "from_id", "user_id"}),
            "text": self._redact_command_secret(
                str(message.get("text") or self._first_nested_value(payload, {"text", "content", "message_text"}) or "")
            ),
            "top_level_keys": sorted(str(key) for key in payload.keys()),
        }

    @staticmethod
    def _normalize_group_command(update_data: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, str]]]:
        """Turn a Zalo group mention such as '@Bot name /status' into '/status'."""
        message = update_data.get("message") if isinstance(update_data, dict) else None
        if not isinstance(message, dict):
            return update_data, None

        text = message.get("text")
        if not isinstance(text, str):
            return update_data, None

        stripped = text.strip()
        if stripped.startswith("/") or not stripped.startswith("@"):
            return update_data, None

        # Do not require whitespace/end after the command. Zalo may append an
        # invisible mention separator that looks normal in logs but breaks a
        # strict regex lookahead.
        command_match = re.search(r"/[A-Za-z][A-Za-z0-9_]*", stripped)
        if not command_match:
            return update_data, None

        command = command_match.group(0)
        tail = stripped[command_match.end():].lstrip(" \t\r\n\u200b\u200c\u200d\ufeff")
        normalized_text = command if not tail else f"{command} {tail}"
        normalized_data = copy.deepcopy(update_data)
        normalized_data["message"]["text"] = normalized_text
        return normalized_data, {
            "original_text": ZaloBotService._redact_command_secret(text),
            "normalized_text": ZaloBotService._redact_command_secret(normalized_text),
            "mention_prefix": stripped[:command_match.start()].strip(),
        }

    def _find_debug_event(self, trace_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not trace_id:
            return None
        return next((event for event in reversed(self._debug_events) if event["trace_id"] == trace_id), None)

    def get_debug_events(self, limit: int = 20) -> Dict[str, Any]:
        events = list(self._debug_events)[-max(1, min(limit, 100)):]
        return {
            "code_version": ZALO_TRACE_VERSION,
            "mode": self._bot_mode(),
            "sdk_available": ZALO_AVAILABLE,
            "bot_enabled": bool(settings.ENABLE_ZALO_BOT),
            "app_initialized": self.app is not None,
            "events": list(reversed(events)),
        }

    def clear_debug_events(self) -> None:
        self._debug_events.clear()

    def _install_send_trace(self) -> None:
        bot = getattr(self.app, "bot", None) if self.app else None
        if not bot or getattr(bot, "_cors_send_trace_installed", False):
            return

        original_send_message = getattr(bot, "send_message", None)
        if not callable(original_send_message):
            logger.warning("Zalo outbound tracing unavailable: bot.send_message is missing")
            return

        async def traced_send_message(*args, **kwargs):
            trace_id = self._active_trace_id.get()
            chat_id = kwargs.get("chat_id", args[0] if args else None)
            text = kwargs.get("text", args[1] if len(args) > 1 else None)
            cors_context = self._cors_outbound.get()
            # Change delivery already owns the preference row lock. Reuse that
            # policy rather than acquire a second auth session while holding it.
            policy = cors_context["policy"] if isinstance(cors_context, dict) else await zalo_repository.chat_delivery_policy(str(chat_id))
            cors_kind = cors_context.get("kind") if isinstance(cors_context, dict) else cors_context
            if (not policy["active"] or (policy["mode"] == "cors_only" and not cors_kind)
                    or (cors_kind == "alert" and not (policy["mode"] == "cors_only" and policy["cors_notify_changes"]))):
                logger.info("Zalo outbound suppressed by chat policy for chat_id=%s", chat_id)
                return None
            outbound = {
                "at": datetime.now().astimezone().isoformat(),
                "chat_id": str(chat_id) if chat_id is not None else None,
                "text": self._safe_debug_value(text),
                "status": "sending",
            }
            event = self._find_debug_event(trace_id)
            if event is not None:
                event.setdefault("outbound", []).append(outbound)
                event["outbound_count"] = len(event["outbound"])
            if (
                str(chat_id) in self._revoked_chat_ids
                and not self._allow_revoked_outbound.get()
            ):
                outbound["status"] = "suppressed_revoked"
                logger.info(
                    "[ZALO TRACE %s] suppressed outbound to revoked chat_id=%s",
                    trace_id or "background", chat_id,
                )
                return None
            logger.info(
                "[ZALO TRACE %s] outbound sending chat_id=%s text=%r",
                trace_id or "background", chat_id,
                self._redact_command_secret(str(text or "")),
            )
            attempts = max(1, min(int(getattr(settings, "ZALO_SEND_RETRY_ATTEMPTS", 3) or 3), 5))
            base_delay = max(0.1, min(float(getattr(settings, "ZALO_SEND_RETRY_BASE_DELAY_SECONDS", 0.4) or 0.4), 5.0))
            try:
                for attempt in range(1, attempts + 1):
                    outbound["attempts"] = attempt
                    try:
                        result = await self._maybe_await(original_send_message(*args, **kwargs))
                        break
                    except Exception as exc:
                        if not self._is_zalo_timeout(exc) or attempt >= attempts:
                            raise
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            "[ZALO TRACE %s] outbound timeout chat_id=%s attempt=%s/%s; retrying in %.1fs",
                            trace_id or "background", chat_id, attempt, attempts, delay,
                        )
                        await asyncio.sleep(delay)
                outbound["status"] = "sent"
                outbound["result"] = self._safe_debug_value(
                    result.to_dict() if hasattr(result, "to_dict") else result
                )
                logger.info("[ZALO TRACE %s] outbound sent chat_id=%s", trace_id or "background", chat_id)
                return result
            except Exception as exc:
                outbound["status"] = "error"
                outbound["error"] = f"{type(exc).__name__}: {exc}"
                logger.error("[ZALO TRACE %s] outbound failed chat_id=%s: %s", trace_id or "background", chat_id, exc, exc_info=True)
                raise

        try:
            setattr(bot, "send_message", traced_send_message)
            setattr(bot, "_cors_send_trace_installed", True)
        except Exception as exc:
            logger.warning("Could not install Zalo outbound tracing: %s", exc)

    def _bot_mode(self) -> str:
        mode = str(getattr(settings, "ZALO_BOT_MODE", "polling") or "polling").strip().lower()
        return mode if mode in {"polling", "webhook"} else "polling"

    def _register_handlers(self, target) -> None:
        target.add_handler(CommandHandler("cors", self._cors_cmd))
        target.add_handler(CommandHandler("start", self._start_cmd))
        target.add_handler(CommandHandler("help", self._help_cmd))
        target.add_handler(CommandHandler("menu", self._menu_cmd))
        target.add_handler(CommandHandler("register", self._register_cmd))
        target.add_handler(CommandHandler("link", self._link_cmd))
        target.add_handler(CommandHandler("notifications", self._notifications_cmd))
        target.add_handler(CommandHandler("status", self._status_cmd))
        target.add_handler(CommandHandler("errors", self._errors_cmd))
        target.add_handler(CommandHandler("stations", self._stations_cmd))
        target.add_handler(CommandHandler("station", self._station_cmd))
        target.add_handler(CommandHandler("config", self._config_cmd))
        target.add_handler(CommandHandler("configset", self._configset_cmd))
        target.add_handler(CommandHandler("coord", self._coord_cmd))
        target.add_handler(CommandHandler("rovers", self._rovers_cmd))
        target.add_handler(CommandHandler("rover", self._rover_cmd))
        target.add_handler(CommandHandler("rovercreate", self._rovercreate_cmd))
        target.add_handler(CommandHandler("roverupdate", self._roverupdate_cmd))
        target.add_handler(CommandHandler("roverdelete", self._roverdelete_cmd))
        target.add_handler(CommandHandler("roverrenew", self._roverrenew_cmd))
        target.add_handler(CommandHandler("roverblock", self._roverblock_cmd))
        target.add_handler(CommandHandler("roverunblock", self._roverunblock_cmd))
        target.add_handler(CommandHandler("stationlock", self._stationlock_cmd))
        target.add_handler(CommandHandler("stationunlock", self._stationunlock_cmd))
        target.add_handler(CommandHandler("stationreset", self._stationreset_cmd))
        target.add_handler(CommandHandler("stationdelete", self._stationdelete_cmd))
        target.add_handler(CommandHandler("confirm", self._confirm_cmd))
        target.add_handler(CommandHandler("cancel", self._cancel_cmd))
        target.add_handler(CommandHandler("logout", self._logout_cmd))
        target.add_handler(CommandHandler("unlink", self._logout_cmd))

        try:
            from zalo_bot.ext import MessageHandler, filters
            target.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._conversation_message))
            target.add_handler(MessageHandler(filters.COMMAND, self._unknown_cmd))
        except ImportError:
            pass

    def _build_app(self) -> None:
        if self.app:
            return
        self.app = ApplicationBuilder().token(settings.ZALO_BOT_TOKEN).build()
        self._install_send_trace()
        self._register_handlers(self.app)

    def _ensure_dispatcher(self):
        if self.dispatcher:
            return self.dispatcher
        try:
            from zalo_bot.ext import Dispatcher
        except ImportError:
            return None
        self.dispatcher = Dispatcher(self.app.bot, None, workers=0)
        self._register_handlers(self.dispatcher)
        return self.dispatcher

    async def _call_app_method(self, method_name: str) -> None:
        if not self.app or not hasattr(self.app, method_name):
            return
        method = getattr(self.app, method_name)
        await self._maybe_await(method())

    async def _delayed_set_webhook(self) -> None:
        await asyncio.sleep(3)
        webhook_url = str(getattr(settings, "ZALO_WEBHOOK_URL", "") or "").strip()
        secret_token = str(getattr(settings, "ZALO_WEBHOOK_SECRET", "") or "").strip()
        if not webhook_url or not secret_token:
            logger.warning("Zalo webhook mode is enabled but ZALO_WEBHOOK_URL or ZALO_WEBHOOK_SECRET is missing.")
            return
        if not self.app or not getattr(self.app, "bot", None):
            logger.error("Zalo SDK bot is not initialized; webhook registration was skipped.")
            return
        try:
            bot = self.app.bot
            async_set_webhook = getattr(bot, "_set_webhook_async", None)
            if async_set_webhook:
                # python-zalo-bot 0.1.9 exposes a synchronous set_webhook()
                # wrapper that calls asyncio.run(), which cannot run inside Uvicorn.
                await async_set_webhook(url=webhook_url, secret_token=secret_token)
            elif inspect.iscoroutinefunction(getattr(bot, "set_webhook", None)):
                await bot.set_webhook(url=webhook_url, secret_token=secret_token)
            else:
                raise RuntimeError("Installed Zalo SDK does not expose an async webhook API")
            logger.info("Zalo webhook registered: %s", webhook_url)
        except Exception as e:
            logger.error("Failed to register Zalo webhook: %s", e, exc_info=True)

    def _start_polling_task(self) -> None:
        logger.info("Starting Zalo polling loop...")

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
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Zalo Bot polling loop crashed: {e}. Restarting in 5s...", exc_info=True)
                    await asyncio.sleep(5)

        self._polling_task = asyncio.create_task(safe_polling_loop())

    async def _start_webhook_runtime(self) -> None:
        await self.app.bot.initialize()
        self._ensure_dispatcher()
        self._webhook_setup_task = asyncio.create_task(self._delayed_set_webhook())
        logger.info("Zalo webhook runtime is ready.")

    async def start(self, *, strict: bool = False) -> bool:
        try:
            if not ZALO_AVAILABLE or not settings.ENABLE_ZALO_BOT or not settings.ZALO_BOT_TOKEN:
                message = "Zalo Bot is disabled or missing requirements/token."
                logger.warning(message)
                if strict:
                    raise RuntimeError(message)
                return False

            self._load_chats()
            # The database is now the only authorization source.  Keeping the
            # old JSON cache would let a revoked group recreate its own binding.
            if self.chats:
                logger.info("Retiring %s legacy Zalo chat cache entries", len(self.chats))
                self.chats.clear()
                self._save_chats()
            logger.info("Initializing Zalo Bot Service...")
            self._build_app()

            if self._bot_mode() == "webhook":
                await self._start_webhook_runtime()
            else:
                self._start_polling_task()

            if settings.ENABLE_ZALO_ERROR_ALERTS and settings.ZALO_ENABLE_NOTIFICATIONS:
                self._task = asyncio.create_task(self._error_check_loop())
            self._cors_status_task = asyncio.create_task(self._cors_status_loop())
                
            logger.info("Zalo Bot Service started successfully.")
            return True
        except Exception as e:
            logger.error(f"FATAL ZALO BOT ERROR: {e}", exc_info=True)
            await self.stop()
            if strict:
                raise RuntimeError(f"Zalo Bot failed to start: {e}") from e
            return False

    async def process_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        trace_id = uuid.uuid4().hex[:12]
        summary = self._summarize_payload(payload)
        try:
            await self._hydrate_conversation_state(
                summary.get("chat_id"), summary.get("sender_id")
            )
            sensitive_input = self._is_sensitive_conversation_input(
                summary.get("chat_id"), summary.get("sender_id")
            )
        except Exception as exc:
            # If persistent state cannot be checked, fail closed for logging: an
            # ordinary message is less valuable than accidentally logging a secret.
            sensitive_input = True
            logger.warning("[ZALO TRACE %s] workflow hydration failed; inbound text redacted: %s", trace_id, exc)
        debug_payload = self._safe_debug_value(payload)
        if sensitive_input:
            summary["text"] = "<redacted>"
            debug_payload = self._redact_payload_message(debug_payload)
        event = {
            "trace_id": trace_id,
            "received_at": datetime.now().astimezone().isoformat(),
            "status": "received",
            "summary": summary,
            "payload": debug_payload,
            "sensitive_input": sensitive_input,
            "outbound": [],
        }
        self._debug_events.append(event)

        # --- Webhook deduplication ---
        msg_id = str(
            summary.get("msg_id") or summary.get("message_id")
            or payload.get("msg_id") or payload.get("message_id") or ""
        ).strip()
        if not msg_id:
            dedup_src = f"{summary.get('chat_id')}:{summary.get('sender_id')}:{summary.get('text', '')}"
            msg_id = hashlib.md5(dedup_src.encode("utf-8", errors="replace")).hexdigest()
        now = time.time()
        self._processed_msg_ids = {
            k: v for k, v in self._processed_msg_ids.items() if now - v < 60
        }
        if msg_id in self._processed_msg_ids:
            event["status"] = "deduplicated"
            logger.info("[ZALO TRACE %s] duplicate msg_id=%s, skipping", trace_id, msg_id)
            return {"trace_id": trace_id, "status": "deduplicated", "outbound_count": 0}
        self._processed_msg_ids[msg_id] = now

        logger.info(
            "[ZALO TRACE %s v=%s] inbound %s",
            trace_id,
            ZALO_TRACE_VERSION,
            json.dumps(event["summary"], ensure_ascii=False, default=str),
        )
        context_token = self._active_trace_id.set(trace_id)
        revoked_outbound_token = None

        try:
            if not ZALO_AVAILABLE:
                raise RuntimeError("Zalo SDK is not installed")
            if not settings.ENABLE_ZALO_BOT:
                raise RuntimeError("Zalo Bot is disabled")
            if not self.app:
                self._load_chats()
                self._build_app()
                if self._bot_mode() == "webhook":
                    self._ensure_dispatcher()

            update_data = payload.get("result") if isinstance(payload, dict) else None
            if update_data is None and isinstance(payload, dict):
                update_data = payload
            if not isinstance(update_data, dict):
                raise ValueError("Invalid Zalo webhook payload: missing result object")

            update_data, normalization = self._normalize_group_command(update_data)
            if normalization:
                event["group_command_normalization"] = normalization
                chat_id = str(event["summary"].get("chat_id") or "")
                if chat_id and normalization.get("mention_prefix"):
                    self._chat_mention_prefixes[chat_id] = normalization["mention_prefix"]
                logger.info(
                    "[ZALO TRACE %s] normalized group command %r -> %r",
                    trace_id,
                    normalization["original_text"],
                    normalization["normalized_text"],
                )

            update = Update.de_json(update_data, self.app.bot)
            if update is None:
                raise ValueError("Zalo SDK could not parse webhook payload")
            event["parsed_update"] = (
                "<redacted>" if sensitive_input else self._safe_debug_value(
                    update.to_dict() if hasattr(update, "to_dict") else update
                )
            )
            parsed_text = getattr(getattr(update, "message", None), "text", None)
            safe_parsed_text = (
                "<redacted>" if sensitive_input
                else self._redact_command_secret(parsed_text) if isinstance(parsed_text, str)
                else parsed_text
            )
            event["dispatch_text"] = safe_parsed_text
            logger.info("[ZALO TRACE %s] SDK dispatch text=%r", trace_id, safe_parsed_text)
            event["status"] = "parsed"

            if await zalo_repository.is_chat_paused(self._get_chat_id(update)):
                event["status"] = "paused_chat"
                return {"trace_id": trace_id, "status": "paused_chat", "outbound_count": 0}

            # Route public health before the SDK's exact command matching, which
            # does not accept /cors@Bot or invisible Zalo mention separators.
            text = str(parsed_text or "").strip().lstrip("\u200b\u200c\u200d\ufeff").strip()
            command = text.split(maxsplit=1)[0].lower() if text else ""
            command = command.split("@", 1)[0].rstrip("\u200b\u200c\u200d\ufeff")
            public_cors = command == "/cors" or _is_cors_status_question(text)
            public_menu = False
            if self._get_chat_type(update) == "group":
                principal = await zalo_repository.resolve_principal(
                    self._get_chat_id(update), self._get_sender_id(update), "group"
                )
                # New groups can call the bot by mention (for example '@Bot helo')
                # or ask for help without acquiring any Base permissions.
                public_menu = not principal.is_linked and command in {"/start", "/help", "/menu"}
                if not principal.is_linked and text.startswith("@") and "/" not in text:
                    public_cors = True
                if not principal.is_linked and not (public_cors or public_menu) and command != "/register":
                    event["status"] = "rejected_unlinked_group"
                    event["dispatch_method"] = None
                    guidance_token = self._allow_revoked_outbound.set(True)
                    try:
                        await update.message.reply_text(
                            "CORS Cục dùng công khai: nhắc tên bot kèm /cors, không cần đăng ký.\n"
                            "Lệnh Base này cần được cấp quyền. Admin vào Dashboard → Zalo Bot, "
                            "chọn quyền Base và tạo OTP, rồi gửi /register <OTP>."
                        )
                    finally:
                        self._allow_revoked_outbound.reset(guidance_token)
                    event["outbound_count"] = len(event["outbound"])
                    logger.info(
                        "[ZALO TRACE %s] rejected command from unlinked group chat_id=%s guidance_sent=true",
                        trace_id, self._get_chat_id(update),
                    )
                    return {
                        "trace_id": trace_id,
                        "status": event["status"],
                        "outbound_count": event["outbound_count"],
                    }
                if not principal.is_linked and command == "/register":
                    # Allow registration guidance/errors to be delivered. A
                    # successful OTP consumption establishes the new DB grant.
                    revoked_outbound_token = self._allow_revoked_outbound.set(True)

            dispatch_method = None
            if public_menu:
                dispatch_method = "public_menu"
                await self._public_menu_cmd(update, None)
            elif public_cors:
                dispatch_method = "public_cors"
                await self._cors_cmd(update, None)
            elif hasattr(self.app, "process_update"):
                dispatch_method = "application.process_update"
                await self._maybe_await(self.app.process_update(update))
            else:
                dispatcher = self._ensure_dispatcher()
                if dispatcher and hasattr(dispatcher, "process_update"):
                    dispatch_method = "dispatcher.process_update"
                    await self._maybe_await(dispatcher.process_update(update))
                elif hasattr(self.app, "update_queue"):
                    dispatch_method = "application.update_queue"
                    await self.app.update_queue.put(update)
                else:
                    raise RuntimeError("Zalo SDK does not expose a webhook update dispatcher")

            event["status"] = "dispatched"
            event["dispatch_method"] = dispatch_method
            event["outbound_count"] = len(event["outbound"])
            logger.info(
                "[ZALO TRACE %s] dispatched via=%s outbound_count=%s",
                trace_id,
                dispatch_method,
                event["outbound_count"],
            )
            return {
                "trace_id": trace_id,
                "status": event["status"],
                "outbound_count": event["outbound_count"],
            }
        except Exception as exc:
            event["status"] = "error"
            safe_error = self._redact_command_secret(f"{type(exc).__name__}: {exc}")
            event["error"] = safe_error
            logger.error("[ZALO TRACE %s] processing failed: %s", trace_id, safe_error)
            raise
        finally:
            if revoked_outbound_token is not None:
                self._allow_revoked_outbound.reset(revoked_outbound_token)
            self._active_trace_id.reset(context_token)

    async def stop(self):
        owned_tasks = [
            task for task in (self._task, self._polling_task, self._webhook_setup_task, self._cors_status_task)
            if task
        ]
        if self.app or owned_tasks:
            logger.info("Stopping Zalo Bot Service...")
            for task in owned_tasks:
                task.cancel()
            if owned_tasks:
                await asyncio.gather(*owned_tasks, return_exceptions=True)
        try:
            if self.app:
                if hasattr(self.app, 'stop'):
                    if asyncio.iscoroutinefunction(self.app.stop):
                        await self.app.stop()
                    else:
                        self.app.stop()
                if hasattr(self.app, 'shutdown'):
                    await self._maybe_await(self.app.shutdown())
                elif self._bot_mode() == "webhook" and getattr(self.app, "bot", None):
                    await self.app.bot.shutdown()
        except Exception as e:
            logger.error(f"Error stopping Zalo Bot: {e}")
        finally:
            self._task = None
            self._polling_task = None
            self._webhook_setup_task = None
            self._cors_status_task = None
            self.dispatcher = None
            self.app = None

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

    @staticmethod
    def _get_sender_id(update: Update) -> str:
        message = getattr(update, "message", None)
        sender = getattr(message, "from_user", None) if message else None
        return str(getattr(sender, "id", "unknown"))

    @staticmethod
    def _get_display_name(update: Update) -> Optional[str]:
        message = getattr(update, "message", None)
        chat = getattr(message, "chat", None) if message else None
        raw_chat_type = str(
            getattr(chat, "type", None) or getattr(chat, "chat_type", None) or ""
        ).lower()
        if "group" in raw_chat_type:
            for attr in ("title", "name", "full_name", "display_name"):
                value = str(getattr(chat, attr, "") or "").strip() if chat else ""
                if value:
                    return value
        sender = getattr(message, "from_user", None) if message else None
        if sender:
            values = [
                getattr(sender, "full_name", None),
                " ".join(
                    part for part in (
                        str(getattr(sender, "first_name", "") or "").strip(),
                        str(getattr(sender, "last_name", "") or "").strip(),
                    ) if part
                ),
                getattr(sender, "username", None),
                getattr(sender, "name", None),
            ]
            for value in values:
                value = str(value or "").strip()
                if value:
                    return value
        for attr in ("title", "name", "full_name", "display_name"):
            value = str(getattr(chat, attr, "") or "").strip() if chat else ""
            if value:
                return value
        return None

    def _get_chat_type(self, update: Update) -> str:
        """Return ``private`` or ``group`` across Zalo SDK payload variants."""
        message = getattr(update, "message", None)
        chat = getattr(message, "chat", None) if message else None
        raw_type = getattr(chat, "type", None) or getattr(chat, "chat_type", None)
        normalized = str(raw_type or "").strip().lower()
        if "group" in normalized:
            return "group"
        if normalized in {"private", "personal", "user", "direct"}:
            return "private"

        # Zalo private-chat payloads use the sender id as the chat id. This
        # fallback keeps context-aware replies working with older SDK models
        # that do not expose chat.type/chat_type.
        chat_id = self._get_chat_id(update)
        sender_id = self._get_sender_id(update)
        if chat_id not in {"", "unknown"} and sender_id not in {"", "unknown"}:
            return "private" if chat_id == sender_id else "group"
        return "private"

    @staticmethod
    def _registration_success_text(role: str, chat_type: str) -> str:
        is_group = chat_type == "group"
        if role == "admin":
            if is_group:
                return (
                    "✅ Nhóm này đã được cấp quyền ADMIN.\n"
                    "Mọi thành viên trong nhóm có thể dùng các lệnh quản trị.\n"
                    "Gõ /menu để mở menu quản trị."
                )
            return (
                "✅ Cuộc trò chuyện cá nhân này đã được cấp quyền ADMIN.\n"
                "Bạn có thể dùng đầy đủ các lệnh quản trị. Gõ /menu để bắt đầu."
            )
        if is_group:
            return (
                "✅ Nhóm này đã được cấp quyền tra cứu PUBLIC.\n"
                "Mọi thành viên có thể tra cứu trạng thái trạm. Gõ /help để xem cách dùng."
            )
        return (
            "✅ Cuộc trò chuyện cá nhân này đã được cấp quyền tra cứu PUBLIC.\n"
            "Gõ /help để xem cách dùng."
        )

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
            logger.error("Zalo admin read failed action=%s: %s", action, exc, exc_info=True)
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
            logger.error(
                "Zalo outbound delivery failed after data was prepared action=%s: %s",
                action, exc, exc_info=True,
            )
            return False

    def _find_device_locally(self, identifier: str, devices_list: List[dict]) -> tuple[Optional[dict], Optional[str]]:
        wanted = str(identifier or "").strip().lower()
        if not wanted:
            return None, "Bạn chưa nhập tên hoặc serial trạm."

        def get_search_key(val):
            normalized = unicodedata.normalize("NFD", str(val or "").lower())
            ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            return "".join(ch for ch in ascii_text if ch.isalnum())

        normalized_wanted = unicodedata.normalize("NFD", wanted)
        ascii_text = "".join(ch for ch in normalized_wanted if not unicodedata.combining(ch))
        stop_words = {
            "xem", "tim", "kiem", "tra", "cuu", "giup", "toi", "cho", "minh",
            "thong", "tin", "chi", "tiet", "tram", "rover", "tai", "khoan",
        }
        tokens = [
            t for t in re.findall(r"[a-z0-9_.-]+", ascii_text)
            if t not in stop_words
        ]
        needles = {get_search_key(wanted)}
        needles.update(get_search_key(token) for token in tokens)
        needles = {n for n in needles if len(n) >= 2}

        scored_devices = []
        for dev in devices_list:
            score = 0
            name_key = get_search_key(dev.get("name") or "")
            serial_key = get_search_key(dev.get("serial") or "")
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
            if score > 0:
                scored_devices.append((dev, score))

        scored_devices.sort(key=lambda x: -x[1])
        if not scored_devices:
            label_lookup = {}
            for dev in devices_list:
                k = get_search_key(dev.get("name") or dev.get("serial") or "")
                if k:
                    label_lookup[k] = f"trạm {dev.get('name') or dev.get('serial')}"
            suggestion_keys = difflib.get_close_matches(
                get_search_key(wanted), list(label_lookup), n=5, cutoff=0.35
            )
            if suggestion_keys:
                sugs = ", ".join(label_lookup[k] for k in suggestion_keys)
                return None, f"Không tìm thấy trạm '{identifier}'. Có phải bạn muốn: {sugs}?"
            return None, f"Không tìm thấy trạm '{identifier}'."

        top_dev, best_score = scored_devices[0]
        second_score = scored_devices[1][1] if len(scored_devices) > 1 else -1

        if best_score > second_score:
            return top_dev, None
        else:
            choices = ", ".join(f"trạm {d.get('name') or d.get('serial')}" for d, _ in scored_devices[:5])
            return None, f"Có nhiều trạm khớp '{identifier}': {choices}."

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

        # --- Validate and Resolve Device for Station Actions ---
        if action in {"station_delete", "station_lock", "station_unlock", "station_reset", "coord_set", "config_set"}:
            station_query = payload.get("station")
            if station_query and self._get_devices_cb:
                try:
                    devices_list = await self._get_devices_cb()
                    device, error_msg = self._find_device_locally(station_query, devices_list)
                    if error_msg:
                        await update.message.reply_text(f"❌ {error_msg}")
                        return
                    if not device:
                        await update.message.reply_text(f"❌ Không tìm thấy trạm '{station_query}'.")
                        return

                    device_name = device.get("name") or device.get("serial")
                    device_serial = device.get("serial")

                    # Special safety check for station_delete: must enter EXACT name or serial
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
                            return

                    # Normalize payload to use the exact serial
                    payload["station"] = device_serial

                    # Update preview to show exact device name & serial
                    if action == "station_delete":
                        preview = f"XÓA VĨNH VIỄN trạm {device_name} ({device_serial}) khỏi hệ thống"
                    elif action == "station_lock":
                        preview = f"KHÓA trạm {device_name} ({device_serial})"
                    elif action == "station_unlock":
                        preview = f"MỞ KHÓA trạm {device_name} ({device_serial})"
                    elif action == "station_reset":
                        preview = f"KHỞI ĐỘNG LẠI NGUỒN trạm {device_name} ({device_serial})"
                    elif action == "coord_set":
                        preview = (
                            f"Trạm: {device_name} ({device_serial})\n"
                            f"Vĩ độ (Lat): {payload.get('lat')}\nKinh độ (Lon): {payload.get('lon')}\n"
                            f"Độ cao ellipsoid (Alt): {payload.get('alt')} m\nĐộ chính xác: {payload.get('accuracy')} m"
                        )
                    elif action == "config_set":
                        changes = dict(payload.get("changes") or {})
                        config_labels = {field: label for field, label in SERVICE_CONFIG_CHOICES.values()}
                        change_lines = [
                            f"• {config_labels.get(field, field)}: {self._human_value(field, value)}"
                            for field, value in changes.items()
                        ]
                        preview = f"Sửa cấu hình trạm {device_name} ({device_serial})\n" + "\n".join(change_lines)

                except Exception as exc:
                    logger.error("Error resolving device in _queue_admin_action: %s", exc, exc_info=True)

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

        asyncio.create_task(expire_conversation())

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

    # ------------------------------------------------------------------
    # Smart intent detection: command-like text + Vietnamese NLP
    # ------------------------------------------------------------------

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
        if _is_cors_status_question(text):
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

    async def _conversation_message_impl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        key = self._conversation_key(update)
        state = self._conversation_states.get(key)
        if not state:
            answer = self._clean_conversation_answer(update, getattr(update.message, "text", ""))
            if not answer:
                return
            # --- Smart dispatch: try command detection / NLP before smart_search ---
            if await self._try_dispatch_intent(update, answer):
                return
            # --- Fallback: smart_search ---
            principal = await self._resolve_principal(update)
            if principal.is_linked and principal.allows(auth.Permission.VIEW_DEVICES):
                await self._admin_read(update, "smart_search", {"query": answer})
            else:
                await update.message.reply_text(
                    "Mình chưa thể tra cứu vì cuộc trò chuyện chưa được liên kết. "
                    "Chat cá nhân dùng /link <mã trên dashboard>; group dùng /register <mã group>."
                )
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
        step = state["step"]
        data = state["data"]

        if flow == "object_context":
            target = data.get("target")
            ctx_type = data.get("type")
            lowered = answer.strip().lower()
            
            # 1. Handle numbered menu choices
            if lowered in {"1", "2", "3", "4", "5"}:
                self._conversation_states.pop(key, None)
                if ctx_type == "station":
                    if lowered == "1":
                        await self._configset_cmd(update, _SyntheticContext([target]))
                    elif lowered == "2":
                        await self._coord_cmd(update, _SyntheticContext([target]))
                    elif lowered == "3":
                        await self._stationreset_cmd(update, _SyntheticContext([target]))
                    elif lowered == "4":
                        await self._stationlock_cmd(update, _SyntheticContext([target]))
                    elif lowered == "5":
                        await self._stationdelete_cmd(update, _SyntheticContext([target]))
                elif ctx_type == "rover":
                    if lowered == "1":
                        await self._roverupdate_cmd(update, _SyntheticContext([target]))
                    elif lowered == "2":
                        await self._roverrenew_cmd(update, _SyntheticContext([target]))
                    elif lowered == "3":
                        await self._roverblock_cmd(update, _SyntheticContext([target]))
                    elif lowered == "4":
                        await self._roverunblock_cmd(update, _SyntheticContext([target]))
                    elif lowered == "5":
                        await self._roverdelete_cmd(update, _SyntheticContext([target]))
                return
            
            # 2. Handle natural language quick edits (One-line parsing)
            # Try to see if the user typed something like 'port 2101 mk 123'
            try:
                parsed = self._parse_key_values(answer.split())
                if parsed:
                    self._conversation_states.pop(key, None)
                    args = [target] + answer.split()
                    if ctx_type == "station":
                        await self._configset_cmd(update, _SyntheticContext(args))
                    elif ctx_type == "rover":
                        await self._roverupdate_cmd(update, _SyntheticContext(args))
                    return
            except ValueError:
                pass # Not a key-value assignment, fall through
            
            # 3. Fallback: User typed something completely unrelated to the context
            # Clear context and re-evaluate as a fresh intent
            self._conversation_states.pop(key, None)
            if await self._try_dispatch_intent(update, answer):
                return
            principal = await self._resolve_principal(update)
            if principal.is_linked and principal.allows(auth.Permission.VIEW_DEVICES):
                await self._admin_read(update, "smart_search", {"query": answer})
            else:
                await update.message.reply_text("Không tìm thấy lệnh hoặc bạn chưa liên kết tài khoản.")
            return

        if flow == "notification_setup":
            if step == "severity":
                lowered = answer.lower()
                if lowered in {"1", "critical", "nghiem trong", "nghiêm trọng"}:
                    data.update({"enabled": True, "min_severity": "critical"})
                elif lowered in {"2", "warning", "canh bao", "cảnh báo"}:
                    data.update({"enabled": True, "min_severity": "warning"})
                elif lowered in {"3", "off", "tat", "tắt", "khong", "không"}:
                    data.update({"enabled": False, "min_severity": "critical"})
                else:
                    await update.message.reply_text("Chọn 1, 2 hoặc 3 để mình đặt mức cảnh báo.")
                    return
                if not data.get("enabled", True):
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
                    return
                if data.get("scope_locked"):
                    state["step"] = "quiet"
                    scope_mode = str((data.get("scopes") or {}).get("mode") or "all")
                    scope_text = (
                        "tất cả trạm" if scope_mode == "all"
                        else "không có trạm" if scope_mode == "none"
                        else f"{len((data.get('scopes') or {}).get('values') or [])} trạm admin đã chọn"
                    )
                    locked_by = "group" if data.get("binding_type") in {"group_admin", "group_public"} else "tài khoản"
                    await update.message.reply_text(
                        f"Phạm vi cảnh báo được khóa theo {locked_by}: {scope_text}.\n\n"
                        "2/2 — Giờ im lặng:\n"
                        "1. Không đặt giờ im lặng\n"
                        "2. 22:00-07:00\n"
                        "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
                    )
                    return
                state["step"] = "scope"
                await update.message.reply_text(
                    "2/3 — Phạm vi cảnh báo:\n"
                    "1. Tất cả trạm/rover trong phạm vi được xem\n"
                    "2. Chỉ một số trạm/rover\n"
                    "3. Không nhận cảnh báo nội bộ"
                )
                return
            if step == "scope":
                lowered = answer.lower()
                if lowered in {"1", "all", "tat ca", "tất cả"}:
                    data["scopes"] = {"mode": "all"}
                    state["step"] = "quiet"
                elif lowered in {"2", "chon", "chọn", "selected"}:
                    state["step"] = "scope_values"
                    await update.message.reply_text(
                        "Nhập danh sách tên trạm hoặc username rover, cách nhau bằng dấu phẩy hoặc dấu chấm phẩy."
                    )
                    return
                elif lowered in {"3", "none", "off", "tat", "tắt", "khong", "không"}:
                    data["enabled"] = False
                    data["scopes"] = {"mode": "none"}
                    state["step"] = "quiet"
                else:
                    await update.message.reply_text("Chọn 1, 2 hoặc 3 cho phạm vi cảnh báo.")
                    return
                await update.message.reply_text(
                    "3/3 — Giờ im lặng:\n"
                    "1. Không đặt giờ im lặng\n"
                    "2. 22:00-07:00\n"
                    "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
                )
                return
            if step == "scope_values":
                selected = self._split_list_value(answer)
                if not selected:
                    await update.message.reply_text("Danh sách chưa hợp lệ. Ví dụ: HCM01; rover01")
                    return
                data["scopes"] = {"mode": "selected", "values": selected}
                state["step"] = "quiet"
                await update.message.reply_text(
                    "3/3 — Giờ im lặng:\n"
                    "1. Không đặt giờ im lặng\n"
                    "2. 22:00-07:00\n"
                    "Hoặc nhập trực tiếp dạng HH:MM-HH:MM."
                )
                return
            if step == "quiet":
                quiet_start = quiet_end = None
                lowered = answer.lower()
                if lowered in {"1", "khong", "không", "none", "no"}:
                    pass
                elif lowered in {"2", "dem", "đêm", "night"}:
                    quiet_start, quiet_end = "22:00", "07:00"
                else:
                    match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*", answer)
                    if not match:
                        await update.message.reply_text("Giờ im lặng cần dạng HH:MM-HH:MM, ví dụ 22:00-07:00.")
                        return
                    quiet_start, quiet_end = match.group(1), match.group(2)
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
                scope_mode = str((data.get("scopes") or {}).get("mode") or "all")
                scope_text = (
                    "tất cả trạm" if scope_mode == "all"
                    else "không có trạm" if scope_mode == "none"
                    else f"{len((data.get('scopes') or {}).get('values') or [])} trạm được chọn"
                )
                await update.message.reply_text(
                    f"✅ Đã lưu thiết lập cảnh báo.\n"
                    f"• Mức nhận: {severity}\n"
                    f"• Phạm vi: {scope_text}\n"
                    f"• Giờ im lặng: {quiet}\n\n"
                    "Gõ /menu để bắt đầu dùng bot."
                )
                return

        if flow in {"station_read", "config_read"}:
            success = await self._admin_read(
                update, "station" if flow == "station_read" else "config", {"station": answer}
            )
            if success:
                self._conversation_states.pop(key, None)
            else:
                await update.message.reply_text("Bạn có thể nhập lại tên trạm, hoặc gõ /cancel để thoát.")
            return
        if flow == "rover_read":
            success = await self._admin_read(update, "rover", {"username": answer})
            if success:
                self._conversation_states.pop(key, None)
            else:
                await update.message.reply_text("Bạn có thể nhập lại username rover, hoặc gõ /cancel để thoát.")
            return

        if flow == "coord":
            if step == "station":
                data["station"] = answer
                state["step"] = "lat"
                await update.message.reply_text("2/5 — Nhập vĩ độ (Lat), ví dụ 21.028511:")
                return
            if step in {"lat", "lon", "alt", "accuracy"}:
                try:
                    value = float(answer.replace(",", "."))
                except ValueError:
                    await update.message.reply_text("❌ Giá trị phải là số. Vui lòng nhập lại:")
                    return
                if step == "lat" and not -90 <= value <= 90:
                    await update.message.reply_text("❌ Vĩ độ (Lat) phải từ -90 đến 90.")
                    return
                if step == "lon" and not -180 <= value <= 180:
                    await update.message.reply_text("❌ Kinh độ (Lon) phải từ -180 đến 180.")
                    return
                data[step] = value
                next_steps = {
                    "lat": ("lon", "3/5 — Nhập kinh độ (Lon):"),
                    "lon": ("alt", "4/5 — Nhập độ cao ellipsoid (Alt), đơn vị mét:"),
                    "alt": ("accuracy", "5/5 — Nhập độ chính xác, mét (ví dụ 0.01):"),
                }
                if step in next_steps:
                    state["step"], prompt = next_steps[step]
                    await update.message.reply_text(prompt)
                    return
                self._conversation_states.pop(key, None)
                await self._queue_admin_action(
                    update, "coord_set", data,
                    f"Trạm: {data['station']}\n"
                    f"Vĩ độ (Lat): {data['lat']}\nKinh độ (Lon): {data['lon']}\n"
                    f"Độ cao ellipsoid (Alt): {data['alt']} m\nĐộ chính xác: {data['accuracy']} m",
                )
                return

        if flow in {"config_set", "rover_update"}:
            target_key = "station" if flow == "config_set" else "username"
            if step == target_key:
                try:
                    await self._load_editor_context(update, flow, answer, state)
                except Exception as exc:
                    self._conversation_states.pop(key, None)
                    await update.message.reply_text(f"❌ Không tải được dữ liệu hiện tại: {exc}")
                return
            if step == "field":
                await self._apply_editor_field_choice(update, flow, state, answer)
                return
            field = str(data.get("current_field") or "")
            current_value = data.get("changes", {}).get(field, data.get("current", {}).get(field))
            if step == "boolean_value":
                if answer in {"0", "quay lại", "quay lai", "back"}:
                    state["step"] = "field"
                    await update.message.reply_text(self._format_editor_menu(flow, data))
                    return
                if answer in {"1", "bật", "bat", "on", "true"}:
                    new_value = True
                elif answer in {"2", "tắt", "tat", "off", "false"}:
                    new_value = False
                else:
                    enabled_label, disabled_label = self._boolean_option_labels(field)
                    await update.message.reply_text(
                        f"Chọn 1 ({enabled_label}), 2 ({disabled_label}) hoặc 0 (quay lại)."
                    )
                    return
                data["changes"][field] = new_value
                state["step"] = "field"
                await update.message.reply_text(
                    f"✅ Đã chọn [{self._human_value(field, new_value)}]. Chưa áp dụng cho tới khi xác nhận.\n\n"
                    + self._format_editor_menu(flow, data)
                )
                return
            if step == "mountpoint_action":
                current_mountpoints = self._split_list_value(current_value)
                if answer in {"0", "quay lại", "quay lai", "back"}:
                    state["step"] = "field"
                    await update.message.reply_text(self._format_editor_menu(flow, data))
                    return
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
                    return
                if answer == "2":
                    if not current_mountpoints:
                        await update.message.reply_text("Danh sách hiện đang trống; không có gì để xóa.")
                        return
                    data["selection_options"] = current_mountpoints
                    state["step"] = "mountpoint_remove"
                    await update.message.reply_text(
                        "Chọn mountpoint cần XÓA bằng số (có thể chọn nhiều: 1 3):\n"
                        + "\n".join(f"[✓] {i}. {item}" for i, item in enumerate(current_mountpoints, 1))
                    )
                    return
                if answer == "3":
                    state["step"] = "mountpoint_replace"
                    await update.message.reply_text(
                        "Nhập toàn bộ danh sách mountpoint mới, cách nhau bằng dấu ;\n"
                        "Ví dụ: HCM01;HN01"
                    )
                    return
                if answer == "4":
                    data["changes"]["mountpoints"] = ""
                    state["step"] = "field"
                    await update.message.reply_text(
                        "✅ Đã đánh dấu xóa toàn bộ mountpoint. Chưa áp dụng cho tới khi xác nhận.\n\n"
                        + self._format_editor_menu(flow, data)
                    )
                    return
                await update.message.reply_text("Chọn từ 0 đến 4 theo danh sách.")
                return
            if step in {"mountpoint_add", "mountpoint_remove"}:
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
                    return
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
                return
            if step == "mountpoint_replace":
                new_list = self._split_list_value(answer)
                if not new_list:
                    await update.message.reply_text("Danh sách không hợp lệ. Nếu muốn xóa hết, quay lại và chọn mục 4.")
                    return
                data["changes"]["mountpoints"] = ";".join(new_list)
                state["step"] = "field"
                await update.message.reply_text(
                    f"✅ Danh sách mới: {', '.join(new_list)}\n\n" + self._format_editor_menu(flow, data)
                )
                return
            if step == "value":
                if answer.lower() in {"giữ", "giu", "back", "quay lại", "quay lai"}:
                    state["step"] = "field"
                    await update.message.reply_text(self._format_editor_menu(flow, data))
                    return
                data["changes"][field] = answer
                state["step"] = "field"
                await update.message.reply_text(
                    f"✅ Đã ghi nhận giá trị mới: {self._human_value(field, answer)}\n"
                    "Chưa áp dụng cho tới khi xác nhận.\n\n"
                    + self._format_editor_menu(flow, data)
                )
                return

        if flow == "rover_create":
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
                return
            if step == "expiry_days":
                try:
                    days = int(answer)
                    if not 1 <= days <= 3650:
                        raise ValueError
                except ValueError:
                    await update.message.reply_text("❌ Số ngày phải từ 1 đến 3650.")
                    return
                data["expiry_days"] = days
                state["step"] = "mountpoint"
                await update.message.reply_text("5/5 — Nhập mountpoint, hoặc gõ “mặc định” để lấy từ trạm:")
                return
            if step == "mountpoint":
                data["mountpoint"] = None if answer.lower() in {"mặc định", "mac dinh", "default", "bo qua", "bỏ qua"} else answer
                self._conversation_states.pop(key, None)
                await self._queue_admin_action(
                    update, "rover_create", data,
                    f"Tạo rover {data['username']} tại trạm {data['station']}\n"
                    f"Hạn: {data['expiry_days']} ngày\nMountpoint: {data['mountpoint'] or 'mặc định'}",
                )
                return

        if flow in {"rover_delete", "rover_unblock"}:
            self._conversation_states.pop(key, None)
            action = "rover_delete" if flow == "rover_delete" else "rover_unblock"
            await self._queue_admin_action(
                update, action, {"username": answer},
                f"{'XÓA' if flow == 'rover_delete' else 'MỞ CHẶN'} rover {answer}",
            )
            return

        if flow == "rover_renew":
            if step == "username":
                data["username"] = answer
                state["step"] = "days"
                await update.message.reply_text("2/2 — Nhập số ngày gia hạn (1–3650):")
                return
            try:
                days = int(answer)
                if not 1 <= days <= 3650:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Số ngày phải từ 1 đến 3650.")
                return
            data["expiry_days"] = days
            self._conversation_states.pop(key, None)
            await self._queue_admin_action(
                update, "rover_renew", data,
                f"Gia hạn rover {data['username']} thêm {days} ngày",
            )
            return

        if flow == "rover_block":
            if step == "username":
                data["username"] = answer
                state["step"] = "reason"
                await update.message.reply_text("2/2 — Nhập lý do chặn:")
                return
            data["reason"] = answer
            self._conversation_states.pop(key, None)
            await self._queue_admin_action(
                update, "rover_block", data,
                f"CHẶN rover {data['username']}\nLý do: {answer}",
            )
            return

        if flow in {"station_power", "station_delete"}:
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
            return

        self._conversation_states.pop(key, None)
        await update.message.reply_text("❌ Phiên thao tác không hợp lệ. Hãy bắt đầu lại từ /menu.")

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
            logger.error("Zalo admin mutation failed action=%s: %s", pending.action, exc, exc_info=True)
            await zalo_repository.finish_pending_action(pending.id, False)
            try:
                if isinstance(exc, ValueError):
                    await update.message.reply_text(f"❌ {exc}")
                else:
                    await update.message.reply_text("❌ Hệ thống gặp sự cố khi xử lý yêu cầu. Vui lòng thử lại sau.")
            except Exception as send_exc:
                logger.error("Could not deliver Zalo mutation failure: %s", send_exc)
            return
        await zalo_repository.finish_pending_action(pending.id, True)
        try:
            await self._reply_text_chunks(update, str(result.get("message") or "✅ Hoàn tất."))
        except Exception as exc:
            logger.error(
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
        elif principal.is_linked:
            text = (
                "👋 Bot CORS Aitogy đã sẵn sàng trong cuộc trò chuyện cá nhân này.\n"
                f"Đang liên kết với tài khoản {principal.username or '-'} ({principal.role or '-'}). "
                "Gõ /menu để xem đúng menu theo quyền của bạn."
            )
        else:
            text = (
                "👋 Chào bạn! Bot giám sát hệ thống CORS Aitogy đã sẵn sàng.\n"
                "Chat cá nhân dùng /link <mã tạo trong dashboard>. Group dùng /register <mã group>."
            )
        await update.message.reply_text(text)

    async def _cors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        if chat_id in self._revoked_chat_ids:
            return
        allowed = await zalo_repository.remember_cors_chat(
            chat_id, self._get_chat_type(update), self._get_display_name(update),
        )
        if not allowed:
            return
        from .reference_network import get_network_status
        health = await get_network_status()
        token = self._cors_outbound.set("reply")
        try:
            await update.message.reply_text(self._cors_status_text(health))
        finally:
            self._cors_outbound.reset(token)

    @staticmethod
    def _cors_status_text(health: dict) -> str:
        headline = {
            "online": "🟢 CORS Cục đang online. Trang dịch vụ có form đăng nhập bình thường.",
            "offline": "🔴 CORS Cục đang lỗi. Trang dịch vụ không có form đăng nhập hoặc đang báo lỗi.",
        }.get(health.get("status"), "⚪ Chưa kiểm tra được CORS Cục; chưa thể kết luận online hay sập.")
        checked_at = datetime.fromtimestamp(health["checked_at"], ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S %d/%m/%Y")
        return (
            f"{headline}\nKiểm tra: {checked_at}\n"
            "Đây là trạng thái hệ thống web Cục, chưa xác nhận tín hiệu RTCM từng trạm."
        )

    async def _notify_cors_changes_once(self):
        from .reference_network import get_network_status
        targets = await zalo_repository.cors_subscriptions()
        if not targets:
            return
        health = await get_network_status()
        status = health.get("status")
        if status not in {"online", "offline"}:
            return
        for target in targets:
            try:
                async with zalo_repository.cors_transition(target["id"], status) as delivery:
                    if delivery["send"]:
                        token = self._cors_outbound.set({"kind": "alert", "policy": delivery["policy"]})
                        try:
                            sent = await self.app.bot.send_message(chat_id=target["chat_id"], text=self._cors_status_text(health))
                            delivery["sent"] = sent is not None
                        finally:
                            self._cors_outbound.reset(token)
            except Exception as exc:
                logger.warning("CORS Cục alert failed for chat_id=%s: %s", target["chat_id"], exc)

    async def _cors_status_loop(self):
        while True:
            try:
                await self._notify_cors_changes_once()
            except Exception as exc:
                logger.warning("CORS Cục monitor failed: %s", exc)
            await asyncio.sleep(30)

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
                "• /menu hoặc /help — Xem hướng dẫn này.\n\n"
                "Trong nhóm, nhắc @bot kèm câu hỏi nếu Zalo yêu cầu.\n"
                "Kết quả kiểm tra trang dịch vụ Cục; chưa xác nhận tín hiệu RTCM từng trạm."
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

    async def _menu_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        principal = await self._resolve_principal(update)
        if not principal.is_linked:
            await self._public_menu_cmd(update, context)
            return

        lines = [
            "🎛️ MENU BOT CORS",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"Quyền hiện tại: {principal.username or principal.binding_type} ({principal.role or 'public'})",
            (
                f"Phạm vi: {len(principal.allowed_bases)} trạm được admin chọn"
                if principal.station_scope_mode == "selected"
                else "Phạm vi: không có trạm" if principal.station_scope_mode == "none"
                else "Phạm vi: theo đúng tài khoản web" if principal.binding_type == "private"
                else "Phạm vi: tất cả trạm"
            ),
            "",
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
        write_lines = []
        if principal.allows(auth.Permission.EDIT_COORDINATES):
            write_lines.append("/coord <tên> — Sửa tọa độ")
        if principal.allows(auth.Permission.EDIT_SERVICE_CONFIG):
            write_lines.append("/configset <tên> — Sửa cấu hình bằng menu từng mục")
        if principal.allows(auth.Permission.MANAGE_CASTER):
            write_lines.extend((
                "/rovercreate — Tạo rover",
                "/roverupdate <user> — Sửa rover: thêm/xóa/thay mountpoint, bật/tắt rõ nghĩa",
                "/roverrenew <user> — Gia hạn rover",
                "/roverblock <user> — Chặn rover",
                "/roverunblock <user> — Mở chặn rover",
                "/roverdelete <user> — Xóa rover",
            ))
        if principal.allows(auth.Permission.MANAGE_USERS):
            write_lines.extend((
                "/stationlock <tên> — Khóa trạm",
                "/stationunlock <tên> — Mở khóa trạm",
            ))
        if principal.allows(auth.Permission.DELETE_DEVICE):
            write_lines.extend((
                "/stationreset <tên> — Khởi động lại trạm",
                "/stationdelete <tên> — Xóa trạm",
            ))
        if write_lines:
            lines.extend(("", "🛠️ THAO TÁC CẦN XÁC NHẬN", *write_lines))
        lines.extend(("", "🔔 HỆ THỐNG"))
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
        text = "\n".join(lines)
        await update.message.reply_text(text)

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
            binding, user = await zalo_repository.consume_group_link_code(
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
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                self._registration_success_text(role, chat_type)
                + (
                    "\nPhạm vi: tất cả trạm."
                    if binding.station_scope_mode == "all"
                    else "\nPhạm vi: không có trạm."
                    if binding.station_scope_mode == "none"
                    else f"\nPhạm vi: {len(binding.allowed_bases or [])} trạm được admin chọn."
                )
            ),
        )
        await self._start_notification_onboarding(update, int(binding.id), str(binding.binding_type))

    async def _status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await self._admin_read(update, "overview", {})
            return
        await self._admin_read(update, "station", {"station": " ".join(context.args).strip()})

    async def _errors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._admin_read(update, "alerts", {})

    @staticmethod
    def _format_system_alert(
        newly_offline: List[dict], newly_online: List[dict],
        new_alerts: List[dict], visible_devices: List[dict],
    ) -> str:
        now_str = datetime.now().strftime("%H:%M %d/%m/%Y")
        lines = [f"🚨 CẢNH BÁO HỆ THỐNG — {now_str}", "━━━━━━━━━━━━━━━━━━━━━━"]
        if newly_offline:
            lines.extend(("", f"❌ MỚI OFFLINE ({len(newly_offline)}):"))
            lines.extend(f"• {item.get('name') or 'Trạm chưa đặt tên'}" for item in newly_offline)
        if new_alerts:
            lines.extend(("", f"⚠️ LỖI MỚI ({len(new_alerts)}):"))
            for item in new_alerts:
                name = item.get("rover_username") or item.get("device_name") or item.get("device_serial") or "Hệ thống"
                lines.append(f"• {name}: {item.get('title') or item.get('message') or 'Cảnh báo'}")
        if newly_online:
            lines.extend(("", f"✅ PHỤC HỒI ({len(newly_online)}):"))
            lines.extend(f"• {item.get('name') or 'Trạm chưa đặt tên'} đã online trở lại" for item in newly_online)
        online = sum(
            1 for item in visible_devices
            if str(item.get("status") or "unknown").lower() not in {"offline", "unknown"}
        )
        offline = sum(
            1 for item in visible_devices
            if str(item.get("status") or "unknown").lower() == "offline"
        )
        lines.extend(("", "━━━━━━━━━━━━━━━━━━━━━━", f"📊 {online} online | {offline} offline"))
        return "\n".join(lines)

    async def _error_check_loop(self):
        while True:
            try:
                if not self._get_devices_cb or not self._get_alerts_cb:
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

                if self._status_snapshot_ready:
                    for serial, status in current_statuses.items():
                        prev_status = self.prev_statuses.get(serial)
                        if prev_status and prev_status != status:
                            if status == "offline":
                                newly_offline.append(next((d for d in devices if d.get("serial") == serial), {}))
                            elif prev_status == "offline" and status != "offline":
                                newly_online.append(next((d for d in devices if d.get("serial") == serial), {}))

                if self._alert_snapshot_ready:
                    new_alert_ids = current_alerts_ids - self.prev_alerts
                    for aid in new_alert_ids:
                        new_alerts.append(current_alerts_map[aid])

                self.prev_statuses = current_statuses
                self.prev_alerts = current_alerts_ids
                self._status_snapshot_ready = True
                self._alert_snapshot_ready = True
                
                if newly_offline or newly_online or new_alerts:
                    alert_severities = {
                        str(alert.get("severity") or "warning").lower() for alert in new_alerts
                    }
                    critical_alert = bool(alert_severities.intersection({
                        "critical", "fatal", "error", "high", "severe",
                    }))
                    severity = (
                        "critical" if newly_offline or critical_alert
                        else "warning" if new_alerts else "info"
                    )
                    subjects = set()
                    for device in newly_offline + newly_online:
                        subjects.update(filter(None, (device.get("name"), device.get("serial"))))
                    for alert in new_alerts:
                        subjects.update(filter(None, (
                            alert.get("rover_username"), alert.get("device_name"), alert.get("device_serial")
                        )))
                    event_key = payload_fingerprint({
                        "window": int(time.time() // 120),
                        "offline": sorted(str(item.get("serial")) for item in newly_offline),
                        "online": sorted(str(item.get("serial")) for item in newly_online),
                        "alerts": sorted(str(item.get("id")) for item in new_alerts),
                    })
                    binding_scopes = (
                        await self._get_binding_scopes_cb() if self._get_binding_scopes_cb else None
                    )
                    targets = await zalo_repository.reserve_notification_targets(
                        event_key, severity, subjects, binding_scopes=binding_scopes,
                    )
                    for target in targets:
                        chat_id = target["chat_id"]
                        scope_values = {
                            str(value).strip().casefold() for value in target.get("scope_values") or []
                        }
                        if target.get("scope_mode") == "selected":
                            target_devices = [
                                item for item in devices
                                if str(item.get("serial") or "").casefold() in scope_values
                                or str(item.get("name") or "").casefold() in scope_values
                            ]
                            target_offline = [item for item in newly_offline if item in target_devices]
                            target_online = [item for item in newly_online if item in target_devices]
                            target_alerts = [
                                item for item in new_alerts
                                if any(
                                    str(item.get(key) or "").casefold() in scope_values
                                    for key in ("rover_username", "device_name", "device_serial")
                                )
                            ]
                        else:
                            target_devices = devices
                            target_offline = newly_offline
                            target_online = newly_online
                            target_alerts = new_alerts
                        if not (target_offline or target_online or target_alerts):
                            continue
                        text = self._format_system_alert(
                            target_offline, target_online, target_alerts, target_devices
                        )
                        try:
                            await self.app.bot.send_message(chat_id=chat_id, text=text)
                        except Exception as e:
                            logger.error(f"Failed to send alert to {chat_id}: {e}")
                            try:
                                await zalo_repository.finish_delivery(target["delivery_id"], False, str(e))
                            except Exception as log_exc:
                                logger.error("Failed to record Zalo delivery failure id=%s: %s", target["delivery_id"], log_exc)
                        else:
                            try:
                                await zalo_repository.finish_delivery(target["delivery_id"], True)
                            except Exception as log_exc:
                                logger.error("Zalo alert sent but delivery log update failed id=%s: %s", target["delivery_id"], log_exc)

            except Exception as e:
                logger.error(f"Error in Zalo error_check_loop: {e}")
                
            await asyncio.sleep(120)

zalo_bot_service = ZaloBotService()
