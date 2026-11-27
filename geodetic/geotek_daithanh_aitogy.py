# geotek_aitogy_working.py
RELAY_VERSION = "V2.4.2"
RECEIVER_PORT_PATTERN = r"(?:com(?:[1-9]|1[0-6])|uart(?:[1-9]|1[0-6])|usb)"
COMMAND_DELAY_PREFIX = b"$DELAY_"
NTRIP_ICY_SUCCESS = b"ICY 200"
NTRIP_HTTP_SUCCESS = b"200 OK"

import json
import logging
import socket
import threading
from typing import Any, NamedTuple
import math
import time
import base64
import select
import os
import re
from collections import OrderedDict
import ctypes
import gc
import hashlib
import psutil
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', force=True)

ACTIVE_WORKERS = {}
WORKER_MAP_LOCK = threading.RLock()
# Avoid a startup/reconnect storm when many stations use the same NTRIP caster.
# Healthy connections only hold this gate for the TCP handshake (normally a few
# milliseconds); a slow caster can no longer make every worker retry together.
SOURCE_CONNECT_LIMIT = threading.BoundedSemaphore(3)
MONITOR_CONNECT_LOCK = threading.Lock()
MONITOR_LAST_CONNECT_AT = 0.0
MONITOR_CONNECT_INTERVAL = 1.0

if os.name == 'nt':
    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        hStdIn = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(hStdIn, ctypes.byref(mode))
        mode.value &= ~0x0040 
        kernel32.SetConsoleMode(hStdIn, mode)
    except Exception:
        pass


try:
    threading.stack_size(65536)
except ValueError:
    pass

# ==============================================================================
# === MANAGERS ===
# ==============================================================================
class ConfigManager:
    def __init__(self, config_path):
        self.config_path = config_path
        self.data = {}
        self._lock = threading.RLock()
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                logging.info(f"✓ Loaded config from {self.config_path}")
            except Exception as e:
                logging.exception(f"Error loading config: {e}")
        else:
            logging.warning(f"Config file {self.config_path} not found. Using defaults.")
            self.data = {
                "mqtt": {
                    "broker": "aitogy.click",
                    "port": 1883,
                    "user": "mqttUser",
                    "pass": "MqttPassword123$%^"
                },
                "ntrip": {
                    "source_host": "rtkvietnam.vn",
                    "source_port": 1508,
                    "source_user": "aitogy123",
                    "source_pass": "aitogy123",
                    "target_host": "aitogy.com.vn",
                    "target_port": 2101,
                    "target_pass": "12345",
                    "version": 1, "reconnect_interval": 15
                },
                "station_list": [],
                "monitor_list": []
            }
    def save(self):
        try:
            with self._lock:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logging.exception(f"Error saving config: {e}")

    def update_mqtt_source_config(self, serial, updates):
        serial = str(serial or "").strip()
        if not serial or not isinstance(updates, dict):
            return False
        with self._lock:
            sources = self.data.setdefault("mqtt_source_list", [])
            for cfg in sources:
                cfg_serial = str(cfg.get("serial") or cfg.get("device_serial") or "").strip()
                if cfg_serial != serial:
                    continue
                for key, value in updates.items():
                    if value is not None:
                        cfg[key] = value
                for key in ("last_applied_config", "last_config_update", "active_mountpoint"):
                    cfg.pop(key, None)
                self.save()
                return True
        return False

    def update_relay_config(self, serial, updates):
        serial = str(serial or "").strip()
        if not serial or not isinstance(updates, dict):
            return False
        with self._lock:
            stations = self.data.setdefault("station_list", [])
            for cfg in stations:
                target_mp = str(cfg.get("target_mp") or "").strip()
                cfg_serial = f"RELAY_{target_mp.replace('.', '_')}"
                if cfg_serial != serial:
                    continue
                for key, value in updates.items():
                    if value is not None:
                        cfg[key] = value
                self.save()
                return True
        return False

class StatusManager:
    SAVE_INTERVAL_SECONDS = 10.0

    def __init__(self, db_path):
        self.db_path = db_path
        self.status_data = {"workers": {}, "system": {}, "last_update": ""}
        self._lock = threading.Lock()
        self._dirty = False
        self._last_save_monotonic = 0.0
        self._load()

    def _load(self):
        if not os.path.exists(self.db_path):
            return
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            if isinstance(existing, dict):
                self.status_data.update(existing)
        except Exception as e:
            logging.warning(f"Error loading status DB: {e}")
        self.status_data.setdefault("workers", {})
        self.status_data.setdefault("system", {})
        self.status_data.setdefault("runtime_config", {})
        self.status_data.setdefault("last_update", "")

    def update_worker_status(self, serial, status_dict):
        with self._lock:
            self.status_data["workers"][serial] = status_dict
            self.status_data["last_update"] = datetime.now().isoformat()
            self._dirty = True
            self._save_if_due()

    def get_runtime_config(self, serial):
        with self._lock:
            runtime = self.status_data.setdefault("runtime_config", {}).get(serial, {})
            return dict(runtime) if isinstance(runtime, dict) else {}

    def update_runtime_config(self, serial, runtime_config):
        with self._lock:
            self.status_data.setdefault("runtime_config", {})[serial] = runtime_config
            self.status_data["last_update"] = datetime.now().isoformat()
            self._dirty = True
            self._save(force=True)

    def update_system_status(self):
        with self._lock:
            try:
                self.status_data["system"] = {
                    "cpu_percent": psutil.cpu_percent(),
                    "memory_percent": psutil.virtual_memory().percent,
                    "disk_percent": psutil.disk_usage('/').percent,
                    "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat()
                }
                self.status_data["last_update"] = datetime.now().isoformat()
                self._dirty = True
                self._save_if_due()
            except Exception as e:
                logging.debug(f"System status update error: {e}")

    def flush(self):
        with self._lock:
            self._save(force=True)

    def _save_if_due(self):
        if time.monotonic() - self._last_save_monotonic >= self.SAVE_INTERVAL_SECONDS:
            self._save()

    def _save(self, force=False):
        if not self._dirty and not force:
            return
        tmp_path = self.db_path + ".tmp"
        last_error = None
        for _ in range(5):
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(self.status_data, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp_path, self.db_path)
                self._dirty = False
                self._last_save_monotonic = time.monotonic()
                return
            except Exception as e:
                last_error = e
                time.sleep(0.1)
        logging.error(f"Error saving status DB: {last_error}")

# Paths (Current directory of the script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "geotek_config.json")
DB_PATH = os.path.join(SCRIPT_DIR, "geotek_db.json")
LICENSE_PATH = os.path.join(SCRIPT_DIR, "license.key")

# Initialize Managers
config_mgr = ConfigManager(CONFIG_PATH)
status_mgr = StatusManager(DB_PATH)


def _safe_license_serial(serial):
    value = str(serial or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return safe or "default"


def get_license_path(serial=None):
    if serial:
        return os.path.join(SCRIPT_DIR, f"license.{_safe_license_serial(serial)}.key")
    return LICENSE_PATH


def has_license(serial=None):
    path = get_license_path(serial)
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def get_license_fingerprint(serial=None):
    try:
        path = get_license_path(serial)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
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


def save_license(serial, token):
    """Atomically replace one device token without terminating the relay."""
    value = str(token or "").strip()
    if not value:
        raise ValueError("license token is empty")
    path = get_license_path(serial)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(value)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp_path, path)
    return path


MQTT_SOURCE_CONFIG_FIELDS = {
    "name", "display_name",
    "setup_method", "duration", "lat", "lon", "alt", "accuracy", "altitude_reference", "gnss_options",
    "base_setup_method", "survey_in_duration", "survey_in_accuracy",
    "target_host", "target_port", "target_pass", "target_mp",
    "stream_on_demand", "stream_active",
    "server1_enabled", "server1_stream_on_demand", "server1_stream_active",
    "ntrip_version1", "username1", "serverhost1", "port1", "mountpoint1", "password1",
    "server2_enabled", "server2_stream_on_demand", "server2_stream_active",
    "ntrip_version2", "username2", "serverhost2", "port2", "mountpoint2", "password2",
    "command_host", "command_port", "command_pass", "command_mp",
    "rtcm_enabled", "rtcmntripversion1", "rtcmserver1", "rtcmport1",
    "rtcmmountpoint1", "rtcmusername1", "rtcmpassword1",
}


def _mqtt_source_persistable_config(values):
    if not isinstance(values, dict):
        return {}
    return {
        key: values[key] for key in MQTT_SOURCE_CONFIG_FIELDS
        if key in values and values[key] is not None
        and not (key in ("target_pass", "password1", "password2", "command_pass", "rtcmpassword1")
                 and values[key] == "")
    }


def config_value(section, key, env_keys, default):
    section_data = config_mgr.data.get(section, {})
    value = section_data.get(key)
    if value is not None and str(value).strip() != "":
        return value
    return default


# Global Settings (From Config)
MQTT_BROKER = config_value("mqtt", "broker", ("MQTT_BROKER", "MQTT_HOST"), "aitogy.click")
MQTT_PORT = int(config_value("mqtt", "port", ("MQTT_PORT",), 1883))
MQTT_USER = config_value("mqtt", "user", ("MQTT_USERNAME",), "mqttUser")
MQTT_PASS = config_value("mqtt", "pass", ("MQTT_PASSWORD",), "MqttPassword123$%^")

SOURCE_HOST = config_value("ntrip", "source_host", ("NTRIP_SOURCE_HOST",), "rtkvietnam.vn")
SOURCE_PORT = int(config_value("ntrip", "source_port", ("NTRIP_SOURCE_PORT",), 1508))
SOURCE_USER = config_value("ntrip", "source_user", ("NTRIP_SOURCE_USER",), "aitogy123")
SOURCE_PASS = config_value("ntrip", "source_pass", ("NTRIP_SOURCE_PASS",), "aitogy123")

TARGET_HOST = config_value("ntrip", "target_host", ("NTRIP_TARGET_HOST",), "aitogy.com.vn")
TARGET_PORT = int(config_value("ntrip", "target_port", ("NTRIP_TARGET_PORT",), 2101))
TARGET_PASS = config_value("ntrip", "target_pass", ("NTRIP_TARGET_PASS",), "12345")

NTRIP_VERSION = config_mgr.data.get("ntrip", {}).get("version", 1)
RECONNECT_INTERVAL = config_mgr.data.get("ntrip", {}).get("reconnect_interval", 15)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', force=True)

# ==============================================================================
# === HELPER ===
# ==============================================================================
def generate_gga(lat, lon, alt):
    now = datetime.now(timezone.utc).strftime("%H%M%S.00")
    
    def to_nmea(val, is_lat):
        abs_v = abs(val)
        deg = int(abs_v)
        minutes = (abs_v - deg) * 60
        direction = ("N" if val >= 0 else "S") if is_lat else ("E" if val >= 0 else "W")
        if is_lat:
            return f"{deg:02d}{minutes:08.5f},{direction}"
        else:
            return f"{deg:03d}{minutes:08.5f},{direction}"
    
    payload = f"GPGGA,{now},{to_nmea(lat, True)},{to_nmea(lon, False)},4,12,1.0,{alt:.1f},M,0.0,M,,"
    cs = 0
    for char in payload:
        cs ^= ord(char)
    return f"${payload}*{cs:02X}\r\n"

def read_http_header(sock, timeout=5):
    sock.settimeout(timeout)
    header_data = b""
    start_time = time.time()
    
    while True:
        if time.time() - start_time > timeout:
            preview = header_data[:160].decode(errors="ignore")
            raise Exception(f"NTRIP header timeout: {preview!r}")
        try:
            chunk = sock.recv(1024)
            if not chunk:
                raise Exception("Connection closed")
            header_data += chunk

            header_end = header_data.find(b"\r\n\r\n")
            if header_end >= 0:
                split_at = header_end + 4
                return header_data[:split_at], header_data[split_at:]

            line_end = header_data.find(b"\r\n")
            if line_end >= 0:
                first_line = header_data[:line_end].decode(errors="ignore")
                if first_line.startswith(NTRIP_ICY_SUCCESS.decode("ascii")):
                    blank_end = header_data.find(b"\r\n\r\n", line_end)
                    split_at = blank_end + 4 if blank_end >= 0 else line_end + 2
                    return header_data[:split_at], header_data[split_at:]
        except socket.timeout:
            raise Exception("Socket timeout")

