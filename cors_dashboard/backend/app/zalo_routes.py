"""Zalo webhook, administration and account-link API routes."""

import logging
from typing import Any, Dict
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import secrets
from dotenv import set_key
from starlette.responses import Response
from fastapi.responses import PlainTextResponse

from . import auth, models, schemas
from .database import get_db, settings
from .runtime_config import ENV_FILE_PATH
from .zalo_bot_identity import BOT_IDENTITIES, chat_bot_key

logger = logging.getLogger("app.main")
router = APIRouter()


def _extract_zalo_webhook_secret(request: Request) -> str:
    query_names = (
        "secret_token",
        "secret",
        "webhook_secret",
        "zalo_webhook_secret",
        "token",
    )
    for query_name in query_names:
        value = request.query_params.get(query_name)
        if value:
            return value.strip()

    header_names = (
        "x-bot-api-secret-token",
        "x-zalo-bot-api-secret-token",
        "x-zalo-bot-secret-token",
        "x-zalo-webhook-secret",
        "x-zalo-webhook-secret-token",
        "x-webhook-secret-token",
        "x-telegram-bot-api-secret-token",
    )
    for header_name in header_names:
        value = request.headers.get(header_name)
        if value:
            return value.strip()

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return ""


def _get_bot_service(bot_key: str):
    from .zalo_bot_service import zalo_bot_services
    if bot_key not in zalo_bot_services:
        raise HTTPException(status_code=404, detail="Không tìm thấy bot Zalo.")
    return zalo_bot_services[bot_key]


def _validate_zalo_webhook_secret(request: Request, bot_key: str = "tun") -> None:
    if not settings.ENABLE_ZALO_BOT:
        raise HTTPException(status_code=503, detail="Zalo Bot is disabled")

    service = _get_bot_service(bot_key)
    expected_secret = service._bot_setting("WEBHOOK_SECRET")
    if not expected_secret:
        logger.error("Zalo webhook request rejected because ZALO_WEBHOOK_SECRET is not configured.")
        raise HTTPException(status_code=503, detail="Zalo webhook secret is not configured")

    provided_secret = _extract_zalo_webhook_secret(request)
    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        client_host = request.client.host if request.client else "unknown"
        logger.warning("Rejected Zalo webhook request with invalid secret from %s", client_host)
        raise HTTPException(status_code=401, detail="Invalid Zalo webhook secret")


def _persist_zalo_bot_enabled(enabled: bool) -> None:
    value = "true" if enabled else "false"
    set_key(str(ENV_FILE_PATH), "ENABLE_ZALO_BOT", value)
    os.environ["ENABLE_ZALO_BOT"] = value
    try:
        settings.ENABLE_ZALO_BOT = enabled
    except Exception:
        setattr(settings, "ENABLE_ZALO_BOT", enabled)


def _get_zalo_system_state() -> dict[str, Any]:
    from .zalo_bot_service import zalo_bot_services, ZALO_AVAILABLE

    bots = [{"key": key, **BOT_IDENTITIES[key],
             "token_configured": bool(service._bot_setting("BOT_TOKEN")),
             "running": service.app is not None, "mode": service._bot_mode()}
            for key, service in zalo_bot_services.items()]
    configured = [bot for bot in bots if bot["token_configured"]]
    running = bool(configured) and all(bot["running"] for bot in configured)
    sdk_available = bool(ZALO_AVAILABLE)
    return {
        "enabled": bool(settings.ENABLE_ZALO_BOT),
        "running": running,
        "token_configured": bool(configured),
        "sdk_available": sdk_available,
        "available": bool(configured) and sdk_available,
        "bots": bots,
        "mode": str(getattr(settings, "ZALO_BOT_MODE", "polling") or "polling").strip().lower(),
        "env_file": str(ENV_FILE_PATH),
    }


@router.get("/api/zalo/webhook", response_class=PlainTextResponse)
@router.get("/api/zalo/webhook/{bot_key}", response_class=PlainTextResponse)
async def zalo_webhook_verify(request: Request, bot_key: str = "tun"):
    _validate_zalo_webhook_secret(request, bot_key)
    return "ok"


