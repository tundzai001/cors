# geotek_aitogy_working.py
RELAY_VERSION = "V1.1.1"

import json
import logging
import socket
import threading
import time
import base64
import select
import os
import ctypes
import gc
import psutil
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', force=True)

ACTIVE_WORKERS = {}

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
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                logging.info(f"✓ Loaded config from {self.config_path}")
            except Exception as e:
                logging.error(f"Error loading config: {e}")
        else:
            logging.warning(f"Config file {self.config_path} not found. Using defaults.")
            self.data = {
                "mqtt": {"broker": "aitogy.click", "port": 1883, "user": "mqttUser", "pass": "MqttPassword123$%^"},
                "ntrip": {
                    "source_host": "rtkvietnam.vn", "source_port": 1508, "source_user": "aitogy123", "source_pass": "aitogy123",
                    "target_host": "aitogy.com.vn", "target_port": 2101, "target_pass": "12345",
                    "version": 1, "reconnect_interval": 15
                },
                "station_list": [],
                "monitor_list": []
            }

    def save(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Error saving config: {e}")

class StatusManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.status_data = {"workers": {}, "system": {}, "last_update": ""}
        self._lock = threading.Lock()
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
            self._save()

    def get_runtime_config(self, serial):
        with self._lock:
            runtime = self.status_data.setdefault("runtime_config", {}).get(serial, {})
            return dict(runtime) if isinstance(runtime, dict) else {}

    def update_runtime_config(self, serial, runtime_config):
        with self._lock:
            self.status_data.setdefault("runtime_config", {})[serial] = runtime_config
            self.status_data["last_update"] = datetime.now().isoformat()
            self._save()

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
                self._save()
            except Exception as e:
                logging.debug(f"System status update error: {e}")

    def _save(self):
        tmp_path = self.db_path + ".tmp"
        last_error = None
        for _ in range(5):
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(self.status_data, f, indent=4, ensure_ascii=False)
                os.replace(tmp_path, self.db_path)
                return
            except Exception as e:
                last_error = e
                time.sleep(0.1)
        logging.error(f"Error saving status DB: {last_error}")

# Paths (Current directory of the script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "geotek_config.json")
DB_PATH = os.path.join(SCRIPT_DIR, "geotek_db.json")

# Initialize Managers
config_mgr = ConfigManager(CONFIG_PATH)
status_mgr = StatusManager(DB_PATH)

# Global Settings (From Config)
MQTT_BROKER = config_mgr.data.get("mqtt", {}).get("broker", "aitogy.click")
MQTT_PORT = config_mgr.data.get("mqtt", {}).get("port", 1883)
MQTT_USER = config_mgr.data.get("mqtt", {}).get("user", "mqttUser")
MQTT_PASS = config_mgr.data.get("mqtt", {}).get("pass", "MqttPassword123$%^")

SOURCE_HOST = config_mgr.data.get("ntrip", {}).get("source_host", "rtkvietnam.vn")
SOURCE_PORT = config_mgr.data.get("ntrip", {}).get("source_port", 1508)
SOURCE_USER = config_mgr.data.get("ntrip", {}).get("source_user", "aitogy123")
SOURCE_PASS = config_mgr.data.get("ntrip", {}).get("source_pass", "aitogy123")

TARGET_HOST = config_mgr.data.get("ntrip", {}).get("target_host", "aitogy.com.vn")
TARGET_PORT = config_mgr.data.get("ntrip", {}).get("target_port", 2101)
TARGET_PASS = config_mgr.data.get("ntrip", {}).get("target_pass", "12345")

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
    
    while b"\r\n\r\n" not in header_data:
        if time.time() - start_time > timeout:
            raise Exception("Timeout")
        try:
            chunk = sock.recv(1024)
            if not chunk:
                raise Exception("Connection closed")
            header_data += chunk
        except socket.timeout:
            raise Exception("Socket timeout")
    
    header_end = header_data.find(b"\r\n\r\n") + 4
    return header_data[:header_end], header_data[header_end:]

def crc24q(data):
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF

def extract_rtcm3_frames(buffer):
    frames = []
    while True:
        start = buffer.find(b"\xd3")
        if start < 0:
            buffer.clear()
            return frames
        if start > 0:
            del buffer[:start]
        if len(buffer) < 6:
            return frames
        if buffer[1] & 0xFC:
            del buffer[0]
            continue

        payload_len = ((buffer[1] & 0x03) << 8) | buffer[2]
        total_len = 3 + payload_len + 3
        if payload_len > 1023:
            del buffer[0]
            continue
        if len(buffer) < total_len:
            if len(buffer) > 8192:
                del buffer[:-total_len]
            return frames

        frame = bytes(buffer[:total_len])
        expected_crc = int.from_bytes(frame[-3:], "big")
        if crc24q(frame[:-3]) == expected_crc:
            frames.append(frame)
            del buffer[:total_len]
        else:
            del buffer[0]

class BitReader:
    def __init__(self, data):
        self.data = data
        self.bitpos = 0

    def remaining(self):
        return (len(self.data) * 8) - self.bitpos

    def read_uint(self, bits):
        if bits < 0 or self.remaining() < bits:
            raise ValueError("Not enough bits")
        value = 0
        for _ in range(bits):
            byte_index = self.bitpos // 8
            bit_index = 7 - (self.bitpos % 8)
            value = (value << 1) | ((self.data[byte_index] >> bit_index) & 1)
            self.bitpos += 1
        return value

    def skip(self, bits):
        if bits < 0 or self.remaining() < bits:
            raise ValueError("Not enough bits")
        self.bitpos += bits

RTCM_MSM_SYSTEMS = {
    107: "us",
    108: "ru",
    109: "eu",
    112: "cn",
}

def rtcm_message_type(frame):
    if len(frame) < 5:
        return None
    payload = frame[3:-3]
    if len(payload) < 2:
        return None
    return (payload[0] << 4) | (payload[1] >> 4)

def parse_rtcm_msm_satellites(frame):
    """
    Decode enough RTCM MSM4 data to expose satellites and CNR for monitoring.
    This intentionally does not compute azimuth/elevation; MSM does not carry it directly.
    """
    msg_type = rtcm_message_type(frame)
    if msg_type is None:
        return []

    family = msg_type // 10
    msm_version = msg_type % 10
    system = RTCM_MSM_SYSTEMS.get(family)
    if not system or msm_version != 4:
        return []

    try:
        reader = BitReader(frame[3:-3])
        reader.skip(12)  # message type
        reader.skip(12)  # station id
        reader.skip(30)  # epoch time
        reader.skip(1)   # multiple message bit
        reader.skip(3)   # issue of data station
        reader.skip(7)   # reserved
        reader.skip(2)   # clock steering
        reader.skip(2)   # external clock
        reader.skip(1)   # smoothing indicator
        reader.skip(3)   # smoothing interval

        satellite_ids = [index + 1 for index in range(64) if reader.read_uint(1)]
        signal_ids = [index + 1 for index in range(32) if reader.read_uint(1)]
        if not satellite_ids or not signal_ids:
            return []

        cells = []
        for sat_id in satellite_ids:
            for signal_id in signal_ids:
                if reader.read_uint(1):
                    cells.append((sat_id, signal_id))

        cell_count = len(cells)
        if cell_count <= 0:
            return []

        # MSM4 satellite data groups.
        reader.skip(len(satellite_ids) * 8)   # rough range integer milliseconds

        # MSM4 cell data groups before CNR.
        reader.skip(cell_count * 15)  # fine pseudorange
        reader.skip(cell_count * 22)  # fine phase range
        reader.skip(cell_count * 4)   # lock time
        reader.skip(cell_count * 1)   # half-cycle ambiguity

        now = time.time()
        by_sat = {}
        for sat_id, signal_id in cells:
            cnr = reader.read_uint(6)
            existing = by_sat.get(sat_id)
            if existing is None or cnr > existing["snr"]:
                by_sat[sat_id] = {
                    "prn": sat_id,
                    "system": system,
                    "signal": signal_id,
                    "snr": cnr,
                    "cnr": cnr,
                    "elevation": 0,
                    "azimuth": 0,
                    "isTracking": True,
                    "source": "RTCM_MSM",
                    "rtcm_type": msg_type,
                    "last_seen": now,
                }
        return list(by_sat.values())
    except Exception:
        return []

def command_delay_seconds(command_bytes):
    if command_bytes.startswith(b"$DELAY_") and command_bytes.endswith(b"$"):
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
    for line in iter_nmea_sentences(payload):
        sentence = line.split("*", 1)[0]
        parts = sentence.split(",")
        if len(parts) < 18 or not parts[0].endswith("GSA"):
            continue
        try:
            active_sats = []
            for value in parts[3:15]:
                if value:
                    active_sats.append(int(value))
            return {
                "type": "GSA",
                "active_sats": active_sats,
                "pdop": float(parts[15]) if parts[15] else 99.99,
                "hdop": float(parts[16]) if parts[16] else 99.99,
                "vdop": float(parts[17]) if parts[17] else 99.99,
                "raw": line,
                "ts": time.time(),
            }
        except Exception:
            continue
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
            chunk = fields[index:index + 4]
            if len(chunk) < 4 or not chunk[0]:
                continue
            try:
                satellites.append({
                    "prn": int(chunk[0]),
                    "system": system,
                    "elevation": int(chunk[1]) if chunk[1] else 0,
                    "azimuth": int(chunk[2]) if chunk[2] else 0,
                    "snr": int(chunk[3]) if chunk[3] else 0,
                    "isTracking": False,
                    "last_seen": time.time(),
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

def build_geotek_lte_unicore_commands(original_config=None, runtime_cfg=None):
    """
    Local command builder for every JSON-configured Geotek LTE source.

    The dashboard can still send encoded commands, but this relay is the device
    boundary for geotek_config.json sources. Rebuild the command list here so all
    LTE units use the same tested COM2 profile regardless of their serial.
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

    lat = _as_float(_first_value(coords.get("lat"), params.get("lat"), runtime_cfg.get("lat"), 0.0), 0.0)
    lon = _as_float(_first_value(coords.get("lon"), params.get("lon"), runtime_cfg.get("lon"), 0.0), 0.0)
    alt = _as_float(_first_value(coords.get("alt"), params.get("alt"), runtime_cfg.get("alt"), 0.0), 0.0)

    rtcm_port = str(_first_value(runtime_cfg.get("rtcm_port"), runtime_cfg.get("sensor_port"), "com2")).strip() or "com2"
    gga_port = str(_first_value(runtime_cfg.get("nmea_gga_port"), runtime_cfg.get("nmea_port"), runtime_cfg.get("sensor_port"), rtcm_port)).strip() or rtcm_port

    commands = [
        b"FRESET\r\n",
        b"$DELAY_8000$",
        b"unlogall\r\n",
        b"$DELAY_1000$",
    ]

    if method == "FIXED_LLA":
        commands.extend([
            f"mode base {lat} {lon} {alt}\r\n".encode("ascii"),
            b"$DELAY_2000$",
        ])
    else:
        commands.extend([
            f"mode base time {duration}\r\n".encode("ascii"),
            b"$DELAY_2000$",
        ])

    # Keep LTE stable: GGA is enough for position; satellite view is decoded from RTCM MSM.
    commands.extend([
        f"gpgga {gga_port} 1\r\n".encode("ascii"),
        b"$DELAY_500$",
    ])

    for msg_id in ("1006", "1033", "1074", "1124", "1084", "1094", "1042", "1019", "1020", "1045"):
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
        self.last_stat_update = time.time()
        self.current_bps = 0
        
        self.name = f"Relay-{self.cfg['target_mp']}"
        self.desired_active = False
        self.wakeup_event = threading.Event()
        self.socket_lock = threading.Lock()
        self._source_sock = None
        self._target_sock = None

    def _source_host(self):
        return self.cfg.get('source_host', SOURCE_HOST)

    def _source_port(self):
        return int(self.cfg.get('source_port', SOURCE_PORT))

    def _source_user(self):
        return self.cfg.get('source_user', SOURCE_USER)

    def _source_pass(self):
        return self.cfg.get('source_pass', SOURCE_PASS)

    def _target_host(self):
        return self.cfg.get('target_host', TARGET_HOST)

    def _target_port(self):
        return int(self.cfg.get('target_port', TARGET_PORT))

    def _target_pass(self):
        return self.cfg.get('target_pass', TARGET_PASS)
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['target_mp']}] {msg}")
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            pass # Vẫn cập nhật DB cục bộ dù mất MQTT
        
        status = {
            "serial": self.serial,
            "name": self.cfg['target_mp'],
            "status": "online" if self.desired_active else "sleep",
            "version": RELAY_VERSION,
            "timestamp": int(time.time()),
            "base_config": {
                "base_setup_method": "FIXED_LLA",
                "coords": {
                    "lat": self.cfg['lat'],
                    "lon": self.cfg['lon'],
                    "alt": self.cfg['alt']
                }
            },
            "ntrip_connected": self.is_connected,
            "ntrip_stats": {"server1_bps": self.current_bps},
            "ntrip_status": {"server1": {"connected": self.is_connected, "bps": self.current_bps}},
            "service_config": {
                "server1_enabled": True,
                "mountpoint1": self.cfg['target_mp'],
                "serverhost1": self._target_host(),
                "stream_on_demand": True,
                "stream_active": bool(self.desired_active),
                "active_mountpoint": self.cfg['target_mp'] if self.desired_active else None
            },
            "source_mountpoint": self.cfg['source_mp'],
            "rtk_fix_status": "FIXED",
            "detected_chip_type": "RELAY",
            "is_provisioned": True
        }
        
        try:
            if self.mqtt_client and self.mqtt_client.is_connected():
                self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
            # Cập nhật DB cục bộ
            status_mgr.update_worker_status(self.serial, status)
        except Exception as e:
            logging.debug(f"Error publishing/saving status: {e}")

    def set_stream_active(self, active: bool):
        self.desired_active = bool(active)
        if not self.desired_active:
            if self.cfg.get("source_on_demand"):
                self._close_live_sockets()
            else:
                self._close_target_socket()
        self.wakeup_event.set()
        self.publish_status()

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
            source_sock = None
            target_sock = None
            try:
                if self.cfg.get("source_on_demand") and not self.desired_active:
                    self.is_connected = False
                    self.current_bps = 0
                    self.publish_status()
                    self.log("INFO", "Relay is sleeping; waiting for wake command before connecting SOURCE.")
                    self.wakeup_event.wait(RECONNECT_INTERVAL)
                    self.wakeup_event.clear()
                    continue

                source_host = self._source_host()
                source_port = self._source_port()
                source_user = self._source_user()
                source_pass = self._source_pass()
                target_host = self._target_host()
                target_port = self._target_port()
                target_pass = self._target_pass()
                # === LUÔN KẾT NỐI SOURCE VÀ LẤY DATA (Kể cả khi bị OFF trên web) ===
                self.log("INFO", f"Connecting SOURCE: {source_host}:{source_port}/{self.cfg['source_mp']}")
                source_sock = socket.create_connection((source_host, source_port), timeout=10)
                with self.socket_lock:
                    self._source_sock = source_sock
                
                auth = base64.b64encode(f"{source_user}:{source_pass}".encode()).decode()
                source_request = (
                    f"GET /{self.cfg['source_mp']} HTTP/1.1\r\n"
                    f"Host: {source_host}\r\n"
                    f"Authorization: Basic {auth}\r\n"
                    f"User-Agent: NTRIP Relay/1.0\r\n"
                    f"Connection: close\r\n\r\n"
                )
                source_sock.sendall(source_request.encode())
                
                src_header, leftover = read_http_header(source_sock)
                if b"200 OK" not in src_header and b"ICY 200" not in src_header:
                    raise Exception("Source rejected")
                
                gga = generate_gga(self.cfg['lat'], self.cfg['lon'], self.cfg['alt'])
                source_sock.sendall(gga.encode())
                
                self.log("SUCCESS", "✓ Source connected")
                
                self.is_connected = True
                self.publish_status()
                
                last_gga = time.time()
                self.bytes_sent = 0
                self.last_stat_update = time.time()
                
                source_sock.setblocking(False)
                
                while not self.stop_event.is_set():
                    # 1. KẾT NỐI TARGET NẾU TRẠM BẬT (desired_active = True) MÀ CHƯA KẾT NỐI
                    if self.desired_active and target_sock is None:
                        self.log("INFO", f"Connecting TARGET: {target_host}:{target_port}/{self.cfg['target_mp']} (v1.0)")
                        try:
                            target_sock = socket.create_connection((target_host, target_port), timeout=10)
                            with self.socket_lock:
                                self._target_sock = target_sock
                            
                            target_request = (
                                f"SOURCE {target_pass} /{self.cfg['target_mp']}\r\n"
                                f"Source-Agent: NTRIP NtripSource/1.0\r\n\r\n"
                            )
                            target_sock.sendall(target_request.encode())
                            response = target_sock.recv(4096)
                            if not (b"ICY 200" in response or b"200 OK" in response):
                                raise Exception(f"Target rejected: {response.decode(errors='ignore')[:100]}")
                            
                            self.log("SUCCESS", f"✅ RELAY ACTIVE: {self.cfg['source_mp']} → {self.cfg['target_mp']}")
                            if leftover:
                                target_sock.sendall(leftover)
                                leftover = None
                        except Exception as e:
                            self.log("WARNING", f"Target connection failed: {e}")
                            if target_sock:
                                try: target_sock.close()
                                except: pass
                                target_sock = None
                                with self.socket_lock:
                                    self._target_sock = None
                    
                    # 2. NGẮT TARGET NẾU TRẠM TẮT (desired_active = False)
                    if not self.desired_active and target_sock is not None:
                        self.log("INFO", "Stream deactivated -> closing TARGET socket only.")
                        try: target_sock.shutdown(socket.SHUT_RDWR)
                        except: pass
                        try: target_sock.close()
                        except: pass
                        target_sock = None
                        with self.socket_lock:
                            self._target_sock = None
                        
                    if time.time() - last_gga > 10:
                        try:
                            source_sock.sendall(gga.encode())
                            last_gga = time.time()
                        except:
                            break
                    
                    # 3. LUÔN ĐỌC RAW DATA TỪ SOURCE
                    readable, _, exceptional = select.select([source_sock], [], [source_sock], 1.0)
                    if exceptional:
                        break
                    
                    if readable:
                        try:
                            data = source_sock.recv(4096)
                            if not data:
                                self.log("WARNING", "Source EOF")
                                break
                            
                            # 4. CHỈ GỬI ĐI NẾU ĐANG BẬT TARGET
                            if target_sock is not None:
                                try:
                                    target_sock.sendall(data)
                                except Exception as e:
                                    self.log("WARNING", f"Error sending to target: {e}")
                                    try: target_sock.close()
                                    except: pass
                                    target_sock = None
                                    with self.socket_lock:
                                        self._target_sock = None
                            
                            self.bytes_sent += len(data)
                            
                            # Tính BPS thật sự
                            now = time.time()
                            if now - self.last_stat_update >= 1.0:
                                self.current_bps = int(self.bytes_sent / (now - self.last_stat_update))
                                # self.log("INFO", f"[SOURCE] {self.current_bps} B/s")
                                self.bytes_sent = 0
                                self.last_stat_update = now
                                self.publish_status()
                                
                        except BlockingIOError:
                            continue
                        except Exception as e:
                            self.log("WARNING", f"Data error: {e}")
                            break
                    else:
                        if target_sock is not None:
                            try: target_sock.sendall(b'\r\n')
                            except: 
                                try: target_sock.close()
                                except: pass
                                target_sock = None
                                with self.socket_lock:
                                    self._target_sock = None
                
                self.log("INFO", f"Source loop ended, reconnecting in {RECONNECT_INTERVAL}s...")
                self.wakeup_event.wait(RECONNECT_INTERVAL)
                self.wakeup_event.clear()
                
            except Exception as e:
                self.log("WARNING", f"Connection error: {e}")
                self.wakeup_event.wait(RECONNECT_INTERVAL)
                self.wakeup_event.clear()
                
            finally:
                self.is_connected = False
                self.current_bps = 0
                if source_sock:
                    try: source_sock.close()
                    except: pass
                if target_sock:
                    try: target_sock.close()
                    except: pass
                with self.socket_lock:
                    self._source_sock = None
                    self._target_sock = None
                
                self.publish_status()

# ==============================================================================
# === MONITOR WORKER (DÃ nh cho tráº¡m Ä‘Ã£ cÃ³ sáºµn trÃªn server) ===
# ==============================================================================
class MonitorWorker(threading.Thread):
    def __init__(self, station_config, mqtt_client):
        super().__init__()
        self.cfg = station_config
        self.mqtt_client = mqtt_client
        self.daemon = True
        self.stop_event = threading.Event()
        
        self.serial = f"MONITOR_{self.cfg['mountpoint'].replace('.', '_')}"
        self.topic_status = f"pi/devices/{self.serial}/status"
        
        self.is_connected = False
        self.bytes_received = 0
        self.last_stat_update = time.time()
        self.last_data_received_at = 0.0
        self.max_no_data_seconds = 12.0
        self.current_bps = 0
        
        self.name = f"Monitor-{self.cfg['mountpoint']}"
        self.desired_active = True
        self.wakeup_event = threading.Event()
        self.socket_lock = threading.Lock()
        self._sock = None
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['mountpoint']}] {msg}")
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            pass

        if self.is_connected:
            current_status = "online" if self.current_bps > 0 else "sleep"
        else:
            current_status = "offline"
        
        status = {
            "serial": self.serial,
            "name": self.cfg['mountpoint'],
            "status": current_status,
            "version": RELAY_VERSION,
            "timestamp": int(time.time()),
            "base_config": {
                "base_setup_method": "FIXED_LLA",
                "coords": {
                    "lat": self.cfg['lat'],
                    "lon": self.cfg['lon'],
                    "alt": self.cfg.get('alt', 0.0)
                }
            },
            "ntrip_connected": self.is_connected,
            "ntrip_stats": {"server1_bps": self.current_bps},
            "ntrip_status": {"server1": {"connected": self.is_connected, "bps": self.current_bps}},
            "service_config": {
                "server1_enabled": True,
                "mountpoint1": self.cfg['mountpoint'],
                "serverhost1": TARGET_HOST,
                "stream_on_demand": True,
                "stream_active": bool(self.is_connected and self.current_bps > 0),
                "active_mountpoint": self.cfg['mountpoint']
            },
            "source_mountpoint": self.cfg['mountpoint'],
            "rtk_fix_status": "FIXED",
            "detected_chip_type": "Ublox",
            "is_provisioned": True
        }
        
        try:
            self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
        except:
            pass

    def set_stream_active(self, active: bool):
        # Ignore for MonitorWorker, it always runs
        pass

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
    
    def run(self):
        while not self.stop_event.is_set():
            sock = None
            try:
                self.log("INFO", f"Connecting MONITOR: {TARGET_HOST}:{TARGET_PORT}/{self.cfg['mountpoint']}")
                sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
                with self.socket_lock:
                    self._sock = sock
                
                # Cấu trúc y hệt chuẩn thiết bị Rover Ntrip 1.0 cổ điển:
                r_user = self.cfg.get('rover_user', 'aitogy')
                r_pass = self.cfg.get('rover_pass', TARGET_PASS)
                auth = base64.b64encode(f"{r_user}:{r_pass}".encode()).decode()
                request = f"GET /{self.cfg['mountpoint']} HTTP/1.0\r\nUser-Agent: NTRIP NtripClient/1.0\r\nAuthorization: Basic {auth}\r\n\r\n"
                sock.sendall(request.encode())
                
                gga = generate_gga(self.cfg['lat'], self.cfg['lon'], self.cfg.get('alt', 0.0))
                sock.sendall(gga.encode())
                
                self.is_connected = True
                self.publish_status()
                self.log("SUCCESS", f"✅ MONITOR ACTIVE: {self.cfg['mountpoint']}")
                
                self.bytes_received = 0
                self.last_stat_update = time.time()
                self.last_data_received_at = time.time()
                
                sock.settimeout(10.0)
                while not self.stop_event.is_set():
                    try:
                        data = sock.recv(4096)
                        if not data:
                            self.log("WARNING", "Monitor EOF (Server tu choi hoac Mountpoint khong ton tai)")
                            break
                        
                        self.bytes_received += len(data)
                        now = time.time()
                        self.last_data_received_at = now
                        if now - self.last_stat_update >= 1.0:
                            self.current_bps = int(self.bytes_received / (now - self.last_stat_update))
                            # if self.current_bps > 0:
                            #     self.log("INFO", f"[MONITOR] {self.current_bps} B/s")
                            self.bytes_received = 0
                            self.last_stat_update = now
                            self.is_connected = True
                            self.publish_status()
                            
                    except socket.timeout:
                        self.current_bps = 0
                        no_data_for = time.time() - self.last_data_received_at
                        # If source has no RTCM data for too long, treat it as offline and force reconnect.
                        if no_data_for >= self.max_no_data_seconds:
                            self.is_connected = False
                            self.publish_status()
                            self.log("WARNING", f"No RTCM data for {no_data_for:.1f}s -> mark OFFLINE and reconnect")
                            break
                        self.is_connected = True
                        self.publish_status()
                    except Exception as e:
                        self.log("WARNING", f"Data error: {e}")
                        break
                        
                self.log("INFO", f"Monitor disconnected, reconnecting in {RECONNECT_INTERVAL}s...")
                self.wakeup_event.wait(RECONNECT_INTERVAL)
                self.wakeup_event.clear()
            except Exception as e:
                self.log("WARNING", f"Connection error: {e}")
                self.wakeup_event.wait(RECONNECT_INTERVAL)
                self.wakeup_event.clear()
            finally:
                self.is_connected = False
                self.current_bps = 0
                if sock:
                    try: sock.close()
                    except: pass
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
        self.data_timeout = int(self.cfg.get("data_timeout", 30))
        self.socket_lock = threading.Lock()
        self._target_sock = None
        self._target_socks = {}
        self.bytes_sent_by_server = {"server1": 0, "server2": 0}
        self.current_bps_by_server = {"server1": 0, "server2": 0}
        self.mqtt_data_client = None
        self.rtcm_buffer = bytearray()
        self.command_response_condition = threading.Condition()
        self.command_responses = []
        self.command_response_timeout = float(self.cfg.get("command_response_timeout", 8))
        self.command_max_retries = int(self.cfg.get("command_max_retries", 20))
        self.command_batch_active = False
        self.latest_gga = None
        self.latest_gsa = None
        self.satellites = {}

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
                "on_demand": bool(self.cfg.get("server1_stream_on_demand", self.cfg.get("stream_on_demand", True))),
                "active": bool(self.cfg.get("server1_stream_active", self.cfg.get("stream_active", False))),
            }
        return {
            "enabled": bool(self.cfg.get("server2_enabled", False)),
            "host": str(self.cfg.get("serverhost2") or "").strip(),
            "port": self.cfg.get("port2"),
            "password": str(self.cfg.get("password2") or "").strip(),
            "mountpoint": str(self.cfg.get("mountpoint2") or "").strip(),
            "on_demand": bool(self.cfg.get("server2_stream_on_demand", True)),
            "active": bool(self.cfg.get("server2_stream_active", False)),
        }

    def _server_target_enabled(self, server_id):
        cfg = self._server_target_config(server_id)
        stream_allowed = (not cfg["on_demand"]) or cfg["active"]
        return cfg["enabled"] and stream_allowed and bool(cfg["host"] and cfg["port"] and cfg["password"] and cfg["mountpoint"])

    def publish_status(self):
        now = time.time()
        mqtt_data_age = None if self.last_mqtt_data_ts <= 0 else int(now - self.last_mqtt_data_ts)
        mqtt_data_active = mqtt_data_age is not None and mqtt_data_age <= self.data_timeout
        target_enabled = self._target_enabled()
        server1_connected = "server1" in self._target_socks
        server2_connected = "server2" in self._target_socks
        server1_bps = self.current_bps_by_server.get("server1", 0)
        server2_bps = self.current_bps_by_server.get("server2", 0)
        latest_gga = self.latest_gga if isinstance(self.latest_gga, dict) else None
        latest_gsa = self.latest_gsa if isinstance(self.latest_gsa, dict) else None
        satellites = {
            key: sat for key, sat in self.satellites.items()
            if now - float(sat.get("last_seen", 0)) <= 30
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
            "server2_enabled": bool(self.cfg.get("server2_enabled", False)),
            "server2_stream_on_demand": bool(self.cfg.get("server2_stream_on_demand", True)),
            "server2_stream_active": bool(self.cfg.get("server2_stream_active", False)),
            "ntrip_version2": int(self.cfg.get("ntrip_version2", 1) or 1),
            "username2": self.cfg.get("username2") or "",
            "serverhost2": self.cfg.get("serverhost2") or "",
            "port2": self.cfg.get("port2"),
            "mountpoint2": self.cfg.get("mountpoint2") or "",
            "password2": self.cfg.get("password2") or "",
            "rtcm_enabled": bool(self.cfg.get("rtcm_enabled", False)),
            "rtcmntripversion1": int(self.cfg.get("rtcmntripversion1", 1) or 1),
            "rtcmserver1": self.cfg.get("rtcmserver1") or "",
            "rtcmport1": self.cfg.get("rtcmport1"),
            "rtcmmountpoint1": self.cfg.get("rtcmmountpoint1") or "",
            "rtcmusername1": self.cfg.get("rtcmusername1") or "",
            "rtcmpassword1": self.cfg.get("rtcmpassword1") or "",
        }
        status = {
            "serial": self.serial,
            "name": self.name,
            "status": "online" if self.is_connected else ("sleep" if mqtt_data_active else "offline"),
            "version": RELAY_VERSION,
            "timestamp": int(time.time()),
            "base_config": base_config,
            "ntrip_connected": self.is_connected,
            "ntrip_stats": {
                "server1_bps": server1_bps,
                "server2_bps": server2_bps,
                "rtcm_input_bps": self.current_mqtt_bps,
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
            "mqtt_stats": {"source_bps": self.current_mqtt_bps},
            "nmea_data": {
                "gga": latest_gga,
                "gsa": latest_gsa,
                "satellites": satellites,
                "base_coords": base_coords,
                "live_coords": live_coords,
            },
            "gnss_stats": {
                "satellites": len([sat for sat in satellites.values() if sat.get("snr", 0) > 0]) or (latest_gga.get("satellites") if latest_gga else 0),
                "hdop": (latest_gsa or latest_gga or {}).get("hdop"),
                "pdop": latest_gsa.get("pdop") if latest_gsa else None,
            },
            "rtk_fix_status": rtk_fix_status,
            "base_mode_active": mqtt_data_active,
            "detected_chip_type": "Unicorecomm",
            "connection_type": "LTE",
            "is_lte": True
        }
        try:
            if self.mqtt_status_client and self.mqtt_status_client.is_connected():
                self.mqtt_status_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
            status_mgr.update_worker_status(self.serial, status)
        except:
            pass

    def _connect_target(self, server_id=1):
        target_cfg = self._server_target_config(server_id)
        target_host = target_cfg["host"]
        target_port = int(target_cfg["port"])
        target_pass = target_cfg["password"]
        target_mp = target_cfg["mountpoint"]
        server_key = f"server{server_id}"

        self.log("INFO", f"Connecting TARGET NTRIP {server_key} for RTCM output: {target_host}:{target_port}/{target_mp}")
        sock = socket.create_connection((target_host, target_port), timeout=10)
        
        # Handshake chính xác như đã test để có thể gửi lệnh vào luồng RTCM của chip
        target_request = f"SOURCE {target_pass} /{target_mp}\r\nSource-Agent: NTRIP GeotekMqttBridge/1.0\r\n\r\n"
        sock.sendall(target_request.encode())
        
        response = sock.recv(4096)
        if not (b"ICY 200" in response or b"200 OK" in response):
            raise Exception(f"Target rejected SOURCE connection: {response.decode(errors='ignore')[:100]}")
        
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
        if not (b"ICY 200" in response or b"200 OK" in response):
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
                matched_servers.append(server_id)

        if not matched_servers and active:
            matched_servers = [1]

        runtime["stream_on_demand"] = True
        for server_id in matched_servers:
            if active:
                runtime[f"server{server_id}_enabled"] = True
            runtime[f"server{server_id}_stream_on_demand"] = True
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
                for sock in list(self._target_socks.values()):
                    try: sock.shutdown(socket.SHUT_RDWR)
                    except Exception: pass
                    try: sock.close()
                    except Exception: pass
                self._target_socks.clear()
                self._target_sock = None
            self.is_connected = False
        else:
            self.log("INFO", f"Wake received; enabling NTRIP target for mountpoint={mountpoint or self.cfg.get('target_mp')}")
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
            for key, value in service_config.items():
                if value is not None:
                    runtime[key] = value
            runtime["server1_enabled"] = bool(service_config.get("server1_enabled", False))
            runtime["stream_on_demand"] = bool(service_config.get("stream_on_demand", False))
            runtime["server1_stream_active"] = service_config.get("server1_stream_active")

        if method:
            runtime["setup_method"] = method
        if method == "SURVEY_IN":
            duration = params.get("survey_in_duration") or params.get("duration")
            if duration is not None:
                runtime["duration"] = duration
        if method == "FIXED_LLA":
            coords = params.get("coords", {}) if isinstance(params.get("coords"), dict) else params
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

        self.log("INFO", "Saved remote base config into geotek_db.json")

    def execute_commands(self, commands_b64, original_config=None):
        """Inject Geotek LTE chip config commands through the active SOURCE socket."""
        try:
            self._persist_remote_config(original_config)

            decoded_commands = build_geotek_lte_unicore_commands(original_config, self.cfg)
            self.log(
                "INFO",
                "Using local Geotek LTE command builder for JSON source "
                f"(GGA/RTCM on {self.cfg.get('rtcm_port') or self.cfg.get('sensor_port') or 'com2'}; no GSV spam)"
            )

            if not decoded_commands:
                self.log("WARNING", "No commands to inject")
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
                for cmd_bytes in decoded_commands:
                    delay = command_delay_seconds(cmd_bytes)
                    if cmd_bytes.startswith(b"$DELAY_"):
                        time.sleep(delay)
                        continue
                    if not self._send_command_with_ack(command_sock, cmd_bytes):
                        raise TimeoutError(f"Command did not receive OK/no func response: {cmd_bytes.decode(errors='ignore').strip()}")
                    time.sleep(delay)
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
        except Exception as e:
            self.log("ERROR", f"Failed to execute Geotek LTE commands: {e}")

    def _send_config_commands(self, sock):
        self.log("WARNING", "Direct local config path is disabled; MQTT EXECUTE_RAW_COMMANDS triggers the Geotek LTE builder.")
        return
        self.log("INFO", "📡 Executing Geotek LTE specialized configuration...")
        
        setup_method = self.cfg.get('setup_method', 'SURVEY_IN')
        duration = self.cfg.get('duration', 60)
        lat = self.cfg.get('lat', 0.0)
        lon = self.cfg.get('lon', 0.0)
        alt = self.cfg.get('alt', 0.0)
        
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
                
                if b"$DELAY_" in cmd_bytes:
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
        while not self.stop_event.is_set():
            try:
                try:
                    self.mqtt_data_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
                except:
                    self.mqtt_data_client = mqtt.Client()
                
                if self.cfg.get('mqtt_user') and self.cfg.get('mqtt_pass'):
                    self.mqtt_data_client.username_pw_set(self.cfg['mqtt_user'], self.cfg['mqtt_pass'])
                
                def on_message(c, userdata, msg):
                    now = time.time()
                    self.last_mqtt_data_ts = now
                    self.bytes_received += len(msg.payload or b"")
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
                            self.satellites[sat_key] = sat
                    self.rtcm_buffer.extend(msg.payload or b"")
                    rtcm_frames = extract_rtcm3_frames(self.rtcm_buffer)
                    if not rtcm_frames:
                        return

                    for frame in rtcm_frames:
                        for sat in parse_rtcm_msm_satellites(frame):
                            sat_key = f"{sat.get('system', 'un')}:{sat.get('prn')}"
                            self.satellites[sat_key] = sat

                    with self.socket_lock:
                        for server_key, sock in list(self._target_socks.items()):
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

                self.mqtt_data_client.on_message = on_message
                self.mqtt_data_client.connect(self.cfg['mqtt_broker'], int(self.cfg['mqtt_port']), 60)
                self.mqtt_data_client.subscribe(self.cfg['topic'])
                self.log("INFO", f"Subscribed MQTT source topic: {self.cfg['topic']}")
                self.mqtt_data_client.loop_start()

                while not self.stop_event.is_set():
                    now = time.time()
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
                time.sleep(RECONNECT_INTERVAL)
            finally:
                if self.mqtt_data_client:
                    try:
                        self.mqtt_data_client.loop_stop()
                        self.mqtt_data_client.disconnect()
                    except: pass
                with self.socket_lock:
                    for sock in list(self._target_socks.values()):
                        try: sock.close()
                        except: pass
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

def setup_mqtt(worker_map):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"relay-{int(time.time())}")
    except:
        client = mqtt.Client(client_id=f"relay-{int(time.time())}")
    
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    seen_status_commands = {}
    
    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            logging.info("âœ“ MQTT Connected")
            c.subscribe("pi/devices/+/command", qos=1)
            c.subscribe("pi/devices/+/commands", qos=1)
            c.subscribe("pi/device/+/command", qos=1)
            c.subscribe("pi/devices/+/status", qos=0)
            logging.info(f"MQTT control broker {MQTT_BROKER}:{MQTT_PORT}; waiting for dashboard commands")
        else:
            logging.error(f"MQTT failed: {rc}")

    def on_message(c, userdata, msg):
        try:
            payload = json.loads((msg.payload or b"{}").decode("utf-8"))
        except Exception:
            logging.warning(f"Control MQTT non-JSON message on {msg.topic}")
            return

        parts = msg.topic.split("/")
        serial = parts[2] if len(parts) >= 4 else None
        serial = str(serial or payload.get("serial") or "").strip()
        is_status_topic = msg.topic.endswith("/status")

        if is_status_topic:
            if "command" not in payload:
                return
            signature = json.dumps(payload, sort_keys=True, default=str)
            if seen_status_commands.get(serial) == signature:
                return
            seen_status_commands[serial] = signature
            logging.warning(f"[{serial}] Command payload was received on status topic; processing fallback path")

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
                logging.info(f"[{serial}] Executing remote commands from Dashboard")
                worker.execute_commands(commands_b64, payload.get("original_config"))
            return

        if command == "DEPLOY_SERVICE_CONFIG":
            if not serial:
                return

            worker = worker_map.get(serial)
            if not worker or not hasattr(worker, "_persist_remote_config"):
                logging.warning(f"[{serial}] Service config received but no active MQTT worker. Known serials: {list(worker_map.keys())}")
                return

            cmd_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
            logging.info(f"[{serial}] Persisting service config from Dashboard")
            worker._persist_remote_config({"payload": {"service_config": cmd_payload}})
            worker.publish_status()
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
        logging.error(f"MQTT error: {e}")
        return None

# ==============================================================================
# === MAIN ===
# ==============================================================================
if __name__ == "__main__":
    print("="*70)
    print(" ðŸš€ NTRIP RELAY SYSTEM - WORKING VERSION")
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
        logging.info("ðŸš€ ALL WORKERS RUNNING")
        logging.info("="*70)
        
        last_gc = time.time()
        last_sys_stat = 0
        while True:
            time.sleep(1)
            
            # Cập nhật thông số hệ thống mỗi 5s
            if time.time() - last_sys_stat > 5:
                status_mgr.update_system_status()
                last_sys_stat = time.time()

            # Tối ưu dọn rác bộ nhớ (Garbage Collector) mỗi 60s
            if time.time() - last_gc > 60:
                gc.collect()
                last_gc = time.time()
    
    except KeyboardInterrupt:
        print("\nðŸ›‘ Stopping...")
        
        for w in workers:
            w.stop_event.set()
        
        for w in workers:
            w.join(timeout=3)
        
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        
        print("âœ… Done")
