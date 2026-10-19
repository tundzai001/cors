"""Cors behavior for the Zalo service."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from ..zalo_assistant import zalo_repository
from .common import _COMMAND_DISPATCH

logger = logging.getLogger("app.zalo_bot_service")


class CorsMixin:
    def _is_cors_followup(self, update, text):
        from ..zalo_cors_report import _plain
        from ..zalo_cors_dialogue import cors_intent
        words = _plain(text)
        first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ''
        if first.startswith('/') or first in _COMMAND_DISPATCH or re.search(r'\b(base|rover)\b', words):
            return False
        previous = self._cors_regions.get(self._conversation_key(update))
        return bool(previous and time.monotonic() - previous[1] < 1200
                    and cors_intent(words, previous[2] if len(previous) > 2 else 'status') != 'status')

    async def _cors_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = self._get_chat_id(update)
        if chat_id in self._revoked_chat_ids:
            return
        allowed = await zalo_repository.remember_cors_chat(
            chat_id, self._get_chat_type(update), self._get_display_name(update),
        )
        if not allowed:
            return
        policy = await zalo_repository.chat_delivery_policy(chat_id)
        if not policy.get('active', False):
            return
        from ..reference_network import get_network_status
        from ..zalo_cors_report import report_text, region_for_question, region_options, _plain
        from ..zalo_cors_dialogue import cors_intent, STATIC_INTENTS
        question = str(getattr(update.message, "text", "") or "")
        key = self._conversation_key(update)
        now = time.monotonic()
        self._cors_regions = {k: v for k, v in self._cors_regions.items() if now - v[1] < 1200}
        previous = self._cors_regions.get(key)
        previous_intent = previous[2] if previous and len(previous) > 2 else 'status'
        words = _plain(question)
        intent = cors_intent(words, previous_intent)
        options = region_options(question)
        name = region_for_question(question)[0]
        new_unknown_area = not options and re.search(r"\b(?:o|tai|tinh|khu vuc)\s+(?!do\b|day\b|dau\b)\w+", words)
        if len(options) > 1 or new_unknown_area:
            self._cors_regions.pop(key, None)
        elif name != "Việt Nam":
            self._cors_regions.pop(key, None)
            if len(self._cors_regions) >= 512:
                self._cors_regions.pop(next(iter(self._cors_regions)))
            self._cors_regions[key] = (name, now, intent)
        elif re.search(r"\b(viet nam|ca nuoc|toan quoc)\b", words) or words == "cors" or re.search(r"(?:^|\s)/cors(?:@\S+)?\s*$", question, re.IGNORECASE):
            self._cors_regions.pop(key, None)
        elif key in self._cors_regions and (intent != 'status' or re.search(r"\b(do|o do|cho do|tai do|float|fix|dien ly)\b", words)):
            name = self._cors_regions[key][0]
            question += " " + name
            self._cors_regions[key] = (name, now, intent)
        # Definitions, small talk and clarification need no external status probe.
        health = {} if intent in STATIC_INTENTS or len(options) > 1 else await get_network_status()
        token = self._cors_outbound.set("reply")
        try:
            await update.message.reply_text(await report_text(
                health, question, previous_intent=previous_intent,
                delivery_mode=policy.get('mode', 'standard'),
            ))
        finally:
            self._cors_outbound.reset(token)

    async def _notify_cors_changes_once(self):
        from ..reference_network import get_network_status
        targets = await zalo_repository.cors_subscriptions()
        if not targets:
            return
        health = await get_network_status()
        status = health.get("status")
        if status not in {"online", "offline"}:
            return
        from ..zalo_cors_report import status_change_text
        message = None
        for target in targets:
            try:
                async with zalo_repository.cors_transition(target["id"], status) as delivery:
                    if delivery["send"]:
                        if message is None:
                            message = status_change_text(health)
                        token = self._cors_outbound.set({"kind": "alert", "policy": delivery["policy"]})
                        try:
                            sent = await self.app.bot.send_message(chat_id=target["chat_id"], text=message)
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
