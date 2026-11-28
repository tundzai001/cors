"""Deterministic ordering for device status arriving over multiple transports."""
from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional


SOURCE_PRIORITY = {
    "disconnect": 5,
    "heartbeat": 10,
    "mqtt": 20,
    "websocket": 30,
}


def _as_timestamp(value: Any) -> Optional[float]:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return timestamp


def normalize_station_snapshot(item: dict, *, now: Optional[float] = None) -> dict:
    """Match dashboard offline detection without mutating the map cache."""
    now = time.time() if now is None else now
    timestamp = _as_timestamp(item.get("timestamp"))
    stale = timestamp is not None and now - timestamp > 180
    if str(item.get("status") or "").lower() != "offline" and not stale:
        return dict(item)
    snapshot = {
        **item, "status": "offline", "bps": 0,
        "ntrip_connected": False, "ntrip_status": {},
    }
    if stale and str(item.get("status") or "").lower() != "offline":
        snapshot["offline_since"] = int(timestamp + 180)
    return snapshot


def _as_sequence(value: Any) -> Optional[int]:
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        return None
    return sequence if sequence >= 0 else None


@dataclass
class StatusState:
    boot_id: Optional[str]
    sequence: Optional[int]
    agent_timestamp: Optional[float]
    received_at: float
    source: str
    source_priority: int
    status: str
    revision: int


@dataclass
class StatusDecision:
    accepted: bool
    payload: Optional[dict]
    reason: str
    revision: int = 0


