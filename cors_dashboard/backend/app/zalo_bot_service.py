"""Zalo SDK lifecycle and webhook entry point; feature handlers live in app.zalo_bot."""
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
from .zalo_bot_identity import BOT_IDENTITIES, chat_bot_key, raw_chat_id, scoped_chat_id

try:
    from zalo_bot import Update
    from zalo_bot.ext import ApplicationBuilder, CommandHandler, ContextTypes
    ZALO_AVAILABLE = True
except ImportError:
    ZALO_AVAILABLE = False

logger = logging.getLogger(__name__)
ZALO_TRACE_VERSION = "20261021-two-bots-v24"
REDACTED_VALUE = "<redacted>"

# Preserve existing imports of command constants and intent helpers.
from .zalo_bot.common import (
    BOT_COMMANDS, _COMMAND_DISPATCH, _normalize_to_ascii, _is_cors_status_question,
    _NLP_INTENT_PATTERNS, _SyntheticContext, SERVICE_CONFIG_CHOICES,
    ROVER_UPDATE_CHOICES, SERVICE_BOOLEAN_FIELDS, ROVER_BOOLEAN_FIELDS,
    ACTION_PERMISSIONS, DANGEROUS_ACTIONS,
)
from .zalo_bot.administration import AdministrationMixin
from .zalo_bot.editor import EditorMixin
from .zalo_bot.conversation import ConversationMixin
from .zalo_bot.commands import CommandsMixin
from .zalo_bot.cors import CorsMixin
from .zalo_bot.alerts import AlertsMixin


def _chat_display_name(chat):
    for attribute in ("title", "name", "full_name", "display_name"):
        value = str(getattr(chat, attribute, "") or "").strip() if chat else ""
        if value:
            return value
    return None


