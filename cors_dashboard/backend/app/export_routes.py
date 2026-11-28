"""Operations spreadsheet exports."""

import logging
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from fastapi.responses import Response

from . import auth, crud, models
from .database import get_auth_db, get_db
from .export_workbook import make_workbook
from .caster_routes import get_caster_users
from .operations_routes import (
    _ops_visible_devices,
    _ops_visible_rovers,
    _build_ops_alerts,
    _ops_events_query,
    get_ops_summary,
)

logger = logging.getLogger("app.main")
router = APIRouter()
UPDATED_AT_HEADER = "Cập nhật lúc"
CREATED_AT_HEADER = "Ngày tạo"
ACCOUNT_HEADER = "Tài khoản"


def _export_config_value(device, section, key):
    config = getattr(device, section, None)
    return config.get(key, "") if isinstance(config, dict) else ""


def _export_base_coordinate(device, key):
    base = device.base_config if isinstance(device.base_config, dict) else {}
    coords = base.get("coords")
    return coords.get(key, base.get(key, "")) if isinstance(coords, dict) else base.get(key, "")


def _export_stream_bps(device, index):
    status = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    server = status.get(f"server{index}")
    value = server.get("bps", 0) if isinstance(server, dict) else status.get(f"server{index}_bps", 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _export_stream_connected(device, index):
    status = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    server = status.get(f"server{index}")
    return bool(isinstance(server, dict) and server.get("connected")) or bool(_export_stream_bps(device, index))


def _export_ntrip_version(device, index):
    value = str(_export_config_value(device, "service_config", f"ntrip_version{index}") or 1).removeprefix("v")
    return f"v{value}" if "." in value else f"v{value}.0"


async def _visible_export_devices(devices_db, auth_db, current_user, device_serial):
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    if device_serial:
        devices = [device for device in devices if str(device.serial).casefold() == device_serial.casefold()]
        if not devices:
            raise HTTPException(status_code=404, detail="Không tìm thấy trạm trong phạm vi được phép")
    return devices


async def _export_device_coordinators(auth_db, devices):
    coordinator_ids = {device.user_id for device in devices if device.user_id}
    if not coordinator_ids:
        return {}
    result = await auth_db.execute(
        select(models.User.id, models.User.username).where(models.User.id.in_(coordinator_ids))
    )
    return dict(result.all())


def _selected_device_export_fields(request, station_fields):
    selected = request.get("device_fields")
    if selected is None:
        selected = ["serial", "name", "status", "updated_at", "coordinator", "synced"]
    invalid = (
        not isinstance(selected, list)
        or not selected
        or len(selected) > len(station_fields)
        or any(not isinstance(key, str) or key not in station_fields for key in selected)
    )
    if invalid:
        raise HTTPException(status_code=400, detail="Thông số trạm không hợp lệ")
    return list(dict.fromkeys(selected))


async def _export_devices_report(context):
    subtitle = context["subtitle"]
    request = context['request']
    auth_db = context['auth_db']
    devices_db = context['devices_db']
    current_user = context['current_user']
    device_serial = context['device_serial']
    when = context['when']
    rows = context['rows']
    devices = await _visible_export_devices(devices_db, auth_db, current_user, device_serial)
    coordinators = await _export_device_coordinators(auth_db, devices)

    station_fields = {
        "serial": ("Số sê-ri", lambda d: d.serial),
        "name": ("Tên trạm", lambda d: d.name),
        "status": ("Trạng thái", lambda d: d.status),
        "updated_at": (UPDATED_AT_HEADER, lambda d: when(d.timestamp)),
        "version": ("Phiên bản", lambda d: d.version),
        "chip": ("Loại chip", lambda d: d.detected_chip_type),
        "coordinator": ("Điều phối viên", lambda d: coordinators.get(d.user_id, "")),
        "synced": ("Đã đồng bộ", lambda d: d.is_synced),
        "locked": ("Đã khóa", lambda d: d.is_locked),
        "ntrip_connected": ("NTRIP kết nối", lambda d: d.ntrip_connected),
        "total_bps": ("Tổng lưu lượng (B/s)", lambda d: (_export_stream_bps(d, 1) or 0) + (_export_stream_bps(d, 2) or 0)),
        "base_method": ("Chế độ Base", lambda d: _export_config_value(d, "base_config", "base_setup_method")),
        "base_lat": ("Vĩ độ Base", lambda d: _export_base_coordinate(d, "lat")),
        "base_lon": ("Kinh độ Base", lambda d: _export_base_coordinate(d, "lon")),
        "base_alt": ("Cao độ Base (m)", lambda d: _export_base_coordinate(d, "alt")),
        "base_accuracy": ("Độ chính xác Base (m)", lambda d: _export_config_value(d, "base_config", "accuracy") or _export_config_value(d, "base_config", "acc")),
        "server1_enabled": ("Bật luồng 1", lambda d: _export_config_value(d, "service_config", "server1_enabled")),
        "server1_version": ("NTRIP phiên bản 1", lambda d: _export_ntrip_version(d, 1)),
        "server1_host": ("Caster 1", lambda d: _export_config_value(d, "service_config", "serverhost1") or _export_config_value(d, "service_config", "host1")),
        "server1_port": ("Cổng 1", lambda d: _export_config_value(d, "service_config", "port1") or _export_config_value(d, "service_config", "serverport1")),
        "server1_mountpoint": ("Mountpoint 1", lambda d: _export_config_value(d, "service_config", "mountpoint1") or _export_config_value(d, "service_config", "mp1")),
        "server1_bps": ("Lưu lượng 1 (B/s)", lambda d: _export_stream_bps(d, 1)),
        "server1_username": ("Tài khoản luồng 1", lambda d: _export_config_value(d, "service_config", "username1") or _export_config_value(d, "service_config", "user1")),
        "server1_connected": ("Kết nối luồng 1", lambda d: _export_stream_connected(d, 1)),
        "server2_enabled": ("Bật luồng 2", lambda d: _export_config_value(d, "service_config", "server2_enabled")),
        "server2_version": ("NTRIP phiên bản 2", lambda d: _export_ntrip_version(d, 2)),
        "server2_host": ("Caster 2", lambda d: _export_config_value(d, "service_config", "serverhost2") or _export_config_value(d, "service_config", "host2")),
        "server2_port": ("Cổng 2", lambda d: _export_config_value(d, "service_config", "port2") or _export_config_value(d, "service_config", "serverport2")),
        "server2_mountpoint": ("Mountpoint 2", lambda d: _export_config_value(d, "service_config", "mountpoint2") or _export_config_value(d, "service_config", "mp2")),
        "server2_bps": ("Lưu lượng 2 (B/s)", lambda d: _export_stream_bps(d, 2)),
        "server2_username": ("Tài khoản luồng 2", lambda d: _export_config_value(d, "service_config", "username2") or _export_config_value(d, "service_config", "user2")),
        "server2_connected": ("Kết nối luồng 2", lambda d: _export_stream_connected(d, 2)),
        "rtcm_enabled": ("Bật RTCM client", lambda d: _export_config_value(d, "service_config", "rtcm_enabled")),
        "rtcm_server": ("RTCM server", lambda d: _export_config_value(d, "service_config", "rtcmserver1")),
        "rtcm_port": ("RTCM port", lambda d: _export_config_value(d, "service_config", "rtcmport1")),
        "rtcm_mountpoint": ("RTCM mountpoint", lambda d: _export_config_value(d, "service_config", "rtcmmountpoint1")),
        "rtcm_username": ("RTCM tài khoản", lambda d: _export_config_value(d, "service_config", "rtcmusername1")),
    }
    selected = _selected_device_export_fields(request, station_fields)
    headers = [station_fields[key][0] for key in selected]
    rows = [[station_fields[key][1](device) for key in selected] for device in devices]
    title = "BÁO CÁO TRẠM BASE"
    subtitle += f" · {len(rows)} trạm" + (f" · {device_serial}" if device_serial else "")
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _visible_export_rovers(context):
    auth_db = context['auth_db']
    devices_db = context['devices_db']
    current_user = context['current_user']
    rover_name = context['rover_name']
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    allowed_names = {str(rover.username).casefold() for rover in rovers}
    if rover_name and current_user.role != auth.Role.ADMIN and rover_name.casefold() not in allowed_names:
        raise HTTPException(status_code=404, detail="Không tìm thấy Rover trong phạm vi được phép")
    return rovers, allowed_names


async def _export_rover_list_report(context):
    subtitle = context["subtitle"]
    rover_name = context['rover_name']
    when = context['when']
    words = context['words']
    rows = context['rows']
    rovers, _ = await _visible_export_rovers(context)
    selected_rovers = [rover for rover in rovers if not rover_name or str(rover.username).casefold() == rover_name.casefold()]
    if rover_name and not selected_rovers:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover")
    headers = [ACCOUNT_HEADER, "Số sê-ri Base", "Mountpoint", "Caster", "Hoạt động", "Kết nối tối đa", "Hết hạn", CREATED_AT_HEADER]
    rows = [[rover.username, rover.base_serial, words(rover.mountpoints), words(rover.target_casters), rover.is_active, rover.max_connections, when(rover.expires_at), when(rover.created_at)] for rover in selected_rovers]
    title = "DANH SÁCH ROVER"
    subtitle += f" · {len(rows)} dòng" + (f" · Rover {rover_name}" if rover_name else "")
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_rover_history_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    current_user = context['current_user']
    rover_name = context['rover_name']
    when = context['when']
    _, allowed_names = await _visible_export_rovers(context)
    query = select(models.RoverSessionHistory)
    if rover_name:
        query = query.where(func.lower(models.RoverSessionHistory.username) == rover_name.lower())
    elif current_user.role != auth.Role.ADMIN:
        query = query.where(func.lower(models.RoverSessionHistory.username).in_(allowed_names))
    result = await auth_db.execute(query.order_by(models.RoverSessionHistory.last_seen_at.desc()))
    sessions = result.scalars().all()
    if rover_name and not sessions:
        raise HTTPException(status_code=404, detail="Rover này chưa có phiên được lưu")
    headers = [ACCOUNT_HEADER, "Caster", "Mountpoint", "Receiver", "Bắt đầu", "Cập nhật cuối", "Kết thúc", "Vĩ độ cuối", "Kinh độ cuối", "Ghi nhận vị trí", "Google Maps"]
    rows = [[session.username, session.server, session.mountpoint, session.receiver_id, when(session.started_at), when(session.last_seen_at), when(session.ended_at), session.latitude if session.position_fix_quality == 4 else None, session.longitude if session.position_fix_quality == 4 else None, when(session.position_updated_at) if session.position_fix_quality == 4 else None, f"https://www.google.com/maps/search/?api=1&query={session.latitude},{session.longitude}" if session.position_fix_quality == 4 and session.latitude is not None and session.longitude is not None else ""] for session in sessions]
    title = "LỊCH SỬ PHIÊN ROVER"
    subtitle += f" · {len(rows)} dòng" + (f" · Rover {rover_name}" if rover_name else "")
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_caster_users_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    devices_db = context['devices_db']
    current_user = context['current_user']
    when = context['when']
    words = context['words']
    data = await get_caster_users(auth_db=auth_db, devices_db=devices_db, current_user=current_user)
    headers = [ACCOUNT_HEADER, "Caster", "Mountpoint", "Kết nối", "Giới hạn", "Trong DB", "Bị chặn", UPDATED_AT_HEADER]
    rows = [[u.username, u.server, words(u.mountpoints), u.nclient, u.maxclient, u.in_db, u.is_blocked, when(u.last_update)] for u in data.items]
    title = "ROVER TRÊN CASTER"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_blocklist_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    when = context['when']
    result = await auth_db.execute(select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()))
    headers = [ACCOUNT_HEADER, "Mountpoint", "Bị chặn lúc", "Người chặn", "Lý do"]
    rows = [[item.username, item.mountpoint, when(item.blocked_at), item.blocked_by, item.reason] for item in result.scalars()]
    title = "DANH SÁCH ROVER BỊ CHẶN"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_alerts_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    devices_db = context['devices_db']
    current_user = context['current_user']
    kind = context['kind']
    when = context['when']
    alerts = await _build_ops_alerts(auth_db, devices_db, current_user)
    desired_state = "resolved" if kind == "resolved-alerts" else None
    alerts = [item for item in alerts if (item.get("state") == "resolved") == bool(desired_state)]
    headers = ["Mức độ", "Loại", "Tiêu đề", "Nội dung", "Trạm", "Rover", "Caster", "Trạng thái", "Thời gian"]
    rows = [[a.get("severity"), a.get("type"), a.get("title"), a.get("message"), a.get("device_serial"), a.get("rover_username"), a.get("caster_host"), a.get("state"), when(a.get("created_at"))] for a in alerts]
    title = "CẢNH BÁO ĐÃ XỬ LÝ CÒN TỒN TẠI" if desired_state else "CẢNH BÁO HIỆN TẠI"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_events_report(context):
    subtitle = context["subtitle"]
    devices_db = context['devices_db']
    when = context['when']
    events = await _ops_events_query(devices_db, limit=200)
    headers = ["Thời gian", "Mức độ", "Sự kiện", "Trạm", "Người dùng ID", "IP"]
    rows = [[when(e.get("timestamp")), e.get("severity"), e.get("message"), e.get("device_serial"), e.get("actor_user_id"), e.get("ip_address")] for e in events]
    title = "200 SỰ KIỆN GẦN NHẤT"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_casters_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    devices_db = context['devices_db']
    current_user = context['current_user']
    when = context['when']
    summary = await get_ops_summary(auth_db=auth_db, devices_db=devices_db, current_user=current_user)
    headers = ["Máy chủ", "Cổng", ACCOUNT_HEADER, "Kết nối", "Lỗi", "Dữ liệu cũ", UPDATED_AT_HEADER]
    rows = [[c.get("host"), c.get("port"), c.get("users"), c.get("clients"), c.get("error"), c.get("stale"), when(c.get("last_update"))] for c in summary["casters"]]
    title = "TÌNH TRẠNG CASTER"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


