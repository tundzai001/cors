"""Admin host policy; independent of telemetry and whole-device remote locks."""
from fastapi import HTTPException
from .schemas import service_server_ids


def host_lock_snapshot(config: dict) -> dict:
    return {key: config.get(key, False if key.endswith('_enabled') else None)
            for sid in service_server_ids(config)
            for key in (f'serverhost{sid}', f'port{sid}', f'server{sid}_enabled')}


def locked_server_ids(policy: dict | None) -> list[int]:
    if not policy:
        return []
    if 'locked_servers' in policy:
        return sorted(policy['locked_servers'])
    # Read policies saved by the previous host-disable implementation.
    return [sid for sid in (1, 2) if policy.get(f'server{sid}_enabled') is False]


def supports_server_device_lock(device) -> bool:
    info = device.system_info if isinstance(device.system_info, dict) else {}
    report = info.get('device_lock')
    return isinstance(report, dict) and report.get('scoped') is True


def _host_change_aliases(current: dict) -> dict[str, str]:
    aliases = {f'host{sid}': f'serverhost{sid}' for sid in service_server_ids(current)}
    aliases.update({f'serverport{sid}': f'port{sid}' for sid in service_server_ids(current)})
    aliases.update({'target_host': 'serverhost1', 'target_port': 'port1'})
    return aliases


def _is_stream_mode_key(key: str) -> bool:
    return key in {'stream_active', 'stream_on_demand'} or (
        key.startswith('server') and key.endswith(('_stream_active', '_stream_on_demand'))
    )


def _locked_server_field_id(key: str, value, current: dict, locked_ids: list[int]) -> int | None:
    for sid in (1, 2):
        blocked_field = (
            key in {f'ntrip_version{sid}', f'username{sid}', f'mountpoint{sid}', f'password{sid}'}
            or key.startswith(f'server{sid}_')
        )
        if sid in locked_ids and blocked_field and value != current.get(key):
            return sid
    return None


def check_host_lock(device, changes: dict, *, is_admin: bool = False) -> None:
    policy = getattr(device, 'server_config_lock', None) or {}
    locked_ids = locked_server_ids(policy)
    if not locked_ids or is_admin:
        return
    if changes.get('removed_servers'):
        raise HTTPException(403, 'Chỉ admin được xóa caster khi khóa host.')
    current = device.service_config if isinstance(device.service_config, dict) else {}
    if set(service_server_ids(changes)) - set(service_server_ids(current)):
        raise HTTPException(403, 'Chỉ admin được thêm caster khi khóa host.')
    aliases = _host_change_aliases(current)
    for raw_key, value in changes.items():
        key = aliases.get(raw_key, raw_key)
        if _is_stream_mode_key(key) and value != current.get(key):
            raise HTTPException(403, 'Chế độ gửi của server do admin quản lý khi khóa host.')
        if key in policy and value != policy[key]:
            raise HTTPException(403, 'Host và trạng thái server do admin khóa. Bạn không được thay đổi.')
        # A blocked slot cannot be reconfigured, including credentials/mountpoint.
        locked_server_id = _locked_server_field_id(key, value, current, locked_ids)
        if locked_server_id is not None:
            raise HTTPException(403, f'Server {locked_server_id} bị admin chặn gửi dữ liệu.')


def refresh_host_lock(device, config: dict) -> None:
    if getattr(device, 'server_config_lock', None):
        locked = locked_server_ids(device.server_config_lock)
        device.server_config_lock = {**host_lock_snapshot(config), 'locked_servers': locked} if locked else {'locked_servers': []}