@router.post("/api/zalo/webhook", response_class=PlainTextResponse)
@router.post("/api/zalo/webhook/{bot_key}", response_class=PlainTextResponse)
async def zalo_webhook(request: Request, response: Response, bot_key: str = "tun"):
    _validate_zalo_webhook_secret(request, bot_key)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    try:
        result = await _get_bot_service(bot_key).process_webhook_payload(payload)
        response.headers["X-Zalo-Trace-Id"] = result["trace_id"]
        response.headers["X-Zalo-Outbound-Count"] = str(result["outbound_count"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.exception("Zalo webhook runtime error: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Unhandled Zalo webhook error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Zalo webhook processing failed")

    return "ok"


@router.get("/api/admin/zalo/diagnostics")
async def get_zalo_diagnostics(
    limit: int = Query(default=20, ge=1, le=100),
    bot_key: str = Query(default="tun"),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    """Show recent, credential-redacted Zalo inbound/dispatch/outbound traces."""
    return _get_bot_service(bot_key).get_debug_events(limit=limit)


@router.delete("/api/admin/zalo/diagnostics")
async def clear_zalo_diagnostics(
    bot_key: str = Query(default="tun"),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    _get_bot_service(bot_key).clear_debug_events()
    return {"status": "cleared"}


@router.get("/api/admin/zalo/system")
async def get_zalo_system_state(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem trạng thái hệ thống Zalo.")
    return _get_zalo_system_state()


@router.patch("/api/admin/zalo/system")
async def update_zalo_system_state(
    payload: Dict[str, Any] = Body(...),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được bật/tắt hệ thống Zalo.")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload không hợp lệ.")

    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="Thiếu trường enabled.")

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled phải là boolean.")

    current_state = _get_zalo_system_state()
    if enabled and not current_state["available"]:
        if not current_state["sdk_available"]:
            raise HTTPException(status_code=400, detail="Thiếu Zalo SDK trong môi trường hiện tại nên không thể bật bot.")
        raise HTTPException(status_code=400, detail="Thiếu ZALO_BOT_TOKEN trong .env nên không thể bật bot.")

    if enabled == current_state["enabled"]:
        return current_state

    from .zalo_bot_service import start_zalo_bots, stop_zalo_bots

    _persist_zalo_bot_enabled(enabled)
    try:
        if enabled:
            if not current_state["running"]:
                await start_zalo_bots(strict=True)
        else:
            await stop_zalo_bots()
    except Exception as exc:
        if enabled:
            try:
                await stop_zalo_bots()
            except Exception:
                pass
        _persist_zalo_bot_enabled(not enabled)
        raise HTTPException(status_code=500, detail=f"Không thể cập nhật trạng thái Zalo Bot: {exc}")

    return _get_zalo_system_state()


@router.post("/api/auth/zalo-link-code")
async def create_zalo_link_code(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    from .zalo_assistant import zalo_repository
    result = await zalo_repository.create_link_code(current_user.id)
    result["can_register_group"] = False
    return result


@router.post("/api/auth/zalo-my-link-code")
async def create_my_zalo_link_code(
    current_user: models.User = Depends(auth.get_current_user),
):
    """Self-service: any authenticated user can create a personal link code."""
    from .zalo_assistant import zalo_repository
    result = await zalo_repository.create_link_code(current_user.id)
    result["can_register_group"] = False
    return result


@router.post("/api/admin/zalo-group-link-code")
async def create_zalo_group_link_code(
    body: schemas.ZaloGroupLinkCodeCreate,
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    """Create a 30-second OTP whose group role and station scope are immutable."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được tạo OTP liên kết group.")
    binding_type = str(body.binding_type or "")
    if binding_type not in {"group_admin", "group_public"}:
        raise HTTPException(status_code=400, detail="Loại group không hợp lệ.")
    scope_mode = str(body.station_scope_mode or "all")
    allowed_bases = sorted({str(value).strip() for value in body.allowed_bases if str(value).strip()})
    if binding_type == "group_admin":
        scope_mode, allowed_bases = "all", []
    elif scope_mode not in {"all", "selected", "none"}:
        raise HTTPException(status_code=400, detail="Phạm vi trạm không hợp lệ.")
    elif scope_mode == "selected":
        if not allowed_bases:
            raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một trạm.")
        available_rs = await devices_db.execute(select(models.Device.serial))
        available = {str(serial) for serial in available_rs.scalars().all()}
        unknown = sorted(set(allowed_bases) - available)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Trạm không tồn tại: {', '.join(unknown)}")
    else:
        allowed_bases = []

    from .zalo_assistant import zalo_repository
    return await zalo_repository.create_link_code(
        current_user.id,
        link_type=binding_type,
        station_scope_mode=scope_mode,
        allowed_bases=allowed_bases,
    )


@router.get("/api/auth/zalo-bindings")
async def get_zalo_bindings(
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    from .zalo_assistant import zalo_repository
    items = await zalo_repository.list_bindings_for_user(
        current_user.id, include_all=current_user.role == auth.Role.ADMIN,
    )
    stations = []
    if current_user.role == auth.Role.ADMIN:
        station_rows = await devices_db.execute(select(models.Device).order_by(models.Device.name, models.Device.serial))
        stations = [
            {"serial": str(item.serial), "name": str(item.name or item.serial), "status": str(item.status or "unknown")}
            for item in station_rows.scalars().all()
        ]
    return {
        "items": items,
        "total": len(items),
        "stations": stations,
        "is_admin": current_user.role == auth.Role.ADMIN,
        "system_enabled": bool(settings.ENABLE_ZALO_BOT),
        "bots": _get_zalo_system_state()["bots"],
    }


@router.patch("/api/auth/zalo-bindings/{binding_id}")
async def update_zalo_binding(
    binding_id: int,
    body: schemas.ZaloBindingAdminUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    from .zalo_assistant import zalo_repository
    changes = body.model_dump(exclude_unset=True)
    is_admin = current_user.role == auth.Role.ADMIN
    if "allowed_bases" in changes and is_admin:
        available_rs = await devices_db.execute(select(models.Device.serial))
        available = {str(serial) for serial in available_rs.scalars().all()}
        unknown = sorted(set(changes["allowed_bases"] or []) - available)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Trạm không tồn tại: {', '.join(unknown)}")
    try:
        item = await zalo_repository.update_binding_admin(
            binding_id, changes, actor_user_id=current_user.id, is_admin=is_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated", "item": item}


@router.delete("/api/auth/zalo-bindings/{binding_id}")
async def revoke_zalo_binding(
    binding_id: int,
    current_user: models.User = Depends(auth.get_current_user),
):
    from .zalo_assistant import zalo_repository
    deleted_chat_id = await zalo_repository.revoke_binding(
        binding_id, current_user.id, current_user.role == auth.Role.ADMIN,
    )
    if not deleted_chat_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy liên kết Zalo hoặc bạn không có quyền xóa")
    _get_bot_service(chat_bot_key(deleted_chat_id)).forget_chat(deleted_chat_id)
    return {"status": "deleted", "binding_id": binding_id, "chat_id": deleted_chat_id}