class StatusReconciler:
    def __init__(self, *, retained_online_max_age: float = 30.0, max_states: int = 10000) -> None:
        self.retained_online_max_age = retained_online_max_age
        self.max_states = max(100, int(max_states))
        self._states: OrderedDict[str, StatusState] = OrderedDict()
        self._retired_boot_ids: dict[str, set[str]] = {}
        self._accepted_count = 0
        self._rejected_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _reject(self, reason: str, revision: int = 0) -> StatusDecision:
        self._rejected_counts[reason] = self._rejected_counts.get(reason, 0) + 1
        return StatusDecision(False, None, reason, revision)

    @staticmethod
    def _normalized_status_serial(payload):
        serial = str(payload.get("serial") or "").strip()
        if not serial or len(serial) > 128:
            return None
        if any(ord(char) < 33 or ord(char) > 126 for char in serial):
            return None
        return serial

    @staticmethod
    def _normalized_agent_timestamp(payload):
        timestamp = _as_timestamp(payload.get("agent_timestamp", payload.get("timestamp")))
        # ESP uptime is neither an absolute date nor a safe ordering key across boots.
        if timestamp is not None and (
            not math.isfinite(timestamp) or timestamp < 1_000_000_000
        ):
            return None
        return timestamp

    def _normalize_status_input(self, payload, source, retained, received_at):
        if not isinstance(payload, dict):
            return self._reject("invalid_payload")
        serial = self._normalized_status_serial(payload)
        if serial is None:
            return self._reject("missing_serial")

        now = float(received_at if received_at is not None else time.time())
        source_name = str(source or "unknown").lower()
        priority = SOURCE_PRIORITY.get(source_name, 0)
        boot_id = str(payload.get("status_boot_id") or "").strip() or None
        sequence = _as_sequence(payload.get("status_sequence"))
        agent_timestamp = self._normalized_agent_timestamp(payload)
        status = str(payload.get("status") or "").strip().lower()
        event = str(payload.get("status_event") or "").strip().lower()
        if not status:
            return self._reject("missing_status")
        if agent_timestamp is not None and agent_timestamp > now + 120:
            return self._reject("future_agent_timestamp")
        return {
            "payload": payload,
            "serial": serial,
            "now": now,
            "source_name": source_name,
            "priority": priority,
            "boot_id": boot_id,
            "sequence": sequence,
            "agent_timestamp": agent_timestamp,
            "status": status,
            "event": event,
            "retained": retained,
        }

    def _reject_boot_order(self, serial, previous, boot_id, agent_timestamp, retained, status, now):
        retired = self._retired_boot_ids.get(serial, set())
        if boot_id and boot_id in retired:
            return self._reject("retired_boot", previous.revision)
        older_boot = (
            boot_id and previous.boot_id and boot_id != previous.boot_id
            and agent_timestamp and previous.agent_timestamp
            and agent_timestamp < previous.agent_timestamp - 2.0
        )
        if older_boot:
            return self._reject("older_boot_timestamp", previous.revision)
        previous_is_fresh = now - previous.received_at <= self.retained_online_max_age
        if retained and previous_is_fresh and (boot_id != previous.boot_id or status != previous.status):
            return self._reject("retained_over_fresh_state", previous.revision)
        return None

    def _reject_same_boot_sequence(self, previous, boot_id, sequence, priority, event, status):
        same_boot = bool(boot_id and previous.boot_id and boot_id == previous.boot_id)
        explicit_lwt = event in {"lwt", "cleanup"} and status == "offline"
        has_sequences = sequence is not None and previous.sequence is not None
        if not (same_boot and has_sequences and not explicit_lwt):
            return None
        if sequence < previous.sequence:
            return self._reject("out_of_order_sequence", previous.revision)
        if sequence == previous.sequence and priority <= previous.source_priority:
            return self._reject("duplicate_sequence", previous.revision)
        return None

    def _reject_timestamp_order(self, previous, boot_id, agent_timestamp, status, priority):
        if boot_id and previous.boot_id:
            return None
        if not (agent_timestamp and previous.agent_timestamp):
            return None
        if agent_timestamp < previous.agent_timestamp - 0.5:
            return self._reject("out_of_order_timestamp", previous.revision)
        is_duplicate = (
            abs(agent_timestamp - previous.agent_timestamp) <= 0.001
            and status == previous.status
            and priority <= previous.source_priority
        )
        if is_duplicate:
            return self._reject("duplicate_timestamp", previous.revision)
        return None

    def _record_retired_boot(self, serial, previous, boot_id):
        if not (boot_id and previous.boot_id and boot_id != previous.boot_id):
            return
        retired = self._retired_boot_ids.setdefault(serial, set())
        retired.add(previous.boot_id)
        if len(retired) > 8:
            retired.pop()

    def _prior_status_rejection(self, serial, previous, fields):
        if previous is None:
            return None
        reason = self._reject_boot_order(
            serial, previous, fields["boot_id"], fields["agent_timestamp"],
            fields["retained"], fields["status"], fields["now"],
        )
        if reason:
            return reason
        reason = self._reject_same_boot_sequence(
            previous, fields["boot_id"], fields["sequence"], fields["priority"],
            fields["event"], fields["status"],
        )
        if reason:
            return reason
        reason = self._reject_timestamp_order(
            previous, fields["boot_id"], fields["agent_timestamp"],
            fields["status"], fields["priority"],
        )
        if reason:
            return reason
        self._record_retired_boot(serial, previous, fields["boot_id"])
        return None

    def _stale_retained_status(self, fields) -> bool:
        retained_online = fields["retained"] and fields["status"] not in {"offline", "locked"}
        return (
            retained_online
            and (
                not fields["agent_timestamp"]
                or fields["now"] - fields["agent_timestamp"] > self.retained_online_max_age
            )
        )

    @staticmethod
    def _accepted_status_payload(fields, revision):
        normalized = dict(fields["payload"])
        normalized.update(
            serial=fields["serial"],
            timestamp=int(fields["now"]),
            server_received_at=fields["now"],
            status_revision=revision,
            status_source=fields["source_name"],
            agent_timestamp=fields["agent_timestamp"],
        )
        return normalized

    @staticmethod
    def _accepted_status_sequence(fields, previous):
        stored_sequence = fields["sequence"]
        if previous and fields["boot_id"] == previous.boot_id and fields["event"] in {"lwt", "cleanup"}:
            stored_sequence = previous.sequence
        if previous and fields["source_name"] in {"heartbeat", "disconnect"}:
            stored_sequence = previous.sequence
        return stored_sequence

    def _store_accepted_status(self, serial, fields, previous, revision, stored_sequence):
        self._states[serial] = StatusState(
            boot_id=fields["boot_id"] or (previous.boot_id if previous else None),
            sequence=stored_sequence,
            agent_timestamp=fields["agent_timestamp"] or (previous.agent_timestamp if previous else None),
            received_at=fields["now"],
            source=fields["source_name"],
            source_priority=fields["priority"],
            status=fields["status"],
            revision=revision,
        )
        self._states.move_to_end(serial)
        while len(self._states) > self.max_states:
            evicted_serial, _ = self._states.popitem(last=False)
            self._retired_boot_ids.pop(evicted_serial, None)
        self._accepted_count += 1

    def _accept_status_locked(self, fields):
        serial = fields["serial"]
        previous = self._states.get(serial)
        if self._stale_retained_status(fields):
            return self._reject("stale_retained_online", previous.revision if previous else 0)
        rejection = self._prior_status_rejection(serial, previous, fields)
        if rejection:
            return rejection

        revision = previous.revision + 1 if previous else 1
        normalized = self._accepted_status_payload(fields, revision)
        stored_sequence = self._accepted_status_sequence(fields, previous)
        self._store_accepted_status(serial, fields, previous, revision, stored_sequence)
        return StatusDecision(True, normalized, "accepted", revision)

    async def accept(
        self,
        payload: dict,
        *,
        source: str,
        retained: bool = False,
        received_at: Optional[float] = None,
    ) -> StatusDecision:
        fields = self._normalize_status_input(payload, source, retained, received_at)
        if isinstance(fields, StatusDecision):
            return fields
        async with self._lock:
            return self._accept_status_locked(fields)

    async def has_update_after(self, serial: str, observed_at: float) -> bool:
        async with self._lock:
            state = self._states.get(str(serial))
            return bool(state and state.received_at > observed_at)

    async def is_fresh(self, serial: str, max_age: float, *, now: Optional[float] = None) -> bool:
        check_at = float(now if now is not None else time.time())
        async with self._lock:
            state = self._states.get(str(serial))
            return bool(state and check_at - state.received_at <= max_age and state.status != "offline")

    async def current_status(self, serial: str) -> Optional[str]:
        async with self._lock:
            state = self._states.get(str(serial))
            return state.status if state else None

    async def metrics_snapshot(self) -> dict:
        """Small operational snapshot; never exposes station payload contents."""
        now = time.time()
        async with self._lock:
            fresh_states = sum(
                1
                for state in self._states.values()
                if now - state.received_at <= self.retained_online_max_age
            )
            return {
                "tracked_stations": len(self._states),
                "fresh_stations": fresh_states,
                "accepted_total": self._accepted_count,
                "rejected_total": sum(self._rejected_counts.values()),
                "rejected_by_reason": dict(sorted(self._rejected_counts.items())),
                "retained_online_max_age_sec": self.retained_online_max_age,
            }

    async def accept_offline(self, serial: str, *, source: str, received_at: Optional[float] = None) -> StatusDecision:
        return await self.accept(
            {"serial": str(serial), "status": "offline", "status_event": source},
            source=source,
            received_at=received_at,
        )

    async def reset(self) -> None:
        async with self._lock:
            self._states.clear()
            self._retired_boot_ids.clear()
            self._accepted_count = 0
            self._rejected_counts.clear()


status_reconciler = StatusReconciler()