async def _export_users_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    when = context['when']
    rows = context['rows']
    users = await crud.get_all_users(auth_db)
    headers = ["ID", ACCOUNT_HEADER, "Họ tên", "Vai trò", "Hoạt động", "Số sê-ri trạm", "Quyền trạm", "Xem Rover", "Tạo Rover", "Tạo tài khoản", CREATED_AT_HEADER]
    def assigned(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        return value if isinstance(value, list) else []
    for user in users:
        view_only = {str(serial).strip() for serial in assigned(user.view_only_bases)}
        assignments = [(serial, "Được sửa") for serial in assigned(user.allowed_bases) if str(serial).strip() not in view_only]
        assignments.extend((serial, "Chỉ xem") for serial in sorted(view_only))
        for serial, access in assignments or [("", "")]:
            rows.append([user.id, user.username, user.full_name, user.role, user.is_active, serial, access, user.can_view_rovers, user.can_create_rovers, user.can_create_users, when(user.created_at)])
    title = "TÀI KHOẢN HỆ THỐNG"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


def _template_parameters(value, words, prefix=""):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _template_parameters(nested, words, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, nested in enumerate(value, 1):
            yield from _template_parameters(nested, words, f"{prefix} [{index}]")
    else:
        yield prefix, words(value)


def _append_template_section(rows, template, section, label, when, words):
    config = template.get(section) or {}
    if isinstance(config, dict):
        for key, value in _template_parameters(config, words):
            rows.append([template.get("name"), label, key, value, template.get("created_by"), when(template.get("created_at"))])


async def _export_templates_report(context):
    subtitle = context["subtitle"]
    auth_db = context['auth_db']
    when = context['when']
    words = context['words']
    rows = context['rows']
    settings_data = await crud.get_system_settings(auth_db)
    headers = ["Tên mẫu", "Phần", "Thông số", "Giá trị", "Người tạo", CREATED_AT_HEADER]
    for template in settings_data.get("device_config_templates", []) or []:
        if not isinstance(template, dict):
            continue
        _append_template_section(rows, template, "base_config", "Base", when, words)
        _append_template_section(rows, template, "service_config", "Dịch vụ", when, words)
        if not (template.get("base_config") or template.get("service_config")):
            rows.append([template.get("name"), "", "", "", template.get("created_by"), when(template.get("created_at"))])
    title = "MẪU CẤU HÌNH TRẠM"
    context["rows"] = rows
    context["subtitle"] = subtitle
    context["headers"] = headers
    context["title"] = title


_EXPORT_REPORT_HANDLERS = {
    'devices': _export_devices_report,
    'rovers': _export_rover_list_report,
    'rover-history': _export_rover_history_report,
    'caster-users': _export_caster_users_report,
    'blocklist': _export_blocklist_report,
    'alerts': _export_alerts_report,
    'resolved-alerts': _export_alerts_report,
    'events': _export_events_report,
    'casters': _export_casters_report,
    'users': _export_users_report,
    'templates': _export_templates_report,
}


@router.get("/api/ops/export/options")
async def get_ops_export_options(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA)),
):
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    names = {str(rover.username).casefold(): str(rover.username) for rover in rovers if rover.username}
    if current_user.role == auth.Role.ADMIN:
        result = await auth_db.execute(select(models.RoverSessionHistory.username).distinct())
        for name in result.scalars():
            if name:
                names.setdefault(str(name).casefold(), str(name))
    return {
        "rovers": sorted(names.values(), key=str.casefold),
        "devices": [{"serial": d.serial, "name": d.name} for d in devices],
    }


