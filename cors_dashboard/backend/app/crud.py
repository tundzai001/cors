# ==============================================================================
# == backend/app/crud.py 
# ==============================================================================
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, func, and_, or_, delete, case
from sqlalchemy.dialects.postgresql import insert as pg_insert, JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from . import models, schemas
from .database import settings
import time
import logging
import asyncio
import re
import json

logger = logging.getLogger(__name__)
 
def _ensure_dict(val) -> dict:
    """Đảm bảo trả về dict để tránh lỗi 'list' object has no attribute 'get'"""
    if isinstance(val, dict):
        return val
    return {}


def normalize_service_config(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    return value if isinstance(value, dict) else {}


def normalize_ntrip_status_payload(device_data: dict) -> dict:
    """Normalize agent NTRIP telemetry into per-server bps and connection fields."""
    stats = _ensure_dict(device_data.get("ntrip_stats"))
    raw_status = _ensure_dict(device_data.get("ntrip_status"))
    stream_state = _ensure_dict(device_data.get("ntrip_stream_state"))
    server_keys = ("server1", "server2")
    has_server_telemetry = any(
        f"{key}_bps" in stats
        or f"{key}_bps" in raw_status
        or key in raw_status
        for key in server_keys
    )
    if not has_server_telemetry:
        return raw_status

    normalized = {}
    for key in server_keys:
        raw_server = raw_status.get(key)
        server_status = raw_server if isinstance(raw_server, dict) else {}
        raw_bps = stats.get(f"{key}_bps")
        if raw_bps is None:
            raw_bps = server_status.get("bps", raw_status.get(f"{key}_bps", 0))
        try:
            bps = max(0, int(float(raw_bps or 0)))
        except (TypeError, ValueError, OverflowError):
            bps = 0

        connected = server_status.get("connected")
        if isinstance(raw_server, bool):
            connected = raw_server
        connected = (connected is True) or bps > 0
        normalized_server = {**server_status, "connected": connected, "bps": bps}
        stream_server = _ensure_dict(stream_state.get(key))
        for state_key in (
            "enabled", "on_demand", "active", "can_push", "sleep", "mountpoint"
        ):
            if state_key in stream_server:
                normalized_server[state_key] = stream_server[state_key]
        normalized[key] = normalized_server

    for key in ("rtcm_input_bps", "rtcm_in_bps", "antenna_status"):
        if key in stats:
            normalized[key] = stats[key]
        elif key in raw_status:
            normalized[key] = raw_status[key]

    return normalized


def _has_usable_value(value) -> bool:
    if value is None:
        return False
    return str(value).strip() != ""


def normalize_base_config(base_config: dict | None) -> dict:
    """Normalize base_config to the canonical nested coords shape.

    Returns an empty dict when the payload does not contain usable coordinates,
    so callers can treat partial/offline payloads as no-ops instead of wiping
    an existing stored configuration.
    """
    if not isinstance(base_config, dict):
        return {}

    coords = _ensure_dict(base_config.get("coords"))
    lat = coords.get("lat", base_config.get("lat"))
    lon = coords.get("lon", base_config.get("lon"))
    alt = coords.get("alt", base_config.get("alt"))

    if not _has_usable_value(lat) or not _has_usable_value(lon):
        return {}

    normalized = {
        "base_setup_method": base_config.get("base_setup_method", "FIXED_LLA"),
        "coords": {
            "lat": lat,
            "lon": lon,
            "alt": alt if _has_usable_value(alt) else 0.0,
        },
    }

    for key, value in base_config.items():
        if key not in ["lat", "lon", "alt", "base_setup_method", "coords"]:
            normalized[key] = value

    return normalized

async def batch_update_devices(db: AsyncSession, devices_data: list[dict]) -> int:
    """
    Cập nhật hàng loạt thiết bị (Batch Update) sử dụng PostgreSQL UPSERT.
    Hàm này được tối ưu để chịu tải cao, tự động Retry khi gặp Deadlock.
    """
    if not devices_data:
        return 0
    
    # ===== CẤU HÌNH RETRY =====
    MAX_RETRIES = 3
    RETRY_DELAY = 0.1  # 100ms
    
    for attempt in range(MAX_RETRIES):
        try:
            def _normalize_optional_text(value):
                if value is None:
                    return None
                text = str(value).strip()
                return text if text else None

            def _normalize_optional_json(value, *, require_coords: bool = False):
                if value is None:
                    return None
                # 🔥 FIX: Nếu là List (do migration JSONB hoặc lỗi Agent), chuyển về None để tránh lỗi .get() sau này
                if isinstance(value, list) or (isinstance(value, dict) and len(value) == 0):
                    return None
                if require_coords:
                    normalized = normalize_base_config(value)
                    return normalized or None
                return value

            # 1. Deduplication: Chỉ giữ lại update mới nhất cho mỗi Serial
            unique_devices_map = {}
            
            for device in devices_data:
                # 🔥 FIX: Đảm bảo item trong list là dict
                if not isinstance(device, dict):
                    continue
                    
                serial = device.get('serial')
                if not serial:
                    continue

                normalized_ntrip_status = normalize_ntrip_status_payload(device)
                if normalized_ntrip_status:
                    device['ntrip_status'] = normalized_ntrip_status
                    has_server_rates = any(
                        f"server{sid}_bps" in _ensure_dict(device.get('ntrip_stats'))
                        or 'bps' in _ensure_dict(device['ntrip_status'].get(f'server{sid}'))
                        or f"server{sid}_bps" in _ensure_dict(device.get('ntrip_status'))
                        for sid in (1, 2)
                    )
                    if has_server_rates:
                        device['bps'] = sum(
                            int(_ensure_dict(device['ntrip_status'].get(f'server{sid}')).get('bps', 0) or 0)
                            for sid in (1, 2)
                        )
                    if not isinstance(device.get('ntrip_connected'), bool):
                        device['ntrip_connected'] = any(
                            bool(_ensure_dict(device['ntrip_status'].get(f'server{sid}')).get('connected'))
                            for sid in (1, 2)
                        )

                # Chuẩn bị record
                # Lưu ý: Dùng .get(key, default) để tránh lỗi KeyError
                normalized_name = _normalize_optional_text(device.get('name'))
                normalized_chip_type = _normalize_optional_text(device.get('detected_chip_type'))
                normalized_base_config = _normalize_optional_json(device.get('base_config'), require_coords=True)
                normalized_service_config = _normalize_optional_json(
                    normalize_service_config(device.get('service_config'))
                )
                normalized_system_info = _normalize_optional_json(device.get('system_info'))
                if isinstance(normalized_system_info, dict):
                    normalized_system_info = dict(normalized_system_info)
                    for managed_key in (
                        'auth_license_bypass',
                        'auth_license_bypass_by',
                        'auth_license_bypass_at',
                    ):
                        normalized_system_info.pop(managed_key, None)

                device_record = {
                    'serial': serial,
                    'name': normalized_name,
                    # Với payload partial (ví dụ offline event), không ép default để tránh ghi đè dữ liệu cũ.
                    'status': device.get('status'),
                    'timestamp': device.get('timestamp', int(time.time())),
                    'offline_since': device.get('offline_since'),
                    'bps': device.get('bps'),
                    'ntrip_status': device.get('ntrip_status'),
                    'ntrip_connected': device.get('ntrip_connected'),
                    'detected_chip_type': normalized_chip_type,
                    'base_config': normalized_base_config,
                    'service_config': normalized_service_config,
                    'system_info': normalized_system_info,
                    'version': device.get('version'),
                    'is_provisioned': device.get('is_provisioned'),
                    # Created only by the explicit auto-deploy flow so the UI can
                    # show a real "token not sent yet" state for new agents.
                    'device_token': None,
                    # is_locked thường ít khi gửi qua websocket status, nên dùng default cẩn thận
                    # Tốt nhất là không update is_locked ở đây nếu không có trong payload
                }

                # Chỉ mang theo is_synced khi payload gửi thật sự.
                # Tránh đưa NULL vào cột NOT NULL khi dùng bulk UPSERT.
                if 'is_synced' in device:
                    device_record['is_synced'] = True if device.get('is_synced') is None else bool(device.get('is_synced'))
                
                unique_devices_map[serial] = device_record
            
            # 2. Sắp xếp để tránh Deadlock (Lock order consistency)
            values_list = sorted(unique_devices_map.values(), key=lambda x: x['serial'])
            
            if not values_list:
                return 0
            
            async def execute_upsert(batch_values: list[dict], update_is_synced: bool = False) -> None:
                if not batch_values:
                    return

                stmt = pg_insert(models.Device).values(batch_values)

                # Định nghĩa các cột cần update khi trùng Serial
                update_dict = {
                    'status': func.coalesce(stmt.excluded.status, models.Device.status),
                    'timestamp': stmt.excluded.timestamp,
                    'offline_since': case(
                        (stmt.excluded.status.is_(None), models.Device.offline_since),
                        (
                            stmt.excluded.status == 'offline',
                            case(
                                (
                                    models.Device.status == 'offline',
                                    func.coalesce(models.Device.offline_since, stmt.excluded.offline_since),
                                ),
                                else_=func.coalesce(
                                    stmt.excluded.offline_since,
                                    case(
                                        (
                                            and_(
                                                models.Device.timestamp > 0,
                                                stmt.excluded.timestamp - models.Device.timestamp > 180,
                                            ),
                                            models.Device.timestamp + 180,
                                        ),
                                        else_=stmt.excluded.timestamp,
                                    ),
                                ),
                            ),
                        ),
                        else_=None,
                    ),
                    'bps': func.coalesce(stmt.excluded.bps, models.Device.bps),
                    'ntrip_status': func.coalesce(stmt.excluded.ntrip_status, models.Device.ntrip_status),
                    'ntrip_connected': func.coalesce(stmt.excluded.ntrip_connected, models.Device.ntrip_connected),
                    # Không cho UNKNOWN ghi đè chip type hợp lệ đã lưu trước đó.
                    # RÀO CHẮN (IMPORTANT): Chỉ cập nhật Chip Type nếu đang Synced hoặc đang chờ kích hoạt lần đầu.
                    'detected_chip_type': case(
                        (or_(models.Device.is_synced == True, models.Device.detected_chip_type == 'UNKNOWN'), 
                         func.coalesce(func.nullif(stmt.excluded.detected_chip_type, 'UNKNOWN'), models.Device.detected_chip_type)),
                        else_=models.Device.detected_chip_type
                    ),
                    
                    # 🔥 FIX: Sử dụng JSONB merge (||) để gộp config thay vì ghi đè toàn bộ.
                    # RÀO CHẮN (IMPORTANT): Nếu trạm đang OUT OF SYNC (is_synced=False), KHÔNG cho phép status đè lên cấu hình mục tiêu.
                    'base_config': case(
                        (models.Device.is_synced == False, models.Device.base_config), 
                        (and_(models.Device.base_config != None, stmt.excluded.base_config != None), 
                         models.Device.base_config.op('||')(stmt.excluded.base_config)),
                        (stmt.excluded.base_config != None, stmt.excluded.base_config),
                        else_=models.Device.base_config
                    ),
                    'service_config': case(
                        (and_(models.Device.server_config_lock.is_not(None), models.Device.server_config_lock != {}), models.Device.service_config),
                        (models.Device.is_synced == False, models.Device.service_config),
                        (and_(models.Device.service_config != None, stmt.excluded.service_config != None), 
                         case(
                             (func.jsonb_typeof(models.Device.service_config) == 'object',
                              models.Device.service_config.op('||')(stmt.excluded.service_config)),
                             else_=stmt.excluded.service_config,
                         )),
                        (stmt.excluded.service_config != None, stmt.excluded.service_config),
                        else_=models.Device.service_config
                    ),
                    'system_info': case(
                        (and_(models.Device.system_info != None, stmt.excluded.system_info != None), 
                         models.Device.system_info.op('||')(stmt.excluded.system_info)),
                        (stmt.excluded.system_info != None, stmt.excluded.system_info),
                        else_=models.Device.system_info
                    ),
                    
                    'is_provisioned': func.coalesce(stmt.excluded.is_provisioned, models.Device.is_provisioned),
                    'version': func.coalesce(stmt.excluded.version, models.Device.version),
                }

                if update_is_synced:
                    update_dict['is_synced'] = stmt.excluded.is_synced

                stmt = stmt.on_conflict_do_update(
                    index_elements=['serial'],
                    set_=update_dict,
                    # Ignore out-of-order stale status packets.
                    where=(stmt.excluded.timestamp >= models.Device.timestamp)
                )

                await db.execute(stmt)

            batch_with_is_synced = [item for item in values_list if 'is_synced' in item]
            batch_without_is_synced = [item for item in values_list if 'is_synced' not in item]

            await execute_upsert(batch_without_is_synced, update_is_synced=False)
            await execute_upsert(batch_with_is_synced, update_is_synced=True)
            await db.commit()
            
            # Log thành công (chỉ log khi retry để đỡ rác)
            if attempt > 0:
                logger.info(f"✓ Batch update succeeded on attempt {attempt+1}")
            
            return len(values_list)

        except DBAPIError as e:
            await db.rollback()
            msg = str(e).lower()
            if "deadlock" in msg or "concurrent update" in msg:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"⚠️ DB Deadlock (Attempt {attempt+1}). Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
            logger.error(f"❌ Batch update DB Error: {e}")
            return 0
            
        except Exception as e:
            await db.rollback()
            logger.error(f"❌ Batch update Unexpected Error: {e}")
            return 0
            
    return 0

async def get_device_for_update(db: AsyncSession, serial: str):
    result = await db.execute(select(models.Device).where(models.Device.serial == serial)
                              .with_for_update().execution_options(populate_existing=True))
    return result.scalars().first()

async def get_device_by_serial(db: AsyncSession, serial: str):
    result = await db.execute(select(models.Device).filter(models.Device.serial == serial))
    return result.scalars().first()

async def get_all_devices(db: AsyncSession):
    result = await db.execute(select(models.Device).order_by(models.Device.name))
    return result.scalars().all()

async def delete_device(db: AsyncSession, serial: str) -> bool:
    """Xóa thiết bị khỏi DB theo serial. Trả về True nếu tìm thấy và xóa thành công."""
    device = await get_device_by_serial(db, serial)
    if not device:
        return False
    await db.delete(device)
    await db.commit()
    logger.info(f"🗑️ Device deleted: {serial}")
    return True

# ==============================================================================
# VALIDATION FUNCTION: Chặn Ghost Devices
# ==============================================================================

def is_valid_serial(serial: str) -> bool:
    """
    Kiểm tra serial có hợp lệ không.
    
    CÁC ĐỊNH DẠNG HỢP LỆ:
    - Ublox: 10000000xxxxxxxx (16 ký tự hex)
    - LoRa Pi: LP_xxxxxxxxxxxx (MAC address)
    - Pi agent: PI_xxxxxxxxxxxx (12 ký tự hex)
    - Relay: RELAY_xxxxxxx
    - Monitor: MONITOR_xxxxxxx
    
    KHÔNG HỢP LỆ:
    - Pi-xxxx (auto-generated name)
    - Garbled text
    - Empty/None
    """
    if not serial:
        return False
    serial = str(serial).strip()
    if len(serial) < 8:
        return False
    
    # Pattern 1: Ublox (16 hex chars)
    if re.match(r'^[0-9a-fA-F]{16}$', serial):
        return True
        
    # Pattern 1.1: IMEI-like (15-20 digits)
    if re.match(r'^\d{15,20}$', serial):
        return True
    
    # Pattern 2: LoRa Pi (LP_MAC)
    if re.match(r'^LP_[0-9A-F]{12}$', serial, re.IGNORECASE):
        return True

    # Pi agent fallback serial: PI_ + 12 hex chars from uuid4.
    if re.fullmatch(r'PI_[0-9A-F]{12}', serial, re.IGNORECASE):
        return True
    
    # Pattern 3: Relay (RELAY_XXX)
    if serial.startswith('RELAY_'):
        return True


    # Pattern 5: Monitor station (MONITOR_XXX)
    if serial.startswith('MONITOR_'):
        return True

    # Pattern 6: LTE/MQTT bridge stations, for Geotek/Unicorecomm via 4G module.
    if '_LTE_' in serial or serial.startswith('MQTT_') or serial.startswith('GEOTEK_LTE_'):
        return True
    
    # Bạn có thể thêm pattern khác nếu cần
    
    return False


async def update_or_create_device(db: AsyncSession, device_data: dict) -> models.Device | None:
    """Tạo mới hoặc cập nhật thiết bị với validation chặt chẽ"""
    
    serial = device_data.get("serial")
    if not serial: 
        return None
    serial = str(serial).strip()
    device_data["serial"] = serial

    # ✅ BƯỚC 1: VALIDATE SERIAL TRƯỚC KHI XỬ LÝ
    if not is_valid_serial(serial):
        logger.warning(
            f"⚠️ REJECTED INVALID SERIAL: '{serial}' "
            f"(status={device_data.get('status')}, name={device_data.get('name')})"
        )
        return None  # Từ chối xử lý serial không hợp lệ

    existing_device = await get_device_for_update(db, serial)

    new_base_config = normalize_base_config(device_data.get("base_config", {}))
    new_service_config = normalize_service_config(device_data.get("service_config"))
    new_system_info = _ensure_dict(device_data.get("system_info"))
    new_system_info = {
        key: value for key, value in new_system_info.items()
        if key not in {
            "auth_license_bypass",
            "auth_license_bypass_by",
            "auth_license_bypass_at",
        }
    }
    command_source = _ensure_dict(device_data.get("command_source"))
    if command_source:
        new_system_info["command_source"] = command_source
    new_chip_type = device_data.get("detected_chip_type", "UNKNOWN")
    if "config_status" in device_data:
        new_system_info["config_status"] = device_data["config_status"]
    new_name = device_data.get("name")
    
    raw_is_provisioned = device_data.get("is_provisioned") 
    current_ts = device_data.get("timestamp", int(time.time()))
    new_status = device_data.get("status")
    new_is_synced = device_data.get("is_synced", True)
    
    def build_ntrip_status(device_data: dict, service_config: dict) -> dict:
        if not isinstance(device_data, dict):
            return {}
        normalized = normalize_ntrip_status_payload(device_data)
        if "antenna_status" in device_data and "antenna_status" not in normalized:
            normalized["antenna_status"] = device_data["antenna_status"]
        return normalized
    
    # ✅ BƯỚC 2: TỰ ĐỘNG TẠO CHO RELAY / VIRTUAL / MONITOR
    if not existing_device:
        is_relay_like = (
            serial.startswith(('RELAY_', 'MONITOR_', 'GEOTEK_LTE_', 'MQTT_')) 
            or '_LTE_' in serial
            or device_data.get("is_lte") is True
        )
        
        if is_relay_like:
            # Initialize with defaults if missing to prevent sync errors
            final_service_config = {
                "port1": 2101,
                "ntrip_version1": 1,
                "reconnectioninterval": 10,
                "stream_on_demand": True,
                "server1_enabled": True
            }
            if isinstance(new_service_config, dict):
                final_service_config.update(new_service_config)

            # Tạo mới thiết bị nếu là relay-like
            new_device = models.Device(
                serial=serial,
                name=new_name or serial,
                status=new_status or "offline",
                is_provisioned=True, 
                timestamp=current_ts,
                offline_since=device_data.get("offline_since"),
                base_config=new_base_config,
                service_config=final_service_config,
                system_info=new_system_info or None,
                detected_chip_type=new_chip_type,
                version=device_data.get("version"),
                device_token=None,
                is_synced=True
            )
            db.add(new_device)
            await db.commit()
            await db.refresh(new_device)
            logger.info(f"🆕 AUTO-CREATED RELAY DEVICE: {serial}")
            return new_device
            
        logger.warning(
            f"⚠️ SKIP UNKNOWN DEVICE: Serial '{serial}' does not exist in DB. "
            f"Auto-create is disabled for status updates."
        )
        return None
    
    # ✅ BƯỚC 3: UPDATE EXISTING DEVICE (giữ nguyên logic cũ)
    should_update = False
    update_values = {}

    # Xử lý is_provisioned
    if existing_device.is_provisioned and raw_is_provisioned is False:
        update_values["is_provisioned"] = False
        should_update = True
    elif not existing_device.is_provisioned and raw_is_provisioned is True:
        update_values["is_provisioned"] = True
        should_update = True

    # Chip type
    if new_chip_type and new_chip_type != "UNKNOWN" and existing_device.detected_chip_type != new_chip_type:
        update_values["detected_chip_type"] = new_chip_type
        should_update = True

    # Version
    new_version = device_data.get("version")
    if new_version and existing_device.version != new_version:
        update_values["version"] = new_version
        should_update = True

    # ✅ KIỂM TRA BẢO VỆ CẤU HÌNH KHI OUT OF SYNC
    is_out_of_sync = existing_device.is_synced is False

    # ✅ KIỂM TRA BASE CONFIG
    # 🔥 FIX: Nếu đang OUT OF SYNC, không cho phép status từ device ghi đè Target Config trong DB
    if is_out_of_sync:
        if new_base_config and _ensure_dict(existing_device.base_config) != new_base_config:
            logger.debug(f"ℹ️ [{serial}] Config diff ignored: Waiting for sync to apply target config.")
    elif new_base_config and _ensure_dict(existing_device.base_config) != new_base_config:
        # Chỉ cập nhật khi payload có tọa độ thực sự; payload partial/offline
        # không được làm rơi config đã lưu trong DB.
        old_config = _ensure_dict(existing_device.base_config)
        old_coords = _ensure_dict(old_config.get('coords'))
        new_coords = _ensure_dict(new_base_config.get('coords'))
        old_method = old_config.get('base_setup_method')
        new_method = new_base_config.get('base_setup_method')

        # Nếu DB đã có tọa độ mà status mới không có (nhưng cùng method), giữ nguyên DB
        if old_coords and not new_coords:
            pass
        else:
            if "gnss_options" not in new_base_config and old_config.get("gnss_options"):
                new_base_config["gnss_options"] = old_config["gnss_options"]
            update_values["base_config"] = new_base_config
            update_values["is_synced"] = False
            should_update = True

    # ✅ KIỂM TRA SERVICE CONFIG
    # 🔥 FIX: Không xóa config nếu device gửi dữ liệu rỗng
    previous_service_config = _ensure_dict(existing_device.service_config)
    if new_service_config:
        new_service_config = {
            **previous_service_config,
            **schemas.preserve_service_passwords(previous_service_config, new_service_config),
        }
    previous_relay_source = previous_service_config.get("relay_source")
    is_relay_device = str(serial or "").strip().upper().startswith("RELAY_")
    if (
        new_service_config
        and is_relay_device
        and isinstance(previous_relay_source, dict)
        and "relay_source" not in new_service_config
    ):
        new_service_config = {**new_service_config, "relay_source": previous_relay_source}

    if is_out_of_sync and is_relay_device and new_service_config:
        relay_runtime_config = {
            key: new_service_config[key]
            for key in ("agent_station_power_control", "agent_station_enabled")
            if key in new_service_config
        }
        updated_service_config = {**previous_service_config, **relay_runtime_config}
        if updated_service_config != previous_service_config:
            update_values["service_config"] = updated_service_config
            should_update = True
    elif not is_out_of_sync and not existing_device.server_config_lock and new_service_config and previous_service_config != new_service_config:
        update_values["service_config"] = new_service_config
        should_update = True
    
    # Status
    normalized_new_status = str(new_status or "").strip().lower()
    normalized_existing_status = str(existing_device.status or "").strip().lower()
    if normalized_new_status and normalized_new_status != normalized_existing_status:
        update_values["status"] = new_status
        update_values["timestamp"] = current_ts
        if normalized_new_status == "offline":
            last_seen = int(existing_device.timestamp or 0)
            update_values["offline_since"] = (
                min(current_ts, last_seen + 180)
                if last_seen > 0 and current_ts - last_seen > 180
                else current_ts
            )
        else:
            update_values["offline_since"] = None
        should_update = True
    else:
        update_values["timestamp"] = current_ts
        if normalized_new_status == "offline" and existing_device.offline_since is None and device_data.get("offline_since"):
            update_values["offline_since"] = int(device_data["offline_since"])
        elif normalized_new_status and normalized_new_status != "offline" and existing_device.offline_since is not None:
            update_values["offline_since"] = None
        should_update = True

    # NTRIP status
    current_service_config = new_service_config if new_service_config else _ensure_dict(existing_device.service_config)
    new_ntrip_status = build_ntrip_status(device_data, current_service_config)
    
    if new_ntrip_status:
        old_ntrip_status = _ensure_dict(existing_device.ntrip_status)
        if old_ntrip_status != new_ntrip_status:
            update_values["ntrip_status"] = new_ntrip_status
            should_update = True
            
            # Recalculate top-level bps
            s1 = new_ntrip_status.get("server1", {})
            s2 = new_ntrip_status.get("server2", {})
            total_bps = int(s1.get("bps", 0) or 0) + int(s2.get("bps", 0) or 0)
            if total_bps != existing_device.bps:
                update_values["bps"] = total_bps
                should_update = True

    # NTRIP connected
    new_ntrip = device_data.get("ntrip_connected")
    if new_ntrip is None and new_ntrip_status:
        s1 = new_ntrip_status.get("server1", {})
        s2 = new_ntrip_status.get("server2", {})
        new_ntrip = bool(s1.get("connected") or s2.get("connected"))
    if new_ntrip is not None and new_ntrip != existing_device.ntrip_connected:
        update_values["ntrip_connected"] = new_ntrip
        should_update = True
    
    # ✅ XỬ LÝ is_synced
    if new_is_synced is False and existing_device.is_synced is not False:
        update_values["is_synced"] = False
        should_update = True
    elif new_is_synced is True and existing_device.is_synced is False:
        update_values["is_synced"] = True
        should_update = True

    # System info
    if new_system_info:
        # Logic so sánh đơn giản để giảm update DB không cần thiết
        old_info = _ensure_dict(existing_device.system_info)
        old_auth_status = _ensure_dict(old_info.get("auth_status"))
        new_auth_status = _ensure_dict(new_system_info.get("auth_status"))
        
        # 🔥 FIX: Thêm bảo vệ sâu hơn cho các trường lồng nhau
        old_cpu_dict = _ensure_dict(old_info.get('cpu'))
        new_cpu_dict = _ensure_dict(new_system_info.get('cpu'))
        
        old_cpu = old_cpu_dict.get('usage_percent', 0)
        new_cpu = new_cpu_dict.get('usage_percent', 0)
        
        # Chỉ update nếu CPU thay đổi > 5% hoặc chưa có info
        if (
            not old_info
            or abs(float(old_cpu or 0) - float(new_cpu or 0)) > 5
            or old_auth_status != new_auth_status
        ):
            # Merge to preserve backend-managed flags (e.g. auth_license_bypass)
            merged_info = old_info.copy()
            merged_info.update(new_system_info)
            update_values["system_info"] = merged_info
            should_update = True

    if should_update:
        stmt = (
            update(models.Device)
            .where(models.Device.serial == serial)
            .values(**update_values)
        )
        await db.execute(stmt)
        await db.commit()
        await db.refresh(existing_device)
        return existing_device

    return existing_device

async def get_system_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(models.SystemSetting))
    settings_list = result.scalars().all()
    defaults = {
        'firewall_enabled': True, 
        'vpn_detection_enabled': True, 
        'pattern_analysis_enabled': True,
        'caster_host': settings.CASTER_HOST or 'aitogy.com.vn',
        'caster_port': settings.CASTER_PORT or 2101
    }
    settings_dict = {s.key: s.value for s in settings_list}
    for key, value in defaults.items():
        settings_dict.setdefault(key, value)
    return settings_dict