def enable_tcp_keepalive(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass

def extract_rtcm3_frames(buffer):
    frames = []
    while len(buffer) > 0:
        if buffer[0] == 0xD3:
            if len(buffer) < 6:
                break
            if buffer[1] & 0xFC:
                del buffer[0]
                continue
            payload_len = ((buffer[1] & 0x03) << 8) | buffer[2]
            total_len = 3 + payload_len + 3
            if payload_len > 1023:
                del buffer[0]
                continue
            if len(buffer) < total_len:
                break
            
            frame = bytes(buffer[:total_len])
            expected_crc = int.from_bytes(frame[-3:], "big")
            if crc24q(frame[:-3]) == expected_crc:
                frames.append(frame)
                del buffer[:total_len]
            else:
                del buffer[0]
        elif buffer[0] == 0x24:  # '$' - NMEA sentence start
            end_idx = buffer.find(b'\n')
            if end_idx >= 0:
                del buffer[:end_idx + 1]
            else:
                if len(buffer) > 512:
                    del buffer[0]
                    continue
                break  # Wait for more data to complete NMEA sentence
        else:
            # Skip any other non-RTCM, non-NMEA byte
            del buffer[0]
    return frames

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
        
        system_map = {"GPS": "G", "GLONASS": "R", "Galileo": "E", "BeiDou": "C"}
        sys_code = system_map.get(nav_system)
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
            if cached and now_s - cached[0] < 120.0:
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
            return {k: {"interval": v["interval"], "count": v["count"]} for k, v in raw.items()}
        return {}

    def get_base_coordinates(self, serial: str) -> tuple[float, float, float] | None:
        base_pos = self._base_positions.get(serial)
        if not base_pos:
            return None
        lat, lon, alt = self._ecef2pos(*base_pos)
        return math.degrees(lat), math.degrees(lon), alt

    def decode_sync(self, serial: str, payload: bytes) -> dict[str, Any] | None:
        buffer = self._buffers.get(serial, b"") + payload
        packets: list[dict[str, Any]] = []
        satellites_by_id: dict[str, dict[str, Any]] = {}
        errors = 0

        while len(buffer) >= 3:
            start = buffer.find(b"\xD3")
            if start < 0:
                buffer = b""
                break
            if start > 0:
                buffer = buffer[start:]
            if len(buffer) < 3:
                break

            message_length = ((buffer[1] & 0x03) << 8) | buffer[2]
            packet_length = 3 + message_length + 3
            if len(buffer) < packet_length:
                break

            packet = buffer[:packet_length]
            buffer = buffer[packet_length:]

            expected_crc = int.from_bytes(packet[-3:], "big")
            if crc24q(packet[:-3]) != expected_crc:
                errors += 1
                continue

            payload_bytes = packet[3:-3]
            message_type = (payload_bytes[0] << 4) | (payload_bytes[1] >> 4) if len(payload_bytes) >= 2 else -1

            if message_type > 0:
                import time
                now = time.time()
                if not hasattr(self, '_stats_by_serial'):
                    self._stats_by_serial = {}
                if serial not in self._stats_by_serial:
                    self._stats_by_serial[serial] = {}
                msg_str = str(message_type)
                if msg_str not in self._stats_by_serial[serial]:
                    self._stats_by_serial[serial][msg_str] = {"last_seen": now, "interval": 0.0, "count": 1}
                else:
                    last_seen = self._stats_by_serial[serial][msg_str]["last_seen"]
                    self._stats_by_serial[serial][msg_str]["interval"] = round(now - last_seen, 2)
                    self._stats_by_serial[serial][msg_str]["last_seen"] = now
                    self._stats_by_serial[serial][msg_str]["count"] += 1

            try:
                if message_type in (1005, 1006):
                    self._decode_1005(payload_bytes, serial)
                    decoded = None
                elif message_type == 1019:
                    self._decode_1019(payload_bytes, serial)
                    decoded = None
                elif message_type == 1020:
                    self._decode_1020(payload_bytes, serial)
                    decoded = None
                elif message_type == 1042:
                    self._decode_1042(payload_bytes, serial)
                    decoded = None
                elif message_type == 1044:
                    self._decode_1044(payload_bytes, serial)
                    decoded = None
                elif message_type == 1045:
                    self._decode_1045(payload_bytes, serial)
                    decoded = None
                else:
                    decoded = self._decode_msm_payload(message_type, payload_bytes, serial)
            except (IndexError, ValueError):
                decoded = None
                errors += 1
            if decoded:
                packets.append(
                    {
                        "messageType": message_type,
                        "navSystem": decoded["navSystem"],
                        "msmLevel": decoded["msmLevel"],
                        "satelliteCount": len(decoded["satellites"]),
                    }
                )
                for sat in decoded["satellites"]:
                    satellites_by_id[sat["id"]] = sat
            else:
                packets.append({"messageType": message_type, "satelliteCount": 0})

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

        cell_mask: list[tuple[int, int]] = []
        for satellite_id in satellite_ids:
            for signal_id in signal_ids:
                if reader.read_uint(1):
                    cell_mask.append((satellite_id, signal_id))

        self._skip_msm_satellite_data(reader, msm_level, len(satellite_ids))
        signal_cnr = self._read_msm_signal_cnr(reader, msm_level, cell_mask)
        now_ms = int(time.time() * 1000)

        satellites: list[dict[str, Any]] = []
        for satellite_id in satellite_ids:
            best_signal_id, best_cnr = self._best_signal(signal_cnr, satellite_id)
            if best_cnr <= 0:
                continue
                
            sat_str_id = f"RTCM:{info.system}:{satellite_id}"
            
            elevation = 0.0
            azimuth = 0.0
            
            now = time.time()
            if serial not in self._azel_cache: self._azel_cache[serial] = {}
            cached = self._azel_cache[serial].get(sat_str_id)
            
            if cached and now - cached[0] < 120.0:
                azimuth, elevation = cached[1], cached[2]
            else:
                base_pos = self._base_positions.get(serial)
                eph = self._ephemeris_data.get(serial, {}).get(sat_str_id)
                
                if base_pos and eph:
                    if eph.get('type') == 'glonass':
                        sat_pos = self._glonass_eph2pos(eph, epoch_time_ms / 1000.0)
                    else:
                        sat_pos = self._eph2pos(eph, epoch_time_ms / 1000.0, info.nav_system)
                        
                    if sat_pos:
                        az, el = self._satazel(sat_pos, base_pos)
                        azimuth = az
                        elevation = el
                        self._azel_cache[serial][sat_str_id] = (now, azimuth, elevation)

            satellites.append(
                {
                    "id": sat_str_id,
                    "prn": satellite_id,
                    "system": info.system,
                    "talker": "RTCM",
                    "snr": best_cnr,
                    "cnr": best_cnr,
                    "signalId": best_signal_id,
                    "messageType": message_type,
                    "isTracking": True,
                    "elevation": elevation,
                    "azimuth": azimuth,
                    "lastSeen": now_ms,
                }
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


rtcm_signal_decoder = RTCMSignalDecoder()

def command_delay_seconds(command_bytes):
    if command_bytes.startswith(COMMAND_DELAY_PREFIX) and command_bytes.endswith(b"$"):
        try:
            return int(command_bytes.decode("ascii").split("_", 1)[1].strip("$")) / 1000.0
        except Exception:
            return 0.0
    lowered = command_bytes.strip().lower()
    if b"freset" in lowered:
        return 2.0
    if b"unlogall" in lowered:
        return 1.0
    if lowered.startswith(b"mode base"):
        return 2.0
    if lowered.startswith(b"gpgsv"):
        return 2.0
    if lowered.startswith(b"gpgga") or lowered.startswith(b"gpgsa"):
        return 0.2
    if b"saveconfig" in lowered:
        return 0.2
    return 0.2

def command_key(command_bytes):
    text = command_bytes.decode("ascii", errors="ignore").strip()
    if not text:
        return ""
    return text.split()[0].strip().upper()

def parse_command_response(payload):
    text = (payload or b"").decode("ascii", errors="ignore").strip()
    if "$command," not in text.lower() or ",response:" not in text.lower():
        return None

    start = text.lower().find("$command,")
    line = text[start:].splitlines()[0].strip()
    no_checksum = line.split("*", 1)[0]
    parts = no_checksum.split(",", 2)
    if len(parts) < 3:
        return None

    cmd = parts[1].strip()
    response_part = parts[2]
    marker = "response:"
    marker_index = response_part.lower().find(marker)
    if marker_index < 0:
        return None

    response = response_part[marker_index + len(marker):].strip()
    return {
        "command": cmd,
        "key": cmd.split()[0].strip().upper(),
        "response": response.upper(),
        "raw": line,
        "ts": time.time(),
    }

def nmea_coord_to_decimal(value, direction):
    if not value or not direction:
        return None
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - (degrees * 100)
    decimal = degrees + minutes / 60.0
    if direction in ("S", "W"):
        decimal *= -1
    return decimal

def parse_nmea_gga(payload):
    text = (payload or b"").decode("ascii", errors="ignore")
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line.startswith("$") or len(line) < 6:
            continue
        sentence = line.split("*", 1)[0]
        parts = sentence.split(",")
        if len(parts) < 10 or not parts[0].endswith("GGA"):
            continue
        if not all(parts[index] for index in (2, 3, 4, 5, 6, 7, 8, 9)):
            continue
        try:
            fix_quality = int(parts[6])
            fix_map = {
                0: "NO_FIX",
                1: "GPS",
                2: "DGPS",
                3: "PPS",
                4: "RTK_FIXED",
                5: "RTK_FLOAT",
                6: "ESTIMATED",
            }
            lat = nmea_coord_to_decimal(parts[2], parts[3])
            lon = nmea_coord_to_decimal(parts[4], parts[5])
            alt_msl = float(parts[9])
            geoid_sep = float(parts[11]) if len(parts) > 11 and parts[11] else 0.0
            alt = alt_msl + geoid_sep
            return {
                "type": "GGA",
                "timestamp_utc": parts[1],
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "altitude_msl": alt_msl,
                "altitude_ref": "ELLIPSOID",
                "fix_quality": fix_quality,
                "fix_status": fix_map.get(fix_quality, f"UNKNOWN_{fix_quality}"),
                "satellites": int(parts[7]),
                "hdop": float(parts[8]),
                "raw": line,
                "ts": time.time(),
            }
        except Exception:
            continue
    return None

def iter_nmea_sentences(payload):
    text = (payload or b"").decode("ascii", errors="ignore")
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("$") and len(line) >= 6:
            yield line

def parse_nmea_gsa(payload):
    active_sats = []
    latest_ts = time.time()
    last_line = ""
    for line in iter_nmea_sentences(payload):
        sentence = line.split("*", 1)[0]
        parts = sentence.split(",")
        if len(parts) < 18 or not parts[0].endswith("GSA"):
            continue
        try:
            for value in parts[3:15]:
                if value:
                    active_sats.append(int(value))
            last_line = line
        except Exception:
            continue
    if active_sats:
        return {
            "type": "GSA",
            "active_sats": list(set(active_sats)),
            "ts": latest_ts,
            "raw": last_line
        }
    return None

def parse_nmea_gsv(payload):
    system_map = {
        "GP": "us",
        "GL": "ru",
        "GA": "eu",
        "GB": "cn",
        "BD": "cn",
        "GQ": "jp",
        "QZ": "jp",
        "GN": "un",
    }
    satellites = []
    for line in iter_nmea_sentences(payload):
        sentence = line.split("*", 1)[0]
        parts = sentence.split(",")
        if len(parts) < 4 or not parts[0].endswith("GSV"):
            continue
        talker_id = parts[0][1:3]
        system = system_map.get(talker_id, "un")
        fields = parts[4:]
        for index in range(0, len(fields), 4):
            chunk = [c.strip() for c in fields[index:index + 4]]
            if len(chunk) < 4 or not chunk[0]:
                continue
            try:
                prn = int(chunk[0])
                if talker_id == 'GP':
                    if prn >= 65 and prn <= 96:
                        system = 'ru'
                        prn -= 64
                    elif prn >= 193 and prn <= 202:
                        system = 'jp'
                        prn -= 192
                    elif prn > 100:
                        continue # Completely ignore PRNs like 1212, 254
                elif talker_id == 'GL' and prn > 64:
                    prn -= 64
                if prn > 200:
                    continue
                    
                satellites.append({
                    "prn": prn,
                    "system": system,
                    "elevation": int(chunk[1]) if chunk[1] else 0,
                    "azimuth": int(chunk[2]) if chunk[2] else 0,
                    "snr": int(chunk[3]) if chunk[3] else 0,
                    "isTracking": False,
                    "source": "NMEA_GSV",
                    "last_seen": time.time()
                })
            except Exception:
                continue
    return satellites

NMEA_COMMAND_NAMES = {
    "gpgga", "gpgsa", "gpgsv",
    "glgga", "glgsa", "glgsv",
    "gagga", "gagsa", "gagsv",
    "bdgga", "bdgsa", "bdgsv",
}
NMEA_GGA_COMMAND_NAMES = {"gpgga", "glgga", "gagga", "bdgga"}
NMEA_SAT_COMMAND_NAMES = {"gpgsa", "gpgsv", "glgsa", "glgsv", "gagsa", "gagsv", "bdgsa", "bdgsv"}

def rewrite_command_port(command_bytes, nmea_port=None, rtcm_port=None, nmea_gga_port=None, nmea_sat_port=None):
    text = command_bytes.decode("ascii", errors="ignore")
    stripped = text.strip()
    if not stripped:
        return command_bytes

    parts = stripped.split()
    if len(parts) < 3:
        return command_bytes

    command = parts[0].lower()
    target_port = None
    if command in NMEA_GGA_COMMAND_NAMES and nmea_gga_port:
        target_port = str(nmea_gga_port).strip()
    elif command in NMEA_SAT_COMMAND_NAMES and nmea_sat_port:
        target_port = str(nmea_sat_port).strip()
    elif command in NMEA_COMMAND_NAMES and nmea_port:
        target_port = str(nmea_port).strip()
    elif command.startswith("rtcm") and rtcm_port:
        target_port = str(rtcm_port).strip()

    if not target_port:
        return command_bytes

    if not re.fullmatch(RECEIVER_PORT_PATTERN, target_port, flags=re.IGNORECASE):
        logging.warning("Ignored invalid receiver port configuration")
        return command_bytes

    original_port = parts[1]
    if original_port.lower() == target_port.lower():
        return command_bytes

    parts[1] = target_port
    rewritten = " ".join(parts) + "\r\n"
    logging.info(f"Rewrite command port: {stripped} -> {rewritten.strip()}")
    return rewritten.encode("ascii")

def _first_value(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None

def _as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

def _as_int(value, default=60):
    try:
        return int(value)
    except Exception:
        return default

def _extract_remote_payload(original_config):
    if not isinstance(original_config, dict):
        return {}, {}
    payload = original_config.get("payload", original_config)
    if not isinstance(payload, dict):
        return {}, {}
    params = payload.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return payload, params

def normalize_lte_gnss_options(options=None):
    raw = options if isinstance(options, dict) else {}
    mode = str(raw.get("output_mode") or "rtcm_only").strip().lower()
    if mode not in ("rtcm_only", "diagnostics_rtcm"):
        raise ValueError("Unsupported LTE output mode")
    level = str(raw.get("msm_level", "both")).strip().lower()
    if level not in ("4", "7", "both"):
        raise ValueError("Unsupported LTE MSM level")
    selected = raw.get("constellations") or {}
    constellations = {key: bool(selected.get(key, True))
                      for key in ("gps", "glo", "gal", "bds", "qzss", "sbas")}
    if not any(constellations[key] for key in ("gps", "glo", "gal", "bds", "qzss")):
        constellations["gps"] = True
    return {"output_mode": mode, "msm_level": "both" if level == "both" else int(level),
            "constellations": constellations}


def build_lte_unicore_commands(original_config=None, runtime_cfg=None):
    """
    Local command builder for every JSON-configured Geotek LTE source.

    The dashboard can still send encoded commands, but this relay is the device
    boundary for geotek_config.json sources. Rebuild the command list here so all
    LTE units use their configured receiver port and requested output profile.
    """
    runtime_cfg = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    payload, params = _extract_remote_payload(original_config)
    coords = params.get("coords", {}) if isinstance(params.get("coords"), dict) else {}

    method = str(_first_value(
        payload.get("method"),
        payload.get("base_setup_method"),
        params.get("base_setup_method"),
        runtime_cfg.get("setup_method"),
        "SURVEY_IN",
    )).upper()
    duration = _as_int(_first_value(
        params.get("survey_in_duration"),
        params.get("duration"),
        runtime_cfg.get("duration"),
        60,
    ), 60)

    position = []
    for key, lower, upper in (('lat', -90, 90), ('lon', -180, 180), ('alt', -12000, 100000)):
        text = str(_first_value(coords.get(key), params.get(key), runtime_cfg.get(key), 0.0)).strip().replace(',', '.')
        if method == 'FIXED_LLA' and (not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text) or not lower <= float(text) <= upper):
            raise ValueError(f'Invalid fixed position {key}')
        position.append(text)
    lat, lon, alt = position

    rtcm_port = str(_first_value(runtime_cfg.get("rtcm_port"), runtime_cfg.get("sensor_port"), "com2")).strip() or "com2"
    if not re.fullmatch(RECEIVER_PORT_PATTERN, rtcm_port, flags=re.IGNORECASE):
        raise ValueError("Invalid Unicore RTCM port")
    commands = [
        b"FRESET\r\n",
        b"$DELAY_5000$",
        b"unlogall\r\n",
        b"$DELAY_1000$",
    ]

    if method == "FIXED_LLA":
        # A new fixed-position payload without metadata retains legacy semantics;
        # on restart, a saved explicit reference must survive FRESET.
        reference = params.get("altitude_reference", payload.get("altitude_reference"))
        if not params and not payload:
            reference = runtime_cfg.get("altitude_reference")
        if reference not in (None, "LEGACY", "ELLIPSOID"):
            raise ValueError("Unsupported altitude reference")
        if reference == "ELLIPSOID":
            commands.append(b"config undulation 0.0\r\n")
        commands.extend([
            f"mode base {lat} {lon} {alt}\r\n".encode("ascii"),
            b"$DELAY_2000$",
        ])
    else:
        commands.extend([
            f"mode base time {duration}\r\n".encode("ascii"),
            b"$DELAY_2000$",
        ])

    options = normalize_lte_gnss_options(_first_value(
        params.get("gnss_options"), payload.get("gnss_options"), runtime_cfg.get("gnss_options")))
    selected = options["constellations"]
    if options["output_mode"] == "diagnostics_rtcm":
        nmea_port = str(runtime_cfg.get("nmea_port") or rtcm_port).strip()
        if not re.fullmatch(RECEIVER_PORT_PATTERN, nmea_port, flags=re.IGNORECASE):
            raise ValueError("Invalid Unicore NMEA port")
        for system, messages in {
            "gps": ("gpgga", "gpgsa", "gpgsv", "gpgst"),
            "glo": ("glgga", "glgsa", "glgsv"),
            "gal": ("gagga", "gagsa", "gagsv"),
            "bds": ("bdgga", "bdgsa", "bdgsv"),
        }.items():
            if selected[system]:
                for message in messages:
                    commands.extend([f"{message} {nmea_port} 1\r\n".encode("ascii"), b"$DELAY_200$"])
    messages = ["1006", "1033"]
    for system, msm4, msm7, ephemeris in (
        ("gps", "1074", "1077", "1019"), ("glo", "1084", "1087", "1020"),
        ("gal", "1094", "1097", "1045"), ("bds", "1124", "1127", "1042"),
        ("qzss", "1114", "1117", "1044"),
    ):
        if selected[system]:
            if options["msm_level"] in (4, "both"):
                messages.append(msm4)
            if options["msm_level"] in (7, "both"):
                messages.append(msm7)
            messages.append(ephemeris)
    for msg_id in messages:
        commands.append(f"rtcm{msg_id} {rtcm_port} 1\r\n".encode("ascii"))
        commands.append(b"$DELAY_200$")

    commands.append(b"saveconfig\r\n")
    return commands

# ==============================================================================
# === RELAY WORKER ===
# ==============================================================================
class RelayWorker(threading.Thread):
    def __init__(self, station_config, mqtt_client):
        super().__init__()
        self.cfg = station_config
        self.mqtt_client = mqtt_client
        self.daemon = True
        self.stop_event = threading.Event()
        
        self.serial = f"RELAY_{self.cfg['target_mp'].replace('.', '_')}"
        self.topic_status = f"pi/devices/{self.serial}/status"
        
        self.is_connected = False
        self.bytes_sent = 0
        self.bytes_received = 0
        self.last_stat_update = time.time()
        self.current_bps = 0
        self.current_source_bps = 0
        self.source_connected = False
        self._last_disconnect_reason = ""
        self._source_failure_count = 0
        
        self.name = f"Relay-{self.cfg['target_mp']}"
        self.desired_active = bool(self.cfg.get("stream_active", True))
        self.station_enabled = bool(self.cfg.get("station_enabled", True))
        self.wakeup_event = threading.Event()
        self.socket_lock = threading.Lock()
        self._source_sock = None
        self._target_sock = None
        self.satellites = {}
        self.raw_publish_buffer = bytearray()
        self.last_raw_publish = time.time()

    def _source_host(self):
        return self.cfg.get('source_host', SOURCE_HOST)

    def _source_port(self):
        return int(self.cfg.get('source_port', SOURCE_PORT))

    def _source_user(self):
        return self.cfg.get('source_user', SOURCE_USER)

    def _source_pass(self):
        env_name = str(self.cfg.get('source_pass_env') or '').strip()
        if env_name:
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value
        return self.cfg.get('source_pass', SOURCE_PASS)

    def _target_host(self):
        return self.cfg.get('target_host', TARGET_HOST)

    def _target_port(self):
        return int(self.cfg.get('target_port', TARGET_PORT))

    def _target_pass(self):
        return self.cfg.get('target_pass', TARGET_PASS)

    def _source_retry_delay(self):
        self._source_failure_count += 1
        configured = max(2, int(RECONNECT_INTERVAL))
        backoff = min(60, max(configured, 2 ** min(self._source_failure_count, 6)))
        # Stable per-station jitter prevents all workers waking on the same tick.
        jitter = hashlib.sha256(self.serial.encode("utf-8")).digest()[0] % 4
        return backoff + jitter

    def _keep_target_connected(self):
        return True

    def _stream_on_demand(self):
        return bool(self.cfg.get("stream_on_demand", True))

    def _source_send_gga(self):
        return bool(self.cfg.get("source_send_gga", False))
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['target_mp']}] {msg}")
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            pass # Vẫn cập nhật DB cục bộ dù mất MQTT
        
        source_has_data = bool(self.station_enabled and self.source_connected and self.current_source_bps > 0)
        output_connected = bool(self.station_enabled and self.is_connected)
        output_bps = self.current_bps if self.station_enabled and self.desired_active else 0
        if not source_has_data:
            status_value = "offline"
        elif not self.desired_active:
            status_value = "sleep"
        elif output_connected and output_bps > 0:
            status_value = "online"
        else:
            status_value = "offline"
        now = time.time()
        satellites = {
            key: sat for key, sat in self.satellites.items()
            if now - float(sat.get("last_seen", 0)) <= 30
        }
        self.satellites = satellites

        status = {
            "serial": self.serial,
            "rtcm_stats": globals().get("rtcm_signal_decoder").get_stats(self.serial) if globals().get("rtcm_signal_decoder") else {},
            "name": self.cfg['target_mp'],
            "status": status_value,
            "version": RELAY_VERSION,
            "agent_token_fingerprint": get_license_fingerprint(self.serial),
            "timestamp": round(time.time(), 3),
            "base_config": {
                "base_setup_method": "FIXED_LLA",
                "coords": {
                    "lat": self.cfg['lat'],
                    "lon": self.cfg['lon'],
                    "alt": self.cfg['alt']
                }
            },
            "ntrip_connected": output_connected,
            "source_connected": bool(self.source_connected),
            "ntrip_stats": {"server1_bps": output_bps, "rtcm_input_bps": self.current_source_bps},
            "ntrip_status": {"server1": {"connected": output_connected, "bps": output_bps}},
            "nmea_data": {
                "gga": None,
                "gsa": None,
                "satellites": satellites,
                "base_coords": {
                    "lat": self.cfg['lat'],
                    "lon": self.cfg['lon'],
                    "alt": self.cfg['alt']
                }
            },
            "gnss_stats": {
                "satellites": len([sat for sat in satellites.values() if sat.get("snr", 0) > 0])
            },
            "service_config": {
                "server1_enabled": True,
                "mountpoint1": self.cfg['target_mp'],
                "serverhost1": self._target_host(),
                "port1": self._target_port(),
                "relay_source": {
                    "host": self._source_host(),
                    "port": self._source_port(),
                    "username": self._source_user(),
                    "password": self._source_pass(),
                    "mountpoint": self.cfg['source_mp'],
                },
                "stream_on_demand": self._stream_on_demand(),
                "server1_stream_on_demand": self._stream_on_demand(),
                "server1_stream_active": bool(self.desired_active),
                "stream_active": bool(self.desired_active),
                "active_mountpoint": self.cfg['target_mp'] if self.desired_active else None,
                "agent_station_power_control": True,
                "agent_station_enabled": self.station_enabled,
            },
            "source_mountpoint": self.cfg['source_mp'],
            "rtk_fix_status": "FIXED",
            "detected_chip_type": "RELAY",
            "is_provisioned": has_license(self.serial)
        }
        status["service_config"] = _status_safe_config(status.get("service_config"))
        
        try:
            if self.mqtt_client and self.mqtt_client.is_connected():
                self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
            # Cập nhật DB cục bộ
            status_mgr.update_worker_status(self.serial, status)
        except Exception as e:
            logging.debug(f"Error publishing/saving status: {e}")

    def set_stream_active(self, active: bool, mountpoint=None):
        next_active = bool(active)
        state_changed = self.desired_active != next_active
        self.desired_active = next_active
        self.cfg["stream_active"] = next_active
        self.cfg["server1_stream_active"] = next_active
        if state_changed:
            config_mgr.update_relay_config(
                self.serial,
                {
                    "stream_active": next_active,
                    "server1_stream_active": next_active,
                },
            )
        if not self.desired_active:
            self.bytes_sent = 0
            self.current_bps = 0
        self.wakeup_event.set()
        self.publish_status()

    def set_station_active(self, active: bool):
        self.station_enabled = bool(active)
        self.cfg["station_enabled"] = self.station_enabled
        config_mgr.update_relay_config(self.serial, {"station_enabled": self.station_enabled})
        if not self.station_enabled:
            self._close_live_sockets()
            self.is_connected = False
            self.source_connected = False
            self.current_bps = 0
            self.current_source_bps = 0
        self.wakeup_event.set()
        self.publish_status()

    def _persist_remote_config(self, original_config):
        if not isinstance(original_config, dict):
            return

        payload = original_config.get("payload", original_config)
        service_config = payload.get("service_config", {}) if isinstance(payload.get("service_config"), dict) else {}
        if not service_config:
            service_config = payload

        before_target = (
            self.cfg.get("target_host"),
            str(self.cfg.get("target_port") or ""),
            self.cfg.get("target_pass"),
            self.cfg.get("target_mp"),
        )
        before_source = (
            self._source_host(), self._source_port(), self._source_user(),
            self._source_pass(), self.cfg.get("source_mp"),
        )

        updates = {}
        
        # Parse stream_active
        stream_active = service_config.get("server1_stream_active")
        if stream_active is None:
            stream_active = service_config.get("stream_active")
        if stream_active is not None:
            updates["stream_active"] = bool(stream_active)
            self.desired_active = bool(stream_active)

        target_aliases = {
            "target_host": ("target_host", "serverhost1", "host", "caster_host"),
            "target_port": ("target_port", "serverport1", "port1", "port", "caster_port"),
            "target_pass": ("target_pass", "password1", "password", "pass", "caster_password"),
            "target_mp": ("target_mp", "mountpoint", "mountpoint1", "active_mountpoint"),
        }
        for target_key, aliases in target_aliases.items():
            for alias in aliases:
                val = service_config.get(alias)
                if val not in (None, ""):
                    updates[target_key] = val
                    break

        source_config = service_config.get("relay_source")
        if isinstance(source_config, dict):
            source_aliases = {
                "source_host": ("host", "source_host"),
                "source_port": ("port", "source_port"),
                "source_user": ("username", "user", "source_user"),
                "source_pass": ("password", "pass", "source_pass"),
                "source_mp": ("mountpoint", "source_mp"),
            }
            for target_key, aliases in source_aliases.items():
                for alias in aliases:
                    if alias in source_config and source_config[alias] is not None:
                        if target_key == "source_pass" and source_config[alias] == "":
                            continue
                        updates[target_key] = source_config[alias]
                        break

        if updates:
            self.cfg.update(updates)
            config_mgr.update_relay_config(self.serial, updates)
            safe_updates = {
                key: "***" if "pass" in key.lower() else value
                for key, value in updates.items()
            }
            self.log("INFO", f"Saved remote config into geotek_config.json: {safe_updates}")

        after_target = (
            self.cfg.get("target_host"),
            str(self.cfg.get("target_port") or ""),
            self.cfg.get("target_pass"),
            self.cfg.get("target_mp"),
        )

        if after_target != before_target:
            self.log("INFO", "Runtime target changed; reconnecting TARGET socket")
            self._close_target_socket()

        after_source = (
            self._source_host(), self._source_port(), self._source_user(),
            self._source_pass(), self.cfg.get("source_mp"),
        )
        if after_source != before_source:
            self.log("INFO", "Runtime source changed; reconnecting SOURCE socket")
            self._close_source_socket()

        self.wakeup_event.set()

    def _close_source_socket(self):
        with self.socket_lock:
            if self._source_sock:
                try:
                    self._source_sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._source_sock.close()
                except Exception:
                    pass
            self._source_sock = None

    def _close_target_socket(self):
        with self.socket_lock:
            if self._target_sock:
                try:
                    self._target_sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._target_sock.close()
                except Exception:
                    pass
            self._target_sock = None

    def _close_live_sockets(self):
        with self.socket_lock:
            for sock in (self._source_sock, self._target_sock):
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    try:
                        sock.close()
                    except Exception:
                        pass
            self._source_sock = None
            self._target_sock = None
    
    def run(self):
        while not self.stop_event.is_set():
            if not self.station_enabled:
                self.is_connected = False
                self.source_connected = False
                self.current_bps = 0
                self.current_source_bps = 0
                self.publish_status()
                if self.wakeup_event.wait(30):
                    self.wakeup_event.clear()
                continue

            source_sock = None
            target_sock = None
            try:
                # Kết nối SOURCE khi trạm bật, kể cả khi stream output đang ở chế độ ngủ.
                source_host = self._source_host()
                source_port = self._source_port()
                source_user = self._source_user()
                source_pass = self._source_pass()
                target_host = self._target_host()
                target_port = self._target_port()
                target_pass = self._target_pass()
                # Kết nối SOURCE để lấy dữ liệu khi bật nguồn trạm.
                self.log("INFO", f"Connecting SOURCE: {source_host}:{source_port}/{self.cfg['source_mp']}")
                with SOURCE_CONNECT_LIMIT:
                    if self.stop_event.is_set() or not self.station_enabled:
                        continue
                    source_sock = socket.create_connection((source_host, source_port), timeout=10)
                enable_tcp_keepalive(source_sock)
                with self.socket_lock:
                    self._source_sock = source_sock
                if not self.station_enabled:
                    continue
                
                auth = base64.b64encode(f"{source_user}:{source_pass}".encode()).decode()
                source_request = (
                    f"GET /{self.cfg['source_mp']} HTTP/1.1\r\n"
                    f"Host: {source_host}\r\n"
                    f"Authorization: Basic {auth}\r\n"
                    f"User-Agent: NTRIP Relay/1.0\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                )
                source_sock.sendall(source_request.encode())
                
                src_header, leftover = read_http_header(source_sock)
                if NTRIP_HTTP_SUCCESS not in src_header and NTRIP_ICY_SUCCESS not in src_header:
                    raise Exception("Source rejected")
                self.source_connected = True
                
                gga = generate_gga(self.cfg['lat'], self.cfg['lon'], self.cfg['alt'])
                source_send_gga = self._source_send_gga()
                if source_send_gga:
                    source_sock.sendall(gga.encode())
                
                self.log("SUCCESS", "✓ Source connected")
                
                # A 200/ICY response only authenticates the source. Do not mark
                # the relay output online or open TARGET until real RTCM arrives.
                self.is_connected = False
                self.publish_status()
                
                last_gga = time.time()
                self.bytes_sent = 0
                self.bytes_received = 0
                self.last_stat_update = time.time()
                rtcm_buffer = bytearray()
                source_has_rtcm = False
                if leftover:
                    rtcm_buffer.extend(leftover)
                    leftover = None
                
                source_sock.settimeout(5.0)
                
                while not self.stop_event.is_set():
                    if not self.station_enabled:
                        break
                    # 1. Kết nối TARGET khi stream đang bật hoặc cấu hình giữ kết nối standby.
                    if self.station_enabled and source_has_rtcm and target_sock is None and (self.desired_active or self._keep_target_connected()):
                        self.log("INFO", f"Connecting TARGET: {target_host}:{target_port}/{self.cfg['target_mp']} (v1.0)")
                        try:
                            target_sock = socket.create_connection((target_host, target_port), timeout=10)
                            enable_tcp_keepalive(target_sock)
                            with self.socket_lock:
                                self._target_sock = target_sock
                            if not self.station_enabled:
                                break
                            
                            target_request = (
                                f"SOURCE {target_pass} /{self.cfg['target_mp']}\r\n"
                                f"Source-Agent: NTRIP NtripSource/1.0\r\n\r\n"
                            )
                            target_sock.sendall(target_request.encode())
                            response, _ = read_http_header(target_sock, timeout=10)
                            if not (NTRIP_ICY_SUCCESS in response or NTRIP_HTTP_SUCCESS in response):
                                raise Exception(f"Target rejected: {response.decode(errors='ignore')[:100]}")
                            target_sock.settimeout(None)
                            self.is_connected = True
                            
                            target_mode = "ACTIVE" if self.desired_active else "SLEEP-STANDBY"
                            self.log("SUCCESS", f"RELAY {target_mode}: {self.cfg['source_mp']} -> {self.cfg['target_mp']}")
                        except Exception as e:
                            self.log("WARNING", f"Target connection failed: {e}")
                            if target_sock:
                                try: target_sock.close()
                                except Exception: pass
                                target_sock = None
                                with self.socket_lock:
                                    self._target_sock = None
                            self.is_connected = False
                    
                    # 2. NGẮT TARGET NẾU TRẠM TẮT (desired_active = False)
                    if not self.desired_active and target_sock is not None and not self._keep_target_connected():
                        self.log("INFO", "Stream deactivated -> closing TARGET socket only.")
                        try: target_sock.shutdown(socket.SHUT_RDWR)
                        except Exception: pass
                        try: target_sock.close()
                        except Exception: pass
                        target_sock = None
                        with self.socket_lock:
                            self._target_sock = None
                        self.is_connected = False
                        
                    if source_send_gga and time.time() - last_gga > 10:
                        try:
                            source_sock.sendall(gga.encode())
                            last_gga = time.time()
                        except Exception as e:
                            self._last_disconnect_reason = "source_gga_send_failed"
                            self.log("INFO", f"Source GGA send failed; reconnecting source socket: {e}")
                            break
                    
                    # 3. LUÔN ĐỌC RAW DATA TỪ SOURCE
                    try:
                        data = source_sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError as e:
                        if getattr(e, "winerror", None) in (10053, 10054) or getattr(e, "errno", None) in (10053, 10054):
                            self._last_disconnect_reason = "source_remote_close"
                            self.log("INFO", f"Source remote closed socket; reconnecting source socket: {e}")
                        else:
                            self._last_disconnect_reason = "source_socket_error"
                            self.log("WARNING", f"Source socket error: {e}")
                        break
                    except Exception as e:
                        self._last_disconnect_reason = "source_data_error"
                        self.log("WARNING", f"Data error: {e}")
                        break

                    if not data:
                        self._last_disconnect_reason = "source_eof"
                        self.log("INFO", "Source EOF; reconnecting source socket.")
                        break

                    self.bytes_received += len(data)
                    
                    # if self.mqtt_client and self.mqtt_client.is_connected():
                    #     self.raw_publish_buffer.extend(data)
                    #     if time.time() - self.last_raw_publish >= 1.0:
                    #         try:
                    #             self.mqtt_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                    #         except Exception as e:
                    #             self.log("WARNING", f"Failed to publish raw_data: {e}")
                    #         self.raw_publish_buffer.clear()
                    #         self.last_raw_publish = time.time()

                    rtcm_buffer.extend(data)
                    frames = extract_rtcm3_frames(rtcm_buffer)
                    if frames:
                        source_has_rtcm = True
                        self._source_failure_count = 0
                    for frame in frames:
                        decoded = rtcm_signal_decoder.decode_sync(self.serial, frame)
                        sats = decoded.get("satellites", []) if decoded else []
                        for sat in sats:
                            sat_key = f"{sat.get('system', 'un')}:{sat.get('prn')}"
                            existing = self.satellites.get(sat_key, {})
                            existing.update({k: v for k, v in sat.items() if v or k not in existing})
                            existing["last_seen"] = time.time()
                            self.satellites[sat_key] = existing

                    # 4. CHỈ GỬI ĐI NẾU ĐANG BẬT TARGET
                    if self.station_enabled and self.desired_active and target_sock is not None and frames:
                        for frame in frames:
                            try:
                                target_sock.sendall(frame)
                                self.bytes_sent += len(frame)
                            except Exception as e:
                                self.log("WARNING", f"Error sending RTCM frame to target: {e}")
                                try: target_sock.close()
                                except Exception: pass
                                target_sock = None
                                with self.socket_lock:
                                    self._target_sock = None
                                self.is_connected = False
                                break

                    # Tính BPS thật sự
                    now = time.time()
                    if now - self.last_stat_update >= 1.0:
                        self.current_bps = int(self.bytes_sent / (now - self.last_stat_update))
                        self.current_source_bps = int(self.bytes_received / (now - self.last_stat_update))
                        self.bytes_sent = 0
                        self.bytes_received = 0
                        self.last_stat_update = now
                        self.publish_status()
                
                if self._last_disconnect_reason.startswith("source_"):
                    reconnect_wait = self._source_retry_delay()
                else:
                    reconnect_wait = RECONNECT_INTERVAL
                self.log("INFO", f"Source loop ended, reconnecting in {reconnect_wait}s...")
                self.wakeup_event.wait(reconnect_wait)
                self.wakeup_event.clear()
                
            except Exception as e:
                self.log("WARNING", f"Connection error: {e}")
                reconnect_wait = self._source_retry_delay()
                self.log("INFO", f"Source connection retry scheduled in {reconnect_wait}s")
                self.wakeup_event.wait(reconnect_wait)
                self.wakeup_event.clear()
                
            finally:
                self.is_connected = False
                self.source_connected = False
                self.current_bps = 0
                self.current_source_bps = 0
                if source_sock:
                    try: source_sock.close()
                    except Exception: pass
                if target_sock:
                    try: target_sock.close()
                    except Exception: pass
                with self.socket_lock:
                    self._source_sock = None
                    self._target_sock = None
                
                self.publish_status()

# ==============================================================================
# === MONITOR WORKER (Dành cho trạm đã có sẵn trên server) ===
# ==============================================================================
class MonitorWorker(threading.Thread):
    def __init__(self, station_config, mqtt_client):
        super().__init__()
        self.cfg = dict(station_config)
        self.cfg['mountpoint'] = str(self.cfg.get('mountpoint') or '').strip().lstrip('/')
        self.is_prefix7 = self.cfg['mountpoint'].startswith('7')
        self.host = self.cfg.get('host') or TARGET_HOST
        self.port = int(self.cfg.get('port') or TARGET_PORT)
        self.retry_seconds = max(RECONNECT_INTERVAL, 60) if self.is_prefix7 else RECONNECT_INTERVAL
        self.mqtt_client = mqtt_client
        self.daemon = True
        self.stop_event = threading.Event()
        
        configured_serial = str(self.cfg.get('serial') or self.cfg.get('device_serial') or '').strip()
        if configured_serial:
            self.serial = configured_serial
        elif str(self.cfg.get('mountpoint', '')).lower().startswith('esp_'):
            self.serial = self.cfg['mountpoint']
        elif self.is_prefix7:
            self.serial = f"MONITOR_{self.cfg['mountpoint']}"
        else:
            self.serial = f"MONITOR_{self.cfg['mountpoint'].replace('.', '_')}"
        if self.is_prefix7:
            decoder = globals().get('rtcm_signal_decoder')
            if decoder:
                for cache_name in ('_buffers', '_base_positions', '_ephemeris_data', '_azel_cache', '_stats_by_serial'):
                    getattr(decoder, cache_name, {}).pop(self.serial, None)
        self.topic_status = f"pi/devices/{self.serial}/status"
        self.satellites = {}
        
        self.is_connected = False
        self.bytes_received = 0
        self.last_stat_update = time.time()
        self.last_data_received_at = 0.0
        self.last_rtcm_received_at = 0.0
        self.max_no_data_seconds = 12.0
        self.current_bps = 0
        self.last_gga_send_time = 0.0
        self.gga_send_interval = 5.0
        
        self.name = f"Monitor-{self.cfg['mountpoint']}"
        self.desired_active = True
        self.wakeup_event = threading.Event()
        self.socket_lock = threading.Lock()
        self._sock = None
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['mountpoint']}] {msg}")

    def _send_rover_gga(self, sock: socket.socket) -> None:
        if self.is_prefix7 or self.serial.lower().startswith('esp_'):
            return  # Fixed-base decoding must not advertise a synthetic rover position.
        decoder = globals().get("rtcm_signal_decoder")
        base_coords = decoder.get_base_coordinates(self.serial) if decoder else None
        if base_coords:
            lat, lon, alt = base_coords
        else:
            lat = float(self.cfg.get('lat') or 0.0)
            lon = float(self.cfg.get('lon') or 0.0)
            alt = float(self.cfg.get('alt') or 0.0)
            if not (lat or lon):
                lat, lon, alt = 21.0285, 105.8542, 10.0
        gga = generate_gga(lat, lon, alt)
        sock.sendall(gga.encode())
        self.last_gga_send_time = time.time()

    def stop(self):
        self.stop_event.set()
        self.wakeup_event.set()
        self._close_live_socket()
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return

        now = time.time()
        if self.is_prefix7:
            recent_rtcm = self.last_rtcm_received_at > 0 and 0 <= now - self.last_rtcm_received_at < self.max_no_data_seconds
            current_status = "online" if self.is_connected and recent_rtcm else "offline"
        elif self.is_connected:
            current_status = "online" if self.current_bps > 0 else "sleep"
        else:
            current_status = "offline"
        satellites = {
            key: sat for key, sat in self.satellites.items()
            if now - float(sat.get("last_seen", 0)) <= 30
        }
        self.satellites = satellites
        satellite_count = len([sat for sat in satellites.values() if sat.get("snr", 0) > 0])
        decoder = globals().get("rtcm_signal_decoder")
        rtcm_stats = decoder.get_stats(self.serial) if decoder else {}
        
        decoded_coords = decoder.get_base_coordinates(self.serial) if decoder else None
        coords = dict(zip(('lat', 'lon', 'alt'), decoded_coords)) if decoded_coords else {
            'lat': self.cfg.get('lat', 0.0), 'lon': self.cfg.get('lon', 0.0), 'alt': self.cfg.get('alt', 0.0)
        }
        if self.is_prefix7 and not decoded_coords:
            coords = {}  # GGA positions are not decoded RTCM observations.
        # Physical ESP firmware owns presence, configuration and license state.
        # The pull worker contributes decoded observations only.
        if self.serial.lower().startswith("esp_") and not self.is_prefix7:
            observation = {
                "type": "rtcm_skyview_calculated",
                "satellites": list(satellites.values()),
                "packets": rtcm_stats,
                "timestamp": round(now, 3),
            }
            if decoded_coords:
                observation["base_coords"] = coords
            if rtcm_stats or satellites or decoded_coords:
                self.mqtt_client.publish(
                    f"pi/devices/{self.serial}/raw_data", json.dumps(observation), qos=0
                )
            return
        status = {
            "serial": self.serial,
            "rtcm_stats": rtcm_stats,
            "name": self.cfg.get('name') or self.cfg['mountpoint'],
            "status": current_status,
            "version": RELAY_VERSION,
            "agent_token_fingerprint": None if self.is_prefix7 else get_license_fingerprint(self.serial),
            "timestamp": round(now, 3),
            "base_config": {
                "base_setup_method": "FIXED_LLA",
                "coords": coords
            },
            "ntrip_connected": self.is_connected,
            "ntrip_stats": {"server1_bps": self.current_bps},
            "ntrip_status": {"server1": {"connected": self.is_connected, "bps": self.current_bps}},
            "service_config": {
                "server1_enabled": True,
                "mountpoint1": self.cfg['mountpoint'],
                "serverhost1": self.host,
                "port1": self.port,
                "stream_on_demand": not self.is_prefix7,
                "server1_stream_on_demand": not self.is_prefix7,
                "stream_active": bool(self.is_connected and self.current_bps > 0),
                "active_mountpoint": self.cfg['mountpoint']
            },
            "source_mountpoint": self.cfg['mountpoint'],
            "rtk_fix_status": "NO_FIX" if self.is_prefix7 else ("FIXED" if satellite_count > 0 else "NO_FIX"),
            "detected_chip_type": "UNKNOWN" if self.is_prefix7 else "Ublox",
            "is_provisioned": self.is_prefix7 or has_license(self.serial),
            "nmea_data": {
                "gga": None,
                "gsa": None,
                "satellites": satellites,
                "base_coords": coords or None
            },
            "gnss_stats": {
                "satellites": satellite_count
            },
            "satellite_count": satellite_count
        }
        
        try:
            self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
            if satellites:
                skyview_payload = {
                    "type": "rtcm_skyview_calculated",
                    "satellites": list(satellites.values()),
                    "packets": rtcm_stats,
                    "timestamp": round(now, 3)
                }
                self.mqtt_client.publish(f"pi/devices/{self.serial}/raw_data", json.dumps(skyview_payload), qos=0)
        except Exception:
            pass

    def set_stream_active(self, active: bool):
        # Prefix-7 bases are passive; ESP monitors must stop pulling a sleeping source.
        if not self.is_prefix7 and not active:
            self.stop()

    def _close_live_socket(self):
        with self.socket_lock:
            if self._sock:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
    
    @staticmethod
    def _read_ntrip_response(sock):
        """Validate HTTP/ICY success and preserve coalesced binary stream bytes."""
        response = bytearray()
        while b'\r\n' not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('Caster closed before NTRIP response')
            response.extend(chunk)
            if len(response) > 16384:
                raise ConnectionError('NTRIP response headers too large')
        line = bytes(response).split(b'\r\n', 1)[0]
        fields = line.split()
        if len(fields) < 2 or fields[1] != b'200' or not (fields[0] == b'ICY' or fields[0].startswith(b'HTTP/')):
            raise ConnectionError('Caster rejected NTRIP stream')
        if fields[0] == b'ICY':
            body = bytes(response).split(b'\r\n', 1)[1]
            return body[2:] if body.startswith(b'\r\n') else body
        while b'\r\n\r\n' not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('Incomplete NTRIP headers')
            response.extend(chunk)
            if len(response) > 16384:
                raise ConnectionError('NTRIP response headers too large')
        header, body = bytes(response).split(b'\r\n\r\n', 1)
        if b'transfer-encoding: chunked' in header.lower():
            raise ConnectionError('Chunked NTRIP stream unsupported')
        return body

    def _connect_socket(self):
        global MONITOR_LAST_CONNECT_AT
        while not self.stop_event.is_set():
            if not SOURCE_CONNECT_LIMIT.acquire(timeout=0.2):
                continue
            try:
                if self.is_prefix7:
                    # Pace actual TCP starts even when the shared connect gate is busy.
                    while not self.stop_event.is_set():
                        with MONITOR_CONNECT_LOCK:
                            delay = MONITOR_CONNECT_INTERVAL - (time.monotonic() - MONITOR_LAST_CONNECT_AT)
                            if delay <= 0:
                                MONITOR_LAST_CONNECT_AT = time.monotonic()
                                break
                        if self.stop_event.wait(delay):
                            return None
                if self.stop_event.is_set():
                    return None
                self.log('INFO', f"Connecting MONITOR: {self.host}:{self.port}/{self.cfg['mountpoint']}")
                return socket.create_connection((self.host, self.port), timeout=10)
            finally:
                SOURCE_CONNECT_LIMIT.release()
        return None

    def run(self):
        while not self.stop_event.is_set():
            sock = None
            self.last_rtcm_received_at = 0.0
            try:
                sock = self._connect_socket()
                if sock is None:
                    break
                with self.socket_lock:
                    self._sock = sock
                if self.stop_event.is_set():
                    break

                # Cấu trúc y hệt chuẩn thiết bị Rover Ntrip 1.0 cổ điển:
                r_user = self.cfg.get('rover_user') or self.cfg['mountpoint']
                r_pass = self.cfg.get('rover_pass')
                if self.is_prefix7 and not r_pass:
                    raise ValueError('Base 4G requires the saved Rover password')
                r_pass = r_pass or "12345"
                auth = base64.b64encode(f"{r_user}:{r_pass}".encode()).decode()
                # Caster exposes the final User-Agent segment as receiver_id/client_id.
                decoder_id = 'AITOGY_DECODER_' + re.sub(r'[^A-Za-z0-9_.-]', '_', self.serial)
                request = f"GET /{self.cfg['mountpoint']} HTTP/1.0\r\nUser-Agent: NTRIP AitogyDecoder/{decoder_id}\r\nAuthorization: Basic {auth}\r\n\r\n"
                sock.sendall(request.encode())
                pending_data = self._read_ntrip_response(sock)
                
                try:
                    self._send_rover_gga(sock)
                except Exception as e:
                    self.log("WARNING", f"Failed to send initial GGA: {e}")
                
                self.is_connected = True
                self.publish_status()
                self.log("INFO", "NTRIP authenticated; waiting for valid RTCM")
                rtcm_confirmed = False
                
                self.bytes_received = 0
                self.last_stat_update = time.time()
                self.last_data_received_at = time.time()
                
                sock.settimeout(2.0)
                rtcm_buffer = bytearray()
                while not self.stop_event.is_set():
                    try:
                        data = pending_data or sock.recv(4096)
                        pending_data = b''
                        if not data:
                            self.log("WARNING", "Caster closed the authenticated stream (EOF)")
                            break
                        
                        now = time.time()

                        if now - self.last_gga_send_time >= self.gga_send_interval:
                            try:
                                self._send_rover_gga(sock)
                            except Exception as e:
                                self.log("WARNING", f"Periodic GGA send error: {e}")
                                break

                        rtcm_buffer.extend(data)
                        frames = extract_rtcm3_frames(rtcm_buffer)
                        first_rtcm = bool(frames) and not rtcm_confirmed
                        if frames:
                            self.last_rtcm_received_at = now
                            if not rtcm_confirmed:
                                self.log("INFO", "RTCM confirmed: received CRC-valid frame")
                                rtcm_confirmed = True
                            self.bytes_received += sum(len(frame) for frame in frames)
                            self.last_data_received_at = now
                        if self.is_prefix7 and now - self.last_data_received_at >= self.max_no_data_seconds:
                            break
                        decoder = globals().get("rtcm_signal_decoder")
                        if decoder and frames:
                            for frame in frames:
                                decoded = decoder.decode_sync(self.serial, frame)
                                sats = decoded.get("satellites", []) if decoded else []
                                for sat in sats:
                                    sat_key = f"{sat.get('system', 'un')}:{sat.get('prn')}"
                                    existing = self.satellites.get(sat_key, {})
                                    existing.update({k: v for k, v in sat.items() if v or k not in existing})
                                    existing["last_seen"] = now
                                    self.satellites[sat_key] = existing

                        if first_rtcm or now - self.last_stat_update >= 1.0:
                            self.current_bps = int(self.bytes_received / max(1.0, now - self.last_stat_update))
                            self.bytes_received = 0
                            self.last_stat_update = now
                            self.is_connected = True
                            self.publish_status()
                            
                    except socket.timeout:
                        self.current_bps = 0
                        now = time.time()
                        if now - self.last_gga_send_time >= self.gga_send_interval:
                            try:
                                self._send_rover_gga(sock)
                            except Exception as e:
                                self.log("WARNING", f"Periodic GGA send error: {e}")
                                break
                        no_data_for = now - self.last_data_received_at
                        # If source has no RTCM data for too long, treat it as offline and force reconnect.
                        if no_data_for >= self.max_no_data_seconds:
                            self.is_connected = False
                            self.publish_status()
                            self.log("WARNING", f"No RTCM data for {no_data_for:.1f}s -> mark OFFLINE and reconnect")
                            break
                        self.is_connected = True
                        self.publish_status()
                    except Exception as e:
                        if not self.stop_event.is_set():
                            self.log("WARNING", f"Data error: {e}")
                        break
                        
                self.log("INFO", f"Monitor disconnected, reconnecting in {self.retry_seconds}s...")
                self._close_live_socket()
                self.is_connected = False
                self.current_bps = 0
                self.publish_status()
                self.wakeup_event.wait(self.retry_seconds)
                self.wakeup_event.clear()
            except Exception as e:
                self.log("WARNING", f"Connection error: {e}")
                self._close_live_socket()
                self.is_connected = False
                self.current_bps = 0
                self.publish_status()
                self.wakeup_event.wait(self.retry_seconds)
                self.wakeup_event.clear()
            finally:
                self.is_connected = False
                self.current_bps = 0
                if sock:
                    try: sock.close()
                    except Exception: pass
                with self.socket_lock:
                    self._sock = None
                self.publish_status()

