# geotek_aitogy_working.py
RELAY_VERSION = "V1.1.0"

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
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

ACTIVE_WORKERS = {}

if os.name == 'nt':
    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        hStdIn = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(hStdIn, ctypes.byref(mode))
        mode.value &= ~0x0040 # Táº¯t ENABLE_QUICK_EDIT_MODE
        kernel32.SetConsoleMode(hStdIn, mode)
    except Exception:
        pass


try:
    threading.stack_size(65536)
except ValueError:
    pass

# ==============================================================================
# === Cáº¤U HÃŒNH ===
# ==============================================================================
STATION_LIST = [
    {"source_mp": "YBI_XuanAI",       "lat": 21.850058284, "lon": 104.705661679, "alt": 30.8801, "target_mp": "YB.VanYen"},
    {"source_mp": "TNN_VoNhai",       "lat": 21.75413356,  "lon": 106.07747532,  "alt": 61.9418, "target_mp": "TN.VoNhai"},
    {"source_mp": "TNN_TPThaiNguyen", "lat": 21.573653528, "lon": 105.840291958, "alt": 11.1829, "target_mp": "TN.TP"},
    {"source_mp": "TNN_DaiTu",        "lat": 21.633503578, "lon": 105.637082715, "alt": 47.8438, "target_mp": "TN.DaiTu"},
    {"source_mp": "TNN_PhoYen",       "lat": 21.416232244, "lon": 105.86204898,  "alt": 6.469,   "target_mp": "TN.PhoYen"},
    {"source_mp": "BG.TP",            "lat": 21.279629,    "lon": 106.212334,   "alt": 0.0,     "target_mp": "BG.TP"},
    {"source_mp": "HN.QuocOai",       "lat": 20.926164,    "lon": 105.600298,   "alt": 0.0,     "target_mp": "HN.QuocOai"},
    {"source_mp": "BG.LucNam",        "lat": 21.311513,    "lon": 106.386418,   "alt": 0.0,     "target_mp": "BG.LucNam"},
    {"source_mp": "HB.TP",            "lat": 20.806464,    "lon": 105.358762,   "alt": 0.0,     "target_mp": "HB.TP"},
]

MONITOR_LIST = [
    {
        "mountpoint": "ATGDT", 
        "lat": 19.848627810, 
        "lon": 105.482040020, 
        "alt": 0.0,
        "rover_user": "daithanh_webcors",
        "rover_pass": "12345"
    }
]

# MQTT
MQTT_BROKER = "45.117.179.134"
MQTT_PORT = 1883
MQTT_USER = "mqttUser"
MQTT_PASS = "MqttPassword123$%^"

SOURCE_HOST = "rtkvietnam.vn"
SOURCE_PORT = 1508
SOURCE_USER = "aitogy123"
SOURCE_PASS = "aitogy123"

TARGET_HOST = "aitogy.com.vn"
TARGET_PORT = 2101
TARGET_PASS = "12345"

