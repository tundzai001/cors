"""Distinguish managed ESP32 devices from read-only caster sources."""
import json
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from .database import get_db


def is_4g_mountpoint(mountpoint) -> bool:
    return str(mountpoint or '').strip().lstrip('/').startswith('7')


def is_readonly_base(device) -> bool:
    serial = str(getattr(device, 'serial', '') or '').strip().upper()
    if serial.startswith('ESP_'):
        return False
    if serial.startswith('MONITOR_7'):
        return True
    config = getattr(device, 'service_config', None) or {}
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (TypeError, ValueError):
            config = {}
    return isinstance(config, dict) and any(is_4g_mountpoint(config.get(key)) for key in (
        'mountpoint1', 'active_mountpoint', 'mountpoint', 'source_mountpoint', 'mountpoint2'))


async def _editable_base_request_serials(request):
    serials = [request.path_params['serial']] if request.path_params.get('serial') else []
    if request.url.path in ('/api/license/pi', '/api/devices/bulk-action', '/api/devices/apply-template'):
        try:
            payload = await request.json()
        except ValueError:
            return None  # Leave malformed bodies to the endpoint's schema validation.
        if not isinstance(payload, dict):
            return None
        if request.url.path == '/api/license/pi':
            if isinstance(payload.get('serial'), str):
                serials.append(payload['serial'])
        elif isinstance(payload.get('serials'), list):
            serials.extend(s for s in payload['serials'] if isinstance(s, str))
    return serials


async def require_editable_base(request: Request, db: AsyncSession = Depends(get_db)):
    """Reject station mutations before handlers persist configuration or licenses."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    serials = await _editable_base_request_serials(request)
    if serials is None:
        return
    from . import crud
    for serial in serials:
        device = await crud.get_device_by_serial(db, serial)
        if str(serial).upper().startswith('MONITOR_7') or (device and is_readonly_base(device)):
            raise HTTPException(status_code=403, detail='Base 4G chỉ xem Overview; không cấu hình, kích hoạt hoặc license.')