# ==============================================================================
# === MQTT SOURCE WORKER (MQTT -> NTRIP) ===
# ==============================================================================
class MqttSourceWorker(threading.Thread):
    def __init__(self, cfg, mqtt_client_for_status):
        super().__init__()
        self.cfg = cfg
        self.mqtt_status_client = mqtt_client_for_status
        self.daemon = True
        self.stop_event = threading.Event()
        
        self.serial = str(self.cfg.get('serial') or self.cfg.get('device_serial') or f"MQTT_{self.cfg['topic'].replace('/', '_')}")
        self.runtime_config = status_mgr.get_runtime_config(self.serial)
        self.cfg.update(self.runtime_config)
        self.topic_status = f"pi/devices/{self.serial}/status"
        
        self.is_connected = False
        self.bytes_sent = 0
        self.last_stat_update = time.time()
        self.current_bps = 0
        
        self.name = str(self.cfg.get("name") or self.cfg.get("display_name") or self.cfg.get("target_mp") or self.serial)
        self.bytes_received = 0
        self.last_mqtt_data_ts = 0.0
        self.current_mqtt_bps = 0
        self.data_timeout = int(self.cfg.get("data_timeout", 2))
        self.socket_lock = threading.Lock()
        self._target_sock = None
        self._target_socks = {}
        self.bytes_sent_by_server = {"server1": 0, "server2": 0}
        self.current_bps_by_server = {"server1": 0, "server2": 0}
        self.mqtt_data_client = None
        self.restart_connection_event = threading.Event()
        self.rtcm_buffer = bytearray()
        self.command_response_condition = threading.Condition()
        self.command_responses = []
        self.command_response_timeout = float(self.cfg.get("command_response_timeout", 8))
        self.command_max_retries = int(self.cfg.get("command_max_retries", 20))
        self.command_batch_active = False
        self.last_antenna_status = None
        self.latest_gga = None
        self.latest_gsa = None
        self.satellites = {}
        self.raw_publish_buffer = bytearray()
        self.last_raw_publish = time.time()
        self.startup_time = time.time()
        self.was_offline = False
        self.offline_start_time = None

    def log_offline_event(self, message):
        try:
            log_path = os.path.join(SCRIPT_DIR, "offline_lte.txt")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
            self.log("INFO", f"Logged offline event: {message}")
        except Exception as e:
            self.log("ERROR", f"Failed to write to offline_lte.txt: {e}")

    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['topic']} -> {self.cfg['target_mp']}] {msg}")

    def _target_enabled(self):
        return any(self._server_target_enabled(server_id) for server_id in (1, 2))

    def _server_target_config(self, server_id):
        if server_id == 1:
            return {
                "enabled": bool(self.cfg.get("server1_enabled", False)),
                "host": str(self.cfg.get("target_host") or self.cfg.get("serverhost1") or "").strip(),
                "port": self.cfg.get("target_port") or self.cfg.get("port1"),
                "password": str(self.cfg.get("target_pass") or self.cfg.get("password1") or "").strip(),
                "mountpoint": str(self.cfg.get("target_mp") or self.cfg.get("mountpoint1") or "").strip(),
                "username": str(self.cfg.get("username1") or "source").strip() or "source",
                "version": int(self.cfg.get("ntrip_version1", 1) or 1),
                "on_demand": bool(self.cfg.get("server1_stream_on_demand", self.cfg.get("stream_on_demand", True))),
                "active": bool(self.cfg.get("server1_stream_active", self.cfg.get("stream_active", False))),
            }
        return {
            "enabled": bool(self.cfg.get("server2_enabled", False)),
            "host": str(self.cfg.get("serverhost2") or "").strip(),
            "port": self.cfg.get("port2"),
            "password": str(self.cfg.get("password2") or "").strip(),
            "mountpoint": str(self.cfg.get("mountpoint2") or "").strip(),
            "username": str(self.cfg.get("username2") or "source").strip() or "source",
            "version": int(self.cfg.get("ntrip_version2", 1) or 1),
            "on_demand": bool(self.cfg.get("server2_stream_on_demand", True)),
            "active": bool(self.cfg.get("server2_stream_active", False)),
        }

    def _server_target_enabled(self, server_id):
        cfg = self._server_target_config(server_id)
        return cfg["enabled"] and bool(cfg["host"] and cfg["port"] and cfg["password"] and cfg["mountpoint"])

    def _server_target_can_push(self, server_id):
        cfg = self._server_target_config(server_id)
        return self._server_target_enabled(server_id) and ((not cfg["on_demand"]) or cfg["active"])

    def _has_active_client(self):
        return bool(self._target_socks)

    def _derive_status(self, mqtt_data_active):
        if not mqtt_data_active:
            return "offline"
        if not any(self._server_target_can_push(server_id) for server_id in (1, 2)):
            return "sleep"
        if self.current_mqtt_bps <= 0:
            return "offline"
        return "online"

    def publish_status(self):
        now = time.time()
        mqtt_data_age = None if self.last_mqtt_data_ts <= 0 else int(now - self.last_mqtt_data_ts)
        mqtt_data_active = mqtt_data_age is not None and mqtt_data_age <= self.data_timeout
        server1_connected = "server1" in self._target_socks
        server2_connected = "server2" in self._target_socks
        server1_bps = self.current_bps_by_server.get("server1", 0)
        server2_bps = self.current_bps_by_server.get("server2", 0)
        if not mqtt_data_active:
            self.latest_gga = None
            self.latest_gsa = None
        latest_gga = self.latest_gga if isinstance(self.latest_gga, dict) else None
        latest_gsa = self.latest_gsa if isinstance(self.latest_gsa, dict) else None
        satellites = {
            key: sat for key, sat in self.satellites.items()
            if mqtt_data_active and now - float(sat.get("last_seen", 0)) <= 30
        }
        self.satellites = satellites
        configured_coords = {
            "lat": self.cfg.get("lat"),
            "lon": self.cfg.get("lon"),
            "alt": self.cfg.get("alt"),
        }
        live_coords = None
        if latest_gga and latest_gga.get("latitude") is not None and latest_gga.get("longitude") is not None:
            live_coords = {
                "lat": latest_gga.get("latitude"),
                "lon": latest_gga.get("longitude"),
                "alt": latest_gga.get("altitude"),
            }
        base_coords = configured_coords
        rtk_fix_status = latest_gga.get("fix_status") if latest_gga else ("DGPS" if mqtt_data_active else "NO_FIX")
        base_config = {
            "base_setup_method": self.cfg.get("setup_method") or self.cfg.get("base_setup_method"),
            "survey_in_duration": self.cfg.get("duration"),
            "accuracy": self.cfg.get("accuracy"),
            "coords": base_coords,
        }
        if base_config["base_setup_method"] == "FIXED_LLA":
            # Older configurations retain the receiver's existing geoid semantics.
            base_config["altitude_reference"] = self.cfg.get("altitude_reference") or "LEGACY"
        base_config["gnss_options"] = normalize_lte_gnss_options(self.cfg.get("gnss_options"))
        service_config = {
            "ncomport": self.cfg.get("ncomport") or "",
            "reconnectioninterval": int(self.cfg.get("reconnectioninterval", 10) or 10),
            "stream_on_demand": bool(self.cfg.get("stream_on_demand", self.cfg.get("server1_stream_on_demand", True))),
            "stream_active": bool(self.cfg.get("stream_active", self.cfg.get("server1_stream_active", False))),
            "server1_enabled": bool(self.cfg.get("server1_enabled", False)),
            "server1_stream_on_demand": bool(self.cfg.get("server1_stream_on_demand", self.cfg.get("stream_on_demand", True))),
            "server1_stream_active": bool(self.cfg.get("server1_stream_active", False)),
            "ntrip_version1": int(self.cfg.get("ntrip_version1", 1) or 1),
            "username1": self.cfg.get("username1") or "source",
            "serverhost1": self.cfg.get("target_host"),
            "port1": self.cfg.get("target_port"),
            "mountpoint1": self.cfg.get("target_mp"),
            "password1": self.cfg.get("target_pass"),
            "active_mountpoint": self.cfg.get("target_mp") if bool(self.cfg.get("stream_active", self.cfg.get("server1_stream_active", False))) else None,
            "server2_enabled": bool(self.cfg.get("server2_enabled", False)),
            "server2_stream_on_demand": bool(self.cfg.get("server2_stream_on_demand", True)),
            "server2_stream_active": bool(self.cfg.get("server2_stream_active", False)),
            "ntrip_version2": int(self.cfg.get("ntrip_version2", 1) or 1),
            "username2": self.cfg.get("username2") or "",
            "serverhost2": self.cfg.get("serverhost2") or "",
            "port2": self.cfg.get("port2"),
            "mountpoint2": self.cfg.get("mountpoint2") or "",
            "password2": self.cfg.get("password2") or "",
            "command_host": self.cfg.get("command_host") or self.cfg.get("source_host") or "",
            "command_port": self.cfg.get("command_port") or self.cfg.get("source_port"),
            "command_pass": self.cfg.get("command_pass") or self.cfg.get("source_pass") or "",
            "command_mp": self.cfg.get("command_mp") or self.cfg.get("source_mp") or "",
            "rtcm_enabled": bool(self.cfg.get("rtcm_enabled", False)),
            "rtcmntripversion1": int(self.cfg.get("rtcmntripversion1", 1) or 1),
            "rtcmserver1": self.cfg.get("rtcmserver1") or "",
            "rtcmport1": self.cfg.get("rtcmport1"),
            "rtcmmountpoint1": self.cfg.get("rtcmmountpoint1") or "",
            "rtcmusername1": self.cfg.get("rtcmusername1") or "",
            "rtcmpassword1": self.cfg.get("rtcmpassword1") or "",
        }
        status_value = self._derive_status(mqtt_data_active)
        rtcm_input_bps = self.current_mqtt_bps if mqtt_data_active else 0
        satellite_count = len([sat for sat in satellites.values() if sat.get("snr", 0) > 0]) or (latest_gga.get("satellites", 0) if latest_gga else 0)
        if mqtt_data_active:
            antenna_state, antenna_message = None, None
            if 0 < rtcm_input_bps < 100:
                antenna_state = "FAULT"
                antenna_message = f"Tốc độ nhận RTCM từ máy thu cực thấp ({rtcm_input_bps} B/s). Kiểm tra ăng-ten và đường truyền nguồn."
            elif satellite_count > 0:
                antenna_state = "OK"
                antenna_message = f"Máy thu đang nhận được {satellite_count} vệ tinh."
            elif latest_gga is not None:
                antenna_state = "FAULT"
                antenna_message = "Không bắt được vệ tinh nào (0 Sats). Kiểm tra ăng-ten và nguồn cấp."
            if antenna_state:
                self.last_antenna_status = {"state": antenna_state, "message": antenna_message, "timestamp": now}
        status = {
            "serial": self.serial,
            "rtcm_stats": globals().get("rtcm_signal_decoder").get_stats(self.serial) if globals().get("rtcm_signal_decoder") else {},
            "name": self.name,
            "status": status_value,
            "version": RELAY_VERSION,
            "agent_token_fingerprint": get_license_fingerprint(self.serial),
            "timestamp": round(time.time(), 3),
            "base_config": base_config,
            "ntrip_connected": self.is_connected,
            "ntrip_stats": {
                "server1_bps": server1_bps,
                "server2_bps": server2_bps,
                "rtcm_input_bps": rtcm_input_bps,
            },
            "ntrip_status": {
                "server1": {"connected": server1_connected, "bps": server1_bps},
                "server2": {"connected": server2_connected, "bps": server2_bps},
            },
            "service_config": service_config,
            "command_source": {
                "host": self.cfg.get("command_host") or self.cfg.get("source_host"),
                "port": self.cfg.get("command_port") or self.cfg.get("source_port"),
                "mountpoint": self.cfg.get("command_mp") or self.cfg.get("source_mp")
            },
            "source_topic": self.cfg['topic'],
            "mqtt_data_active": mqtt_data_active,
            "mqtt_data_age_seconds": mqtt_data_age,
            "mqtt_stats": {"source_bps": rtcm_input_bps},
            "nmea_data": {
                "gga": latest_gga,
                "gsa": latest_gsa,
                "satellites": satellites,
                "base_coords": base_coords,
                "live_coords": live_coords,
            },
            "gnss_stats": {
                "satellites": satellite_count,
                "hdop": (latest_gsa or latest_gga or {}).get("hdop"),
                "pdop": latest_gsa.get("pdop") if latest_gsa else None,
            },
            "rtk_fix_status": rtk_fix_status,
            "base_mode_active": mqtt_data_active,
            "detected_chip_type": "Unicorecomm",
            "connection_type": "LTE",
            "is_lte": True,
            "is_provisioned": has_license(self.serial),
            "config_status": getattr(self, "config_status", None)
        }
        if self.last_antenna_status:
            status["antenna_status"] = self.last_antenna_status
        status["service_config"] = _status_safe_config(status.get("service_config"))
        try:
            if self.mqtt_status_client and self.mqtt_status_client.is_connected():
                self.mqtt_status_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
            status_mgr.update_worker_status(self.serial, status)
        except Exception:
            pass

    def _connect_target(self, server_id=1):
        target_cfg = self._server_target_config(server_id)
        target_host = target_cfg["host"]
        target_port = int(target_cfg["port"])
        target_pass = target_cfg["password"]
        target_mp = target_cfg["mountpoint"]
        target_user = target_cfg.get("username") or "source"
        target_version = int(target_cfg.get("version") or 1)
        server_key = f"server{server_id}"

        self.log("INFO", f"Connecting TARGET NTRIP {server_key} for RTCM output: {target_host}:{target_port}/{target_mp} (v{target_version}.0)")
        sock = socket.create_connection((target_host, target_port), timeout=10)
        enable_tcp_keepalive(sock)
        
        # Handshake chính xác như đã test để có thể gửi lệnh vào luồng RTCM của chip
        # TARGET only forwards MQTT RTCM to enabled NTRIP servers.
        # Chip/base configuration uses the separate COMMAND SOURCE path below.
        auth_str = f"{target_user}:{target_pass or ''}"
        auth = base64.b64encode(auth_str.encode("ascii")).decode("ascii")
        if target_version == 2:
            target_request = (
                f"POST /{target_mp} HTTP/1.1\r\n"
                f"Host: {target_host}:{target_port}\r\n"
                f"Ntrip-Version: Ntrip/2.0\r\n"
                f"User-Agent: NTRIP GeotekMqttBridge/1.0\r\n"
                f"Authorization: Basic {auth}\r\n"
                f"Transfer-Encoding: chunked\r\n"
                f"Connection: Keep-Alive\r\n\r\n"
            )
        else:
            target_request = (
                f"SOURCE {target_pass or ''} /{target_mp} HTTP/1.1\r\n"
                f"Host: {target_host}:{target_port}\r\n"
                f"Ntrip-Version: Ntrip/2.0\r\n"
                f"User-Agent: NTRIP GeotekMqttBridge/1.0\r\n"
                f"Authorization: Basic {auth}\r\n"
                f"Connection: Keep-Alive\r\n\r\n"
            )
        sock.sendall(target_request.encode())
        
        response = sock.recv(4096)
        if not (NTRIP_ICY_SUCCESS in response or NTRIP_HTTP_SUCCESS in response):
            raise Exception(f"Target rejected NTRIP connection: {response.decode(errors='ignore')[:100]}")
        
        self.log("SUCCESS", f"TARGET ACTIVE {server_key}: MQTT RTCM -> {target_mp}")
        
        # Thực hiện cấu hình chip qua luồng này nếu là lần đầu hoặc reconnect
        # Tuy nhiên, chúng ta sẽ ưu tiên cấu hình qua MQTT lệnh từ Dashboard
        # self._send_config_commands(sock)
        
        return sock

    def _connect_command_source(self):
        command_host = self.cfg.get("command_host") or self.cfg.get("source_host")
        command_port = int(self.cfg.get("command_port") or self.cfg.get("source_port") or 2101)
        command_pass = self.cfg.get("command_pass") or self.cfg.get("source_pass") or ""
        command_mp = self.cfg.get("command_mp") or self.cfg.get("source_mp")

        if not command_host or not command_mp:
            raise ValueError("Missing command_host/command_mp for chip command SOURCE")

        self.log("INFO", f"Connecting COMMAND SOURCE: {command_host}:{command_port}/{command_mp}")
        sock = socket.create_connection((command_host, command_port), timeout=10)
        request = f"SOURCE {command_pass} /{command_mp}\r\nSource-Agent: NTRIP NtripServerCMD/1.0\r\n\r\n"
        sock.sendall(request.encode())

        response = sock.recv(4096)
        self.log("INFO", f"COMMAND SOURCE handshake response: {response.decode(errors='ignore').strip()[:120]}")
        if not (NTRIP_ICY_SUCCESS in response or NTRIP_HTTP_SUCCESS in response):
            raise Exception(f"Command SOURCE rejected: {response.decode(errors='ignore')[:100]}")

        self.log("SUCCESS", f"COMMAND SOURCE ACTIVE: {command_mp}")
        return sock

    def _record_command_response(self, payload):
        response = parse_command_response(payload)
        if not response:
            return False
        if not self.command_batch_active:
            return True
        with self.command_response_condition:
            self.command_responses.append(response)
            if len(self.command_responses) > 100:
                self.command_responses = self.command_responses[-100:]
            self.command_response_condition.notify_all()
        self.log("INFO", f"Chip response: {response['raw']}")
        return True

    def _clear_command_responses(self):
        with self.command_response_condition:
            self.command_responses.clear()

    def _wait_for_command_response(self, expected_key, timeout):
        deadline = time.time() + timeout
        expected_key = expected_key.upper()
        with self.command_response_condition:
            while True:
                for index, response in enumerate(self.command_responses):
                    if response.get("key") == expected_key:
                        matched = self.command_responses.pop(index)
                        text = matched.get("response", "")
                        ok = "OK" in text
                        no_func = (
                            "NO FUNC" in text
                            or "NOFUNC" in text
                            or "NO FUNCTION" in text
                            or "NO MATCHING FUNC" in text
                            or "NO MATCHING FUNCTION" in text
                        )
                        return ok or no_func, matched

                remaining = deadline - time.time()
                if remaining <= 0:
                    return False, None
                self.command_response_condition.wait(remaining)

    def _send_command_with_ack(self, command_sock, cmd_bytes):
        key = command_key(cmd_bytes)
        if not key:
            return True

        for attempt in range(1, self.command_max_retries + 1):
            self._clear_command_responses()
            self.log("INFO", f"Send command attempt {attempt}/{self.command_max_retries}: {cmd_bytes.decode(errors='ignore').strip()}")
            command_sock.sendall(cmd_bytes)
            ok, response = self._wait_for_command_response(key, self.command_response_timeout)
            if ok and response:
                response_text = response.get("response", "")
                if "OK" in response_text:
                    self.log("SUCCESS", f"Command ACK OK: {response['raw']}")
                    return True
                if key != "FRESET":
                    self.log("WARNING", f"Command response accepted: {response['raw']}")
                    return True
                self.log("WARNING", f"FRESET requires OK response, got: {response['raw']}")
            if response:
                self.log("WARNING", f"Command rejected/unknown response: {response['raw']}")
            else:
                self.log("WARNING", f"No response for command {key}; retrying")

        return False

    def set_stream_active(self, active: bool, mountpoint=None):
        active = bool(active)
        target_mp = str(mountpoint or "").strip().lower()
        runtime = status_mgr.get_runtime_config(self.serial)

        matched_servers = []
        for server_id in (1, 2):
            server_cfg = self._server_target_config(server_id)
            server_mp = str(server_cfg.get("mountpoint") or "").strip().lower()
            if target_mp and server_mp and server_mp != target_mp:
                continue
            if server_cfg.get("enabled") and server_cfg.get("mountpoint"):
                if not active and not server_cfg.get("on_demand"):
                    continue
                matched_servers.append(server_id)

        if not matched_servers and active:
            matched_servers = [1]

        for server_id in matched_servers:
            if active:
                runtime[f"server{server_id}_enabled"] = True
            runtime[f"server{server_id}_stream_active"] = active

        runtime["stream_active"] = any(
            bool(runtime.get(f"server{server_id}_stream_active", self.cfg.get(f"server{server_id}_stream_active", False)))
            for server_id in (1, 2)
        )
        if active and mountpoint:
            runtime["active_mountpoint"] = str(mountpoint)
        elif not active:
            runtime.pop("active_mountpoint", None)

        self.runtime_config = runtime
        self.cfg.update(runtime)
        status_mgr.update_runtime_config(self.serial, runtime)

        if not active:
            with self.socket_lock:
                for server_id in matched_servers:
                    key = f"server{server_id}"
                    self.current_bps_by_server[key] = 0
                    self.bytes_sent_by_server[key] = 0
                self.current_bps = self.current_bps_by_server.get("server1", 0)
            self.log("INFO", "RTCM paused; keeping authenticated caster connections")
        else:
            self.log("INFO", f"Wake received; enabling NTRIP target for mountpoint={mountpoint or self.cfg.get('target_mp')}")
        self.publish_status()

    def apply_provision_payload(self, payload):
        if not isinstance(payload, dict):
            return
        name = str(payload.get("name") or payload.get("display_name") or "").strip()
        if not name:
            return

        runtime = status_mgr.get_runtime_config(self.serial)
        runtime["name"] = name
        runtime["display_name"] = name

        self.runtime_config = runtime
        self.cfg.update(runtime)
        self.name = name
        status_mgr.update_runtime_config(self.serial, runtime)

        if config_mgr.update_mqtt_source_config(self.serial, {"name": name, "display_name": name}):
            self.log("INFO", f"Saved provisioned name '{name}' into geotek_config.json")
        else:
            self.log("WARNING", "Could not find MQTT source in geotek_config.json for provision save")
        self.publish_status()

    def _persist_remote_config(self, original_config):
        if not isinstance(original_config, dict):
            return

        runtime = status_mgr.get_runtime_config(self.serial)
        before_target = (
            self.cfg.get("target_host"),
            str(self.cfg.get("target_port")),
            self.cfg.get("target_pass"),
            self.cfg.get("target_mp"),
        )
        runtime["last_applied_config"] = original_config
        runtime["last_config_update"] = datetime.now(timezone.utc).isoformat()

        payload = original_config.get("payload", original_config)
        params = payload.get("params", {}) if isinstance(payload.get("params"), dict) else {}
        method = str(
            payload.get("method")
            or payload.get("base_setup_method")
            or params.get("base_setup_method")
            or ""
        ).upper()
        if not params:
            params = payload
        service_config = payload.get("service_config", {}) if isinstance(payload.get("service_config"), dict) else {}
        if service_config:
            runtime.update(_mqtt_source_persistable_config(service_config))

        if "gnss_options" in params:
            runtime["gnss_options"] = normalize_lte_gnss_options(params["gnss_options"])

        if method:
            runtime["setup_method"] = method
            runtime["base_setup_method"] = method
        if method == "SURVEY_IN":
            duration = params.get("survey_in_duration") or params.get("duration")
            if duration is not None:
                runtime["duration"] = duration
                runtime["survey_in_duration"] = duration
            if "survey_in_accuracy" in params:
                runtime["survey_in_accuracy"] = params["survey_in_accuracy"]
        if method == "FIXED_LLA":
            coords = params.get("coords", {}) if isinstance(params.get("coords"), dict) else params
            runtime["altitude_reference"] = params.get("altitude_reference", "LEGACY")
            for key in ("lat", "lon", "alt"):
                if key in coords:
                    runtime[key] = coords[key]
            if "accuracy" in params:
                runtime["accuracy"] = params["accuracy"]

        target_sources = (payload, params, service_config)
        target_aliases = {
            "target_host": ("target_host", "serverhost1", "host", "caster_host"),
            "target_port": ("target_port", "serverport1", "port1", "port", "caster_port"),
            "target_pass": ("target_pass", "password1", "password", "pass", "caster_password"),
            "target_mp": ("target_mp", "mountpoint", "mountpoint1", "active_mountpoint"),
        }
        for target_key, aliases in target_aliases.items():
            updated = False
            for source in target_sources:
                for alias in aliases:
                    value = source.get(alias) if isinstance(source, dict) else None
                    if value not in (None, ""):
                        runtime[target_key] = value
                        updated = True
                        break
                if updated:
                    break

        self.runtime_config = runtime
        self.cfg.update(runtime)
        status_mgr.update_runtime_config(self.serial, runtime)
        hard_config = _mqtt_source_persistable_config(runtime)
        if hard_config:
            if config_mgr.update_mqtt_source_config(self.serial, hard_config):
                self.log("INFO", "Saved remote config into geotek_config.json")
            else:
                self.log("WARNING", "Could not find MQTT source in geotek_config.json for hard save")

        after_target = (
            self.cfg.get("target_host"),
            str(self.cfg.get("target_port")),
            self.cfg.get("target_pass"),
            self.cfg.get("target_mp"),
        )
        if after_target != before_target:
            with self.socket_lock:
                for sock in list(self._target_socks.values()):
                    try: sock.shutdown(socket.SHUT_RDWR)
                    except Exception: pass
                    try: sock.close()
                    except Exception: pass
                self._target_socks.clear()
                self._target_sock = None
            self.is_connected = False
            self.log("INFO", "Runtime target changed; reconnecting SOURCE socket")

        self.log("INFO", "Saved remote config into geotek_db.json")

    def execute_commands(self, commands_b64, original_config=None):
        """Inject Geotek LTE chip config commands through the active SOURCE socket."""
        try:
            self.config_status = {"state": "started", "progress": "Initializing...", "error": None}
            self.publish_status()
            
            self._persist_remote_config(original_config)

            decoded_commands = build_lte_unicore_commands(original_config, self.cfg)
            self.log(
                "INFO",
                "Using local Geotek LTE command builder for JSON source "
                f"(port={self.cfg.get('rtcm_port') or self.cfg.get('sensor_port') or 'com2'}; "
                f"profile={self.cfg.get('gnss_options') or 'legacy RTCM-only MSM4+MSM7'})"
            )

            if not decoded_commands:
                self.log("WARNING", "No commands to inject")
                self.config_status = {"state": "done", "progress": "No commands needed", "error": None}
                self.publish_status()
                return

            self.log("INFO", f"Received {len(decoded_commands)} commands to send to command SOURCE")
            command_sock = None
            try:
                self.log(
                    "INFO",
                    "Command SOURCE target: "
                    f"{self.cfg.get('command_host') or self.cfg.get('source_host')}:"
                    f"{self.cfg.get('command_port') or self.cfg.get('source_port') or 2101}/"
                    f"{self.cfg.get('command_mp') or self.cfg.get('source_mp')}"
                )
                command_sock = self._connect_command_source()
                self._clear_command_responses()
                self.command_batch_active = True
                
                total = len(decoded_commands)
                for i, cmd_bytes in enumerate(decoded_commands):
                    self.config_status = {"state": "executing", "progress": f"Executing {i+1}/{total}", "error": None}
                    self.publish_status()
                    
                    delay = command_delay_seconds(cmd_bytes)
                    if cmd_bytes.startswith(COMMAND_DELAY_PREFIX):
                        time.sleep(delay)
                        continue
                    if not self._send_command_with_ack(command_sock, cmd_bytes):
                        raise TimeoutError(f"No response for: {cmd_bytes.decode(errors='ignore').strip()}")
                    time.sleep(delay)
                
                self.config_status = {"state": "success", "progress": "Configuration applied successfully", "error": None}
                self.log("SUCCESS", "Commands sent through command SOURCE successfully")
            finally:
                if command_sock:
                    try: command_sock.shutdown(socket.SHUT_RDWR)
                    except Exception: pass
                    try: command_sock.close()
                    except Exception: pass
                    self.log("INFO", "COMMAND SOURCE closed after command batch")
                self.command_batch_active = False
                self._clear_command_responses()
                self.publish_status()
        except Exception as e:
            self.config_status = {"state": "error", "progress": "Failed", "error": str(e)}
            self.publish_status()
            self.log("ERROR", f"Failed to execute Geotek LTE commands: {e}")

    def _send_config_commands(self, sock):
        self.log("WARNING", "Direct local config path is disabled; MQTT EXECUTE_RAW_COMMANDS triggers the Geotek LTE builder.")
        return
        self.log("INFO", "📡 Executing Geotek LTE specialized configuration...")
        
        _ = self.cfg.get('setup_method', 'SURVEY_IN')
        _ = self.cfg.get('duration', 60)
        _ = self.cfg.get('lat', 0.0)
        _ = self.cfg.get('lon', 0.0)
        _ = self.cfg.get('alt', 0.0)
        
        cmds = []
        # Lệnh RESET vật lý chip đầu tiên
        cmds.append(b"FRESET\r\n")
        
        if False:
            # Gọi hàm chuyên biệt dành riêng cho Geotek
            pass
        else:
            self.log("ERROR", "❌ Specialized command builder function not found!")
            return

        for cmd in cmds:
            try:
                cmd_bytes = cmd if isinstance(cmd, bytes) else cmd.encode('ascii')
                
                if COMMAND_DELAY_PREFIX in cmd_bytes:
                    ms = int(cmd_bytes.decode().split('_')[1].replace('$', ''))
                    time.sleep(ms / 1000.0)
                    continue
                    
                sock.sendall(cmd_bytes)
                if b"FRESET" in cmd_bytes: time.sleep(3.0)
                else: time.sleep(0.05) # Delay cực nhỏ giữa các lệnh
            except Exception as e:
                self.log("WARNING", f"Failed to send Geotek command: {e}")
                break
        self.log("SUCCESS", "✅ Geotek LTE configuration finished.")

    def run(self):
        self.log_offline_event(f"Monitoring started for {self.name} ({self.serial})")
        while not self.stop_event.is_set():
            try:
                self.restart_connection_event.clear()
                try:
                    self.mqtt_data_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                except Exception:
                    self.mqtt_data_client = mqtt.Client()
                
                if self.cfg.get('mqtt_user') and self.cfg.get('mqtt_pass'):
                    self.mqtt_data_client.username_pw_set(self.cfg['mqtt_user'], self.cfg['mqtt_pass'])
                
                def on_message(c, userdata, msg):
                    now = time.time()
                    if self.was_offline:
                        offline_duration = int(now - self.offline_start_time)
                        m, s = divmod(offline_duration, 60)
                        h, m = divmod(m, 60)
                        duration_str = f"{h}h {m}m {s}s" if h > 0 else (f"{m}m {s}s" if m > 0 else f"{s}s")
                        self.log_offline_event(f"{self.name} ({self.serial}) came back ONLINE. Offline duration: {duration_str} ({offline_duration} seconds)")
                        self.was_offline = False
                        self.offline_start_time = None
                    elif self.offline_start_time is None:
                        self.was_offline = False
                    
                    self.last_mqtt_data_ts = now
                    payload_bytes = msg.payload or b""
                    self.bytes_received += len(payload_bytes)
                    
                    # if self.mqtt_status_client and self.mqtt_status_client.is_connected():
                    #     self.raw_publish_buffer.extend(payload_bytes)
                    #     if time.time() - self.last_raw_publish >= 1.0:
                    #         try:
                    #             self.mqtt_status_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                    #         except Exception as e:
                    #             self.log("WARNING", f"Failed to publish raw_data: {e}")
                    #         self.raw_publish_buffer.clear()
                    #         self.last_raw_publish = time.time()

                    if self._record_command_response(msg.payload):
                        return
                    gga = parse_nmea_gga(msg.payload)
                    if gga:
                        self.latest_gga = gga
                    gsa = parse_nmea_gsa(msg.payload)
                    if gsa:
                        self.latest_gsa = gsa
                        active = set(gsa.get("active_sats", []))
                        for sat in self.satellites.values():
                            sat["isTracking"] = sat.get("prn") in active
                    gsv_sats = parse_nmea_gsv(msg.payload)
                    if gsv_sats:
                        active = set((self.latest_gsa or {}).get("active_sats", []))
                        for sat in gsv_sats:
                            sat["isTracking"] = sat.get("prn") in active
                            sat_key = f"{sat.get('system', 'un')}:{sat.get('prn')}"
                            existing = self.satellites.get(sat_key, {})
                            existing.update({k: v for k, v in sat.items() if v or k not in existing})
                            existing["last_seen"] = time.time()
                            self.satellites[sat_key] = existing
                    self.rtcm_buffer.extend(msg.payload or b"")
                    rtcm_frames = extract_rtcm3_frames(self.rtcm_buffer)
                    if not rtcm_frames:
                        return

                    for frame in rtcm_frames:
                        decoded = rtcm_signal_decoder.decode_sync(self.serial, frame)
                        sats = decoded.get("satellites", []) if decoded else []
                        for sat in sats:
                            sat_key = f"{sat.get('system', 'un')}:{sat.get('prn')}"
                            existing = self.satellites.get(sat_key, {})
                            existing.update({k: v for k, v in sat.items() if v or k not in existing})
                            existing["last_seen"] = time.time()
                            self.satellites[sat_key] = existing

                    with self.socket_lock:
                        for server_key, sock in list(self._target_socks.items()):
                            if not self._server_target_can_push(int(server_key[-1])):
                                continue
                            try:
                                for frame in rtcm_frames:
                                    sock.sendall(frame)
                                    self.bytes_sent += len(frame)
                                    self.bytes_sent_by_server[server_key] = self.bytes_sent_by_server.get(server_key, 0) + len(frame)
                            except Exception:
                                self.log("WARNING", f"Target socket write error: {server_key}")
                                try: sock.close()
                                except Exception: pass
                                self._target_socks.pop(server_key, None)
                        self._target_sock = self._target_socks.get("server1")
                        self.is_connected = bool(self._target_socks)

                def on_disconnect(c, userdata, *args):
                    rc = args[-1] if args else 0
                    self.log("WARNING", f"MQTT source client disconnected with code {rc}")
                    if not self.was_offline:
                        self.was_offline = True
                        self.offline_start_time = time.time()
                        self.log_offline_event(f"{self.name} ({self.serial}) went OFFLINE (MQTT connection lost, code {rc})")

                self.mqtt_data_client.on_message = on_message
                self.mqtt_data_client.on_disconnect = on_disconnect
                self.mqtt_data_client.connect(self.cfg['mqtt_broker'], int(self.cfg['mqtt_port']), 60)
                self.mqtt_data_client.subscribe(self.cfg['topic'])
                self.log("INFO", f"Subscribed MQTT source topic: {self.cfg['topic']}")
                self.mqtt_data_client.loop_start()

                while not self.stop_event.is_set():
                    if self.restart_connection_event.is_set():
                        self.log("WARNING", "Connection restart requested; rebuilding MQTT subscription and NTRIP targets")
                        break
                    now = time.time()
                    
                    # Check offline status
                    mqtt_data_age = None if self.last_mqtt_data_ts <= 0 else int(now - self.last_mqtt_data_ts)
                    mqtt_data_active = mqtt_data_age is not None and mqtt_data_age <= self.data_timeout
                    has_active_client = self._has_active_client()
                    
                    if has_active_client and not mqtt_data_active:
                        if not self.was_offline:
                            self.was_offline = True
                            if self.last_mqtt_data_ts > 0:
                                self.offline_start_time = self.last_mqtt_data_ts
                                last_data_dt = datetime.fromtimestamp(self.last_mqtt_data_ts).strftime("%Y-%m-%d %H:%M:%S")
                                self.log_offline_event(f"{self.name} ({self.serial}) went OFFLINE (No MQTT data for {mqtt_data_age}s, last data received at {last_data_dt})")
                            else:
                                # No data received since startup
                                if now - self.startup_time > self.data_timeout:
                                    self.offline_start_time = self.startup_time
                                    self.log_offline_event(f"{self.name} ({self.serial}) went OFFLINE (No MQTT data received since startup)")
                                else:
                                    self.was_offline = False
                    elif not has_active_client:
                        if self.was_offline:
                            self.was_offline = False
                            self.offline_start_time = None

                    if now - self.last_stat_update >= 5.0:
                        elapsed = now - self.last_stat_update
                        self.current_bps_by_server = {
                            "server1": int(self.bytes_sent_by_server.get("server1", 0) / elapsed),
                            "server2": int(self.bytes_sent_by_server.get("server2", 0) / elapsed),
                        }
                        self.current_bps = self.current_bps_by_server.get("server1", 0)
                        self.current_mqtt_bps = int(self.bytes_received / (now - self.last_stat_update))
                        self.bytes_sent = 0
                        self.bytes_sent_by_server = {"server1": 0, "server2": 0}
                        self.bytes_received = 0
                        self.last_stat_update = now
                        self.publish_status()

                    if not self._target_enabled():
                        if self.is_connected or self._target_socks:
                            with self.socket_lock:
                                for sock in list(self._target_socks.values()):
                                    try: sock.shutdown(socket.SHUT_RDWR)
                                    except Exception: pass
                                    try: sock.close()
                                    except Exception: pass
                                self._target_socks.clear()
                                self._target_sock = None
                            self.is_connected = False
                            self.current_bps = 0
                            self.current_bps_by_server = {"server1": 0, "server2": 0}
                            self.publish_status()
                        time.sleep(1)
                        continue

                    with self.socket_lock:
                        for server_id in (1, 2):
                            server_key = f"server{server_id}"
                            if not self._server_target_enabled(server_id):
                                sock = self._target_socks.pop(server_key, None)
                                if sock:
                                    try: sock.shutdown(socket.SHUT_RDWR)
                                    except Exception: pass
                                    try: sock.close()
                                    except Exception: pass
                                continue
                            if server_key in self._target_socks:
                                sock = self._target_socks[server_key]
                                try:
                                    readable, _, _ = select.select([sock], [], [], 0)
                                    if readable and not sock.recv(4096):
                                        raise ConnectionError("Caster closed the connection")
                                except (OSError, ConnectionError):
                                    sock.close()
                                    self._target_socks.pop(server_key, None)
                            if server_key not in self._target_socks:
                                try:
                                    self._target_socks[server_key] = self._connect_target(server_id)
                                    self.publish_status()
                                except Exception as e:
                                    self.log("WARNING", f"Target connection failed {server_key}: {e}")
                        self._target_sock = self._target_socks.get("server1")
                        self.is_connected = bool(self._target_socks)
                    
                    time.sleep(1)

            except Exception as e:
                self.log("ERROR", f"Worker loop error: {e}")
                if not self.was_offline:
                    self.was_offline = True
                    self.offline_start_time = time.time()
                    self.log_offline_event(f"{self.name} ({self.serial}) went OFFLINE (Worker loop error: {e})")
                time.sleep(RECONNECT_INTERVAL)
            finally:
                if self.mqtt_data_client:
                    try:
                        self.mqtt_data_client.loop_stop()
                        self.mqtt_data_client.disconnect()
                    except Exception: pass
                with self.socket_lock:
                    for sock in list(self._target_socks.values()):
                        try: sock.close()
                        except Exception: pass
                    self._target_socks.clear()
                    self._target_sock = None
                self.is_connected = False
                self.publish_status()

