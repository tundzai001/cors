"""Application startup, background tasks and orderly shutdown."""

import logging
import asyncio
from datetime import datetime
import time
import re
import unicodedata
import difflib
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Set, Dict
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from .device_command_routes import (
    configure_chip_endpoint,
    configure_service_endpoint,
    delete_device_from_list,
    lock_device,
    reset_pi_device,
    unlock_device,
)
from .caster_monitor import caster_monitor_task
from .esp_base_manager import esp_base_manager_task
from .station_runtime import (
    PI_INGEST_WORKER_COUNT,
    enqueue_ui_broadcast,
    event_loop_watchdog_task,
    latest_device_telemetry_cache,
    memory_guard_task,
    periodic_buffer_flush,
    pi_status_ingest_worker_task,
    ui_broadcast_worker_task,
    ui_status_broadcast_worker_task,
)
from .rover_accounts_routes import create_multi_base_rover
from .rover_tools_routes import bulk_renew_rovers
from .caster_routes import block_caster_user, unblock_caster_user, get_admin_caster_sources
from .ntrip_control import (
    handle_ntrip_mqtt_command,
    rtcm_ntrip_clienttable_keepalive_task,
    rtcm_session_reaper_task,
)
from .station_config import _device_service_config
from .redis_client import redis_client
from . import crud, models, schemas, auth
from .database import engine, auth_engine, settings, device_session_scope, auth_session_scope
from . import mqtt as mqtt_handler
from . import models, auth, crud
from .schema_compat import ensure_auth_zalo_runtime_columns, ensure_device_runtime_columns
from .status_reconciler import status_reconciler
from .operations_routes import _ops_visible_devices, _ops_visible_rovers, _build_ops_alerts
from .caster_cache import _get_all_caster_cache

logger = logging.getLogger("app.main")