@router.post("/api/ops/export/xlsx", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}})
async def export_ops_xlsx(
    request: dict = Body(...),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA)),
):
    """Build a readable workbook from the requested, permission-scoped report."""
    kind = str(request.get("kind") or "").strip()
    rover_name = str(request.get("rover_username") or "").strip()
    device_serial = str(request.get("device_serial") or "").strip()
    if len(rover_name) > 128 or len(device_serial) > 128:
        raise HTTPException(status_code=400, detail="Bộ lọc quá dài")

    def when(value):
        try:
            return datetime.fromtimestamp(int(value), timezone(timedelta(hours=7))).replace(tzinfo=None) if value else None
        except (TypeError, ValueError, OverflowError):
            return None

    def words(value):
        if isinstance(value, (list, tuple, set)):
            return "; ".join(str(item) for item in value if item is not None)
        return "" if value is None else str(value)

    rows = []
    subtitle = f"Xuất lúc {when(time.time()).strftime('%d/%m/%Y %H:%M:%S')} (GMT+7)"
    handler = _EXPORT_REPORT_HANDLERS.get(kind)
    if handler is None:
        raise HTTPException(status_code=400, detail="Loại dữ liệu không hợp lệ")
    context = {
        "request": request, "auth_db": auth_db, "devices_db": devices_db,
        "current_user": current_user, "kind": kind, "rover_name": rover_name,
        "device_serial": device_serial, "when": when, "words": words,
        "rows": rows, "subtitle": subtitle,
    }
    await handler(context)
    rows, subtitle = context["rows"], context["subtitle"]
    headers, title = context["headers"], context["title"]

    if kind not in {"devices", "rovers", "rover-history"}:
        subtitle += f" · {len(rows)} dòng"
    workbook = await asyncio.to_thread(make_workbook, title, subtitle, headers, rows)
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{time.strftime("%Y-%m-%d")}.xlsx"'},
    )