# ==============================================================================
# === MQTT ===
# ==============================================================================
def _worker_mountpoint(worker):
    if isinstance(worker, RelayWorker):
        return str(worker.cfg.get("target_mp", "")).strip()
    return str(worker.cfg.get("target_mp") or worker.cfg.get("mountpoint", "")).strip()


def _close_worker_connections(worker, stop_worker=False):
    """Close transports without discarding the worker's runtime state."""
    if stop_worker:
        worker.stop_event.set()
    wakeup = getattr(worker, "wakeup_event", None)
    if wakeup:
        wakeup.set()

    mqtt_data_client = getattr(worker, "mqtt_data_client", None)
    if mqtt_data_client:
        try:
            mqtt_data_client.loop_stop()
        except Exception:
            pass
        try:
            mqtt_data_client.disconnect()
        except Exception:
            pass

    socket_lock = getattr(worker, "socket_lock", None)
    acquired = socket_lock.acquire(timeout=2) if socket_lock is not None else False
    try:
        sockets = []
        for attr in ("_sock", "_source_sock", "_target_sock"):
            sock = getattr(worker, attr, None)
            if sock:
                sockets.append(sock)
                setattr(worker, attr, None)
        target_socks = getattr(worker, "_target_socks", None)
        if isinstance(target_socks, dict):
            sockets.extend(target_socks.values())
            target_socks.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
    finally:
        if acquired:
            socket_lock.release()