def _sender_display_name(sender):
    if not sender:
        return None
    first_last = " ".join(
        part for part in (
            str(getattr(sender, "first_name", "") or "").strip(),
            str(getattr(sender, "last_name", "") or "").strip(),
        ) if part
    )
    for candidate in (
        getattr(sender, "full_name", None),
        first_last,
        getattr(sender, "username", None),
        getattr(sender, "name", None),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


class ZaloBotService(
    AdministrationMixin, EditorMixin, ConversationMixin, CommandsMixin, CorsMixin, AlertsMixin
):
    def __init__(self, bot_key: str = "tun"):
        if bot_key not in BOT_IDENTITIES:
            raise ValueError("Unknown Zalo bot")
        self.bot_key = bot_key
        self.app = None
        self.dispatcher = None
        self.chats = {}
        self.CHATS_FILE = "zalo_chats.json" if bot_key == "tun" else "zalo_tb_chats.json"

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
        self._cors_regions: Dict[str, tuple[str, float, str]] = {}
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

    def _bot_setting(self, suffix: str) -> str:
        prefix = "ZALO_" if self.bot_key == "tun" else "ZALO_TB_"
        return str(getattr(settings, prefix + suffix, "") or "").strip()

    def owns_chat(self, chat_id: str) -> bool:
        return chat_bot_key(chat_id) == self.bot_key

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
            except Exception:
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
        for key in list(self._cors_regions):
            if key.startswith(f"{chat_id}:"):
                self._cors_regions.pop(key, None)
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
        return await zalo_repository.resolve_principal(chat_id, sender_id, chat_type)

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
        text = re.sub(r"(?i)(/register)(\s+)\S+", rf"\1\2{REDACTED_VALUE}", text)
        text = re.sub(r"(?i)(/link)(\s+)\S+", rf"\1\2{REDACTED_VALUE}", text)
        text = re.sub(
            r"(?i)(/rovercreate\s+\S+\s+\S+\s+)\S+",
            rf"\1{REDACTED_VALUE}",
            text,
        )
        value_pattern = re.compile(r"""(?:\s*=\s*|\s+)(?=\S)(?P<value>"[^"]*"|'[^']*'|\S+)""")
        segments = []
        cursor = 0
        for key in re.finditer(r"[A-Za-z0-9_]+", text):
            if key.start() < cursor:
                continue
            name = key[0].lower()
            if not (any(part in name for part in ("password", "secret", "token"))
                    or name.endswith(("passwd", "pass", "mk"))):
                continue
            value = value_pattern.match(text, key.end())
            if value:
                segments.append(text[cursor:value.start("value")])
                segments.append(REDACTED_VALUE)
                cursor = value.end()
        segments.append(text[cursor:])
        return "".join(segments)

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
                    safe[key_text] = REDACTED_VALUE
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
            found = ZaloBotService._first_nested_mapping_value(data, wanted_keys)
            if found not in (None, "", [], {}):
                return found
            values = data.values()
        elif isinstance(data, list):
            values = data
        else:
            return None
        for value in values:
            found = ZaloBotService._first_nested_value(value, wanted_keys)
            if found not in (None, "", [], {}):
                return found
        return None

    @staticmethod
    def _first_nested_mapping_value(data: dict, wanted_keys: Set[str]) -> Any:
        for key, value in data.items():
            if str(key).lower() in wanted_keys and value not in (None, "", [], {}):
                return value
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
            "bot_key": self.bot_key,
            "bot_name": BOT_IDENTITIES[self.bot_key]["name"],
            "mode": self._bot_mode(),
            "sdk_available": ZALO_AVAILABLE,
            "bot_enabled": bool(settings.ENABLE_ZALO_BOT),
            "app_initialized": self.app is not None,
            "events": list(reversed(events)),
        }

    def clear_debug_events(self) -> None:
        self._debug_events.clear()

    async def _outbound_chat_policy(self, chat_id, cors_context):
        if isinstance(cors_context, dict):
            return cors_context["policy"], cors_context.get("kind")
        return await zalo_repository.chat_delivery_policy(str(chat_id)), cors_context

    @staticmethod
    def _outbound_policy_suppresses(policy, cors_kind):
        return (
            not policy["active"]
            or (policy["mode"] == "cors_only" and not cors_kind)
            or (cors_kind == "alert" and not (
                policy["mode"] == "cors_only" and policy["cors_notify_changes"]
            ))
        )

    def _track_outbound_message(self, trace_id, chat_id, text):
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
        return outbound

    async def _send_traced_message(self, original_send_message, args, kwargs, chat_id, trace_id, outbound):
        attempts = max(1, min(int(getattr(settings, "ZALO_SEND_RETRY_ATTEMPTS", 3) or 3), 5))
        base_delay = max(0.1, min(float(getattr(settings, "ZALO_SEND_RETRY_BASE_DELAY_SECONDS", 0.4) or 0.4), 5.0))
        if "chat_id" in kwargs:
            kwargs["chat_id"] = raw_chat_id(chat_id)
        elif args:
            args = (raw_chat_id(chat_id), *args[1:])
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
            logger.exception(
                "[ZALO TRACE %s] outbound failed chat_id=%s: %s",
                trace_id or "background", chat_id, exc, exc_info=True,
            )
            raise

    # SDK replies use raw IDs; background sends use the bot-scoped stored key.
    def _normalize_outbound_chat_id(self, args, kwargs) -> str:
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        if self.bot_key == "tun" and not self.owns_chat(str(chat_id)):
            raise ValueError("Chat belongs to another Zalo bot")
        return scoped_chat_id(self.bot_key, str(chat_id))

    async def _handle_traced_send_message(self, original_send_message, args, kwargs):
        trace_id = self._active_trace_id.get()
        chat_id = self._normalize_outbound_chat_id(args, kwargs)
        text = kwargs.get("text", args[1] if len(args) > 1 else None)
        cors_context = self._cors_outbound.get()
        # Change delivery already owns the preference row lock. Reuse that
        # policy instead of opening another auth session while holding it.
        policy, cors_kind = await self._outbound_chat_policy(chat_id, cors_context)
        if self._outbound_policy_suppresses(policy, cors_kind):
            logger.info("Zalo outbound suppressed by chat policy for chat_id=%s", chat_id)
            return None
        outbound = self._track_outbound_message(trace_id, chat_id, text)
        if str(chat_id) in self._revoked_chat_ids and not self._allow_revoked_outbound.get():
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
        return await self._send_traced_message(
            original_send_message, args, kwargs, chat_id, trace_id, outbound
        )

    def _install_send_trace(self) -> None:
        bot = getattr(self.app, "bot", None) if self.app else None
        if not bot or getattr(bot, "_cors_send_trace_installed", False):
            return

        original_send_message = getattr(bot, "send_message", None)
        if not callable(original_send_message):
            logger.warning("Zalo outbound tracing unavailable: bot.send_message is missing")
            return

        async def traced_send_message(*args, **kwargs):
            return await self._handle_traced_send_message(original_send_message, args, kwargs)

        try:
            setattr(bot, "send_message", traced_send_message)
            setattr(bot, "_cors_send_trace_installed", True)
        except Exception as exc:
            logger.warning("Could not install Zalo outbound tracing: %s", exc)

    def _bot_mode(self) -> str:
        mode = (self._bot_setting("BOT_MODE") or "polling").lower()
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
        self.app = ApplicationBuilder().token(self._bot_setting("BOT_TOKEN")).build()
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
        webhook_url = self._bot_setting("WEBHOOK_URL")
        secret_token = self._bot_setting("WEBHOOK_SECRET")
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
            logger.exception("Failed to register Zalo webhook: %s", e, exc_info=True)

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
                    logger.exception(f"Zalo Bot polling loop crashed: {e}. Restarting in 5s...", exc_info=True)
                    await asyncio.sleep(5)

        self._polling_task = asyncio.create_task(safe_polling_loop())

    async def _start_webhook_runtime(self) -> None:
        await self.app.bot.initialize()
        self._ensure_dispatcher()
        self._webhook_setup_task = asyncio.create_task(self._delayed_set_webhook())
        logger.info("Zalo webhook runtime is ready.")

    async def start(self, *, strict: bool = False) -> bool:
        try:
            if self._polling_task is not None or self._webhook_setup_task is not None:
                return True
            if not ZALO_AVAILABLE or not settings.ENABLE_ZALO_BOT or not self._bot_setting("BOT_TOKEN"):
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
            logger.exception(f"FATAL ZALO BOT ERROR: {e}", exc_info=True)
            await self.stop()
            if strict:
                raise RuntimeError(f"Zalo Bot failed to start: {e}") from e
            return False

    def _webhook_test_response(self, payload, trace_id):
        root = payload.get("result", payload)
        if not isinstance(root, dict) or root.get("event_name") != "webhook.test":
            return None
        self._debug_events.append({
            "trace_id": trace_id,
            "received_at": datetime.now().astimezone().isoformat(),
            "status": "webhook_test", "summary": {"event": "webhook.test"},
            "outbound": [], "outbound_count": 0,
        })
        logger.info("[ZALO TRACE %s v=%s] webhook test acknowledged", trace_id, ZALO_TRACE_VERSION)
        return {"trace_id": trace_id, "status": "webhook_test", "outbound_count": 0}

    async def _record_webhook_event(self, payload, trace_id, summary):
        try:
            stored_chat_id = scoped_chat_id(self.bot_key, str(summary["chat_id"])) if summary.get("chat_id") else None
            await self._hydrate_conversation_state(stored_chat_id, summary.get("sender_id"))
            sensitive_input = self._is_sensitive_conversation_input(stored_chat_id, summary.get("sender_id"))
        except Exception as exc:
            # If persistent state cannot be checked, fail closed for logging: an
            # ordinary message is less valuable than accidentally logging a secret.
            sensitive_input = True
            logger.warning("[ZALO TRACE %s] workflow hydration failed; inbound text redacted: %s", trace_id, exc)
        debug_payload = self._safe_debug_value(payload)
        if sensitive_input:
            summary["text"] = REDACTED_VALUE
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
        return event, sensitive_input

    def _is_duplicate_webhook(self, payload, summary, event, trace_id):
        msg_id = str(
            summary.get("msg_id") or summary.get("message_id")
            or payload.get("msg_id") or payload.get("message_id") or ""
        ).strip()
        if not msg_id:
            dedup_src = f"{summary.get('chat_id')}:{summary.get('sender_id')}:{summary.get('text', '')}"
            msg_id = hashlib.sha256(dedup_src.encode("utf-8", errors="replace")).hexdigest()
        now = time.time()
        self._processed_msg_ids = {key: stamp for key, stamp in self._processed_msg_ids.items() if now - stamp < 60}
        if msg_id in self._processed_msg_ids:
            event["status"] = "deduplicated"
            logger.info("[ZALO TRACE %s] duplicate msg_id=%s, skipping", trace_id, msg_id)
            return True
        self._processed_msg_ids[msg_id] = now
        return False

    def _ensure_webhook_app(self):
        if not ZALO_AVAILABLE:
            raise RuntimeError("Zalo SDK is not installed")
        if not settings.ENABLE_ZALO_BOT:
            raise RuntimeError("Zalo Bot is disabled")
        if self.app:
            return
        self._load_chats()
        self._build_app()
        if self._bot_mode() == "webhook":
            self._ensure_dispatcher()

    def _prepare_webhook_update(self, payload, event, trace_id, sensitive_input):
        update_data = payload.get("result") if isinstance(payload, dict) else None
        if update_data is None and isinstance(payload, dict):
            update_data = payload
        if not isinstance(update_data, dict):
            raise ValueError("Invalid Zalo webhook payload: missing result object")
        update_data, normalization = self._normalize_group_command(update_data)
        if normalization:
            event["group_command_normalization"] = normalization
            chat_id = scoped_chat_id(self.bot_key, str(event["summary"].get("chat_id") or ""))
            if chat_id and normalization.get("mention_prefix"):
                self._chat_mention_prefixes[chat_id] = normalization["mention_prefix"]
            logger.info(
                "[ZALO TRACE %s] normalized group command %r -> %r",
                trace_id, normalization["original_text"], normalization["normalized_text"],
            )
        update = Update.de_json(update_data, self.app.bot)
        if update is None:
            raise ValueError("Zalo SDK could not parse webhook payload")
        event["parsed_update"] = REDACTED_VALUE if sensitive_input else self._safe_debug_value(
            update.to_dict() if hasattr(update, "to_dict") else update
        )
        message = getattr(update, "message", None)
        if not getattr(getattr(message, "chat", None), "id", None):
            return update, None, True
        parsed_text = getattr(message, "text", None)
        safe_text = (
            REDACTED_VALUE if sensitive_input
            else self._redact_command_secret(parsed_text) if isinstance(parsed_text, str)
            else parsed_text
        )
        event["dispatch_text"] = safe_text
        logger.info("[ZALO TRACE %s] SDK dispatch text=%r", trace_id, safe_text)
        event["status"] = "parsed"
        return update, parsed_text, False

    async def _webhook_public_routes(self, update, text, command):
        public_cors = command == "/cors" or _is_cors_status_question(text) or self._is_cors_followup(update, text)
        public_menu = False
        principal = None
        if self._get_chat_type(update) == "group":
            principal = await zalo_repository.resolve_principal(
                self._get_chat_id(update), self._get_sender_id(update), "group"
            )
            # New groups can call the bot by mention (for example '@Bot helo')
            # or ask for help without acquiring any Base permissions.
            public_menu = not principal.is_linked and command in {"/start", "/help", "/menu"}
            if not principal.is_linked and text.startswith("@") and "/" not in text:
                public_cors = True
        return public_cors, public_menu, principal

    async def _reject_unlinked_webhook_group(self, update, event, trace_id, command, text, routes):
        public_cors, public_menu, principal = routes
        if principal is None or principal.is_linked or public_cors or public_menu or command == "/register":
            return None
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
        return {"trace_id": trace_id, "status": event["status"], "outbound_count": event["outbound_count"]}

    async def _dispatch_webhook_update(self, update, parsed_text, event, trace_id):
        if await zalo_repository.is_chat_paused(self._get_chat_id(update)):
            event["status"] = "paused_chat"
            return {"trace_id": trace_id, "status": "paused_chat", "outbound_count": 0}
        text = str(parsed_text or "").strip().lstrip("\u200b\u200c\u200d\ufeff").strip()
        command = text.split(maxsplit=1)[0].lower() if text else ""
        command = command.split("@", 1)[0].rstrip("\u200b\u200c\u200d\ufeff")
        routes = await self._webhook_public_routes(update, text, command)
        rejected = await self._reject_unlinked_webhook_group(update, event, trace_id, command, text, routes)
        if rejected:
            return rejected

        public_cors, public_menu, principal = routes
        revoked_token = None
        if principal is not None and not principal.is_linked and command == "/register":
            # Allow registration guidance/errors to be delivered. A successful OTP consumption establishes the new DB grant.
            revoked_token = self._allow_revoked_outbound.set(True)
        try:
            dispatch_method = await self._dispatch_webhook_route(update, public_menu, public_cors)
        finally:
            if revoked_token is not None:
                self._allow_revoked_outbound.reset(revoked_token)
        event["status"] = "dispatched"
        event["dispatch_method"] = dispatch_method
        event["outbound_count"] = len(event["outbound"])
        logger.info(
            "[ZALO TRACE %s] dispatched via=%s outbound_count=%s",
            trace_id, dispatch_method, event["outbound_count"],
        )
        return {"trace_id": trace_id, "status": event["status"], "outbound_count": event["outbound_count"]}

    async def _dispatch_webhook_route(self, update, public_menu, public_cors):
        if public_menu:
            await self._public_menu_cmd(update, None)
            return "public_menu"
        if public_cors:
            await self._cors_cmd(update, None)
            return "public_cors"
        if hasattr(self.app, "process_update"):
            await self._maybe_await(self.app.process_update(update))
            return "application.process_update"
        dispatcher = self._ensure_dispatcher()
        if dispatcher and hasattr(dispatcher, "process_update"):
            await self._maybe_await(dispatcher.process_update(update))
            return "dispatcher.process_update"
        if hasattr(self.app, "update_queue"):
            await self.app.update_queue.put(update)
            return "application.update_queue"
        raise RuntimeError("Zalo SDK does not expose a webhook update dispatcher")

    async def process_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        trace_id = uuid.uuid4().hex[:12]
        summary = self._summarize_payload(payload)
        test_response = self._webhook_test_response(payload, trace_id)
        if test_response is not None:
            return test_response
        event, sensitive_input = await self._record_webhook_event(payload, trace_id, summary)
        if self._is_duplicate_webhook(payload, summary, event, trace_id):
            return {"trace_id": trace_id, "status": "deduplicated", "outbound_count": 0}
        logger.info(
            "[ZALO TRACE %s v=%s] inbound %s",
            trace_id, ZALO_TRACE_VERSION,
            json.dumps(event["summary"], ensure_ascii=False, default=str),
        )
        context_token = self._active_trace_id.set(trace_id)
        try:
            self._ensure_webhook_app()
            update, parsed_text, missing_chat = self._prepare_webhook_update(
                payload, event, trace_id, sensitive_input
            )
            if missing_chat:
                event["status"] = "ignored_no_chat"
                event["outbound_count"] = 0
                logger.info("[ZALO TRACE %s] ignored update without a replyable chat", trace_id)
                return {"trace_id": trace_id, "status": "ignored_no_chat", "outbound_count": 0}
            return await self._dispatch_webhook_update(update, parsed_text, event, trace_id)
        except Exception as exc:
            event["status"] = "error"
            safe_error = self._redact_command_secret(f"{type(exc).__name__}: {exc}")
            event["error"] = safe_error
            logger.error("[ZALO TRACE %s] processing failed: %s", trace_id, safe_error)
            raise
        finally:
            self._active_trace_id.reset(context_token)

    async def _cancel_owned_tasks(self, owned_tasks):
        for task in owned_tasks:
            task.cancel()
        if owned_tasks:
            await asyncio.gather(*owned_tasks, return_exceptions=True)

    async def _shutdown_bot_application(self):
        if not self.app:
            return
        if hasattr(self.app, 'stop'):
            if asyncio.iscoroutinefunction(self.app.stop):
                await self.app.stop()
            else:
                self.app.stop()
        if hasattr(self.app, 'shutdown'):
            await self._maybe_await(self.app.shutdown())
        elif self._bot_mode() == "webhook" and getattr(self.app, "bot", None):
            await self.app.bot.shutdown()

    def _clear_bot_application_state(self):
        self._task = None
        self._polling_task = None
        self._webhook_setup_task = None
        self._cors_status_task = None
        self.dispatcher = None
        self.app = None

    async def stop(self):
        owned_tasks = [
            task for task in (self._task, self._polling_task, self._webhook_setup_task, self._cors_status_task)
            if task
        ]
        if self.app or owned_tasks:
            logger.info("Stopping Zalo Bot Service...")
            await self._cancel_owned_tasks(owned_tasks)
        try:
            await self._shutdown_bot_application()
        except Exception as e:
            logger.exception(f"Error stopping Zalo Bot: {e}")
        finally:
            self._clear_bot_application_state()

    def _get_chat_id(self, update: Update) -> str:
        return scoped_chat_id(self.bot_key, self._get_raw_chat_id(update))

    @staticmethod
    def _get_raw_chat_id(update: Update) -> str:
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
            name = _chat_display_name(chat)
            if name:
                return name
        sender = getattr(message, "from_user", None) if message else None
        return _sender_display_name(sender) or _chat_display_name(chat)

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
        chat_id = self._get_raw_chat_id(update)
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


zalo_bot_service = ZaloBotService()
zalo_bot_services = {"tun": zalo_bot_service, "tb": ZaloBotService("tb")}


async def start_zalo_bots(*, strict: bool = False) -> None:
    configured = [service for service in zalo_bot_services.values() if service._bot_setting("BOT_TOKEN")]
    tokens = [service._bot_setting("BOT_TOKEN") for service in configured]
    if len(tokens) != len(set(tokens)):
        raise RuntimeError("Hai bot Zalo phải dùng token khác nhau.")
    for service in configured:
        await service.start(strict=strict)


async def stop_zalo_bots() -> None:
    await asyncio.gather(*(service.stop() for service in zalo_bot_services.values()))
