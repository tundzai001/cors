"""Configure station identities and the shared AutoNearest profile."""

import math
import re

from sqlalchemy import func, select

from . import crud, models


def _coordinate_pair(value):
    if not isinstance(value, dict):
        return None
    try:
        lat = float(value.get('lat', value.get('latitude')))
        lon = float(value.get('lon', value.get('longitude')))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    if lat == 0 and lon == 0:
        return None
    return lat, lon


def _station_coords(device: models.Device):
    config = device.base_config if isinstance(device.base_config, dict) else {}
    coords = _coordinate_pair(config.get('coords') or config)
    if coords:
        return coords
    system = device.system_info if isinstance(device.system_info, dict) else {}
    readback = system.get('receiver_base_readback') or {}
    if readback.get('state') == 'ok' and readback.get('mode_kind') == 'base':
        return _coordinate_pair(readback.get('bestpos'))
    return None


DEFAULT_ROVER_PASSWORD = "12345"


def esp_rover_name(device, proposed_name=None):
    name = str(proposed_name if proposed_name is not None else device.name or device.serial or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        raise ValueError("Tên ESP32 phải là tên mountpoint hợp lệ, ví dụ 71232.")
    if name.casefold() in {"genuser", "gensource", "sourcetable", "usertable", "clienttable",
                           "getmountpoint", "getmountpointlist", "setclient", "showpos",
                           "deluser", "delsource", "blockuser", "unblockuser"}:
        raise ValueError("Tên này trùng với lệnh điều khiển của caster.")
    return name


async def existing_esp_rover(auth_db, device, username):
    rows = (await auth_db.execute(select(models.RoverAccount).where(
        (func.lower(models.RoverAccount.username) == username.casefold())
        | ((models.RoverAccount.base_serial == device.serial)
           & models.RoverAccount.created_by_user_id.is_(None))
    ))).scalars().all()
    current = next((row for row in rows if row.username.casefold() == username.casefold()), None)
    if current and (current.base_serial != device.serial or current.created_by_user_id is not None):
        raise ValueError(f"Tài khoản {username} đang thuộc trạm khác hoặc được quản lý thủ công.")
    previous = next((row for row in sorted(rows, key=lambda row: row.id, reverse=True)
                     if row.base_serial == device.serial and row.created_by_user_id is None), None)
    return current, current or previous


async def caster_esp_rover(caster, username, current):
    account = next((row for row in await caster.get_users(require_complete=True)
                    if row["username"].casefold() == username.casefold()), None)
    if account is not None and current is None:
        raise ValueError(f"Tài khoản {username} đã tồn tại trên {caster.host}:{caster.port}.")
    return account