def _stop_worker_connections(worker):
    """Stop one worker and actively unblock its network loops."""
    _close_worker_connections(worker, stop_worker=True)


def restart_worker_connections(worker, reason="remote_restart"):
    """Reconnect transports in-place, preserving device/runtime data."""
    restart_event = getattr(worker, "restart_connection_event", None)
    if restart_event is not None:
        restart_event.set()
    _close_worker_connections(worker, stop_worker=False)
    logging.warning(
        f"[{getattr(worker, 'serial', '?')}] Restarting connections in-place reason={reason}; "
        "config, token, runtime cache and device data are preserved"
    )
    return worker


def restart_worker(worker_map, serial, mqtt_status_client, reason="remote_restart"):
    """Replace a Thread object; Python threads themselves cannot be restarted."""
    serial = str(serial or "").strip()
    with WORKER_MAP_LOCK:
        old_worker = worker_map.get(serial)
        if old_worker is None:
            return None
        cfg = dict(old_worker.cfg)
        worker_type = type(old_worker)
        _stop_worker_connections(old_worker)
        if old_worker is not threading.current_thread():
            old_worker.join(timeout=5)

        new_worker = worker_type(cfg, mqtt_status_client)
        worker_map[serial] = new_worker
        new_worker.start()
        logging.warning(
            f"[{serial}] Restarted {worker_type.__name__} reason={reason}; "
            "MQTT/source/target connections will be recreated"
        )
        try:
            new_worker.publish_status()
        except Exception:
            pass
        return new_worker