async def update_system_settings(db: AsyncSession, settings_to_update: dict):
    for key, value in settings_to_update.items():
        stmt = pg_insert(models.SystemSetting).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_={'value': value})
        await db.execute(stmt)
    await db.commit()

async def get_user_by_username(db: AsyncSession, username: str) -> models.User | None:
    result = await db.execute(select(models.User).filter(models.User.username == username))
    return result.scalars().first()

async def get_user_by_id(db: AsyncSession, user_id: int) -> models.User | None:
    result = await db.execute(select(models.User).filter(models.User.id == user_id))
    return result.scalars().first()

async def get_all_users(db: AsyncSession) -> list[models.User]:
    result = await db.execute(select(models.User).order_by(models.User.username))
    return result.scalars().all()

import json

async def create_user(db: AsyncSession, username: str, hashed_password: str, role: str, full_name: str | None = None, is_active: bool = True, can_create_rovers: bool = False, can_create_users: bool = False, extra_mountpoints: str | None = None, parent_id: int | None = None, allowed_bases: list[str] | None = None, view_only_bases: list[str] | None = None, allowed_rovers: list[str] | None = None, can_view_rovers: bool = False) -> models.User:
    db_user = models.User(
        username=username, 
        hashed_password=hashed_password, 
        full_name=full_name, 
        role=role, 
        is_active=is_active, 
        can_view_rovers=can_view_rovers,
        can_create_rovers=can_create_rovers,
        can_create_users=can_create_users,
        extra_mountpoints=extra_mountpoints,
        parent_id=parent_id,
        allowed_bases=json.dumps(allowed_bases) if allowed_bases is not None else None,
        view_only_bases=json.dumps(view_only_bases) if view_only_bases is not None else None,
        allowed_rovers=json.dumps(allowed_rovers) if allowed_rovers is not None else None,
        created_at=int(time.time())
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def update_user(db: AsyncSession, user_id: int, update_data: dict) -> models.User | None:
    user = await get_user_by_id(db, user_id)
    if not user: 
        return None
    for key, value in update_data.items():
        if key in ("allowed_bases", "view_only_bases", "allowed_rovers"):
            setattr(user, key, json.dumps(value) if value is not None else None)
        elif hasattr(user, key): 
            setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user

async def get_devices_by_user_id(db: AsyncSession, user_id: int) -> list[models.Device]:
    result = await db.execute(
        select(models.Device)
        .filter(models.Device.user_id == user_id)
        .order_by(models.Device.name)
    )
    return result.scalars().all()

async def delete_user(db: AsyncSession, user_id: int) -> bool:
    user = await get_user_by_id(db, user_id)
    if not user: 
        return False
    await db.delete(user)
    await db.commit()
    return True

async def create_rover_account(
    db: AsyncSession, 
    username: str, 
    password: str, 
    base_serial: str, 
    creator_id: int,
    mountpoints: str = None,
    max_connections: int = 1,
    show_position: bool = True,
    target_casters: str = None,
    rtcm_cycle_enabled: bool = False,
    rtcm_disconnect_interval_seconds: int = 300,
    rtcm_disconnect_duration_seconds: int = 10,
    expires_at: int = None,
    is_shared_with_managers: bool = False,
    auto_save_sessions: bool = True,
    auto_save_fixed_points: bool = False,
) -> models.RoverAccount:
    db_rover = models.RoverAccount(
        username=username, 
        password=password, 
        base_serial=base_serial, 
        mountpoints=mountpoints, 
        created_by_user_id=creator_id, 
        is_active=True, 
        max_connections=max_connections,
        show_position=show_position,
        target_casters=target_casters,
        created_at=int(time.time()),
        rtcm_cycle_enabled=rtcm_cycle_enabled,
        rtcm_disconnect_interval_seconds=rtcm_disconnect_interval_seconds,
        rtcm_disconnect_duration_seconds=rtcm_disconnect_duration_seconds,
        expires_at=expires_at,
        is_shared_with_managers=is_shared_with_managers,
        auto_save_sessions=auto_save_sessions,
        auto_save_fixed_points=auto_save_fixed_points,
    )
    db.add(db_rover)
    await db.commit()
    await db.refresh(db_rover)
    return db_rover

async def get_rovers_by_base_serial(db: AsyncSession, base_serial: str) -> list[models.RoverAccount]:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.base_serial == base_serial)
    )
    return result.scalars().all()

