from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import auth, models
from .database import auth_session_scope, settings
from .zalo_bot_identity import BOT_IDENTITIES, chat_bot_key, raw_chat_id


LINK_ALPHABET = string.digits
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_TIMEZONE = "Asia/Saigon"


def _now() -> int:
    return int(time.time())


def _hash_token(value: str, purpose: str) -> str:
    key = str(settings.SECRET_KEY).encode("utf-8")
    message = f"{purpose}:{str(value).strip().upper()}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    configured = str(getattr(settings, "ZALO_STATE_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        key = configured.encode("ascii")
    else:
        digest = hashlib.sha256(f"zalo-state:{settings.SECRET_KEY}".encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(value: str) -> dict[str, Any]:
    try:
        raw = _fernet().decrypt(str(value).encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return {}


def payload_fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ZaloPrincipal:
    chat_id: str
    sender_id: str
    chat_type: str
    binding_type: Optional[str]
    user_id: Optional[int]
    username: Optional[str]
    role: Optional[str]
    permissions: frozenset[str]
    is_linked: bool
    allowed_bases: frozenset[str] = frozenset()
    station_scope_mode: str = "all"

    @property
    def is_admin(self) -> bool:
        return self.role == auth.Role.ADMIN

    @property
    def is_public_group(self) -> bool:
        return self.binding_type == "group_public"

    def allows(self, permission: str) -> bool:
        return permission in self.permissions


class ZaloAssistantRepository:
    @staticmethod
    def delivery_mode(binding, pref) -> str:
        scopes = pref.scopes if pref and isinstance(pref.scopes, dict) else {}
        return "cors_only" if binding.binding_type == "cors_public" or scopes.get("delivery_mode") == "cors_only" else "standard"

    async def chat_delivery_policy(self, chat_id: str) -> dict:
        async with auth_session_scope() as db:
            result = await db.execute(select(models.ZaloChatBinding, models.ZaloNotificationPreference).outerjoin(
                models.ZaloNotificationPreference, models.ZaloNotificationPreference.binding_id == models.ZaloChatBinding.id
            ).where(models.ZaloChatBinding.chat_id == str(chat_id)))
            row = result.first()
            if not row:
                return {"active": True, "mode": "standard", "cors_notify_changes": False}
            binding, pref = row
            scopes = pref.scopes if pref and isinstance(pref.scopes, dict) else {}
            return {"active": bool(binding.is_active), "mode": self.delivery_mode(binding, pref),
                    "cors_notify_changes": bool(scopes.get("cors_notify_changes", False))}

    async def cors_subscriptions(self) -> list[dict]:
        async with auth_session_scope() as db:
            result = await db.execute(select(models.ZaloChatBinding, models.ZaloNotificationPreference).join(
                models.ZaloNotificationPreference, models.ZaloNotificationPreference.binding_id == models.ZaloChatBinding.id
            ).where(models.ZaloChatBinding.is_active.is_(True)))
            return [{"id": binding.id, "chat_id": str(binding.chat_id), "last_status": (pref.scopes or {}).get("cors_last_status")}
                    for binding, pref in result.all()
                    if self.delivery_mode(binding, pref) == "cors_only" and (pref.scopes or {}).get("cors_notify_changes")]

    @asynccontextmanager
    async def cors_transition(self, binding_id: int, status: str):
        """Serialize each chat's change delivery across bot workers."""
        async with auth_session_scope() as db:
            result = await db.execute(select(models.ZaloNotificationPreference).where(
                models.ZaloNotificationPreference.binding_id == binding_id).with_for_update(skip_locked=True))
            pref = result.scalars().first()
            binding = await db.get(models.ZaloChatBinding, binding_id) if pref else None
            valid = bool(binding and binding.is_active and self.delivery_mode(binding, pref) == "cors_only"
                         and (pref.scopes or {}).get("cors_notify_changes"))
            previous = (pref.scopes or {}).get("cors_last_status") if pref else None
            delivery = {"send": valid and previous is not None and previous != status, "sent": False,
                        "policy": {"active": valid, "mode": "cors_only", "cors_notify_changes": valid}}
            yield delivery
            if valid and (previous is None or delivery["sent"]):
                pref.scopes = {**(pref.scopes or {}), "cors_last_status": status}
                pref.updated_at = _now()
            await db.commit()

    async def is_chat_paused(self, chat_id: str) -> bool:
        async with auth_session_scope() as db:
            result = await db.execute(select(models.ZaloChatBinding.is_active).where(
                models.ZaloChatBinding.chat_id == str(chat_id),
            ))
            return result.scalar_one_or_none() is False

    async def remember_cors_chat(self, chat_id: str, chat_type: str, display_name: Optional[str] = None) -> bool:
        """Remember public callers for per-chat muting without granting dashboard access."""
        now = _now()
        async with auth_session_scope() as db:
            await db.execute(pg_insert(models.ZaloChatBinding).values(
                chat_id=str(chat_id), chat_type=chat_type, binding_type="cors_public",
                display_name=str(display_name or "")[:255] or None, is_active=True,
                station_scope_mode="none", allowed_bases=[], created_at=now, last_seen_at=now,
            ).on_conflict_do_nothing(index_elements=["chat_id"]))
            result = await db.execute(select(models.ZaloChatBinding).where(
                models.ZaloChatBinding.chat_id == str(chat_id),
            ))
            binding = result.scalars().first()
            if binding and binding.is_active:
                binding.last_seen_at = now
            await db.commit()
            return bool(binding and binding.is_active)

    @staticmethod
    def _notification_scope_allowed(scopes, subjects):
        mode = str(scopes.get("mode") or "all").lower()
        if mode == "none":
            return False
        if mode == "selected":
            selected = {
                str(value).strip().casefold()
                for value in (scopes.get("values") or [])
                if str(value).strip()
            }
            if not selected.intersection(subjects):
                return False
        return True

    @staticmethod
    def _inside_notification_quiet_hours(pref, severity):
        try:
            timezone = ZoneInfo(str(pref.timezone or DEFAULT_TIMEZONE))
        except ZoneInfoNotFoundError:
            # Windows Python installations may not ship an IANA tzdata
            # database. Vietnam has used UTC+7 without DST since 1975.
            timezone = datetime_timezone(timedelta(hours=7))
        now = datetime.now(timezone)
        try:
            start_hour, start_minute = map(int, str(pref.quiet_start).split(":", 1))
            end_hour, end_minute = map(int, str(pref.quiet_end).split(":", 1))
            current = now.hour * 60 + now.minute
            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            in_quiet_hours = start <= current < end if start < end else current >= start or current < end
        except (TypeError, ValueError):
            in_quiet_hours = False
        return in_quiet_hours and not (severity == "critical" and pref.critical_override)

    @staticmethod
    def _notification_allowed(
        pref: models.ZaloNotificationPreference,
        severity: str,
        subjects: set[str],
        scope_override: Optional[dict[str, Any]] = None,
    ) -> bool:
        severity = str(severity or "info").lower()
        minimum = str(pref.min_severity or "critical").lower()
        if not pref.enabled or SEVERITY_ORDER.get(severity, 0) < SEVERITY_ORDER.get(minimum, 2):
            return False
        scopes = scope_override if isinstance(scope_override, dict) else (
            pref.scopes if isinstance(pref.scopes, dict) else {}
        )
        if not ZaloAssistantRepository._notification_scope_allowed(scopes, subjects):
            return False
        if pref.quiet_start and pref.quiet_end and ZaloAssistantRepository._inside_notification_quiet_hours(pref, severity):
            return False
        return True

    async def create_link_code(
        self,
        user_id: int,
        link_type: str = "private",
        station_scope_mode: str = "all",
        allowed_bases: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        now = _now()
        link_type = str(link_type or "private")
        if link_type not in {"private", "group_admin", "group_public"}:
            raise ValueError("Loại liên kết Zalo không hợp lệ.")
        station_scope_mode = str(station_scope_mode or "all")
        if station_scope_mode not in {"all", "selected", "none"}:
            raise ValueError("Phạm vi trạm không hợp lệ.")
        normalized_bases = sorted({
            str(value).strip() for value in (allowed_bases or []) if str(value).strip()
        })
        if link_type != "group_public":
            station_scope_mode = "all"
            normalized_bases = []
        elif station_scope_mode == "selected" and not normalized_bases:
            raise ValueError("Group public giới hạn cần chọn ít nhất một trạm.")
        elif station_scope_mode != "selected":
            normalized_bases = []
        # Link OTPs intentionally behave like authenticator codes.  Cap an old
        # production value (for example the former 600 seconds) at 30 seconds.
        ttl = max(15, min(int(settings.ZALO_LINK_CODE_TTL_SECONDS), 30))
        code = "".join(secrets.choice(LINK_ALPHABET) for _ in range(6))
        code_hash = _hash_token(code, "link")
        async with auth_session_scope() as db:
            await db.execute(
                update(models.ZaloLinkCode)
                .where(
                    models.ZaloLinkCode.user_id == int(user_id),
                    models.ZaloLinkCode.used_at.is_(None),
                    models.ZaloLinkCode.link_type == link_type,
                )
                .values(used_at=now)
            )
            db.add(models.ZaloLinkCode(
                user_id=int(user_id), code_hash=code_hash, created_at=now, expires_at=now + ttl,
                link_type=link_type, station_scope_mode=station_scope_mode,
                allowed_bases=normalized_bases,
            ))
            await db.commit()
        return {
            "code": code, "expires_at": now + ttl, "expires_in": ttl,
            "link_type": link_type, "station_scope_mode": station_scope_mode,
            "allowed_bases": normalized_bases,
        }

    async def consume_link_code(
        self, code: str, chat_id: str, sender_id: str, display_name: Optional[str] = None
    ) -> tuple[models.ZaloChatBinding, models.User]:
        now = _now()
        code_hash = _hash_token(code, "link")
        async with auth_session_scope() as db:
            rs = await db.execute(
                select(models.ZaloLinkCode)
                .where(models.ZaloLinkCode.code_hash == code_hash)
                .with_for_update()
            )
            link = rs.scalars().first()
            if (
                not link or link.used_at is not None or int(link.expires_at) < now
                or str(link.link_type or "private") != "private"
            ):
                raise ValueError("Mã liên kết không hợp lệ, đã dùng hoặc đã hết hạn.")
            user = await db.get(models.User, link.user_id)
            if not user or not user.is_active:
                raise ValueError("Tài khoản dashboard không tồn tại hoặc đã bị vô hiệu hóa.")

            binding_rs = await db.execute(
                select(models.ZaloChatBinding).where(models.ZaloChatBinding.chat_id == str(chat_id))
            )
            binding = binding_rs.scalars().first()
            if binding is None:
                binding = models.ZaloChatBinding(
                    chat_id=str(chat_id), chat_type="private", binding_type="private",
                    created_at=now, registered_by=str(sender_id), last_seen_at=now,
                )
                db.add(binding)
            binding.binding_type = "private"
            binding.user_id = int(user.id)
            binding.sender_id = str(sender_id)
            binding.display_name = str(display_name or "")[:255] or None
            binding.is_active = True
            binding.onboarding_completed = False
            binding.last_seen_at = now
            link.used_at = now
            link.used_by_chat_id = str(chat_id)
            await db.flush()

            pref_rs = await db.execute(
                select(models.ZaloNotificationPreference)
                .where(models.ZaloNotificationPreference.binding_id == binding.id)
            )
            if pref_rs.scalars().first() is None:
                db.add(models.ZaloNotificationPreference(
                    binding_id=binding.id, enabled=True, min_severity="critical", scopes={},
                    timezone=DEFAULT_TIMEZONE, critical_override=True, updated_at=now,
                ))
            await db.commit()
            await db.refresh(binding)
            return binding, user

    async def consume_group_link_code(
        self, code: str, chat_id: str, sender_id: str, display_name: Optional[str] = None
    ) -> tuple[models.ZaloChatBinding, models.User]:
        """Consume an admin-created OTP carrying the complete group policy."""
        now = _now()
        code_hash = _hash_token(code, "link")
        async with auth_session_scope() as db:
            rs = await db.execute(
                select(models.ZaloLinkCode)
                .where(models.ZaloLinkCode.code_hash == code_hash)
                .with_for_update()
            )
            link = rs.scalars().first()
            if (
                not link or link.used_at is not None or int(link.expires_at) < now
                or str(link.link_type or "private") not in {"group_admin", "group_public"}
            ):
                raise ValueError("Mã liên kết không hợp lệ, đã dùng hoặc đã hết hạn.")
            user = await db.get(models.User, link.user_id)
            if not user or not user.is_active:
                raise ValueError("Tài khoản dashboard không tồn tại hoặc đã bị vô hiệu hóa.")
            if user.role != auth.Role.ADMIN:
                raise ValueError("Chỉ tài khoản dashboard admin mới tạo được mã đăng ký group admin.")

            binding_rs = await db.execute(
                select(models.ZaloChatBinding).where(models.ZaloChatBinding.chat_id == str(chat_id))
            )
            binding = binding_rs.scalars().first()
            if binding is None:
                binding = models.ZaloChatBinding(
                    chat_id=str(chat_id), chat_type="group", created_at=now,
                )
                db.add(binding)
            binding.chat_type = "group"
            binding.binding_type = str(link.link_type)
            binding.user_id = None
            binding.sender_id = None
            binding.display_name = str(display_name or "")[:255] or None
            binding.registered_by = str(sender_id)
            binding.is_active = True
            binding.onboarding_completed = False
            binding.last_seen_at = now
            binding.station_scope_mode = (
                str(link.station_scope_mode or "all")
                if binding.binding_type == "group_public" else "all"
            )
            binding.allowed_bases = (
                list(link.allowed_bases or [])
                if binding.binding_type == "group_public"
                and binding.station_scope_mode == "selected" else []
            )
            link.used_at = now
            link.used_by_chat_id = str(chat_id)
            await db.flush()

            pref_rs = await db.execute(
                select(models.ZaloNotificationPreference)
                .where(models.ZaloNotificationPreference.binding_id == binding.id)
            )
            pref = pref_rs.scalars().first()
            if pref is None:
                db.add(models.ZaloNotificationPreference(
                    binding_id=binding.id, enabled=True, min_severity="critical",
                    scopes={
                        "mode": binding.station_scope_mode,
                        "allowed_bases": list(binding.allowed_bases or []),
                    }, timezone=DEFAULT_TIMEZONE,
                    critical_override=True, updated_at=now,
                ))
            else:
                pref.scopes = {
                    "mode": binding.station_scope_mode,
                    "allowed_bases": list(binding.allowed_bases or []),
                }
                pref.updated_at = now
            await db.commit()
            await db.refresh(binding)
            return binding, user

    async def resolve_principal(
        self, chat_id: str, sender_id: str, chat_type: str
    ) -> ZaloPrincipal:
        async with auth_session_scope() as db:
            rs = await db.execute(
                select(models.ZaloChatBinding).where(
                    models.ZaloChatBinding.chat_id == str(chat_id),
                    models.ZaloChatBinding.is_active.is_(True),
                )
            )
            binding = rs.scalars().first()
            if not binding:
                return ZaloPrincipal(
                    str(chat_id), str(sender_id), chat_type, None, None, None, None,
                    frozenset(), False,
                )
            binding.last_seen_at = _now()
            pref = (await db.execute(select(models.ZaloNotificationPreference).where(
                models.ZaloNotificationPreference.binding_id == binding.id))).scalars().first()
            if self.delivery_mode(binding, pref) == "cors_only":
                await db.commit()
                return ZaloPrincipal(
                    str(chat_id), str(sender_id), chat_type, "cors_public", None, None, None,
                    frozenset(), False, station_scope_mode="none",
                )
            if binding.binding_type == "group_admin":
                permissions = frozenset(auth.ROLE_PERMISSIONS[auth.Role.ADMIN])
                allowed_bases = frozenset(
                    str(value) for value in (binding.allowed_bases or []) if str(value).strip()
                ) if binding.station_scope_mode == "selected" else frozenset()
                await db.commit()
                return ZaloPrincipal(
                    str(chat_id), str(sender_id), chat_type, binding.binding_type,
                    None, "zalo-group-admin", auth.Role.ADMIN, permissions, True,
                    allowed_bases, str(binding.station_scope_mode or "all"),
                )
            if binding.binding_type == "group_public":
                permissions = frozenset({auth.Permission.VIEW_DEVICES})
                allowed_bases = frozenset(
                    str(value) for value in (binding.allowed_bases or []) if str(value).strip()
                ) if binding.station_scope_mode == "selected" else frozenset()
                await db.commit()
                return ZaloPrincipal(
                    str(chat_id), str(sender_id), chat_type, binding.binding_type,
                    None, "zalo-group-public", auth.Role.VIEWER, permissions, True,
                    allowed_bases, str(binding.station_scope_mode or "all"),
                )
            user = await db.get(models.User, binding.user_id) if binding.user_id else None
            if not user or not user.is_active:
                binding.is_active = False
                await db.commit()
                return ZaloPrincipal(
                    str(chat_id), str(sender_id), chat_type, binding.binding_type,
                    None, None, None, frozenset(), False,
                )
            permissions = frozenset(auth.get_user_permissions(user))
            await db.commit()
            return ZaloPrincipal(
                str(chat_id), str(sender_id), chat_type, binding.binding_type,
                int(user.id), str(user.username), str(user.role), permissions, True,
            )

    async def list_bindings_for_user(self, user_id: int, include_all: bool = False) -> list[dict[str, Any]]:
        async with auth_session_scope() as db:
            query = (
                select(models.ZaloChatBinding, models.ZaloNotificationPreference)
                .outerjoin(
                    models.ZaloNotificationPreference,
                    models.ZaloNotificationPreference.binding_id == models.ZaloChatBinding.id,
                )
            )
            if not include_all:
                query = query.where(models.ZaloChatBinding.user_id == int(user_id))
            rs = await db.execute(query.order_by(models.ZaloChatBinding.created_at.desc()))
            items = []
            for binding, pref in rs.all():
                item = self.binding_dict(binding)
                mode = self.delivery_mode(binding, pref)
                item["delivery_mode"] = mode
                user = await db.get(models.User, binding.user_id) if binding.user_id else None
                item.update({
                    "dashboard_username": str(user.username) if user else None,
                    "dashboard_role": str(user.role) if user else None,
                    "permissions": [] if mode == "cors_only" else sorted(auth.get_user_permissions(user)) if user else (
                        sorted(auth.ROLE_PERMISSIONS[auth.Role.ADMIN])
                        if binding.binding_type == "group_admin"
                        else [] if binding.binding_type == "cors_public" else [auth.Permission.VIEW_DEVICES]
                    ),
                    "notifications": {
                        "cors_notify_changes": bool((pref.scopes or {}).get("cors_notify_changes", False)),
                        "enabled": bool(pref.enabled),
                        "min_severity": str(pref.min_severity),
                        "quiet_start": pref.quiet_start,
                        "quiet_end": pref.quiet_end,
                        "critical_override": bool(pref.critical_override),
                    } if pref else None,
                })
                items.append(item)
            return items

    async def revoke_binding(
        self, binding_id: int, user_id: int, is_admin: bool
    ) -> Optional[str]:
        async with auth_session_scope() as db:
            binding = await db.get(models.ZaloChatBinding, int(binding_id))
            if not binding or (not is_admin and int(binding.user_id or 0) != int(user_id)):
                return None
            chat_id = str(binding.chat_id)
            await db.execute(delete(models.ZaloWorkflowState).where(
                models.ZaloWorkflowState.chat_id == binding.chat_id
            ))
            await db.execute(delete(models.ZaloPendingAction).where(
                models.ZaloPendingAction.chat_id == binding.chat_id
            ))
            await db.execute(delete(models.ZaloDeliveryLog).where(
                models.ZaloDeliveryLog.binding_id == binding.id
            ))
            await db.execute(delete(models.ZaloNotificationPreference).where(
                models.ZaloNotificationPreference.binding_id == binding.id
            ))
            await db.delete(binding)
            await db.commit()
            return chat_id

    async def unlink_private_chat(self, chat_id: str, sender_id: str) -> bool:
        async with auth_session_scope() as db:
            rs = await db.execute(select(models.ZaloChatBinding).where(
                models.ZaloChatBinding.chat_id == str(chat_id),
                models.ZaloChatBinding.chat_type == "private",
                models.ZaloChatBinding.binding_type == "private",
                models.ZaloChatBinding.is_active.is_(True),
            ))
            binding = rs.scalars().first()
            if not binding or (binding.sender_id and str(binding.sender_id) != str(sender_id)):
                return False
            await db.execute(delete(models.ZaloWorkflowState).where(
                models.ZaloWorkflowState.chat_id == str(chat_id),
                models.ZaloWorkflowState.sender_id == str(sender_id),
            ))
            await db.execute(delete(models.ZaloPendingAction).where(
                models.ZaloPendingAction.chat_id == str(chat_id),
                models.ZaloPendingAction.sender_id == str(sender_id),
            ))
            await db.execute(delete(models.ZaloDeliveryLog).where(
                models.ZaloDeliveryLog.binding_id == binding.id
            ))
            await db.execute(delete(models.ZaloNotificationPreference).where(
                models.ZaloNotificationPreference.binding_id == binding.id
            ))
            await db.delete(binding)
            await db.commit()
            return True

    async def get_binding(self, chat_id: str) -> Optional[models.ZaloChatBinding]:
        async with auth_session_scope() as db:
            rs = await db.execute(select(models.ZaloChatBinding).where(
                models.ZaloChatBinding.chat_id == str(chat_id),
                models.ZaloChatBinding.is_active.is_(True),
            ))
            return rs.scalars().first()

    async def complete_onboarding(self, binding_id: int) -> None:
        async with auth_session_scope() as db:
            binding = await db.get(models.ZaloChatBinding, int(binding_id))
            if binding:
                binding.onboarding_completed = True
                await db.commit()

    @staticmethod
    def binding_dict(binding: models.ZaloChatBinding) -> dict[str, Any]:
        return {
            "id": binding.id,
            "chat_id": raw_chat_id(binding.chat_id),
            "bot_key": chat_bot_key(binding.chat_id),
            "bot_name": BOT_IDENTITIES[chat_bot_key(binding.chat_id)]["name"],
            "bot_owner": BOT_IDENTITIES[chat_bot_key(binding.chat_id)]["owner"],
            "chat_type": binding.chat_type,
            "binding_type": binding.binding_type,
            "display_name": binding.display_name,
            "is_active": binding.is_active,
            "onboarding_completed": binding.onboarding_completed,
            "created_at": binding.created_at,
            "last_seen_at": binding.last_seen_at,
            "station_scope_mode": str(binding.station_scope_mode or "all"),
            "allowed_bases": list(binding.allowed_bases or []),
        }

    @staticmethod
    def _validate_binding_admin_changes(changes: dict[str, Any]) -> None:
        if "delivery_mode" in changes and changes["delivery_mode"] not in {"standard", "cors_only"}:
            raise ValueError("Chế độ gửi tin không hợp lệ.")
        if "min_severity" in changes and str(changes["min_severity"]) not in SEVERITY_ORDER:
            raise ValueError("Mức cảnh báo không hợp lệ.")
        quiet_start = changes.get("quiet_start")
        quiet_end = changes.get("quiet_end")
        if (quiet_start is None) != (quiet_end is None):
            raise ValueError("Giờ im lặng phải có đủ thời gian bắt đầu và kết thúc.")
        for value in (quiet_start, quiet_end):
            if value is None:
                continue
            try:
                hour, minute = map(int, str(value).split(":", 1))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("Giờ im lặng phải đúng định dạng HH:MM.")

    @staticmethod
    def _authorize_binding_admin_update(binding, changes, actor_user_id, is_admin):
        if is_admin:
            return
        if binding.binding_type != "private" or int(binding.user_id or 0) != int(actor_user_id or 0):
            raise ValueError("Bạn không có quyền sửa liên kết Zalo này.")
        allowed_self_service = {
            "display_name", "notifications_enabled", "min_severity",
            "quiet_start", "quiet_end", "critical_override",
        }
        forbidden = sorted(set(changes) - allowed_self_service)
        if forbidden:
            raise ValueError("Tài khoản cá nhân chỉ được sửa tên và cấu hình cảnh báo.")

    @staticmethod
    def _apply_binding_identity_changes(binding, changes):
        if "display_name" in changes:
            binding.display_name = str(changes.get("display_name") or "").strip()[:255] or None
        if "binding_type" in changes:
            binding_type = str(changes["binding_type"])
            allowed_types = (
                {"private", "cors_public"} if binding.chat_type == "private"
                else {"group_admin", "group_public", "cors_public"}
            )
            if binding_type not in allowed_types:
                raise ValueError("Loại liên kết không phù hợp với loại chat.")
            binding.binding_type = binding_type
            if binding_type == "group_admin":
                binding.station_scope_mode = "all"
                binding.allowed_bases = []
        if "is_active" in changes:
            binding.is_active = bool(changes["is_active"])

    @staticmethod
    def _apply_binding_scope_changes(binding, changes):
        if "station_scope_mode" in changes:
            mode = str(changes["station_scope_mode"])
            if mode not in {"all", "selected", "none"}:
                raise ValueError("Phạm vi trạm không hợp lệ.")
            binding.station_scope_mode = mode
        if "allowed_bases" in changes:
            binding.allowed_bases = sorted({
                str(value).strip() for value in (changes.get("allowed_bases") or []) if str(value).strip()
            })
        if binding.binding_type == "group_admin":
            binding.station_scope_mode = "all"
            binding.allowed_bases = []
        if binding.binding_type == "cors_public":
            binding.station_scope_mode = "none"
            binding.allowed_bases = []
            changes["notifications_enabled"] = False

    @staticmethod
    def _notification_change_values(changes):
        notification_keys = {
            "notifications_enabled": "enabled",
            "min_severity": "min_severity",
            "quiet_start": "quiet_start",
            "quiet_end": "quiet_end",
            "critical_override": "critical_override",
        }
        return {
            target: changes[source]
            for source, target in notification_keys.items()
            if source in changes
        }

    async def _get_or_create_notification_preference(self, db, binding, now):
        result = await db.execute(select(models.ZaloNotificationPreference).where(
            models.ZaloNotificationPreference.binding_id == binding.id
        ))
        preference = result.scalars().first()
        if preference is None:
            preference = models.ZaloNotificationPreference(
                binding_id=binding.id, enabled=True, min_severity="critical",
                scopes={"mode": "all"}, timezone=DEFAULT_TIMEZONE,
                critical_override=True, updated_at=now,
            )
            db.add(preference)
        return preference

    @staticmethod
    def _apply_notification_policy_changes(binding, changes, scopes):
        if "delivery_mode" in changes:
            if binding.binding_type == "cors_public" and changes["delivery_mode"] == "standard":
                raise ValueError("Chat public cần liên kết tài khoản trước khi dùng quyền dashboard.")
            if changes["delivery_mode"] == "cors_only" and scopes.get("delivery_mode") != "cors_only":
                scopes.pop("cors_last_status", None)
            scopes["delivery_mode"] = changes["delivery_mode"]
        if "cors_notify_changes" in changes:
            if changes["cors_notify_changes"] and not scopes.get("cors_notify_changes"):
                scopes.pop("cors_last_status", None)
            scopes["cors_notify_changes"] = bool(changes["cors_notify_changes"])

    async def _update_binding_notification_preference(self, db, binding, changes, now):
        notification_changes = self._notification_change_values(changes)
        scope_changed = "station_scope_mode" in changes or "allowed_bases" in changes
        policy_changed = "delivery_mode" in changes or "cors_notify_changes" in changes
        if not (notification_changes or scope_changed or policy_changed):
            return
        preference = await self._get_or_create_notification_preference(db, binding, now)
        for key, value in notification_changes.items():
            setattr(preference, key, value)
        scopes = dict(preference.scopes or {})
        if scope_changed:
            scopes.update({
                "mode": str(binding.station_scope_mode or "all"),
                "values": list(binding.allowed_bases or []),
            })
        self._apply_notification_policy_changes(binding, changes, scopes)
        preference.scopes = scopes
        preference.updated_at = now

    async def update_binding_admin(
        self, binding_id: int, changes: dict[str, Any],
        actor_user_id: Optional[int] = None, is_admin: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        self._validate_binding_admin_changes(changes)
        async with auth_session_scope() as db:
            binding = await db.get(models.ZaloChatBinding, int(binding_id))
            if not binding:
                raise ValueError("Không tìm thấy liên kết Zalo.")
            self._authorize_binding_admin_update(binding, changes, actor_user_id, is_admin)
            self._apply_binding_identity_changes(binding, changes)
            self._apply_binding_scope_changes(binding, changes)
            await self._update_binding_notification_preference(db, binding, changes, now)
            await db.commit()
            await db.refresh(binding)
            return self.binding_dict(binding)
    async def save_workflow(
        self, chat_id: str, sender_id: str, flow: str, step: str,
        state: dict[str, Any], ttl_seconds: int = 600,
    ) -> None:
        now = _now()
        encrypted = encrypt_json(state)
        async with auth_session_scope() as db:
            rs = await db.execute(select(models.ZaloWorkflowState).where(
                models.ZaloWorkflowState.chat_id == str(chat_id),
                models.ZaloWorkflowState.sender_id == str(sender_id),
            ))
            row = rs.scalars().first()
            if row is None:
                row = models.ZaloWorkflowState(
                    chat_id=str(chat_id), sender_id=str(sender_id), created_at=now,
                )
                db.add(row)
            row.flow = flow
            row.step = step
            row.state_encrypted = encrypted
            row.updated_at = now
            row.expires_at = now + max(60, int(ttl_seconds))
            await db.commit()

    async def load_workflow(self, chat_id: str, sender_id: str) -> Optional[dict[str, Any]]:
        now = _now()
        async with auth_session_scope() as db:
            rs = await db.execute(select(models.ZaloWorkflowState).where(
                models.ZaloWorkflowState.chat_id == str(chat_id),
                models.ZaloWorkflowState.sender_id == str(sender_id),
            ))
            row = rs.scalars().first()
            if not row:
                return None
            if int(row.expires_at) < now:
                await db.delete(row)
                await db.commit()
                return None
            return {
                "flow": row.flow, "step": row.step, "data": decrypt_json(row.state_encrypted),
                "expires_at": row.expires_at, "version": row.version,
            }

    async def delete_workflow(self, chat_id: str, sender_id: str) -> None:
        async with auth_session_scope() as db:
            await db.execute(delete(models.ZaloWorkflowState).where(
                models.ZaloWorkflowState.chat_id == str(chat_id),
                models.ZaloWorkflowState.sender_id == str(sender_id),
            ))
            await db.commit()

    async def create_pending_action(
        self, *, chat_id: str, sender_id: str, user_id: Optional[int], action: str,
        payload: dict[str, Any], preview: str, risk_level: str = "standard",
        target_identifier: Optional[str] = None, state_fingerprint: Optional[str] = None,
    ) -> tuple[str, models.ZaloPendingAction]:
        now = _now()
        ttl = max(30, min(int(settings.ZALO_PENDING_ACTION_TTL_SECONDS), 600))
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        row = models.ZaloPendingAction(
            id=uuid.uuid4().hex, code_hash=_hash_token(code, "confirm"),
            chat_id=str(chat_id), sender_id=str(sender_id), user_id=user_id,
            action=action, risk_level=risk_level, target_identifier=target_identifier,
            payload_encrypted=encrypt_json(payload), preview=preview,
            state_fingerprint=state_fingerprint, status="pending",
            created_at=now, expires_at=now + ttl,
        )
        async with auth_session_scope() as db:
            await db.execute(update(models.ZaloPendingAction).where(
                models.ZaloPendingAction.chat_id == str(chat_id),
                models.ZaloPendingAction.sender_id == str(sender_id),
                models.ZaloPendingAction.status == "pending",
            ).values(status="cancelled"))
            db.add(row)
            await db.commit()
        return code, row

    async def claim_pending_action(
        self, code: str, chat_id: str, sender_id: str, typed_target: Optional[str] = None
    ) -> tuple[models.ZaloPendingAction, dict[str, Any]]:
        now = _now()
        async with auth_session_scope() as db:
            rs = await db.execute(
                select(models.ZaloPendingAction)
                .where(models.ZaloPendingAction.code_hash == _hash_token(code, "confirm"))
                .with_for_update()
            )
            row = rs.scalars().first()
            if not row or row.status != "pending" or int(row.expires_at) < now:
                raise ValueError("Mã xác nhận không tồn tại, đã dùng hoặc đã hết hạn.")
            if row.chat_id != str(chat_id) or row.sender_id != str(sender_id):
                raise ValueError("Mã xác nhận thuộc một người hoặc cuộc trò chuyện khác.")
            if row.risk_level == "dangerous":
                expected = str(row.target_identifier or "").strip().casefold()
                received = str(typed_target or "").strip().casefold()
                if not expected or not hmac.compare_digest(expected, received):
                    raise ValueError("Thao tác nguy hiểm cần nhập lại chính xác tên đối tượng sau mã.")
            row.status = "executing"
            await db.commit()
            await db.refresh(row)
            return row, decrypt_json(row.payload_encrypted)

    async def finish_pending_action(self, action_id: str, success: bool) -> None:
        async with auth_session_scope() as db:
            row = await db.get(models.ZaloPendingAction, str(action_id))
            if row:
                row.status = "executed" if success else "failed"
                row.executed_at = _now()
                await db.commit()

    async def cancel_pending_action(self, code: str, chat_id: str, sender_id: str) -> bool:
        async with auth_session_scope() as db:
            rs = await db.execute(select(models.ZaloPendingAction).where(
                models.ZaloPendingAction.code_hash == _hash_token(code, "confirm"),
                models.ZaloPendingAction.chat_id == str(chat_id),
                models.ZaloPendingAction.sender_id == str(sender_id),
                models.ZaloPendingAction.status == "pending",
            ))
            row = rs.scalars().first()
            if not row:
                return False
            row.status = "cancelled"
            await db.commit()
            return True

    async def set_notification_preference(
        self, binding_id: int, **changes: Any
    ) -> dict[str, Any]:
        allowed = {
            "enabled", "min_severity", "scopes", "quiet_start", "quiet_end",
            "timezone", "critical_override",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        values["updated_at"] = _now()
        async with auth_session_scope() as db:
            binding = await db.get(models.ZaloChatBinding, int(binding_id))
            if not binding:
                raise ValueError("Không tìm thấy liên kết Zalo.")
            if binding.chat_type == "group":
                values["scopes"] = {
                    "mode": str(binding.station_scope_mode or "all"),
                    "values": list(binding.allowed_bases or []),
                }
            rs = await db.execute(select(models.ZaloNotificationPreference).where(
                models.ZaloNotificationPreference.binding_id == int(binding_id)
            ))
            pref = rs.scalars().first()
            if pref is None:
                pref = models.ZaloNotificationPreference(binding_id=int(binding_id), **values)
                db.add(pref)
            else:
                for key, value in values.items():
                    setattr(pref, key, value)
            await db.commit()
            return {
                "enabled": pref.enabled, "min_severity": pref.min_severity,
                "scopes": pref.scopes or {}, "quiet_start": pref.quiet_start,
                "quiet_end": pref.quiet_end, "timezone": pref.timezone,
                "critical_override": pref.critical_override,
            }

    @staticmethod
    def _private_notification_scope(binding_id, pref, binding_scopes):
        values = binding_scopes.get(int(binding_id), set())
        preference_scope = pref.scopes if isinstance(pref.scopes, dict) else {}
        preference_mode = str(preference_scope.get("mode") or "all")
        if preference_mode == "none":
            values = set()
        elif preference_mode == "selected":
            preferred = {
                str(value).strip().casefold()
                for value in (preference_scope.get("values") or []) if str(value).strip()
            }
            values = set(values).intersection(preferred)
        return {"mode": "selected", "values": sorted(values)}

    def _notification_scope_override(self, binding, pref, binding_scopes):
        if binding.binding_type == "private" and binding_scopes is not None:
            return self._private_notification_scope(binding.id, pref, binding_scopes)
        if binding.chat_type == "group":
            return {
                "mode": str(binding.station_scope_mode or "all"),
                "values": list(binding.allowed_bases or []),
            }
        return None

    async def _reserve_notification_target(
        self, db, binding, pref, event_key, severity, subjects, binding_scopes, bot_key, now
    ):
        if bot_key is not None and chat_bot_key(binding.chat_id) != bot_key:
            return None
        if self.delivery_mode(binding, pref) == "cors_only":
            return None
        override = self._notification_scope_override(binding, pref, binding_scopes)
        if not self._notification_allowed(pref, severity, subjects, override):
            return None
        reserve = (
            pg_insert(models.ZaloDeliveryLog)
            .values(
                binding_id=int(binding.id), event_key=str(event_key),
                severity=str(severity), status="pending", attempts=0, created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["binding_id", "event_key"])
            .returning(models.ZaloDeliveryLog.id)
        )
        reserved = (await db.execute(reserve)).scalar_one_or_none()
        if reserved is None:
            return None
        scope = override or pref.scopes or {}
        return {
            "binding_id": int(binding.id),
            "chat_id": str(binding.chat_id),
            "delivery_id": int(reserved),
            "scope_mode": str(scope.get("mode") or "all"),
            "scope_values": list(scope.get("values") or []),
        }

    async def reserve_notification_targets(
        self,
        event_key: str,
        severity: str,
        subjects: set[str],
        binding_scopes: Optional[dict[int, set[str]]] = None,
        bot_key: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Select eligible chats and atomically reserve one delivery per event/chat."""
        normalized_subjects = {
            str(value).strip().casefold() for value in subjects if str(value).strip()
        }
        now = _now()
        targets: list[dict[str, Any]] = []
        async with auth_session_scope() as db:
            rs = await db.execute(
                select(models.ZaloChatBinding, models.ZaloNotificationPreference)
                .join(
                    models.ZaloNotificationPreference,
                    models.ZaloNotificationPreference.binding_id == models.ZaloChatBinding.id,
                )
                .where(models.ZaloChatBinding.is_active.is_(True))
            )
            for binding, pref in rs.all():
                target = await self._reserve_notification_target(
                    db, binding, pref, event_key, severity, normalized_subjects,
                    binding_scopes, bot_key, now,
                )
                if target is not None:
                    targets.append(target)
            await db.commit()
        return targets

    async def finish_delivery(self, delivery_id: int, success: bool, error: Optional[str] = None) -> None:
        async with auth_session_scope() as db:
            values: dict[str, Any] = {
                "status": "sent" if success else "failed",
                "attempts": models.ZaloDeliveryLog.attempts + 1,
                "last_error": None if success else str(error or "Unknown delivery error")[:4000],
            }
            if success:
                values["sent_at"] = _now()
            await db.execute(
                update(models.ZaloDeliveryLog)
                .where(models.ZaloDeliveryLog.id == int(delivery_id))
                .values(**values)
            )
            await db.commit()


zalo_repository = ZaloAssistantRepository()