def replace_worker_serial(worker_map, old_serial, new_serial, mqtt_status_client):
    old_serial = str(old_serial or "").strip()
    new_serial = str(new_serial or "").strip()
    if not old_serial or not new_serial:
        return None
    with WORKER_MAP_LOCK:
        old_worker = worker_map.pop(old_serial, None)
        if old_worker is None:
            return None
        cfg = dict(old_worker.cfg)
        cfg["serial"] = new_serial
        cfg.pop("device_serial", None)
        runtime = status_mgr.get_runtime_config(old_serial)
        if runtime:
            status_mgr.update_runtime_config(new_serial, runtime)
        _stop_worker_connections(old_worker)
        old_worker.join(timeout=5)
        new_worker = type(old_worker)(cfg, mqtt_status_client)
        worker_map[new_serial] = new_worker
        new_worker.start()
        logging.warning(f"[{old_serial}] Worker serial changed to {new_serial} without stopping relay process")
        try:
            new_worker.publish_status()
        except Exception:
            pass
        return new_worker


def publish_unconfigured_device_status(client, serial, detail=""):
    """A token may arrive before its source exists in geotek_config.json."""
    status = {
        "serial": serial,
        "name": serial,
        "status": "awaiting_config",
        "version": RELAY_VERSION,
        "is_provisioned": has_license(serial),
        "agent_token_fingerprint": get_license_fingerprint(serial),
        "detail": str(detail or "")[:300],
        "timestamp": time.time(),
    }
    if client and client.is_connected():
        client.publish(f"pi/devices/{serial}/status", json.dumps(status), qos=0, retain=True)


