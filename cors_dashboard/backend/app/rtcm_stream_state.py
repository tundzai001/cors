"""Pure helpers for resolving per-server RTCM stream state.

The legacy top-level ``stream_on_demand`` and ``stream_active`` fields are
aggregate compatibility flags.  They may both be true in a valid mixed setup
(for example S1 On-Demand while S2 is Always-On), so control-plane decisions
must be made from the per-server fields instead.
"""

from __future__ import annotations

from .schemas import service_server_ids

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def normalize_mountpoint(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip().strip("/")
    return cleaned.lower() if cleaned else None


def has_per_server_stream_state(config: Mapping[str, Any] | None) -> bool:
    cfg = config if isinstance(config, Mapping) else {}
    return any(
        cfg.get(f"server{sid}_{field}") is not None
        for sid in service_server_ids(cfg)
        for field in ("stream_on_demand", "stream_active")
    )


@dataclass(frozen=True)
class ServerStreamState:
    server_id: int
    enabled: bool
    on_demand: bool
    active: bool
    mountpoint: Optional[str]

    @property
    def can_push(self) -> bool:
        return self.enabled and ((not self.on_demand) or self.active)


def get_server_stream_state(config: Mapping[str, Any] | None, server_id: int) -> ServerStreamState:
    if type(server_id) is not int or server_id <= 0:
        raise ValueError("server_id must be a positive integer")

    cfg = config if isinstance(config, Mapping) else {}
    global_on_demand = _as_bool(cfg.get("stream_on_demand"), False)
    global_active = _as_bool(cfg.get("stream_active"), False)
    has_explicit_server_state = has_per_server_stream_state(cfg)

    on_demand_key = f"server{server_id}_stream_on_demand"
    active_key = f"server{server_id}_stream_active"

    on_demand = _as_bool(cfg.get(on_demand_key), global_on_demand)
    # Always-On is effectively active regardless of a stale per-server active
    # flag. For On-Demand streams, the active flag represents a live wake.
    active_fallback = False if has_explicit_server_state else global_active
    active = True if not on_demand else _as_bool(cfg.get(active_key), active_fallback)

    return ServerStreamState(
        server_id=server_id,
        enabled=_as_bool(cfg.get(f"server{server_id}_enabled"), False),
        on_demand=on_demand,
        active=active,
        mountpoint=normalize_mountpoint(cfg.get(f"mountpoint{server_id}")),
    )


def active_on_demand_server_streams(
    config: Mapping[str, Any] | None,
) -> Tuple[ServerStreamState, ...]:
    """Return only enabled On-Demand streams that are currently awake.

    Always-On servers are deliberately excluded: they never belong to the
    RTCM session reaper even though they keep the aggregate stream active.
    """

    states = (get_server_stream_state(config, sid) for sid in service_server_ids(config or {}))
    return tuple(
        state
        for state in states
        if state.enabled and state.on_demand and state.active and state.mountpoint
    )


def collect_reapable_server_streams(
    config: Mapping[str, Any] | None,
    legacy_active_mountpoint: Any = None,
) -> Tuple[Tuple[Optional[int], str], ...]:
    """Resolve session-reaper targets without mixing aggregate server state."""

    return tuple(_iter_reapable_server_streams(config, legacy_active_mountpoint))


def _iter_reapable_server_streams(config, legacy_active_mountpoint):
    cfg = config if isinstance(config, Mapping) else {}
    active_streams = active_on_demand_server_streams(cfg)
    if has_per_server_stream_state(cfg):
        yield from ((state.server_id, state.mountpoint) for state in active_streams if state.mountpoint)
        return

    active_mountpoint = normalize_mountpoint(legacy_active_mountpoint)
    if not active_mountpoint:
        return

    for state in active_streams:
        if state.mountpoint == active_mountpoint:
            yield state.server_id, active_mountpoint
            return

    legacy_state = get_server_stream_state(cfg, 1)
    if not (legacy_state.on_demand and legacy_state.active):
        return

    resolved_server_id: Optional[int] = None
    for server_id in service_server_ids(cfg):
        if normalize_mountpoint(cfg.get(f"mountpoint{server_id}")) == active_mountpoint:
            resolved_server_id = server_id
            break
    yield resolved_server_id, active_mountpoint


def _target_stream_server_ids(cfg: Dict[str, Any], active: bool, mountpoint_norm: str, server_id: Any):
    configured_ids = service_server_ids(cfg)
    try:
        parsed_server_id = int(server_id) if server_id is not None else None
    except (TypeError, ValueError):
        parsed_server_id = None
    if parsed_server_id not in configured_ids:
        parsed_server_id = None
    if parsed_server_id is not None:
        return (parsed_server_id,)
    if mountpoint_norm:
        return tuple(
            sid
            for sid in configured_ids
            if normalize_mountpoint(cfg.get(f"mountpoint{sid}")) == mountpoint_norm
        )
    return configured_ids if not bool(active) else ()


def _set_stream_server_state(cfg: Dict[str, Any], sid: int, active: bool) -> None:
    state = get_server_stream_state(cfg, sid)
    on_demand_key = f"server{sid}_stream_on_demand"
    active_key = f"server{sid}_stream_active"
    if bool(active):
        # A wake must not silently convert an explicit Always-On server to
        # On-Demand. Preserve its mode and only align the active flag.
        cfg[on_demand_key] = state.on_demand
        cfg[active_key] = True
    elif state.on_demand:
        cfg[on_demand_key] = True
        cfg[active_key] = False


def _update_stream_aggregate_state(cfg: Dict[str, Any], active: bool, mountpoint: Any, mountpoint_norm: str):
    if bool(active) and mountpoint_norm:
        cfg["active_mountpoint"] = str(mountpoint).strip()

    server_states = tuple(get_server_stream_state(cfg, sid) for sid in service_server_ids(cfg))
    cfg["stream_on_demand"] = any(state.enabled and state.on_demand for state in server_states)
    cfg["stream_active"] = any(state.can_push for state in server_states)

    if not bool(active):
        current_active_mp = normalize_mountpoint(cfg.get("active_mountpoint"))
        if not mountpoint_norm or current_active_mp == mountpoint_norm:
            remaining_active_streams = active_on_demand_server_streams(cfg)
            if not any(state.mountpoint == current_active_mp for state in remaining_active_streams):
                cfg.pop("active_mountpoint", None)

    return cfg


def apply_stream_control_state(
    config: Mapping[str, Any] | None,
    active: bool,
    mountpoint: Any = None,
    server_id: Any = None,
) -> Dict[str, Any]:
    """Apply one stream command to config and recompute aggregate flags."""

    cfg: Dict[str, Any] = dict(config) if isinstance(config, Mapping) else {}
    mountpoint_norm = normalize_mountpoint(mountpoint)
    target_server_ids = _target_stream_server_ids(cfg, active, mountpoint_norm, server_id)
    for sid in target_server_ids:
        _set_stream_server_state(cfg, sid, active)
    return _update_stream_aggregate_state(cfg, active, mountpoint, mountpoint_norm)


def build_stream_control_payload(
    active: bool,
    mountpoint: Any = None,
    address: Any = None,
    server_id: Any = None,
) -> Dict[str, Any]:
    """Build a canonical command while retaining aliases for older agents."""

    payload: Dict[str, Any] = {"active": bool(active)}

    mountpoint_text = str(mountpoint or "").strip()
    if mountpoint_text:
        payload["mountpoint"] = mountpoint_text

    address_text = str(address or "").strip()
    if address_text:
        payload["address"] = address_text
        payload["caster_host"] = address_text

    try:
        parsed_server_id = int(server_id) if server_id is not None else None
    except (TypeError, ValueError):
        parsed_server_id = None
    if parsed_server_id is not None and parsed_server_id > 0:
        payload["server_id"] = parsed_server_id
        payload["server_idx"] = parsed_server_id

    return payload