GNSS_SYSTEM_NAMES = {
    "us": "GPS (Mỹ)", "gps": "GPS (Mỹ)", "gp": "GPS (Mỹ)",
    "ru": "GLONASS (Nga)", "glonass": "GLONASS (Nga)", "gl": "GLONASS (Nga)",
    "eu": "Galileo (Châu Âu)", "galileo": "Galileo (Châu Âu)", "ga": "Galileo (Châu Âu)",
    "cn": "BeiDou (Trung Quốc)", "beidou": "BeiDou (Trung Quốc)", "gb": "BeiDou (Trung Quốc)", "bd": "BeiDou (Trung Quốc)",
    "jp": "QZSS (Nhật Bản)", "qzss": "QZSS (Nhật Bản)", "gq": "QZSS (Nhật Bản)", "qz": "QZSS (Nhật Bản)",
    "un": "GNSS chưa phân loại", "gn": "GNSS chưa phân loại",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context với proper error handling, khởi động MQTT vÃ  Rate Limiter.
    """
    logger.info("ðŸš€ Application starting...")

    # List để track tất cả background tasks
    tasks = []

    try:
        # ===================================================================
        # BƯỚC 1: VERIFY DATABASES (Cleaned up, stable schema assumed)
        # ===================================================================
        try:
            from sqlalchemy import text
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✓ Device database connection verified")
            await ensure_device_runtime_columns(engine)

            async with auth_engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✓ Auth database connection verified")
            await ensure_auth_zalo_runtime_columns(auth_engine)
        except Exception as e:
            logger.critical(f"Database connection check failed: {e}", exc_info=True)
            raise

        # ===================================================================
        # BƯỚC 2: CREATE DEFAULT ADMIN USER
        # ===================================================================
        try:
            async with auth_session_scope() as db_session:
                admin_username = settings.ADMIN_USERNAME.strip()
                admin_password = settings.ADMIN_PASSWORD
                admin_user = await crud.get_user_by_username(db_session, username=admin_username)
                if not admin_user and admin_password:
                    hashed_password = await auth.get_password_hash(admin_password)
                    await crud.create_user(
                        db=db_session,
                        username=admin_username,
                        hashed_password=hashed_password,
                        role=auth.Role.ADMIN,
                        full_name="Administrator"
                    )
                    logger.info("Created bootstrap admin user from environment configuration.")
                elif not admin_user:
                    logger.warning("ADMIN_PASSWORD is unset; skipping bootstrap admin creation.")
        except Exception:
            logger.exception("Failed to create default admin")

        # ===================================================================
        # BƯỚC 2.1: SYNC AUTH SESSION & BLOCKED DEVICE CACHE
        # ===================================================================
        try:
            async with auth_session_scope() as db_session:
                await auth.sync_auth_session_cache(db_session)
                logger.info("✓ Auth session and blocked devices cache initialized")
        except Exception:
            logger.warning("Auth session cache init note", exc_info=True)

        # ===================================================================
        # BƯỚC 2.5: CLEANUP GHOST DEVICES (optional)
        # ===================================================================
        try:
            async with device_session_scope() as db:
                deleted_count = await crud.cleanup_ghost_devices(db)
                if deleted_count > 0:
                    logger.info(f"ðŸ—‘ï¸  Cleaned up {deleted_count} ghost devices on startup")
        except Exception:
            logger.warning("Ghost device cleanup failed (non-critical)", exc_info=True)

        # ===================================================================
        # BƯỚC 3: START MQTT
        # ===================================================================
        mqtt_handler.set_external_command_handler(handle_ntrip_mqtt_command)
        mqtt_handler.set_status_broadcast_handler(enqueue_ui_broadcast)
        mqtt_handler.start_mqtt_loop()

        # ===================================================================
        # BƯỚC 4: START BACKGROUND TASKS
        # ===================================================================

        # Task 1: Check device heartbeats
        async def check_device_heartbeats_with_retry():
            """Heartbeat check với retry vÃ  backoff"""
            from .database import AsyncSessionLocal
            from . import schemas
            from .websocket import manager as ui_manager

            HEARTBEAT_TIMEOUT = 180
            max_retries = 3

            while True:
                await asyncio.sleep(30)

                for attempt in range(max_retries):
                    try:
                        async with device_session_scope() as db:
                            now = int(time.time())
                            timeout_threshold = now - HEARTBEAT_TIMEOUT

                            result = await db.execute(
                                select(models.Device).filter(
                                    models.Device.status.in_([
                                        'online', 'sleep', 'streaming', 'starting',
                                        'initializing', 'configuring', 'rebooting',
                                        'rebooting_for_reset'
                                    ]),
                                    models.Device.timestamp < timeout_threshold
                                )
                            )
                            timed_out_devices = result.scalars().all()

                            if timed_out_devices:
                                accepted_timeouts = []
                                timeout_payloads = {}
                                for device in timed_out_devices:
                                    if await status_reconciler.is_fresh(device.serial, HEARTBEAT_TIMEOUT, now=now):
                                        continue
                                    decision = await status_reconciler.accept_offline(
                                        device.serial,
                                        source="heartbeat",
                                        received_at=now,
                                    )
                                    if not decision.accepted:
                                        continue
                                    device.status = 'offline'
                                    device.offline_since = min(now, int(device.timestamp or now) + HEARTBEAT_TIMEOUT)
                                    device.timestamp = now
                                    device.bps = 0
                                    device.ntrip_connected = False
                                    accepted_timeouts.append(device)
                                    timeout_payloads[device.serial] = decision.payload

                                if accepted_timeouts:
                                    await db.commit()
                                    logger.info(f"Set {len(accepted_timeouts)} devices to offline")

                                # Broadcast updates
                                for device in accepted_timeouts:
                                    broadcast_data = schemas.Device.from_orm(device).model_dump()
                                    broadcast_data.update(timeout_payloads.get(device.serial) or {})
                                    await enqueue_ui_broadcast({
                                        "type": "status_update",
                                        "data": broadcast_data
                                    })

                        break  # Success, thoát retry loop

                    except Exception:
                        logger.exception("Heartbeat check failed (attempt %s/%s)", attempt + 1, max_retries)
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)

        # Task 2: Global rate limiter cleanup (nếu có)
        async def global_rate_limiter_cleanup_task():
            """Dọn dẹp global rate limiter định kỳ"""
            from .monitoring import global_rate_limiter
            while True:
                await asyncio.sleep(300)  # Mỗi 5 phút
                try:
                    global_rate_limiter.cleanup()
                    logger.debug("Global rate limiter cleanup completed")
                except Exception:
                    logger.exception("Global rate limiter cleanup error")

        async def security_rate_limiter_cleanup_task():
            """Dọn dẹp dữ liệu cũ trong security rate limiter"""
            from .security import rate_limiter
            while True:
                await asyncio.sleep(900)  # Mỗi 15 phút
                try:
                    rate_limiter.cleanup_stale_data()
                    logger.debug("Security rate limiter cleanup completed")
                except Exception:
                    logger.exception("Security rate limiter cleanup error")

        # Tạo vÃ  track tất cả tasks
        # Tạo và track tất cả tasks
        tasks.extend([
            asyncio.create_task(check_device_heartbeats_with_retry()),
            asyncio.create_task(global_rate_limiter_cleanup_task()),
            asyncio.create_task(security_rate_limiter_cleanup_task()),
            asyncio.create_task(periodic_buffer_flush()),
            asyncio.create_task(rtcm_session_reaper_task()),
            asyncio.create_task(rtcm_ntrip_clienttable_keepalive_task()),
            asyncio.create_task(ui_broadcast_worker_task()),
            asyncio.create_task(ui_status_broadcast_worker_task()),
            asyncio.create_task(event_loop_watchdog_task()),
            asyncio.create_task(memory_guard_task()),
        ])
        for _ in range(max(1, PI_INGEST_WORKER_COUNT)):
            tasks.append(asyncio.create_task(pi_status_ingest_worker_task()))

        # Start global caster monitor (synchronizes multiple caster servers into local cache)
        tasks.append(asyncio.create_task(caster_monitor_task()))
        tasks.append(asyncio.create_task(esp_base_manager_task()))

        logger.info("=" * 60)
        logger.info("🎉 Application startup complete!")
        logger.info("=" * 60)

        await redis_client.connect()
        from .nginx_security import nginx_security, nginx_security_loop, sync_security_bans
        try:
            await sync_security_bans(restore_application=True)
        except Exception:
            logger.exception("Could not restore persistent IP bans")
        if nginx_security.enabled:
            tasks.append(asyncio.create_task(nginx_security_loop()))

        # --- ZALO BOT INTEGRATION ---
        def _configure_zalo_bots():
            from .zalo_bot_service import zalo_bot_services

            async def _zalo_get_devices():
                from .database import device_session_scope, auth_session_scope
                from . import crud
                async with device_session_scope() as devices_db, auth_session_scope() as auth_db:
                    user = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                    if not user:
                        return []
                    devices = await _ops_visible_devices(devices_db, auth_db, user)
                    return [{
                        "serial": d.serial,
                        "name": d.name,
                        "status": getattr(d, "status", "unknown"),
                        "latest_throughput_bps": getattr(d, "latest_throughput_bps", 0),
                        "latitude": getattr(d, "latitude", 0),
                        "longitude": getattr(d, "longitude", 0),
                        "mountpoint": getattr(d, "mountpoint", "")
                    } for d in devices]

            async def _zalo_get_alerts():
                from .database import device_session_scope, auth_session_scope
                from . import crud
                async with auth_session_scope() as auth_db, device_session_scope() as devices_db:
                    user = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                    if not user:
                        return []
                    alerts = await _build_ops_alerts(auth_db, devices_db, user)
                    return alerts # it's already a list of dicts

            def _zalo_parse_scalar(value: Any) -> Any:
                text_value = str(value).strip()
                lowered = text_value.lower()
                if lowered in {"true", "yes", "on", "1"}:
                    return True
                if lowered in {"false", "no", "off", "0"}:
                    return False
                if lowered in {"none", "null"}:
                    return None
                if re.fullmatch(r"-?\d+", text_value):
                    return int(text_value)
                if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", text_value):
                    return float(text_value)
                return text_value

            async def _zalo_find_device(
                devices_db: AsyncSession,
                identifier: str,
                devices_list: Optional[list[models.Device]] = None,
            ):
                wanted = str(identifier or "").strip().lower()
                if not wanted:
                    raise ValueError("Bạn chưa nhập tên hoặc serial trạm. Ví dụ: /station HCM-01")
                if devices_list is None:
                    result = await devices_db.execute(select(models.Device))
                    devices_list = result.scalars().all()
                exact = [
                    device for device in devices_list
                    if wanted in {
                        str(device.name or "").strip().lower(),
                        str(device.serial or "").strip().lower(),
                    }
                ]
                if len(exact) == 1:
                    return exact[0]
                partial = [
                    device for device in devices_list
                    if wanted in str(device.name or "").lower()
                    or wanted in str(device.serial or "").lower()
                ]
                if not partial:
                    label_lookup = {
                        str(device.name or device.serial or "").strip().lower():
                        str(device.name or device.serial or "").strip()
                        for device in devices_list
                        if device.name or device.serial
                    }
                    suggestion_keys = difflib.get_close_matches(
                        wanted, list(label_lookup), n=5, cutoff=0.35
                    )
                    suggestions = [label_lookup[key] for key in suggestion_keys]
                    if suggestions:
                        raise ValueError(
                            f"Không tìm thấy trạm '{identifier}'. Có phải bạn muốn: "
                            + ", ".join(suggestions)
                            + "?"
                        )
                    raise ValueError(
                        f"Không tìm thấy trạm '{identifier}'. Gõ /stations để xem danh sách trạm."
                    )
                if len(partial) > 1:
                    choices = ", ".join(str(device.name or "Chưa đặt tên") for device in partial[:8])
                    raise ValueError(f"Có nhiều trạm khớp '{identifier}': {choices}")
                return partial[0]

            async def _zalo_find_rover(
                auth_db: AsyncSession,
                devices_db: AsyncSession,
                identifier: str,
                allow_partial: bool = False,
                rover_items: Optional[list[models.RoverAccount]] = None,
                device_items: Optional[list[models.Device]] = None,
            ):
                wanted = str(identifier or "").strip().lower()
                if not wanted:
                    raise ValueError("Bạn chưa nhập username rover. Ví dụ: /rover rover01")
                if rover_items is None:
                    rover_rs = await auth_db.execute(select(models.RoverAccount))
                    rover_items = rover_rs.scalars().all()
                exact = [item for item in rover_items if str(item.username or "").strip().lower() == wanted]
                if len(exact) == 1:
                    return exact[0]

                partial = [item for item in rover_items if wanted in str(item.username or "").lower()]
                if allow_partial and len(partial) == 1:
                    return partial[0]
                if len(partial) > 1:
                    choices = ", ".join(str(item.username) for item in partial[:8])
                    raise ValueError(f"Có nhiều rover khớp '{identifier}': {choices}")

                # A common human mistake is entering a station name while the
                # bot is waiting for a rover username. Detect that explicitly.
                if device_items is None:
                    device_rs = await devices_db.execute(select(models.Device))
                    device_items = device_rs.scalars().all()
                matching_stations = [
                    item for item in device_items
                    if wanted in {
                        str(item.name or "").strip().lower(),
                        str(item.serial or "").strip().lower(),
                    }
                    or wanted in str(item.name or "").lower()
                    or wanted in str(item.serial or "").lower()
                ]
                if matching_stations:
                    station = matching_stations[0]
                    station_name = str(station.name or station.serial)
                    raise ValueError(
                        f"'{identifier}' là TRẠM, không phải rover. "
                        f"Để xem trạm, dùng /station {station_name}"
                    )

                username_lookup = {
                    str(item.username or "").strip().lower(): str(item.username or "").strip()
                    for item in rover_items if item.username
                }
                suggestion_keys = difflib.get_close_matches(wanted, list(username_lookup), n=5, cutoff=0.35)
                suggestions = [username_lookup[key] for key in suggestion_keys]
                if suggestions:
                    raise ValueError(
                        f"Không tìm thấy rover '{identifier}'. Có phải bạn muốn: "
                        + ", ".join(suggestions)
                        + "?"
                    )
                raise ValueError(
                    f"Không tìm thấy rover '{identifier}'. Gõ /rovers để xem danh sách rover."
                )

            def _zalo_safe_config(config: Any) -> dict:
                if not isinstance(config, dict):
                    return {}
                return {
                    str(key): ("<redacted>" if "password" in str(key).lower() else value)
                    for key, value in config.items()
                }

            def _zalo_format_speed(value: Any) -> str:
                try:
                    speed = max(0.0, float(value or 0))
                except (TypeError, ValueError):
                    speed = 0.0
                if speed < 1024:
                    return f"{speed:.0f} B/s"
                if speed < 1024 * 1024:
                    number = speed / 1024
                    return f"{number:.1f} KB/s" if number < 10 else f"{number:.0f} KB/s"
                return f"{speed / 1024 / 1024:.2f} MB/s"

            def _zalo_yes_no(value: Any, yes: str = "Có", no: str = "Không") -> str:
                return yes if bool(value) else no

            def _zalo_stream_mode(value: Any) -> str:
                return "Ngủ khi không có rover" if bool(value) else "Luôn truyền dữ liệu"

            def _zalo_device_traffic(device: models.Device, snapshot: dict) -> dict:
                stats = snapshot.get("ntrip_stats") if isinstance(snapshot.get("ntrip_stats"), dict) else {}
                status = {}
                if isinstance(device.ntrip_status, dict):
                    status.update(device.ntrip_status)
                if isinstance(snapshot.get("ntrip_status"), dict):
                    status.update(snapshot["ntrip_status"])

                def server_bps(server_id: int) -> int:
                    detail = status.get(f"server{server_id}")
                    detail_bps = detail.get("bps") if isinstance(detail, dict) else 0
                    try:
                        return int(detail_bps or stats.get(f"server{server_id}_bps") or 0)
                    except (TypeError, ValueError):
                        return 0

                server1 = server_bps(1)
                server2 = server_bps(2)
                try:
                    total = int(server1 + server2 or snapshot.get("bps") or device.bps or 0)
                except (TypeError, ValueError):
                    total = server1 + server2
                try:
                    rtcm_input = int(
                        stats.get("rtcm_input_bps") or stats.get("rtcm_in_bps")
                        or snapshot.get("rtcm_input_bps") or 0
                    )
                except (TypeError, ValueError):
                    rtcm_input = 0
                return {"server1": server1, "server2": server2, "total": total, "rtcm_input": rtcm_input}

            def _zalo_traffic_lines(device: models.Device, snapshot: dict) -> List[str]:
                traffic = _zalo_device_traffic(device, snapshot)
                lines = [
                    "📈 DÒNG DỮ LIỆU HIỆN TẠI",
                    f"• Tổng dữ liệu gửi đi: {_zalo_format_speed(traffic['total'])}",
                    f"• Server 1: {_zalo_format_speed(traffic['server1'])}",
                    f"• Server 2: {_zalo_format_speed(traffic['server2'])}",
                ]
                if traffic["rtcm_input"]:
                    lines.append(f"• RTCM nhận vào: {_zalo_format_speed(traffic['rtcm_input'])}")
                return lines

            def _zalo_config_summary(device: models.Device, snapshot: dict) -> str:
                base = device.base_config if isinstance(device.base_config, dict) else {}
                service = device.service_config if isinstance(device.service_config, dict) else {}
                coords = base.get("coords") if isinstance(base.get("coords"), dict) else {}
                gnss = base.get("gnss_options") if isinstance(base.get("gnss_options"), dict) else {}
                constellations = gnss.get("constellations") if isinstance(gnss.get("constellations"), dict) else {}
                constellation_labels = {
                    "gps": "GPS", "glo": "GLONASS", "gal": "Galileo",
                    "bds": "BeiDou", "qzss": "QZSS", "sbas": "SBAS",
                }
                enabled_constellations = [
                    label for key, label in constellation_labels.items() if constellations.get(key)
                ]
                method_labels = {
                    "FIXED_LLA": "Tọa độ cố định Lat/Lon/Alt",
                    "SURVEY_IN": "Tự khảo sát vị trí",
                }
                output_labels = {
                    "rtcm_only": "Chỉ xuất RTCM", "nmea_only": "Chỉ xuất NMEA",
                    "rtcm_nmea": "RTCM và NMEA",
                }
                traffic = _zalo_device_traffic(device, snapshot)
                lines = [
                    f"⚙️ CẤU HÌNH TRẠM {device.name or device.serial}",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    "📍 VỊ TRÍ BASE",
                    f"• Chế độ: {method_labels.get(str(base.get('base_setup_method') or ''), base.get('base_setup_method') or 'Chưa cấu hình')}",
                    f"• Vĩ độ: {coords.get('lat', 'Chưa có')}",
                    f"• Kinh độ: {coords.get('lon', 'Chưa có')}",
                    f"• Độ cao ellipsoid: {str(coords.get('alt')) + ' m' if coords.get('alt') not in (None, '') else 'Chưa có'}",
                    f"• Độ chính xác yêu cầu: {str(base.get('accuracy')) + ' m' if base.get('accuracy') not in (None, '') else 'Chưa đặt'}",
                    "",
                    "🛰️ GNSS",
                    f"• Cổng nhận dữ liệu: {', '.join(str(item) for item in gnss.get('ports', []) if item) or 'Chưa cấu hình'}",
                    f"• Dữ liệu đầu ra: {output_labels.get(str(gnss.get('output_mode') or ''), gnss.get('output_mode') or 'Chưa cấu hình')}",
                    f"• Hệ vệ tinh bật: {', '.join(enabled_constellations) or 'Chưa chọn'}",
                ]

                for server_id in (1, 2):
                    enabled = bool(service.get(f"server{server_id}_enabled"))
                    host = service.get(f"serverhost{server_id}") or "Chưa cấu hình"
                    port = service.get(f"port{server_id}") or "--"
                    mountpoint = service.get(f"mountpoint{server_id}") or "Chưa cấu hình"
                    username = service.get(f"username{server_id}") or "Không dùng"
                    ntrip_version = service.get(f"ntrip_version{server_id}") or "--"
                    on_demand = service.get(
                        f"server{server_id}_stream_on_demand", service.get("stream_on_demand", False)
                    )
                    active = bool(service.get(f"server{server_id}_stream_active")) or traffic[f"server{server_id}"] > 0
                    lines.extend((
                        "",
                        f"🔗 SERVER {server_id}",
                        f"• Kích hoạt: {_zalo_yes_no(enabled, 'Đang bật', 'Đang tắt')}",
                        f"• Máy chủ: {host}:{port}",
                        f"• Mountpoint: {mountpoint}",
                        f"• Tài khoản: {username}",
                        f"• Mật khẩu: {'Đã thiết lập' if service.get(f'password{server_id}') else 'Chưa thiết lập'}",
                        f"• Phiên bản NTRIP: {ntrip_version}",
                        f"• Chế độ truyền: {_zalo_stream_mode(on_demand)}",
                        f"• Trạng thái dòng: {_zalo_yes_no(active, 'Đang truyền', 'Chưa truyền')}",
                        f"• Tốc độ hiện tại: {_zalo_format_speed(traffic[f'server{server_id}'])}",
                    ))

                lines.extend((
                    "",
                    "📥 NGUỒN RTCM ĐẦU VÀO",
                    f"• Kích hoạt: {_zalo_yes_no(service.get('rtcm_enabled'), 'Đang bật', 'Đang tắt')}",
                    f"• Máy chủ: {service.get('rtcmserver1') or 'Chưa cấu hình'}:{service.get('rtcmport1') or '--'}",
                    f"• Mountpoint: {service.get('rtcmmountpoint1') or 'Chưa cấu hình'}",
                    f"• Tài khoản: {service.get('rtcmusername1') or 'Không dùng'}",
                    f"• Mật khẩu: {'Đã thiết lập' if service.get('rtcmpassword1') else 'Chưa thiết lập'}",
                    f"• Phiên bản NTRIP: {service.get('rtcmntripversion1') or '--'}",
                    f"• Tốc độ nhận: {_zalo_format_speed(traffic['rtcm_input'])}",
                    "",
                    "🔄 KẾT NỐI",
                    f"• Thử kết nối lại sau: {service.get('reconnectioninterval', 10)} giây",
                    f"• Tổng dữ liệu gửi đi: {_zalo_format_speed(traffic['total'])}",
                ))
                return "\n".join(lines)

            def _zalo_live_telemetry(serial: str) -> dict:
                serial_text = str(serial)

                def cache_value(cache: dict) -> dict:
                    direct = cache.get(serial_text)
                    if isinstance(direct, dict):
                        return direct
                    wanted = serial_text.casefold()
                    for key, value in cache.items():
                        if str(key).casefold() == wanted and isinstance(value, dict):
                            return value
                    return {}

                snapshot: dict = {}
                merged_nmea: dict = {}
                for source in (
                    cache_value(mqtt_handler.device_telemetry_cache),
                    cache_value(latest_device_telemetry_cache),
                ):
                    source_nmea = source.get("nmea_data")
                    if isinstance(source_nmea, dict):
                        for key, value in source_nmea.items():
                            if key == "satellites" and not value and merged_nmea.get("satellites"):
                                continue
                            merged_nmea[key] = value
                    snapshot.update({key: value for key, value in source.items() if key != "nmea_data"})
                if merged_nmea:
                    snapshot["nmea_data"] = merged_nmea
                return snapshot

            def _zalo_search_key(value: Any) -> str:
                raw = str(value or "").lower().replace("đ", "d")
                normalized = unicodedata.normalize("NFD", raw)
                ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
                return "".join(ch for ch in ascii_text if ch.isalnum())

            def _zalo_search_needles(query: Any) -> Set[str]:
                raw = str(query or "").strip().lower().replace("đ", "d")
                normalized = unicodedata.normalize("NFD", raw)
                ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
                stop_words = {
                    "xem", "tim", "kiem", "tra", "cuu", "giup", "toi", "cho", "minh",
                    "thong", "tin", "chi", "tiet", "tram", "rover", "tai", "khoan",
                    # Bot command names — must not pollute search needles
                    "station", "stations", "stationdelete", "stationlock",
                    "stationunlock", "stationreset", "config", "configset",
                    "coord", "rovers", "rovercreate", "roverupdate",
                    "roverdelete", "roverrenew", "roverblock", "roverunblock",
                    "status", "errors", "menu", "help", "start", "confirm",
                    "cancel", "logout", "unlink", "link", "register",
                    "notifications", "overview",
                    # Vietnamese action words that are not station/rover identifiers
                    "xoa", "khoa", "mo", "reset", "sua", "dat", "tao",
                    "them", "chan", "gia", "han", "huy", "xac", "nhan",
                    "danh", "sach", "loi", "canh", "bao", "delete", "lock",
                    "unlock", "block", "unblock", "create", "update", "renew",
                    "info", "list", "view", "check",
                }
                tokens = [
                    token for token in re.findall(r"[a-z0-9_.-]+", ascii_text)
                    if token not in stop_words
                ]
                needles = {_zalo_search_key(raw)}
                needles.update(_zalo_search_key(token) for token in tokens)
                return {needle for needle in needles if len(needle) >= 2}

            def _zalo_satellite_summary(snapshot: dict) -> List[str]:
                nmea = snapshot.get("nmea_data") if isinstance(snapshot.get("nmea_data"), dict) else {}
                rtcm = snapshot.get("rtcm_signal_data") if isinstance(snapshot.get("rtcm_signal_data"), dict) else {}
                rtcm_satellites = rtcm.get("satellites") if isinstance(rtcm.get("satellites"), (list, dict)) else []
                now_ms = int(time.time() * 1000)
                if isinstance(rtcm_satellites, dict):
                    rtcm_satellites = list(rtcm_satellites.values())
                fresh_rtcm_satellites = []
                for item in rtcm_satellites:
                    if not isinstance(item, dict):
                        continue
                    try:
                        last_seen = int(item.get("lastSeen") or rtcm.get("updated_at_ms") or 0)
                    except (TypeError, ValueError):
                        last_seen = 0
                    if not last_seen or now_ms - last_seen <= 15_000:
                        fresh_rtcm_satellites.append(item)
                satellites = fresh_rtcm_satellites or nmea.get("satellites") or snapshot.get("satellites") or []
                if isinstance(satellites, dict):
                    satellites = list(satellites.values())
                if not isinstance(satellites, list):
                    satellites = []

                grouped: Dict[str, Set[str]] = {}
                active_satellites = []
                for index, sat in enumerate(satellites):
                    if not isinstance(sat, dict):
                        continue
                    try:
                        snr = float(sat.get("snr") or 0)
                    except (TypeError, ValueError):
                        snr = 0
                    if "snr" in sat and snr <= 0:
                        continue
                    active_satellites.append(sat)
                    raw_system = str(
                        sat.get("system") or sat.get("navSystem") or sat.get("talker") or "un"
                    ).strip().lower()
                    label = GNSS_SYSTEM_NAMES.get(raw_system, raw_system.upper() or GNSS_SYSTEM_NAMES["un"])
                    sat_key = str(sat.get("id") or sat.get("prn") or index)
                    grouped.setdefault(label, set()).add(sat_key)

                gga = nmea.get("gga") if isinstance(nmea.get("gga"), dict) else {}
                used_count = gga.get("satellites")
                tracking_count = sum(1 for sat in active_satellites if sat.get("isTracking"))
                if fresh_rtcm_satellites or used_count in (None, ""):
                    used_count = tracking_count
                total_count = sum(len(items) for items in grouped.values())
                if not total_count:
                    rtcm_is_fresh = bool(fresh_rtcm_satellites) or not rtcm
                    total_count = int((snapshot.get("satellite_count") if rtcm_is_fresh else 0) or used_count or 0)
                if not total_count:
                    return ["🛰️ Vệ tinh: chưa nhận được dữ liệu trực tiếp."]

                lines = [
                    "🛰️ VỆ TINH HIỆN TẠI",
                    f"• Đang thu tín hiệu: {total_count} vệ tinh",
                    f"• Đang dùng để định vị: {int(used_count or 0)} vệ tinh",
                ]
                for label in (
                    "GPS (Mỹ)", "GLONASS (Nga)", "Galileo (Châu Âu)",
                    "BeiDou (Trung Quốc)", "QZSS (Nhật Bản)",
                ):
                    lines.append(f"• {label}: {len(grouped.get(label, set()))}")
                if grouped.get("GNSS chưa phân loại"):
                    lines.append(f"• GNSS chưa phân loại: {len(grouped['GNSS chưa phân loại'])}")
                for label in sorted(set(grouped) - {
                    "GPS (Mỹ)", "GLONASS (Nga)", "Galileo (Châu Âu)",
                    "BeiDou (Trung Quốc)", "QZSS (Nhật Bản)", "GNSS chưa phân loại",
                }):
                    lines.append(f"• {label}: {len(grouped[label])}")
                return lines

            zalo_action_permissions = {
                "overview": auth.Permission.VIEW_DEVICES,
                "smart_search": auth.Permission.VIEW_DEVICES,
                "stations": auth.Permission.VIEW_DEVICES,
                "station": auth.Permission.VIEW_DEVICES,
                "alerts": auth.Permission.VIEW_CONFIG,
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

            zalo_write_actions = {
                "config_edit_context", "coord_set", "config_set", "station_lock", "station_unlock",
                "station_reset", "station_delete", "rover_edit_context", "rover_create",
                "rover_update", "rover_delete", "rover_renew", "rover_block", "rover_unblock",
            }

            async def _zalo_admin_action(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
                from .routers import devices as device_routes

                required_permission = zalo_action_permissions.get(action)
                granted_permissions = {
                    str(value) for value in (payload.get("permissions") or [])
                }
                if required_permission and required_permission not in granted_permissions:
                    raise PermissionError("Tài khoản Zalo hiện tại không còn quyền thực hiện chức năng này")
                if action in zalo_write_actions and not settings.ZALO_ENABLE_WRITE_ACTIONS:
                    raise PermissionError("Các thao tác ghi từ Zalo đang bị tắt bởi cấu hình hệ thống")

                async with auth_session_scope() as auth_db:
                    service_admin = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                    if not service_admin or service_admin.role != auth.Role.ADMIN:
                        raise RuntimeError("ZALO_BOT_ADMIN_USERNAME phải trỏ tới tài khoản web có role admin")

                    async with device_session_scope() as devices_db:
                        try:
                            actor_user_id = payload.get("actor_user_id")
                            actor_user = await auth_db.get(models.User, int(actor_user_id)) if actor_user_id else None
                            if actor_user_id and (not actor_user or not actor_user.is_active):
                                raise PermissionError("Tài khoản dashboard liên kết đã bị vô hiệu hóa")
                            execution_user = actor_user or service_admin
                            fresh_permissions = set(auth.get_user_permissions(execution_user))
                            if required_permission and required_permission not in fresh_permissions:
                                raise PermissionError("Tài khoản dashboard hiện tại không còn quyền thực hiện chức năng này")

                            visible_devices = await _ops_visible_devices(devices_db, auth_db, execution_user)
                            visible_rovers = await _ops_visible_rovers(auth_db, devices_db, execution_user)
                            can_view_rovers = auth.Permission.MANAGE_CASTER in granted_permissions
                            if not actor_user:
                                scope_mode = str(payload.get("station_scope_mode") or "all")
                                allowed_serials = {
                                    str(value) for value in (payload.get("allowed_bases") or []) if str(value).strip()
                                }
                                if scope_mode == "none":
                                    visible_devices = []
                                elif scope_mode == "selected":
                                    visible_devices = [
                                        item for item in visible_devices if str(item.serial) in allowed_serials
                                    ]
                                if scope_mode in {"none", "selected"}:
                                    scoped_serials = {str(item.serial) for item in visible_devices}
                                    scoped_mountpoints = set()
                                    for item in visible_devices:
                                        config = item.service_config if isinstance(item.service_config, dict) else {}
                                        scoped_mountpoints.update(
                                            str(config.get(key)).strip() for key in ("mountpoint1", "mountpoint2")
                                            if str(config.get(key) or "").strip()
                                        )
                                    visible_rovers = [
                                        rover for rover in visible_rovers
                                        if str(rover.base_serial or "") in scoped_serials
                                        or bool({
                                            part.strip() for part in re.split(r"[;,]+", str(rover.mountpoints or "")) if part.strip()
                                        }.intersection(scoped_mountpoints))
                                    ]
                            if not can_view_rovers:
                                visible_rovers = []

                            if action == "overview":
                                online = sum(
                                    1 for item in visible_devices
                                    if str(item.status or "").lower() not in {"offline", "unknown"}
                                )
                                offline = len(visible_devices) - online
                                total_bps = sum(
                                    _zalo_device_traffic(item, _zalo_live_telemetry(item.serial))["total"]
                                    for item in visible_devices
                                )
                                overview_lines = [
                                    "📊 TỔNG QUAN HỆ THỐNG CORS",
                                    "━━━━━━━━━━━━━━━━━━━━━━",
                                    f"🟢 Online: {online}/{len(visible_devices)}",
                                    f"🔴 Offline: {offline}/{len(visible_devices)}",
                                ]
                                if can_view_rovers:
                                    overview_lines.append(f"🛰️ Rover được xem: {len(visible_rovers)}")
                                overview_lines.append(
                                    f"📈 Tổng dữ liệu gửi đi: {_zalo_format_speed(total_bps)}"
                                )
                                return {"message": "\n".join(overview_lines)}

                            if action == "alerts":
                                alert_items = await _build_ops_alerts(
                                    auth_db, devices_db, execution_user,
                                    devices=visible_devices, rovers=visible_rovers,
                                )
                                if not alert_items:
                                    return {"message": "✅ Không có cảnh báo trong phạm vi bạn được xem."}
                                lines = [f"⚠️ CẢNH BÁO ({len(alert_items)})"]
                                for item in alert_items[:30]:
                                    subject = item.get("rover_username") or item.get("device_name") or item.get("device_serial") or "Hệ thống"
                                    lines.append(f"• {subject}: {item.get('title') or item.get('message') or 'Cảnh báo'}")
                                return {"message": "\n".join(lines)}

                            if action == "smart_search":
                                query = str(payload.get("query") or "").strip()
                                needles = _zalo_search_needles(query)
                                search_devices = visible_devices
                                search_rovers = visible_rovers

                                # --- Scoring-based smart search ---
                                device_scores = []
                                for item in search_devices:
                                    score = 0
                                    name_key = _zalo_search_key(item.name)
                                    serial_key = _zalo_search_key(item.serial)
                                    for needle in needles:
                                        # Exact match on name or serial
                                        if needle == name_key:
                                            score += 10
                                        if needle == serial_key:
                                            score += 10
                                        # IMEI / long serial bonus
                                        if len(needle) >= 10 and needle.isdigit() and needle == serial_key:
                                            score += 20
                                        # Partial (substring) match
                                        if len(needle) >= 3:
                                            if needle != name_key and name_key and needle in name_key:
                                                score += 3
                                            if needle != serial_key and serial_key and needle in serial_key:
                                                score += 3
                                    if score > 0:
                                        device_scores.append((item, score))

                                rover_scores = []
                                for item in search_rovers:
                                    score = 0
                                    username_key = _zalo_search_key(item.username)
                                    for needle in needles:
                                        if needle == username_key:
                                            score += 10
                                        if len(needle) >= 10 and needle.isdigit() and needle == username_key:
                                            score += 20
                                        if len(needle) >= 3 and needle != username_key and username_key and needle in username_key:
                                            score += 3
                                    if score > 0:
                                        rover_scores.append((item, score))

                                device_scores.sort(key=lambda x: -x[1])
                                rover_scores.sort(key=lambda x: -x[1])

                                top_device = device_scores[0] if device_scores else None
                                top_rover = rover_scores[0] if rover_scores else None
                                second_device_score = device_scores[1][1] if len(device_scores) > 1 else -1
                                second_rover_score = rover_scores[1][1] if len(rover_scores) > 1 else -1

                                best_device_score = top_device[1] if top_device else -1
                                best_rover_score = top_rover[1] if top_rover else -1

                                winner = None  # ("station"|"rover", item)
                                if best_device_score > 0 or best_rover_score > 0:
                                    if best_device_score > best_rover_score:
                                        # Device is stronger — check if it's clearly the best device
                                        if best_device_score > second_device_score:
                                            winner = ("station", top_device[0])
                                    elif best_rover_score > best_device_score:
                                        if best_rover_score > second_rover_score:
                                            winner = ("rover", top_rover[0])
                                    else:
                                        # Tie between best device and best rover
                                        if best_device_score > second_device_score and best_rover_score <= second_rover_score:
                                            winner = ("station", top_device[0])
                                        elif best_rover_score > second_rover_score and best_device_score <= second_device_score:
                                            winner = ("rover", top_rover[0])

                                if winner:
                                    if winner[0] == "station":
                                        action = "station"
                                        payload["station"] = str(winner[1].serial)
                                    else:
                                        action = "rover"
                                        payload["username"] = str(winner[1].username)
                                elif device_scores or rover_scores:
                                    choices = [
                                        f"trạm {item.name or item.serial}" for item, _ in device_scores[:5]
                                    ] + [f"rover {item.username}" for item, _ in rover_scores[:5]]
                                    return {
                                        "message": "Mình tìm thấy nhiều kết quả: " + ", ".join(choices)
                                        + ". Hãy gửi rõ tên cần xem."
                                    }
                                else:
                                    # No scored matches — try fuzzy (difflib)
                                    label_lookup = {}
                                    for item in search_devices:
                                        key = _zalo_search_key(item.name or item.serial)
                                        if key:
                                            label_lookup[key] = f"trạm {item.name or item.serial}"
                                    for item in search_rovers:
                                        key = _zalo_search_key(item.username)
                                        if key:
                                            label_lookup[key] = f"rover {item.username}"
                                    query_key = _zalo_search_key(query)
                                    suggestion_keys = difflib.get_close_matches(
                                        query_key, list(label_lookup), n=5, cutoff=0.35
                                    )
                                    if suggestion_keys:
                                        suggestions = [label_lookup[k] for k in suggestion_keys]
                                        return {
                                            "message": f"Không tìm thấy kết quả cho '{query}'. Có phải bạn muốn: "
                                            + ", ".join(suggestions) + "?"
                                        }
                                    subject_label = "trạm hoặc rover" if can_view_rovers else "trạm"
                                    command_hint = "/stations hoặc /rovers" if can_view_rovers else "/stations"
                                    return {
                                        "message": f"Không tìm thấy {subject_label} phù hợp với '{query}'. "
                                        f"Gõ {command_hint} để xem danh sách."
                                    }

                            if action == "stations":
                                query = str(payload.get("query") or "").strip().lower()
                                station_items = list(visible_devices)
                                if query:
                                    station_items = [
                                        item for item in station_items
                                        if query in str(item.name or "").lower()
                                    ]
                                station_items = sorted(station_items, key=lambda item: str(item.name or item.serial))
                                if not station_items:
                                    return {"message": "Không tìm thấy trạm."}
                                total_matches = len(station_items)
                                lines = [f"📡 TRẠM ({total_matches})"]
                                for item in station_items[:30]:
                                    icon = "🔴" if str(item.status).lower() in {"offline", "unknown"} else "🟢"
                                    speed = _zalo_device_traffic(
                                        item, _zalo_live_telemetry(item.serial)
                                    )["total"]
                                    lines.append(
                                        f"{icon} {item.name or 'Chưa đặt tên'} | {item.status} | {_zalo_format_speed(speed)}"
                                    )
                                if total_matches > 30:
                                    lines.append("… Gõ /stations <từ khóa tên> để lọc danh sách.")
                                return {"message": "\n".join(lines)}

                            if action in {
                                "station", "config", "config_edit_context", "coord_set", "config_set", "rover_create",
                                "station_lock", "station_unlock", "station_reset", "station_delete",
                            }:
                                device = await _zalo_find_device(
                                    devices_db, payload.get("station"), devices_list=visible_devices
                                )
                            else:
                                device = None

                            if action == "config_edit_context":
                                return {
                                    "station": str(device.name or device.serial),
                                    "label": str(device.name or device.serial),
                                    "current": _zalo_safe_config(device.service_config),
                                }

                            if action == "station":
                                base_cfg = _zalo_safe_config(device.base_config)
                                service_cfg = _zalo_safe_config(device.service_config)
                                coords = base_cfg.get("coords") or getattr(device, "base_coords", None) or {}
                                telemetry = _zalo_live_telemetry(device.serial)
                                nmea = telemetry.get("nmea_data") if isinstance(telemetry.get("nmea_data"), dict) else {}
                                gga = nmea.get("gga") if isinstance(nmea.get("gga"), dict) else {}
                                lat = lon = alt = None
                                if isinstance(coords, dict):
                                    lat = coords.get("lat", coords.get("latitude"))
                                    lon = coords.get("lon", coords.get("longitude"))
                                    alt = coords.get("alt", coords.get("altitude"))
                                lat = lat if lat is not None else gga.get("latitude")
                                lon = lon if lon is not None else gga.get("longitude")
                                alt = alt if alt is not None else gga.get("altitude")
                                status_text = {
                                    "online": "Trực tuyến", "offline": "Ngoại tuyến", "sleep": "Đang nghỉ",
                                    "unknown": "Chưa xác định", "awaiting_license": "Chờ cấp phép",
                                }.get(str(device.status or "").lower(), str(device.status or "Chưa xác định"))
                                lines = [
                                    f"📡 TRẠM {device.name or '-'}",
                                    f"• Serial: {device.serial}",
                                    f"• Trạng thái: {status_text}",
                                    f"• Chip GNSS: {device.detected_chip_type or 'Chưa xác định'}",
                                    f"• Đồng bộ cấu hình: {'Có' if getattr(device, 'is_synced', False) else 'Không'}",
                                    "",
                                    *_zalo_traffic_lines(device, telemetry),
                                    "",
                                    "📍 TỌA ĐỘ",
                                    f"• Vĩ độ (Lat): {lat if lat is not None else 'Chưa có'}",
                                    f"• Kinh độ (Lon): {lon if lon is not None else 'Chưa có'}",
                                    f"• Độ cao ellipsoid (Alt): {str(alt) + ' m' if alt is not None else 'Chưa có'}",
                                    "",
                                    *_zalo_satellite_summary(telemetry),
                                    "",
                                    "🔗 MOUNTPOINT",
                                    f"• Server 1: {service_cfg.get('mountpoint1') or 'Chưa cấu hình'}",
                                    f"• Server 2: {service_cfg.get('mountpoint2') or 'Chưa cấu hình'}",
                                ]
                                if str(device.status or "").lower() in {"offline", "unknown"}:
                                    lines.append("ℹ️ Trạm đang ngoại tuyến; số vệ tinh có thể là dữ liệu gần nhất.")
                                return {
                                    "message": "\n".join(lines),
                                    "context_target": str(device.serial),
                                    "context_type": "station",
                                    "context_name": str(device.name or device.serial),
                                }

                            if action == "config":
                                return {
                                    "message": _zalo_config_summary(device, _zalo_live_telemetry(device.serial)),
                                    "context_target": str(device.serial),
                                    "context_type": "station",
                                    "context_name": str(device.name or device.serial),
                                }

                            if action == "coord_set":
                                sensor_type = str(device.detected_chip_type or "").strip()
                                if not sensor_type or sensor_type.upper() == "UNKNOWN":
                                    raise ValueError("Trạm chưa xác định loại chip GNSS")
                                params = dict(device.base_config or {})
                                params.update({
                                    "base_setup_method": "FIXED_LLA",
                                    "coords": {
                                        "lat": float(payload["lat"]),
                                        "lon": float(payload["lon"]),
                                        "alt": float(payload["alt"]),
                                    },
                                    "accuracy": float(payload.get("accuracy", 0.01)),
                                })
                                command = schemas.Command(
                                    command="CONFIGURE_CHIP",
                                    payload={"sensor_type": sensor_type, "mode": "BASE", "params": params},
                                )
                                result = await configure_chip_endpoint(device.serial, command, devices_db, execution_user)

                                from app.mqtt import device_sync_cache, _compare_configs
                                is_synced = False
                                wait_time = 0
                                is_relay = device.serial.startswith(("RELAY_", "MONITOR_"))
                                while wait_time < 12:
                                    await asyncio.sleep(1)
                                    wait_time += 1
                                    await devices_db.refresh(device)
                                    agent_base = device_sync_cache.get(f"{device.serial}_base_config", {})
                                    db_base = device.base_config or {}
                                    base_ok, _ = _compare_configs(agent_base, db_base, "Base", is_relay=is_relay)
                                    if base_ok:
                                        is_synced = True
                                        break

                                if is_synced:
                                    return {"message": f"✅ Đã cập nhật tọa độ cho trạm {device.name or device.serial} và đồng bộ thành công!"}
                                else:
                                    return {"message": f"✅ Đã gửi lệnh cập nhật tọa độ tới {device.name or device.serial}, đang chờ trạm đồng bộ."}

                            if action == "config_set":
                                changes = dict(payload.get("changes") or {})
                                allowed_fields = set(schemas.ServiceConfig.model_fields)
                                unknown = sorted(set(changes) - allowed_fields)
                                if unknown:
                                    raise ValueError(f"Config key không hợp lệ: {', '.join(unknown)}")
                                merged = dict(_device_service_config(device))
                                merged.update({key: _zalo_parse_scalar(value) for key, value in changes.items()})
                                service_payload = schemas.ServiceConfig(**merged)
                                result = await configure_service_endpoint(device.serial, service_payload, devices_db, execution_user)

                                from app.mqtt import device_sync_cache, _compare_configs
                                is_synced = False
                                wait_time = 0
                                is_relay = device.serial.startswith(("RELAY_", "MONITOR_"))
                                while wait_time < 12:
                                    await asyncio.sleep(1)
                                    wait_time += 1
                                    await devices_db.refresh(device)
                                    agent_service = device_sync_cache.get(f"{device.serial}_service_config", {})
                                    db_service = _device_service_config(device)
                                    srv_ok, _ = _compare_configs(agent_service, db_service, "Service", is_relay=is_relay)
                                    if srv_ok:
                                        is_synced = True
                                        break

                                if is_synced:
                                    return {"message": f"✅ Đã cập nhật cấu hình cho trạm {device.name or device.serial} và đồng bộ thành công!"}
                                else:
                                    return {"message": f"✅ Đã gửi lệnh cập nhật cấu hình tới {device.name or device.serial}, đang chờ trạm đồng bộ."}

                            if action == "station_lock":
                                result = await lock_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã khóa cấu hình host của trạm {device.name or device.serial} thành công (Đang chờ trạm đồng bộ)."}

                            if action == "station_unlock":
                                result = await unlock_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã mở khóa cấu hình host của trạm {device.name or device.serial} thành công (Đang chờ trạm đồng bộ)."}

                            if action == "station_reset":
                                result = await reset_pi_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã gửi lệnh RESET tới trạm {device.name or device.serial} thành công."}

                            if action == "station_delete":
                                station_label = str(device.name or device.serial)
                                serial = str(device.serial)
                                requested = str(payload.get("station") or "").strip().lower()
                                exact_identifiers = {
                                    str(device.name or "").strip().lower(),
                                    serial.lower(),
                                }
                                if requested not in exact_identifiers:
                                    raise ValueError(
                                        f"Để tránh xóa nhầm, hãy nhập chính xác tên '{station_label}' "
                                        f"hoặc serial '{serial}'."
                                    )
                                await delete_device_from_list(serial, devices_db, execution_user)
                                latest_device_telemetry_cache.pop(serial, None)
                                mqtt_handler.device_telemetry_cache.pop(serial, None)
                                return {"message": f"✅ Đã xóa trạm {station_label} ({serial}) khỏi hệ thống."}

                            if action == "rovers":
                                query = str(payload.get("query") or "").strip().lower()
                                rover_items = list(visible_rovers)
                                if query:
                                    rover_items = [item for item in rover_items if query in str(item.username or "").lower()]
                                rover_items = sorted(rover_items, key=lambda item: str(item.username))
                                if not rover_items:
                                    return {"message": "Không tìm thấy rover."}
                                total_matches = len(rover_items)
                                lines = [f"🛰️ ROVER ({total_matches})"]
                                for item in rover_items[:30]:
                                    icon = "✅" if item.is_active else "⛔"
                                    lines.append(f"{icon} {item.username} | {item.mountpoints or '-'} | max={item.max_connections}")
                                if total_matches > 30:
                                    lines.append("… Gõ /rovers <từ khóa> để lọc danh sách.")
                                return {"message": "\n".join(lines)}

                            if action in {
                                "rover", "rover_edit_context", "rover_update", "rover_delete", "rover_renew",
                                "rover_block", "rover_unblock",
                            }:
                                rover = await _zalo_find_rover(
                                    auth_db, devices_db, payload.get("username"),
                                    allow_partial=action in {"rover", "rover_edit_context"},
                                    rover_items=visible_rovers, device_items=visible_devices,
                                )
                            else:
                                rover = None

                            if action == "rover_edit_context":
                                available_mountpoints: Set[str] = set()
                                for station_item in visible_devices:
                                    service_cfg = station_item.service_config if isinstance(station_item.service_config, dict) else {}
                                    for server_id in (1, 2):
                                        mountpoint = str(service_cfg.get(f"mountpoint{server_id}") or "").strip()
                                        if mountpoint:
                                            available_mountpoints.add(mountpoint)
                                try:
                                    caster_sources = await get_admin_caster_sources(execution_user)
                                    for source in caster_sources.get("all", []):
                                        mountpoint = str(source.get("mountpoint") or "").strip()
                                        if mountpoint:
                                            available_mountpoints.add(mountpoint)
                                except Exception as exc:
                                    logger.warning("Zalo editor could not load live caster sources: %s", exc)
                                remaining_days = None
                                if rover.expires_at:
                                    remaining_days = max(0, int((int(rover.expires_at) - int(time.time()) + 86399) // 86400))
                                return {
                                    "username": str(rover.username),
                                    "label": str(rover.username),
                                    "available_mountpoints": sorted(available_mountpoints),
                                    "current": {
                                        "password": bool(rover.password),
                                        "mountpoints": rover.mountpoints or "",
                                        "expiry_days": remaining_days,
                                        "max_connections": rover.max_connections,
                                        "show_position": bool(rover.show_position),
                                        "rtcm_cycle_enabled": bool(getattr(rover, "rtcm_cycle_enabled", False)),
                                        "rtcm_disconnect_interval_seconds": int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300),
                                        "rtcm_disconnect_duration_seconds": int(getattr(rover, "rtcm_disconnect_duration_seconds", 10) or 10),
                                        "is_shared_with_managers": bool(getattr(rover, "is_shared_with_managers", False)),
                                    },
                                }

                            if action == "rover":
                                expiry = datetime.fromtimestamp(rover.expires_at).strftime("%d/%m/%Y %H:%M") if rover.expires_at else "không giới hạn"
                                live_hosts: Set[str] = set()
                                live_mountpoints: Set[str] = set()
                                try:
                                    rover_key = str(rover.username or "").strip().lower()
                                    for cache_data in (await _get_all_caster_cache()).values():
                                        host = str(cache_data.get("host") or "").strip()
                                        for caster_user in cache_data.get("users") or []:
                                            if str(caster_user.get("username") or "").strip().lower() != rover_key:
                                                continue
                                            if host:
                                                live_hosts.add(host)
                                            raw_mountpoints = caster_user.get("mountpoints") or []
                                            if isinstance(raw_mountpoints, str):
                                                raw_mountpoints = re.split(r"[;,\s]+", raw_mountpoints)
                                            live_mountpoints.update(
                                                str(item).strip() for item in raw_mountpoints if str(item).strip()
                                            )
                                except Exception as exc:
                                    logger.warning("Could not resolve live rover placement for %s: %s", rover.username, exc)

                                if not live_hosts:
                                    live_hosts.update(
                                        item.strip() for item in str(rover.target_casters or "").split(";") if item.strip()
                                    )
                                if not live_mountpoints:
                                    live_mountpoints.update(
                                        item.strip() for item in re.split(r"[;,]+", str(rover.mountpoints or "")) if item.strip()
                                    )
                                sorted_hosts = sorted(live_hosts)
                                sorted_mountpoints = sorted(live_mountpoints)
                                caster_label = "Caster" if len(sorted_hosts) <= 1 else "Các Caster"
                                mountpoint_label = "Mountpoint" if len(sorted_mountpoints) <= 1 else "Các mountpoint"
                                return {
                                    "message": "\n".join((
                                        f"🛰️ ROVER {rover.username}",
                                        f"• Trạng thái: {'Đang hoạt động' if rover.is_active else 'Đang khóa'}",
                                        f"• {mountpoint_label}: {', '.join(sorted_mountpoints) or 'Không có'}",
                                        f"• {caster_label}: {', '.join(sorted_hosts) or 'Chưa xác định'}",
                                        f"• Kết nối tối đa: {rover.max_connections}",
                                        f"• Hiển thị vị trí: {'Có' if rover.show_position else 'Không'}",
                                        f"• Hết hạn: {expiry}",
                                    )),
                                    "context_target": str(rover.username),
                                    "context_type": "rover",
                                    "context_name": str(rover.username),
                                }

                            if action == "rover_create":
                                service_cfg = device.service_config if isinstance(device.service_config, dict) else {}
                                default_mountpoint = next((
                                    str(service_cfg.get(key)).strip()
                                    for enabled_key, key in (
                                        ("server1_enabled", "mountpoint1"),
                                        ("server2_enabled", "mountpoint2"),
                                    )
                                    if service_cfg.get(enabled_key) and str(service_cfg.get(key) or "").strip()
                                ), None)
                                target_mountpoint = str(payload.get("mountpoint") or default_mountpoint or "").strip()
                                if not target_mountpoint:
                                    raise ValueError("Trạm chưa có mountpoint; hãy truyền mountpoint trong lệnh")
                                create_request = schemas.RoverMultiCreate(
                                    username=str(payload["username"]),
                                    password=str(payload["password"]),
                                    expiry_days=int(payload.get("expiry_days", 365)),
                                    selected_serials=[str(device.serial)],
                                    mountpoint_override=target_mountpoint,
                                )
                                created = await create_multi_base_rover(
                                    create_request,
                                    auth_db,
                                    devices_db,
                                    execution_user,
                                )
                                await crud.log_system_event(
                                    devices_db, "success",
                                    f"Zalo admin created rover '{created.username}'",
                                    user_id=execution_user.id, serial=device.serial,
                                )
                                return {"message": f"✅ Đã tạo rover {created.username} cho {device.name or device.serial}."}

                            if action == "rover_update":
                                changes = dict(payload.get("changes") or {})
                                allowed_fields = set(schemas.RoverUpdate.model_fields)
                                unknown = sorted(set(changes) - allowed_fields)
                                if unknown:
                                    raise ValueError(f"Rover key không hợp lệ: {', '.join(unknown)}")
                                list_fields = {"selected_serials", "target_casters"}
                                converted = {}
                                for key, value in changes.items():
                                    converted[key] = (
                                        [part.strip() for part in str(value).split(",") if part.strip()]
                                        if key in list_fields else _zalo_parse_scalar(value)
                                    )
                                update_request = schemas.RoverUpdate(**converted)
                                updated = await device_routes.update_rover(
                                    rover.id, update_request, auth_db, devices_db, execution_user
                                )
                                await crud.log_system_event(
                                    devices_db, "success",
                                    f"Zalo admin updated rover '{updated.username}'",
                                    user_id=execution_user.id,
                                )
                                return {"message": f"✅ Đã cập nhật rover {updated.username}."}

                            if action == "rover_delete":
                                await device_routes.delete_rover(rover.id, auth_db, devices_db, execution_user)
                                await crud.log_system_event(
                                    devices_db, "warning",
                                    f"Zalo admin deleted rover '{rover.username}'",
                                    user_id=execution_user.id,
                                )
                                return {"message": f"✅ Đã xóa rover {rover.username}."}

                            if action == "rover_renew":
                                request = schemas.RoverBulkRenewRequest(
                                    usernames=[rover.username],
                                    expiry_days=int(payload.get("expiry_days", 365)),
                                )
                                result = await bulk_renew_rovers(request, auth_db, devices_db, execution_user)
                                return {"message": (
                                    f"✅ Đã gia hạn rover {rover.username} {result.get('expiry_days')} ngày.\n"
                                    f"Hết hạn mới: {datetime.fromtimestamp(result['expires_at']).strftime('%d/%m/%Y %H:%M')}"
                                )}

                            if action == "rover_block":
                                request = schemas.BlockRoverRequest(
                                    username=rover.username,
                                    reason=str(payload.get("reason") or "Blocked via Zalo admin"),
                                )
                                result = await block_caster_user(request, auth_db, devices_db, execution_user)
                                return {"message": (
                                    f"✅ Đã chặn rover {rover.username}. "
                                    f"Caster ảnh hưởng: {result.get('casters_affected', 0)}"
                                )}

                            if action == "rover_unblock":
                                request = schemas.UnblockRoverRequest(
                                    username=rover.username,
                                    mountpoint=payload.get("mountpoint"),
                                )
                                result = await unblock_caster_user(request, auth_db, devices_db, execution_user)
                                return {"message": f"✅ Đã mở chặn rover {rover.username} thành công."}

                            raise ValueError(f"Admin action không được hỗ trợ: {action}")
                        except HTTPException as exc:
                            raise RuntimeError(str(exc.detail)) from exc

            async def _zalo_get_binding_scopes() -> Dict[int, Set[str]]:
                scopes: Dict[int, Set[str]] = {}
                async with auth_session_scope() as auth_db:
                    binding_rs = await auth_db.execute(select(models.ZaloChatBinding).where(
                        models.ZaloChatBinding.binding_type == "private",
                        models.ZaloChatBinding.is_active.is_(True),
                    ))
                    bindings = binding_rs.scalars().all()
                    async with device_session_scope() as devices_db:
                        for binding in bindings:
                            user = await auth_db.get(models.User, binding.user_id) if binding.user_id else None
                            if not user or not user.is_active:
                                scopes[int(binding.id)] = set()
                                continue
                            devices = await _ops_visible_devices(devices_db, auth_db, user)
                            rovers = await _ops_visible_rovers(auth_db, devices_db, user)
                            values = {
                                str(value).strip().casefold()
                                for item in devices
                                for value in (item.serial, item.name)
                                if str(value or "").strip()
                            }
                            values.update(
                                str(item.username).strip().casefold()
                                for item in rovers if str(item.username or "").strip()
                            )
                            scopes[int(binding.id)] = values
                return scopes

            for service in zalo_bot_services.values():
                service.set_callbacks(
                    get_devices=_zalo_get_devices,
                    get_alerts=_zalo_get_alerts,
                    admin_action=_zalo_admin_action,
                    get_binding_scopes=_zalo_get_binding_scopes,
                )
        _configure_zalo_bots()
        if settings.ENABLE_ZALO_BOT:
            from .zalo_bot_service import start_zalo_bots
            tasks.append(asyncio.create_task(start_zalo_bots()))
            logger.info("✓ Zalo Bot service started")

        yield

    finally:
        logger.info("🛑 Application shutting down...")

        if settings.ENABLE_ZALO_BOT:
            try:
                from .zalo_bot_service import stop_zalo_bots
                await stop_zalo_bots()
            except Exception:
                logger.exception("Error stopping Zalo Bot")

        await redis_client.disconnect()

        # --- BƯỚC 1: DỪNG MQTT TRƯỚC (Ngắt nguồn dữ liệu vào) ---
        try:
            mqtt_handler.stop_mqtt_loop()
            logger.info("âœ“ MQTT stopped")
            await asyncio.sleep(1.0)
        except Exception:
            logger.exception("Error stopping MQTT")

        # --- BƯỚC 2: HỦY CÁC BACKGROUND TASKS ---
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"âœ“ Cancelled {len(tasks)} background tasks")

        # --- BƯỚC 3: ĐÓNG KẾT NỐI DATABASE (QUAN TRỌNG NHẤT) ---
        try:
            logger.info("Setting all devices to OFFLINE...")
            shutdown_at = int(time.time())
            async with device_session_scope() as db:
                await db.execute(
                    update(models.Device)
                    .where(models.Device.status.in_(['online', 'sleep']))
                    .values(
                        status='offline',
                        offline_since=shutdown_at,
                        ntrip_connected=False,
                        bps=0
                    )
                )
                await db.commit()
            logger.info("âœ“ All devices marked as OFFLINE")
        except Exception:
            logger.exception("Error setting devices offline")

        # 4. ĐÓNG KẾT NỐI DATABASE (Cuối cùng)
        try:
            logger.info("Closing database connections...")
            await engine.dispose()
            await auth_engine.dispose()
            logger.info("âœ“ Database connections closed gracefully")
        except Exception:
            logger.exception("Error closing databases")

        logger.info("âœ… Shutdown sequence complete")