def setup_mqtt(worker_map):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"relay-{int(time.time())}")
    except Exception:
        client = mqtt.Client(client_id=f"relay-{int(time.time())}")
    
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    seen_command_ids = OrderedDict()
    command_lock = threading.Lock()
    active_execution_serials = set()
    
    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            logging.info("✓ MQTT Connected")
            c.subscribe("pi/devices/+/command", qos=1)
            c.subscribe("pi/devices/+/commands", qos=1)
            c.subscribe("pi/device/+/command", qos=1)
            c.subscribe("pi/decoder/esp_base", qos=1)
            try:
                c.publish("pi/decoder/esp_base/sync_request", json.dumps({"ready": True}), qos=1)
            except Exception:
                pass
            logging.info(f"MQTT control broker {MQTT_BROKER}:{MQTT_PORT}; waiting for dashboard commands")
        else:
            logging.error(f"MQTT failed: {rc}")

    def on_message(c, userdata, msg):
        if msg.topic == "pi/decoder/esp_base":
            if bool(getattr(msg, "retain", False)):
                return
            try:
                payload = json.loads((msg.payload or b"{}").decode("utf-8"))
                action = payload.get("action")
                serial = str(payload.get("serial") or '').strip()
                mp = str(payload.get("mountpoint") or '').strip().lstrip('/')
                if not serial or not mp:
                    return
                with WORKER_MAP_LOCK:
                    if action == "start":
                        prefix7 = mp.startswith('7')
                        if prefix7 and (not payload.get('rover_pass') or payload.get('rover_user') != mp):
                            logging.warning('Rejected Base 4G decoder start without Rover credentials')
                            return
                        cfg = {
                            "serial": serial, "mountpoint": mp,
                            "rover_user": payload.get("rover_user") or mp,
                            "rover_pass": payload.get("rover_pass") or "12345",
                            "host": str(payload.get("host") or TARGET_HOST).strip().lower(),
                            "port": int(payload.get("port") or TARGET_PORT),
                            "lat": payload.get("lat", 0.0), "lon": payload.get("lon", 0.0),
                            "alt": payload.get("alt", 0.0),
                        }
                        existing = worker_map.get(serial)
                        connection_keys = ('mountpoint', 'rover_user', 'rover_pass', 'host', 'port')
                        if existing and existing.is_alive() and all(
                            getattr(existing, 'cfg', {}).get(key) == cfg[key] for key in connection_keys
                        ):
                            return
                        if existing:
                            changed = [key for key in connection_keys if existing.cfg.get(key) != cfg[key]]
                            logging.info('Decoder configuration changed for %s: %s', serial, ', '.join(changed) or 'worker stopped')
                            existing.stop()
                            existing.join(timeout=12)
                            if existing.is_alive():
                                logging.warning('Previous decoder still stopping; deferring replacement')
                                return
                        worker = MonitorWorker(cfg, c)
                        worker_map[serial] = worker
                        worker.start()
                        logging.info(f"✓ Dynamically started ESP Decoder Worker: serial={serial}, mp={mp}")
                    elif action == "stop":
                        existing = worker_map.get(serial)
                        if existing:
                            if hasattr(existing, "stop"):
                                existing.stop()
                            try:
                                existing.join(timeout=2)
                            except Exception:
                                pass
                            worker_map.pop(serial, None)
                            logging.info(f"✓ Stopped ESP Decoder Worker: serial={serial}, mp={mp}")
            except Exception as e:
                logging.exception(f"Error handling esp_base decoder control: {e}")
            return

        try:
            payload = json.loads((msg.payload or b"{}").decode("utf-8"))
        except Exception:
            logging.warning(f"Control MQTT non-JSON message on {msg.topic}")
            return
        if not isinstance(payload, dict):
            return

        parts = msg.topic.split("/")
        valid_topic = (
            len(parts) == 4
            and ((parts[0:2] == ["pi", "devices"] and parts[3] in {"command", "commands"})
                 or (parts[0:2] == ["pi", "device"] and parts[3] == "command"))
        )
        if not valid_topic or bool(getattr(msg, "retain", False)):
            return
        serial = str(parts[2] or "").strip()
        if not serial or (payload.get("serial") and str(payload.get("serial")).strip() != serial):
            logging.warning("Rejected control message with invalid topic serial")
            return

        command_id = str(payload.get("command_id") or "")
        now = int(time.time())
        try:
            issued_at = int(payload.get("issued_at"))
            expires_at = int(payload.get("expires_at"))
        except (TypeError, ValueError):
            issued_at = expires_at = 0
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{16,100}", command_id)
            or issued_at > now + 60
            or issued_at < now - 300
            or expires_at < now
            or expires_at > issued_at + 300
        ):
            logging.warning(
                "Rejected control command freshness: id_valid=%s issued_at=%s expires_at=%s now=%s",
                bool(re.fullmatch(r"[A-Za-z0-9_-]{16,100}", command_id)), issued_at, expires_at, now,
            )
            return
        with command_lock:
            if command_id in seen_command_ids:
                logging.warning("Rejected replayed MQTT command id")
                return
            seen_command_ids[command_id] = expires_at
            while len(seen_command_ids) > 2048:
                seen_command_ids.popitem(last=False)
            for old_id, old_expiry in list(seen_command_ids.items()):
                if old_expiry < now:
                    seen_command_ids.pop(old_id, None)

        worker = worker_map.get(serial)
        if serial.upper().startswith('MONITOR_7') or (isinstance(worker, MonitorWorker) and worker.is_prefix7):
            logging.warning('Rejected device command for read-only Base 4G')
            return
        command = str(payload.get("command", "")).strip().upper()
        if command != "SET_RTCM_STREAM_ACTIVE" or serial in worker_map:
            logging.info(f"Control MQTT received topic={msg.topic} command={command}")

        if command == "EXECUTE_RAW_COMMANDS":
            if not serial:
                return

            worker = worker_map.get(serial)
            if not worker:
                logging.warning(f"[{serial}] Command received but no active worker. Known serials: {list(worker_map.keys())}")
                return

            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            commands_b64 = cmd_payload.get("commands_b64")
            if commands_b64 and hasattr(worker, "execute_commands"):
                with command_lock:
                    if serial in active_execution_serials:
                        logging.warning("Rejected concurrent receiver command execution for %s", serial)
                        return
                    active_execution_serials.add(serial)
                logging.info(f"[{serial}] Executing remote commands from Dashboard in background thread")
                import threading

                def execute_once():
                    try:
                        worker.execute_commands(commands_b64, payload.get("original_config"))
                    finally:
                        with command_lock:
                            active_execution_serials.discard(serial)

                threading.Thread(
                    target=execute_once,
                    daemon=True
                ).start()
            return

        if command == "DEPLOY_SERVICE_CONFIG":
            if not serial:
                return

            worker = worker_map.get(serial)
            if not worker or not hasattr(worker, "_persist_remote_config"):
                cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
                runtime = status_mgr.get_runtime_config(serial)
                runtime.update(_mqtt_source_persistable_config(cmd_payload))
                status_mgr.update_runtime_config(serial, runtime)
                publish_unconfigured_device_status(c, serial, "Service config saved; waiting for geotek source config")
                logging.warning(f"[{serial}] Service config saved without active worker. Known serials: {list(worker_map.keys())}")
                return

            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            logging.info(f"[{serial}] Persisting service config from Dashboard")
            worker._persist_remote_config({"payload": {"service_config": cmd_payload}})
            worker.publish_status()
            return

        if command == "PROVISION_DEVICE":
            if not serial:
                return

            worker = worker_map.get(serial)
            if not worker or not hasattr(worker, "apply_provision_payload"):
                cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
                runtime = status_mgr.get_runtime_config(serial)
                name = str(cmd_payload.get("name") or cmd_payload.get("display_name") or "").strip()
                if name:
                    runtime.update({"name": name, "display_name": name})
                    status_mgr.update_runtime_config(serial, runtime)
                publish_unconfigured_device_status(c, serial, "Provision saved; waiting for geotek source config")
                logging.warning(f"[{serial}] Provision saved without active worker. Known serials: {list(worker_map.keys())}")
                return

            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            worker.apply_provision_payload(cmd_payload)
            logging.info(f"[{serial}] Applied provision payload from Dashboard")
            return
            
        if command == "DEPLOY_LICENSE":
            if not serial:
                return
            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            license_key = cmd_payload.get("license_key")
            if license_key:
                try:
                    license_path = save_license(serial, license_key)
                    worker = worker_map.get(serial)
                    if worker and hasattr(worker, "publish_status"):
                        worker.publish_status()
                    else:
                        publish_unconfigured_device_status(c, serial, "License saved; waiting for geotek source config")
                    logging.info(f"[{serial}] Received and applied new license key at {license_path}; relay remains running")
                except Exception as e:
                    logging.exception(f"[{serial}] Failed to save license key: {e}")
            return

        if command in {"REBOOT_DEVICE", "RESTART_DEVICE", "RESTART_MQTT"}:
            worker = worker_map.get(serial)
            if worker is None:
                publish_unconfigured_device_status(c, serial, f"{command} ignored: no configured worker")
                logging.warning(f"[{serial}] {command} received but no configured worker exists")
            elif worker.is_alive():
                restart_worker_connections(worker, reason=command.lower())
            else:
                restart_worker(worker_map, serial, c, reason=f"{command.lower()}_dead_worker")
            return

        if command == "CHANGE_SERIAL":
            if not serial:
                return
            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            new_serial = cmd_payload.get("new_serial")
            if new_serial:
                try:
                    updated = config_mgr.update_mqtt_source_config(serial, {"serial": new_serial})
                    if updated:
                        worker = replace_worker_serial(worker_map, serial, new_serial, c)
                        if worker is None:
                            publish_unconfigured_device_status(c, new_serial, "Serial saved; waiting for geotek source config")
                        logging.info(f"[{serial}] Received CHANGE_SERIAL command. Applied new serial '{new_serial}' without exiting")
                except Exception as e:
                    logging.exception(f"[{serial}] Failed to update serial: {e}")
            return

        if command == "SET_CONFIGURED_RELAY_ACTIVE":
            worker = worker_map.get(serial)
            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            active = cmd_payload.get("active")
            if not isinstance(worker, RelayWorker) or not isinstance(active, bool):
                logging.warning(f"[{serial}] Ignore invalid configured-relay power command")
                return
            worker.set_station_active(active)
            logging.info(f"[{serial}] Configured relay station active={active}")
            return

        if command != "SET_RTCM_STREAM_ACTIVE":
            return

        if not serial:
            return

        worker = worker_map.get(serial)
        if not worker:
            return

        cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
        active = bool(cmd_payload.get("active", False))
        cmd_mp = str(cmd_payload.get("mountpoint", "")).strip()
        worker_mp = _worker_mountpoint(worker)
        if cmd_mp and worker_mp and cmd_mp.lower() != worker_mp.lower():
            logging.info(f"[{serial}] Ignore SET_RTCM_STREAM_ACTIVE for mountpoint={cmd_mp}, worker_mp={worker_mp}")
            return

        try:
            worker.set_stream_active(active, mountpoint=cmd_mp or None)
        except TypeError:
            worker.set_stream_active(active)
        logging.info(f"[{serial}] SET_RTCM_STREAM_ACTIVE active={active} mountpoint={cmd_mp or worker_mp}")
        return

    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        return client
    except Exception as e:
        logging.exception(f"MQTT error: {e}")
        return None