async def get_rover_by_id(db: AsyncSession, rover_id: int) -> models.RoverAccount | None:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.id == rover_id)
    )
    return result.scalars().first()

async def get_rover_by_username(db: AsyncSession, username: str) -> models.RoverAccount | None:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.username == username)
    )
    return result.scalars().first()

async def update_rover_account(db: AsyncSession, rover_id: int, update_data: dict) -> models.RoverAccount | None:
    rover = await get_rover_by_id(db, rover_id)
    if not rover: 
        return None
    
    for key, value in update_data.items():
        # Chỉ cập nhật nếu value có giá trị (không None)
        if value is not None and hasattr(rover, key): 
            setattr(rover, key, value)
            
    await db.commit()
    await db.refresh(rover)
    return rover

async def delete_rover_account(db: AsyncSession, rover_id: int) -> bool:
    rover = await get_rover_by_id(db, rover_id)
    if not rover: 
        return False
    await db.delete(rover)
    await db.commit()
    return True

async def get_all_rovers_by_user(db: AsyncSession, user_id: int, role: str) -> list[models.RoverAccount]:
    if role == "admin":
        result = await db.execute(select(models.RoverAccount).order_by(models.RoverAccount.created_at.desc()))
        return result.scalars().all()
    
    if role == "coordinator":
        result = await db.execute(
            select(models.RoverAccount)
            .filter(models.RoverAccount.created_by_user_id == user_id)
            .order_by(models.RoverAccount.created_at.desc())
        )
        return result.scalars().all()
    
    return []

