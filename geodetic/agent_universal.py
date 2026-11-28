#agent_universal.py
AGENT_VERSION = "V2.4.2"
UNICORE_LOG_LIST_KEY = b'#UNILOGLIST'
UNICORE_AGCA_MESSAGE = '#AGCA'
UNICORE_BASE_MODE_PREFIX = 'MODE BASE'
UNICORE_VERSION_COMMAND = b'version\r\n'

import asyncio
import copy
import gc
import base64
from collections import OrderedDict, deque
import hashlib
import json
import logging
import os
import re
import socket
import select
import sys
import threading
import time
import uuid
import platform
import signal
import statistics
import sqlite3
import struct
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from queue import Queue, Empty, Full
from pathlib import Path
from typing import Callable, Mapping

import paho.mqtt.client as mqtt
import serial
import serial.tools.list_ports
import websockets
import psutil
import random 
import unicodedata
import urllib.parse
import urllib.request


# --- CONTROL-PLANE FAILOVER ---
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
    """Decide when both backend response paths are stale."""

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
HAS_RTCM_DECODER = True



# --- PLATFORM DETECTION ---
IS_WINDOWS = platform.system() == "Windows"
IS_RASPBERRY_PI = platform.system() == "Linux" and os.path.exists('/proc/cpuinfo')

if IS_WINDOWS:
    import subprocess
    from logging.handlers import RotatingFileHandler

# --- PATH CONFIGURATION ---
if IS_WINDOWS:
    user_home = os.path.expanduser("~")
    BASE_DIR = os.path.join(user_home, "GeodeticAgent")
elif IS_RASPBERRY_PI:
    try:
        user_home = os.path.expanduser(f"~{os.environ.get('SUDO_USER', os.environ.get('USER'))}")
    except KeyError:
        user_home = os.path.expanduser("~")
    BASE_DIR = os.path.join(user_home, "geodetic")
else:
    BASE_DIR = os.path.expanduser("~/geodetic")

os.makedirs(BASE_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(BASE_DIR, "agent_config.json")
LICENSE_PATH = os.path.join(BASE_DIR, "license.key")
LOCK_FILE_PATH = os.path.join(BASE_DIR, "agent.lock")
REMOTE_LOCK_PATH = os.path.join(BASE_DIR, "remote.lock") 

# --- LOGGING CONFIGURATION ---
if IS_WINDOWS:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    LOG_FILE_PATH = os.path.join(BASE_DIR, "agent.log")
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    
    class SafeStreamHandler(logging.StreamHandler):
        def emit(self, record):
            try:
                super().emit(record)
            except UnicodeEncodeError:
                record.msg = str(record.msg).encode('utf-8', 'replace').decode('ascii', 'replace')
                super().emit(record)
                
    console_handler = SafeStreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    if logging.root.hasHandlers():
        logging.root.handlers.clear()
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONNECTION CONFIGURATION ---
DEFAULT_BACKEND_HOST = "aitogy.click"
DEFAULT_MQTT_BROKER = "aitogy.click"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_USERNAME = "mqttUser"
DEFAULT_MQTT_PASSWORD = "MqttPassword123$%^"

BACKEND_HOST = DEFAULT_BACKEND_HOST
BACKEND_WS_HOST = os.getenv("BACKEND_WS_HOST", "base.aitogy.com").strip()
BACKEND_WS_SCHEME = os.getenv("BACKEND_WS_SCHEME", "wss").strip().lower()
BACKEND_WS_PORT = int(os.getenv("BACKEND_WS_PORT", "443" if BACKEND_WS_SCHEME == "wss" else "8000"))
ALLOW_INSECURE_AGENT_WS = os.getenv("ALLOW_INSECURE_AGENT_WS", "false").strip().lower() in {"1", "true", "yes"}
MQTT_BROKER = DEFAULT_MQTT_BROKER
MQTT_PORT = DEFAULT_MQTT_PORT
MQTT_USERNAME = DEFAULT_MQTT_USERNAME
MQTT_PASSWORD = DEFAULT_MQTT_PASSWORD
DEFAULT_BAUDRATE = 115200 if IS_RASPBERRY_PI else 460800
STATUS_PUBLISH_INTERVAL_SECONDS = 5
CONTROL_PLANE_STALE_SECONDS = max(15.0, float(os.getenv("CONTROL_PLANE_STALE_SECONDS", "45")))
CONTROL_PLANE_RECOVERY_SECONDS = max(0.0, float(os.getenv("CONTROL_PLANE_RECOVERY_SECONDS", "5")))
CONTROL_PLANE_CHECK_INTERVAL_SECONDS = max(1.0, float(os.getenv("CONTROL_PLANE_CHECK_INTERVAL_SECONDS", "2")))
NMEA_IDLE_PUBLISH_INTERVAL_SECONDS = 1.0
NMEA_MQTT_PUBLISH_INTERVAL_SECONDS = 1.0
SYSTEM_INFO_INTERVAL_SECONDS = 30
# Force-enable parser debug in code (no environment variable required).
PARSER_DEBUG_ENABLED = False
PARSER_DEBUG_INTERVAL_SECONDS = 2.0

# --- GLOBAL VARIABLES ---
AGENT_START_TS = time.time()
STATUS_BOOT_ID = uuid.uuid4().hex
STATUS_SEQUENCE = 0
MACHINE_SERIAL = ""
rtcm_subscribers = []
nmea_subscribers = []
subscriber_lock = threading.Lock()
serial_port_lock = threading.Lock()
telemetry_lock = threading.Lock()
current_state = "INITIALIZING"
active_websocket_connection = None
control_plane_fail_open_event = threading.Event()
is_remotely_locked = False 
command_execution_lock = asyncio.Lock()
initialization_complete = asyncio.Event()
remote_unlock_event = asyncio.Event()
last_command_result = {}
seen_command_ids = OrderedDict()
transport_status = {
    "mqtt_connected": False,
    "websocket_connected": False,
    "active_channel": None,
    "mqtt_broker": MQTT_BROKER,
    "websocket_host": BACKEND_HOST,
    "last_mqtt_connect_ts": 0,
    "last_mqtt_disconnect_ts": 0,
    "last_websocket_connect_ts": 0,
    "last_websocket_disconnect_ts": 0,
}
reconnect_counters = {
    "mqtt": 0,
    "websocket": 0,
    "serial": 0,
    "ntrip_server1": 0,
    "ntrip_server2": 0,
    "ntrip_client": 0,
}
queue_metrics = {
    "rtcm_dispatch_drops": 0,
    "nmea_dispatch_drops": 0,
    "rtcm_inject_drops": 0,
    "nmea_mqtt_published": 0,
    "nmea_mqtt_publish_failures": 0,
    "nmea_ws_forwarded": 0,
    "nmea_filtered": 0,
}
system_info_cache_lock = threading.Lock()
system_info_cache = {"timestamp": 0.0, "payload": {}}
# Last parsed GGA fix status (updated by NMEA dispatcher)
LAST_GGA_FIX_STATUS = "NO_FIX"
LAST_GGA_COORD = None
LAST_RAW_GGA = None
LAST_RAW_GGA_TS = 0.0
AUTO_BASE_PROGRESS = {}
LAST_HPPOSLLH_COORD = None
LAST_HPPOSLLH_TS = 0.0
LAST_UBX_NUMSV = None
LAST_UBX_NUMSV_TS = 0.0
LAST_RECEIVER_QUALITY = None
LAST_RECEIVER_QUALITY_TS = 0.0
LAST_NMEA_TYPE_COUNTS = {}
LAST_GST_HACC_M = None
LAST_GST_SEMI_MAJOR_M = None
LAST_GST_SEMI_MINOR_M = None
LAST_GST_TS = 0.0
LAST_GSV_AVG_SNR = None
LAST_GSV_SNR_SAMPLES = 0
LAST_GSV_TS = 0.0
LAST_CONFIGURED_OUTPUT_MODE = None
nmea_rate_limit_counter = 0
nmea_rate_limit_reset_ts = time.time()


def _ubx_navigation_quality(payload):
    """NAV-PVT units and validity, UBX-22008968. Time-only is not RTK Fixed."""
    if len(payload) != 92 or payload[20] > 5:
        raise ValueError("Invalid NAV-PVT payload")
    valid = bool(payload[21] & 1) and payload[20] in (2, 3, 4, 5) and not (payload[78] & 1)
    return {'source':'ubx_nav_pvt', 'fix_valid':valid, 'fix_type':payload[20],
                'satellites':payload[23], 'pdop':struct.unpack_from('<H', payload, 76)[0] * 0.01,
                'hacc_m':struct.unpack_from('<I', payload, 40)[0] * 0.001 if valid else None}


def _ubx_dop(payload):
    if len(payload) != 18:
        raise ValueError('Invalid NAV-DOP payload')
    return {'source':'ubx_nav_dop', 'pdop':struct.unpack_from('<H', payload, 6)[0] * 0.01,
                'hdop':struct.unpack_from('<H', payload, 12)[0] * 0.01}


def _ubx_satellite_payload(payload):
    if len(payload) < 8 or payload[4] != 1 or payload[5] > 128 or len(payload) != 8 + payload[5] * 12:
        raise ValueError('Invalid NAV-SAT payload')
    keys = [(payload[i], payload[i + 1]) for i in range(8, len(payload), 12)]
    known = [key for key in keys if key != (6, 255)]  # GLONASS may report an unassigned slot.
    if any(gnss > 7 or sv == 0 for gnss, sv in keys) or len(set(known)) != len(known):
        raise ValueError('Invalid NAV-SAT satellite identifiers')
    return {'source':'ubx_nav_sat', 'payload_b64':base64.b64encode(payload).decode('ascii')}


def _ubx_rf_payload(payload, message_id):
    size = {0x38: 24, 0x31: 272}.get(message_id)
    if size is None or len(payload) < 4 or payload[0] != 0 or not 1 <= payload[1] <= 4 or len(payload) != 4 + payload[1] * size:
        raise ValueError(f'Invalid RF payload: id={message_id:02x} bytes={len(payload)} header={payload[:4].hex()}')
    # Older F9P firmware repeats blockId=0 (HPG 1.30 release note).
    # RF counts are raw U2 values; semantic warnings must not discard other flags.
    if message_id == 0x31:
        for offset in range(4, len(payload), size):
            span, resolution, center = struct.unpack_from('<III', payload, offset + 256)
            if not span or not resolution or not center:
                raise ValueError('Invalid MON-SPAN frequencies')
    return {'source':'ubx_mon_rf' if message_id == 0x38 else 'ubx_mon_span',
                'payload_b64':base64.b64encode(payload).decode('ascii')}


def _ubx_security_payload(payload, message_id):
    # HPG 1.51 interface: SEC-SIG v2 and SEC-SIGLOG v1.
    if message_id == 9:
        if len(payload) < 4 or payload[0] != 2 or payload[3] > 16 or len(payload) != 4 + payload[3] * 4:
            raise ValueError('Invalid SEC-SIG payload')
        flags = payload[1]
        jam, spoof = (flags >> 1) & 3, (flags >> 4) & 7
        if jam > 2 or spoof > 3:
            raise ValueError('Unknown SEC-SIG state')
        frequencies = []
        for offset in range(4, len(payload), 4):
            value = int.from_bytes(payload[offset:offset+4], 'little')
            khz = value & 0xffffff
            if not khz or any(row['khz'] == khz for row in frequencies):
                raise ValueError('Invalid SEC-SIG frequency')
            frequencies.append({'khz':khz, 'jammed':bool(value & (1 << 24))})
        result = {'source':'ubx_sec_sig', 'jam_enabled':bool(flags & 1), 'jam_state':jam,
                      'spoof_enabled':bool(flags & 8), 'spoof_state':spoof, 'frequencies':frequencies}
    elif message_id == 0x10:
        if len(payload) < 8 or payload[0] != 1 or payload[1] > 16 or len(payload) != 8 + payload[1] * 8:
            raise ValueError('Invalid SEC-SIGLOG payload')
        result = {'source':'ubx_sec_siglog', 'events':[{
            'elapsed_seconds':int.from_bytes(payload[o:o+4], 'little'), 'detection':payload[o+4], 'event':payload[o+5]}
            for o in range(8, len(payload), 8)]}
    else:
        raise ValueError('Unsupported security message')
    return dict(result, payload_b64=base64.b64encode(payload).decode('ascii'))


def _ubx_version_payload(payload):
    if not 40 <= len(payload) <= 520 or (len(payload) - 40) % 30:
        raise ValueError('Invalid MON-VER payload')
    fields = [payload[:30], payload[30:40]] + [payload[i:i+30] for i in range(40, len(payload), 30)]
    if any(any(c < 32 or c > 126 for c in field.split(b'\0', 1)[0]) for field in fields) or not fields[0].split(b'\0', 1)[0]:
        raise ValueError('Invalid MON-VER text')
    extensions = [field.split(b'\0', 1)[0].decode('ascii') for field in fields[2:]]
    firmware = next((item[6:] for item in extensions if item.startswith('FWVER=')), None)
    protocol = next((item[8:] for item in extensions if item.startswith('PROTVER=')), None)
    model = next((item[4:] for item in extensions if item.startswith('MOD=')), None)
    return {'source':'ubx_mon_ver', 'payload_b64':base64.b64encode(payload).decode('ascii'),
                'firmware':firmware, 'protocol':protocol, 'model':model,
                'itfm_supported':False if firmware in ('HPG 1.50', 'HPG 1.51') else None}


async def _refresh_ubx_receiver_info(reader):
    info = {'source':'ubx_mon_ver', 'itfm_enabled':None}
    try:
        packet = await reader.query_base(_ubx_packet(0x0a, 4), (0x0a, 4))
        info.update(_ubx_version_payload(_base_ubx_payload(packet, (0x0a, 4))))
    except Exception as error:
        info['error'] = (str(error) or type(error).__name__)[:160]
    if info.get('itfm_supported') is False:
        try:
            poll = bytes(4) + struct.pack('<I', 0x10f60051)
            packet = await reader.query_base(_ubx_packet(6, 0x8b, poll), (6, 0x8b))
            values = _ubx_cfg_values(_base_ubx_payload(packet, (6, 0x8b)))
            if values.get(0x10f60051) not in (b'\0', b'\x01'):
                raise ValueError('Invalid jamming sensitivity response')
            info['jamming_sensitivity_high'] = values[0x10f60051] == b'\x01'
        except Exception as error:
            info['sensitivity_error'] = (str(error) or type(error).__name__)[:160]
        globals()['LAST_UBX_RECEIVER_INFO'] = info
        globals()['LAST_UBX_RECEIVER_INFO_TS'] = time.time()
        return
    try:
        poll = bytes(4) + struct.pack('<I', 0x1041000d)  # VALGET: running RAM layer, exact enable key.
        packet = await reader.query_base(_ubx_packet(6, 0x8b, poll), (6, 0x8b))
        body = _base_ubx_payload(packet, (6, 0x8b))
        if len(body) != 9 or body[:4] != b'\x01\0\0\0' or struct.unpack_from('<I', body, 4)[0] != 0x1041000d or body[8] > 1:
            raise ValueError('Invalid ITFM VALGET response')
        info['itfm_enabled'] = bool(body[8])
        info['itfm_source'] = 'ubx_cfg_valget'
    except Exception:
        try:
            packet = await reader.query_base(_ubx_packet(6, 0x39), (6, 0x39))
            body = _base_ubx_payload(packet, (6, 0x39))
            if len(body) != 8:
                raise ValueError('Invalid CFG-ITFM response')
            info['itfm_enabled'] = bool(struct.unpack_from('<I', body)[0] & 0x80000000)
            info['itfm_source'] = 'ubx_cfg_itfm'
        except Exception as error:
            info['itfm_error'] = (str(error) or type(error).__name__)[:160]
    globals()['LAST_UBX_RECEIVER_INFO'] = info
    globals()['LAST_UBX_RECEIVER_INFO_TS'] = time.time()


def _unicore_rf_log(packet):
    if len(packet) > 383 or not re.search(rb'\*[0-9a-fA-F]{8}$', packet.strip()):
        raise ValueError('Invalid Unicore RF checksum format')
    text, body = _base_unicore_fields(packet)
    message = text.split(',', 1)[0]
    fields = body.split(',')
    count = {UNICORE_AGCA_MESSAGE: 10, '#JAMSTATUSA': 5, '#FREQJAMSTATUSA': 9}.get(message)
    if count is None or len(fields) != count:
        raise ValueError('Invalid Unicore RF fields')
    numeric = fields if message == UNICORE_AGCA_MESSAGE else fields[1:]
    if any(not re.fullmatch(r'-?\d+', value.strip()) for value in numeric):
        raise ValueError('Invalid Unicore RF numbers')
    values = [int(value) for value in numeric]
    if message == UNICORE_AGCA_MESSAGE:
        if any(not -1 <= value <= 255 for value in values):
            raise ValueError('Invalid Unicore AGC')
        return {'source':'unicore_agca', 'values':values}
    pairs = 3 if message == '#FREQJAMSTATUSA' else 1
    ratios, flags = values[:pairs*2:2], values[1:pairs*2:2]
    if any(not 0 <= value <= 255 for value in values) or any(value not in (0, 1, 2) for value in flags):
        raise ValueError('Invalid Unicore CW indication')
    return {'source':'unicore_freqjamstatusa' if pairs == 3 else 'unicore_jamstatusa',
                'ratios':ratios, 'flags':flags}


def _rf_monitor_age(snapshot):
    value = snapshot.get('age_seconds') if isinstance(snapshot, dict) else None
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 45 else None


def _rf_monitor_decode(snapshot, source, limit):
    if _rf_monitor_age(snapshot) is None or snapshot.get('source') != source:
        return None
    encoded = snapshot.get('payload_b64')
    if not isinstance(encoded, str) or len(encoded) > limit:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError:
        return None


class GnssRfMonitor:
    """Distinct samples in successive ten-minute periods, with bounded persistent history."""
    def __init__(self, history_path=None):
        self.samples = deque(maxlen=120)
        self.previous_cno = {}
        self.previous_at = None
        self.source = None
        self.started_at = None
        self.previous_epoch = None
        self.history_path = history_path
        self.history = None
        self.history_error = None

    def _archive(self, sample, now):
        if not self.history_path:
            return
        connection = None
        try:
            connection = sqlite3.connect(self.history_path, timeout=1)
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS rf_samples (at REAL PRIMARY KEY, source TEXT, severity INTEGER, delta REAL, matched INTEGER, changed INTEGER, minimum REAL, maximum REAL)')
                if sample:
                    connection.execute('INSERT OR IGNORE INTO rf_samples VALUES (?,?,?,?,?,?,?,?)',
                        (sample['at'], self.source, sample['severity'], sample['cno_delta'], sample['matched'], sample['changed'], sample['minimum'], sample['maximum']))
                connection.execute('DELETE FROM rf_samples WHERE at < ?', (now - 86400,))
                connection.execute('DELETE FROM rf_samples WHERE at NOT IN (SELECT at FROM rf_samples ORDER BY at DESC LIMIT 20000)')
                source = self.source
                if source is None:
                    row = connection.execute('SELECT source FROM rf_samples ORDER BY at DESC LIMIT 1').fetchone()
                    source = row[0] if row else None
                rows = connection.execute('SELECT CAST(at/3600 AS INTEGER)*3600, COUNT(*), SUM(severity>0), SUM(severity=2), COALESCE(SUM(severity>0 AND delta<=-3),0), COUNT(delta), MIN(delta) FROM rf_samples GROUP BY 1 ORDER BY 1').fetchall()
                bounds = connection.execute('SELECT MIN(at),MAX(at) FROM rf_samples').fetchone()
            self.history = {'source':source, 'hours':[list(row) for row in rows], 'first_sample_at':bounds[0], 'last_sample_at':bounds[1]} if rows else None
            self.history_error = None
        except (sqlite3.Error, OSError) as error:
            self.history_error = 'Không ghi được lịch sử tại Pi.'
            logging.warning('[GNSS][RF HISTORY] %s', error)
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _decode_security_sample(raw):
        data = _rf_monitor_decode(raw, 'ubx_sec_sig', 92)
        if not data or raw.get('error'):
            return None, None, False, False
        try:
            security = _ubx_security_payload(data, 9)
        except ValueError:
            return None, None, False, False
        age = _rf_monitor_age(raw)
        jam_warning = security['jam_enabled'] and security['jam_state'] == 2
        spoof_warning = security['spoof_enabled'] and security['spoof_state'] >= 2
        if jam_warning or spoof_warning:
            severity = 2 if spoof_warning and security['spoof_state'] == 3 else 1
        elif (security['jam_enabled'] and security['jam_state'] == 1
              and security['spoof_enabled'] and security['spoof_state'] == 1):
            severity = 0
        else:
            severity = None
        return age, severity, jam_warning, spoof_warning

    @staticmethod
    def _decode_monitor_rf_sample(payload, raw):
        data = _rf_monitor_decode(raw, 'ubx_mon_rf', 136)
        if not data or len(data) < 4 or data[0] != 0 or not 1 <= data[1] <= 4 or len(data) != 4 + 24 * data[1]:
            return None, None, []
        blocks = [[data[offset], data[offset + 1] & 3, int.from_bytes(data[offset + 12:offset + 14], 'little'),
                   int.from_bytes(data[offset + 14:offset + 16], 'little'), data[offset + 16]]
                  for offset in range(4, len(data), 24)]
        flags = [block[1] for block in blocks]
        severity = max(flags) if max(flags) >= 2 or 0 not in flags else None
        if severity is not None:
            severity -= 1
        age = _rf_monitor_age(raw)
        receiver = payload.get('gnss_receiver')
        receiver_disabled = (
            _rf_monitor_age(receiver) is not None and receiver.get('itfm_enabled') is False
        )
        if receiver_disabled or raw.get('last_error'):
            severity = None
        return age, severity, blocks

    @staticmethod
    def _decode_unicore_rf_sample(raw):
        jam = raw.get('jamming')
        age = _rf_monitor_age(jam)
        count = {'unicore_freqjamstatusa': 3, 'unicore_jamstatusa': 1}.get(jam.get('source')) if isinstance(jam, dict) else None
        flags = jam.get('flags') if isinstance(jam, dict) else None
        severity = None
        if age is not None and count and isinstance(flags, list) and len(flags) == count and all(
            type(flag) is int and 0 <= flag <= 2 for flag in flags
        ):
            severity = max(flags)
        return age, severity

    def _decode_rf_sample(self, payload, raw, source):
        if payload.get('status') == 'offline':
            return None, None, [], False, False
        if source == 'ubx_sec_sig':
            age, severity, jam_warning, spoof_warning = self._decode_security_sample(raw)
            return age, severity, [], jam_warning, spoof_warning
        if source == 'ubx_mon_rf':
            age, severity, blocks = self._decode_monitor_rf_sample(payload, raw)
            return age, severity, blocks, False, False
        if source == 'unicore_rf':
            age, severity = self._decode_unicore_rf_sample(raw)
            return age, severity, [], False, False
        return None, None, [], False, False

    @staticmethod
    def _decode_satellite_cno(payload, source, age):
        satellites = payload.get('gnss_satellites')
        data = _rf_monitor_decode(satellites, 'ubx_nav_sat', 2060) if source in ('ubx_mon_rf', 'ubx_sec_sig') else None
        if not (data and len(data) >= 8 and data[4] == 1 and data[5] <= 128
                and len(data) == 8 + 12 * data[5] and abs(_rf_monitor_age(satellites) - age) <= 10):
            return {}, None
        epoch = int.from_bytes(data[:4], 'little')
        cno = {}
        for offset in range(8, len(data), 12):
            gnss, sv, value = data[offset:offset + 3]
            flags = int.from_bytes(data[offset + 8:offset + 12], 'little')
            if gnss <= 7 and sv and value > 0 and (flags & 7) >= 4:
                key = (gnss, sv)
                if key in cno:
                    return {}, epoch
                cno[key] = value
        return cno, epoch

    def _cno_changes(self, cno, epoch, sample_at):
        common = cno.keys() & self.previous_cno.keys()
        if not (len(common) >= 4 and epoch != self.previous_epoch and self.previous_at is not None
                and 0 < sample_at - self.previous_at <= 45):
            return None, []
        changes = [cno[key] - self.previous_cno[key] for key in common]
        return round(statistics.median(changes), 1), changes

    def _append_rf_sample(self, sample_at, severity, age, jam_warning, spoof_warning, blocks, payload, now):
        if self.started_at is None:
            self.started_at = sample_at
        elif sample_at >= self.started_at + 600:
            self.started_at += int((sample_at - self.started_at) // 600) * 600
            self.samples.clear()
        cno, epoch = self._decode_satellite_cno(payload, self.source, age)
        delta, changes = self._cno_changes(cno, epoch, sample_at)
        sample = {
            'at': sample_at, 'severity': severity, 'cno_delta': delta,
            'jamming': jam_warning, 'spoofing': spoof_warning, 'rf_blocks': blocks,
            'matched': len(changes), 'changed': sum(value != 0 for value in changes),
            'minimum': min(changes) if changes else None,
            'maximum': max(changes) if changes else None,
        }
        self.samples.append(sample)
        if epoch != self.previous_epoch:
            self.previous_cno, self.previous_at, self.previous_epoch = cno, sample_at, epoch
        self._archive(sample, now)

    def _build_rf_summary(self, severity):
        warnings = [sample for sample in self.samples if sample['severity'] > 0]
        episodes = sum(
            sample['severity'] > 0 and (
                index == 0 or self.samples[index - 1]['severity'] == 0
                or sample['at'] - self.samples[index - 1]['at'] > 45
            )
            for index, sample in enumerate(self.samples)
        )
        block_ranges = []
        for index in range(4):
            readings = [sample['rf_blocks'][index] for sample in self.samples if len(sample['rf_blocks']) > index]
            if readings:
                # Preserve receiver order: old firmware can report duplicate block IDs.
                block_ranges.append([
                    index + 1, readings[-1][0], len(readings),
                    sum(row[1] == 2 for row in readings), sum(row[1] == 3 for row in readings),
                    *[value for column in (2, 3, 4) for value in (
                        min(row[column] for row in readings), max(row[column] for row in readings)
                    )],
                ])
        last = self.samples[-1] if self.samples else {
            'at': self.history['last_sample_at'], 'cno_delta': None, 'matched': 0,
        }
        return {
            'source': self.source or self.history['source'], 'window_seconds': 600,
            'sample_count': len(self.samples), 'window_mode': 'fixed',
            'window_started_at': self.started_at,
            'window_ends_at': self.started_at + 600 if self.started_at is not None else None,
            'jamming_samples': sum(sample.get('jamming', False) for sample in self.samples),
            'spoofing_samples': sum(sample.get('spoofing', False) for sample in self.samples),
            'observed_seconds': min(600, max(0, round(last['at'] - self.started_at, 1))) if self.started_at is not None else 0,
            'samples': [[round(last['at'] - sample['at'], 1), sample['severity'], sample['cno_delta'],
                         sample['matched'], sample['changed'], sample['minimum'], sample['maximum']]
                        for sample in self.samples],
            'history_24h': self.history, 'history_error': self.history_error,
            'block_ranges': block_ranges, 'warning_samples': len(warnings),
            'critical_samples': sum(sample['severity'] == 2 for sample in self.samples),
            'episodes': episodes,
            'coincident_cno_drops': sum(
                sample['cno_delta'] is not None and sample['cno_delta'] <= -3 for sample in warnings
            ),
            'quality_comparisons': sum(sample['cno_delta'] is not None for sample in self.samples),
            'first_sample_at': self.samples[0]['at'] if self.samples else last['at'],
            'last_sample_at': last['at'], 'last_warning_at': warnings[-1]['at'] if warnings else None,
            'current_severity': severity, 'cno_delta_dbhz': last['cno_delta'],
            'matched_satellites': last['matched'],
        }

    def observe(self, payload, now):
        while self.samples and now - self.samples[0]['at'] > 600:
            self.samples.popleft()
        raw = payload.get('gnss_rf')
        modern = (payload.get('gnss_receiver') or {}).get('itfm_supported') is False
        if modern or payload.get('gnss_security'):
            raw = payload.get('gnss_security') or {'source': 'ubx_sec_sig'}
        source = raw.get('source') if isinstance(raw, dict) else None
        age, severity, blocks, jam_warning, spoof_warning = self._decode_rf_sample(payload, raw, source)
        if source != self.source and severity is not None:
            self.samples.clear()
            self.previous_cno = {}
            self.previous_at = None
            self.source = source
            self.started_at = None
            self.previous_epoch = None
        sample_at = now - age if age is not None else None
        should_append = severity is not None and (
            not self.samples or sample_at - self.samples[-1]['at'] >= 5
        )
        if should_append:
            self._append_rf_sample(sample_at, severity, age, jam_warning, spoof_warning, blocks, payload, now)
        elif self.history_path:
            self._archive(None, now)
        if not self.samples and not self.history:
            return None
        return self._build_rf_summary(severity)


def _fresh_receiver_reading(key, now):
    reading = globals().get(key)
    age = now - float(globals().get(key + '_TS') or 0)
    return dict(reading, age_seconds=round(age, 1)) if reading and 0 <= age <= 45 else None


def _attach_receiver_rf_error(status, now):
    error = _fresh_receiver_reading('LAST_UBX_RF_ERROR', now)
    if not error:
        return
    if status['gnss_rf']:
        status['gnss_rf']['last_error'] = error
    else:
        error.update(source='ubx_mon_rf', state='read_error')
        status['gnss_rf'] = error


def _fresh_unicore_rf_status(now):
    status = {'source':'unicore_rf', 'agc':None, 'jamming':None}
    for field, key in [('agc', 'LAST_UNICORE_AGC'), ('jamming', 'LAST_UNICORE_JAM')]:
        status[field] = _fresh_receiver_reading(key, now)
    return status if status['agc'] or status['jamming'] else None


def receiver_quality_status(now):
    # Explicit null clears stale data; legacy hacc is always millimetres.
    status = {'hacc':None, 'gnss_quality':None, 'gnss_dop':None, 'gnss_satellites':None, 'gnss_rf':None, 'gnss_spectrum':None, 'gnss_receiver':None,
              'gnss_security':None, 'gnss_security_log':None}
    report = globals().get('AUTOMATIC_RF_REPORT')
    status['gnss_rf_monitor'] = dict(report, age_seconds=max(0, round(now - report['last_sample_at'], 1))) if report else None
    for field, key in [('gnss_receiver', 'LAST_UBX_RECEIVER_INFO'), ('gnss_rf', 'LAST_UBX_RF'), ('gnss_spectrum', 'LAST_UBX_SPECTRUM'),
                       ('gnss_security', 'LAST_UBX_SECURITY'), ('gnss_security_log', 'LAST_UBX_SECURITY_LOG')]:
        status[field] = _fresh_receiver_reading(key, now)
    _attach_receiver_rf_error(status, now)
    unicore = _fresh_unicore_rf_status(now)
    if unicore:
        status['gnss_rf'] = unicore
    status['gnss_satellites'] = _fresh_receiver_reading('LAST_UBX_SATELLITES', now)
    status['gnss_dop'] = _fresh_receiver_reading('LAST_UBX_DOP', now)
    quality = _fresh_receiver_reading('LAST_RECEIVER_QUALITY', now)
    if quality:
        status['gnss_quality'] = quality
        if 'satellites' in quality:
            status['satellite_count'] = quality['satellites']
        if quality['hacc_m'] is not None:
            status['hacc'] = round(quality['hacc_m'] * 1000)
        return status
    gst = globals().get('LAST_GST_HACC_M')
    age = now - float(globals().get('LAST_GST_TS') or 0)
    if gst is not None and math.isfinite(gst) and gst > 0 and 0 <= age <= 15:
        status['hacc'] = round(gst * 1000)
        status['gnss_quality'] = {'hacc_m':float(gst), 'source':'nmea_gst', 'age_seconds':round(age, 1)}
    return status


def _update_automatic_rf_monitor():
    monitor = globals().get('AUTOMATIC_RF_MONITOR')
    if monitor is None:
        monitor = GnssRfMonitor(os.path.join(BASE_DIR, 'gnss_rf_history.sqlite3'))
        globals()['AUTOMATIC_RF_MONITOR'] = monitor
    now = time.time()
    globals()['AUTOMATIC_RF_REPORT'] = monitor.observe(dict(receiver_quality_status(now), status='online'), now)


async def _refresh_unicore_quality(reader):
    try:
        packet = await reader.query_base(b'\r\nAGCA\r\n', UNICORE_AGCA_MESSAGE.encode('ascii'))
        globals()['LAST_UNICORE_AGC'] = _unicore_rf_log(packet)
        globals()['LAST_UNICORE_AGC_TS'] = time.time()
    except Exception as error:
        logging.warning('[GNSS][AGCA] %s', error)
    for name in (b'FREQJAMSTATUSA', b'JAMSTATUSA'):
        try:
            packet = await reader.query_base(b'\r\n' + name + b'\r\n', b'#' + name)
            globals()['LAST_UNICORE_JAM'] = _unicore_rf_log(packet)
            globals()['LAST_UNICORE_JAM_TS'] = time.time()
            break
        except Exception as error:
            logging.warning('[GNSS][%s] %s', name.decode('ascii'), error)


async def _refresh_ubx_navigation_quality(reader):
    try:
        packet = await reader.query_base(_ubx_packet(1, 7), (1, 7))
        quality = _ubx_navigation_quality(_base_ubx_payload(packet, (1, 7)))
        globals()['LAST_RECEIVER_QUALITY'] = quality
        globals()['LAST_RECEIVER_QUALITY_TS'] = time.time()
    except Exception as error:
        logging.warning('[GNSS][PVT] %s', error)
    try:
        packet = await reader.query_base(_ubx_packet(1, 4), (1, 4))
        globals()['LAST_UBX_DOP'] = _ubx_dop(_base_ubx_payload(packet, (1, 4)))
        globals()['LAST_UBX_DOP_TS'] = time.time()
    except Exception as error:
        logging.warning('[GNSS][DOP] %s', error)  # Keep PVT available if only DOP fails.
    try:
        packet = await reader.query_base(_ubx_packet(1, 0x35), (1, 0x35))
        globals()['LAST_UBX_SATELLITES'] = _ubx_satellite_payload(_base_ubx_payload(packet, (1, 0x35)))
        globals()['LAST_UBX_SATELLITES_TS'] = time.time()
    except Exception as error:
        logging.warning('[GNSS][NAV-SAT] %s', error)


async def _refresh_ubx_rf_quality(reader):
    for message_id, key in [(0x38, 'LAST_UBX_RF'), (0x31, 'LAST_UBX_SPECTRUM')]:
        packet = None
        try:
            packet = await reader.query_base(_ubx_packet(0x0a, message_id), (0x0a, message_id))
            globals()[key] = _ubx_rf_payload(_base_ubx_payload(packet, (0x0a, message_id)), message_id)
            globals()[key + '_TS'] = time.time()
            if message_id == 0x38:
                globals()['LAST_UBX_RF_ERROR'] = None
        except Exception as error:
            if message_id == 0x38:
                failure = {'error':(str(error) or type(error).__name__)[:160], 'error_kind':'timeout' if isinstance(error, TimeoutError) else 'invalid_response' if isinstance(error, ValueError) else 'query_failed'}
                if packet:
                    failure['frame_b64'] = base64.b64encode(packet[:128]).decode('ascii')
                globals()['LAST_UBX_RF_ERROR'] = failure
                globals()['LAST_UBX_RF_ERROR_TS'] = time.time()
            logging.warning('[GNSS][RF %02X] %s', message_id, error)


async def _refresh_ubx_security(reader):
    await _refresh_ubx_receiver_info(reader)
    if (globals().get('LAST_UBX_RECEIVER_INFO') or {}).get('itfm_supported') is False:
        for message_id, key in ((9, 'LAST_UBX_SECURITY'), (0x10, 'LAST_UBX_SECURITY_LOG')):
            try:
                packet = await reader.query_base(_ubx_packet(0x27, message_id), (0x27, message_id))
                globals()[key] = _ubx_security_payload(_base_ubx_payload(packet, (0x27, message_id)), message_id)
                globals()[key + '_TS'] = time.time()
            except Exception as error:
                globals()[key] = {'source':'ubx_sec_sig' if message_id == 9 else 'ubx_sec_siglog',
                                      'error':(str(error) or type(error).__name__)[:160]}
                globals()[key + '_TS'] = time.time()
                logging.warning('[GNSS][SEC %02X] %s', message_id, error)


async def refresh_receiver_quality(agent, reader):
    chip_type = agent.detected_chip.get('type')
    if chip_type == 'Unicorecomm':
        await _refresh_unicore_quality(reader)
        _update_automatic_rf_monitor()
        return
    if chip_type != 'Ublox':
        return
    await _refresh_ubx_navigation_quality(reader)
    await _refresh_ubx_rf_quality(reader)
    await _refresh_ubx_security(reader)
    _update_automatic_rf_monitor()



def _runtime_serial_baud(agent=None, gnss_reader=None) -> int:
    """Use the detected GNSS baud for command writes; DEFAULT_BAUDRATE is only a fallback."""
    candidates = [
        getattr(gnss_reader, "baudrate", None),
        (getattr(agent, "detected_chip", {}) or {}).get("baud") if agent else None,
        DEFAULT_BAUDRATE,
    ]
    for value in candidates:
        try:
            baud = int(value)
            if baud > 0:
                return baud
        except (TypeError, ValueError):
            continue
    return DEFAULT_BAUDRATE


def _delay_marker_seconds(command_bytes: bytes) -> float | None:
    if not command_bytes.startswith(b"$DELAY_") or not command_bytes.endswith(b"$"):
        return None
    try:
        return max(0.0, min(10.0, int(command_bytes[7:-1]) / 1000.0))
    except ValueError:
        return None


def _record_nmea_type(nmea_type):
    counts = globals().setdefault("LAST_NMEA_TYPE_COUNTS", {})
    counts[nmea_type] = int(counts.get(nmea_type, 0)) + 1
    globals()["LAST_VALID_NMEA_TS"] = time.time()
    if len(counts) > 16:
        for key in list(counts.keys())[:-16]:
            counts.pop(key, None)


def _record_gst_quality(parts):
    semi_major = float(parts[3]) if len(parts) > 3 and parts[3] else None
    semi_minor = float(parts[4]) if len(parts) > 4 and parts[4] else None
    globals()["LAST_GST_SEMI_MAJOR_M"] = semi_major
    globals()["LAST_GST_SEMI_MINOR_M"] = semi_minor
    lat_sigma = float(parts[6]) if parts[6] else None
    lon_sigma = float(parts[7]) if parts[7] else None
    if lat_sigma is not None and lon_sigma is not None:
        hacc = (lat_sigma ** 2 + lon_sigma ** 2) ** 0.5
        if hacc > 0:
            globals()["LAST_GST_HACC_M"] = hacc
            globals()["LAST_GST_HACC_SOURCE"] = "gst_lat_lon_sigma"
    globals()["LAST_GST_TS"] = time.time()


def _record_gsv_quality(parts):
    snrs = []
    fields = parts[4:]
    for index in range(0, len(fields), 4):
        if len(fields[index:index + 4]) < 4:
            continue
        try:
            snr = int(fields[index + 3]) if fields[index + 3] else 0
        except ValueError:
            snr = 0
        if snr > 0:
            snrs.append(snr)
    if snrs:
        globals()["LAST_GSV_AVG_SNR"] = sum(snrs) / len(snrs)
        globals()["LAST_GSV_SNR_SAMPLES"] = len(snrs)
    globals()["LAST_GSV_TS"] = time.time()


def _update_local_nmea_health(sentence: str) -> None:
    if not sentence or not sentence.startswith("$") or len(sentence) < 6:
        return
    try:
        head = sentence.split(",", 1)[0]
        nmea_type = head[-3:]
        _record_nmea_type(nmea_type)
        parts = sentence.split("*", 1)[0].split(",")
        if nmea_type == "GST" and len(parts) > 7:
            _record_gst_quality(parts)
        elif nmea_type == "GSV" and len(parts) > 7:
            _record_gsv_quality(parts)
    except Exception:
        return


def _telemetry_inc(section: str, key: str, amount: int = 1) -> None:
    target = reconnect_counters if section == "reconnect" else queue_metrics
    with telemetry_lock:
        target[key] = int(target.get(key, 0)) + int(amount)


def _set_transport_state(channel: str, connected: bool) -> None:
    now_ts = int(time.time())
    with telemetry_lock:
        if channel == "mqtt":
            transport_status["mqtt_connected"] = bool(connected)
            transport_status["last_mqtt_connect_ts" if connected else "last_mqtt_disconnect_ts"] = now_ts
        elif channel == "websocket":
            transport_status["websocket_connected"] = bool(connected)
            transport_status["last_websocket_connect_ts" if connected else "last_websocket_disconnect_ts"] = now_ts

        if transport_status.get("websocket_connected"):
            transport_status["active_channel"] = "websocket"
        elif transport_status.get("mqtt_connected"):
            transport_status["active_channel"] = "mqtt"
        else:
            transport_status["active_channel"] = None


def _record_command_result(command: str, source: str, status: str, detail: str = "", command_id: str = None) -> None:
    with telemetry_lock:
        last_command_result.clear()
        last_command_result.update({
            "command": command or "UNKNOWN",
            "source": source,
            "status": status,
            "detail": str(detail or "")[:300],
            "command_id": command_id,
            "timestamp": int(time.time()),
        })


def _snapshot_agent_telemetry() -> dict:
    with telemetry_lock:
        return {
            "transport_status": dict(transport_status),
            "last_command_result": dict(last_command_result),
            "reconnect_counters": dict(reconnect_counters),
            "queue_metrics": dict(queue_metrics),
        }


def _get_mqtt_runtime_config(agent=None) -> dict:
    cfg = {}
    if agent is not None and isinstance(getattr(agent, "config", None), dict):
        cfg = agent.config.get("mqtt") or agent.config.get("mqtt_config") or {}
        if not isinstance(cfg, dict):
            cfg = {}

    broker = (
        cfg.get("broker")
        or cfg.get("host")
        or MQTT_BROKER
    )
    port = cfg.get("port") or MQTT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 1883

    return {
        "broker": str(broker or BACKEND_HOST).strip(),
        "port": port,
        "username": cfg.get("username") or cfg.get("user") or DEFAULT_MQTT_USERNAME,
        "password": cfg.get("password") or cfg.get("pass") or DEFAULT_MQTT_PASSWORD,
    }


def _should_forward_nmea_to_backend(data: bytes) -> bool:
    global nmea_rate_limit_counter, nmea_rate_limit_reset_ts
    
    # NMEA sentences can be easily filtered
    # Return True if we allow it, False to drop
    now = time.time()
    if now - nmea_rate_limit_reset_ts > 1.0:
        nmea_rate_limit_counter = 0
        nmea_rate_limit_reset_ts = now
        
    if nmea_rate_limit_counter >= 50: # Max 50 messages/sec (was 15, caused GSV truncation)
        return False
        
    nmea_rate_limit_counter += 1
    try:
        text = data.decode("ascii", errors="ignore")
    except Exception:
        return False
    return any(token in text[:12] for token in ("GGA", "GSA", "GSV", "GST"))


@dataclass
class EllipPara:
    a: float = 6378137.0
    b: float = 6356752.31424518
    f: float = 1.0 / 298.2572236
    we: float = 7.292115147e-5
    e2: float = 1.0 - (6356752.3142 / 6378137.0) ** 2
    gm: float = 3986004.418e8
    j2: float = 1.082626683e-3
    j3: float = -2.5327e-6

@dataclass
class RTCMDatum:
    rtcm1025_update: bool = False
    projection_1025: dict = field(default_factory=dict)
    k0: float = 0.9999
    to_zone: int = 3
    l0: float = 105.0 * 0.017453292519943295
    central_meridian_deg: int = 105
    central_meridian_min: int = 0
    elip: EllipPara = field(default_factory=EllipPara)
    to_proj: int = 0
    to_ellip: int = 0
    rtcm1021_update: bool = False
    kT: float = 0.999999747093722
    dX: float = 191.90441429
    dY: float = 39.30318279
    dZ: float = 111.45032835
    rX: float = 0.00928836
    rY: float = -0.01975479
    rZ: float = 0.00427372
    para_model: int = 0
    false_easting: float = 500000.0
    false_northing: float = 0.0
    false_h: float = 0.0
    rtcm1023_update: bool = False
    resB: float = 0.0
    resL: float = 0.0
    resH: float = 0.0
    resN: float = 0.0
    resE: float = 0.0
    resHS: float = 0.0
    tra_update: int = 0
    res_update: bool = False
    pro_update: bool = False
    shift_cors: bool = True
    name: str = "WGS-84"
    datum_type: int = 0

DATUM_LOCK = threading.Lock()
AUTO_BASE_DATUM = RTCMDatum()
active_auto_base_task = None
active_auto_base_client = None
# RTCM dispatcher flag (true when at least one configured server is allowed to publish)
rtcm_stream_active_flag = False


def _to_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _normalize_mountpoint_value(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned.lower() if cleaned else None


def _get_ntrip_server_ids(cfg: dict) -> list[int]:
    """Keep the two legacy slots and discover additional numbered outputs."""
    ids = {1, 2}
    for key in cfg:
        if key.startswith('server') and key.endswith('_enabled'):
            suffix = key[6:-8]
            if suffix.isdigit() and int(suffix) > 0:
                ids.add(int(suffix))
        elif key.startswith('serverhost') and key[10:].isdigit() and int(key[10:]) > 0:
            ids.add(int(key[10:]))
    return sorted(ids)


def _get_server_stream_switches(cfg: dict, server_id: int, *, apply_fail_open: bool = False) -> dict:
    global_on_demand = _to_bool(cfg.get('stream_on_demand', False), False)
    global_active = _to_bool(cfg.get('stream_active', False), False)

    server_on_demand_key = f'server{server_id}_stream_on_demand'
    server_active_key = f'server{server_id}_stream_active'

    if server_on_demand_key in cfg and cfg.get(server_on_demand_key) is not None:
        on_demand = _to_bool(cfg.get(server_on_demand_key), global_on_demand)
    else:
        on_demand = global_on_demand

    if not on_demand:
        active = True
    elif server_active_key in cfg and cfg.get(server_active_key) is not None:
        active = _to_bool(cfg.get(server_active_key), global_active)
    else:
        active = global_active

    enabled = _to_bool(cfg.get(f'server{server_id}_enabled'), False)
    effective = resolve_effective_stream_switch(
        enabled=enabled,
        on_demand=on_demand,
        active=active,
        fail_open=apply_fail_open and control_plane_fail_open_event.is_set(),
    )

    locked = is_remote_locked(server_id)
    if locked:
        effective["can_push"] = False
        effective["fail_open_forced"] = False
    return {
        'locked': locked,
        'enabled': enabled,
        'on_demand': on_demand,
        'active': active,
        **effective,
    }


def _compute_any_server_can_push(cfg: dict, *, apply_fail_open: bool = False) -> bool:
    for server_id in _get_ntrip_server_ids(cfg):
        if _get_server_stream_switches(
            cfg,
            server_id,
            apply_fail_open=apply_fail_open,
        )['can_push']:
            return True
    return False


def _resolve_preferred_server_for_mountpoint(cfg: dict, mountpoint: str | None) -> int | None:
    target_mp = _normalize_mountpoint_value(mountpoint)
    if not target_mp:
        return None

    candidates = []
    for sid in _get_ntrip_server_ids(cfg):
        server_mp = _normalize_mountpoint_value(cfg.get(f'mountpoint{sid}'))
        enabled = _to_bool(cfg.get(f'server{sid}_enabled'), False)
        if enabled and server_mp and server_mp == target_mp:
            candidates.append(sid)

    if not candidates:
        return None
    return sorted(candidates)[0]

# Internal RTCM ingress bitrate tracker (independent from NTRIP socket forwarding)
rtcm_input_stats_lock = threading.Lock()
rtcm_input_window_bytes = 0
rtcm_input_window_start_ts = time.time()
rtcm_input_bps = 0
rtcm_mqtt_queue = None
LAST_NMEA_GSV_GSA_TS = 0.0

# ==============================================================================
# === EMBEDDED LICENSE MANAGER                                              ===
# ==============================================================================
import math

def get_license_code_from_string(string_number: str) -> str:
    try:
        length = len(string_number)
        if length == 0: return ""
        arr = [0] * (length + 1)
        narr = [0.0] * (length + 1)
        number = float(string_number)
        r_string = ""
        n = length
        number *= n
        arr[0] = int(number * math.pow(10.0, -float(n)))
        for i in range(1, length):
            number = number - float(arr[i - 1]) / math.pow(10.0, -float(n))
            n -= 1
            arr[i] = int(number * math.pow(10, -float(n)))
        arr[length] = (arr[length - 2] + arr[length - 1]) / 2 + 1
        narr[0] = float(arr[0])
        for i in range(1, length + 1):
            narr[i] = (narr[i - 1] + float(arr[i])) / 2.0
        for i in range(length):
            A = narr[i + 1] * math.exp(-0.2)    
            B = (math.log(arr[length], 10)) * math.pow(arr[i + 1], 0.2)
            r_string += str(int(round(A + B)))
        return r_string
    except (ValueError, IndexError, TypeError, ZeroDivisionError):
        return "Error: Invalid input"

def generate_pi_license_base(serial_number: str) -> str:
    base_code_str = ""
    s_number_part = serial_number[-10:]
    temp_str = ''.join(c.upper() if 'a' <= c.lower() <= 'z' else c for c in s_number_part)
    for char in temp_str:
        base_code_str += str(ord(char))
    if len(base_code_str) > 12:
        return base_code_str[:12]
    return base_code_str.ljust(12, '0')

CHIP_DETECT_BAUD_HINTS = {}


def _ordered_baud_rates(port_device: str, common_baud_rates: list[int]) -> list[int]:
    hint = CHIP_DETECT_BAUD_HINTS.get(str(port_device or "").strip())
    if not hint or hint not in common_baud_rates:
        return list(common_baud_rates)
    return [hint] + [baud for baud in common_baud_rates if baud != hint]


def _remember_baud_hint(port_device: str, baud: int) -> None:
    if not port_device:
        return
    CHIP_DETECT_BAUD_HINTS[str(port_device).strip()] = int(baud)


def _wait_for_serial_response(ser, wait_seconds: float, max_bytes: int = 2048) -> bytes:
    deadline = time.time() + max(0.1, float(wait_seconds))
    chunks = []
    seen_data = False
    idle_ticks_after_data = 0

    while time.time() < deadline:
        waiting = int(getattr(ser, 'in_waiting', 0) or 0)
        if waiting > 0:
            read_size = min(waiting, max_bytes)
            chunk = ser.read(read_size)
            if chunk:
                chunks.append(chunk)
                seen_data = True
                idle_ticks_after_data = 0
                if sum(len(x) for x in chunks) >= max_bytes:
                    break
            time.sleep(0.03)
            continue

        if seen_data:
            idle_ticks_after_data += 1
            if idle_ticks_after_data >= 3:
                break
        time.sleep(0.05)

    return b''.join(chunks)

# ==============================================================================
# === EMBEDDED CHIP DETECTOR                                                ===
# ==============================================================================
def get_chip_info(port: str, baudrate: int) -> dict | None:
    try:
        with serial.Serial(port, baudrate, timeout=2) as ser:
            # Test U-blox first
            ser.reset_input_buffer()
            ser.write(b'\xB5\x62\x0A\x04\x00\x00\x0E\x34')
            ser.flush()
            response = _wait_for_serial_response(ser, wait_seconds=0.6, max_bytes=160)
            if b'\xB5\x62\x0A\x04' in response:
                _remember_baud_hint(port, baudrate)
                return {"type": "Ublox"}
    except Exception:
        pass
    
    unicore_test_commands = [
        UNICORE_VERSION_COMMAND,
        b'config version\r\n',
        b'config\r\n',
    ]
    
    for test_cmd in unicore_test_commands:
        try:
            with serial.Serial(port, baudrate, timeout=2) as ser:
                ser.reset_input_buffer()
                ser.write(test_cmd)
                ser.flush()
                response = _wait_for_serial_response(ser, wait_seconds=1.0, max_bytes=1024)
            
                unicore_keywords = [b'Unicore', b'UM982', b'UM9', b'UB4', b'FIRMWARE', b'COMPTYPE']
                if any(keyword in response for keyword in unicore_keywords):
                    _remember_baud_hint(port, baudrate)
                    return {"type": "Unicorecomm"}
        except Exception:
            continue
    
    return None

def _probe_unicore(port_name, baud):
    commands = [UNICORE_VERSION_COMMAND, b'config version\r\n', b'config\r\n']
    keywords = ['Unicore', 'UM982', 'UM9', 'UB4', 'FIRMWARE', 'COMPTYPE', 'MODEL']
    for command in commands:
        try:
            with serial.Serial(port_name, baud, timeout=2.5, write_timeout=2.0) as ser:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.write(command)
                ser.flush()
                response = _wait_for_serial_response(ser, wait_seconds=1.2, max_bytes=2048)
            if not response:
                continue
            response_text = response.decode('ascii', errors='ignore')
            if any(keyword in response_text for keyword in keywords):
                logging.info(f"!!! [ACTIVE] Detected UM982/Unicorecomm on {port_name} @ {baud}")
                logging.info(f"    Response snippet: {response_text[:100]}")
                _remember_baud_hint(port_name, baud)
                return {"port": port_name, "type": "Unicorecomm", "baud": baud}
        except Exception as exc:
            logging.debug(f"Unicore command test failed: {exc}")
    return None


def _probe_ublox(port_name, baud):
    try:
        with serial.Serial(port_name, baud, timeout=2.0, write_timeout=2.0) as ser:
            ser.reset_input_buffer()
            ser.write(b'\xB5\x62\x0A\x04\x00\x00\x0E\x34')
            ser.flush()
            response = _wait_for_serial_response(ser, wait_seconds=0.9, max_bytes=512)
        if b'\xB5\x62\x0A\x04' not in response:
            return None
        logging.info(f"!!! [ACTIVE] Detected U-blox on {port_name} @ {baud}")
        _remember_baud_hint(port_name, baud)
        return {"port": port_name, "type": "Ublox", "baud": baud}
    except Exception as exc:
        logging.debug(f"Ublox test failed: {exc}")
        return None


def _probe_active_port(port_name, baud_rates):
    for baud in baud_rates:
        logging.info(f"  -> [ACTIVE] Probing {port_name} @ {baud}...")
        detection = _probe_unicore(port_name, baud)
        if detection:
            return detection
        detection = _probe_ublox(port_name, baud)
        if detection:
            return detection
    return None


def _is_non_receiver_port(port_name):
    name = port_name.lower()
    return 'bluetooth' in name or 'rfcomm' in name


def _probe_active_ports(ports, common_baud_rates):
    for port in ports:
        if _is_non_receiver_port(port.device):
            continue
        detection = _probe_active_port(
            port.device, _ordered_baud_rates(port.device, common_baud_rates)
        )
        if detection:
            return detection
    return None


def _probe_passive_port(port_name, baud_rates):
    for baud in baud_rates:
        logging.info(f"  -> [PASSIVE] Listening on {port_name} @ {baud}...")
        try:
            with serial.Serial(port_name, baud, timeout=4.0) as ser:
                raw_data = _wait_for_serial_response(ser, wait_seconds=1.6, max_bytes=4096)
            if b'\xD3' in raw_data:
                chip_type = "RTCM3_Source"
            elif b'$GP' in raw_data or b'$GN' in raw_data:
                chip_type = "Ublox" if b'$PUBX' in raw_data else "Generic_NMEA"
                if chip_type == "Generic_NMEA":
                    logging.warning(f"    Detected NMEA on {port_name} - might be UM982")
            else:
                continue
            logging.info(f"!!! [PASSIVE] Detected {chip_type} on {port_name} @ {baud}")
            _remember_baud_hint(port_name, baud)
            return {"port": port_name, "type": chip_type, "baud": baud}
        except Exception as exc:
            logging.debug(f"Passive scan error: {exc}")
    return None


def _probe_passive_ports(ports, common_baud_rates):
    for port in ports:
        if _is_non_receiver_port(port.device):
            continue
        detection = _probe_passive_port(
            port.device, _ordered_baud_rates(port.device, common_baud_rates)
        )
        if detection:
            return detection
    return None


def find_chip_robustly():
    common_baud_rates = [115200, 460800, 921600, 230400, 38400, 9600]
    ports = serial.tools.list_ports.comports()
    if not ports:
        logging.warning("[ChipDetector] No ports found.")
        return {"port": None, "type": "UNKNOWN", "baud": None}

    logging.info("[ChipDetector] Starting ACTIVE COMMAND-BASED scan...")
    active_detection = _probe_active_ports(ports, common_baud_rates)
    if active_detection:
        return active_detection

    logging.warning("[ChipDetector] Active scan failed. Starting PASSIVE scan...")
    logging.warning("              (May misidentify UM982 as Generic_NMEA)")
    passive_detection = _probe_passive_ports(ports, common_baud_rates)
    if passive_detection:
        return passive_detection
    logging.error("[ChipDetector] All scans failed. No supported chip found.")
    return {"port": None, "type": "UNKNOWN", "baud": None}


def find_chip_fallback():
    logging.info("[ChipDetector] Running FALLBACK detection with NMEA disable...")
    ports = serial.tools.list_ports.comports()
    
    for port in ports:
        if 'bluetooth' in port.device.lower():
            continue
            
        logging.info(f"[Fallback] Testing {port.device}...")
        
        for baud in _ordered_baud_rates(port.device, [115200, 460800, 921600, 38400]):
            try:
                with serial.Serial(port.device, baud, timeout=3) as ser:
                    ser.reset_input_buffer()
                    ser.write(b'unlog\r\n')
                    ser.flush()
                    _wait_for_serial_response(ser, wait_seconds=0.6, max_bytes=512)
                    
                    # Clear buffer
                    if ser.in_waiting > 0:
                        ser.read(ser.in_waiting)
                    
                    # Test version command
                    ser.write(UNICORE_VERSION_COMMAND)
                    ser.flush()
                    response = _wait_for_serial_response(ser, wait_seconds=1.2, max_bytes=2048)
                    if response:
                        response_str = response.decode('ascii', errors='ignore')
                        
                        if any(kw in response_str for kw in ['UM982', 'Unicore', 'FIRMWARE']):
                            logging.info(f"[Fallback] Found UM982 on {port.device} @ {baud}")
                            _remember_baud_hint(port.device, baud)
                            return {"port": port.device, "type": "Unicorecomm", "baud": baud}
                    
                    # Test for any GNSS data
                    ser.reset_input_buffer()
                    data = _wait_for_serial_response(ser, wait_seconds=1.2, max_bytes=2048)
                    if b'$G' in data or b'\xB5\x62' in data or b'\xD3' in data:
                        logging.info(f"[Fallback] Found GNSS-like data on {port.device} @ {baud}")
                        _remember_baud_hint(port.device, baud)
                        return {"port": port.device, "type": "Generic", "baud": baud}
                            
            except Exception as e:
                logging.debug(f"Fallback test error: {e}")
                continue
    
    return {"port": None, "type": "UNKNOWN", "baud": None}

async def detect_chip_with_retry(max_retries=3, retry_delay=10):
    for attempt in range(max_retries):
        logging.info(f"{'='*60}\nChip Detection Attempt {attempt + 1}/{max_retries}\n{'='*60}")
        
        chip_info = find_chip_robustly()
        if chip_info and chip_info.get("port"):
            return chip_info
        
        logging.warning("Main detection failed, trying fallback...")
        chip_info = find_chip_fallback()
        if chip_info and chip_info.get("port"):
            return chip_info
        
        if attempt < max_retries - 1:
            logging.warning(f"Chip not found. Retrying in {retry_delay} seconds.")
            await asyncio.sleep(retry_delay)
            
    logging.error("!!! Could not find GNSS chip after all attempts.")
    return {"port": None, "type": "UNKNOWN", "baud": None}

# ==============================================================================
# === UTILITY FUNCTIONS                                                     ===
# ==============================================================================

def get_machine_serial():
    SERIAL_FILE_PATH = os.path.join(BASE_DIR, "device_id.txt") 

    if os.path.exists(SERIAL_FILE_PATH):
        try:
            with open(SERIAL_FILE_PATH, 'r') as f:
                saved_serial = f.read().strip()
                if (saved_serial.startswith("LP_") and len(saved_serial) > 10) or re.fullmatch(
                    r'(?:PI_[0-9a-f]{12}|[0-9a-f]{16})', saved_serial, re.IGNORECASE
                ):
                    return saved_serial
        except Exception:
            pass
    final_serial = ""

    if IS_WINDOWS:
        try:
            mac = uuid.getnode()

            mac_str = f"{mac:012X}"
  
            final_serial = f"LP_{mac_str}"
            
            logging.info(f"Generated new serial from MAC: {final_serial}")
        except Exception:
            final_serial = f"LP_RND_{uuid.uuid4().hex[:12]}"
    
    elif IS_RASPBERRY_PI:
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        final_serial = line.strip().split(':')[-1].strip()
        except Exception: pass
        if not final_serial: final_serial = f"PI_{uuid.uuid4().hex[:12]}"
    
    else:
        final_serial = f"UNK_{uuid.uuid4().hex[:12]}"

    try:
        with open(SERIAL_FILE_PATH, 'w') as f:
            f.write(final_serial)
            logging.info(f"Saved new serial to {SERIAL_FILE_PATH}")
    except Exception as e:
        logging.exception(f"Cannot save device ID: {e}")

    return final_serial

def is_valid_license_key(key: str, serial: str) -> bool:
    if re.fullmatch(r"dt_[A-Za-z0-9_-]{40,64}", key):
        return True
    # Keep existing serial-bound licenses valid during a mixed-version rollout.
    expected = get_license_code_from_string(generate_pi_license_base(serial))
    return bool(key and key.isascii() and key.isdecimal() and key == expected)


def license_is_valid():
    global MACHINE_SERIAL
    MACHINE_SERIAL = get_machine_serial()
    if "ERROR" in MACHINE_SERIAL or "UNKNOWN" in MACHINE_SERIAL:
        return False
    try:
        with open(LICENSE_PATH, 'r') as f:
            saved_key = f.read().strip()
    except Exception:
        return False
    return is_valid_license_key(saved_key, MACHINE_SERIAL)

def cleanup_lock_file():
    if not os.path.exists(LOCK_FILE_PATH): return True
    try:
        with open(LOCK_FILE_PATH, 'r') as f:
            pid_str = f.read().strip()
            if not pid_str:
                os.remove(LOCK_FILE_PATH); return True
            
            pid = int(pid_str)
        
        if psutil.pid_exists(pid):
            p = psutil.Process(pid)
            if 'python' in p.name():
                logging.error(f"Lock file hop le ton tai cho PID: {pid}. Mot agent khac dang chay.")
                return False
        
        logging.warning(f"Tim thay lock file cu (stale) cho PID: {pid} da thoat. Dang xoa...")
        os.remove(LOCK_FILE_PATH)
        return True
    except (ValueError, psutil.NoSuchProcess, FileNotFoundError):
        try: os.remove(LOCK_FILE_PATH)
        except Exception: pass
        return True
    except Exception as e:
        logging.exception(f"Loi kiem tra lock file: {e}")
        return False

def create_lock_file():
    try:
        with open(LOCK_FILE_PATH, 'w') as f:
            f.write(f"{os.getpid()}")
        return True
    except Exception:
        return False

def remove_lock_file():
    if os.path.exists(LOCK_FILE_PATH):
        try:
            os.remove(LOCK_FILE_PATH)
        except Exception:
            pass

def read_remote_lock():
    try:
        with open(REMOTE_LOCK_PATH) as handle:
            value = json.load(handle)
        if isinstance(value, dict) and isinstance(value.get("locked_servers"), list):
            ids = value["locked_servers"]
            if all(type(sid) is int and sid in (1, 2) for sid in ids):
                return {"locked_servers": sorted(set(ids))}
        return {"all": True}
    except FileNotFoundError:
        return {"locked_servers": []}
    except (ValueError, OSError):
        # Preserve the old LOCKED_AT file and fail closed on corrupt locks.
        return {"all": True}


def is_remote_locked(server_id=None):
    lock = read_remote_lock()
    return bool(lock.get("all") or (server_id is not None and server_id in lock.get("locked_servers", [])))


def get_device_lock_status():
    lock = read_remote_lock()
    return {"scoped": True, "locked_servers": [1, 2] if lock.get("all") else lock["locked_servers"], "all": bool(lock.get("all"))}


def create_remote_lock(locked_servers=None):
    try:
        if locked_servers is not None:
            if not isinstance(locked_servers, list) or any(type(sid) is not int or sid not in (1, 2) for sid in locked_servers):
                return False
            if not locked_servers:
                return remove_remote_lock()
        temporary = REMOTE_LOCK_PATH + ".tmp"
        with open(temporary, 'w') as handle:
            if locked_servers is None:
                handle.write(f"LOCKED_AT_{int(time.time())}")
            else:
                json.dump({"locked_servers": sorted(set(locked_servers))}, handle)
        os.replace(temporary, REMOTE_LOCK_PATH)
        return True
    except OSError:
        return False


def remove_remote_lock():
    try:
        os.remove(REMOTE_LOCK_PATH)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def get_system_info() -> dict:
    try:
        # CPU
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_freq = psutil.cpu_freq()
        
        # Temperature & Voltage (Raspberry Pi)
        temp = None
        core_volts = None
        if IS_RASPBERRY_PI:
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = float(f.read().strip()) / 1000.0  # Celsius
            except Exception:
                pass
            try:
                import subprocess
                out = subprocess.check_output(["vcgencmd", "measure_volts", "core"], text=True, timeout=1)
                if "volt=" in out:
                    core_volts = float(out.strip().replace("volt=", "").replace("V", ""))
            except Exception:
                pass
        
        # Memory
        mem = psutil.virtual_memory()
        
        # Disk
        disk = psutil.disk_usage('/')
        
        # Uptime (seconds)
        boot_time = psutil.boot_time()
        uptime_seconds = int(time.time() - boot_time)
        
        return {
            "cpu": {
                "usage_percent": round(cpu_percent, 1),
                "frequency_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
                "core_volts": core_volts,
                "count": psutil.cpu_count()
            },
            "temperature": {
                "celsius": round(temp, 1) if temp else None
            },
            "memory": {
                "total_mb": round(mem.total / (1024**2), 0),
                "used_mb": round(mem.used / (1024**2), 0),
                "percent": round(mem.percent, 1)
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "percent": round(disk.percent, 1)
            },
            "uptime_seconds": uptime_seconds,
            "timestamp": int(time.time())
        }
    except Exception as e:
        logging.exception(f"Error collecting system info: {e}")
        return {}


def get_cached_system_info() -> dict:
    now = time.time()
    with system_info_cache_lock:
        cached_ts = float(system_info_cache.get("timestamp") or 0.0)
        cached_payload = system_info_cache.get("payload") or {}
        if cached_payload and now - cached_ts < max(5, SYSTEM_INFO_INTERVAL_SECONDS):
            return dict(cached_payload)

    payload = get_system_info()
    if payload:
        with system_info_cache_lock:
            system_info_cache["timestamp"] = now
            system_info_cache["payload"] = payload
    return payload

def get_license_fingerprint() -> str | None:
    try:
        if not os.path.exists(LICENSE_PATH):
            return None
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if not token:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def _status_safe_config(value):
    """Remove reusable credentials from retained/live status telemetry."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(marker in normalized for marker in ("password", "passwd", "secret", "token")) or normalized.endswith(("_pass", "pass")):
                continue
            safe[key] = _status_safe_config(item)
        return safe
    if isinstance(value, list):
        return [_status_safe_config(item) for item in value]
    return value
    
def parse_gga_data(gga_sentence: str) -> tuple:
    """
    Parse NMEA GGA sentence để lấy Fix Quality và Tọa độ (Lat, Lon, Alt)
    Trường hợp lỗi trả về ("NO_FIX", None, None, None)
    """
    try:
        s = gga_sentence.strip()
        lines = s.split('\n')
        line = next((l.strip() for l in lines if 'GGA' in l), s)
        parts = line.split(',')
        if len(parts) < 10:
            return ("NO_FIX", None, None, None)

        try:
            fix_quality = int(parts[6])
        except ValueError:
            return ("NO_FIX", None, None, None)

        status_map = {1: "GPS_FIX", 2: "DGPS", 4: "RTK_FIXED", 5: "RTK_FLOAT"}
        status = status_map.get(fix_quality, "NO_FIX")

        lat, lon, alt_ellipsoid = None, None, None
        try:
            if parts[2] and parts[4] and parts[9]:
                # Convert NMEA DDMM.MMMMM to Decimal Degrees DD.DDDDDD
                raw_lat = float(parts[2])
                lat = int(raw_lat / 100) + (raw_lat % 100) / 60.0
                if parts[3] == 'S': lat = -lat

                raw_lon = float(parts[4])
                lon = int(raw_lon / 100) + (raw_lon % 100) / 60.0
                if parts[5] == 'W': lon = -lon

                # Prefer true ellipsoidal height from UBX-NAV-HPPOSLLH when available.
                hpp = globals().get('LAST_HPPOSLLH_COORD')
                hpp_ts = float(globals().get('LAST_HPPOSLLH_TS') or 0.0)
                if hpp and isinstance(hpp, tuple) and len(hpp) >= 3 and (time.time() - hpp_ts) <= 2.5:
                    lat_hpp, lon_hpp, h_hpp = hpp[:3]
                    # Use HPPOSLLH full LLH if close enough to current GGA position.
                    if (
                        lat_hpp is not None and lon_hpp is not None and h_hpp is not None
                        and abs(float(lat_hpp) - float(lat)) <= 0.0002
                        and abs(float(lon_hpp) - float(lon)) <= 0.0002
                    ):
                        lat = float(lat_hpp)
                        lon = float(lon_hpp)
                        alt_ellipsoid = float(h_hpp)
                    else:
                        alt_ellipsoid = float(h_hpp)
                else:
                    # Fallback from GGA fields:
                    # - parts[9]: orthometric height H (MSL)
                    # - parts[11]: geoid separation N
                    # Ellipsoidal height h = H + N
                    alt_msl = float(parts[9])
                    geoid_sep = float(parts[11]) if len(parts) > 11 and parts[11] not in (None, "") else 0.0
                    alt_ellipsoid = alt_msl + geoid_sep
        except ValueError:
            pass

        return (status, lat, lon, alt_ellipsoid)
    except Exception:
        return ("NO_FIX", None, None, None)

def _nmea_checksum(body: str) -> str:
    csum = 0
    for ch in body:
        csum ^= ord(ch)
    return f"{csum:02X}"

def _normalize_gga_for_ntrip(raw_gga: str) -> str | None:
    """
    Normalize outbound GGA for NTRIP:
    - Keep original sentence as base.
    - Override satellite count with UBX NAV-PVT numSV when available (fresh).
    - Recalculate checksum.
    """
    if not raw_gga:
        return None
    try:
        s = str(raw_gga).strip()
        if not s:
            return None
        if "\n" in s:
            s = next((l.strip() for l in s.splitlines() if "GGA" in l), s.strip())
        if not s.startswith("$") or "GGA" not in s[:12]:
            return s

        body = s[1:].split("*", 1)[0]
        parts = body.split(",")
        if len(parts) < 15:
            return s

        numsv = globals().get("LAST_UBX_NUMSV")
        numsv_ts = float(globals().get("LAST_UBX_NUMSV_TS") or 0.0)
        if numsv is not None and (time.time() - numsv_ts) <= 2.5:
            try:
                n = int(numsv)
                if n < 0:
                    n = 0
                if n > 99:
                    n = 99
                parts[7] = str(n)
            except Exception:
                pass

        body2 = ",".join(parts)
        return f"${body2}*{_nmea_checksum(body2)}"
    except Exception:
        return raw_gga

def _is_rtk_usable_status(status: str) -> bool:
    # A float solution must not become the permanent base coordinate.
    return status == "RTK_FIXED"

def _get_fresh_outbound_gga(max_age_seconds: float = 10.0) -> str | None:
    raw_gga = globals().get('LAST_RAW_GGA')
    gga_ts = float(globals().get('LAST_RAW_GGA_TS') or 0.0)
    if not raw_gga or gga_ts <= 0.0:
        return None
    if (time.time() - gga_ts) > max_age_seconds:
        return None
    return raw_gga.strip()

def _mark_gnss_data_stale():
    globals()['LAST_RAW_GGA'] = None
    globals()['LAST_RAW_GGA_TS'] = 0.0
    globals()['LAST_GGA_FIX_STATUS'] = "NO_FIX"
    globals()['LAST_GGA_COORD'] = None
    globals()['LAST_HPPOSLLH_COORD'] = None
    globals()['LAST_HPPOSLLH_TS'] = 0.0
    globals()['LAST_UBX_NUMSV'] = None
    globals()['LAST_UBX_NUMSV_TS'] = 0.0
    globals()['LAST_RECEIVER_QUALITY'] = None
    globals()['LAST_RECEIVER_QUALITY_TS'] = 0.0
    globals()['LAST_UBX_DOP'] = None
    globals()['LAST_UBX_DOP_TS'] = 0.0
    globals()['LAST_UBX_SATELLITES'] = None
    globals()['LAST_UBX_SATELLITES_TS'] = 0.0

def _crc24q(data: bytes) -> int:
    crc = 0
    poly = 0x1864CFB
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= poly
            crc &= 0xFFFFFF
    return crc

def is_valid_rtcm3_packet(packet: bytes) -> bool:
    if not packet or len(packet) < 6 or packet[0] != 0xD3:
        return False

    payload_len = ((packet[1] & 0x03) << 8) | packet[2]
    if len(packet) != payload_len + 6:
        return False

    expected_crc = _crc24q(packet[:-3])
    packet_crc = (packet[-3] << 16) | (packet[-2] << 8) | packet[-1]
    return expected_crc == packet_crc


def extract_valid_rtcm3_packets(stream_buffer: bytearray, max_packet_len: int = 2048) -> list[bytes]:
    """Extract only complete, CRC-valid RTCM3 packets from a streaming buffer."""
    packets: list[bytes] = []

    while True:
        if len(stream_buffer) < 3:
            return packets

        preamble_idx = stream_buffer.find(0xD3)
        if preamble_idx < 0:
            stream_buffer.clear()
            break

        if preamble_idx > 0:
            del stream_buffer[:preamble_idx]

        if len(stream_buffer) < 3:
            break

        payload_len = ((stream_buffer[1] & 0x03) << 8) | stream_buffer[2]
        packet_len = payload_len + 6

        if packet_len < 6 or packet_len > max_packet_len:
            del stream_buffer[0]
            continue

        if len(stream_buffer) < packet_len:
            break

        candidate = bytes(stream_buffer[:packet_len])
        if is_valid_rtcm3_packet(candidate):
            packets.append(candidate)
            del stream_buffer[:packet_len]
        else:
            del stream_buffer[0]

    return packets

def _rtcm_getbitu(buff: bytes, pos: int, length: int) -> int:
    bits = 0
    for i in range(pos, pos + length):
        bits = (bits << 1) + ((buff[i // 8] >> (7 - i % 8)) & 1)
    return bits

def _rtcm_getbits(buff: bytes, pos: int, length: int) -> int:
    bits = _rtcm_getbitu(buff, pos, length)
    if length <= 0:
        return 0
    sign_bit = 1 << (length - 1)
    if bits & sign_bit:
        bits -= (1 << length)
    return int(bits)

def _rtcm_getbits_38(buff: bytes, pos: int) -> float:
    return float(_rtcm_getbits(buff, pos, 38))

def _decode_type1021(buff: bytes, datum: RTCMDatum):
    i = 36
    n = _rtcm_getbitu(buff, i, 5)
    m = _rtcm_getbitu(buff, i + 5 + 8 * n, 5)
    if i + 400 + 8 * n + 8 * m > len(buff) * 8 + 24:
        return

    i += 5 + 8 * n
    i += 5 + 8 * m
    i += 8 + 10 + 5 + 4 + 2 + 19 + 20 + 14 + 14

    dx = _rtcm_getbits(buff, i, 23); i += 23
    dy = _rtcm_getbits(buff, i, 23); i += 23
    dz = _rtcm_getbits(buff, i, 23); i += 23
    rx = _rtcm_getbits(buff, i, 32); i += 32
    ry = _rtcm_getbits(buff, i, 32); i += 32
    rz = _rtcm_getbits(buff, i, 32); i += 32
    ds = _rtcm_getbits(buff, i, 25)

    datum.dX = float(dx) * 0.001
    datum.dY = float(dy) * 0.001
    datum.dZ = float(dz) * 0.001
    datum.rX = float(rx) * 0.00002
    datum.rY = float(ry) * 0.00002
    datum.rZ = float(rz) * 0.00002
    datum.kT = float(ds) * 0.00000000001 + 1.0
    datum.tra_update = 2
    datum.rtcm1021_update = True

def _decode_type1023(buff: bytes, datum: RTCMDatum):
    i = 24 + 12
    if i + 566 > len(buff) * 8 + 24:
        return
    i += 8 + 1 + 1 + 21 + 22 + 12 + 12
    m_lat_o = _rtcm_getbits(buff, i, 8); i += 8
    m_lon_o = _rtcm_getbits(buff, i, 8); i += 8
    m_h_o = _rtcm_getbits(buff, i, 15); i += 15
    dlat_res = []
    dlon_res = []
    dhei_res = []
    for _ in range(16):
        dlat_res.append(float(_rtcm_getbits(buff, i, 9))); i += 9
        dlon_res.append(float(_rtcm_getbits(buff, i, 9))); i += 9
        dhei_res.append(float(_rtcm_getbits(buff, i, 9))); i += 9

    res_b_sec = float(m_lat_o) * 0.001 + (dlat_res[5] + dlat_res[6] + dlat_res[9] + dlat_res[10]) * 0.00003 / 4.0
    res_l_sec = float(m_lon_o) * 0.001 + (dlon_res[5] + dlon_res[6] + dlon_res[9] + dlon_res[10]) * 0.00003 / 4.0
    res_h_m = float(m_h_o) * 0.01 + (dhei_res[5] + dhei_res[6] + dhei_res[9] + dhei_res[10]) * 0.001 / 4.0

    datum.resB = res_b_sec * (math.pi / (3600.0 * 180.0))
    datum.resL = res_l_sec * (math.pi / (3600.0 * 180.0))
    datum.resH = -res_h_m
    datum.tra_update += 1
    datum.res_update = True
    datum.rtcm1023_update = True

def _decode_type1025(buff: bytes, datum: RTCMDatum):
    i = 24 + 12
    if i + 184 > len(buff) * 8 + 24:
        return
    i += 8  # station id
    projection_type = _rtcm_getbitu(buff, i, 6); i += 6
    _ = _rtcm_getbits(buff, i, 34); i += 34  # latitude of natural origin
    lon_no = _rtcm_getbits(buff, i, 35); i += 35  # longitude of natural origin
    sno = _rtcm_getbitu(buff, i, 30); i += 30
    fe = _rtcm_getbitu(buff, i, 36); i += 36
    fn = _rtcm_getbits(buff, i, 35)

    k0 = (float(sno) * 0.00001 + 993000.0) * 0.000001
    # RTCM 1025 LoNO unit: 1.1e-8 degree
    l0_deg_raw = float(lon_no) * 0.000000011
    l0_norm = math.radians(l0_deg_raw)

    # Guard corrupted/invalid decode values.
    if not math.isfinite(l0_norm) or not (math.radians(90.0) <= l0_norm <= math.radians(120.0)):
        logging.warning(
            f"RTCM1025 decode ignored invalid L0 raw_deg={l0_deg_raw}"
        )
        return
    if not math.isfinite(k0) or not (0.9 <= k0 <= 1.1):
        logging.warning(f"RTCM1025 decode ignored invalid k0={k0}")
        return

    datum.l0 = l0_norm
    datum.k0 = k0
    datum.false_easting = float(fe) * 0.001
    datum.false_northing = float(fn) * 0.001
    datum.rtcm1025_update = True
    datum.projection_1025 = {
        'raw_l0': str(lon_no), 'raw_k0': sno, 'projection_type': projection_type,
        'l0_deg': repr(l0_deg_raw), 'k0': repr(k0),
        'false_easting': repr(datum.false_easting), 'false_northing': repr(datum.false_northing),
        'frame_hex': buff[:32].hex(), 'frame_bytes': len(buff), 'frame_truncated': len(buff) > 32,
    }
    logging.info(
        "RTCM1025 datum updated: processor=python_agent; "
        f"LoNO={lon_no}, SNO={sno}, PT={projection_type}, "
        f"L0_deg={math.degrees(datum.l0)!r}, k0={datum.k0!r}, "
        f"FE={datum.false_easting!r}, FN={datum.false_northing!r}, "
        f"frame_bytes={len(buff)}, hex={datum.projection_1025['frame_hex']}"
    )

def _update_datum_from_rtcm_packet(packet: bytes, datum=None):
    if len(packet) < 6 or packet[0] != 0xD3:
        return
    try:
        msg = _rtcm_getbitu(packet, 24, 12)
    except Exception:
        return

    if msg not in (1021, 1023, 1025):
        return

    try:
        with DATUM_LOCK:
            target = AUTO_BASE_DATUM if datum is None else datum
            if msg == 1021:
                _decode_type1021(packet, target)
            elif msg == 1023:
                _decode_type1023(packet, target)
            elif msg == 1025:
                _decode_type1025(packet, target)
    except Exception as e:
        logging.debug(f"RTCM datum decode failed (msg={msg}): {e}")

def _receive_auto_base_correction(packet, reader, datum, counts):
    """Only this CORS session may supply the transformation parameters for its result."""
    if not is_valid_rtcm3_packet(packet):
        return
    message = str(_rtcm_getbitu(packet, 24, 12))
    with DATUM_LOCK:
        counts[message] = counts.get(message, 0) + 1
    _update_datum_from_rtcm_packet(packet, datum)
    reader.inject_rtcm(packet)


def _datum_snapshot() -> RTCMDatum:
    with DATUM_LOCK:
        d = AUTO_BASE_DATUM
        snap = RTCMDatum()
        snap.__dict__.update(d.__dict__)
        snap.elip = EllipPara(**d.elip.__dict__)
        return snap

def _llh_rad_to_ecef(phi: float, lam: float, h: float, a: float, b: float):
    e2 = 1.0 - (b * b) / (a * a)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    n = a / math.sqrt(1.0 - e2 * sin_phi * sin_phi)
    x = (n + h) * cos_phi * math.cos(lam)
    y = (n + h) * cos_phi * math.sin(lam)
    z = (n * (1.0 - e2) + h) * sin_phi
    return x, y, z

def _ecef_to_llh_rad(x: float, y: float, z: float, a: float, b: float):
    e2 = 1.0 - (b * b) / (a * a)
    ep2 = (a * a - b * b) / (b * b)
    p = math.sqrt(x * x + y * y)
    if p < 1e-12:
        lat = math.copysign(math.pi / 2.0, z)
        lon = 0.0
        h = abs(z) - b
        return lat, lon, h
    theta = math.atan2(z * a, p * b)
    st = math.sin(theta)
    ct = math.cos(theta)
    lat = math.atan2(z + ep2 * b * st * st * st, p - e2 * a * ct * ct * ct)
    lon = math.atan2(y, x)
    sin_lat = math.sin(lat)
    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - n
    return lat, lon, h

def _blh_to_xyh(l0: float, k0: float, phi: float, lam: float, h: float, datum: RTCMDatum):
    a = datum.elip.a
    e2 = datum.elip.e2
    ep2 = e2 / (1.0 - e2)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    tan_phi = math.tan(phi)
    n = a / math.sqrt(1.0 - e2 * sin_phi * sin_phi)
    t = tan_phi * tan_phi
    c = ep2 * cos_phi * cos_phi
    A = (lam - l0) * cos_phi
    e4 = e2 * e2
    e6 = e4 * e2
    m = a * (
        (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0) * phi
        - (3.0 * e2 / 8.0 + 3.0 * e4 / 32.0 + 45.0 * e6 / 1024.0) * math.sin(2.0 * phi)
        + (15.0 * e4 / 256.0 + 45.0 * e6 / 1024.0) * math.sin(4.0 * phi)
        - (35.0 * e6 / 3072.0) * math.sin(6.0 * phi)
    )
    north = datum.false_northing + k0 * (
        m + n * tan_phi * (
            A * A / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * A**4 / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * ep2) * A**6 / 720.0
        )
    )
    east = datum.false_easting + k0 * n * (
        A
        + (1.0 - t + c) * A**3 / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * ep2) * A**5 / 120.0
    )
    return north, east, h

def _xy_to_blh(l0: float, k0: float, north: float, east: float, h: float, datum: RTCMDatum):
    a = datum.elip.a
    e2 = datum.elip.e2
    ep2 = e2 / (1.0 - e2)
    x = (north - datum.false_northing) / k0
    y = (east - datum.false_easting) / k0
    e4 = e2 * e2
    e6 = e4 * e2
    mu = x / (a * (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))
    j1 = (3.0 * e1 / 2.0) - (27.0 * e1**3 / 32.0)
    j2 = (21.0 * e1**2 / 16.0) - (55.0 * e1**4 / 32.0)
    j3 = (151.0 * e1**3 / 96.0)
    j4 = (1097.0 * e1**4 / 512.0)
    fp = mu + j1 * math.sin(2.0 * mu) + j2 * math.sin(4.0 * mu) + j3 * math.sin(6.0 * mu) + j4 * math.sin(8.0 * mu)
    sf = math.sin(fp)
    cf = math.cos(fp)
    tf = math.tan(fp)
    c1 = ep2 * cf * cf
    t1 = tf * tf
    n1 = a / math.sqrt(1.0 - e2 * sf * sf)
    r1 = n1 * (1.0 - e2) / (1.0 - e2 * sf * sf)
    d = y / n1
    lat = fp - (n1 * tf / r1) * (
        d * d / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1 * c1 - 9.0 * ep2) * d**4 / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1 * t1 - 252.0 * ep2 - 3.0 * c1 * c1) * d**6 / 720.0
    )
    lon = l0 + (
        d
        - (1.0 + 2.0 * t1 + c1) * d**3 / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1 * c1 + 8.0 * ep2 + 24.0 * t1 * t1) * d**5 / 120.0
    ) / cf
    return lat, lon, h

def _vn2000_blh_to_local_wgs84_llh(lat_blh: float, lon_blh: float, h_blh: float, datum: RTCMDatum):
    """
    Follow vn20002llh() flow from legacy implementation:
    BLH(VN2000) -> XYZ -> inverse 7-parameter -> LLH(local/WGS84-like)
    """
    a = datum.elip.a
    b = datum.elip.b
    x, y, z = _llh_rad_to_ecef(lat_blh, lon_blh, h_blh, a, b)

    ro = math.pi / (180.0 * 3600.0)
    d_x0 = -191.90441429
    d_y0 = -39.30318279
    d_z0 = -111.45032835
    omega0 = -0.00928836 * ro
    phi0 = 0.01975479 * ro
    epsilon0 = -0.00427372 * ro
    k = 1.000000252906278

    xv = d_x0 + k * (x + epsilon0 * y - phi0 * z)
    yv = d_y0 + k * (-epsilon0 * x + y + omega0 * z)
    zv = d_z0 + k * (phi0 * x - omega0 * y + z)

    lat_local, lon_local, h_local = _ecef_to_llh_rad(xv, yv, zv, a, b)
    return lat_local, lon_local, h_local

def _transform_itrf_to_vn2000_neh_and_llh(lat_deg: float, lon_deg: float, h: float, datum: RTCMDatum):
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    a = datum.elip.a
    b = datum.elip.b
    x, y, z = _llh_rad_to_ecef(phi, lam, h, a, b)

    ro = math.pi / (180.0 * 3600.0)
    omega = datum.rX * ro
    phi0 = datum.rY * ro
    eps = datum.rZ * ro
    k = datum.kT

    xv = datum.dX + k * (x + eps * y - phi0 * z)
    yv = datum.dY + k * (-eps * x + y + omega * z)
    zv = datum.dZ + k * (phi0 * x - omega * y + z)
    lat_vn, lon_vn, h_vn = _ecef_to_llh_rad(xv, yv, zv, a, b)

    if datum.res_update:
        lat_vn += datum.resB
        lon_vn += datum.resL
        h_vn += datum.resH

    north, east, h_ne = _blh_to_xyh(datum.l0, datum.k0, lat_vn, lon_vn, h_vn, datum)
    if datum.shift_cors:
        north += datum.resN
        east += datum.resE
        h_ne += datum.resHS

    lat_blh, lon_blh, h_blh = _xy_to_blh(datum.l0, datum.k0, north, east, h_ne, datum)
    lat_local, lon_local, h_local = _vn2000_blh_to_local_wgs84_llh(lat_blh, lon_blh, h_blh, datum)
    return north, east, h_ne, math.degrees(lat_local), math.degrees(lon_local), h_local

def _is_valid_vn2000_projection_params(datum: RTCMDatum) -> bool:
    l0_deg = math.degrees(datum.l0)
    if not math.isfinite(l0_deg) or not math.isfinite(datum.k0):
        return False
    # Vietnam practical bounds for central meridian and projection scale.
    if not (100.0 <= l0_deg <= 112.0):
        return False
    if not (0.9990 <= datum.k0 <= 1.0010):
        return False
    return True

def _resolve_auto_base_location(lat, lon, alt):
    # Province boundaries and projection regions belong to the server catalog.
    scheme = 'https' if BACKEND_WS_SCHEME == 'wss' else 'http'
    url = f'{scheme}://{BACKEND_WS_HOST}:{BACKEND_WS_PORT}/api/coordinates/auto-base-location'
    coord = {'lat':lat, 'lon':lon, 'alt':alt}
    request = urllib.request.Request(url, data=json.dumps(coord).encode('utf-8'),
                                    headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read(65536))
    if result.get('input_coord') != coord:
        raise ValueError('Server location response does not match sampled LLH')
    return result

def is_valid_nmea_checksum(sentence) -> bool:
    try:
        if isinstance(sentence, (bytes, bytearray)):
            s = sentence.decode('ascii', errors='ignore').strip()
        else:
            s = str(sentence).strip()

        if not s.startswith('$') or '*' not in s:
            return False

        body, checksum_part = s[1:].split('*', 1)
        if len(checksum_part) < 2:
            return False

        given = checksum_part[:2].upper()
        calc = 0
        for ch in body:
            calc ^= ord(ch)

        return f"{calc:02X}" == given
    except Exception:
        return False
    
# ==============================================================================
# === DISPATCHER FUNCTIONS                                                  ===
# ==============================================================================
def dispatch_rtcm_data(data):
    global rtcm_input_window_bytes, rtcm_input_window_start_ts, rtcm_input_bps
    # Parse datum messages (1021/1023/1025) regardless of stream forwarding state.
    _update_datum_from_rtcm_packet(data)
    now = time.time()
    with rtcm_input_stats_lock:
        rtcm_input_window_bytes += len(data)
        elapsed = now - rtcm_input_window_start_ts
        if elapsed >= 1.0:
            rtcm_input_bps = int(rtcm_input_window_bytes / elapsed)
            rtcm_input_window_bytes = 0
            rtcm_input_window_start_ts = now

    global rtcm_mqtt_queue
    if 'rtcm_mqtt_queue' in globals() and rtcm_mqtt_queue:
        try:
            rtcm_mqtt_queue.put_nowait(data)
        except Exception:
            pass

    # Check if RTCM streaming is active (can be disabled by SET_RTCM_STREAM_ACTIVE command)
    global rtcm_stream_active_flag
    if not rtcm_stream_active_flag:
        return  # Drop RTCM data if stream is inactive
    
    with subscriber_lock:
        for queue in list(rtcm_subscribers):
            try:
                queue.put_nowait(data)
            except Full:
                _telemetry_inc("queue", "rtcm_dispatch_drops")
                # Drop 2 oldest packets and force insert
                try:
                    queue.get_nowait()
                    queue.get_nowait()
                    queue.put_nowait(data)
                except (Empty, Full):
                    pass

def _update_dispatched_nmea_health(text):
    global LAST_NMEA_GSV_GSA_TS
    if text and '$G' in text:
        _update_local_nmea_health(text.strip())
        if any(sentence_type in text for sentence_type in ('GSV', 'GSA')):
            LAST_NMEA_GSV_GSA_TS = time.time()


def _record_dispatched_gga(text):
    try:
        new_status, lat, lon, alt = parse_gga_data(text)
        globals()['LAST_GGA_FIX_STATUS'] = new_status
        if lat is not None:
            globals()['LAST_GGA_COORD'] = (lat, lon, alt)
        raw_line = text.strip()
        if '\n' in raw_line:
            raw_line = next((line.strip() for line in raw_line.split('\n') if 'GGA' in line), raw_line)
        globals()['LAST_RAW_GGA'] = raw_line
        globals()['LAST_RAW_GGA_TS'] = time.time()
        agent = globals().get('agent')
        if agent and hasattr(agent, 'last_gga_fix_status'):
            agent.last_gga_fix_status = new_status
        now_ts = time.time()
        if now_ts - getattr(dispatch_nmea_data, '_last_gga_log_ts', 0) >= 2.0:
            dispatch_nmea_data._last_gga_log_ts = now_ts
            lat_str = repr(lat) if lat is not None else "None"
            lon_str = repr(lon) if lon is not None else "None"
            alt_str = f"{alt!r}m" if alt is not None else "None"
            logging.info(f"[GNSS NMEA] Rx GGA: {raw_line} | Fix={new_status} | Lat={lat_str} Lon={lon_str} Alt={alt_str}")
    except Exception:
        pass


def dispatch_nmea_data(data):
    """Update NMEA status and fan out data with overflow protection."""
    try:
        text = data.decode('ascii', errors='ignore') if isinstance(data, (bytes, bytearray)) else data
        if isinstance(text, str):
            _update_dispatched_nmea_health(text)
            if '$G' in text and 'GGA' in text[:10]:
                _record_dispatched_gga(text)
    except Exception:
        pass

    with subscriber_lock:
        for queue in list(nmea_subscribers):
            try:
                queue.put_nowait(data)
            except Full:
                _telemetry_inc("queue", "nmea_dispatch_drops")
                try:
                    queue.get_nowait()
                    queue.put_nowait(data)
                except (Empty, Full):
                    pass  # Silently skip NMEA on overflow

def _base_config_expects_nmea_skyview(agent_manager=None) -> bool:
    """
    Return False only when the UI explicitly configured RTCM-only output.

    RTCM MSM packets are still forwarded to NTRIP in combined modes, but they
    must not be decoded and republished on raw_data. Defaulting to NMEA here is
    intentional: an absent/stale config must never make calculated RTCM JSON
    replace the receiver's GGA/GST/GSV sentences.
    """
    try:
        # Prefer the persisted agent configuration.  LAST_CONFIGURED_OUTPUT_MODE
        # is only a runtime cache and is reset whenever the process restarts.
        ag = agent_manager or globals().get('agent')
        base_cfg = ag.get_base_config() if ag and hasattr(ag, 'get_base_config') else {}
        if isinstance(base_cfg, dict) and base_cfg:
            gnss_options = base_cfg.get("gnss_options") or {}
            output_mode = str(gnss_options.get("output_mode") or "").strip().lower()
            if output_mode:
                return output_mode != "rtcm_only"

        configured_mode = globals().get("LAST_CONFIGURED_OUTPUT_MODE")
        if configured_mode:
            return str(configured_mode).strip().lower() != "rtcm_only"
        return True
    except Exception:
        return True

def _remember_output_mode_from_original_config(data: dict) -> None:
    try:
        params = ((data.get("original_config") or {}).get("params") or {})
        gnss_options = params.get("gnss_options") or {}
        output_mode = str(gnss_options.get("output_mode") or "").strip().lower()
        if output_mode:
            globals()["LAST_CONFIGURED_OUTPUT_MODE"] = output_mode
    except Exception:
        pass

# ==============================================================================
# === RTCM DECODER INTEGRATION                                              ===
# ==============================================================================
from typing import Any, NamedTuple

CRC24Q_POLY = 0x1864CFB
MAX_BUFFER_BYTES = 65536

# Ephemeris Constants
MU_GPS   = 3.9860050E14     # gravitational constant
MU_GAL   = 3.986004418E14   # galileo
MU_CMP   = 3.986004418E14   # beidou
OMGE     = 7.2921151467E-5  # earth angular velocity (IS-GPS)
OMGE_GAL = 7.2921151467E-5  # earth angular velocity (Galileo)
OMGE_CMP = 7.292115E-5      # earth angular velocity (BeiDou)
RTOL_KEPLER = 1E-13         # relative tolerance for Kepler equation
MAX_ITER_KEPLER = 30        # max number of iterations for Kepler


def _build_crc24q_table() -> list[int]:
    table: list[int] = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= CRC24Q_POLY
        table.append(crc & 0xFFFFFF)
    return table


CRC24Q_TABLE = _build_crc24q_table()


def crc24q(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ CRC24Q_TABLE[((crc >> 16) ^ byte) & 0xFF]) & 0xFFFFFF
    return crc


class BitReader:
    def __init__(self, data: bytes, bit_index: int = 0) -> None:
        self.data = data
        self.bit_index = bit_index

    def read_uint(self, bits: int) -> int:
        value = 0
        for _ in range(bits):
            byte_index = self.bit_index >> 3
            bit_offset = 7 - (self.bit_index & 7)
            self.bit_index += 1
            value = (value << 1) | ((self.data[byte_index] >> bit_offset) & 1)
        return value

    def read_int(self, bits: int) -> int:
        value = self.read_uint(bits)
        if value & (1 << (bits - 1)):
            value -= (1 << bits)
        return value

    def skip(self, bits: int) -> None:
        self.bit_index += bits


class MsmTypeInfo(NamedTuple):
    base: int
    system: str
    nav_system: str


MSM_TYPES = (
    MsmTypeInfo(1071, "us", "GPS"),
    MsmTypeInfo(1081, "ru", "GLONASS"),
    MsmTypeInfo(1091, "eu", "GALILEO"),
    MsmTypeInfo(1101, "un", "SBAS"),
    MsmTypeInfo(1111, "jp", "QZSS"),
    MsmTypeInfo(1121, "cn", "BEIDOU"),
    MsmTypeInfo(1131, "un", "IRNSS"),
)


def _message_type_info(message_type: int) -> tuple[MsmTypeInfo, int] | None:
    for info in MSM_TYPES:
        msm_level = message_type - info.base + 1
        if 1 <= msm_level <= 7:
            return info, msm_level
    return None


def _active_ids(mask: int, width: int) -> list[int]:
    return [idx for idx in range(1, width + 1) if (mask >> (width - idx)) & 1]


class RTCMSignalDecoder:
    def __init__(self) -> None:
        self._buffers: dict[str, bytes] = {}
        self._base_positions: dict[str, tuple[float, float, float]] = {}
        self._ephemeris_data: dict[str, dict[str, dict[str, Any]]] = {}
        self._azel_cache: dict[str, dict[str, tuple[float, float, float]]] = {}

    @staticmethod
    def _llh2ecef(lat_deg: float, lon_deg: float, height: float) -> tuple[float, float, float]:
        lat = math.radians(lat_deg)
        lon = math.radians(lon_deg)
        a = 6378137.0
        f = 1.0 / 298.257223563
        e2 = f * (2.0 - f)
        sin_lat = math.sin(lat)
        cos_lat = math.cos(lat)
        n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
        x = (n + height) * cos_lat * math.cos(lon)
        y = (n + height) * cos_lat * math.sin(lon)
        z = (n * (1.0 - e2) + height) * sin_lat
        return x, y, z

    @staticmethod
    def _glonass_eph2pos(eph: dict[str, Any], time_s: float) -> tuple[float, float, float] | None:
        import time
        # time_s is gps epoch in ms / 1000, but GLONASS uses UTC/Moscow time.
        # It's easier to just use the current time because RTCM packets are real-time.
        now = time.time()
        utc_tod = now % 86400.0
        dt = utc_tod - eph['tb']
        if dt > 43200: dt -= 86400
        elif dt < -43200: dt += 86400
        
        pos = list(eph['pos'])
        vel = list(eph['vel'])
        acc = eph['acc']
        
        GM = 398600.4418e9
        J2 = 1.0826257e-3
        ae = 6378136.0
        
        def derivatives(p, v):
            x, y, z = p
            vx, vy, vz = v
            r2 = x*x + y*y + z*z
            r = math.sqrt(r2)
            r3 = r2 * r
            a = 1.5 * J2 * GM * (ae**2) / (r2 * r3)
            b = 5.0 * z*z / r2
            c = -GM / r3 - a * (1.0 - b)
            
            w = 7.292115e-5
            
            ax = c * x + 2.0 * w * vy + w*w * x + acc[0]
            ay = c * y - 2.0 * w * vx + w*w * y + acc[1]
            az = -GM * z / r3 - a * (3.0 - b) * z + acc[2]
            return [vx, vy, vz], [ax, ay, az]
            
        step = 60.0
        sgn = 1.0 if dt > 0 else -1.0
        rem = abs(dt)
        
        while rem > 0:
            h = step if rem > step else rem
            h *= sgn
            
            dp1, dv1 = derivatives(pos, vel)
            p_k1 = [pos[i] + dp1[i]*h/2 for i in range(3)]
            v_k1 = [vel[i] + dv1[i]*h/2 for i in range(3)]
            
            dp2, dv2 = derivatives(p_k1, v_k1)
            p_k2 = [pos[i] + dp2[i]*h/2 for i in range(3)]
            v_k2 = [vel[i] + dv2[i]*h/2 for i in range(3)]
            
            dp3, dv3 = derivatives(p_k2, v_k2)
            p_k3 = [pos[i] + dp3[i]*h for i in range(3)]
            v_k3 = [vel[i] + dv3[i]*h for i in range(3)]
            
            dp4, dv4 = derivatives(p_k3, v_k3)
            
            for i in range(3):
                pos[i] += h * (dp1[i] + 2*dp2[i] + 2*dp3[i] + dp4[i]) / 6.0
                vel[i] += h * (dv1[i] + 2*dv2[i] + 2*dv3[i] + dv4[i]) / 6.0
                
            rem -= abs(h)
            
        return pos[0], pos[1], pos[2]

    @staticmethod
    def _eph2pos(eph: dict[str, float], time_s: float, sys_name: str) -> tuple[float, float, float] | None:
        if sys_name == 'BEIDOU':
            MU = MU_CMP
            OMGE_SYS = OMGE_CMP
        elif sys_name == 'GALILEO':
            MU = MU_GAL
            OMGE_SYS = OMGE_GAL
        else:
            MU = MU_GPS
            OMGE_SYS = OMGE

        tk = time_s - eph['toe']
        if tk > 302400.0: tk -= 604800.0
        elif tk < -302400.0: tk += 604800.0

        A = eph.get('A', 0)
        if A <= 0.0:
            return None

        M = eph['M0'] + (math.sqrt(MU / (A**3)) + eph['deln']) * tk
        E = M
        for _ in range(MAX_ITER_KEPLER):
            ek = E
            E = M + eph['e'] * math.sin(ek)
            if abs(E - ek) < RTOL_KEPLER:
                break

        sin_e = math.sin(E)
        cos_e = math.cos(E)
        nu = math.atan2(math.sqrt(1.0 - eph['e']**2) * sin_e, cos_e - eph['e'])
        phi = nu + eph['omg']
        sin2phi = math.sin(2.0 * phi)
        cos2phi = math.cos(2.0 * phi)

        u = phi + eph['cuc'] * cos2phi + eph['cus'] * sin2phi
        r = A * (1.0 - eph['e'] * cos_e) + eph['crc'] * cos2phi + eph['crs'] * sin2phi
        i = eph['i0'] + eph['idot'] * tk + eph['cic'] * cos2phi + eph['cis'] * sin2phi

        x = r * math.cos(u)
        y = r * math.sin(u)

        if sys_name == 'BEIDOU' and eph.get('is_geo', False):
            o_mg = eph['OMG0'] + eph['OMGd'] * tk - OMGE_SYS * eph['toe']
            xg = x * math.cos(o_mg) - y * math.cos(i) * math.sin(o_mg)
            yg = x * math.sin(o_mg) + y * math.cos(i) * math.cos(o_mg)
            zg = y * math.sin(i)
            sina = math.sin(-5.0 * math.pi / 180.0)
            cosa = math.cos(-5.0 * math.pi / 180.0)
            rs_x =  xg * cosa + zg * sina
            rs_y =  yg
            rs_z = -xg * sina + zg * cosa
        else:
            o_mg = eph['OMG0'] + (eph['OMGd'] - OMGE_SYS) * tk - OMGE_SYS * eph['toe']
            cos_o = math.cos(o_mg)
            sin_o = math.sin(o_mg)
            cosi = math.cos(i)
            sini = math.sin(i)
            rs_x = x * cos_o - y * cosi * sin_o
            rs_y = x * sin_o + y * cosi * cos_o
            rs_z = y * sini

        return (rs_x, rs_y, rs_z)

    @staticmethod
    def _ecef2pos(x: float, y: float, z: float) -> tuple[float, float, float]:
        a = 6378137.0
        f = 1.0 / 298.257223563
        e2 = f * (2 - f)
        r = math.sqrt(x**2 + y**2)
        if r < 1e-12:
            return 0.0, math.pi/2 if z > 0 else -math.pi/2, z - a*(1-f)
        v = a
        lat = math.atan2(z, r * (1 - e2))
        for _ in range(10):
            sinp = math.sin(lat)
            v = a / math.sqrt(1 - e2 * sinp**2)
            lat_new = math.atan2(z + e2 * v * sinp, r)
            if abs(lat_new - lat) < 1e-12:
                break
            lat = lat_new
        lon = math.atan2(y, x)
        h = math.sqrt(r**2 + z**2) - v # simplified height
        return lat, lon, h

    @staticmethod
    def _satazel(sat_ecef: tuple[float, float, float], base_ecef: tuple[float, float, float]) -> tuple[float, float]:
        r = math.sqrt(sum((sat_ecef[i] - base_ecef[i])**2 for i in range(3)))
        if r < 1e-12: return 0.0, 0.0
        e = [(sat_ecef[i] - base_ecef[i]) / r for i in range(3)]
        lat, lon, _ = RTCMSignalDecoder._ecef2pos(*base_ecef)
        sinp = math.sin(lat)
        cosp = math.cos(lat)
        sinl = math.sin(lon)
        cosl = math.cos(lon)
        enu_x = -sinl * e[0] + cosl * e[1]
        enu_y = -sinp * cosl * e[0] - sinp * sinl * e[1] + cosp * e[2]
        enu_z = cosp * cosl * e[0] + cosp * sinl * e[1] + sinp * e[2]
        az = math.atan2(enu_x, enu_y)
        if az < 0: az += 2 * math.pi
        el = math.asin(enu_z)
        return math.degrees(az), math.degrees(el)

    def _decode_1005(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 138: return
        reader = BitReader(payload, 12)
        reader.skip(12) # stn_id
        reader.skip(6)  # itrf
        reader.skip(1)  # gps_ind
        reader.skip(1)  # glo_ind
        reader.skip(1)  # gal_ind
        reader.skip(1)  # ref_stn_ind
        x = reader.read_int(38) / 10000.0
        reader.skip(1)  # osc
        reader.skip(1)  # res
        y = reader.read_int(38) / 10000.0
        reader.skip(2)  # q_ind
        z = reader.read_int(38) / 10000.0
        self._base_positions[serial] = (x, y, z)

    def _decode_1019(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 476: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(10) # week
        reader.skip(4) # sva
        reader.skip(2) # code
        idot = reader.read_int(14) * (2**-43) * math.pi
        reader.skip(8) # iode
        toc = reader.read_uint(16) * 16.0
        f2 = reader.read_int(8) * (2**-55)
        f1 = reader.read_int(16) * (2**-43)
        f0 = reader.read_int(22) * (2**-31)
        reader.skip(10) # iodc
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrt_a = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(16) * 16.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        omg_d = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:us:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrt_a**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': omg_d, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }

    def _decode_1020(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 336: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        _ = reader.read_uint(5) - 7
        reader.skip(4)
        _ = reader.read_uint(5)
        _ = reader.read_uint(6)
        _ = reader.read_uint(1) * 30.0
        _ = reader.read_uint(1)
        reader.skip(1)
        tb = reader.read_uint(7)
        vel_x = reader.read_int(24) * (2**-20) * 1000.0
        pos_x = reader.read_int(27) * (2**-11) * 1000.0
        acc_x = reader.read_int(5) * (2**-30) * 1000.0
        vel_y = reader.read_int(24) * (2**-20) * 1000.0
        pos_y = reader.read_int(27) * (2**-11) * 1000.0
        acc_y = reader.read_int(5) * (2**-30) * 1000.0
        vel_z = reader.read_int(24) * (2**-20) * 1000.0
        pos_z = reader.read_int(27) * (2**-11) * 1000.0
        acc_z = reader.read_int(5) * (2**-30) * 1000.0
        reader.skip(1)
        _ = reader.read_int(11) * (2**-40)
        reader.skip(3)
        _ = reader.read_int(22) * (2**-30)
        
        tb_time = tb * 900.0 - 10800.0
        if tb_time < 0: tb_time += 86400.0
        
        sat_id = f"RTCM:ru:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'type': 'glonass',
            'pos': (pos_x, pos_y, pos_z),
            'vel': (vel_x, vel_y, vel_z),
            'acc': (acc_x, acc_y, acc_z),
            'tb': tb_time
        }

    def _decode_1042(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 499: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(13) # week
        reader.skip(4) # sva
        idot = reader.read_int(14) * (2**-43) * math.pi
        reader.skip(5) # aodc
        toc = reader.read_uint(17) * 8.0
        f2 = reader.read_int(11) * (2**-66)
        f1 = reader.read_int(22) * (2**-50)
        f0 = reader.read_int(24) * (2**-33)
        reader.skip(5) # aode
        crs = reader.read_int(18) * (2**-6)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(18) * (2**-31)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(18) * (2**-31)
        sqrt_a = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(17) * 8.0
        cic = reader.read_int(18) * (2**-31)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(18) * (2**-31)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(18) * (2**-6)
        omg = reader.read_int(32) * (2**-31) * math.pi
        omg_d = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:cn:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        is_geo = (1 <= prn <= 5) or (59 <= prn <= 63)
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrt_a**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': omg_d, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2, 'is_geo': is_geo
        }

    def _decode_1044(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 473: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(4) + 192
        toc = reader.read_uint(16) * 16.0
        f2 = reader.read_int(8) * (2**-55)
        f1 = reader.read_int(16) * (2**-43)
        f0 = reader.read_int(22) * (2**-31)
        _ = reader.read_uint(8)
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrt_a = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(16) * 16.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        omg_d = reader.read_int(24) * (2**-43) * math.pi
        idot = reader.read_int(14) * (2**-43) * math.pi
        
        sat_id = f"RTCM:jp:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrt_a**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': omg_d, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }

    def _decode_1045(self, payload: bytes, serial: str) -> None:
        if len(payload) * 8 < 12 + 484: return
        reader = BitReader(payload, 12)
        prn = reader.read_uint(6)
        reader.skip(12 + 10 + 8) # week, iode, sva
        idot = reader.read_int(14) * (2**-43) * math.pi
        toc = reader.read_uint(14) * 60.0
        f2 = reader.read_int(6) * (2**-59)
        f1 = reader.read_int(21) * (2**-46)
        f0 = reader.read_int(31) * (2**-34)
        crs = reader.read_int(16) * (2**-5)
        deln = reader.read_int(16) * (2**-43) * math.pi
        M0 = reader.read_int(32) * (2**-31) * math.pi
        cuc = reader.read_int(16) * (2**-29)
        e = reader.read_uint(32) * (2**-33)
        cus = reader.read_int(16) * (2**-29)
        sqrt_a = reader.read_uint(32) * (2**-19)
        toes = reader.read_uint(14) * 60.0
        cic = reader.read_int(16) * (2**-29)
        OMG0 = reader.read_int(32) * (2**-31) * math.pi
        cis = reader.read_int(16) * (2**-29)
        i0 = reader.read_int(32) * (2**-31) * math.pi
        crc = reader.read_int(16) * (2**-5)
        omg = reader.read_int(32) * (2**-31) * math.pi
        omg_d = reader.read_int(24) * (2**-43) * math.pi
        
        sat_id = f"RTCM:eu:{prn}"
        if serial not in self._ephemeris_data: self._ephemeris_data[serial] = {}
        self._ephemeris_data[serial][sat_id] = {
            'A': sqrt_a**2, 'e': e, 'i0': i0, 'OMG0': OMG0, 'omg': omg, 'M0': M0,
            'deln': deln, 'OMGd': omg_d, 'idot': idot, 'crc': crc, 'crs': crs,
            'cuc': cuc, 'cus': cus, 'cic': cic, 'cis': cis, 'toe': toes, 'toc': toc,
            'f0': f0, 'f1': f1, 'f2': f2
        }

    def decode(self, serial: str, payload: bytes) -> dict[str, Any] | None:
        if not payload or (not self._buffers.get(serial) and not self._looks_like_rtcm(payload)):
            return None
        return self.decode_sync(serial, payload)

    def decode_json(self, serial: str, data: dict) -> dict[str, Any] | None:
        nav_system = data.get("navSystem")
        sats_in = data.get("satellites", [])
        if not nav_system or not sats_in:
            return None
        
        system_map = {
            "gps": "G", "us": "G", "g": "G",
            "glonass": "R", "ru": "R", "r": "R",
            "galileo": "E", "eu": "E", "e": "E",
            "beidou": "C", "cn": "C", "c": "C",
        }
        sys_code = system_map.get(str(nav_system or "").strip().lower())
        if not sys_code:
            return None
            
        now_ms = int(time.time() * 1000)
        now_s = now_ms / 1000.0
        satellites_out = []
        base_pos = self._base_positions.get(serial)
        
        if serial not in self._azel_cache:
            self._azel_cache[serial] = {}
            
        for sat in sats_in:
            prn = sat.get("prn")
            snr = sat.get("snr", 0)
            if not prn or snr <= 0:
                continue
                
            sat_str_id = f"RTCM:{sys_code}:{prn}"
            elevation = 0.0
            azimuth = 0.0
            
            cached = self._azel_cache[serial].get(sat_str_id)
            if cached and now_s - cached[0] < 10.0:
                azimuth, elevation = cached[1], cached[2]
            else:
                if base_pos:
                    sat_pos = self._satpos(sys_code, prn, now_ms)
                    if sat_pos:
                        az, el = self._satazel(sat_pos, base_pos)
                        azimuth = az
                        elevation = el
                        self._azel_cache[serial][sat_str_id] = (now_s, azimuth, elevation)
            
            satellites_out.append({
                "id": sat_str_id,
                "prn": prn,
                "system": sys_code,
                "snr": snr,
                "cnr": float(snr),
                "signalId": 1,
                "messageType": 9999,
                "isTracking": True,
                "elevation": elevation,
                "azimuth": azimuth,
                "lastSeen": now_ms,
            })
            
        return {
            "navSystem": nav_system,
            "msmLevel": "JSON",
            "satellites": satellites_out
        }

    def get_stats(self, serial: str) -> dict[str, Any]:
        if hasattr(self, '_stats_by_serial'):
            raw = self._stats_by_serial.get(serial, {})
            now = time.time()
            active = {}
            stale = []
            for message_type, stat in raw.items():
                interval = max(0.0, float(stat.get("interval") or 0.0))
                # Fast MSM/base messages disappear promptly after reconfigure;
                # slower ephemeris/descriptor messages retain an adaptive TTL.
                ttl = max(15.0, min(180.0, interval * 3.0 + 2.0))
                age = now - float(stat.get("last_seen") or 0.0)
                if age <= ttl:
                    active[message_type] = {
                        "interval": interval,
                        "count": stat.get("count", 0),
                        "age": round(max(0.0, age), 1),
                    }
                else:
                    stale.append(message_type)
            for message_type in stale:
                raw.pop(message_type, None)
            return active
        return {}

    @staticmethod
    def _extract_rtcm_packet(buffer: bytes) -> tuple[bytes | None, bytes]:
        start = buffer.find(b"\xD3")
        if start < 0:
            return None, b""
        if start > 0:
            buffer = buffer[start:]
        if len(buffer) < 3:
            return None, buffer
        packet_length = 3 + (((buffer[1] & 0x03) << 8) | buffer[2]) + 3
        if len(buffer) < packet_length:
            return None, buffer
        return buffer[:packet_length], buffer[packet_length:]

    def _record_rtcm_message_stats(self, serial: str, message_type: int) -> None:
        import time
        now = time.time()
        if not hasattr(self, '_stats_by_serial'):
            self._stats_by_serial = {}
        if serial not in self._stats_by_serial:
            self._stats_by_serial[serial] = {}
        message_key = str(message_type)
        stats = self._stats_by_serial[serial]
        if message_key not in stats:
            stats[message_key] = {"last_seen": now, "interval": 0.0, "count": 1}
            return
        stat = stats[message_key]
        stat["interval"] = round(now - stat["last_seen"], 2)
        stat["last_seen"] = now
        stat["count"] += 1

    def _decode_rtcm_message(self, message_type: int, payload: bytes, serial: str):
        if message_type in (1005, 1006):
            self._decode_1005(payload, serial)
            return None
        if message_type == 1019:
            self._decode_1019(payload, serial)
            return None
        if message_type == 1020:
            self._decode_1020(payload, serial)
            return None
        if message_type == 1042:
            self._decode_1042(payload, serial)
            return None
        if message_type == 1044:
            self._decode_1044(payload, serial)
            return None
        if message_type == 1045:
            self._decode_1045(payload, serial)
            return None
        return self._decode_msm_payload(message_type, payload, serial)

    @staticmethod
    def _append_rtcm_result(message_type, decoded, packets, satellites_by_id):
        if not decoded:
            packets.append({"messageType": message_type, "satelliteCount": 0})
            return
        packets.append({
            "messageType": message_type,
            "navSystem": decoded["navSystem"],
            "msmLevel": decoded["msmLevel"],
            "satelliteCount": len(decoded["satellites"]),
        })
        for satellite in decoded["satellites"]:
            satellites_by_id[satellite["id"]] = satellite

    def decode_sync(self, serial: str, payload: bytes) -> dict[str, Any] | None:
        buffer = self._buffers.get(serial, b"") + payload
        packets: list[dict[str, Any]] = []
        satellites_by_id: dict[str, dict[str, Any]] = {}
        errors = 0

        while len(buffer) >= 3:
            packet, buffer = self._extract_rtcm_packet(buffer)
            if packet is None:
                break
            if crc24q(packet[:-3]) != int.from_bytes(packet[-3:], "big"):
                errors += 1
                continue

            payload_bytes = packet[3:-3]
            message_type = (payload_bytes[0] << 4) | (payload_bytes[1] >> 4) if len(payload_bytes) >= 2 else -1
            if message_type > 0:
                self._record_rtcm_message_stats(serial, message_type)
            try:
                decoded = self._decode_rtcm_message(message_type, payload_bytes, serial)
            except (IndexError, ValueError):
                decoded = None
                errors += 1
            self._append_rtcm_result(message_type, decoded, packets, satellites_by_id)

        if len(buffer) > MAX_BUFFER_BYTES:
            buffer = buffer[-MAX_BUFFER_BYTES:]
        self._buffers[serial] = buffer
        if not packets and not satellites_by_id and errors == 0:
            return None
        return {
            "type": "rtcm_signal",
            "serial": serial,
            "packets": packets,
            "satellites": list(satellites_by_id.values()),
            "errors": errors,
            "bufferedBytes": len(buffer),
        }

    @staticmethod
    def _read_msm_cell_mask(reader, satellite_ids, signal_ids):
        cells = []
        for satellite_id in satellite_ids:
            for signal_id in signal_ids:
                if reader.read_uint(1):
                    cells.append((satellite_id, signal_id))
        return cells

    def _msm_satellite_angles(self, serial, sat_str_id, epoch_time_ms, nav_system, cache_ttl):
        now = time.time()
        cache = self._azel_cache.setdefault(serial, {})
        cached = cache.get(sat_str_id)
        if cached and now - cached[0] < cache_ttl:
            return cached[1], cached[2]
        base_pos = self._base_positions.get(serial)
        eph = self._ephemeris_data.get(serial, {}).get(sat_str_id)
        if not base_pos or not eph:
            return 0.0, 0.0
        if eph.get('type') == 'glonass':
            sat_pos = self._glonass_eph2pos(eph, epoch_time_ms / 1000.0)
        else:
            sat_pos = self._eph2pos(eph, epoch_time_ms / 1000.0, nav_system)
        if not sat_pos:
            return 0.0, 0.0
        azimuth, elevation = self._satazel(sat_pos, base_pos)
        cache[sat_str_id] = (now, azimuth, elevation)
        return azimuth, elevation

    def _collect_msm_satellites(self, message_type, serial, info, satellite_ids, signal_cnr, epoch_time_ms, now_ms):
        satellites = []
        for satellite_id in satellite_ids:
            best_signal_id, best_cnr = self._best_signal(signal_cnr, satellite_id)
            if best_cnr < 0:
                continue
            prn = satellite_id + 192 if info.system == "jp" and 1 <= satellite_id <= 10 else satellite_id
            sat_str_id = f"RTCM:{info.system}:{prn}"
            azimuth, elevation = self._msm_satellite_angles(
                serial, sat_str_id, epoch_time_ms, info.nav_system, 10.0
            )
            satellites.append({
                "id": sat_str_id, "prn": prn, "system": info.system, "talker": "RTCM",
                "snr": round(best_cnr), "cnr": best_cnr, "signalId": best_signal_id,
                "messageType": message_type, "isTracking": best_cnr > 0,
                "elevation": elevation, "azimuth": azimuth, "lastSeen": now_ms,
            })
        return satellites

    def _decode_msm_payload(self, message_type: int, payload: bytes, serial: str) -> dict[str, Any] | None:
        type_info = _message_type_info(message_type)
        if not type_info:
            return None

        info, msm_level = type_info
        reader = BitReader(payload, 12)
        reader.skip(12)  # reference station id
        epoch_time_ms = reader.read_uint(30)  # gnss epoch time
        reader.skip(1)   # multiple message indicator
        reader.skip(3)   # issue of data station
        reader.skip(7)   # reserved
        reader.skip(2)   # clock steering
        reader.skip(2)   # external clock
        reader.skip(1)   # divergence-free smoothing
        reader.skip(3)   # smoothing interval

        satellite_mask = reader.read_uint(64)
        signal_mask = reader.read_uint(32)
        satellite_ids = _active_ids(satellite_mask, 64)
        signal_ids = _active_ids(signal_mask, 32)
        if not satellite_ids or not signal_ids:
            return {"navSystem": info.nav_system, "msmLevel": msm_level, "satellites": []}

        cell_mask = self._read_msm_cell_mask(reader, satellite_ids, signal_ids)
        self._skip_msm_satellite_data(reader, msm_level, len(satellite_ids))
        signal_cnr = self._read_msm_signal_cnr(reader, msm_level, cell_mask)
        now_ms = int(time.time() * 1000)
        satellites = self._collect_msm_satellites(
            message_type, serial, info, satellite_ids, signal_cnr, epoch_time_ms, now_ms
        )
        return {"navSystem": info.nav_system, "msmLevel": msm_level, "satellites": satellites}

    @staticmethod
    def _skip_msm_satellite_data(reader: BitReader, msm_level: int, satellite_count: int) -> None:
        if msm_level in (1, 2, 3):
            reader.skip(10 * satellite_count)
        elif msm_level == 4:
            reader.skip(18 * satellite_count)
        elif msm_level == 5:
            reader.skip(22 * satellite_count)
        elif msm_level == 6:
            reader.skip(32 * satellite_count)
        elif msm_level == 7:
            reader.skip(36 * satellite_count)

    @staticmethod
    def _read_msm_signal_cnr(
        reader: BitReader,
        msm_level: int,
        cell_mask: list[tuple[int, int]],
    ) -> dict[tuple[int, int], float]:
        signal_cnr: dict[tuple[int, int], float] = {}
        cell_count = len(cell_mask)

        if msm_level in (1, 2, 3):
            return signal_cnr
        if msm_level in (4, 6):
            reader.skip(42 * cell_count)
            for cell in cell_mask:
                signal_cnr[cell] = float(reader.read_uint(6))
            return signal_cnr
        if msm_level == 5:
            reader.skip(42 * cell_count)
            for cell in cell_mask:
                signal_cnr[cell] = reader.read_uint(10) / 16.0
            reader.skip(15 * cell_count)
            return signal_cnr
        if msm_level == 7:
            reader.skip(55 * cell_count)
            for cell in cell_mask:
                signal_cnr[cell] = reader.read_uint(10) / 16.0
            reader.skip(15 * cell_count)
        return signal_cnr

    @staticmethod
    def _best_signal(signal_cnr: dict[tuple[int, int], float], satellite_id: int) -> tuple[int | None, float]:
        best_signal_id: int | None = None
        best_cnr = 0.0
        for (sat_id, signal_id), cnr in signal_cnr.items():
            if sat_id == satellite_id and cnr > best_cnr:
                best_signal_id = signal_id
                best_cnr = cnr
        return best_signal_id, best_cnr

    @staticmethod
    def _looks_like_rtcm(payload: bytes) -> bool:
        return payload[:1] == b"\xD3" or b"\xD3" in payload[:64]


# ==============================================================================
# === WORKER CLASSES                                                        ===
# ==============================================================================
class NMEAPublisher(threading.Thread):
    def __init__(self, mqtt_client: mqtt.Client, serial_number: str, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.daemon = True
        self.name = "NMEAPublisher"
        self._stop_event = threading.Event()
        self.mqtt_client = mqtt_client
        self.serial_number = serial_number
        self.loop = loop
        self.queue = Queue(maxsize=200)
        self.last_mqtt_publish_ts = 0.0
        with subscriber_lock:
            nmea_subscribers.append(self.queue)
    
    def stop(self):
        self._stop_event.set()
        with subscriber_lock:
            if self.queue in nmea_subscribers:
                nmea_subscribers.remove(self.queue)

    def run(self):
        logging.info("NMEA Publisher thread started.")
        topic = f"pi/devices/{self.serial_number}/raw_data"
        
        while not self._stop_event.is_set():
            try:
                if is_remote_locked():
                    time.sleep(1)
                    continue
                
                data_chunk = self.queue.get(timeout=1.0)
                should_forward = _should_forward_nmea_to_backend(data_chunk)
                if not should_forward:
                    _telemetry_inc("queue", "nmea_filtered")
                mqtt_delivered = False
                if self.mqtt_client and self.mqtt_client.is_connected() and should_forward:
                    try:
                        result = self.mqtt_client.publish(topic, data_chunk, qos=0)
                        if result.rc == mqtt.MQTT_ERR_SUCCESS:
                            mqtt_delivered = True
                            _telemetry_inc("queue", "nmea_mqtt_published")
                        else:
                            _telemetry_inc("queue", "nmea_mqtt_publish_failures")
                            logging.warning(f"Failed to publish NMEA to MQTT: rc={result.rc}")
                    except Exception as e:
                        _telemetry_inc("queue", "nmea_mqtt_publish_failures")
                        logging.warning(f"Failed to publish NMEA to MQTT: {e}")

                # Raw telemetry uses one transport at a time. MQTT is the
                # primary path; WebSocket is a fallback when MQTT cannot
                # accept the packet, avoiding duplicate hot-path work in the
                # backend while preserving delivery during broker outages.
                if (
                    not mqtt_delivered
                    and active_websocket_connection
                    and self.loop
                    and not self.loop.is_closed()
                    and should_forward
                ):
                    try:
                        ws_message = {
                            "type": "nmea_update",
                            "payload": data_chunk.decode('ascii', errors='ignore')
                        }
                        asyncio.run_coroutine_threadsafe(
                            active_websocket_connection.send(json.dumps(ws_message)),
                            self.loop
                        )
                        _telemetry_inc("queue", "nmea_ws_forwarded")
                    except Exception as e:
                        logging.debug(f"Failed to send NMEA over websocket: {e}")

            except Empty:
                continue
            except Exception as e:
                logging.exception(f"Error in NMEAPublisher: {e}", exc_info=True)


class RTCMPublisherMQTT(threading.Thread):
    def __init__(self, mqtt_client, serial_number, loop, agent_manager=None):
        super().__init__()
        self.daemon = True
        self.name = "RTCMPublisherMQTT"
        self._stop_event = threading.Event()
        self.mqtt_client = mqtt_client
        self.serial_number = serial_number
        self.loop = loop
        self.agent_manager = agent_manager
        from queue import Queue
        self.queue = Queue(maxsize=1000)
        self.decoder = RTCMSignalDecoder() if HAS_RTCM_DECODER else None
        
        global rtcm_mqtt_queue
        rtcm_mqtt_queue = self.queue

    def stop(self):
        self._stop_event.set()

    def _should_publish_rtcm_skyview(self):
        last_gsv_ts = globals().get('LAST_GSV_TS', 0)
        return (
            not _base_config_expects_nmea_skyview(self.agent_manager)
            and time.time() - last_gsv_ts >= 60
        )

    def _rtcm_reference_coordinates(self, agent_manager):
        base_config = agent_manager.get_base_config() or {}
        base_coords = base_config.get("coords") or {}
        if not base_coords and "lat" in base_config:
            base_coords = {
                "lat": base_config.get("lat"),
                "lon": base_config.get("lon"),
                "alt": base_config.get("alt", 0.0),
            }
        if base_coords and base_coords.get("lat") is not None and base_coords.get("lon") is not None:
            reference = (
                float(base_coords["lat"]),
                float(base_coords["lon"]),
                float(base_coords.get("alt") or 0.0),
            )
            return reference
        return globals().get('LAST_HPPOSLLH_COORD') or globals().get('LAST_GGA_COORD')

    def _ensure_rtcm_base_position(self, publish_rtcm_skyview):
        if not publish_rtcm_skyview or self.serial_number in self.decoder._base_positions:
            return
        agent_manager = self.agent_manager or globals().get('agent')
        if not agent_manager or not hasattr(agent_manager, 'get_base_config'):
            return
        reference = self._rtcm_reference_coordinates(agent_manager)
        if not reference:
            return
        try:
            x, y, z = self.decoder._llh2ecef(*reference)
            self.decoder._base_positions[self.serial_number] = (x, y, z)
        except Exception:
            pass

    def _rtcm_skyview_payload(self, parsed):
        nav_system = "GPS"
        for packet in parsed.get("packets", []):
            if "navSystem" in packet:
                nav_system = packet["navSystem"]
                break
        system_names = {"us": "GPS", "ru": "GLONASS", "eu": "GALILEO", "cn": "BEIDOU", "jp": "QZSS"}
        satellites = []
        for satellite in parsed.get("satellites", []):
            system = satellite.get("system", nav_system)
            if isinstance(system, str):
                system = system_names.get(system, system)
            satellites.append({
                "prn": satellite["prn"],
                "snr": satellite.get("cnr", satellite.get("snr", 0)),
                "sys": system,
                "system": system,
                "talker": satellite.get("talker", "RTCM"),
                "azimuth": satellite.get("azimuth", 0.0),
                "elevation": satellite.get("elevation", 0.0),
                "isTracking": satellite.get("isTracking", True),
                "last_seen": time.time(),
            })
        return nav_system, satellites

    def _publish_rtcm_skyview(self, topic, nav_system, satellites):
        if not satellites:
            return
        globals()['LAST_RTCM_SATELLITES'] = satellites
        globals()['LAST_RTCM_SATELLITES_TS'] = time.time()
        decoded_payload = {
            "type": "rtcm_skyview_calculated",
            "navSystem": nav_system,
            "satellites": satellites,
            "timestamp": round(time.time(), 3),
        }
        payload = json.dumps(decoded_payload).encode('ascii')
        mqtt_delivered = False
        if self.mqtt_client and self.mqtt_client.is_connected():
            try:
                result = self.mqtt_client.publish(topic, payload, qos=0)
                mqtt_delivered = result.rc == mqtt.MQTT_ERR_SUCCESS
            except Exception as error:
                logging.exception(f"Failed to publish RTCM skyview: {error}")
        if not mqtt_delivered and active_websocket_connection and self.loop and not self.loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    active_websocket_connection.send(json.dumps({
                        "type": "rtcm_signal_update",
                        "payload": decoded_payload,
                    })),
                    self.loop,
                )
            except Exception as error:
                logging.debug(f"Failed to send RTCM skyview over websocket: {error}")

    def _decode_rtcm_chunk(self, data_chunk, topic):
        if not HAS_RTCM_DECODER or not self.decoder:
            return
        try:
            publish_skyview = self._should_publish_rtcm_skyview()
            self._ensure_rtcm_base_position(publish_skyview)
            parsed = self.decoder.decode_sync(self.serial_number, data_chunk)
            while parsed is not None:
                if publish_skyview and parsed.get("satellites"):
                    nav_system, satellites = self._rtcm_skyview_payload(parsed)
                    self._publish_rtcm_skyview(topic, nav_system, satellites)
                parsed = self.decoder.decode_sync(self.serial_number, b"")
        except Exception as error:
            logging.debug(f"RTCMSignalDecoder error: {error}")

    def run(self):
        import time, json, logging
        from queue import Empty

        logging.info("RTCM MQTT Publisher thread started.")
        topic = f"pi/devices/{self.serial_number}/raw_data"
        while not self._stop_event.is_set():
            try:
                data_chunk = self.queue.get(timeout=0.1)
                # The station streams raw RTCM directly to NTRIP; this worker only decodes it.
                self._decode_rtcm_chunk(data_chunk, topic)
            except Empty:
                pass
            except Exception as error:
                logging.exception(f"Error in RTCMPublisherMQTT: {error}", exc_info=True)

class NTRIPServerWorker(threading.Thread):
    def __init__(self, server_id, config, log_callback, stats_dict, stats_lock, connection_status_dict):
        super().__init__()
        self.server_id = server_id
        self.config = config
        self.log = log_callback
        self.stats = stats_dict
        self.stats_lock = stats_lock
        self.connection_status = connection_status_dict
        self.daemon = True
        self._stop_event = threading.Event()
        self.name = f"NTRIPServerWorker-{server_id}"
        self.queue = Queue(maxsize=3000)
        
        self.bytes_sent = 0
        self.last_stat_update = time.time()
        
        self.dropped_packets = 0
        self.last_drop_log = time.time()

        with subscriber_lock:
            rtcm_subscribers.append(self.queue)
    
    def stop(self):
        self._stop_event.set()
        with subscriber_lock:
            if self.queue in rtcm_subscribers:
                rtcm_subscribers.remove(self.queue)
    
    def _can_push_rtcm(self):
        switches = _get_server_stream_switches(self.config, self.server_id, apply_fail_open=True)
        selected = str(self.config.get('active_mountpoint') or '').strip().lower()
        own_mountpoint = str(self.config.get(f'mountpoint{self.server_id}') or '').strip().lower()
        return switches['can_push'] and (
            not switches['on_demand'] or control_plane_fail_open_event.is_set()
            or not selected or selected == own_mountpoint
        )

    def _connection_settings(self):
        host = self.config.get(f'serverhost{self.server_id}')
        port_value = self.config.get(f'port{self.server_id}')
        mountpoint = self.config.get(f'mountpoint{self.server_id}')
        password = self.config.get(f'password{self.server_id}')
        username = self.config.get(f'username{self.server_id}')
        if not username or username.strip() == "":
            username = "source"
        version = int(self.config.get(f'ntrip_version{self.server_id}', 1))
        if not all([host, port_value, mountpoint]):
            return None
        try:
            port = int(port_value)
        except (ValueError, TypeError):
            return None
        reconnect_interval = int(self.config.get('reconnectioninterval', 15))
        return host, port, mountpoint, password, username, version, reconnect_interval

    def _clear_rtcm_queue(self):
        try:
            while True:
                self.queue.get_nowait()
        except Empty:
            pass

    def _wait_for_server_enabled(self):
        if is_remote_locked(self.server_id):
            with self.stats_lock:
                self.connection_status[f'server{self.server_id}'] = False
            time.sleep(2)
            return False
        switches = _get_server_stream_switches(
            self.config, self.server_id, apply_fail_open=True
        )
        if switches['enabled']:
            return True
        with self.stats_lock:
            self.connection_status[f'server{self.server_id}'] = False
        self._clear_rtcm_queue()
        time.sleep(1)
        return False

    def _build_source_headers(self, host, port, mountpoint, password, username, version, auth):
        if version == 2:
            self.log("INFO", f"S{self.server_id}: Using NTRIP v2.0 (HTTP POST) with User: {username}")
            return (
                f"POST /{mountpoint} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Ntrip-Version: Ntrip/2.0\r\n"
                f"User-Agent: NTRIP GeodeticAgent/4.1\r\n"
                f"Authorization: Basic {auth}\r\n"
                f"Transfer-Encoding: chunked\r\n"
                f"Connection: Keep-Alive\r\n\r\n"
            )
        self.log("INFO", f"S{self.server_id}: Using NTRIP v1.0 (SOURCE)")
        return (
            f"SOURCE {password or ''} /{mountpoint} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"User-Agent: NTRIP GeodeticAgent/4.1\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"Connection: Keep-Alive\r\n\r\n"
        )

    def _authenticate_caster(self, client_socket, host, port, mountpoint, password, username, version):
        client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        auth_string = f"{username}:{password or ''}"
        auth = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
        headers = self._build_source_headers(host, port, mountpoint, password, username, version, auth)
        client_socket.sendall(headers.encode('ascii'))
        response = client_socket.recv(4096)
        if not (b"ICY 200 OK" in response or b"HTTP/1.1 200 OK" in response):
            raise ConnectionError(f"Caster rejected: {response.decode(errors='ignore')}")
        with self.stats_lock:
            self.connection_status[f'server{self.server_id}'] = True
        self.log("SUCCESS", f"S{self.server_id}: Authenticated. Caster connection ready.")
        self.last_stat_update = time.time()
        self.bytes_sent = 0

    def _enter_stream_standby(self, stream_state):
        if not stream_state["standby_logged"]:
            self.log("INFO", f"S{self.server_id}: RTCM paused; keeping authenticated caster connection.")
            stream_state["standby_logged"] = True
        with self.stats_lock:
            self.stats[f'server{self.server_id}_bps'] = 0
        self.bytes_sent = 0
        self.last_stat_update = time.time()
        self._clear_rtcm_queue()
        self._stop_event.wait(0.25)

    def _forward_next_rtcm_packet(self, client_socket, version, stream_state):
        data_chunk = self.queue.get(timeout=1.0)
        if not self._can_push_rtcm():
            return False
        if not is_valid_rtcm3_packet(data_chunk):
            stream_state["non_rtcm_dropped"] += 1
            now = time.time()
            if now - stream_state["non_rtcm_last_log_ts"] >= 5.0:
                self.log(
                    "WARNING",
                    f"S{self.server_id}: Dropped non-RTCM payloads={stream_state['non_rtcm_dropped']}"
                )
                stream_state["non_rtcm_last_log_ts"] = now
            return False
        if version == 2:
            chunk_size = hex(len(data_chunk))[2:].encode('ascii')
            client_socket.sendall(chunk_size + b'\r\n' + data_chunk + b'\r\n')
        else:
            client_socket.sendall(data_chunk)
        self.bytes_sent += len(data_chunk)
        return True

    def _update_sent_rate(self):
        now = time.time()
        if now - self.last_stat_update < 1.0:
            return
        elapsed = now - self.last_stat_update
        with self.stats_lock:
            self.stats[f'server{self.server_id}_bps'] = int(self.bytes_sent / elapsed)
        self.bytes_sent = 0
        self.last_stat_update = now

    def _stream_rtcm(self, client_socket, version):
        stream_state = {
            "standby_logged": False,
            "non_rtcm_dropped": 0,
            "non_rtcm_last_log_ts": 0.0,
        }
        while not self._stop_event.is_set() and not is_remote_locked(self.server_id):
            try:
                switches = _get_server_stream_switches(
                    self.config, self.server_id, apply_fail_open=True
                )
                if not switches['enabled']:
                    break
                readable, _, _ = select.select([client_socket], [], [], 0)
                if readable and not client_socket.recv(4096):
                    raise ConnectionError("Caster closed the connection")
                if not self._can_push_rtcm():
                    self._enter_stream_standby(stream_state)
                    continue
                stream_state["standby_logged"] = False
                if not self._forward_next_rtcm_packet(client_socket, version, stream_state):
                    continue
            except Empty:
                pass
            self._update_sent_rate()

    def _log_connection_error(self, error, reconnect_interval):
        _telemetry_inc("reconnect", f"ntrip_server{self.server_id}")
        self.log("WARNING", f"S{self.server_id}: Connection error: {error}.")
        with self.stats_lock:
            self.stats[f'server{self.server_id}_bps'] = 0
            self.connection_status[f'server{self.server_id}'] = False
        self._stop_event.wait(reconnect_interval)

    def run(self):
        connection_settings = self._connection_settings()
        if not connection_settings:
            return
        host, port, mountpoint, password, username, version, reconnect_interval = connection_settings

        while not self._stop_event.is_set():
            if not self._wait_for_server_enabled():
                continue
            client_socket = None
            try:
                self.log("INFO", f"S{self.server_id}: Connecting to ntrip://{host}:{port}/{mountpoint} (v{version}.0)...")
                client_socket = socket.create_connection((host, port), timeout=10)
                self._authenticate_caster(
                    client_socket, host, port, mountpoint, password, username, version
                )
                self._stream_rtcm(client_socket, version)
            except Exception as error:
                self._log_connection_error(error, reconnect_interval)
            finally:
                with self.stats_lock:
                    self.stats[f'server{self.server_id}_bps'] = 0
                    self.connection_status[f'server{self.server_id}'] = False
                if client_socket:
                    client_socket.close()

class NTRIPClientWorker(threading.Thread):
    def __init__(self, config, log_callback, correction_sink=None):
        super().__init__()
        self.config = config; self.log = log_callback
        self.correction_sink = correction_sink or dispatch_rtcm_data
        self.daemon = True; self._stop_event = threading.Event()
        self.name = "NTRIPClientWorker"
    def stop(self): self._stop_event.set()
    def _client_connection_settings(self):
        host = self.config.get('rtcmserver1')
        port_value = self.config.get('rtcmport1')
        mountpoint = self.config.get('rtcmmountpoint1')
        username = self.config.get('rtcmusername1')
        password = self.config.get('rtcmpassword1')
        if not all([host, port_value, mountpoint, username]):
            return None
        try:
            port = int(port_value)
        except (ValueError, TypeError):
            return None
        reconnect_interval = int(self.config.get('reconnectioninterval', 15))
        return host, port, mountpoint, username, password, reconnect_interval

    def _read_client_response_header(self, client_socket):
        response_header = bytearray()
        header_deadline = time.monotonic() + 15.0
        while b"\r\n\r\n" not in response_header:
            if len(response_header) >= 16 * 1024:
                raise ConnectionError("Caster response headers exceed the 16 KiB limit")
            remaining = header_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Caster response headers timed out")
            client_socket.settimeout(min(remaining, 2.0))
            byte = client_socket.recv(1)
            if not byte:
                raise ConnectionError("Caster closed connection before response headers completed")
            response_header.extend(byte)
            if response_header == b"ICY 200 OK\r\n":
                break
        return bytes(response_header)

    def _authenticate_client_socket(self, client_socket, mountpoint, username, password):
        auth_str = f"{username}:{password or ''}"
        auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii').strip()
        headers = (
            f"GET /{mountpoint} HTTP/1.0\r\n"
            f"User-Agent: NTRIP AitogyNTRIPClient/20131124\r\n"
            f"Authorization: Basic {auth_b64}\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n\r\n"
        )
        client_socket.sendall(headers.encode('ascii'))
        response_header = self._read_client_response_header(client_socket)
        if not response_header.startswith((b"ICY 200 OK\r\n", b"HTTP/1.1 200 ", b"HTTP/1.0 200 ")):
            status_line = response_header.split(b"\r\n", 1)[0].decode(errors="ignore")[:200]
            raise ConnectionError(f"Caster rejected request: {status_line}")
        self.log("INFO", "[RTCM Client] Authenticated. Receiving correction data.")

    def _send_initial_client_gga(self, client_socket):
        self.last_sent_gga = None
        self.last_sent_gga_time = 0.0
        self.total_rtcm_bytes = 0
        self.total_rtcm_pkts = 0
        out_gga = _get_fresh_outbound_gga(10.0)
        if out_gga:
            try:
                gga_bytes = (out_gga + '\r\n').encode('ascii')
                client_socket.sendall(gga_bytes)
                self.last_sent_gga = out_gga
                self.last_sent_gga_time = time.time()
                self.log("INFO", f"[RTCM Client] >>> SENT INITIAL GGA: {out_gga}")
            except Exception as error:
                self.log("WARNING", f"[RTCM Client] Failed to send initial GGA: {error}")
            return
        raw_gga = globals().get('LAST_RAW_GGA')
        if raw_gga:
            age = time.time() - float(globals().get('LAST_RAW_GGA_TS') or 0.0)
            self.log("WARNING", f"[RTCM Client] Last GGA is {age:.1f}s old: {raw_gga}")
        else:
            self.log("WARNING", "[RTCM Client] Waiting for rover chip to output first GGA sentence...")

    def _send_periodic_client_gga(self, client_socket, now, last_send_time):
        if now - last_send_time < 5:
            return last_send_time, True
        out_gga = _get_fresh_outbound_gga(10.0)
        if out_gga:
            try:
                gga_bytes = (out_gga + '\r\n').encode('ascii')
                client_socket.sendall(gga_bytes)
                self.last_sent_gga = out_gga
                self.last_sent_gga_time = now
                self.log("INFO", f"[RTCM Client] >>> GGA sent: {out_gga}")
            except Exception as error:
                self.log("WARNING", f"[RTCM Client] Failed to send GGA: {error}")
                return last_send_time, False
        else:
            raw_gga = globals().get('LAST_RAW_GGA')
            if raw_gga:
                age = now - float(globals().get('LAST_RAW_GGA_TS') or 0.0)
                self.log("WARNING", f"[RTCM Client] GGA is stale ({age:.1f}s old): {raw_gga}")
            else:
                self.log("WARNING", "[RTCM Client] No GGA received from rover chip yet")
        return now, True

    def _receive_client_rtcm(self, client_socket):
        try:
            return client_socket.recv(4096)
        except socket.timeout:
            self.log(
                "INFO",
                f"[RTCM Client] recv timeout (5s), total RTCM: {self.total_rtcm_bytes}B / "
                f"{self.total_rtcm_pkts} pkts | Last GGA: {getattr(self, 'last_sent_gga', 'None')}",
            )
            return None

    def _process_client_rtcm_chunk(self, data, rtcm_stream_buffer, total_rtcm_bytes, total_rtcm_pkts):
        total_rtcm_bytes += len(data)
        rtcm_stream_buffer.extend(data)
        if len(rtcm_stream_buffer) > 16384:
            self.log("WARNING", "[RTCM Client] Stream buffer overflow; dropping buffered data")
            rtcm_stream_buffer.clear()
            return total_rtcm_bytes, total_rtcm_pkts
        packets = extract_valid_rtcm3_packets(rtcm_stream_buffer)
        for packet in packets:
            self.correction_sink(packet)
            total_rtcm_pkts += 1
        self.total_rtcm_bytes = total_rtcm_bytes
        self.total_rtcm_pkts = total_rtcm_pkts
        if packets:
            now_log = time.time()
            if not hasattr(self, '_last_rtcm_log_time'):
                self._last_rtcm_log_time = 0
            if now_log - self._last_rtcm_log_time >= 30.0 or total_rtcm_pkts <= 5:
                self.log("INFO", f"[RTCM Client] Total received so far: {total_rtcm_bytes}B / {total_rtcm_pkts} pkts")
                self._last_rtcm_log_time = now_log
        return total_rtcm_bytes, total_rtcm_pkts

    def _read_client_rtcm_stream(self, client_socket):
        total_rtcm_bytes = 0
        total_rtcm_pkts = 0
        last_gga_send_time = time.time()
        rtcm_stream_buffer = bytearray()
        client_socket.settimeout(5.0)
        while not self._stop_event.is_set() and not is_remote_locked():
            now = time.time()
            last_gga_send_time, keep_reading = self._send_periodic_client_gga(
                client_socket, now, last_gga_send_time
            )
            if not keep_reading:
                break
            rtcm_data = self._receive_client_rtcm(client_socket)
            if rtcm_data is None:
                continue
            if not rtcm_data:
                self.log("WARNING", f"[RTCM Client] Server closed connection (recv returned empty). Total received: {total_rtcm_bytes}B")
                break
            if self._stop_event.is_set():
                break
            total_rtcm_bytes, total_rtcm_pkts = self._process_client_rtcm_chunk(
                rtcm_data, rtcm_stream_buffer, total_rtcm_bytes, total_rtcm_pkts
            )

    def run(self):
        connection_settings = self._client_connection_settings()
        if connection_settings is None:
            return
        host, port, mountpoint, username, password, reconnect_interval = connection_settings
        while not self._stop_event.is_set():
            if is_remote_locked():
                time.sleep(2)
                continue
            client_socket = None
            try:
                self.log("INFO", f"[RTCM Client] Connecting to ntrip://{host}:{port}/{mountpoint}...")
                client_socket = socket.create_connection((host, port), timeout=10)
                self._authenticate_client_socket(
                    client_socket, mountpoint, username, password
                )
                if self._stop_event.is_set():
                    break
                self._send_initial_client_gga(client_socket)
                self._read_client_rtcm_stream(client_socket)
            except Exception as error:
                _telemetry_inc("reconnect", "ntrip_client")
                self.log("WARNING", f"[RTCM Client] Connection error: {error}.")
            finally:
                if client_socket:
                    client_socket.close()
            self._stop_event.wait(reconnect_interval)

class GNSSReader(threading.Thread):
    def __init__(self, log_callback, port, baudrate):
        super().__init__()
        self.log = log_callback
        self.port = port
        self.baudrate = baudrate
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._base_queries = Queue(maxsize=1)
        self._base_query = None
        self.daemon = True
        self.name = "GNSSReaderThread"
        
        # RTCM injection queue (for writing CORS corrections to serial)
        self.rtcm_inject_queue = Queue(maxsize=200)
        
        # Statistics
        self.rtcm_packets_sent = 0
        self.nmea_packets_sent = 0
        self.bytes_read = 0
        self.last_data_time = time.time()
        self.buffer_overflows = 0
        self.parse_errors = 0

        # Parser diagnostics (helps verify RTCM/NMEA are not dropped incorrectly)
        self.parser_debug_enabled = PARSER_DEBUG_ENABLED
        self.parser_debug_interval = max(1.0, PARSER_DEBUG_INTERVAL_SECONDS)
        self.rtcm_preamble_seen = 0
        self.rtcm_valid_packets = 0
        self.rtcm_invalid_crc = 0
        self.rtcm_invalid_length = 0
        self.rtcm_incomplete_waits = 0
        self.nmea_sentence_seen = 0
        self.nmea_valid_packets = 0
        self.nmea_invalid_checksum = 0
        self.nmea_unimportant_skipped = 0
        self.nmea_incomplete_waits = 0
        self.unknown_bytes_dropped = 0
        self._parser_debug_prev = {}
        self._parser_debug_last_log_ts = time.time()
        
        self.log("INFO", f"GNSSReader initialized for {port} @ {baudrate} baud")
        self.log(
            "INFO",
            f"[PARSER_DEBUG] enabled={self.parser_debug_enabled} interval={self.parser_debug_interval:.1f}s"
        )
    
    def stop(self):
        self._stop_event.set()
        self.log("INFO", "GNSSReader stop requested")
    
    def pause(self):
        self._pause_event.set()
        self.log("INFO", "GNSSReader paused")
    
    def resume(self):
        self._pause_event.clear()
        self.log("INFO", "GNSSReader resumed")
    
    async def query_base(self, wire, key):
        """Ask the owning UART thread; never open a second serial reader."""
        if not self.is_alive() or self._pause_event.is_set():
            raise RuntimeError("GNSS reader is not available")
        request = {'wire':wire, 'key':key, 'deadline':time.monotonic() + 4.0,
                       'reply':None, 'cancelled':False}
        try:
            self._base_queries.put_nowait(request)
        except Full:
            raise RuntimeError("GNSS readback queue is busy")
        try:
            while request['reply'] is None:
                if time.monotonic() >= request['deadline'] or self._stop_event.is_set():
                    if key == UNICORE_LOG_LIST_KEY:
                        logging.warning("[GNSS][VERIFY][UNILOGLIST] timeout; partial=%r",
                                        request.get('response_preview', b''))
                    raise TimeoutError(f"Receiver readback timeout: {key}")
                await asyncio.sleep(0.02)
            if request.get('error'):
                raise RuntimeError(request['error'])
            return request['reply']
        finally:
            request['cancelled'] = True

    def _service_base_query(self, ser):
        request = self._base_query
        if request and (request['cancelled'] or time.monotonic() >= request['deadline']):
            self._base_query = None
        if self._base_query is not None:
            return
        try:
            request = self._base_queries.get_nowait()
        except Empty:
            return
        if request['cancelled'] or time.monotonic() >= request['deadline']:
            return
        self._base_query = request
        try:
            if ser.write(request['wire']) != len(request['wire']):
                raise IOError("Incomplete GNSS readback query write")
            ser.flush()
        except Exception as error:
            request['error'] = str(error)
            request['reply'] = b''
            self._base_query = None

    def _deliver_base_reply(self, key, packet):
        request = getattr(self, '_base_query', None)
        if (request and request['key'] == key and not request['cancelled']
                and time.monotonic() < request['deadline']):
            request['reply'] = packet
            self._base_query = None

    def inject_rtcm(self, data: bytes):
        """Queue incoming corrections independently of outbound caster sleep gates."""
        _update_datum_from_rtcm_packet(data)
        try:
            self.rtcm_inject_queue.put_nowait(data)
        except Full:
            _telemetry_inc("queue", "rtcm_inject_drops")
            try:
                self.rtcm_inject_queue.get_nowait()
                self.rtcm_inject_queue.put_nowait(data)
            except (Empty, Full):
                pass
    
    def get_statistics(self):
        return {
            "rtcm_packets": self.rtcm_packets_sent,
            "nmea_packets": self.nmea_packets_sent,
            "bytes_read": self.bytes_read,
            "last_data_time": self.last_data_time,
            "seconds_since_data": int(time.time() - self.last_data_time),
            "buffer_overflows": self.buffer_overflows,
            "parse_errors": self.parse_errors,
            "parser_debug": self._build_parser_debug_snapshot()
        }

    def _build_parser_debug_snapshot(self):
        return {
            "enabled": self.parser_debug_enabled,
            "parse_errors": self.parse_errors,
            "rtcm_preamble_seen": self.rtcm_preamble_seen,
            "rtcm_valid_packets": self.rtcm_valid_packets,
            "rtcm_invalid_crc": self.rtcm_invalid_crc,
            "rtcm_invalid_length": self.rtcm_invalid_length,
            "rtcm_incomplete_waits": self.rtcm_incomplete_waits,
            "nmea_sentence_seen": self.nmea_sentence_seen,
            "nmea_valid_packets": self.nmea_valid_packets,
            "nmea_invalid_checksum": self.nmea_invalid_checksum,
            "nmea_unimportant_skipped": self.nmea_unimportant_skipped,
            "nmea_incomplete_waits": self.nmea_incomplete_waits,
            "unknown_bytes_dropped": self.unknown_bytes_dropped
        }

    def _log_parser_debug_if_due(self, now: float, buffer_len: int):
        if not self.parser_debug_enabled:
            return
        elapsed = now - self._parser_debug_last_log_ts
        if elapsed < self.parser_debug_interval:
            return

        curr = self._build_parser_debug_snapshot()
        prev = self._parser_debug_prev
        self._parser_debug_prev = curr
        self._parser_debug_last_log_ts = now

        def delta(name: str) -> int:
            return int(curr.get(name, 0)) - int(prev.get(name, 0))

        rtcm_valid = delta("rtcm_valid_packets")
        rtcm_bad_crc = delta("rtcm_invalid_crc")
        rtcm_bad_len = delta("rtcm_invalid_length")
        nmea_valid = delta("nmea_valid_packets")
        nmea_bad_ck = delta("nmea_invalid_checksum")
        unknown_drop = delta("unknown_bytes_dropped")
        parse_err = self.parse_errors - int(prev.get("parse_errors", 0))

        self.log(
            "INFO",
            (
                "[PARSER_DEBUG] "
                f"dt={elapsed:.1f}s "
                f"rtcm_ok={rtcm_valid} rtcm_crc_fail={rtcm_bad_crc} rtcm_len_fail={rtcm_bad_len} "
                f"nmea_ok={nmea_valid} nmea_ck_fail={nmea_bad_ck} "
                f"unknown_drop={unknown_drop} parse_err={parse_err} "
                f"buf={buffer_len}"
            )
        )
    
    def _parse_rtcm3_buffer(self, buffer):
        self.rtcm_preamble_seen += 1
        if len(buffer) < 3:
            self.rtcm_incomplete_waits += 1
            return None
        payload_length = ((buffer[1] & 0x03) << 8) | buffer[2]
        packet_length = payload_length + 6
        if packet_length > 2048:
            self.log("WARNING", f"Invalid RTCM3 length: {packet_length}, discarding byte")
            buffer.pop(0)
            self.parse_errors += 1
            self.rtcm_invalid_length += 1
            return buffer
        if len(buffer) < packet_length:
            self.rtcm_incomplete_waits += 1
            return None
        packet = bytes(buffer[:packet_length])
        if not is_valid_rtcm3_packet(packet):
            # Drop one byte so a false preamble cannot hide a valid packet behind it.
            self.parse_errors += 1
            self.rtcm_invalid_crc += 1
            buffer.pop(0)
            return buffer
        dispatch_rtcm_data(packet)
        self.rtcm_packets_sent += 1
        self.rtcm_valid_packets += 1
        self.bytes_read += len(packet)
        self.last_data_time = time.time()
        return buffer[packet_length:]

    def _handle_ubx_security_packet(self, packet, ubx_id):
        try:
            key = 'LAST_UBX_SECURITY' if ubx_id == 9 else 'LAST_UBX_SECURITY_LOG'
            globals()[key] = _ubx_security_payload(packet[6:-2], ubx_id)
            globals()[key + '_TS'] = time.time()
        except ValueError:
            self.parse_errors += 1

    def _handle_ubx_high_precision_position(self, packet, payload_length):
        if payload_length != 36 or packet[9] & 1:
            return
        try:
            payload = packet[6:6 + payload_length]
            lon = struct.unpack_from('<i', payload, 8)[0] * 1e-7
            lat = struct.unpack_from('<i', payload, 12)[0] * 1e-7
            h_mm = struct.unpack_from('<i', payload, 16)[0]
            lon_hp = struct.unpack_from('<b', payload, 24)[0] * 1e-9
            lat_hp = struct.unpack_from('<b', payload, 25)[0] * 1e-9
            h_hp_m = struct.unpack_from('<b', payload, 26)[0] * 1e-4
            lat_hpp = float(lat + lat_hp)
            lon_hpp = float(lon + lon_hp)
            h_ell = float(h_mm * 1e-3 + h_hp_m)
            if -90.0 <= lat_hpp <= 90.0 and -180.0 <= lon_hpp <= 180.0:
                globals()['LAST_HPPOSLLH_COORD'] = (lat_hpp, lon_hpp, h_ell)
                globals()['LAST_HPPOSLLH_TS'] = time.time()
        except Exception:
            pass

    def _handle_ubx_rf_packet(self, packet, ubx_id):
        try:
            key = 'LAST_UBX_RF' if ubx_id == 0x38 else 'LAST_UBX_SPECTRUM'
            globals()[key] = _ubx_rf_payload(packet[6:-2], ubx_id)
            globals()[key + '_TS'] = time.time()
        except ValueError:
            self.parse_errors += 1

    def _handle_ubx_satellite_packet(self, packet):
        try:
            globals()['LAST_UBX_SATELLITES'] = _ubx_satellite_payload(packet[6:-2])
            globals()['LAST_UBX_SATELLITES_TS'] = time.time()
        except ValueError:
            self.parse_errors += 1

    def _handle_ubx_navigation_quality(self, packet, payload_length):
        if payload_length != 92:
            return
        try:
            payload = packet[6:6 + payload_length]
            globals()['LAST_RECEIVER_QUALITY'] = _ubx_navigation_quality(payload)
            globals()['LAST_RECEIVER_QUALITY_TS'] = time.time()
            num_sv = int(payload[23])
            if 0 <= num_sv <= 99:
                globals()['LAST_UBX_NUMSV'] = num_sv
                globals()['LAST_UBX_NUMSV_TS'] = time.time()
        except Exception:
            pass

    def _process_ubx_packet(self, packet, payload_length):
        ubx_class, ubx_id = packet[2], packet[3]
        self._deliver_base_reply((ubx_class, ubx_id), packet)
        if ubx_class == 0x27 and ubx_id in (9, 0x10):
            self._handle_ubx_security_packet(packet, ubx_id)
        if ubx_class == 0x01 and ubx_id == 0x14:
            self._handle_ubx_high_precision_position(packet, payload_length)
        if ubx_class == 0x0a and ubx_id in (0x38, 0x31):
            self._handle_ubx_rf_packet(packet, ubx_id)
        if ubx_class == 1 and ubx_id == 0x35:
            self._handle_ubx_satellite_packet(packet)
        if ubx_class == 1 and ubx_id == 4 and payload_length == 18:
            globals()['LAST_UBX_DOP'] = _ubx_dop(packet[6:-2])
            globals()['LAST_UBX_DOP_TS'] = time.time()
        if ubx_class == 0x01 and ubx_id == 0x07:
            self._handle_ubx_navigation_quality(packet, payload_length)
        if ubx_class == 0x05:
            self.log("DEBUG", f"UBX ACK/NAK: id=0x{ubx_id:02X}")

    def _parse_ubx_buffer(self, buffer):
        if len(buffer) < 8:
            return None
        if len(buffer) < 6:
            return None
        payload_length = buffer[4] | (buffer[5] << 8)
        packet_length = 6 + payload_length + 2
        if packet_length > 4096:
            self.log("WARNING", f"Invalid UBX length: {packet_length}, discarding byte")
            buffer.pop(0)
            self.parse_errors += 1
            return buffer
        if len(buffer) < packet_length:
            return None
        packet = bytes(buffer[:packet_length])
        checksum_a = checksum_b = 0
        for value in packet[2:-2]:
            checksum_a = (checksum_a + value) & 255
            checksum_b = (checksum_b + checksum_a) & 255
        if packet[-2:] != bytes((checksum_a, checksum_b)):
            self.parse_errors += 1
            return buffer[packet_length:]
        self._process_ubx_packet(packet, payload_length)
        self.bytes_read += len(packet)
        self.last_data_time = time.time()
        return buffer[packet_length:]

    def _parse_unicore_log_list(self, buffer):
        request = getattr(self, '_base_query', None)
        if request and request['key'] == UNICORE_LOG_LIST_KEY and not request['cancelled']:
            request['response_preview'] = bytes(buffer[:512])
        trailer = re.search(rb'\*[0-9a-fA-F]{8}\r?\n|\r?\n\r?\n', buffer)
        if trailer is None:
            if len(buffer) <= 4096:
                return None
            buffer.pop(0)
            self.parse_errors += 1
            return buffer
        packet = bytes(buffer[:trailer.end()])
        self._deliver_base_reply(UNICORE_LOG_LIST_KEY, packet)
        return buffer[trailer.end():]

    def _parse_unicore_line(self, buffer):
        line_end = buffer.find(b'\n')
        if line_end < 0:
            if len(buffer) <= 2048:
                return None
            buffer.pop(0)
            self.parse_errors += 1
            return buffer
        packet = bytes(buffer[:line_end + 1]).strip()
        key = packet.split(b',', 1)[0]
        self._deliver_base_reply(key, packet)
        return buffer[line_end + 1:]

    def _parse_unicore_buffer(self, buffer):
        if buffer.startswith(UNICORE_LOG_LIST_KEY + b','):
            return self._parse_unicore_log_list(buffer)
        return self._parse_unicore_line(buffer)

    @staticmethod
    def _is_important_nmea(sentence):
        important_types = ('GGA', 'GSA', 'GSV', 'GST')
        sentence_text = sentence.decode('ascii', errors='ignore')
        return any(nmea_type in sentence_text[:10] for nmea_type in important_types)

    def _parse_incomplete_nmea(self, buffer):
        if len(buffer) >= 6:
            try:
                sentence_start = buffer[:6].decode('ascii', errors='ignore')
                valid_prefixes = ('$GP', '$GN', '$GL', '$GA', '$GB', '$GQ', '$PU', '$BD', '$P')
                is_valid_nmea = any(sentence_start.startswith(prefix) for prefix in valid_prefixes)
                if is_valid_nmea:
                    if len(buffer) > 512:
                        next_dollar = buffer.find(b'$', 1)
                        buffer = buffer[next_dollar:] if next_dollar > 0 else buffer[1:]
                        self.parse_errors += 1
                        return buffer
                    self.nmea_incomplete_waits += 1
                    return None
                buffer.pop(0)
                return buffer
            except Exception:
                buffer.pop(0)
                self.parse_errors += 1
                return buffer
        if len(buffer) > 512:
            buffer.pop(0)
            return buffer
        self.nmea_incomplete_waits += 1
        return None

    def _parse_nmea_sentence(self, buffer, sentence_end):
        sentence = buffer[:sentence_end]
        self.nmea_sentence_seen += 1
        try:
            if not self._is_important_nmea(sentence):
                self.nmea_unimportant_skipped += 1
                return buffer[sentence_end:]
            if not (10 <= len(sentence) <= 200 and is_valid_nmea_checksum(sentence)):
                buffer.pop(0)
                self.parse_errors += 1
                self.nmea_invalid_checksum += 1
                return buffer
            packet = bytes(sentence)
            dispatch_nmea_data(packet)
            self.nmea_packets_sent += 1
            self.nmea_valid_packets += 1
            self.bytes_read += len(packet)
            self.last_data_time = time.time()
            return buffer[sentence_end:]
        except Exception as error:
            self.log("DEBUG", f"NMEA filter error: {error}")
            buffer.pop(0)
            self.parse_errors += 1
            return buffer

    def _parse_nmea_buffer(self, buffer):
        line_end = buffer.find(b'\n')
        if line_end >= 0:
            sentence_end = line_end + 1
        else:
            line_end = buffer.find(b'\r')
            sentence_end = line_end + 1 if line_end >= 0 else -1
        if sentence_end == -1:
            return self._parse_incomplete_nmea(buffer)
        return self._parse_nmea_sentence(buffer, sentence_end)

    def _get_buffer_parser(self, buffer):
        if len(buffer) > 1 and buffer[:2] == b'\xB5\x62':
            return self._parse_ubx_buffer
        first_byte = buffer[0]
        if first_byte == 0xD3:
            return self._parse_rtcm3_buffer
        if first_byte == ord('#'):
            return self._parse_unicore_buffer
        if first_byte == ord('$'):
            return self._parse_nmea_buffer
        return None

    def _parse_buffer(self, buffer: bytearray) -> bytearray:
        while buffer:
            if len(buffer) == 1 and buffer[0] == 0xB5:
                break
            parser = self._get_buffer_parser(buffer)
            if parser is None:
                self.unknown_bytes_dropped += 1
                buffer.pop(0)
                continue
            remaining = parser(buffer)
            if remaining is None:
                break
            buffer = remaining
        return buffer

    def _reader_should_pause(self):
        if self._pause_event.is_set():
            time.sleep(0.5)
            return True
        if not is_remote_locked():
            return False
        if self._reader_error_count == 0:
            self.log("WARNING", "Device is remotely locked, pausing GNSS reader")
        time.sleep(1)
        return True

    def _refresh_reader_port(self, current_port, buffer):
        available_ports = [port.device for port in serial.tools.list_ports.comports()]
        if current_port in available_ports:
            return current_port, True
        self.log("WARNING", f"Port {current_port} disappeared! Attempting auto-redetection...")
        chip_info = find_chip_robustly()
        if not chip_info or not chip_info.get("port"):
            self.log("ERROR", "Failed to find GNSS chip. Retrying in 5s...")
            time.sleep(5)
            return current_port, False
        current_port = chip_info["port"]
        self.port = current_port
        self.baudrate = chip_info.get("baud", self.baudrate)
        self.log("SUCCESS", f"Re-detected GNSS chip on NEW PORT: {current_port} @ {self.baudrate}")
        self._reader_error_count = 0
        buffer.clear()
        return current_port, True

    def _increase_reader_kernel_buffer(self, current_port):
        try:
            if IS_RASPBERRY_PI and self._reader_error_count == 0:
                temp_ser = serial.Serial(current_port, self.baudrate, timeout=0.1)
                try:
                    temp_ser.set_buffer_size(rx_size=16384, tx_size=4096)
                    self.log("DEBUG", f"Increased serial buffer size for {current_port}")
                except AttributeError:
                    pass
                temp_ser.close()
        except Exception as error:
            self.log("DEBUG", f"Could not set buffer size: {error}")

    def _inject_queued_rtcm(self, ser):
        inject_count = 0
        inject_bytes = 0
        while not self.rtcm_inject_queue.empty():
            try:
                packet = self.rtcm_inject_queue.get_nowait()
                if ser.write(packet) != len(packet):
                    raise IOError("Incomplete RTCM correction write")
                inject_count += 1
                inject_bytes += len(packet)
            except Empty:
                break
            except Exception as error:
                self.log("WARNING", f"RTCM inject write error: {error}")
                break
        if inject_count == 0:
            return
        if not hasattr(self, "_inject_total"):
            self._inject_total = 0
            self._inject_bytes_total = 0
            self._inject_last_log = 0
        self._inject_total += inject_count
        self._inject_bytes_total += inject_bytes
        now = time.time()
        if now - self._inject_last_log >= 30.0:
            self.log("INFO", f"[RTCM INJECT] Written {self._inject_total} pkts / {self._inject_bytes_total}B to serial port")
            self._inject_last_log = now

    def _parse_reader_buffer_batch(self, buffer):
        attempts = 0
        while len(buffer) > 0 and attempts < 20:
            old_length = len(buffer)
            parsed_buffer = self._parse_buffer(buffer)
            if parsed_buffer is not buffer:
                buffer[:] = parsed_buffer
            if len(buffer) >= old_length:
                break
            attempts += 1
        return buffer

    def _read_reader_batch(self, ser, buffer):
        if ser.in_waiting <= 0:
            return buffer
        bytes_to_read = min(ser.in_waiting, 4096)
        buffer.extend(ser.read(bytes_to_read))
        self._consecutive_read_failures = 0
        return self._parse_reader_buffer_batch(buffer)

    def _trim_reader_buffer(self, buffer):
        if len(buffer) <= 8192:
            return buffer
        self.log("WARNING", f"Buffer overflow ({len(buffer)} bytes) - trimming and resync")
        tail = bytearray(buffer[-4096:])
        marker_positions = []
        for marker in (b"$", b"\xd3", b"\xb5\x62"):
            position = tail.find(marker)
            if position != -1:
                marker_positions.append(position)
        if marker_positions:
            buffer[:] = tail[min(marker_positions):]
        else:
            buffer.clear()
        self.buffer_overflows += 1
        return buffer

    def _log_reader_statistics(self, buffer):
        current_time = time.time()
        if current_time - self._last_stats_log > 30:
            _ = self.get_statistics()
            self._last_stats_log = current_time
        self._log_parser_debug_if_due(current_time, len(buffer))

    def _handle_reader_read_error(self, current_port, read_error, buffer):
        self._consecutive_read_failures += 1
        if self._consecutive_read_failures < 5:
            time.sleep(0.1)
            return False
        current_time = time.time()
        if current_time - self._last_error_log_time > 5:
            self.log("WARNING", f"Read instability on {current_port}: {read_error}")
            self._last_error_log_time = current_time
        _mark_gnss_data_stale()
        buffer.clear()
        return True

    def _handle_reader_parse_error(self, parse_error, buffer):
        current_time = time.time()
        if current_time - self._last_error_log_time > 10:
            self.log("ERROR", f"Data parsing error: {str(parse_error)[:100]}")
            self._last_error_log_time = current_time
        buffer.clear()
        self.parse_errors += 1
        time.sleep(0.1)

    def _read_reader_loop(self, ser, current_port, buffer):
        while not self._stop_event.is_set():
            if self._pause_event.is_set() or is_remote_locked():
                break
            try:
                self._service_base_query(ser)
                self._inject_queued_rtcm(ser)
                buffer = self._read_reader_batch(ser, buffer)
                buffer = self._trim_reader_buffer(buffer)
                self._log_reader_statistics(buffer)
                time.sleep(0.003)
            except (serial.SerialException, OSError) as read_error:
                if self._handle_reader_read_error(current_port, read_error, buffer):
                    break
            except Exception as parse_error:
                self._handle_reader_parse_error(parse_error, buffer)

    def _open_reader_connection(self, current_port, buffer):
        with serial_port_lock:
            with serial.Serial(
                current_port,
                self.baudrate,
                timeout=1,
                write_timeout=1
            ) as ser:
                buffer.clear()
                self._consecutive_read_failures = 0
                if self._reader_error_count > 0:
                    self.log("SUCCESS", f"Reconnected to {current_port} after {self._reader_error_count} failed attempts")
                else:
                    self.log("SUCCESS", f"Connected to GNSS receiver on {current_port}")
                self._reader_error_count = 0
                self._read_reader_loop(ser, current_port, buffer)

    def _handle_reader_connection_error(self, conn_error, current_port, buffer):
        _telemetry_inc("reconnect", "serial")
        self._reader_error_count += 1
        wait_time = min(5 * self._reader_error_count, 30)
        current_time = time.time()
        should_log = (
            self._reader_error_count == 1
            or self._reader_error_count <= 3
            or self._reader_error_count % 10 == 0
            or current_time - self._last_error_log_time > 30
        )
        if should_log:
            if self._reader_error_count == 1:
                if "FileNotFoundError" in str(type(conn_error).__name__):
                    self.log("ERROR", f"Port {current_port} disappeared (device unplugged?)")
                else:
                    self.log("WARNING", f"Connection lost on {current_port}: {str(conn_error)[:100]}")
            elif self._reader_error_count <= 3:
                self.log("INFO", f"Reconnection attempt #{self._reader_error_count} in {wait_time}s...")
            else:
                self.log("WARNING", f"Persistent failure (attempt {self._reader_error_count}). Next retry in {wait_time}s")
            self._last_error_log_time = current_time
        buffer.clear()
        _mark_gnss_data_stale()
        time.sleep(wait_time)

    def _handle_reader_critical_error(self, critical_error, buffer):
        self._reader_error_count += 1
        self.log("ERROR", f"Critical error in GNSSReader: {critical_error}", exc_info=True)
        buffer.clear()
        _mark_gnss_data_stale()
        time.sleep(min(5 * self._reader_error_count, 30))

    def run(self):
        if not self.port:
            self.log("ERROR", "GNSSReader: No serial port configured. Thread exiting.")
            return
        self.log("SUCCESS", f"GNSSReader thread started for {self.port}")
        self._reader_error_count = 0
        self._last_error_log_time = 0
        self._consecutive_read_failures = 0
        self._last_stats_log = time.time()
        buffer = bytearray()
        current_port = self.port
        while not self._stop_event.is_set():
            try:
                if self._reader_should_pause():
                    continue
                current_port, port_ready = self._refresh_reader_port(current_port, buffer)
                if not port_ready:
                    continue
                self._increase_reader_kernel_buffer(current_port)
                self._open_reader_connection(current_port, buffer)
            except (serial.SerialException, OSError, FileNotFoundError) as conn_error:
                self._handle_reader_connection_error(conn_error, current_port, buffer)
            except Exception as critical_error:
                self._handle_reader_critical_error(critical_error, buffer)
        self.log("INFO", f"GNSSReader thread stopped. Final statistics: {self.get_statistics()}")

def _base_ubx_payload(packet, key):
    if (len(packet) < 8 or packet[:2] != b'\xb5\x62' or tuple(packet[2:4]) != key
            or len(packet) != int.from_bytes(packet[4:6], 'little') + 8
            or _ubx_packet(*key, packet[6:-2]) != packet):
        raise ValueError("Invalid UBX readback checksum or frame")
    payload = packet[6:-2]
    if key[0] == 0x27 and key[1] in (9, 0x10):
        _ubx_security_payload(payload, key[1])
        return payload
    if key == (0x0a, 4):
        _ubx_version_payload(payload)
        return payload
    if key in ((0x0a, 0x38), (0x0a, 0x31)):
        _ubx_rf_payload(payload, key[1])
        return payload
    if key == (1, 0x35):
        _ubx_satellite_payload(payload)
        return payload
    size = {(1, 7): 92, (1, 4): 18, (6, 0x39): 8, (6, 0x8b): 9}.get(key, 40)
    if len(payload) != size or (size == 40 and payload[0] != 0):
        raise ValueError("Unsupported UBX readback payload")
    return payload


def _ubx_cfg_values(body):
    if len(body) < 4 or body[:4] not in (b'\x01\0\0\0', b'\x01\x01\0\0', b'\x01\x02\0\0'):
        raise ValueError('Invalid CFG-VALGET header')
    values, offset = {}, 4
    while offset < len(body):
        if offset + 4 > len(body):
            raise ValueError('Truncated configuration key')
        key = int.from_bytes(body[offset:offset+4], 'little')
        size = {1:1,2:1,3:2,4:4,5:8}.get(key >> 28)
        if size is None or key in values or len(values) >= 64 or offset+4+size > len(body):
            raise ValueError('Invalid configuration key/value')
        values[key] = body[offset+4:offset+4+size]
        offset += 4+size
    return values


def _ubx_tmode_fields():
    return [(0x20030001,2,1),(0x20030002,3,1),
            (0x40030003,4,4),(0x40030004,8,4),(0x40030005,12,4),
            (0x20030006,16,1),(0x20030007,17,1),(0x20030008,18,1),
            (0x40030009,4,4),(0x4003000a,8,4),(0x4003000b,12,4),
            (0x2003000c,16,1),(0x2003000d,17,1),(0x2003000e,18,1),
            (0x4003000f,20,4),(0x40030010,24,4),(0x40030011,28,4)]


async def _read_ublox_tmode(reader, modern=False):
    if not modern:
        return _base_ubx_payload(await reader.query_base(_ubx_packet(6,0x71),(6,0x71)),(6,0x71))
    fields = _ubx_tmode_fields()
    poll = bytes(4)+b''.join(struct.pack('<I',key) for key,_,_ in fields)
    packet = await reader.query_base(_ubx_packet(6,0x8b,poll),(6,0x8b))
    if packet != _ubx_packet(6,0x8b,packet[6:-2]) or packet[6:10] != b'\x01\0\0\0':
        raise ValueError('Invalid TMODE VALGET response')
    values = _ubx_cfg_values(packet[6:-2])
    if set(values) != {key for key,_,_ in fields} or values[0x20030001][0] > 2 or values[0x20030002][0] > 1:
        raise ValueError('Incomplete or invalid TMODE keys')
    mode = bytearray(40)
    lla = values[0x20030002] == b'\x01'
    for key,offset,size in fields:
        if (key in (0x40030003,0x40030004,0x40030005,0x20030006,0x20030007,0x20030008) and lla
            or key in (0x40030009,0x4003000a,0x4003000b,0x2003000c,0x2003000d,0x2003000e) and not lla):
            continue
        mode[offset:offset+size] = values[key]
    return bytes(mode)


def _base_ecef_position(x, y, z):
    if not 5000000 <= math.sqrt(x*x + y*y + z*z) <= 8000000:
        raise ValueError("Receiver base ECEF coordinates are invalid")
    lat, lon, height = _ecef_to_llh_rad(x, y, z, 6378137.0, 6356752.314245179)
    return math.degrees(lat), math.degrees(lon), height


def _base_ubx_position(payload, *, survey=False, ready=False):
    start, hp_start = (12, 24) if survey else (4, 16)
    whole = struct.unpack_from('<iii', payload, start)
    hp = struct.unpack_from('<bbb', payload, hp_start)
    if any(abs(value) > 99 for value in hp):
        raise ValueError("Invalid UBX high precision coordinates")
    if not survey and payload[3] & 1:
        lat = whole[0] * 1e-7 + hp[0] * 1e-9
        lon = whole[1] * 1e-7 + hp[1] * 1e-9
        height = whole[2] * 0.01 + hp[2] * 0.0001
    else:
        lat, lon, height = _base_ecef_position(*(a * 0.01 + b * 0.0001 for a, b in zip(whole, hp)))
    if not all(math.isfinite(value) for value in (lat, lon, height)) or abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("Invalid UBX base coordinates")
    return {'state':'ok', 'message':'UBX', 'position_type':'FIXEDPOS' if ready else 'SURVEY-IN-ESTIMATE',
                'datum':'WGS84', 'altitude_reference':'ELLIPSOID',
                'lat':repr(lat), 'lon':repr(lon), 'alt':repr(height)}


def _base_unicore_fields(packet):
    text = packet.decode('ascii').strip()
    body, checksum = text.rsplit('*', 1)
    if not text.startswith('#') or len(checksum) not in (2, 8) or not re.fullmatch(r'[0-9a-fA-F]+', checksum):
        raise ValueError("Invalid Unicore readback checksum format")
    crc = 0
    for value in (body if len(checksum) == 2 else body[1:]).encode('ascii'):
        crc ^= value
        if len(checksum) == 8:
            for _ in range(8):
                crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    if crc != int(checksum, 16):
        raise ValueError("Unicore readback checksum mismatch")
    return text, body.split(';', 1)[1].strip().rstrip(',')


async def _add_ublox_survey_status(reader, mode, result):
    svin = _base_ubx_payload(await reader.query_base(_ubx_packet(1, 0x3b), (1, 0x3b)), (1, 0x3b))
    if svin[36] > 1 or svin[37] > 1:
        raise ValueError("Invalid NAV-SVIN status flags")
    ready = bool(svin[36] and not svin[37])
    result.update(target_seconds=struct.unpack_from('<I', mode, 24)[0],
                  accuracy_limit_m=struct.unpack_from('<I', mode, 28)[0] * 0.0001,
                  elapsed_seconds=struct.unpack_from('<I', svin, 8)[0],
                  observations=struct.unpack_from('<I', svin, 32)[0],
                  mean_accuracy_m=struct.unpack_from('<I', svin, 28)[0] * 0.0001,
                  active=bool(svin[37]), valid=bool(svin[36]))
    if svin[36] or svin[37]:
        result['bestpos'] = _base_ubx_position(svin, survey=True, ready=ready)


async def _read_ublox_base_status(reader, result):
    result['query'] = 'CFG-TMODE3 / NAV-SVIN'
    modern = (globals().get('LAST_UBX_RECEIVER_INFO') or {}).get('itfm_supported') is False
    mode = await _read_ublox_tmode(reader, modern)
    if modern:
        result['query'] = 'CFG-VALGET / NAV-SVIN'
    flags = int.from_bytes(mode[2:4], 'little')
    kind = flags & 0xff
    if kind not in (0, 1, 2):
        raise ValueError("Unsupported u-blox receiver mode")
    result.update(state='ok', mode_kind=('rover', 'survey_in', 'base')[kind],
                  mode_raw=('Rover', 'Survey-In', 'Fixed LLA')[kind] + (' (CFG-VALGET)' if modern else ' (CFG-TMODE3)'))
    if kind == 2:
        result['bestpos'] = _base_ubx_position(mode, ready=True)
    elif kind == 1:
        await _add_ublox_survey_status(reader, mode, result)


def _record_unicore_accuracy(fields):
    if len(fields) < 10:
        return
    try:
        lat_sigma, lon_sigma = map(float, fields[7:9])
        if not all(math.isfinite(v) and 0 <= v <= 10000000 for v in (lat_sigma, lon_sigma)):
            raise ValueError('Invalid BESTNAV standard deviations')
        globals()['LAST_RECEIVER_QUALITY'] = {
            'source':'unicore_bestnav_sigma', 'hacc_m':math.hypot(lat_sigma, lon_sigma)}
        globals()['LAST_RECEIVER_QUALITY_TS'] = time.time()
    except ValueError:
        pass  # Position remains useful when optional statistics are unavailable.


async def _read_unicore_best_position(reader):
    raw, payload = _base_unicore_fields(await reader.query_base(b'\r\nBESTNAVA\r\n', b'#BESTNAVA'))
    fields = payload.split(',')
    if len(fields) < 7 or fields[0] != 'SOL_COMPUTED' or fields[6] != 'WGS84':
        raise ValueError("Unicore BESTNAV has no computed WGS84 position")
    lat, lon, height, undulation = map(float, fields[2:6])
    if not all(math.isfinite(v) for v in (lat, lon, height, undulation)) or abs(lat) > 90 or abs(lon) > 180:
        raise ValueError("Invalid Unicore BESTNAV coordinates")
    _record_unicore_accuracy(fields)
    return {'state':'ok', 'message':'BESTNAV', 'raw':raw,
            'solution_status':fields[0], 'position_type':fields[1],
            'latitude':lat, 'longitude':lon, 'height_msl':height}


async def _read_unicore_base_status(reader, result):
    globals()['LAST_RECEIVER_QUALITY'] = None
    result['query'] = 'MODE / BESTNAVA'
    _, mode = _base_unicore_fields(await reader.query_base(b'\r\nMODE\r\n', b'#MODE'))
    mode = mode.upper()
    if not mode.startswith((UNICORE_BASE_MODE_PREFIX, 'MODE ROVER')):
        raise ValueError("Unsupported Unicore operating mode")
    if mode.startswith(f'{UNICORE_BASE_MODE_PREFIX} TIME'):
        kind = 'survey_in'
    elif mode.startswith(UNICORE_BASE_MODE_PREFIX):
        kind = 'base'
    else:
        kind = 'rover'
    result.update(state='ok', mode_kind=kind, mode_raw=mode)
    if kind == 'survey_in':
        result['target_seconds'] = int(mode.split()[3])
    try:
        result['bestpos'] = await _read_unicore_best_position(reader)
    except Exception as error:
        result['bestpos'] = {'state':'unavailable', 'error':str(error)}
        raise


def _save_completed_survey_result(agent, chip, result):
    position = result.get('bestpos', {})
    if (result.get('mode_kind') != 'survey_in' or position.get('position_type') != 'FIXEDPOS'
            or position.get('state') != 'ok'):
        return
    saved = {'chip':chip, 'bestpos':position, 'mode_raw':result['mode_raw'], 'recorded_at':int(time.time())}
    previous_saved = agent.config.get('survey_in_result') or {}
    previous_position = previous_saved.get('bestpos') or {}
    if ({k: v for k, v in previous_position.items() if k != 'raw'}
            != {k: v for k, v in position.items() if k != 'raw'}
            or previous_saved.get('mode_raw') != saved['mode_raw']):
        agent.config['survey_in_result'] = saved
        agent.save_config()


async def refresh_receiver_base_status(agent, reader):
    """Read configuration and position without changing mode or enabling NMEA."""
    previous = getattr(agent, 'receiver_base_readback', {})
    chip = agent.detected_chip.get('type')
    result = {'chip':chip, 'revision':int(previous.get('revision', 0)) + 1, 'state':'error',
              'mode_kind':'unknown'}
    try:
        if chip not in ('Ublox', 'Unicorecomm') or reader is None:
            raise RuntimeError("Base readback requires a supported GNSS reader")
        if chip == 'Ublox':
            await _read_ublox_base_status(reader, result)
        else:
            await _read_unicore_base_status(reader, result)
    except Exception as error:
        result['error'] = str(error)
        logging.warning('[GNSS][READBACK] %s: %s', chip, error)
        raise
    finally:
        agent.receiver_base_readback = result
        agent.receiver_base_readback_at = time.monotonic()
    position = result.get('bestpos', {})
    logging.info('[GNSS][READBACK] %s mode=%s position=%s revision=%s', chip,
                 result.get('mode_raw'), position.get('position_type', 'unavailable'), result['revision'])
    _save_completed_survey_result(agent, chip, result)
    return result


def _gnss_verification_expected(payload):
    """Accept bounded, exact output keys; these bytes are never sent as setters."""
    mode_b64, output_b64 = payload.get('tmode_b64'), payload.get('output_b64')
    if (not isinstance(mode_b64, str) or len(mode_b64) > 56
            or not isinstance(output_b64, str) or len(output_b64) > 680):
        raise ValueError('Invalid GNSS verification expectation')
    mode = base64.b64decode(mode_b64, validate=True)
    output = base64.b64decode(output_b64, validate=True)
    if len(mode) != 40 or mode[:1] != b'\0' or mode[2:4] != b'\x02\x01' or not output or len(output) % 5:
        raise ValueError('Invalid Fixed LLA/output verification expectation')
    records = []
    keys = set()
    for offset in range(0, len(output), 5):
        key, value = struct.unpack_from('<IB', output, offset)
        if (key not in (0x10510003, 0x10520005, 0x10530005, 0x10650001, 0x10640006) and key >> 16 not in (0x2091, 0x1072, 0x1074, 0x1076, 0x1078, 0x107a)
                or key & 0xffff == 0xffff or value not in (0, 1) or key in keys):
            raise ValueError('Invalid or duplicate output verification key')
        keys.add(key)
        records.append((key, value))
    return mode, records


def _unicore_loglist_body(packet):
    if not packet.startswith(UNICORE_LOG_LIST_KEY + b',') or len(packet) > 4096:
        raise ValueError('Invalid UNILOGLIST header/size')
    if b'*' in packet:
        return _base_unicore_fields(packet)[1]
    # Some firmware returns a counted list and blank terminator without CRC.
    if not packet.endswith((b'\r\n\r\n', b'\n\n')):
        raise ValueError('Incomplete UNILOGLIST terminator')
    text = packet.decode('ascii')
    if any(ord(char) < 32 and char not in '\t\r\n' or ord(char) == 127 for char in text):
        raise ValueError('Invalid UNILOGLIST characters')
    body = text.split(';', 1)[1]
    if any(not line.lstrip().startswith('<') for line in body.splitlines() if line.strip()):
        raise ValueError('Invalid UNILOGLIST row prefix')
    return body


def _parse_unicore_loglist_entry(line):
    tokens = line.upper().split()
    if tokens and tokens[0] == 'LOG':
        tokens = tokens[1:]
    if len(tokens) < 3:
        raise ValueError('Invalid UNILOGLIST row')
    name, port = (tokens[1], tokens[0]) if tokens[0].startswith('COM') else (tokens[0], tokens[1])
    if not re.fullmatch(r'[A-Z][A-Z0-9]{1,23}', name) or port not in ('COM1', 'COM2', 'COM3'):
        raise ValueError('Invalid UNILOGLIST message/port')
    trigger = tokens[2:]
    if trigger == ['ONCE']:
        return (port, name), None
    if trigger[:1] == ['ONTIME']:
        trigger = trigger[1:]
    if len(trigger) != 1:
        raise ValueError('Unsupported UNILOGLIST trigger')
    period = float(trigger[0])
    if not math.isfinite(period) or period <= 0:
        raise ValueError('Invalid UNILOGLIST period')
    return (port, name), period


def _parse_unicore_loglist(packet):
    body = _unicore_loglist_body(packet)
    lines = [line.strip().lstrip('<').strip() for line in body.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r'\d+', lines[0]) or not 0 <= int(lines[0]) <= 64 or len(lines) != int(lines[0]) + 1:
        raise ValueError('Incomplete UNILOGLIST entries')
    logs = {}
    for line in lines[1:]:
        key, period = _parse_unicore_loglist_entry(line)
        if key in logs:
            raise ValueError('Duplicate UNILOGLIST entry')
        logs[key] = period
    return logs


async def _verify_unicore_configuration(agent, reader, payload, command_id):
    coords = payload.get('coords') or {}
    wanted_position = [float(coords[key]) for key in ('lat', 'lon', 'alt')]
    expected_logs = payload.get('logs')
    if (not all(math.isfinite(value) for value in wanted_position)
            or not -90 <= wanted_position[0] <= 90 or not -180 <= wanted_position[1] <= 180
            or not -12000 <= wanted_position[2] <= 100000 or not isinstance(expected_logs, list)
            or not 1 <= len(expected_logs) <= 64
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9]{1,23}', name) for name in expected_logs)):
        raise ValueError('Invalid Unicore verification expectation')
    groups = {name: {'checked':0, 'mismatched':0, 'unread':0} for name in ('fixed', 'diagnostics', 'rtcm')}
    details = {'kind':'Unicorecomm', 'fixed':{'expected': {'mode': 'Fixed', 'self_position': dict(zip(('lat', 'lon', 'alt'), wanted_position))}},
                   'logs':[], 'expected_logs':expected_logs, 'compare_ports':[payload.get('port')]}
    result = {'command_id':command_id, 'groups':groups, 'details':details}
    try:
        _, mode = _base_unicore_fields(await reader.query_base(b'\r\nMODE\r\n', b'#MODE'))
        mode = mode.upper()
        details['fixed']['actual'] = {'mode': mode}
        if not mode.startswith((UNICORE_BASE_MODE_PREFIX, 'MODE ROVER')):
            raise ValueError('Invalid Unicore MODE response')
        if not mode.startswith(UNICORE_BASE_MODE_PREFIX) or mode.startswith(f'{UNICORE_BASE_MODE_PREFIX} TIME'):
            groups['fixed'].update(checked=1, mismatched=1)
        else:
            _, body = _base_unicore_fields(await reader.query_base(b'\r\nBESTNAVA\r\n', b'#BESTNAVA'))
            fields = body.split(',')
            if len(fields) < 7 or fields[:2] != ['SOL_COMPUTED', 'FIXEDPOS'] or fields[6] != 'WGS84':
                raise ValueError('Unicore has no confirmed fixed WGS84 position')
            latitude, longitude, height, undulation = map(float, fields[2:6])
            if not all(math.isfinite(value) for value in (latitude, longitude, height, undulation)):
                raise ValueError('Invalid Unicore fixed position')
            if payload.get('altitude_reference') == 'ELLIPSOID':
                height += undulation
            details['fixed']['actual'].update(self_position={'lat': latitude, 'lon': longitude, 'alt': height},
                                                height_msl=float(fields[4]), undulation=undulation, datum=fields[6], position_type=fields[1])
            matches = all(abs(a - b) <= tolerance for a, b, tolerance in zip(
                (latitude, longitude, height), wanted_position, (1e-9, 1e-9, 0.0001)))
            groups['fixed'].update(checked=1, mismatched=int(not matches))
    except Exception as error:
        groups['fixed']['unread'] = 1
        result['mode_error'] = str(error)[:200]
    log_packet = b''
    try:
        log_packet = await reader.query_base(b'\r\nUNILOGLIST\r\n', UNICORE_LOG_LIST_KEY)
        logs = _parse_unicore_loglist(log_packet)
        details['logs'] = [{'port':port, 'name':name, 'period':period} for (port, name), period in logs.items()]
        details['logs_state'] = 'ok'
        port = payload.get('port')
        if port not in ('COM1', 'COM2', 'COM3'):
            raise ValueError('Unicore output port is not identified')
        active = {name: period for (output_port, name), period in logs.items() if output_port == port and period is not None}
        for name in ('diagnostics', 'rtcm'):
            rtcm = name == 'rtcm'
            keys = {key for key in set(expected_logs) | set(active) if key.startswith('RTCM') == rtcm}
            groups[name]['checked'] = max(1, len(keys))
            groups[name]['mismatched'] = sum(key not in expected_logs or key not in active
                                            or abs(active[key] - 1) > 1e-6 for key in keys)
    except Exception as error:
        details.setdefault('logs_state', 'unread')
        groups['diagnostics']['unread'] = groups['rtcm']['unread'] = 1
        result['output_error'] = str(error)[:200]
        result['note'] = 'Chưa đọc đủ danh sách bản tin và cổng đầu ra từ UM.'
        logging.warning("[GNSS][VERIFY][UNILOGLIST] %s: %s; response=%r",
                        type(error).__name__, result['output_error'], log_packet[:512])
    result['state'] = ('unread' if any(group['unread'] for group in groups.values())
                       else 'mismatch' if any(group['mismatched'] for group in groups.values()) else 'match')
    agent.gnss_verification = result
    agent.gnss_verification_at = time.monotonic()
    logging.log(logging.INFO if result['state'] == 'match' else logging.WARNING,
                "[GNSS][VERIFY] Unicorecomm command_id=%s state=%s groups=%s mode_error=%s output_error=%s",
                command_id, result['state'], groups, result.get('mode_error', ''), result.get('output_error', ''))
    return result


def _ublox_readback_jobs(payload, records, result):
    inspection = payload.get('readback')
    if not inspection:
        return [(None, records[index:index + 40]) for index in range(0, len(records), 40)], None
    fields = inspection.get('fields', [])
    if not 3 <= len(fields) <= 40 or any(
        not isinstance(field, str) or len(field) > 32 for field in fields
    ):
        raise ValueError('Invalid readback field names')
    ports = inspection.get('ports', [])
    if len(ports) != 5:
        raise ValueError('Expected five u-blox readback ports')
    jobs = []
    seen_ports = set()
    allowed_ports = ('I2C', 'UART1', 'UART2', 'USB', 'SPI')
    for port in ports:
        name = port.get('name')
        if name not in allowed_ports or name in seen_ports:
            raise ValueError('Invalid or duplicate readback port')
        _, port_records = _gnss_verification_expected(
            {**payload, 'output_b64': port.get('output_b64')}
        )
        if len(port_records) != len(fields):
            raise ValueError('Readback field/key count mismatch')
        jobs.append((name, port_records))
        seen_ports.add(name)
    details = {
        'kind': 'Ublox', 'fields': fields,
        'expected': inspection.get('expected', []),
        'compare_ports': inspection.get('compare_ports', []),
        'ports': [], 'fixed': {},
    }
    result['details'] = details
    return jobs, details


def _ublox_describe_tmode(mode):
    state = {
        'mode': {0: 'Rover', 1: 'Survey-In', 2: 'Fixed'}.get(mode[2], 'Unknown'),
        'coordinate_type': 'LLA' if mode[3] & 1 else 'ECEF',
        'accuracy_m': struct.unpack_from('<I', mode, 20)[0] * 0.0001,
    }
    if mode[2] == 2:
        position = _base_ubx_position(mode, ready=True)
        if mode[3] & 1:
            units = [
                struct.unpack_from('<i', mode, 4 + index * 4)[0] * 100
                + struct.unpack_from('<b', mode, 16 + index)[0]
                for index in range(3)
            ]
            position.update(
                lat=f'{units[0] / 1e9:.9f}',
                lon=f'{units[1] / 1e9:.9f}',
                alt=f'{units[2] / 1e4:.4f}',
            )
        state.update(self_position=position)
    return state


async def _verify_ublox_fixed_mode(reader, expected, payload, groups, result, details):
    try:
        actual = await _read_ublox_tmode(reader, payload.get('tmode_valget') is True)
        expected_position = [
            struct.unpack_from('<i', expected, 4 + index * 4)[0] * 100
            + struct.unpack_from('<b', expected, 16 + index)[0]
            for index in range(3)
        ]
        actual_position = [
            struct.unpack_from('<i', actual, 4 + index * 4)[0] * 100
            + struct.unpack_from('<b', actual, 16 + index)[0]
            for index in range(3)
        ]
        # Same quantization tolerance as the ESP32 verifier: 1e-9 degree / 0.1 mm.
        matches = (
            actual[2:4] == b'\x02\x01'
            and all(abs(value) <= 99 for value in struct.unpack_from('<bbb', actual, 16))
            and all(abs(a - b) <= 1 for a, b in zip(actual_position, expected_position))
            and abs(struct.unpack_from('<I', actual, 20)[0] - struct.unpack_from('<I', expected, 20)[0]) <= 1
        )
        groups['fixed'].update(checked=1, mismatched=int(not matches))
        if details is not None:
            details['fixed'] = {
                'actual': _ublox_describe_tmode(actual),
                'expected': _ublox_describe_tmode(expected),
            }
    except Exception as error:
        groups['fixed']['unread'] = 1
        result['mode_error'] = str(error)[:200]


async def _read_ublox_valget(reader, chunk):
    try:
        poll = b'\0\0\0\0' + b''.join(
            struct.pack('<I', key) for key, _ in chunk
        )
        reply = await reader.query_base(_ubx_packet(6, 0x8b, poll), (6, 0x8b))
        body = reply[6:-2]
        if (len(reply) != len(body) + 8 or reply != _ubx_packet(6, 0x8b, body)
                or len(body) != 4 + len(chunk) * 5 or body[:4] != b'\x01\0\0\0'):
            raise ValueError('Invalid CFG-VALGET response')
        received = [struct.unpack_from('<IB', body, index) for index in range(4, len(body), 5)]
        values = dict(received)
        if len(values) != len(chunk) or set(values) != {key for key, _ in chunk}:
            raise ValueError('Missing or duplicate CFG-VALGET keys')
        return values, None
    except Exception as error:
        return None, str(error)[:200]


async def _read_ublox_port_baud(reader, port_name, payload):
    try:
        port_id = int(port_name[-1])
        if payload.get('tmode_valget') is True:
            key = 0x40520001 if port_id == 1 else 0x40530001
            command = _ubx_packet(6, 0x8b, bytes(4) + struct.pack('<I', key))
            reply = await reader.query_base(command, (6, 0x8b))
            if reply != _ubx_packet(6, 0x8b, reply[6:-2]):
                raise ValueError('Invalid baud VALGET response')
            baud_values = _ubx_cfg_values(reply[6:-2])
            if set(baud_values) != {key}:
                raise ValueError('Invalid baud key')
            return int.from_bytes(baud_values[key], 'little')
        reply = await reader.query_base(_ubx_packet(6, 0, bytes([port_id])), (6, 0))
        body = reply[6:-2]
        if len(body) != 20 or reply != _ubx_packet(6, 0, body) or body[0] != port_id:
            raise ValueError('Invalid CFG-PRT response')
        return struct.unpack_from('<I', body, 8)[0]
    except Exception:
        return None


def _ublox_readback_port(port_name, chunk, values, details, result):
    port = {
        'name': port_name,
        'state': 'ok' if values is not None else 'unread',
        'values': [values[key] for key, _ in chunk] if values is not None else None,
    }
    if values is not None and 'PORT_ENABLED' in details['fields']:
        port['enabled'] = port['values'][details['fields'].index('PORT_ENABLED')]
    if values is None:
        port['error'] = result.get('output_error', 'Readback unavailable')
    return port


def _update_ublox_verification_groups(groups, desired, values, chunk):
    for key, _ in chunk:
        if key not in desired:
            continue
        name = 'protocols' if key >> 28 == 1 else 'rtcm' if key >= 0x209102bd else 'diagnostics'
        group = groups[name]
        group['checked' if values is not None else 'unread'] += 1
        if values is not None and values[key] != desired[key]:
            group['mismatched'] += 1


async def _collect_ublox_output_readback(reader, jobs, payload, details, desired, groups, result):
    for port_name, chunk in jobs:
        values, error = await _read_ublox_valget(reader, chunk)
        if error is not None:
            result['output_error'] = error
        if details is not None:
            port = _ublox_readback_port(port_name, chunk, values, details, result)
            if port_name in ('UART1', 'UART2'):
                port['baud'] = await _read_ublox_port_baud(reader, port_name, payload)
            details['ports'].append(port)
        _update_ublox_verification_groups(groups, desired, values, chunk)


def _ublox_desired_values(records, jobs, details):
    desired = dict(records)
    if details is None:
        return desired
    compare_keys = {0x10510003, 0x10520005, 0x10530005, 0x10650001, 0x10640006}
    for port_name, chunk in jobs:
        if port_name in details['compare_ports']:
            for key, _ in chunk:
                if key in compare_keys:
                    desired[key] = 1
    return desired


def _finalize_gnss_verification(agent, result, groups):
    unread = any(group['unread'] or not group['checked'] for group in groups.values())
    mismatch = any(group['mismatched'] for group in groups.values())
    result['state'] = 'unread' if unread else 'mismatch' if mismatch else 'match'
    agent.gnss_verification = result
    agent.gnss_verification_at = time.monotonic()
    return result


async def _verify_ublox_configuration(agent, reader, payload, command_id):
    expected, records = _gnss_verification_expected(payload)
    groups = {name: {'checked': 0, 'mismatched': 0, 'unread': 0}
              for name in ('fixed', 'protocols', 'diagnostics', 'rtcm')}
    result = {'command_id': command_id, 'groups': groups}
    jobs, details = _ublox_readback_jobs(payload, records, result)
    desired = _ublox_desired_values(records, jobs, details)
    await _verify_ublox_fixed_mode(reader, expected, payload, groups, result, details)
    await _collect_ublox_output_readback(
        reader, jobs, payload, details, desired, groups, result
    )
    return _finalize_gnss_verification(agent, result, groups)


async def verify_receiver_configuration(agent, reader, payload, command_id):
    """Read the live receiver through its owning UART thread; never write config."""
    agent.gnss_verification = None
    chip_type = agent.detected_chip.get('type')
    if chip_type not in ('Ublox', 'Unicorecomm') or reader is None:
        raise RuntimeError('Configuration verification requires a supported GNSS reader')
    if chip_type == 'Unicorecomm':
        return await _verify_unicore_configuration(agent, reader, payload, command_id)
    return await _verify_ublox_configuration(agent, reader, payload, command_id)


def _receiver_verification_status(agent, system):
    verification = getattr(agent, 'gnss_verification', None)
    verified_at = getattr(agent, 'gnss_verification_at', None)
    if verification and verified_at is not None:
        verification_age = max(0, time.monotonic() - verified_at)
        if verification_age < 60:
            system['gnss_verification'] = {**copy.deepcopy(verification), 'age_seconds': int(verification_age)}
        else:
            agent.gnss_verification = None


def _survey_readback_fields(result, position, ready, fresh):
    if fresh and position.get('state') == 'ok':
        state = 'base_ready' if ready else 'waiting_base'
    else:
        state = 'awaiting_readback'
    return {
        'state': state,
        'source': 'NAV-SVIN' if result['chip'] == 'Ublox' else 'BESTNAV',
        'target_seconds': result.get('target_seconds', 0),
        'readback_age_seconds': result['age_seconds'],
        'fix_status': 'FIXEDPOS' if ready and fresh else 'UNVERIFIED',
        'elapsed_since_ack_seconds': None,
    }


def _add_survey_progress(agent, survey, result):
    started = getattr(agent, 'survey_in_started_at', None)
    if started is not None:
        survey['elapsed_since_ack_seconds'] = int(max(0, time.monotonic() - started))
    for key in ('elapsed_seconds', 'observations', 'mean_accuracy_m', 'active'):
        if key in result:
            survey[key] = result[key]


def _receiver_survey_status(agent, result, age):
    monitoring = result.get('mode_kind') == 'survey_in'
    position = result.get('bestpos', {})
    ready = position.get('state') == 'ok' and position.get('position_type') == 'FIXEDPOS'
    fresh = age is not None and age <= 45 and result.get('state') == 'ok'
    survey = {'state':'not_monitoring'}
    if monitoring:
        survey.update(_survey_readback_fields(result, position, ready, fresh))
        _add_survey_progress(agent, survey, result)
    elif agent.get_base_config().get('base_setup_method') == 'SURVEY_IN' and (not result or result.get('error')):
        survey['state'] = 'awaiting_readback'
    return survey


def receiver_base_system_info(agent):
    result = copy.deepcopy(getattr(agent, 'receiver_base_readback', {}))
    checked_at = getattr(agent, 'receiver_base_readback_at', None)
    age = max(0, time.monotonic() - checked_at) if checked_at is not None else None
    system = {'gnss_valset_supported':True, 'gnss_verification_supported':True, 'gnss_verification_details_supported':True, 'gnss_verification_uart1':False,
              'gnss_verification':None}
    _receiver_verification_status(agent, system)
    if result:
        result['age_seconds'] = int(age) if age is not None else 0xffffffff
        system['receiver_base_readback'] = result
    if agent.config.get('survey_in_result'):
        system['survey_in_result'] = copy.deepcopy(agent.config['survey_in_result'])
    system['survey_in_status'] = _receiver_survey_status(agent, result, age)
    return system


class AgentManager:
    def __init__(self, serial_number):
        control_plane_fail_open_event.clear()
        self.serial_number = serial_number
        self.config = {}
        self.detected_chip = {"port": None, "type": "UNKNOWN"}
        self.service_workers = []
        self.nmea_publisher = None
        self.gnss_reader = None
        self.receiver_base_readback = {}
        self.receiver_base_readback_at = None
        self.gnss_verification = None
        self.gnss_verification_at = None
        self.survey_in_started_at = None
        self.service_stats = {}
        self.ntrip_connection_status = {} 
        self.stats_lock = threading.Lock()
        self.active_ntrip_casters = {}
        self.cumulative_saved_bytes = 0
        self.cumulative_saved_mwh = 0.0
        self.last_savings_calc_time = time.time()
        self.log = lambda lvl, msg: logging.log(getattr(logging, lvl.upper(), logging.INFO), msg)
        self.rtcm_stream_active = False
        self.control_plane_failover = ControlPlaneFailover(
            STATUS_BOOT_ID,
            stale_after_seconds=CONTROL_PLANE_STALE_SECONDS,
            recovery_after_seconds=CONTROL_PLANE_RECOVERY_SECONDS,
        )
        # Last-known RTK/GGA fix status (updated asynchronously by NMEA dispatcher)
        self.last_gga_fix_status = globals().get('LAST_GGA_FIX_STATUS', 'NO_FIX')
        self.load_config()
        self._force_rtcm_standby_on_startup()

    def _force_rtcm_standby_on_startup(self):
        global rtcm_stream_active_flag
        services = self.config.setdefault('services', {})
        
        global_on_demand = _to_bool(services.get('stream_on_demand', False), False)
        
        if global_on_demand:
            services['stream_active'] = False
            services.pop('active_mountpoint', None)
            
        for sid in _get_ntrip_server_ids(services):
            if _to_bool(services.get(f'server{sid}_stream_on_demand', global_on_demand), global_on_demand):
                services[f'server{sid}_stream_active'] = False
            else:
                services[f'server{sid}_stream_active'] = True
                
        self.rtcm_stream_active = _compute_any_server_can_push(services)
        rtcm_stream_active_flag = self.rtcm_stream_active
        self.save_config()
    
    def load_config(self):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            return
        except Exception as e:
            self.log("WARNING", f"Failed to load primary config ({e}), trying backup...")
            
        bak_path = CONFIG_PATH + '.bak'
        try:
            with open(bak_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            self.log("INFO", "Successfully recovered config from backup")
            self.save_config()
            return
        except Exception as e:
            self.log("WARNING", f"Backup config also failed or missing ({e}). Resetting to default.")
            
        platform_prefix = "PC" if IS_WINDOWS else "Pi"
        self.config = {
            "device_name": f"{platform_prefix}-{self.serial_number[-6:]}",
            "is_provisioned": False,
            "services": {}
        }
        self.save_config()
    
    def save_config(self):
        tmp_path = CONFIG_PATH + '.tmp'
        bak_path = CONFIG_PATH + '.bak'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(CONFIG_PATH):
                import shutil
                try:
                    shutil.copy2(CONFIG_PATH, bak_path)
                except Exception:
                    pass
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            self.log("ERROR", f"Critical error saving config: {e}")
    
    def update_name(self, name: str):
        if name:
            self.config['device_name'] = name
            self.config['is_provisioned'] = True
            self.save_config()
            return True
        return False
    
    def update_service_config(self, cfg: dict):
        # Missing/redacted credentials mean keep the locally saved password.
        current = self.config.get('services') or {}
        cfg = {**current, **cfg}
        removed_servers = set(cfg.pop('removed_servers', []))
        for key in list(cfg):
            match = re.fullmatch(r'server(\d+)_\w+|(?:ntrip_version|username|serverhost|port|mountpoint|password)(\d+)', key)
            if match and int(match.group(1) or match.group(2)) >= 3 and int(match.group(1) or match.group(2)) in removed_servers:
                cfg.pop(key)
        for key in [*(f'password{sid}' for sid in _get_ntrip_server_ids(cfg)), 'rtcmpassword1', 'command_pass']:
            if cfg.get(key) in (None, ''):
                if current.get(key) not in (None, ''):
                    cfg[key] = current[key]
                else:
                    cfg.pop(key, None)
        # Respect user settings from dashboard deployment
        
        # Core logic: derive stream_active from stream_on_demand
        stream_on_demand = bool(cfg.get('stream_on_demand', False))
        
        if not stream_on_demand:
            # Always-On mode: force stream_active = True
            cfg['stream_active'] = True
        else:
            # On-Demand mode: Force stream_active = False so it starts in Sleep mode
            # if not explicitly set (None or missing)
            if cfg.get('stream_active') is None:
                cfg['stream_active'] = False

        # Per-server stream controls with backward-compatible fallback to global flags.
        for server_id in _get_ntrip_server_ids(cfg):
            on_demand_key = f'server{server_id}_stream_on_demand'
            active_key = f'server{server_id}_stream_active'

            if cfg.get(on_demand_key) is None:
                cfg[on_demand_key] = stream_on_demand

            if _to_bool(cfg.get(on_demand_key), stream_on_demand):
                if cfg.get(active_key) is None:
                    cfg[active_key] = bool(cfg.get('stream_active', False))
            else:
                # Always-on for this server.
                cfg[active_key] = True
        
        self.config['services'] = cfg
        
        # Sync current engine state
        self.rtcm_stream_active = _compute_any_server_can_push(cfg, apply_fail_open=True)
        
        global rtcm_stream_active_flag
        rtcm_stream_active_flag = self.rtcm_stream_active
        
        self.save_config()

    @staticmethod
    def _normalize_mountpoint(value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned.lower() if cleaned else None

    def _match_stream_server_by_address(self, services, address):
        normalized_address = str(address).strip().lower()
        for server_id in _get_ntrip_server_ids(services):
            host = str(services.get(f'server{server_id}_host', services.get(f'serverhost{server_id}', '')) or '').strip().lower()
            if host and (normalized_address in host or host in normalized_address):
                logging.info(f"[STREAM CONTROL] Matched address '{address}' to server {server_id} (host: {host})")
                return server_id
        return None

    def _set_explicit_server_stream(self, services, server_id, active, mountpoint):
        previous_switch = _get_server_stream_switches(services, server_id)
        previous_mountpoint = self._normalize_mountpoint(services.get('active_mountpoint'))
        if not bool(active) and not previous_switch['on_demand']:
            logging.info(f"[STREAM CONTROL] Ignoring sleep command for explicit server {server_id} because it is in Always-On mode.")
            return
        services[f'server{server_id}_stream_active'] = bool(active)
        if mountpoint and bool(active):
            services['active_mountpoint'] = str(mountpoint)
        elif not bool(active) and previous_mountpoint == self._normalize_mountpoint(services.get(f'mountpoint{server_id}')):
            services.pop('active_mountpoint', None)

    def _on_demand_stream_servers(self, services, target_mountpoint):
        match_mountpoint = target_mountpoint or self._normalize_mountpoint(services.get('active_mountpoint'))
        target_ids = []
        for server_id in _get_ntrip_server_ids(services):
            switch = _get_server_stream_switches(services, server_id)
            server_mountpoint = self._normalize_mountpoint(services.get(f'mountpoint{server_id}'))
            if switch['enabled'] and switch['on_demand']:
                if match_mountpoint is None or server_mountpoint == match_mountpoint:
                    target_ids.append(server_id)
        return target_ids

    def _target_stream_server_ids(self, services, active, mountpoint, server_id):
        server_ids = _get_ntrip_server_ids(services)
        target_mountpoint = self._normalize_mountpoint(mountpoint)
        if server_id is not None:
            return [server_id] if server_id in server_ids else []
        if not bool(active):
            return self._on_demand_stream_servers(services, target_mountpoint)
        if not target_mountpoint:
            return []
        preferred_id = _resolve_preferred_server_for_mountpoint(services, target_mountpoint)
        return [preferred_id] if preferred_id else []

    def _apply_target_stream_servers(self, services, target_ids, active, mountpoint, previous_mountpoint):
        for server_id in target_ids:
            previous_switch = _get_server_stream_switches(services, server_id)
            if not bool(active) and not previous_switch['on_demand']:
                logging.info(f"[STREAM CONTROL] Ignoring sleep command for server {server_id} because it is Always-On.")
                continue
            services[f'server{server_id}_stream_active'] = bool(active)
            if mountpoint and bool(active):
                services['active_mountpoint'] = str(mountpoint)
            elif not bool(active) and previous_mountpoint == self._normalize_mountpoint(services.get(f'mountpoint{server_id}')):
                services.pop('active_mountpoint', None)

    def _apply_global_stream_state(self, services, active, mountpoint):
        global_on_demand = _to_bool(services.get('stream_on_demand', False), False)
        if not bool(active) and not global_on_demand:
            logging.info("[STREAM CONTROL] Ignoring global sleep command because agent is in Always-On mode.")
            return
        services['stream_active'] = bool(active)
        if mountpoint and bool(active):
            services['active_mountpoint'] = str(mountpoint)
        elif not bool(active):
            services.pop('active_mountpoint', None)

    def set_rtcm_stream_active(self, active: bool, mountpoint: str = None, server_id: int = None, address: str = None):
        global rtcm_stream_active_flag
        services = self.config.setdefault('services', {})
        if address and not server_id:
            server_id = self._match_stream_server_by_address(services, address)

        if server_id in _get_ntrip_server_ids(services):
            self._set_explicit_server_stream(services, server_id, active, mountpoint)
        else:
            previous_mountpoint = self._normalize_mountpoint(services.get('active_mountpoint'))
            target_ids = self._target_stream_server_ids(services, active, mountpoint, server_id)
            if target_ids:
                self._apply_target_stream_servers(services, target_ids, active, mountpoint, previous_mountpoint)
            else:
                self._apply_global_stream_state(services, active, mountpoint)

        self.rtcm_stream_active = _compute_any_server_can_push(
            services,
            apply_fail_open=True,
        )
        rtcm_stream_active_flag = self.rtcm_stream_active
        self.save_config()
        logging.debug("[STREAM CONTROL] Applied stream gate without restarting caster workers.")

    def get_base_config(self):
        return self.config.get('base_config', {})
    
    def get_service_config(self):
        return self.config.get('services', {})

    def apply_control_plane_failover(self, active: bool, reason: str) -> bool:
        """Apply a runtime-only Always-On overlay without saving config."""
        global rtcm_stream_active_flag
        requested = bool(active)
        if control_plane_fail_open_event.is_set() == requested:
            return False

        if requested:
            control_plane_fail_open_event.set()
            self.log(
                "CRITICAL",
                "[CONTROL FAIL-SAFE] WebSocket and MQTT ACKs are stale; "
                "temporarily forcing every enabled output server Always-On.",
            )
        else:
            control_plane_fail_open_event.clear()
            self.log(
                "WARNING",
                "[CONTROL FAIL-SAFE] Backend ACK recovered; restoring saved stream modes.",
            )

        services = self.get_service_config() or {}
        self.rtcm_stream_active = _compute_any_server_can_push(
            services,
            apply_fail_open=True,
        )
        rtcm_stream_active_flag = self.rtcm_stream_active
        self.restart_services()
        return True
    
    def _resolve_reported_status(self):
        base_status = "online" 
        if not self.config.get('is_provisioned', False):
            base_status = "unprovisioned"
        
        # New: Three-state (Online/Sleep/Offline) logic
        services = self.config.get('services', {})
        stream_on_demand = services.get('stream_on_demand') == True
        any_server_can_push = _compute_any_server_can_push(services, apply_fail_open=True)
        
        if base_status == "online" and stream_on_demand and not any_server_can_push:
            base_status = "sleep"

        final_status = current_state.lower()

        if final_status not in [
            "configuring", 
            "rebooting", 
            "rebooting_for_reset", 
            "initializing", 
            "awaiting_license"
        ] and not final_status.startswith("auto_setup"):
            final_status = base_status
        if is_remote_locked():
            final_status = "locked"
        return final_status

    def _build_base_status_payload(self, final_status):
        status = {
            "serial": self.serial_number,
            "rtcm_stats": self.rtcm_mqtt_publisher.decoder.get_stats(self.serial_number) if (getattr(self, "rtcm_mqtt_publisher", None) and getattr(self.rtcm_mqtt_publisher, "decoder", None)) else {},
            "name": self.config.get('device_name'),
            "status": final_status, 
            "version": AGENT_VERSION,
            "agent_token_fingerprint": get_license_fingerprint(),
            "timestamp": round(time.time(), 3),
            "rtk_fix_status": getattr(self, 'last_gga_fix_status', globals().get('LAST_GGA_FIX_STATUS', 'NO_FIX')),
            "last_gga_fix_status": getattr(self, 'last_gga_fix_status', globals().get('LAST_GGA_FIX_STATUS', 'NO_FIX')),
            "last_raw_gga": globals().get('LAST_RAW_GGA'),
            "last_raw_gga_age": round(time.time() - float(globals().get('LAST_RAW_GGA_TS') or 0.0), 1) if (float(globals().get('LAST_RAW_GGA_TS') or 0.0) > 0 and globals().get('LAST_RAW_GGA')) else None,
            "base_mode_active": bool(self.get_base_config()),
            "detected_chip_type": self.detected_chip.get("type", "UNKNOWN"),
            "detected_chip_port": self.detected_chip.get("port"),
            "detected_chip_baud": self.detected_chip.get("baud"),
            "is_provisioned": self.config.get('is_provisioned', False),
            "base_config": self.get_base_config(),
            "service_config": _status_safe_config(self.get_service_config()),
            "auto_base_progress": globals().get("AUTO_BASE_PROGRESS") or ({"phase": "ready_for_review", **self.config["auto_base_candidate"]} if self.config.get("auto_base_candidate") else {}),
            "is_locked": is_remote_locked(),
            "is_synced": not self.config.get("gnss_config_pending", False)
        }
        return status

    def _antenna_status(self, final_status):
        rtcm_input_bps_val = globals().get('rtcm_input_bps', 0)
        numsv_val = globals().get("LAST_UBX_NUMSV")
        numsv_ts_val = float(globals().get("LAST_UBX_NUMSV_TS") or 0.0)
        sats_val = int(numsv_val) if (numsv_val is not None and time.time() - numsv_ts_val <= 15) else 0
        gsv_ts = float(globals().get("LAST_GSV_TS") or 0.0)
        
        rtcm_sats_ts = float(globals().get("LAST_RTCM_SATELLITES_TS") or 0.0)
        has_rtcm_sats = (time.time() - rtcm_sats_ts) <= 30

        antenna_state = "OK"
        antenna_message = None

        if final_status in ("online", "sleep", "streaming", "starting"):
            if rtcm_input_bps_val > 0 and rtcm_input_bps_val < 100:
                antenna_state = "FAULT"
                antenna_message = f"Tốc độ nhận RTCM từ máy thu cực thấp ({rtcm_input_bps_val} B/s). Cáp ăng-ten có thể bị lỏng hoặc đứt."
            elif sats_val == 0 and not has_rtcm_sats and (time.time() - gsv_ts > 30):
                antenna_state = "FAULT"
                antenna_message = "Không bắt được vệ tinh nào (0 Sats). Vui lòng kiểm tra cáp ăng-ten hoặc nguồn cấp."

        status["antenna_status"] = {
            "state": antenna_state,
            "message": antenna_message
        }

        # Throttled local logging for antenna fault
        if antenna_state == "FAULT":
            now_ts = time.time()
            last_log = getattr(self, "_last_antenna_fault_log_ts", 0)
            if now_ts - last_log > 300: # Every 5 minutes
                self.log("ERROR", f"[Antenna Quality] {antenna_message}")
                self._last_antenna_fault_log_ts = now_ts
        return {"state": antenna_state, "message": antenna_message}

    def _auto_base_datum_status(self):
        with DATUM_LOCK:
            datum_l0_deg = math.degrees(AUTO_BASE_DATUM.l0)
            datum_k0 = AUTO_BASE_DATUM.k0
            datum_1021 = AUTO_BASE_DATUM.rtcm1021_update
            datum_1023 = AUTO_BASE_DATUM.rtcm1023_update
            datum_1025 = AUTO_BASE_DATUM.rtcm1025_update
        status["auto_base_datum"] = {
            "rtcm1021_update": datum_1021,
            "rtcm1023_update": datum_1023,
            "rtcm1025_update": datum_1025,
            "l0_deg": datum_l0_deg,
            "k0": datum_k0
        }

    def _add_receiver_status(self, status):
        base_cfg = status.get("base_config") or {}
        coords = base_cfg.get("coords")
        if not coords:
            lat = base_cfg.get("lat")
            lon = base_cfg.get("lon")
            alt = base_cfg.get("alt")
            if lat is not None and lon is not None:
                coords = {"lat": lat, "lon": lon, "alt": alt if alt is not None else 0.0}
        if coords:
            status["base_coords"] = coords

        if hasattr(self, 'gnss_reader') and self.gnss_reader:
            status["gnss_stats"] = self.gnss_reader.get_statistics()
            status["parser_debug"] = status["gnss_stats"].get("parser_debug", {})

        now = time.time()
        numsv = globals().get("LAST_UBX_NUMSV")
        numsv_ts = float(globals().get("LAST_UBX_NUMSV_TS") or 0.0)
        if numsv is not None and now - numsv_ts <= 15:
            status["satellite_count"] = int(numsv)

        status.update(receiver_quality_status(now))
        return now

    def _nmea_health_status(self, now):
        nmea_health = {
            "type_counts": dict(globals().get("LAST_NMEA_TYPE_COUNTS") or {}),
            "gsv_snr_samples": int(globals().get("LAST_GSV_SNR_SAMPLES") or 0),
        }
        gst_hacc = globals().get("LAST_GST_HACC_M")
        gst_ts = float(globals().get("LAST_GST_TS") or 0.0)
        if now - gst_ts <= 15:
            semi_major = globals().get("LAST_GST_SEMI_MAJOR_M")
            semi_minor = globals().get("LAST_GST_SEMI_MINOR_M")
            if semi_major is not None:
                nmea_health["gst_semi_major_sigma"] = float(semi_major)
            if semi_minor is not None:
                nmea_health["gst_semi_minor_sigma"] = float(semi_minor)
            nmea_health["seconds_since_gst"] = round(now - gst_ts, 1)
            if gst_hacc is not None:
                nmea_health["hacc"] = float(gst_hacc)
                nmea_health["hacc_source"] = globals().get("LAST_GST_HACC_SOURCE") or "gst_lat_lon_sigma"
        gsv_avg_snr = globals().get("LAST_GSV_AVG_SNR")
        gsv_ts = float(globals().get("LAST_GSV_TS") or 0.0)
        if gsv_avg_snr is not None and now - gsv_ts <= 15:
            nmea_health["avg_snr"] = round(float(gsv_avg_snr), 1)
            nmea_health["seconds_since_gsv"] = round(now - gsv_ts, 1)
        return nmea_health

    def _ntrip_server_state(self, cfg, server_id, selected_mountpoint, ingress_bps, ntrip_stats):
        key = f"server{server_id}"
        switch = _get_server_stream_switches(cfg, server_id, apply_fail_open=True)
        if switch['locked']:
            self.ntrip_connection_status[key] = False
        connected = bool(self.ntrip_connection_status.get(key))
        mountpoint = self._normalize_mountpoint(cfg.get(f'mountpoint{server_id}'))
        can_forward = switch['can_push'] and (
            not switch['on_demand'] or control_plane_fail_open_event.is_set()
            or not selected_mountpoint or selected_mountpoint == mountpoint
        )
        fallback_bps = ingress_bps if connected and can_forward else 0
        bps = int(ntrip_stats.get(f'{key}_bps', fallback_bps)) if connected and can_forward else 0
        ntrip_stats[f'{key}_bps'] = bps
        return key, {
            **{name: bool(switch[name]) for name in ('enabled', 'on_demand', 'active', 'can_push', 'configured_can_push', 'fail_open_forced')},
            'effective_can_push': bool(switch['can_push']),
            'sleep': bool(switch['enabled'] and switch['on_demand'] and not switch['active']),
            'connected': connected,
            'bps': bps,
            'mountpoint': cfg.get(f'mountpoint{server_id}'),
        }

    def _add_ntrip_status(self, status):
        with self.stats_lock:
            if self.service_stats:
                ntrip_stats = self.service_stats.copy()
            else:
                ntrip_stats = {}

            # Always expose internal RTCM input bitrate for UI, even when forwarding sockets are closed.
            with rtcm_input_stats_lock:
                ingress_bps = rtcm_input_bps
                ntrip_stats["rtcm_input_bps"] = ingress_bps

            cfg = self.get_service_config() or {}
            selected_mountpoint = self._normalize_mountpoint(cfg.get('active_mountpoint'))
            stream_state = {"selected_mountpoint": cfg.get("active_mountpoint")}
            for sid in _get_ntrip_server_ids(cfg):
                key, server_state = self._ntrip_server_state(
                    cfg, sid, selected_mountpoint, ingress_bps, ntrip_stats
                )
                stream_state[key] = server_state
            status['ntrip_stats'] = ntrip_stats
            status['ntrip_connected'] = any(self.ntrip_connection_status.get(f'server{sid}') for sid in _get_ntrip_server_ids(cfg))
            status['ntrip_status'] = self.ntrip_connection_status.copy()
            status['ntrip_stream_state'] = stream_state

            for key, bps in self.service_stats.items():
                if 'bps' in key and bps > 0 and bps < 100:
                    status["warning"] = f"Low data rate detected on {key}: {bps} bps"

    def _serial_health_status(self):
        serial_health = {
            "port": self.detected_chip.get("port"),
            "baud": self.detected_chip.get("baud"),
            "seconds_since_data": None,
            "rtcm_inject_queue_depth": 0,
            "service_queue_depths": {},
        }
        if hasattr(self, 'gnss_reader') and self.gnss_reader:
            try:
                stats = self.gnss_reader.get_statistics()
                serial_health["seconds_since_data"] = stats.get("seconds_since_data")
                serial_health["last_data_time"] = int(stats.get("last_data_time") or 0)
                serial_health["buffer_overflows"] = stats.get("buffer_overflows", 0)
                serial_health["parse_errors"] = stats.get("parse_errors", 0)
                serial_health["rtcm_inject_queue_depth"] = self.gnss_reader.rtcm_inject_queue.qsize()
            except Exception:
                pass
        for worker in list(self.service_workers):
            try:
                serial_health["service_queue_depths"][worker.name] = worker.queue.qsize()
            except Exception:
                pass
        return serial_health

    def _update_savings_status(self):
        now_time = time.time()
        elapsed = now_time - getattr(self, 'last_savings_calc_time', now_time)
        self.last_savings_calc_time = now_time

        is_sleeping = not getattr(self, 'rtcm_stream_active', True)
        
        if is_sleeping:
            saved_bytes = globals().get('rtcm_input_bps', 0) * elapsed
            self.cumulative_saved_bytes += saved_bytes
            
            # Tính toán lượng điện (mWh) tiết kiệm
            # 0.7W tiết kiệm = 0.7 Joules/s = 0.7 / 3.6 mWh mỗi giây
            saved_mwh_per_sec = 0.7 / 3.6 
            self.cumulative_saved_mwh += saved_mwh_per_sec * elapsed

        return {
            "total_data_saved_bytes": int(self.cumulative_saved_bytes),
            "total_saved_mwh": round(self.cumulative_saved_mwh, 3),
            "telemetry_bps": getattr(self, 'telemetry_bps', 0),
            "is_sleeping": is_sleeping
        }

    def get_full_status(self):
        final_status = self._resolve_reported_status()
        status = self._build_base_status_payload(final_status)
        status["antenna_status"] = self._antenna_status(final_status)
        status["auto_base_datum"] = self._auto_base_datum_status()

        now = self._add_receiver_status(status)
        status["nmea_health"] = self._nmea_health_status(now)
        self._add_ntrip_status(status)

        status.update(_snapshot_agent_telemetry())
        status["control_plane_failover"] = self.control_plane_failover.snapshot()
        status["serial_health"] = self._serial_health_status()
        status["savings_stats"] = self._update_savings_status()
        
        return status
    
    def restart_services(self):
        global rtcm_stream_active_flag
        for worker in self.service_workers:
            worker.stop()
            worker.join(timeout=2)
        if hasattr(self, 'ntrip_client_worker') and self.ntrip_client_worker and self.ntrip_client_worker.is_alive():
            self.ntrip_client_worker.stop()
            self.ntrip_client_worker.join(timeout=2)
            
        self.service_workers.clear()
        self.service_stats.clear()
        self.ntrip_connection_status.clear()
        
        if not self.config.get("is_provisioned"):
            return
        
        cfg = self.config.get("services", {})
        self.rtcm_stream_active = _compute_any_server_can_push(cfg, apply_fail_open=True)
        rtcm_stream_active_flag = self.rtcm_stream_active
        selected_mountpoint = self._normalize_mountpoint(cfg.get('active_mountpoint'))

        # Enabled outputs maintain authentication even when RTCM is paused.
        for server_id in _get_ntrip_server_ids(cfg):
            if _get_server_stream_switches(cfg, server_id, apply_fail_open=True)['enabled']:
                self.service_workers.append(NTRIPServerWorker(server_id, cfg, self.log, self.service_stats, self.stats_lock, self.ntrip_connection_status))

        if selected_mountpoint and not self.service_workers and self.rtcm_stream_active:
            self.log("WARNING", f"[RTCM] active_mountpoint='{cfg.get('active_mountpoint')}' does not match any enabled server mountpoint")

        if bool(cfg.get('stream_on_demand', False)) and not self.rtcm_stream_active:
            self.log("INFO", "[RTCM] Stream is in standby mode (on-demand enabled, active=false).")
        
        is_base_station = bool(self.get_base_config())
        
        if cfg.get('rtcm_enabled') and not is_base_station:
            self.log("INFO", "[RTCM Client] Starting NTRIP client mode.")
            self.ntrip_client_worker = NTRIPClientWorker(cfg, self.log)
            self.ntrip_client_worker.start()
        elif is_base_station:
            self.log("INFO", "[RTCM Client] Disabled because agent is configured as a Base Station.")
        
        for worker in self.service_workers:
            worker.start()
# ==============================================================================
# === ASYNC FUNCTIONS                                                       ===
# ==============================================================================
async def send_status(agent: AgentManager, mqtt_client: mqtt.Client):
    global STATUS_SEQUENCE
    await initialization_complete.wait()
    
    status_payload = agent.get_full_status()
    STATUS_SEQUENCE += 1
    status_payload["status_boot_id"] = STATUS_BOOT_ID
    status_payload["status_sequence"] = STATUS_SEQUENCE
    status_payload["agent_timestamp"] = status_payload.get("timestamp", time.time())
    agent.control_plane_failover.record_probe(STATUS_SEQUENCE)
    
    try:
        system_info = get_cached_system_info()
        if system_info:
            status_payload['system_info'] = dict(system_info)
            logging.debug(f"System info collected: CPU={system_info.get('cpu', {}).get('usage_percent')}%, Temp={system_info.get('temperature', {}).get('celsius')} C")
    except Exception as e:
        logging.exception(f"Failed to collect system info: {e}")

    status_payload.setdefault("system_info", {}).update(receiver_base_system_info(agent))
    status_payload["system_info"]["device_lock"] = get_device_lock_status()
    status_payload["system_info"]["auto_base_review_supported"] = True

    # Đếm byte của JSON payload cho telemetry (Mạng nhẹ)
    json_payload = json.dumps(status_payload)
    telemetry_bytes = len(json_payload.encode('utf-8'))
    
    # Tính Bps trung bình (giả định interval là 5s, hoặc cập nhật dồn)
    now = time.time()
    if hasattr(agent, 'last_telemetry_time'):
        elapsed = now - agent.last_telemetry_time
        if elapsed > 0:
            agent.telemetry_bps = round(telemetry_bytes / elapsed, 2)
    else:
        agent.telemetry_bps = round(telemetry_bytes / 5.0, 2)
    agent.last_telemetry_time = now

    # Gửi qua WebSocket
    if active_websocket_connection:
        try:
            ws_message = {"type": "status_update", "payload": status_payload}
            await asyncio.wait_for(
                active_websocket_connection.send(json.dumps(ws_message)),
                timeout=2.0,
            )
        except Exception as e:
            _set_transport_state("websocket", False)
            logging.exception(f"Failed to send status via WebSocket: {e}")

    # Gửi qua MQTT
    if mqtt_client and mqtt_client.is_connected():
        try:
            topic = f"pi/devices/{MACHINE_SERIAL}/status"
            publish_info = mqtt_client.publish(topic, json_payload, qos=1, retain=True)
            if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT status publish returned rc={publish_info.rc}")
            await asyncio.to_thread(publish_info.wait_for_publish, timeout=3.0)
            if not publish_info.is_published():
                raise TimeoutError("MQTT status publish not acknowledged within 3s")
            now_monotonic = time.monotonic()
            if now_monotonic - getattr(agent, '_last_status_publish_log', float('-inf')) >= 60:
                logging.info(
                    "MQTT status acknowledged: topic=%s status=%s sequence=%s boot=%s broker=%s",
                    topic, status_payload.get('status'), status_payload['status_sequence'],
                    STATUS_BOOT_ID, transport_status.get('mqtt_broker'),
                )
                agent._last_status_publish_log = now_monotonic
        except Exception as e:
            logging.warning(f"MQTT publish failed: {e}")

# ==============================================================================
# === ASYNC FUNCTIONS - PROCESS COMMAND (UPDATED)                           ===
# ==============================================================================
def _validate_simple_command_payload(command, payload):
    if command == "PROVISION_DEVICE" and not str(payload.get("name") or "").strip():
        return "missing payload.name"
    if command == "DEPLOY_LICENSE" and not str(payload.get("license_key") or "").strip():
        return "missing payload.license_key"
    if command == "DEPLOY_SERVICE_CONFIG" and not payload:
        return "missing service config payload"
    if command == "SET_RTCM_STREAM_ACTIVE" and "active" not in payload:
        return "missing payload.active"
    if command == "APPLY_VN2000_PROVINCE" and not str(payload.get("province_code") or "").strip():
        return "missing payload.province_code"
    return None


def _validate_auto_base_payload(payload):
    if 'enable_itrf_vn2000_transform' in payload and not isinstance(payload['enable_itrf_vn2000_transform'], bool):
        return "enable_itrf_vn2000_transform must be a boolean"
    required_fields = ("ip", "port", "user", "password", "mountpoint")
    if any(not str(payload.get(key) or "").strip() for key in required_fields if key != "password"):
        return "missing CORS connection fields"
    if not str(payload.get("password") or ""):
        return "missing CORS connection fields"
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError):
        return "invalid CORS port"
    if not 1 <= port <= 65535:
        return "invalid CORS port"
    if any(char in "\r\n" for char in "".join(str(payload.get(key) or "") for key in required_fields)):
        return "invalid CORS connection fields"
    return None


def _validate_raw_commands_payload(payload, agent):
    commands_b64 = payload.get("commands_b64")
    if not isinstance(commands_b64, list) or not commands_b64:
        return "missing payload.commands_b64"
    if len(commands_b64) > 200:
        return "too many raw commands"
    for item in commands_b64:
        if not isinstance(item, str) or len(item) > 4096:
            return "invalid raw command item"
        try:
            base64.b64decode(item, validate=True)
        except Exception:
            return "raw command is not valid base64"
    if not agent.detected_chip.get("port"):
        return "no detected serial port"
    return None


def _validate_command_payload(command: str, payload: dict, data: dict, agent: AgentManager, gnss_reader: GNSSReader) -> tuple[bool, str]:
    if not command:
        return False, "missing command"
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return False, "payload must be an object"

    error = _validate_simple_command_payload(command, payload)
    if error:
        return False, error
    if command == "TRIGGER_AUTO_BASE":
        error = _validate_auto_base_payload(payload)
        if error:
            return False, error
    if command == "EXECUTE_RAW_COMMANDS":
        error = _validate_raw_commands_payload(payload, agent)
        if error:
            return False, error

    known_commands = {
        "LOCK_DEVICE",
        "UNLOCK_DEVICE",
        "PROVISION_DEVICE",
        "DEPLOY_LICENSE",
        "TRIGGER_AUTO_BASE",
        "TRIGGER_BASE_REFERENCE_CHECK",
        "STOP_AUTO_BASE",
        "APPLY_VN2000_PROVINCE",
        "EXECUTE_RAW_COMMANDS",
        "DEPLOY_SERVICE_CONFIG",
        "SET_RTCM_STREAM_ACTIVE",
        "DELETE_DEVICE",
        "CHECK_BASE_STATUS",
        "VERIFY_GNSS_CONFIG",
        "REBOOT_DEVICE",
    }
    if command not in known_commands:
        return False, f"unsupported command: {command}"
    return True, ""

async def _handle_command_provision_device(context):
    global current_state
    payload = context['payload']
    agent = context['agent']
    if agent.update_name(payload.get("name")):
        current_state = "ONLINE" if license_is_valid() else "AWAITING_LICENSE"
        if current_state == "ONLINE":
            agent.restart_services()


async def _handle_command_deploy_license(context):
    global current_state
    command = context['command']
    source = context['source']
    payload = context['payload']
    command_id = context['command_id']
    agent = context['agent']
    mqtt_client = context['mqtt_client']
    if payload.get("license_key"):
        license_key = str(payload["license_key"]).strip()
        if not is_valid_license_key(license_key, MACHINE_SERIAL):
            raise ValueError("Invalid device token format")
        await asyncio.to_thread(_write_license_key, license_key)
        current_state = "REBOOTING"
        _record_command_result(command, source, "success", "license deployed; rebooting", command_id)
        await send_status(agent, mqtt_client)
        await asyncio.sleep(2)
        remove_lock_file()
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])


async def _handle_command_trigger_auto_base(context):
    global active_auto_base_task
    payload = context['payload']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    logging.info("Received TRIGGER_AUTO_BASE command. Configuring and starting state machine...")
    await _stop_auto_base(agent, gnss_reader)
    auto_setup_metadata = payload
    raw_mp = str(auto_setup_metadata.get("mountpoint") or "").strip()

    agent.config['auto_base_setup'] = {
        "enabled": True,
        "ip": str(auto_setup_metadata.get("ip") or "").strip(),
        "port": int(auto_setup_metadata.get("port")),
        "user": str(auto_setup_metadata.get("user") or "").strip(),
        "password": str(auto_setup_metadata.get("password") or ""),
        "mountpoint": raw_mp,
        "timeout": auto_setup_metadata.get("timeout", 3600),
        "samples": auto_setup_metadata.get("samples", 60),
        "sensor_type": auto_setup_metadata.get("sensor_type"),
        "review_only": True,
        "enable_itrf_vn2000_transform": auto_setup_metadata.get("enable_itrf_vn2000_transform", True),
        "l0_deg": auto_setup_metadata.get("l0_deg"),
        "k0": auto_setup_metadata.get("k0"),
        **{key: auto_setup_metadata.get(key, 0.0) for key in ("resN", "resE", "resHS")}
    }
    agent.save_config()
    _set_auto_base_progress("queued", step=0, total_steps=4)
    await send_status(agent, mqtt_client)

    # Start the state machine as a background task
    active_auto_base_task = asyncio.create_task(auto_base_state_machine(agent, gnss_reader, mqtt_client))


def _parse_reference_coordinates(coords, missing_error, range_error):
    try:
        parsed = {
            "lat": float(coords["lat"]),
            "lon": float(coords["lon"]),
            "alt": float(coords["alt"]),
        }
    except (KeyError, TypeError, ValueError):
        raise ValueError(missing_error)
    if not (-90.0 <= parsed["lat"] <= 90.0 and -180.0 <= parsed["lon"] <= 180.0):
        raise ValueError(range_error)
    return parsed


def _build_reference_check_config(payload, reference, restore_coords, original_base_config):
    config = {
        "ip": str(payload.get("ip") or "").strip(),
        "port": int(payload.get("port") or 0),
        "user": str(payload.get("user") or ""),
        "password": str(payload.get("password") or ""),
        "mountpoint": str(payload.get("mountpoint") or "").strip(),
        "timeout": int(payload.get("timeout") or 300),
        "samples": int(payload.get("samples") or 60),
        "reference_coords": reference,
        "restore_coords": restore_coords,
        "original_base_config": original_base_config,
    }
    if not all((config["ip"], config["mountpoint"], config["user"], config["password"])):
        raise ValueError("NTRIP host, credentials, and mountpoint are required")
    return config


async def _handle_command_trigger_base_reference_check(context):
    global active_auto_base_task
    payload = context['payload']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    logging.info("Received TRIGGER_BASE_REFERENCE_CHECK command.")
    reference = _parse_reference_coordinates(
        payload.get("reference_coords") or {},
        "reference_coords must contain valid lat, lon, alt",
        "reference_coords is out of range",
    )
    original_base_config = copy.deepcopy(agent.get_base_config() or {})
    original_coords = original_base_config.get("coords") or original_base_config
    restore_coords = _parse_reference_coordinates(
        original_coords,
        "stored base coordinate is missing; refusing reference check",
        "stored base coordinate is out of range",
    )

    if active_auto_base_task and not active_auto_base_task.done():
        raise RuntimeError("an automatic base task is already running")
    reference_check_cfg = _build_reference_check_config(
        payload, reference, restore_coords, original_base_config
    )
    agent.config["pending_base_restore"] = {
        "coords": restore_coords,
        "base_config": original_base_config,
    }
    agent.save_config()
    _set_auto_base_progress("reference_check_queued", step=0, total_steps=4)
    await send_status(agent, mqtt_client)
    active_auto_base_task = asyncio.create_task(
        base_reference_check_state_machine(agent, gnss_reader, mqtt_client, reference_check_cfg)
    )


async def _handle_command_stop_auto_base(context):
    global current_state
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    logging.info("Received STOP_AUTO_BASE command")
    await _stop_auto_base(agent, gnss_reader)
    current_state = "ONLINE"
    _set_auto_base_progress("stopped", step=0, total_steps=4, reason="Stopped by user")
    await send_status(agent, mqtt_client)


async def _handle_command_apply_vn2000_province(context):
    if context["command"] == "APPLY_VN2000_PROVINCE":
        raise ValueError('Province overrides no longer write the receiver. Review Auto Base results and apply Fixed LLA.')


async def _handle_command_execute_raw_commands(context):
    data = context['data']
    payload = context['payload']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    _remember_output_mode_from_original_config(data)
    original_config = data.get("original_config") or {}
    configuring_base = original_config.get("mode") == "BASE"
    await _prepare_raw_command_execution(agent, gnss_reader, configuring_base)
    commands_b64 = payload.get("commands_b64", [])
    port = agent.detected_chip.get("port")
    await _execute_raw_gnss_commands(
        commands_b64, port, payload, data, original_config, configuring_base,
        agent, gnss_reader, mqtt_client,
    )

async def _prepare_raw_command_execution(agent, gnss_reader, configuring_base):
    if not configuring_base:
        return
    agent.config['gnss_config_pending'] = True
    await _stop_auto_base(agent, gnss_reader)
    agent.save_config()
    globals()['AUTO_BASE_PROGRESS'] = {}
    _mark_gnss_data_stale()


def _verify_raw_command_receiver(ser, profile):
    if not profile.get('modern'):
        return
    logging.info('[GNSS][CONFIG] firmware=%s interface=VALSET layers=RAM/BBR/Flash', profile.get('firmware'))
    actual_info = _ubx_version_payload(_query_ubx_serial(ser, 0x0a, 4))
    if actual_info.get('firmware') != profile.get('firmware') or actual_info.get('model') != 'ZED-F9P':
        raise RuntimeError('GNSS firmware changed; refresh receiver information before applying')


async def _send_raw_gnss_command(ser, cmd_b64, profile, expected_values):
    decoded_cmd = base64.b64decode(cmd_b64)
    delay_seconds = _delay_marker_seconds(decoded_cmd)
    if delay_seconds is not None:
        await asyncio.sleep(delay_seconds)
        return None
    expected_tmode = decoded_cmd if decoded_cmd[:4] == b"\xb5\x62\x06\x71" and len(decoded_cmd) == 48 else None
    if profile.get('modern'):
        expected_values.update(_ubx_valset_values(decoded_cmd))
    logging.info(f"  -> Sending: {decoded_cmd.decode('ascii', errors='ignore').strip()}")
    ser.write(decoded_cmd)
    ser.flush()
    await asyncio.sleep(0.5)
    return expected_tmode


def _verify_raw_gnss_commands(ser, profile, expected_values, expected_tmode):
    if profile.get('modern'):
        _verify_ublox_values(ser, expected_values)
    if expected_tmode is not None:
        _verify_ublox_tmode(ser, expected_tmode)


async def _write_raw_gnss_commands(ser, commands_b64, profile):
    _verify_raw_command_receiver(ser, profile)
    expected_tmode = None
    expected_values = {}
    for cmd_b64 in commands_b64:
        command_tmode = await _send_raw_gnss_command(ser, cmd_b64, profile, expected_values)
        if command_tmode is not None:
            expected_tmode = command_tmode
    _verify_raw_gnss_commands(ser, profile, expected_values, expected_tmode)


def _apply_raw_command_base_config(agent, original_config):
    agent.config['base_config'] = copy.deepcopy(original_config['params'])
    agent.config.pop('auto_base_candidate', None)
    agent.config['gnss_config_pending'] = False
    agent.receiver_base_readback = {}
    agent.receiver_base_readback_at = None
    if agent.config['base_config'].get('base_setup_method') == 'SURVEY_IN':
        agent.survey_in_started_at = time.monotonic()
        agent.config.pop('survey_in_result', None)
    else:
        agent.survey_in_started_at = None
    agent.save_config()


async def _execute_raw_gnss_commands(commands_b64, port, payload, data, original_config,
                                     configuring_base, agent, gnss_reader, mqtt_client):
    global current_state
    if not commands_b64 or not port:
        return
    if gnss_reader:
        gnss_reader.pause()
        await asyncio.sleep(1.0)
    try:
        baud = _runtime_serial_baud(agent, gnss_reader)
        logging.info(f"Opening GNSS command port {port} @ {baud} baud")
        with serial_port_lock, serial.Serial(port, baud, timeout=2) as ser:
            _write_raw_gnss_commands(ser, commands_b64, payload.get('receiver_profile') or {})
        if configuring_base:
            _apply_raw_command_base_config(agent, original_config)
        current_state = "ONLINE"
        logging.info("All base station commands executed successfully")
        if data.get("original_config", {}).get("mode") == "BASE":
            agent.restart_services()
    except Exception as error:
        logging.exception(f"Raw command execution error: {error}")
        raise
    finally:
        if gnss_reader:
            await asyncio.sleep(2.0)  # Wait for chip to stabilize
            gnss_reader.resume()
        current_state = "ONLINE"
        logging.info("GNSS command port released; reader resumed")
        await send_status(agent, mqtt_client)


async def _handle_command_deploy_service_config(context):
    global current_state
    payload = context['payload']
    agent = context['agent']
    mqtt_client = context['mqtt_client']
    agent.update_service_config(payload)
    agent.restart_services()
    current_state = "ONLINE"
    await send_status(agent, mqtt_client)


def _resolve_rtcm_server_id(raw_server_id, agent):
    try:
        parsed_server_id = int(raw_server_id)
    except (TypeError, ValueError):
        return None
    if parsed_server_id in _get_ntrip_server_ids(agent.get_service_config() or {}):
        return parsed_server_id
    return None


def _log_rtcm_stream_status(active, server_id, address, mountpoint):
    if active:
        if server_id:
            logging.info(f"[RTCM] Stream activated for server {server_id} by control plane")
        elif address:
            logging.info(f"[RTCM] Stream activated for address '{address}' by control plane")
        elif mountpoint:
            logging.info(f"[RTCM] Stream activated for mountpoint '{mountpoint}' by control plane")
        else:
            logging.info("[RTCM] Stream activated by control plane - forwarding to NTRIP caster enabled")
        return
    if server_id:
        logging.info(f"[RTCM] Stream set to standby for server {server_id} by control plane")
    else:
        logging.info("[RTCM] Stream set to standby by control plane - forwarding to NTRIP caster disabled")


async def _handle_command_set_rtcm_stream_active(context):
    global current_state, rtcm_stream_active_flag
    payload = context['payload']
    agent = context['agent']
    mqtt_client = context['mqtt_client']
    active = bool(payload.get("active", False))
    mountpoint = payload.get("mountpoint")
    address = (
        payload.get("address")
        or payload.get("caster_host")
        or payload.get("ip")
        or payload.get("host")
    )
    raw_server_id = payload.get(
        "server_id",
        payload.get("server_idx", payload.get("server")),
    )
    server_id = _resolve_rtcm_server_id(raw_server_id, agent) if raw_server_id is not None else None

    agent.set_rtcm_stream_active(active, mountpoint=mountpoint, server_id=server_id, address=address)
    rtcm_stream_active_flag = bool(agent.rtcm_stream_active)  # Update global dispatch gate immediately

    _log_rtcm_stream_status(active, server_id, address, mountpoint)
    current_state = "ONLINE"
    await send_status(agent, mqtt_client)


async def _handle_command_reboot_device(context):
    global current_state
    command = context['command']
    source = context['source']
    command_id = context['command_id']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    current_state = "REBOOTING"
    _record_command_result(command, source, "success", "device reboot requested", command_id)
    await send_status(agent, mqtt_client)
    await asyncio.sleep(1) # Chờ để MQTT kịp gửi bản tin

    if agent.nmea_publisher:
        agent.nmea_publisher.stop()
    agent.restart_services()
    if gnss_reader and gnss_reader.is_alive():
        gnss_reader.stop()
        gnss_reader.join(timeout=2)
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    await asyncio.sleep(2)
    remove_lock_file()

    # Restart tiến trình (process) thay vì khởi động lại OS
    logging.info("[REBOOT] Restarting Agent process...")
    sys.exit(0)


async def _handle_command_delete_device(context):
    global current_state
    command = context['command']
    source = context['source']
    command_id = context['command_id']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    mqtt_client = context['mqtt_client']
    current_state = "REBOOTING_FOR_RESET"
    _record_command_result(command, source, "success", "device reset requested", command_id)
    await send_status(agent, mqtt_client)
    await asyncio.sleep(1)

    if agent.nmea_publisher:
        agent.nmea_publisher.stop()
    agent.restart_services()
    if gnss_reader and gnss_reader.is_alive():
        gnss_reader.stop()
        gnss_reader.join(timeout=2)
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    for path in [CONFIG_PATH, LICENSE_PATH]:
        if os.path.exists(path):
            os.remove(path)

    await asyncio.sleep(2)
    remove_lock_file()

    logging.info("[REBOOT_FOR_RESET] Restarting Agent process...")
    sys.exit(0)


async def _handle_command_verify_gnss_config(context):
    payload = context['payload']
    command_id = context['command_id']
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    if active_auto_base_task and not active_auto_base_task.done():
        raise RuntimeError('Base workflow is running; wait before verifying configuration')
    await verify_receiver_configuration(agent, gnss_reader, payload, command_id)


async def _handle_command_check_base_status(context):
    agent = context['agent']
    gnss_reader = context['gnss_reader']
    await refresh_receiver_base_status(agent, gnss_reader)


_COMMAND_HANDLERS = {
    'PROVISION_DEVICE': _handle_command_provision_device,
    'DEPLOY_LICENSE': _handle_command_deploy_license,
    'TRIGGER_AUTO_BASE': _handle_command_trigger_auto_base,
    'TRIGGER_BASE_REFERENCE_CHECK': _handle_command_trigger_base_reference_check,
    'STOP_AUTO_BASE': _handle_command_stop_auto_base,
    'APPLY_VN2000_PROVINCE': _handle_command_apply_vn2000_province,
    'EXECUTE_RAW_COMMANDS': _handle_command_execute_raw_commands,
    'DEPLOY_SERVICE_CONFIG': _handle_command_deploy_service_config,
    'SET_RTCM_STREAM_ACTIVE': _handle_command_set_rtcm_stream_active,
    'REBOOT_DEVICE': _handle_command_reboot_device,
    'DELETE_DEVICE': _handle_command_delete_device,
    'VERIFY_GNSS_CONFIG': _handle_command_verify_gnss_config,
    'CHECK_BASE_STATUS': _handle_command_check_base_status,
}


async def process_command(source: str, data: dict, agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    async with command_execution_lock:
        await _process_command(source, data, agent, gnss_reader, mqtt_client)

def _write_license_key(license_key: str) -> None:
    fd = os.open(LICENSE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as license_file:
        license_file.write(license_key)


async def _extract_command_payload(data, source, agent, mqtt_client):
    if not isinstance(data, dict):
        _record_command_result("UNKNOWN", source, "rejected", "command envelope must be an object")
        logging.warning("Rejected malformed command envelope from %s", source)
        return None
    command = data.get("command")
    payload = data.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        _record_command_result(str(command or "UNKNOWN"), source, "rejected", "command payload must be an object")
        await send_status(agent, mqtt_client)
        return None
    return command, payload, str(data.get("command_id") or "")


async def _accept_command_envelope(command, source, command_id, data, agent, mqtt_client):
    now = int(time.time())
    try:
        issued_at = int(data.get("issued_at"))
        expires_at = int(data.get("expires_at"))
    except (TypeError, ValueError):
        issued_at = expires_at = 0
    id_is_valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{16,100}", command_id))
    if (not id_is_valid or issued_at > now + 60 or issued_at < now - 300
            or expires_at < now or expires_at > issued_at + 300):
        _record_command_result(command, source, "rejected", "missing or expired command envelope", command_id or None)
        logging.warning(
            "Rejected command freshness from %s: id_valid=%s issued_at=%s expires_at=%s now=%s",
            source.upper(), id_is_valid, issued_at, expires_at, now,
        )
        await send_status(agent, mqtt_client)
        return False
    if command_id in seen_command_ids:
        _record_command_result(command, source, "rejected", "replayed command id", command_id)
        await send_status(agent, mqtt_client)
        return False
    seen_command_ids[command_id] = expires_at
    while len(seen_command_ids) > 2048:
        seen_command_ids.popitem(last=False)
    for old_id, old_expiry in list(seen_command_ids.items()):
        if old_expiry < now:
            seen_command_ids.pop(old_id, None)
    return True


async def _validate_received_command(command, source, payload, data, command_id, agent, gnss_reader, mqtt_client):
    is_valid, validation_error = _validate_command_payload(command, payload, data, agent, gnss_reader)
    if is_valid:
        return True
    _record_command_result(command, source, "rejected", validation_error, command_id)
    logging.warning(f"Rejected command '{command}' from {source.upper()}: {validation_error}")
    await send_status(agent, mqtt_client)
    return False


async def _handle_device_lock_commands(command, source, payload, command_id, agent, mqtt_client):
    if command == "LOCK_DEVICE":
        if create_remote_lock(payload.get("locked_servers") if "locked_servers" in payload else None):
            # Scoped locks do not restart services or interrupt the other host.
            if not is_remote_locked() and current_state == "LOCKED":
                remote_unlock_event.set()
            logging.warning("DEVICE LOCK UPDATED: %s", get_device_lock_status())
            _record_command_result(command, source, "success", "device locked", command_id)
        else:
            logging.error("!!! Failed to create remote lock file.")
            _record_command_result(command, source, "error", "failed to create remote lock", command_id)
        await send_status(agent, mqtt_client)
        return True
    if command != "UNLOCK_DEVICE":
        return False
    unlocked = remove_remote_lock()
    if unlocked:
        logging.info(" DEVICE UNLOCKED")
        _record_command_result(command, source, "success", "device unlocked", command_id)
    else:
        logging.error("!!! Failed to remove remote lock file.")
        _record_command_result(command, source, "error", "failed to remove remote lock", command_id)
    await send_status(agent, mqtt_client)
    if unlocked and current_state == "LOCKED":
        remote_unlock_event.set()
    return True


async def _reject_when_device_locked(command, source, command_id, agent, mqtt_client):
    if not is_remote_locked():
        return False
    logging.warning(f"Command '{command}' REJECTED - Device is locked")
    _record_command_result(command, source, "rejected", "device is locked", command_id)
    await send_status(agent, mqtt_client)
    return True


async def _start_command_execution(command, source, command_id, agent, mqtt_client):
    global current_state
    previous_state = current_state
    is_long_running = command in {
        "EXECUTE_RAW_COMMANDS", "DELETE_DEVICE", "PROVISION_DEVICE", "DEPLOY_LICENSE",
        "TRIGGER_AUTO_BASE", "TRIGGER_BASE_REFERENCE_CHECK", "APPLY_VN2000_PROVINCE",
    }
    if is_long_running:
        if not create_lock_file():
            logging.warning(f"Rejected command '{command}', device is busy.")
            _record_command_result(command, source, "rejected", "device is busy", command_id)
            await send_status(agent, mqtt_client)
            return previous_state, is_long_running, False
        current_state = "CONFIGURING"
        await send_status(agent, mqtt_client)
    return previous_state, is_long_running, True


async def _process_command(source: str, data: dict, agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global current_state
    parsed = await _extract_command_payload(data, source, agent, mqtt_client)
    if parsed is None:
        return
    command, payload, command_id = parsed
    if not await _accept_command_envelope(command, source, command_id, data, agent, mqtt_client):
        return
    logging.info(f"Received command '{command}' from {source.upper()}")
    if not await _validate_received_command(
        command, source, payload, data, command_id, agent, gnss_reader, mqtt_client
    ):
        return
    _record_command_result(command, source, "running", "", command_id)

    if await _handle_device_lock_commands(command, source, payload, command_id, agent, mqtt_client):
        return
    if await _reject_when_device_locked(command, source, command_id, agent, mqtt_client):
        return
    previous_state, is_long_running, started = await _start_command_execution(
        command, source, command_id, agent, mqtt_client
    )
    if not started:
        return

    try:
        handler = _COMMAND_HANDLERS.get(command)
        if handler is not None:
            context = {
                "command": command, "source": source, "data": data, "payload": payload,
                "command_id": command_id, "agent": agent, "gnss_reader": gnss_reader,
                "mqtt_client": mqtt_client,
            }
            await handler(context)
        _record_command_result(command, source, "success", "", command_id)
    except Exception as error:
        _record_command_result(command, source, "error", str(error), command_id)
        logging.exception(f"Command '{command}' failed: {error}", exc_info=True)
    finally:
        if current_state == "CONFIGURING":
            current_state = previous_state
        if command != "DELETE_DEVICE":
            await send_status(agent, mqtt_client)
        if is_long_running:
            remove_lock_file()

def setup_mqtt_client(loop: asyncio.AbstractEventLoop, agent: AgentManager, gnss_reader: GNSSReader):
    mqtt_cfg = _get_mqtt_runtime_config(agent)
    if hasattr(mqtt, 'CallbackAPIVersion'):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}")
    else:
        client = mqtt.Client(client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}")
    client.user_data_set({"agent": agent, "gnss_reader": gnss_reader})
    
    command_topics = [
        f"pi/devices/{MACHINE_SERIAL}/command",
        f"pi/devices/{MACHINE_SERIAL}/commands",
        f"pi/device/{MACHINE_SERIAL}/command",
    ]
    control_ack_topic = f"pi/devices/{MACHINE_SERIAL}/control_ack"

    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            _set_transport_state("mqtt", True)
            logging.info("Connected to MQTT Broker.")
            for topic in command_topics:
                result, mid = c.subscribe(topic, qos=1)
                logging.info(f"MQTT subscribe topic='{topic}' result={result} mid={mid}")
            result, mid = c.subscribe(control_ack_topic, qos=1)
            logging.info(f"MQTT control ACK subscription result={result} mid={mid}")
            asyncio.run_coroutine_threadsafe(send_status(userdata["agent"], c), loop)
        else:
            _telemetry_inc("reconnect", "mqtt")
            _set_transport_state("mqtt", False)
            logging.error(f"!!! MQTT connection failed, code: {rc}")

    def on_disconnect(c, userdata, *args):
        rc = args[-2] if len(args) >= 2 else (args[0] if args else 0)
        _set_transport_state("mqtt", False)
        if rc != 0:
            _telemetry_inc("reconnect", "mqtt")
            logging.warning(f"MQTT disconnected unexpectedly (rc={rc}).")

    def on_subscribe(c, userdata, mid, granted_qos, properties=None):
        logging.info(f"MQTT subscribe acknowledged mid={mid} qos={granted_qos}")
    
    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            if msg.topic == control_ack_topic and data.get("type") == "control_plane_ack":
                userdata["agent"].control_plane_failover.record_ack(
                    "mqtt",
                    data,
                    retained=bool(getattr(msg, "retain", False)),
                )
                return
            if msg.topic not in command_topics or bool(getattr(msg, "retain", False)):
                return
            logging.info(f"MQTT command received topic='{msg.topic}' bytes={len(msg.payload)}")
            future = asyncio.run_coroutine_threadsafe(
                process_command('mqtt', data, userdata["agent"], userdata["gnss_reader"], c),
                loop
            )
            def on_done(f):
                try: 
                    f.result()
                except Exception as e:
                    logging.exception(f"Process command failed: {e}", exc_info=True)
            future.add_done_callback(on_done)
        except Exception as e:
            logging.exception(f"Error processing MQTT message: {e}")
    
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=5, max_delay=120)

    if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
    # ==================================
    
    last_will = json.dumps({
        "serial": MACHINE_SERIAL,
        "status": "offline",
        "timestamp": int(time.time()),
        "agent_timestamp": int(time.time()),
        "status_boot_id": STATUS_BOOT_ID,
        "status_event": "lwt"
    })
    client.will_set(f"pi/devices/{MACHINE_SERIAL}/status", payload=last_will, qos=1, retain=True)
    
    with telemetry_lock:
        transport_status["mqtt_broker"] = mqtt_cfg["broker"]
    client.connect_async(mqtt_cfg["broker"], mqtt_cfg["port"], 30)
    client.loop_start()
    return client

async def websocket_task(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global active_websocket_connection
    
    # Read Token from license.key
    auth_token = ""
    if os.path.exists(LICENSE_PATH):
        try:
            auth_token = (await asyncio.to_thread(Path(LICENSE_PATH).read_text, encoding="utf-8")).strip()
        except Exception as e:
            logging.exception(f"Error reading license key: {e}")
            
    if BACKEND_WS_SCHEME not in {"ws", "wss"}:
        raise ValueError("BACKEND_WS_SCHEME must be ws or wss")
    if BACKEND_WS_SCHEME == "ws" and not ALLOW_INSECURE_AGENT_WS:
        raise ValueError("Plaintext device WebSocket requires ALLOW_INSECURE_AGENT_WS=true")
    safe_uri = (
        f"{BACKEND_WS_SCHEME}://{BACKEND_WS_HOST}:{BACKEND_WS_PORT}"
        f"/ws/pi/{urllib.parse.quote(MACHINE_SERIAL, safe='')}"
    )
    ws_uri = f"{safe_uri}?token={urllib.parse.quote(auth_token, safe='')}" if auth_token else safe_uri
    
    while True:
        try:
            async with websockets.connect(
                ws_uri,
                ping_interval=30,
                ping_timeout=60,
                close_timeout=10,
                open_timeout=30  
            ) as websocket:
                logging.info(f"Secondary channel (WebSocket) connected: {safe_uri}")
                active_websocket_connection = websocket
                _set_transport_state("websocket", True)
                
                await send_status(agent, mqtt_client)
                
                async for message in websocket:
                    data = json.loads(message)
                    if data.get("type") == "control_plane_ack":
                        agent.control_plane_failover.record_ack("websocket", data)
                        continue
                    await process_command('websocket', data, agent, gnss_reader, mqtt_client)

        except asyncio.CancelledError:
            logging.info("websocket_task cancelled")
            raise
        except asyncio.TimeoutError:
            _telemetry_inc("reconnect", "websocket")
            logging.warning("WebSocket connection timeout. Retrying in 10s...")
        except websockets.exceptions.WebSocketException as e:
            _telemetry_inc("reconnect", "websocket")
            logging.warning("WebSocket error (%s). Retrying in 10s...", type(e).__name__)
        except Exception as e:
            _telemetry_inc("reconnect", "websocket")
            if type(e).__name__ == "SSLCertVerificationError":
                logging.warning(
                    "WebSocket certificate verification failed: host=%s code=%s reason=%s",
                    globals().get('BACKEND_WS_HOST', BACKEND_HOST), getattr(e, 'verify_code', None),
                    str(getattr(e, 'verify_message', 'unknown')).encode('ascii', 'backslashreplace').decode('ascii'),
                )
            if mqtt_client and mqtt_client.is_connected():
                logging.info("Secondary WebSocket failed (%s). Main MQTT OK; retrying in 10s.", type(e).__name__)
            else:
                logging.warning("Both MQTT and WebSocket failed (%s); retrying in 10s.", type(e).__name__)
        finally:
            active_websocket_connection = None
            _set_transport_state("websocket", False)
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise

async def memory_guard_task():
    """Background task to periodically clean up and monitor RAM usage."""
    logging.info("[MEM GUARD] Memory guard task started (Interval: 30m)")
    while True:
        try:
            # 1. Force Python garbage collection
            collected = gc.collect()
            
            # 2. Check current RAM usage
            process = psutil.Process(os.getpid())
            ram_mb = process.memory_info().rss / (1024 * 1024)
            
            if ram_mb > 200:
                logging.warning(f"[MEM GUARD] High RAM usage detected: {ram_mb:.2f} MB. GC collected {collected} objects.")
            else:
                logging.info(f"[MEM GUARD] Current RAM usage: {ram_mb:.2f} MB. GC collected {collected} objects.")
                
        except Exception as e:
            logging.exception(f"[MEM GUARD] Error during cleanup: {e}")
            
        await asyncio.sleep(1800) # Every 30 minutes


async def status_publisher_task(agent: AgentManager, mqtt_client: mqtt.Client):
    last_base_poll = -30.0
    try:
        while True:
            await asyncio.sleep(max(1, STATUS_PUBLISH_INTERVAL_SECONDS))
            reader = getattr(agent, 'gnss_reader', None)
            if (reader and agent.detected_chip.get('type') in ('Ublox', 'Unicorecomm')
                    and not is_remote_locked() and not command_execution_lock.locked()
                    and not (active_auto_base_task and not active_auto_base_task.done())
                    and time.monotonic() - last_base_poll >= 30):
                async with command_execution_lock:
                    last_base_poll = time.monotonic()
                    try:
                        await refresh_receiver_base_status(agent, reader)
                    except Exception as error:
                        logging.warning("[GNSS][READBACK] %s", error)
                    try:
                        await refresh_receiver_quality(agent, reader)
                    except Exception as error:
                        logging.warning("[GNSS][QUALITY] %s", error)
            await send_status(agent, mqtt_client)
    except asyncio.CancelledError:
        logging.info("status_publisher_task cancelled")
        raise


async def control_plane_watchdog_task(agent: AgentManager):
    """Fail open only when neither backend transport returns a fresh ACK."""
    try:
        while True:
            await asyncio.sleep(CONTROL_PLANE_CHECK_INTERVAL_SECONDS)
            transition = agent.control_plane_failover.evaluate()
            if transition.changed:
                agent.apply_control_plane_failover(transition.active, transition.reason)
    except asyncio.CancelledError:
        logging.info("control_plane_watchdog_task cancelled")
        raise

# ==============================================================================
# === AUTO BASE SETUP STATE MACHINE                                         ===
# ==============================================================================
async def _stop_auto_base(agent, gnss_reader):
    global active_auto_base_task, active_auto_base_client, rtcm_stream_active_flag
    client = active_auto_base_client
    task = active_auto_base_task
    agent.config['auto_base_setup'] = {"enabled": False}
    agent.save_config()
    if client:
        client.stop()
    if task:
        if not task.done():
            task.cancel()
        # Reference-check cancellation may restore a previous base in its finally
        # block. It must finish before a new configuration touches the serial port.
        await asyncio.gather(task, return_exceptions=True)
    if client and client.is_alive():
        await asyncio.to_thread(client.join, 6)
    active_auto_base_task = None
    active_auto_base_client = None
    _cleanup_inject_queue(gnss_reader)
    rtcm_stream_active_flag = _compute_any_server_can_push(
        agent.get_service_config() or {}, apply_fail_open=True,
    )
    if gnss_reader:
        gnss_reader.resume()


def _ubx_packet(msg_class, msg_id, payload=b''):
    body = bytes([msg_class, msg_id]) + len(payload).to_bytes(2, 'little') + payload
    ck_a = ck_b = 0
    for byte in body:
        ck_a = (ck_a + byte) & 0xff
        ck_b = (ck_b + ck_a) & 0xff
    return b'\xb5\x62' + body + bytes([ck_a, ck_b])


def _query_ubx_serial(ser, msg_class, msg_id, payload=b'', timeout=4.0):
    previous_timeout = ser.timeout
    ser.timeout = 0.2
    buffer = bytearray()
    try:
        ser.reset_input_buffer()
        ser.write(_ubx_packet(msg_class,msg_id,payload))
        ser.flush()
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            buffer.extend(ser.read(min(ser.in_waiting or 1,4096)))
            while len(buffer) >= 8:
                start = buffer.find(b'\xb5\x62')
                if start < 0:
                    del buffer[:-1]
                    break
                del buffer[:start]
                if len(buffer) < 8:
                    break
                length = int.from_bytes(buffer[4:6],'little')
                if length > 8192:
                    del buffer[0]
                    continue
                if len(buffer) < length+8:
                    break
                packet = bytes(buffer[:length+8])
                del buffer[:length+8]
                if packet != _ubx_packet(packet[2],packet[3],packet[6:-2]):
                    continue
                if packet[2:4] == b'\x05\0' and packet[6:-2] == bytes([msg_class,msg_id]):
                    raise RuntimeError('Receiver rejected UBX readback')
                if packet[2:4] == bytes([msg_class,msg_id]):
                    return packet[6:-2]
        raise TimeoutError('Receiver configuration readback timeout')
    finally:
        ser.timeout = previous_timeout


def _ubx_valset_values(blob):
    values, offset = {}, 0
    while offset < len(blob):
        length = int.from_bytes(blob[offset+4:offset+6],'little')
        packet = blob[offset:offset+length+8]
        if (len(packet) < 12 or packet != _ubx_packet(6,0x8a,packet[6:-2]) or packet[6:10] != b'\0\x07\0\0'):
            raise ValueError('Expected VALSET RAM/BBR/Flash commands only')
        values.update(_ubx_cfg_values(b'\x01\0\0\0'+packet[10:-2]))
        offset += length+8
    if not values:
        raise ValueError('Empty VALSET configuration')
    return values


def _verify_ublox_values(ser, values):
    items = list(values.items())
    for layer in (0,1,2):  # RAM, BBR and Flash have different VALGET layer IDs.
        for offset in range(0,len(items),64):
            chunk = dict(items[offset:offset+64])
            poll = bytes([0,layer,0,0])+b''.join(struct.pack('<I',key) for key in chunk)
            body = _query_ubx_serial(ser,6,0x8b,poll)
            if body[:4] != bytes([1,layer,0,0]) or _ubx_cfg_values(body) != chunk:
                raise RuntimeError(f'Receiver configuration differs in layer {layer}; configuration is unconfirmed')


def _next_ubx_packet(buffer):
    while len(buffer) >= 8:
        start = buffer.find(b'\xb5\x62')
        if start < 0:
            del buffer[:-1]
            break
        del buffer[:start]
        if len(buffer) < 8:
            break
        length = int.from_bytes(buffer[4:6], 'little')
        if length > 8192:
            del buffer[0]
            continue
        if len(buffer) < length + 8:
            break
        packet = bytes(buffer[:length + 8])
        del buffer[:length + 8]
        if packet == _ubx_packet(packet[2], packet[3], packet[6:-2]):
            return packet
    return None


def _verify_tmode_packet(packet, expected_packet, expected_flags):
    if packet[2:4] == b'\x05\x00' and packet[6:-2] == b'\x06\x71':
        raise RuntimeError("Receiver rejected UBX-CFG-TMODE3 poll")
    if packet[2:4] != b'\x06\x71' or len(packet) != 48:
        return False
    actual_flags = int.from_bytes(packet[8:10], 'little') & 0x1ff
    if actual_flags != expected_flags:
        raise RuntimeError(f"Receiver TMODE mismatch: expected {expected_flags:#x}, got {actual_flags:#x}")
    if expected_flags & 0xff == 2 and (
        packet[10:25] != expected_packet[10:25]
        or packet[26:30] != expected_packet[26:30]
    ):
        raise RuntimeError("Receiver fixed coordinates differ from requested coordinates")
    logging.info("Receiver TMODE3 readback verified: mode=%s", actual_flags & 0xff)
    return True


def _verify_ublox_tmode(ser, expected_packet, timeout=4.0):
    """Poll TMODE3; a successful serial write is not proof of receiver mode."""
    expected_flags = int.from_bytes(expected_packet[8:10], 'little') & 0x1ff
    previous_timeout = ser.timeout
    ser.timeout = 0.2
    buffer = bytearray()
    try:
        ser.reset_input_buffer()
        ser.write(_ubx_packet(0x06, 0x71))
        ser.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buffer.extend(ser.read(min(ser.in_waiting or 1, 4096)))
            while len(buffer) >= 8:
                packet = _next_ubx_packet(buffer)
                if packet is None:
                    break
                if _verify_tmode_packet(packet, expected_packet, expected_flags):
                    return
        raise TimeoutError("No valid TMODE3 readback from receiver; configuration is unconfirmed")
    finally:
        ser.timeout = previous_timeout


def _cleanup_inject_queue(gnss_reader):
    """Remove GNSSReader's inject queue from RTCM subscribers after auto-base is done"""
    if gnss_reader:
        with subscriber_lock:
            if gnss_reader.rtcm_inject_queue in rtcm_subscribers:
                rtcm_subscribers.remove(gnss_reader.rtcm_inject_queue)
        # Drain any remaining packets
        while not gnss_reader.rtcm_inject_queue.empty():
            try:
                gnss_reader.rtcm_inject_queue.get_nowait()
            except Empty:
                break

def _set_auto_base_progress(phase: str, **extra):
    global AUTO_BASE_PROGRESS
    now = time.time()
    raw_gga = globals().get('LAST_RAW_GGA')
    gga_ts = float(globals().get('LAST_RAW_GGA_TS') or 0.0)
    gga_age = round(now - gga_ts, 1) if gga_ts > 0 else None
    fix_status = globals().get('LAST_GGA_FIX_STATUS') or "NO_FIX"
    coord = globals().get('LAST_GGA_COORD')
    
    progress = {
        "phase": phase,
        "updated_at": int(now),
        "last_raw_gga": raw_gga,
        "gga_age_seconds": gga_age,
        "gga_fix_status": fix_status,
        "current_coord": {
            "lat": repr(coord[0]) if coord and coord[0] is not None else None,
            "lon": repr(coord[1]) if coord and coord[1] is not None else None,
            "alt": repr(coord[2]) if coord and coord[2] is not None else None,
        } if coord else None,
    }
    client = globals().get('active_auto_base_client')
    if client is not None and hasattr(client, 'datum_message_counts'):
        with DATUM_LOCK:
            progress['incoming_rtcm_messages'] = dict(client.datum_message_counts)
        config = client.config
        progress['cors_source'] = {"host": config.get('rtcmserver1'), "port": config.get('rtcmport1'),
                                   "mountpoint": config.get('rtcmmountpoint1')}
    progress.update(extra)
    AUTO_BASE_PROGRESS = progress

def _robust_mean(values):
    if not values:
        return None
    if len(values) < 5:
        return sum(values) / len(values)

    median_val = statistics.median(values)
    deviations = [abs(v - median_val) for v in values]
    mad = statistics.median(deviations)

    if mad <= 1e-12:
        sorted_vals = sorted(values)
        trim = max(1, int(len(sorted_vals) * 0.1))
        core = sorted_vals[trim:len(sorted_vals) - trim]
        if not core:
            core = sorted_vals
        return sum(core) / len(core)

    # 1.4826 scales MAD to approximate standard deviation on normal data.
    robust_sigma = 1.4826 * mad
    threshold = 3.5 * robust_sigma
    core = [v for v in values if abs(v - median_val) <= threshold]
    if not core:
        core = values
    return sum(core) / len(core)

def _plain_mean(values):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)

async def _apply_base_mode_to_chip(chip_type: str, port: str, lat: float, lon: float, alt: float, gnss_reader: GNSSReader):
    if not port:
        raise RuntimeError("No GNSS port")

    if gnss_reader:
        gnss_reader.pause()
        await asyncio.sleep(1.0)

    # Ensure coordinates are floats for mathematical operations
    lat = float(lat)
    lon = float(lon)
    alt = float(alt)

    try:
        baud = _runtime_serial_baud(gnss_reader=gnss_reader)
        logging.info(f"Opening GNSS base-mode port {port} @ {baud} baud")
        with serial_port_lock, serial.Serial(port, baud, timeout=2) as ser:
            if chip_type == "Ublox":
                accuracy = 0.01
                msg = bytearray(b'\xb5\x62\x06\x71\x28\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
                msg[8] = 2
                msg[9] = 1

                multiplier = 10000000
                # Match the BASE builder's truncation; rounding the residual can produce invalid HP +/-100.
                fixed_lat, fixed_lon, fixed_alt = (Decimal(str(value)) for value in (lat, lon, alt))
                ecef_x_or_lat = int(fixed_lat * multiplier).to_bytes(4, byteorder='little', signed=True)
                ecef_x_or_lat_hp = (int(fixed_lat * multiplier * 100) - int(fixed_lat * multiplier) * 100).to_bytes(1, byteorder='little', signed=True)
                ecef_y_or_lon = int(fixed_lon * multiplier).to_bytes(4, byteorder='little', signed=True)
                ecef_y_or_lon_hp = (int(fixed_lon * multiplier * 100) - int(fixed_lon * multiplier) * 100).to_bytes(1, byteorder='little', signed=True)
                ecef_z_or_alt = int(fixed_alt * 100).to_bytes(4, byteorder='little', signed=True)
                ecef_z_or_alt_hp = (int(fixed_alt * 10000) - int(fixed_alt * 100) * 100).to_bytes(1, byteorder='little', signed=True)
                fixed_pos_acc = int(accuracy * 10000).to_bytes(4, byteorder='little', signed=False)

                for i in range(4):
                    msg[10 + i] = ecef_x_or_lat[i]
                    msg[14 + i] = ecef_y_or_lon[i]
                    msg[18 + i] = ecef_z_or_alt[i]
                    msg[26 + i] = fixed_pos_acc[i]

                msg[22] = ecef_x_or_lat_hp[0]
                msg[23] = ecef_y_or_lon_hp[0]
                msg[24] = ecef_z_or_alt_hp[0]

                ck_a, ck_b = 0, 0
                for i in range(2, 46):
                    ck_a = (ck_a + msg[i]) & 0xFF
                    ck_b = (ck_b + ck_a) & 0xFF
                msg[46] = ck_a
                msg[47] = ck_b

                mode = bytes(msg[6:-2])
                keys = {key: mode[offset:offset+size] for key,offset,size in _ubx_tmode_fields()
                        if key not in (0x40030003,0x40030004,0x40030005,0x20030006,0x20030007,0x20030008)}
                for key in (0x209102BE,0x209102C0):
                    keys[key] = b'\x01'
                ser.write(_ubx_packet(6,0x8a,b'\0\x07\0\0'+b''.join(struct.pack('<I',key)+value for key,value in keys.items())))
                ser.flush()
                _verify_ublox_values(ser,keys)
            elif chip_type in ("Unicorecomm", "RTCM3_Source"):
                # Truncate latitude/longitude at the receiver boundary, preserving calculation precision.
                base_cmd = f"MODE BASE {Decimal(repr(lat)).quantize(Decimal('0.0000000001'), rounding=ROUND_DOWN):.10f} {Decimal(repr(lon)).quantize(Decimal('0.0000000001'), rounding=ROUND_DOWN):.10f} {Decimal(repr(alt)):f}\r\n".encode("ascii")
                # Auto Base supplies ellipsoidal h; Unicore's default is geoid-based altitude.
                ser.write(b"CONFIG UNDULATION 0.0\r\n")
                ser.flush()
                await asyncio.sleep(0.2)
                ser.write(base_cmd)
                ser.flush()
                await asyncio.sleep(0.5)
                ser.write(b"SAVECONFIG\r\n")
                ser.flush()
            else:
                raise RuntimeError(f"Unsupported chip type: {chip_type}")
    finally:
        if gnss_reader:
            await asyncio.sleep(2.0)
            gnss_reader.resume()

async def _apply_rover_mode_to_chip(chip_type: str, port: str, gnss_reader: GNSSReader):
    if not port:
        raise RuntimeError("No GNSS port")
    if gnss_reader:
        gnss_reader.pause()
        await asyncio.sleep(1.0)
    try:
        baud = _runtime_serial_baud(gnss_reader=gnss_reader)
        with serial_port_lock, serial.Serial(port, baud, timeout=2) as ser:
            if chip_type == "Ublox":
                expected_values = {}
                # Helper for UBX-CFG-VALSET (Generation 9 - F9P / F9H)
                def send_valset_u1(key_id, val):
                    v_msg = bytearray(b'\xb5\x62\x06\x8a\x09\x00\x00\x07\x00\x00')
                    v_msg.extend(key_id.to_bytes(4, 'little'))
                    v_msg.append(val & 0xFF)
                    v_msg.extend([0, 0])
                    v_a = v_b = 0
                    for j in range(2, 15):
                        v_a = (v_a + v_msg[j]) & 0xFF
                        v_b = (v_b + v_a) & 0xFF
                    v_msg[15], v_msg[16] = v_a, v_b
                    ser.write(v_msg)
                    expected_values[key_id] = bytes([val & 0xff])

                # Input protocol gates are independent from message output rates.
                for input_prefix, output_prefix in ((0x10730000, 0x10740000), (0x10750000, 0x10760000), (0x10770000, 0x10780000)):
                    send_valset_u1(input_prefix | 0x01, 1)  # UBX configuration input
                    send_valset_u1(input_prefix | 0x04, 1)  # RTCM3 correction input
                    send_valset_u1(output_prefix | 0x01, 1)  # UBX readback
                    send_valset_u1(output_prefix | 0x02, 1)  # NMEA GGA output

                # Set TMODE to 0 (Disabled / Rover) via VALSET
                send_valset_u1(0x20030001, 0)
                # Output NMEA GGA on USB and UART1 at 1Hz via VALSET
                send_valset_u1(0x209100bd, 1) # CFG-MSGOUT-NMEA_ID_GGA_USB
                send_valset_u1(0x209100bb, 1) # CFG-MSGOUT-NMEA_ID_GGA_UART1
                send_valset_u1(0x20910036, 1) # CFG-MSGOUT-UBX_NAV_HPPOSLLH_USB
                send_valset_u1(0x20910034, 1) # CFG-MSGOUT-UBX_NAV_HPPOSLLH_UART1
                send_valset_u1(0x20910009, 1) # CFG-MSGOUT-UBX_NAV_PVT_USB
                # Disable RTCM 1005 output
                send_valset_u1(0x209102c0, 0) # CFG-MSGOUT-RTCM3X_1005_USB
                send_valset_u1(0x209102be, 0) # CFG-MSGOUT-RTCM3X_1005_UART1

                ser.flush()
                _verify_ublox_values(ser, expected_values)
                logging.info("AutoBase: Ublox ROVER mode applied and verified with VALSET/VALGET")
            elif chip_type in ("Unicorecomm", "RTCM3_Source"):
                ser.write(b'UNLOGALL\r\n')
                ser.flush()
                await asyncio.sleep(0.3)
                ser.write(b'MODE ROVER\r\n')
                ser.flush()
                await asyncio.sleep(0.3)
                ser.write(b'LOG GNGGA ONTIME 1\r\n')
                ser.write(b'LOG GPRMC ONTIME 1\r\n')
                ser.flush()
                await asyncio.sleep(0.3)
                ser.write(b'SAVECONFIG\r\n')
                ser.flush()
                logging.info(f"AutoBase: _apply_rover_mode_to_chip {chip_type} ROVER mode set (UNLOGALL + MODE ROVER + LOG GNGGA ONTIME 1)")
            else:
                ser.write(b'UNLOGALL\r\nMODE ROVER\r\nLOG GNGGA ONTIME 1\r\nSAVECONFIG\r\n')
                ser.flush()
    finally:
        if gnss_reader:
            await asyncio.sleep(2.0)
            gnss_reader.resume()

async def base_reference_check_state_machine(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client, cfg: dict):
    global current_state, active_auto_base_client
    reference = cfg["reference_coords"]
    restore = cfg["restore_coords"]
    port = agent.detected_chip.get("port")
    temp_client = None
    result = None
    failure = None
    try:
        current_state = "REFERENCE_CHECK_ROVER"
        _set_auto_base_progress("reference_check_set_rover", step=1, total_steps=4)
        await send_status(agent, mqtt_client)
        await _apply_rover_mode_to_chip("Ublox", port, gnss_reader)
        temp_client = NTRIPClientWorker({
            "rtcmserver1": cfg["ip"], "rtcmport1": str(cfg["port"]),
            "rtcmusername1": cfg["user"], "rtcmpassword1": cfg["password"],
            "rtcmmountpoint1": cfg["mountpoint"], "reconnectioninterval": 5,
        }, agent.log, correction_sink=gnss_reader.inject_rtcm)
        active_auto_base_client = temp_client
        temp_client.start()
        current_state = "REFERENCE_CHECK_WAIT_FIX"
        deadline = time.time() + cfg["timeout"]
        while globals().get("LAST_GGA_FIX_STATUS") != "RTK_FIXED":
            if time.time() >= deadline:
                raise TimeoutError("RTK FIX timeout")
            _set_auto_base_progress("reference_check_wait_fix", step=2, total_steps=4, fix_status=globals().get("LAST_GGA_FIX_STATUS"))
            await asyncio.sleep(1)
        current_state = "REFERENCE_CHECK_AVERAGING"
        coords = []
        while len(coords) < cfg["samples"]:
            await asyncio.sleep(1)
            coord = globals().get("LAST_GGA_COORD")
            if globals().get("LAST_GGA_FIX_STATUS") == "RTK_FIXED" and coord and len(coord) >= 3:
                coords.append(tuple(float(value) for value in coord[:3]))
            _set_auto_base_progress("reference_check_averaging", step=3, total_steps=4, samples_collected=len(coords), samples_target=cfg["samples"])
        measured = {"lat": _plain_mean([value[0] for value in coords]), "lon": _plain_mean([value[1] for value in coords]), "alt": _plain_mean([value[2] for value in coords])}
        mean_lat = math.radians((measured["lat"] + reference["lat"]) / 2.0)
        result = {
            "reference_coord": reference,
            "measured_coord": measured,
            "delta_n_m": math.radians(measured["lat"] - reference["lat"]) * 6378137.0,
            "delta_e_m": math.radians(measured["lon"] - reference["lon"]) * 6378137.0 * math.cos(mean_lat),
            "delta_u_m": measured["alt"] - reference["alt"],
            "sample_count": len(coords),
        }
    except asyncio.CancelledError:
        failure = "cancelled by user"
        raise
    except Exception as exc:
        failure = str(exc)
        logging.exception("Base reference check failed")
    finally:
        if temp_client:
            temp_client.stop()
            temp_client.join(timeout=5)
        active_auto_base_client = None
        _cleanup_inject_queue(gnss_reader)
        restore_error = None
        try:
            current_state = "REFERENCE_CHECK_RESTORE_BASE"
            _set_auto_base_progress("reference_check_restore_base", step=4, total_steps=4, result=result, failure=failure)
            await _apply_base_mode_to_chip("Ublox", port, restore["lat"], restore["lon"], restore["alt"], gnss_reader)
            agent.config["base_config"] = cfg["original_base_config"]
            agent.config.pop("pending_base_restore", None)
            agent.save_config()
        except Exception as exc:
            restore_error = str(exc)
            logging.exception("Base reference check could not restore base mode")
        current_state = "ONLINE"
        _set_auto_base_progress(
            "reference_check_completed" if result and not restore_error else "reference_check_failed",
            step=4,
            total_steps=4,
            result=result,
            failure=failure or restore_error,
            base_restored=restore_error is None,
        )
        await send_status(agent, mqtt_client)

async def restore_pending_base_if_needed(agent: AgentManager, gnss_reader: GNSSReader):
    pending = agent.config.get("pending_base_restore")
    if not isinstance(pending, dict):
        return
    coords = pending.get("coords") or {}
    try:
        lat, lon, alt = float(coords["lat"]), float(coords["lon"]), float(coords["alt"])
    except (KeyError, TypeError, ValueError):
        logging.error("Pending base restore has invalid coordinates; leaving recovery marker in place")
        return
    try:
        await _apply_base_mode_to_chip(pending.get("chip_type") or "Ublox", agent.detected_chip.get("port"), lat, lon, alt, gnss_reader)
        agent.config["base_config"] = pending.get("base_config") or agent.get_base_config()
        agent.config.pop("pending_base_restore", None)
        agent.save_config()
        logging.warning("Recovered pending base mode from interrupted reference check")
    except Exception:
        logging.exception("Could not recover pending base mode; will retry after next restart")

async def auto_base_state_machine(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global active_auto_base_client, current_state
    if not agent.config.get("auto_base_setup", {}).get("enabled", False):
        return
    agent.config['gnss_config_pending'] = True
    agent.config.pop('auto_base_candidate', None)
    agent.save_config()
    candidate = None
    original_base = copy.deepcopy(agent.config.get('base_config') or {})
    restore = original_base.get('coords') or {}
    # The sampling stage must never install its candidate without operator review.
    if all(key in restore for key in ('lat', 'lon', 'alt')):
        agent.config['pending_base_restore'] = {
            'coords': restore, 'base_config': original_base,
            'chip_type': agent.detected_chip.get('type'),
        }
        agent.save_config()
    try:
        candidate = await _run_auto_base_setup(agent, gnss_reader, mqtt_client)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.exception("Auto Base setup failed")
        current_state = "ONLINE"
        _set_auto_base_progress("failed", reason=str(exc))
        await send_status(agent, mqtt_client)
    finally:
        client = active_auto_base_client
        if client:
            client.stop()
            await asyncio.to_thread(client.join, 6)
        active_auto_base_client = None
        _cleanup_inject_queue(gnss_reader)
        agent.config['auto_base_setup'] = {"enabled": False}
        if agent.config.get('pending_base_restore'):
            try:
                await _apply_base_mode_to_chip(agent.detected_chip.get('type'), agent.detected_chip.get('port'),
                    float(restore['lat']), float(restore['lon']), float(restore['alt']), gnss_reader)
                agent.config.pop('pending_base_restore', None)
            except Exception as exc:
                candidate = None
                _set_auto_base_progress('failed', reason=f'Không khôi phục được Base cũ: {exc}')
                logging.exception('Auto Base could not restore previous base')
        agent.config['gnss_config_pending'] = bool(agent.config.get('pending_base_restore'))
        agent.save_config()
        if gnss_reader:
            gnss_reader.resume()
        current_state = 'ONLINE'
        if candidate:
            agent.config['auto_base_candidate'] = candidate
            agent.save_config()
            _set_auto_base_progress('ready_for_review', **candidate)
        await send_status(agent, mqtt_client)


async def _fail_auto_base_stage(agent, gnss_reader, mqtt_client, temp_client, step, message, reason):
    global current_state
    logging.error(message)
    _set_auto_base_progress("failed", step=step, total_steps=4, reason=reason)
    temp_client.stop()
    _cleanup_inject_queue(gnss_reader)
    current_state = "ONLINE"
    await send_status(agent, mqtt_client)


async def _prepare_auto_base_session(agent, gnss_reader, mqtt_client, cfg, port, chip_type):
    global active_auto_base_client, current_state
    current_state = "AUTO_SETUP_ROVER"
    _set_auto_base_progress("set_rover", step=1, total_steps=4)
    logging.info("AutoBase: Setting chip to ROVER mode...")
    await send_status(agent, mqtt_client)
    gnss_reader.pause()
    await asyncio.sleep(1.0)
    try:
        await _apply_rover_mode_to_chip(chip_type, port, gnss_reader)
        logging.info(f"AutoBase: {chip_type} successfully configured to ROVER mode")
    except Exception as error:
        logging.exception(f"AutoBase: Failed to set ROVER mode: {error}")
        raise

    logging.info("AutoBase: Waiting for rover chip to output NMEA GGA...")
    for _ in range(10):
        if globals().get('LAST_RAW_GGA'):
            logging.info(f"AutoBase: Initial NMEA GGA from chip: {globals().get('LAST_RAW_GGA')}")
            break
        await asyncio.sleep(0.5)

    ntrip_host = str(cfg.get("ip") or "").strip()
    ntrip_port = str(cfg.get("port") or "")
    ntrip_user = str(cfg.get("user") or "").strip()
    ntrip_pass = str(cfg.get("password") or "")
    ntrip_mount = str(cfg.get("mountpoint") or "").strip()
    temp_cfg = {
        'rtcmserver1': ntrip_host,
        'rtcmport1': ntrip_port,
        'rtcmusername1': ntrip_user,
        'rtcmpassword1': ntrip_pass,
        'rtcmmountpoint1': ntrip_mount,
        'reconnectioninterval': 5,
    }
    session_datum = RTCMDatum()
    session_messages = {}

    def receive_correction(packet):
        _receive_auto_base_correction(packet, gnss_reader, session_datum, session_messages)

    temp_client = NTRIPClientWorker(temp_cfg, agent.log, correction_sink=receive_correction)
    temp_client.datum_message_counts = session_messages
    active_auto_base_client = temp_client
    temp_client.start()
    return {
        "client": temp_client,
        "datum": session_datum,
        "messages": session_messages,
        "host": ntrip_host,
        "port": ntrip_port,
        "mountpoint": ntrip_mount,
    }


async def _wait_for_auto_base_fix(agent, gnss_reader, mqtt_client, session, timeout, fixed_streak_required):
    global current_state
    temp_client = session["client"]
    current_state = "AUTO_SETUP_WAIT_FIX"
    start_time = time.time()
    _set_auto_base_progress(
        "wait_fix", step=2, total_steps=4, timeout=timeout, elapsed=0,
        fix_status=globals().get('LAST_GGA_FIX_STATUS'), fixed_streak=0,
        fixed_streak_required=fixed_streak_required, rtcm_bytes=0, rtcm_pkts=0,
    )
    await send_status(agent, mqtt_client)
    logging.info(
        f"AutoBase: Waiting for stable RTK Fix... (Timeout: {timeout}s, required_streak={fixed_streak_required}s)"
    )
    fixed_streak = 0
    while True:
        if _get_fresh_outbound_gga(3.0) and _is_rtk_usable_status(globals().get('LAST_GGA_FIX_STATUS')):
            fixed_streak += 1
        else:
            fixed_streak = 0
        if fixed_streak >= fixed_streak_required:
            return True
        if time.time() - start_time > timeout:
            await _fail_auto_base_stage(
                agent, gnss_reader, mqtt_client, temp_client, 2,
                "AutoBase: RTK Fix timeout!", "RTK fix timeout",
            )
            return False
        elapsed = int(time.time() - start_time)
        _set_auto_base_progress(
            "wait_fix", step=2, total_steps=4, timeout=timeout, elapsed=elapsed,
            fix_status=globals().get('LAST_GGA_FIX_STATUS'), fixed_streak=fixed_streak,
            fixed_streak_required=fixed_streak_required,
            rtcm_bytes=getattr(temp_client, 'total_rtcm_bytes', 0),
            rtcm_pkts=getattr(temp_client, 'total_rtcm_pkts', 0),
            last_sent_gga=getattr(temp_client, 'last_sent_gga', None),
        )
        if elapsed % 2 == 0:
            await send_status(agent, mqtt_client)
        await asyncio.sleep(1)


async def _collect_auto_base_samples(agent, gnss_reader, mqtt_client, session, cfg, fixed_streak_required):
    global current_state
    current_state = "AUTO_SETUP_AVERAGING"
    configured_samples = int(cfg.get("samples", 60))
    samples_needed = 60
    averaging_seconds = 60
    if configured_samples != samples_needed:
        logging.info(
            f"AutoBase: Override requested samples={configured_samples} -> fixed {samples_needed} samples / {averaging_seconds}s"
        )
    logging.info(
        f"AutoBase: Stable RTK usable status confirmed ({fixed_streak_required}s, fixed). "
        f"Collecting up to {samples_needed} samples in {averaging_seconds}s..."
    )
    _set_auto_base_progress(
        "averaging", step=3, total_steps=4, samples_collected=0,
        samples_target=samples_needed, averaging_seconds=averaging_seconds, elapsed=0,
    )
    await send_status(agent, mqtt_client)
    coords = []
    last_sample_ts = 0.0
    averaging_start = time.time()
    sample_deadline = averaging_start + averaging_seconds
    while len(coords) < samples_needed and time.time() < sample_deadline:
        elapsed = int(time.time() - averaging_start)
        await asyncio.sleep(1)
        if not _get_fresh_outbound_gga(3.0) or not _is_rtk_usable_status(globals().get('LAST_GGA_FIX_STATUS')):
            _set_auto_base_progress(
                "averaging", step=3, total_steps=4, samples_collected=len(coords),
                samples_target=samples_needed, fix_status=globals().get('LAST_GGA_FIX_STATUS'),
                averaging_seconds=averaging_seconds, elapsed=elapsed,
            )
            continue
        coord = globals().get('LAST_GGA_COORD')
        sample_ts = float(globals().get('LAST_RAW_GGA_TS') or 0.0)
        if (
            sample_ts > last_sample_ts and coord and len(coord) >= 3
            and coord[0] is not None and coord[1] is not None and coord[2] is not None
            and -90.0 <= float(coord[0]) <= 90.0
            and -180.0 <= float(coord[1]) <= 180.0
        ):
            last_sample_ts = sample_ts
            coords.append(coord)
            _set_auto_base_progress(
                "averaging", step=3, total_steps=4, samples_collected=len(coords),
                samples_target=samples_needed, averaging_seconds=averaging_seconds,
                elapsed=elapsed,
                latest_coord={"lat": float(coord[0]), "lon": float(coord[1]), "alt": float(coord[2])},
            )
            await send_status(agent, mqtt_client)
    if len(coords) < max(5, min(samples_needed, 15)):
        await _fail_auto_base_stage(
            agent, gnss_reader, mqtt_client, session["client"], 3,
            "AutoBase: Failed to collect samples.",
            f"Insufficient valid RTK samples ({len(coords)}/{samples_needed})",
        )
        return None
    avg_lat = _plain_mean([float(coord[0]) for coord in coords])
    avg_lon = _plain_mean([float(coord[1]) for coord in coords])
    avg_alt = _plain_mean([float(coord[2]) for coord in coords])
    logging.info(f"AutoBase: Averaged Pos -> Lat: {avg_lat!r}, Lon: {avg_lon!r}, Alt: {avg_alt!r}")
    return {
        "coords": coords,
        "raw_coord": {"lat": repr(avg_lat), "lon": repr(avg_lon), "alt": repr(avg_alt)},
        "lat": avg_lat,
        "lon": avg_lon,
        "alt": avg_alt,
    }


def _stop_auto_base_client(session, gnss_reader):
    client = session["client"]
    client.stop()
    client.join(timeout=5)
    _cleanup_inject_queue(gnss_reader)
    logging.info("AutoBase: NTRIP client stopped, inject queue cleaned up")


async def _resolve_auto_base_projection_location(agent, mqtt_client, raw_coord, lat, lon, alt):
    _set_auto_base_progress('resolving_location', raw_coord=raw_coord, step=4, total_steps=4)
    await send_status(agent, mqtt_client)
    try:
        return await asyncio.to_thread(_resolve_auto_base_location, lat, lon, alt)
    except Exception as error:
        return {'state': 'lookup_failed', 'reason': str(error)}


async def _configure_auto_base_projection(agent, mqtt_client, cfg, enabled, datum, raw_coord, lat, lon, alt):
    projection_source = 'not_used'
    location = {}
    province_code = None
    province_name = None
    if not enabled:
        return projection_source, location, province_code, province_name
    if not datum.rtcm1021_update:
        raise ValueError('Chưa nhận RTCM 1021 của phiên CORS này; không dùng 7 tham số mặc định.')
    if datum.rtcm1025_update:
        projection_source = 'rtcm1025'
    elif cfg.get('l0_deg') is not None and cfg.get('k0') is not None:
        datum.l0 = math.radians(float(cfg['l0_deg']))
        datum.k0 = float(cfg['k0'])
        projection_source = 'manual'
    else:
        location = await _resolve_auto_base_projection_location(agent, mqtt_client, raw_coord, lat, lon, alt)
        province_code = location.get('province_code')
        province_name = location.get('province_name')
        if location.get('l0_deg') is None or location.get('k0') is None:
            raise ValueError('Chưa xác định được kinh tuyến trục. Cần RTCM 1025 hoặc nhập L0/k0 theo tỉnh.')
        datum.l0 = math.radians(float(location['l0_deg']))
        datum.k0 = float(location['k0'])
        projection_source = 'server_catalog'
    for key in ('resN', 'resE', 'resHS'):
        value = float(cfg.get(key, 0.0))
        if not math.isfinite(value):
            raise ValueError(f'Invalid CORS shift: {key}')
        setattr(datum, key, value)
    return projection_source, location, province_code, province_name


def _transform_auto_base_coordinates(enabled, datum, projection_source, lat, lon, alt):
    transform_applied = False
    transform_reason = 'disabled' if not enabled else 'not_applied'
    vn2000_neh = None
    if enabled:
        try:
            if not _is_valid_vn2000_projection_params(datum):
                transform_reason = "invalid_projection"
                logging.warning(
                    "AutoBase: Skip ITRF->VN2000 transform due to invalid L0/k0 "
                    f"(L0={math.degrees(datum.l0)!r}, k0={datum.k0!r})"
                )
            else:
                north, east, height, local_lat, local_lon, local_alt = _transform_itrf_to_vn2000_neh_and_llh(
                    lat, lon, alt, datum
                )
                if (
                    math.isfinite(local_lat) and math.isfinite(local_lon) and math.isfinite(local_alt)
                    and -90.0 <= local_lat <= 90.0
                    and -180.0 <= local_lon <= 180.0
                    and -1000.0 <= local_alt <= 100000.0
                ):
                    vn2000_neh = {"north": north, "east": east, "h": height}
                    lat, lon, alt = local_lat, local_lon, local_alt
                    logging.info(
                        "AutoBase: ITRF->VN2000 transform applied "
                        f"(L0={math.degrees(datum.l0)!r}, k0={datum.k0!r}, source={projection_source}) "
                        f"-> Lat: {lat!r}, Lon: {lon!r}, Alt: {alt!r}, N: {north!r}, E: {east!r}, H: {height!r}"
                    )
                    transform_applied = True
                    transform_reason = "applied"
                else:
                    transform_reason = "invalid_output"
                    logging.warning(
                        "AutoBase: Skip transformed coordinate out of range, fallback to LLH "
                        f"(lat={local_lat}, lon={local_lon}, alt={local_alt})"
                    )
        except Exception as error:
            transform_reason = "transform_error"
            logging.warning(f"AutoBase: ITRF->VN2000 transform failed, fallback to averaged LLH: {error}")
    if enabled and not transform_applied:
        raise ValueError(f'Không chuyển đổi được tọa độ: {transform_reason}; chưa tạo kết quả để áp dụng.')
    return lat, lon, alt, transform_applied, transform_reason, vn2000_neh


def _build_auto_base_result(session, sample_result, projection, transformed, chip_type, enabled):
    raw_coord = sample_result["raw_coord"]
    lat, lon, alt, transform_applied, transform_reason, vn2000_neh = transformed
    projection_source, location, province_code, province_name = projection
    calculated_coord = {"lat": repr(lat), "lon": repr(lon), "alt": repr(alt)}
    final_coord = dict(calculated_coord)
    if chip_type in ('Ublox', 'Unicorecomm'):
        fields = (('lat', 9), ('lon', 9), ('alt', 4)) if chip_type == 'Ublox' else (('lat', 10), ('lon', 10))
        for key, digits in fields:
            final_coord[key] = format(
                Decimal(calculated_coord[key]).quantize(
                    Decimal(1).scaleb(-digits), rounding=ROUND_DOWN
                ),
                f'.{digits}f',
            )
    datum = sample_result['datum_snapshot']
    conversion_report = {
        "version": 1,
        "source": {"host": session["host"], "port": int(session["port"]), "mountpoint": session["mountpoint"]},
        "input_coord": raw_coord,
        "calculated_coord": calculated_coord,
        "output_coord": final_coord,
        "input_reference": "receiver_reported",
        "output_reference": "local_llh" if transform_applied else "receiver_reported",
        "altitude_reference": "ELLIPSOID",
        "transform_requested": bool(enabled),
        "transform_applied": transform_applied,
        "reason": transform_reason,
        "receiver_write_completed": False,
        "received_messages": session["messages"],
        "parameter_source": "rtcm1021" if datum.rtcm1021_update else "not_used",
        "residual_source": "rtcm1023" if datum.rtcm1023_update else "none",
        "rtcm1025_used": projection_source == "rtcm1025",
        "processor": "python_agent",
        "zone_width_source": "inferred_from_k0",
        "rtcm1025": dict(datum.projection_1025),
        "location": location,
        "projection_source": projection_source,
        "zone_width_deg": (3 if abs(datum.k0 - 0.9999) < 1e-9 else
                           6 if abs(datum.k0 - 0.9996) < 1e-9 else None) if transform_applied else None,
        "province_code": province_code,
        "province_name": province_name,
        "province_hint": None,
        "central_meridian_deg": math.degrees(datum.l0),
        "k0": datum.k0,
        "projected_coord": vn2000_neh,
        "parameters": {key: getattr(datum, key) for key in
                       ("dX", "dY", "dZ", "rX", "rY", "rZ", "kT", "resB", "resL", "resH", "resN", "resE", "resHS")},
        "sample_count": len(sample_result["coords"]),
    }
    return {
        'averaged_coord': final_coord,
        'raw_coord': raw_coord,
        'calculated_coord': calculated_coord,
        'sample_count': len(sample_result["coords"]),
        'fixed_samples_only': True,
        'transform_applied': transform_applied,
        'conversion_report': conversion_report,
        'receiver_write_completed': False,
        'updated_at': int(time.time()),
    }


async def _run_auto_base_setup(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global current_state, LAST_GGA_COORD
    cfg = agent.config.get("auto_base_setup", {})
    enable_transform = cfg.get("enable_itrf_vn2000_transform", True) is not False
    if not cfg or not cfg.get("enabled", False):
        return
    logging.info("=== STARTING AUTO BASE SETUP ===")
    _set_auto_base_progress("starting")
    await send_status(agent, mqtt_client)
    port = agent.detected_chip.get("port")
    chip_type = cfg.get("sensor_type") or agent.detected_chip.get("type", "UNKNOWN")
    if cfg.get("sensor_type"):
        logging.info(f"AutoBase: Using manually overridden chip type from Frontend: {chip_type}")
    if not port or gnss_reader is None:
        raise RuntimeError("AutoBase requires a detected GNSS port and reader")

    session = await _prepare_auto_base_session(agent, gnss_reader, mqtt_client, cfg, port, chip_type)
    timeout = int(cfg.get("timeout", 3600))
    fixed_streak_required = int(cfg.get("fixed_streak_seconds", 5))
    if not await _wait_for_auto_base_fix(
        agent, gnss_reader, mqtt_client, session, timeout, fixed_streak_required
    ):
        return
    sample_result = await _collect_auto_base_samples(
        agent, gnss_reader, mqtt_client, session, cfg, fixed_streak_required
    )
    if sample_result is None:
        return
    _stop_auto_base_client(session, gnss_reader)
    with DATUM_LOCK:
        sample_result['datum_snapshot'] = copy.deepcopy(session['datum'])
        session['messages'] = dict(session['messages'])
    projection = await _configure_auto_base_projection(
        agent, mqtt_client, cfg, enable_transform, sample_result['datum_snapshot'],
        sample_result['raw_coord'], sample_result['lat'], sample_result['lon'], sample_result['alt'],
    )
    transformed = _transform_auto_base_coordinates(
        enable_transform, sample_result['datum_snapshot'], projection[0],
        sample_result['lat'], sample_result['lon'], sample_result['alt'],
    )
    return _build_auto_base_result(session, sample_result, projection, transformed, chip_type, enable_transform)


# ==============================================================================
# === MAIN FUNCTION                                                         ===
# ==============================================================================
async def _ensure_startup_lock():
    if not cleanup_lock_file():
        logging.error("Lock file issue. Another agent instance may be running. Exiting.")
        if IS_WINDOWS:
            await asyncio.to_thread(input, "Press Enter to exit.")
        sys.exit(1)
    
    if not create_lock_file():
        logging.error("Failed to create lock file. Exiting.")
        sys.exit(1)

async def _run_remote_lock_mode(loop):
    global current_state, agent, MACHINE_SERIAL
    current_state = "LOCKED"
    logging.warning("Device is remotely locked. Running in restricted mode.")
    MACHINE_SERIAL = get_machine_serial()
    remote_unlock_event.clear()

    agent = AgentManager(MACHINE_SERIAL)
    mqtt_client = setup_mqtt_client(loop, agent, None)

    initialization_complete.set()
    status_task = asyncio.create_task(status_publisher_task(agent, mqtt_client))
    ws_task = asyncio.create_task(websocket_task(agent, None, mqtt_client))
    unlock_task = asyncio.create_task(remote_unlock_event.wait())
    try:
        done, _ = await asyncio.wait(
            {status_task, ws_task, unlock_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done - {unlock_task}:
            task.result()
        if unlock_task not in done:
            raise RuntimeError("Restricted agent control task stopped before unlock")
    finally:
        for task in (status_task, ws_task, unlock_task):
            task.cancel()
        await asyncio.gather(status_task, ws_task, unlock_task, return_exceptions=True)
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
    logging.info("Remote lock cleared. Continuing normal agent startup.")


async def _run_license_wait_mode(loop):
    global current_state, agent
    current_state = "AWAITING_LICENSE"
    logging.warning("=" * 60)
    logging.warning("LICENSE INVALID OR MISSING")
    logging.warning("Running in limited mode - awaiting license deployment")
    logging.warning("=" * 60)

    agent = AgentManager(MACHINE_SERIAL)
    mqtt_client = setup_mqtt_client(loop, agent, None)
    initialization_complete.set()

    try:
        await asyncio.gather(
            status_publisher_task(agent, mqtt_client),
            websocket_task(agent, None, mqtt_client)
        )
    finally:
        if mqtt_client: mqtt_client.loop_stop(); mqtt_client.disconnect()
        remove_lock_file()


async def _prepare_full_agent(loop):
    global current_state, agent
    logging.info("=" * 60)
    logging.info(f"License is valid. Starting full agent on {platform.system()}.")
    logging.info(f"  Serial: {MACHINE_SERIAL}")
    logging.info("=" * 60)
    
    agent = AgentManager(MACHINE_SERIAL)
    
    chip_info = await detect_chip_with_retry()
    agent.detected_chip = chip_info
    logging.info("=" * 60)
    logging.info(f"Final Detection Result: Chip '{chip_info.get('type')}' on port '{chip_info.get('port') or 'N/A'}'")
    logging.info(f"  Baudrate: {chip_info.get('baud') or 'N/A'}")
    logging.info("=" * 60)
    
    gnss_reader = None
    agent.gnss_reader = None
    if chip_info.get("port") and chip_info.get("baud"): 
        gnss_reader = GNSSReader(
            agent.log, 
            port=chip_info["port"], 
            baudrate=chip_info["baud"] 
        )
        agent.gnss_reader = gnss_reader
        gnss_reader.start()
        logging.info(f"GNSS Reader thread started.")
    else:
        logging.error("No valid GNSS port detected - reader not started")
    mqtt_client = setup_mqtt_client(loop, agent, gnss_reader)
    await restore_pending_base_if_needed(agent, gnss_reader)
    
    agent.nmea_publisher = NMEAPublisher(mqtt_client, MACHINE_SERIAL, loop)
    agent.nmea_publisher.start()
    agent.rtcm_mqtt_publisher = RTCMPublisherMQTT(mqtt_client, MACHINE_SERIAL, loop, agent)
    agent.rtcm_mqtt_publisher.start()
    logging.info("NMEA Publisher thread started.")
    
    if not agent.config.get('is_provisioned'):
        current_state = "UNPROVISIONED"
    else:
        current_state = "ONLINE"
        agent.restart_services()
    
    logging.info(f"Agent is now running. State: {current_state}")
    
    initialization_complete.set()

    return agent, gnss_reader, mqtt_client


async def _run_startup_auto_base(task):
    try:
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        logging.info("Startup Auto Base task cancelled")


async def _cleanup_agent_runtime(agent, gnss_reader, mqtt_client):
    logging.info("="*60)
    logging.info("Main loop interrupted. Starting cleanup...")

    if agent.nmea_publisher and agent.nmea_publisher.is_alive():
        agent.nmea_publisher.stop()
    if hasattr(agent, "rtcm_mqtt_publisher") and agent.rtcm_mqtt_publisher and agent.rtcm_mqtt_publisher.is_alive():
        agent.rtcm_mqtt_publisher.stop()
        agent.nmea_publisher.join(timeout=2)

    agent.restart_services()

    if hasattr(agent, 'ntrip_client_worker') and agent.ntrip_client_worker:
        if agent.ntrip_client_worker.is_alive():
            agent.ntrip_client_worker.stop()
            agent.ntrip_client_worker.join(timeout=2)

    if gnss_reader and gnss_reader.is_alive():
        gnss_reader.stop()
        gnss_reader.join(timeout=3)
        logging.info("GNSS Reader stopped")

    if mqtt_client:
        try:
            offline_msg = json.dumps({
                "serial": MACHINE_SERIAL,
                "status": "offline",
                "timestamp": int(time.time()),
                "agent_timestamp": int(time.time()),
                "status_boot_id": STATUS_BOOT_ID,
                "status_sequence": STATUS_SEQUENCE,
                "status_event": "cleanup",
                "cleanup": True
            })
            mqtt_client.publish(f"pi/devices/{MACHINE_SERIAL}/status", offline_msg, qos=1, retain=True)
            await asyncio.sleep(0.5)
        except Exception as e:
            logging.exception(f"Failed to send final offline status: {e}")

        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    remove_lock_file()
    logging.info("Cleanup complete. Agent stopped.")


async def _run_agent_background_tasks(agent, gnss_reader, mqtt_client):
    global active_auto_base_task
    status_task = asyncio.create_task(status_publisher_task(agent, mqtt_client))
    ws_task = asyncio.create_task(websocket_task(agent, gnss_reader, mqtt_client))
    control_plane_task = asyncio.create_task(control_plane_watchdog_task(agent))

    active_auto_base_task = asyncio.create_task(auto_base_state_machine(agent, gnss_reader, mqtt_client))
    mem_guard_task = asyncio.create_task(memory_guard_task())

    try:
        await asyncio.gather(
            status_task,
            ws_task,
            control_plane_task,
            _run_startup_auto_base(active_auto_base_task),
            mem_guard_task,
        )
    except asyncio.CancelledError:
        logging.info("Tasks cancelled - shutting down gracefully")
        raise
    finally:
        await _cleanup_agent_runtime(agent, gnss_reader, mqtt_client)


async def _run_full_agent_mode(loop):
    agent, gnss_reader, mqtt_client = await _prepare_full_agent(loop)
    await _run_agent_background_tasks(agent, gnss_reader, mqtt_client)


async def main():
    global agent, MACHINE_SERIAL
    logging.info(f"Agent script path: {os.path.abspath(__file__)}")
    logging.info(f"Parser debug default: enabled={PARSER_DEBUG_ENABLED} interval={PARSER_DEBUG_INTERVAL_SECONDS}s")
    await _ensure_startup_lock()
    loop = asyncio.get_running_loop()

    if is_remote_locked():
        await _run_remote_lock_mode(loop)
    if not license_is_valid():
        await _run_license_wait_mode(loop)
        return
    await _run_full_agent_mode(loop)

# ==============================================================================
# === ENTRY POINT                                                           ===
# ==============================================================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Agent stopped by user (Ctrl+C).")
    except Exception as e:
        logging.critical(f"FATAL ERROR in main execution: {e}", exc_info=True)
        if IS_WINDOWS:
            input("A critical error occurred, press Enter to exit.")
    finally:
        remove_lock_file()
        logging.info("Final cleanup complete.")