# ==============================================================================
# === MAIN ===
# ==============================================================================
if __name__ == "__main__":
    print("="*70)
    print(" 🚀 NTRIP RELAY SYSTEM - WORKING VERSION")
    print("="*70)
    print(f" Source: {SOURCE_HOST}:{SOURCE_PORT}")
    print(f" Target: {TARGET_HOST}:{TARGET_PORT}")
    print(f" Protocol: NTRIP v{NTRIP_VERSION}.0 (SOURCE command)")
    print(f" Relays: {len(config_mgr.data.get('station_list', []))} | Monitors: {len(config_mgr.data.get('monitor_list', []))} | MQTT Sources: {len(config_mgr.data.get('mqtt_source_list', []))}")
    print("="*70)
    
    mqtt_client = setup_mqtt(ACTIVE_WORKERS)
    
    workers = []
    try:
        station_list = config_mgr.data.get("station_list", [])
        monitor_list = config_mgr.data.get("monitor_list", [])

        for cfg in station_list:
            worker = RelayWorker(cfg, mqtt_client)
            ACTIVE_WORKERS[worker.serial] = worker
            worker.start()
            workers.append(worker)
            logging.info(f"✓ Started Relay: {cfg['source_mp']} → {cfg['target_mp']}")
            worker.publish_status()
            time.sleep(1)
        
        for cfg in monitor_list:
            if str(cfg.get('mountpoint') or '').strip().lstrip('/').startswith('7'):
                # Backend owns these workers and supplies current Rover credentials.
                continue
            worker = MonitorWorker(cfg, mqtt_client)
            ACTIVE_WORKERS[worker.serial] = worker
            worker.start()
            workers.append(worker)
            logging.info(f"✓ Started Monitor: {cfg['mountpoint']}")
            worker.publish_status()
            time.sleep(1)
        
        mqtt_source_list = config_mgr.data.get("mqtt_source_list", [])
        for cfg in mqtt_source_list:
            worker = MqttSourceWorker(cfg, mqtt_client)
            ACTIVE_WORKERS[worker.serial] = worker
            worker.start()
            workers.append(worker)
            logging.info(f"✓ Started MQTT Source: {cfg['topic']} → {cfg['target_mp']}")
            worker.publish_status()
            time.sleep(1)
            
        logging.info("="*70)
        logging.info("🚀 ALL WORKERS RUNNING")
        logging.info("="*70)
        
        last_gc = time.time()
        last_sys_stat = 0
        last_worker_health = 0
        while True:
            time.sleep(1)
            
            # Cập nhật thông số hệ thống mỗi 5s
            if time.time() - last_sys_stat > 5:
                status_mgr.update_system_status()
                last_sys_stat = time.time()

            # A worker exception must not require a human to rerun this file.
            # Recreate the worker object so MQTT subscriptions and sockets are
            # rebuilt from scratch.
            if time.time() - last_worker_health > 5:
                with WORKER_MAP_LOCK:
                    worker_snapshot = list(ACTIVE_WORKERS.items())
                for serial, worker in worker_snapshot:
                    if not worker.is_alive():
                        logging.error(f"[{serial}] Worker thread stopped unexpectedly; auto-restarting")
                        restart_worker(ACTIVE_WORKERS, serial, mqtt_client, reason="supervisor_dead_thread")
                last_worker_health = time.time()

            # Tối ưu dọn rác bộ nhớ (Garbage Collector) mỗi 60s
            if time.time() - last_gc > 60:
                gc.collect()
                last_gc = time.time()
    
    except KeyboardInterrupt:
        print("\n🛑 Stopping...")
        
        with WORKER_MAP_LOCK:
            active_snapshot = list(ACTIVE_WORKERS.values())
        for w in active_snapshot:
            _stop_worker_connections(w)
        for w in active_snapshot:
            w.join(timeout=3)
        
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        
        print("✅ Done")
