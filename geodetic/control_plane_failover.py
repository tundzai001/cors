"""Thread-safe control-plane ACK tracking for the GNSS agent.

The MQTT socket being connected only proves that the broker is reachable.  It
does not prove that the dashboard backend event loop is still processing
agent messages.  This module therefore tracks application-level ACKs for the
same status probe over both WebSocket and MQTT.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Mapping


VALID_CHANNELS = ("websocket", "mqtt")


def resolve_effective_stream_switch(
    *,
    enabled: bool,
    on_demand: bool,
    active: bool,
    fail_open: bool,
) -> dict[str, bool]:
    """Overlay fail-open without changing the configured stream flags."""
    configured_can_push = bool(enabled and ((not on_demand) or active))
    fail_open_forced = bool(fail_open and enabled and not configured_can_push)
    return {
        "configured_can_push": configured_can_push,
        "fail_open_forced": fail_open_forced,
        "can_push": configured_can_push or fail_open_forced,
    }


@dataclass(frozen=True)
class FailoverTransition:
    changed: bool
    active: bool
    reason: str


class ControlPlaneFailover:
    """Decide when both backend response paths are stale.

    A probe is the agent's regular status packet.  ACKs are accepted only for
    this process boot and for a recently recorded sequence.  Once fail-open is
    active, a delayed ACK from before the transition cannot recover the agent.
    """

    def __init__(
        self,
        boot_id: str,
        *,
        stale_after_seconds: float = 45.0,
        recovery_after_seconds: float = 5.0,
        probe_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.boot_id = str(boot_id)
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self.recovery_after_seconds = max(0.0, float(recovery_after_seconds))
        self.probe_ttl_seconds = max(
            self.stale_after_seconds,
            float(probe_ttl_seconds or self.stale_after_seconds * 2.0),
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._armed_at: float | None = None
        self._probes: dict[int, float] = {}
        self._latest_probe_sequence = -1
        self._last_ack_at: dict[str, float | None] = {
            "websocket": None,
            "mqtt": None,
        }
        self._last_ack_sequence: dict[str, int | None] = {
            "websocket": None,
            "mqtt": None,
        }
        self._active = False
        self._entered_at: float | None = None
        self._entered_after_sequence = -1
        self._recovery_since: float | None = None
        self._last_reason = "waiting_for_first_probe"

    @staticmethod
    def _coerce_sequence(value: object) -> int | None:
        try:
            sequence = int(value)
        except (TypeError, ValueError):
            return None
        return sequence if sequence >= 0 else None

    def record_probe(self, sequence: object, *, now: float | None = None) -> bool:
        parsed = self._coerce_sequence(sequence)
        if parsed is None:
            return False
        current = self._clock() if now is None else float(now)
        with self._lock:
            if self._armed_at is None:
                self._armed_at = current
                self._last_reason = "startup_grace"
            self._probes[parsed] = current
            self._latest_probe_sequence = max(self._latest_probe_sequence, parsed)
            cutoff = current - self.probe_ttl_seconds
            for old_sequence, sent_at in tuple(self._probes.items()):
                if sent_at < cutoff:
                    self._probes.pop(old_sequence, None)
        return True

    def record_ack(
        self,
        channel: str,
        payload: Mapping[str, object] | None,
        *,
        retained: bool = False,
        now: float | None = None,
    ) -> bool:
        normalized_channel = str(channel or "").strip().lower()
        if retained or normalized_channel not in VALID_CHANNELS or not isinstance(payload, Mapping):
            return False
        if str(payload.get("status_boot_id") or "") != self.boot_id:
            return False
        sequence = self._coerce_sequence(payload.get("status_sequence"))
        if sequence is None:
            return False

        current = self._clock() if now is None else float(now)
        with self._lock:
            sent_at = self._probes.get(sequence)
            if sent_at is None or current - sent_at >= self.stale_after_seconds:
                return False
            previous_sequence = self._last_ack_sequence[normalized_channel]
            if previous_sequence is not None and sequence <= previous_sequence:
                return False
            # A queued pre-failure ACK must not make a stalled backend look
            # recovered when its event loop finally drains an old backlog.
            if self._active and sequence <= self._entered_after_sequence:
                return False
            self._last_ack_at[normalized_channel] = current
            self._last_ack_sequence[normalized_channel] = sequence
            if self._active and self._recovery_since is None:
                self._recovery_since = current
        return True

    def _channel_is_stale(self, channel: str, now: float) -> bool:
        baseline = self._last_ack_at[channel]
        if baseline is None:
            baseline = self._armed_at
        return baseline is None or (now - baseline) >= self.stale_after_seconds

    def evaluate(self, *, now: float | None = None) -> FailoverTransition:
        current = self._clock() if now is None else float(now)
        with self._lock:
            if self._armed_at is None:
                return FailoverTransition(False, self._active, "waiting_for_first_probe")

            stale_channels = [
                channel for channel in VALID_CHANNELS
                if self._channel_is_stale(channel, current)
            ]
            both_stale = len(stale_channels) == len(VALID_CHANNELS)

            if not self._active and both_stale:
                self._active = True
                self._entered_at = current
                self._entered_after_sequence = self._latest_probe_sequence
                self._recovery_since = None
                self._last_reason = "websocket_and_mqtt_ack_stale"
                return FailoverTransition(True, True, self._last_reason)

            if self._active:
                eligible_recovery_ack = any(
                    not self._channel_is_stale(channel, current)
                    and self._last_ack_sequence[channel] is not None
                    and self._last_ack_sequence[channel] > self._entered_after_sequence
                    for channel in VALID_CHANNELS
                )
                if eligible_recovery_ack:
                    if self._recovery_since is None:
                        self._recovery_since = current
                    if current - self._recovery_since >= self.recovery_after_seconds:
                        self._active = False
                        self._entered_at = None
                        self._recovery_since = None
                        self._last_reason = "fresh_backend_ack_received"
                        return FailoverTransition(True, False, self._last_reason)
                    self._last_reason = "recovery_confirmation"
                else:
                    self._recovery_since = None
                    self._last_reason = "websocket_and_mqtt_ack_stale"
            else:
                self._last_reason = (
                    "control_plane_healthy" if not stale_channels
                    else f"degraded_{stale_channels[0]}_ack_stale"
                )

            return FailoverTransition(False, self._active, self._last_reason)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def snapshot(self, *, now: float | None = None) -> dict:
        current = self._clock() if now is None else float(now)
        with self._lock:
            def age(channel: str) -> float | None:
                ack_at = self._last_ack_at[channel]
                return None if ack_at is None else round(max(0.0, current - ack_at), 1)

            return {
                "active": self._active,
                "reason": self._last_reason,
                "stale_after_seconds": self.stale_after_seconds,
                "recovery_after_seconds": self.recovery_after_seconds,
                "armed": self._armed_at is not None,
                "entered_at_monotonic": self._entered_at,
                "latest_probe_sequence": self._latest_probe_sequence,
                "last_ack_sequence": dict(self._last_ack_sequence),
                "ack_age_seconds": {
                    "websocket": age("websocket"),
                    "mqtt": age("mqtt"),
                },
            }