def _json_string_set(value, *, lowercase: bool = False) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    if not isinstance(value, list):
        return set()
    result = {str(item).strip() for item in value if str(item).strip()}
    return {item.lower() for item in result} if lowercase else result

def get_allowed_base_serials(user: models.User) -> set[str]:
    return {serial.upper() for serial in _json_string_set(getattr(user, "allowed_bases", None))}


def get_coordinator_view_base_serials(user: models.User) -> set[str]:
    return {serial.upper() for serial in _json_string_set(getattr(user, "view_only_bases", None))}


async def get_coordinator_visible_base_serials(db: AsyncSession, user: models.User) -> set[str]:
    allowed = get_coordinator_view_base_serials(user)
    result = await db.execute(select(models.Device.serial).where(
        or_(models.Device.user_id == user.id, func.upper(models.Device.serial).in_(allowed))
    ))
    return {str(serial).strip().upper() for serial in result.scalars().all() if serial}


async def get_viewer_visible_base_serials(auth_db: AsyncSession, devices_db: AsyncSession, user: models.User) -> set[str]:
    allowed = get_allowed_base_serials(user)
    if not user.parent_id:
        return allowed
    parent = await get_user_by_id(auth_db, user.parent_id)
    if not parent or parent.role != "coordinator" or not parent.is_active:
        return set()
    return allowed & await get_coordinator_visible_base_serials(devices_db, parent)

