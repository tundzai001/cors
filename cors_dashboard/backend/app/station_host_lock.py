"""Admin host policy; independent of telemetry and whole-device remote locks."""
from fastapi import HTTPException


def host_lock_snapshot(config: dict) -> dict:
    return {key: config.get(key, False if key.endswith('_enabled') else None)
            for sid in (1, 2)
            for key in (f'serverhost{sid}', f'port{sid}', f'server{sid}_enabled')}


def check_host_lock(device, changes: dict, *, is_admin: bool = False) -> None:
    policy = getattr(device, 'server_config_lock', None) or {}
    if not policy or is_admin:
        return
    current = device.service_config if isinstance(device.service_config, dict) else {}
    for raw_key, value in changes.items():
        aliases = {f'host{sid}': f'serverhost{sid}' for sid in (1, 2)}
        aliases.update({f'serverport{sid}': f'port{sid}' for sid in (1, 2)})
        aliases.update({'target_host': 'serverhost1', 'target_port': 'port1'})
        key = aliases.get(raw_key, raw_key)
        if key in {'stream_active', 'stream_on_demand', 'server1_stream_active', 'server2_stream_active', 'server1_stream_on_demand', 'server2_stream_on_demand'} and value != current.get(key):
            raise HTTPException(403, 'Chế độ gửi của server do admin quản lý khi khóa host.')
        if key in policy and value != policy[key]:
            raise HTTPException(403, 'Host và trạng thái server do admin khóa. Bạn không được thay đổi.')
        for sid in (1, 2):
            # A blocked slot cannot be reconfigured, including credentials/mountpoint.
            if not policy.get(f'server{sid}_enabled') and (
                key in {f'ntrip_version{sid}', f'username{sid}', f'mountpoint{sid}', f'password{sid}'}
                or key.startswith(f'server{sid}_')
            ) and value != current.get(key):
                raise HTTPException(403, f'Server {sid} bị admin chặn gửi dữ liệu.')


def refresh_host_lock(device, config: dict) -> None:
    if getattr(device, 'server_config_lock', None):
        device.server_config_lock = host_lock_snapshot(config)