NTRIP_VERSION = 1  
RECONNECT_INTERVAL = 15

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

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
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['target_mp']}] {msg}")
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return
        
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
                "serverhost1": TARGET_HOST,
                "stream_on_demand": True,
                "stream_active": bool(self.desired_active),
                "active_mountpoint": self.cfg['target_mp'] if self.desired_active else None
            },
            "source_mountpoint": self.cfg['source_mp'],
            "rtk_fix_status": "FIXED",
            "detected_chip_type": "VIRTUAL_RELAY",
            "is_provisioned": True
        }
        
        try:
            self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
        except:
            pass

    def set_stream_active(self, active: bool):
        self.desired_active = bool(active)
        if not self.desired_active:
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
                # === LUÔN KẾT NỐI SOURCE VÀ LẤY DATA (Kể cả khi bị OFF trên web) ===
                self.log("INFO", f"Connecting SOURCE: {SOURCE_HOST}/{self.cfg['source_mp']}")
                source_sock = socket.create_connection((SOURCE_HOST, SOURCE_PORT), timeout=10)
                with self.socket_lock:
                    self._source_sock = source_sock
                
                auth = base64.b64encode(f"{SOURCE_USER}:{SOURCE_PASS}".encode()).decode()
                source_request = (
                    f"GET /{self.cfg['source_mp']} HTTP/1.1\r\n"
                    f"Host: {SOURCE_HOST}\r\n"
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
                        self.log("INFO", f"Connecting TARGET: {TARGET_HOST}/{self.cfg['target_mp']} (v1.0)")
                        try:
                            target_sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
                            with self.socket_lock:
                                self._target_sock = target_sock
                            
                            target_request = (
                                f"SOURCE {TARGET_PASS} /{self.cfg['target_mp']}\r\n"
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
                                if self.current_bps > 0:
                                    self.log("INFO", f"📊 [SOURCE] {self.current_bps} B/s")
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
        self.current_bps = 0
        
        self.name = f"Monitor-{self.cfg['mountpoint']}"
        self.desired_active = False
        self.wakeup_event = threading.Event()
        self.socket_lock = threading.Lock()
        self._sock = None
    
    def log(self, level, msg):
        logging.log(getattr(logging, level.upper(), logging.INFO), f"[{self.cfg['mountpoint']}] {msg}")
    
    def publish_status(self):
        if not self.mqtt_client or not self.mqtt_client.is_connected():
            return
        
        status = {
            "serial": self.serial,
            "name": self.cfg['mountpoint'],
            "status": "online" if self.is_connected else "offline",
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
                "stream_active": bool(self.desired_active),
                "active_mountpoint": self.cfg['mountpoint'] if self.desired_active else None
            },
            "source_mountpoint": self.cfg['mountpoint'],
            "rtk_fix_status": "FIXED",
            "detected_chip_type": "ublox",
            "is_provisioned": True
        }
        
        try:
            self.mqtt_client.publish(self.topic_status, json.dumps(status), qos=0, retain=True)
        except:
            pass

    def set_stream_active(self, active: bool):
        self.desired_active = bool(active)
        if not self.desired_active:
            self._close_live_socket()
        self.wakeup_event.set()
        self.publish_status()

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
            if not self.desired_active:
                self.log("INFO", "Zzz... Monitor is OFF. Sleeping...")
                self.wakeup_event.wait()
                self.wakeup_event.clear()
                continue
                
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
                
                sock.settimeout(10.0)
                while not self.stop_event.is_set() and self.desired_active:
                    try:
                        data = sock.recv(4096)
                        if not data:
                            self.log("WARNING", "Monitor EOF (Server tu choi hoac Mountpoint khong ton tai)")
                            break
                        
                        self.bytes_received += len(data)
                        now = time.time()
                        if now - self.last_stat_update >= 1.0:
                            self.current_bps = int(self.bytes_received / (now - self.last_stat_update))
                            if self.current_bps > 0:
                                self.log("INFO", f"📊 [MONITOR] {self.current_bps} B/s")
                            self.bytes_received = 0
                            self.last_stat_update = now
                            self.is_connected = True
                            self.publish_status()
                            
                    except socket.timeout:
                        self.log("WARNING", "Monitor socket timeout (no data)")
                        self.current_bps = 0
                        self.is_connected = True # Still connected, just timeout
                        self.publish_status()
                    except Exception as e:
                        self.log("WARNING", f"Data error: {e}")
                        break
                        
                if not self.desired_active:
                    self.log("INFO", "Monitor deactivated, closing connection and sleeping...")
                else:
                    self.log("INFO", f"Monitor disconnected, reconnecting in {RECONNECT_INTERVAL}s...")
                    self.wakeup_event.wait(RECONNECT_INTERVAL)
                    self.wakeup_event.clear()
            except Exception as e:
                self.log("WARNING", f"Connection error: {e}")
                if self.desired_active:
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
# === MQTT ===
# ==============================================================================
def _worker_mountpoint(worker):
    if isinstance(worker, RelayWorker):
        return str(worker.cfg.get("target_mp", "")).strip()
    return str(worker.cfg.get("mountpoint", "")).strip()

def setup_mqtt(worker_map):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"relay-{int(time.time())}")
    except:
        client = mqtt.Client(client_id=f"relay-{int(time.time())}")
    
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    
    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            logging.info("âœ“ MQTT Connected")
            c.subscribe("pi/devices/+/command", qos=1)
            c.subscribe("pi/devices/+/commands", qos=1)
            c.subscribe("pi/device/+/command", qos=1)
        else:
            logging.error(f"MQTT failed: {rc}")

    def on_message(c, userdata, msg):
        try:
            payload = json.loads((msg.payload or b"{}").decode("utf-8"))
        except Exception:
            return

        command = str(payload.get("command", "")).strip().upper()
        if command != "SET_RTCM_STREAM_ACTIVE":
            return

        serial = None
        parts = msg.topic.split("/")
        if len(parts) >= 4:
            serial = parts[2]
        serial = str(serial or payload.get("serial") or "").strip()
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

        worker.set_stream_active(active)
        logging.info(f"[{serial}] SET_RTCM_STREAM_ACTIVE active={active} mountpoint={cmd_mp or worker_mp}")
    
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
    print(f" Relays: {len(STATION_LIST)} | Monitors: {len(MONITOR_LIST)}")
    print("="*70)
    
    mqtt_client = setup_mqtt(ACTIVE_WORKERS)
    
    workers = []
    try:
        for cfg in STATION_LIST:
            worker = RelayWorker(cfg, mqtt_client)
            ACTIVE_WORKERS[worker.serial] = worker
            worker.start()
            workers.append(worker)
            logging.info(f"âœ“ Started Relay: {cfg['source_mp']} â†’ {cfg['target_mp']}")
            worker.publish_status()
            time.sleep(1)
        
        for cfg in MONITOR_LIST:
            worker = MonitorWorker(cfg, mqtt_client)
            ACTIVE_WORKERS[worker.serial] = worker
            worker.start()
            workers.append(worker)
            logging.info(f"âœ“ Started Monitor: {cfg['mountpoint']}")
            worker.publish_status()
            time.sleep(1)
            
        logging.info("="*70)
        logging.info("ðŸš€ ALL WORKERS RUNNING")
        logging.info("="*70)
        
        last_gc = time.time()
        while True:
            time.sleep(1)
            # Tá»‘i Æ°u dá»n rÃ¡c bá»™ nhá»› (Garbage Collector) má»—i 60s
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