def get_allowed_rover_usernames(user: models.User) -> set[str]:
    return _json_string_set(getattr(user, "allowed_rovers", None), lowercase=True)

async def get_rover_manageable_base_serials(db: AsyncSession, user: models.User, auth_db: AsyncSession) -> set[str]:
    role = str(getattr(user, "role", "") or "").strip().lower()
    if role == "admin":
        result = await db.execute(select(models.Device.serial))
        return {str(serial).strip().upper() for serial in result.scalars().all() if serial}
    if role == "viewer":
        return await get_viewer_visible_base_serials(auth_db, db, user)
    if role == "coordinator":
        return await get_coordinator_visible_base_serials(db, user)
    return set()


async def _viewer_rover_scope(auth_db: AsyncSession, devices_db: AsyncSession, user: models.User) -> tuple[set[str], set[str]]:
    serials = await get_viewer_visible_base_serials(auth_db, devices_db, user)
    if not serials:
        return set(), set()
    result = await devices_db.execute(select(models.Device).where(func.upper(models.Device.serial).in_(serials)))
    mountpoints = set()
    for device in result.scalars().all():
        config = normalize_service_config(device.service_config)
        for index in (1, 2):
            if config.get(f"server{index}_enabled") and config.get(f"mountpoint{index}"):
                mountpoints.add(str(config[f"mountpoint{index}"]).strip().lower())
    return serials, mountpoints


def _rover_matches_station_scope(rover: models.RoverAccount, serials: set[str], mountpoints: set[str]) -> bool:
    rover_serials = {value.strip().upper() for value in (rover.base_serial or "").split(",") if value.strip()}
    rover_mountpoints = {value.strip().lower() for value in (rover.mountpoints or "").split(";") if value.strip()}
    if not rover_serials and not rover_mountpoints:
        return False
    return (
        (not rover_serials or rover_serials.issubset(serials))
        and (not rover_mountpoints or rover_mountpoints.issubset(mountpoints))
    )


async def rover_mountpoints_within_scope(devices_db: AsyncSession, user: models.User, serials: list[str], mountpoints: str) -> bool:
    requested = {part.strip().lower() for part in re.split(r"[;,\s]+", mountpoints or "") if part.strip()}
    if not requested:
        return True
    selected = {str(serial).strip().upper() for serial in serials if str(serial).strip()}
    allowed = set()
    if selected:
        result = await devices_db.execute(select(models.Device).where(func.upper(models.Device.serial).in_(selected)))
        for device in result.scalars().all():
            config = normalize_service_config(device.service_config)
            for index in (1, 2):
                if config.get(f"server{index}_enabled") and config.get(f"mountpoint{index}"):
                    allowed.add(str(config[f"mountpoint{index}"]).strip().lower())
    if user.role == "coordinator" and user.extra_mountpoints:
        allowed.update(part.strip().lower() for part in re.split(r"[;,\s]+", user.extra_mountpoints) if part.strip())
    return requested.issubset(allowed)

async def _get_manager_mountpoints_and_serials(devices_db: AsyncSession, current_user: models.User) -> tuple[set[str], set[str]]:
    visible_serials = await get_coordinator_visible_base_serials(devices_db, current_user)
    managed_devices_res = await devices_db.execute(
        select(models.Device).where(func.upper(models.Device.serial).in_(visible_serials))
    )
    managed_devices = managed_devices_res.scalars().all()
    
    managed_serials = {d.serial.strip().upper() for d in managed_devices if d.serial}
    managed_mps = set()
    
    for d in managed_devices:
        cfg = normalize_service_config(d.service_config)
        if cfg.get("server1_enabled") and cfg.get("mountpoint1"):
            managed_mps.add(str(cfg.get("mountpoint1")).strip().lower())
        if cfg.get("server2_enabled") and cfg.get("mountpoint2"):
            managed_mps.add(str(cfg.get("mountpoint2")).strip().lower())
            
    if current_user.extra_mountpoints:
        import re
        extras = [mp.strip().lower() for mp in re.split(r'[;,\s\n]+', current_user.extra_mountpoints) if mp.strip()]
        managed_mps.update(extras)
        
    return managed_mps, managed_serials


async def _get_coordinator_manageable_mountpoints_and_serials(
    devices_db: AsyncSession,
    current_user: models.User,
) -> tuple[set[str], set[str]]:
    result = await devices_db.execute(
        select(models.Device).where(models.Device.user_id == current_user.id)
    )
    devices = result.scalars().all()
    serials = {str(device.serial).strip().upper() for device in devices if device.serial}
    mountpoints = set()
    for device in devices:
        config = normalize_service_config(device.service_config)
        for index in (1, 2):
            mountpoint = config.get(f"mountpoint{index}")
            if config.get(f"server{index}_enabled") and mountpoint:
                mountpoints.add(str(mountpoint).strip().lower())
    if current_user.extra_mountpoints:
        mountpoints.update(
            part.strip().lower()
            for part in re.split(r"[;,\s\n]+", current_user.extra_mountpoints)
            if part.strip()
        )
    return mountpoints, serials

async def get_visible_rovers_for_user(auth_db: AsyncSession, devices_db: AsyncSession, current_user: models.User) -> list[models.RoverAccount]:
    all_rovers_res = await auth_db.execute(
        select(models.RoverAccount).order_by(models.RoverAccount.created_at.desc())
    )
    all_rovers = all_rovers_res.scalars().all()
    
    if current_user.role == "admin":
        return all_rovers
        
    if current_user.role == "viewer":
        allowed = get_allowed_rover_usernames(current_user)
        if current_user.parent_id:
            serials, mountpoints = await _viewer_rover_scope(auth_db, devices_db, current_user)
            if not serials:
                return []
            all_rovers = [r for r in all_rovers if _rover_matches_station_scope(r, serials, mountpoints)]
        return [
            r for r in all_rovers
            if r.created_by_user_id == current_user.id
            or str(r.username or "").strip().lower() in allowed
        ]
        
        
    managed_mps, managed_serials = await _get_manager_mountpoints_and_serials(devices_db, current_user)
    
    visible_rovers = []
    for r in all_rovers:
        if r.created_by_user_id == current_user.id:
            visible_rovers.append(r)
            continue
            
        if not r.is_shared_with_managers:
            continue
            
        if _rover_matches_station_scope(r, managed_serials, managed_mps):
            visible_rovers.append(r)
            continue
            
    return visible_rovers

async def check_rover_management_permission(rover: models.RoverAccount, current_user: models.User, devices_db: AsyncSession, auth_db: AsyncSession) -> bool:
    role = str(getattr(current_user, "role", "") or "").strip().lower()
    if role == "admin":
        return True

    if role == "viewer":
        if not getattr(current_user, "can_view_rovers", False) or not getattr(current_user, "can_create_rovers", False):
            return False
        if current_user.parent_id:
            serials, mountpoints = await _viewer_rover_scope(auth_db, devices_db, current_user)
            if not _rover_matches_station_scope(rover, serials, mountpoints):
                return False
        return (
            rover.created_by_user_id == current_user.id
            or str(rover.username or "").strip().lower() in get_allowed_rover_usernames(current_user)
        )

    if (
        role != "coordinator"
        or not getattr(current_user, "can_view_rovers", False)
        or not getattr(current_user, "can_create_rovers", False)
    ):
        return False
        
    if rover.created_by_user_id == current_user.id:
        return True
        
    if not rover.is_shared_with_managers:
        return False
        
    managed_mps, managed_serials = await _get_coordinator_manageable_mountpoints_and_serials(
        devices_db, current_user
    )
    return _rover_matches_station_scope(rover, managed_serials, managed_mps)

# ==============================================================================
# CLEANUP GHOST DEVICES: Xóa các device không hợp lệ (optional)
# ==============================================================================

async def cleanup_ghost_devices(db: AsyncSession) -> int:
    """
    Xóa các ghost devices không hợp lệ.
    Chạy 1 lần khi backend khởi động.
    """
    
    result = await db.execute(select(models.Device))
    all_devices = result.scalars().all()
    
    deleted_count = 0
    
    for device in all_devices:
        # Kiểm tra các dấu hiệu ghost device:
        # 1. Serial không hợp lệ
        # 2. Tên có dạng "Pi-XXXX" và chưa provision
        # 3. Không có base_config và service_config
        
        is_ghost = False
        reason = ""
        
        if not is_valid_serial(device.serial):
            is_ghost = True
            reason = "Invalid serial format"
        
        elif (device.name and device.name.startswith('Pi-') and 
              not device.is_provisioned and 
              not device.base_config and 
              not device.service_config):
            is_ghost = True
            reason = "Auto-generated unprovision device"
        
        if is_ghost:
            logger.warning(f"🗑️ Deleting ghost device: {device.serial} ({device.name}) - {reason}")
            await db.delete(device)
            deleted_count += 1
    
    if deleted_count > 0:
        await db.commit()
        logger.info(f"✅ Cleaned up {deleted_count} ghost devices")
    
    return deleted_count

async def create_activity_log(db: AsyncSession, log: schemas.ActivityLogCreate):
    db_log = models.ActivityLog(**log.dict())
    db.add(db_log)
    await db.commit()
    await db.refresh(db_log)
    return db_log

# 2. LẤY FEED (Mới nhất trước)
async def get_activity_logs(
    db: AsyncSession,
    limit: int = 15,
    user_id: int | None = None,
    device_serials: set[str] | None = None,
):
    stmt = select(models.ActivityLog)
    if user_id is not None:
        scope = []
        if device_serials:
            scope.append(func.upper(models.ActivityLog.device_serial).in_(device_serials))
        scope.append(and_(models.ActivityLog.user_id == user_id, models.ActivityLog.device_serial.is_(None)))
        stmt = stmt.where(or_(*scope))
    result = await db.execute(
        stmt.order_by(models.ActivityLog.timestamp.desc())
        .limit(max(1, min(int(limit or 15), 100)))
    )
    return result.scalars().all()

# 3. TÍNH TOÁN HEATMAP (Logic quan trọng)
async def get_activity_heatmap(
    db: AsyncSession,
    user_id: int | None = None,
    device_serials: set[str] | None = None,
):
    now = int(time.time())
    seven_days_ago = now - (7 * 24 * 3600)
    
    stmt = select(models.ActivityLog.timestamp).where(models.ActivityLog.timestamp >= seven_days_ago)
    if user_id is not None:
        scope = []
        if device_serials:
            scope.append(func.upper(models.ActivityLog.device_serial).in_(device_serials))
        scope.append(and_(models.ActivityLog.user_id == user_id, models.ActivityLog.device_serial.is_(None)))
        stmt = stmt.where(or_(*scope))
    result = await db.execute(stmt)
    timestamps = result.scalars().all()
    
    grid = [[0] * 24 for _ in range(7)]
    
    current_day_start = now - (now % 86400) # 00:00 hôm nay
    
    for ts in timestamps:
        day_diff = (current_day_start - (ts - (ts % 86400))) // 86400
        
        if 0 <= day_diff < 7:
            hour = int((ts + 7*3600) / 3600) % 24
    return grid

async def log_system_event(
    db: AsyncSession,
    type: str,     # success, error, warning, info
    message: str,
    user_id: int = None,
    serial: str = None,
    ip: str = None
):
    """Hàm helper để ghi log vào DB từ bất kỳ đâu"""
    from .websocket import manager as ui_manager
    icon_map = {
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️"
    }
    try:
        log_entry = schemas.ActivityLogCreate(
            timestamp=int(time.time()),
            type=type,
            message=message,
            icon=icon_map.get(type, "📝"),
            user_id=user_id,
            device_serial=serial,
            ip_address=ip
        )
        await create_activity_log(db, log_entry)
        
        await ui_manager.broadcast({
            "type": "activity_log_event",
            "data": {
                "type": type,
                "message": message,
                "timestamp": log_entry.timestamp,
                "icon": log_entry.icon
            }
        })
    except Exception as e:
        logger.error(f"Failed to write activity log: {e}")
