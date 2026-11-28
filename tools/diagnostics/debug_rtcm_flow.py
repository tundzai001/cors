import asyncio
import time
import base64
import logging

FIRST_SAMPLE_LABEL = "lần đầu"
import sys
import os
import paho.mqtt.client as mqtt

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# --- Cấu hình MQTT (Nguồn RTCM) ---
MQTT_HOST = "aitogy.click"
MQTT_PORT = 1883
MQTT_USER = "mqttUser"
MQTT_PASS = "MqttPassword123$%^"
MQTT_TOPIC = "device/860549070087338/upload"

# --- Cấu hình NTRIP (Nơi nhận) ---
NTRIP_HOST = "aitogy.com.vn"
NTRIP_PORT = 2101
NTRIP_USER = "aitogy"
NTRIP_PASS = "aitogy"
NTRIP_MP = "HT.VuQuang"

# --- Thiết lập Logging ---
log_file = "rtcm_debug.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# Thử import pyrtcm nếu có để phân tích gói tin
try:
    from pyrtcm import RTCMReader
    from io import BytesIO
    HAS_PYRTCM = True
    logging.info("[+] Tìm thấy thư viện pyrtcm, sẽ phân tích gói tin RTCM.")
except ImportError:
    HAS_PYRTCM = False
    logging.info("[-] Không tìm thấy thư viện pyrtcm, chỉ đo kích thước bytes.")

# --- THUẬT TOÁN ĐỌC RTCM3 & TÍNH CRC ---
def crc24q(data):
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF

def _next_rtcm3_frame(buffer):
    start = buffer.find(b"\xd3")
    if start < 0:
        buffer.clear()
        return None, True
    if start > 0:
        del buffer[:start]
    if len(buffer) < 6:
        return None, True
    if buffer[1] & 0xFC:
        del buffer[0]
        return None, False

    payload_len = ((buffer[1] & 0x03) << 8) | buffer[2]
    total_len = 3 + payload_len + 3
    if payload_len > 1023:
        del buffer[0]
        return None, False
    if len(buffer) < total_len:
        # Keep enough bytes to complete this frame, while bounding malformed input.
        if len(buffer) > 8192:
            del buffer[:-total_len]
        return None, True

    frame = bytes(buffer[:total_len])
    expected_crc = int.from_bytes(frame[-3:], "big")
    if crc24q(frame[:-3]) == expected_crc:
        del buffer[:total_len]
        return frame, False
    del buffer[0]
    return None, False


def extract_rtcm3_frames(buffer):
    frames = []
    while True:
        frame, finished = _next_rtcm3_frame(buffer)
        if finished:
            return frames
        if frame is not None:
            frames.append(frame)

# Biến toàn cục theo dõi dữ liệu
stats = {
    "mqtt": {
        "connected": False,
        "total_bytes": 0,       # Tổng bytes thô nhận được từ MQTT (bao gồm NMEA)
        "rtcm_bytes": 0,        # Tổng bytes RTCM hợp lệ (đã lọc & khớp CRC)
        "last_sec_bytes": 0.0,  # B/s thô
        "last_sec_rtcm": 0.0,   # B/s RTCM hợp lệ
        "history": [],          # (timestamp, raw_bytes)
        "rtcm_history": [],     # (timestamp, rtcm_bytes)
        "last_recv_time": 0.0,
    },
    "ntrip": {
        "connected": False,
        "total_bytes": 0,       # Tổng bytes thô nhận được từ NTRIP Client
        "rtcm_bytes": 0,        # Tổng bytes RTCM thô hợp lệ từ Caster
        "last_sec_bytes": 0.0,  # B/s thô
        "last_sec_rtcm": 0.0,   # B/s RTCM hợp lệ
        "history": [],          # (timestamp, raw_bytes)
        "rtcm_history": [],     # (timestamp, rtcm_bytes)
        "last_recv_time": 0.0,
        "reconnect_count": 0,
    }
}

# Buffers phân tách khung bản tin RTCM
mqtt_buffer = bytearray()
ntrip_buffer = bytearray()

# Theo dõi thời gian nhận lần cuối của từng loại bản tin
last_seen = {
    "mqtt": {},
    "ntrip": {}
}

# --- KHỞI TẠO MQTT CLIENT ---
def on_connect(client, userdata, flags, rc, *args, **kwargs):
    if rc == 0:
        logging.info("[MQTT] Kết nối thành công tới broker.")
        stats["mqtt"]["connected"] = True
        client.subscribe(MQTT_TOPIC)
        logging.info(f"[MQTT] Đã subscribe topic: {MQTT_TOPIC}")
    else:
        logging.error(f"[MQTT] Kết nối thất bại với mã lỗi: {rc}")
        stats["mqtt"]["connected"] = False

def on_disconnect(client, userdata, *args, **kwargs):
    rc = args[0] if len(args) == 1 else (args[1] if len(args) >= 2 else None)
    rc_str = f" (rc={rc})" if rc is not None else ""
    logging.warning(f"[MQTT] Mất kết nối khỏi broker{rc_str}. Đang tự động thử lại...")
    stats["mqtt"]["connected"] = False

def _log_mqtt_nmea(payload, now):
    if not payload.startswith(b'$'):
        return
    try:
        nmea_str = payload.decode('utf-8', errors='ignore').strip()
        timestamp = time.strftime('%H:%M:%S', time.localtime(now)) + f".{int((now % 1) * 1000):03d}"
        previous = last_seen["mqtt"].get("NMEA", 0)
        delta = f"{now - previous:.1f}s" if previous > 0 else FIRST_SAMPLE_LABEL
        last_seen["mqtt"]["NMEA"] = now
        logging.info(f"[{timestamp}] [MQTT NMEA] (cách {delta}) | {nmea_str[:80]}")
    except Exception:
        pass


def _log_mqtt_rtcm(frames, now, rtcm_len):
    if not HAS_PYRTCM:
        return
    try:
        identifiers = []
        for frame in frames:
            reader = RTCMReader(BytesIO(frame), quitonerror=False)
            for _, parsed_data in reader:
                if parsed_data:
                    message_type = str(parsed_data.identity)
                    previous = last_seen["mqtt"].get(message_type, 0)
                    delta = f"{now - previous:.1f}s" if previous > 0 else FIRST_SAMPLE_LABEL
                    last_seen["mqtt"][message_type] = now
                    identifiers.append(f"{message_type} (cách {delta})")
        if identifiers:
            timestamp = time.strftime('%H:%M:%S', time.localtime(now)) + f".{int((now % 1) * 1000):03d}"
            logging.info(f"[{timestamp}] [MQTT RTCM] Nhận: {', '.join(identifiers)} | Tổng: {rtcm_len} bytes")
    except Exception:
        pass


def on_message(client, userdata, msg, *args, **kwargs):
    global mqtt_buffer
    now = time.time()
    payload = msg.payload or b""
    payload_len = len(payload)
    
    stats["mqtt"]["total_bytes"] += payload_len
    stats["mqtt"]["last_recv_time"] = now
    stats["mqtt"]["history"].append((now, payload_len))

    _log_mqtt_nmea(payload, now)

    # Đưa vào buffer để trích xuất RTCM3
    mqtt_buffer.extend(payload)
    frames = extract_rtcm3_frames(mqtt_buffer)
    if frames:
        rtcm_len = sum(len(f) for f in frames)
        stats["mqtt"]["rtcm_bytes"] += rtcm_len
        stats["mqtt"]["rtcm_history"].append((now, rtcm_len))
        
        _log_mqtt_rtcm(frames, now, rtcm_len)

# --- NTRIP CLIENT MONITOR ---
async def _read_ntrip_response_header(reader):
    header_buffer = bytearray()
    while len(header_buffer) < 2048:
        chunk = await reader.read(256)
        if not chunk:
            break
        header_buffer.extend(chunk)

        for separator in [b'\r\n\r\n', b'\n\n']:
            idx = header_buffer.find(separator)
            if idx != -1:
                return header_buffer[:idx], header_buffer[idx + len(separator):]

        if b'200' in header_buffer or b'ICY' in header_buffer:
            idx_d3 = header_buffer.find(0xD3)
            if idx_d3 != -1 and idx_d3 > 10:
                return header_buffer[:idx_d3], header_buffer[idx_d3:]
    return None, None


def _log_ntrip_rtcm_frames(frames, now, rtcm_len):
    if not HAS_PYRTCM:
        return
    try:
        idents = []
        for frame in frames:
            stream = BytesIO(frame)
            reader = RTCMReader(stream, quitonerror=False)
            for _, parsed_data in reader:
                if parsed_data:
                    msg_type = str(parsed_data.identity)
                    prev = last_seen["ntrip"].get(msg_type, 0)
                    dt_str = f"{now - prev:.1f}s" if prev > 0 else FIRST_SAMPLE_LABEL
                    last_seen["ntrip"][msg_type] = now
                    idents.append(f"{msg_type} (cách {dt_str})")
        if idents:
            t_str = time.strftime('%H:%M:%S', time.localtime(now)) + f".{int((now % 1) * 1000):03d}"
            logging.info(f"[{t_str}] [NTRIP RTCM] Caster gửi: {', '.join(idents)} | Tổng: {rtcm_len} bytes")
    except Exception:
        pass


def _handle_ntrip_data(data, now):
    data_len = len(data)
    stats["ntrip"]["total_bytes"] += data_len
    stats["ntrip"]["last_recv_time"] = now
    stats["ntrip"]["history"].append((now, data_len))

    ntrip_buffer.extend(data)
    frames = extract_rtcm3_frames(ntrip_buffer)
    if not frames:
        return
    rtcm_len = sum(len(frame) for frame in frames)
    stats["ntrip"]["rtcm_bytes"] += rtcm_len
    stats["ntrip"]["rtcm_history"].append((now, rtcm_len))
    _log_ntrip_rtcm_frames(frames, now, rtcm_len)


async def _receive_ntrip_stream(reader, binary_start):
    if binary_start:
        _handle_ntrip_data(binary_start, time.time())

    while True:
        data = await reader.read(4096)
        if not data:
            logging.warning("[NTRIP] Kết nối bị ngắt từ caster (EOF).")
            return
        _handle_ntrip_data(data, time.time())


async def _run_ntrip_session(reader, writer, auth_b64):
    request_headers = (
        f"GET /{NTRIP_MP} HTTP/1.0\r\n"
        f"User-Agent: NTRIP Client/1.0\r\n"
        f"Authorization: Basic {auth_b64}\r\n\r\n"
    )
    writer.write(request_headers.encode('utf-8'))
    await writer.drain()

    header_bytes, binary_start = await _read_ntrip_response_header(reader)
    if header_bytes is None:
        logging.error("[NTRIP] Không nhận được phản hồi hợp lệ từ Caster.")
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(5)
        return False

    response_str = header_bytes.decode('utf-8', errors='ignore').strip()
    logging.info(f"[NTRIP] Caster phản hồi headers:\n{response_str}")
    if "200" not in response_str and "ICY" not in response_str:
        logging.error("[NTRIP] Kết nối bị từ chối hoặc mountpoint không khả dụng.")
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(5)
        return False

    logging.info(f"[NTRIP] Kết nối thành công! Đang nhận stream từ '{NTRIP_MP}'...")
    stats["ntrip"]["connected"] = True
    stats["ntrip"]["last_recv_time"] = time.time()
    await _receive_ntrip_stream(reader, binary_start)
    return True


async def start_ntrip_monitor():
    auth_str = f"{NTRIP_USER}:{NTRIP_PASS}"
    auth_b64 = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')

    while True:
        logging.info(f"[NTRIP] Đang kết nối tới NTRIP Caster {NTRIP_HOST}:{NTRIP_PORT}...")
        stats["ntrip"]["connected"] = False

        try:
            reader, writer = await asyncio.open_connection(NTRIP_HOST, NTRIP_PORT)
            accepted = await _run_ntrip_session(reader, writer, auth_b64)
            if not accepted:
                continue
        except Exception as e:
            logging.exception(f"[NTRIP] Lỗi đường truyền: {e}")

        stats["ntrip"]["connected"] = False
        stats["ntrip"]["reconnect_count"] += 1
        logging.warning("[NTRIP] Kết nối lại sau 5 giây...")
        await asyncio.sleep(5)

# --- IN THỐNG KÊ CHI TIẾT ---
async def print_statistics():
    start_time = time.time()
    logging.info("=== Bắt đầu đo lường luồng RTCM (MQTT vs NTRIP) ===")
    
    while True:
        await asyncio.sleep(5)
        now = time.time()
        
        for k in ["mqtt", "ntrip"]:
            # Dọn dẹp lịch sử > 60 giây
            stats[k]["history"] = [x for x in stats[k]["history"] if now - x[0] <= 60]
            stats[k]["rtcm_history"] = [x for x in stats[k]["rtcm_history"] if now - x[0] <= 60]
            
            # Tính trung bình 10 giây gần nhất
            recent_raw = sum(x[1] for x in stats[k]["history"] if now - x[0] <= 10)
            recent_rtcm = sum(x[1] for x in stats[k]["rtcm_history"] if now - x[0] <= 10)
            
            stats[k]["last_sec_bytes"] = recent_raw / 10.0
            stats[k]["last_sec_rtcm"] = recent_rtcm / 10.0
            
        uptime = now - start_time
        
        # MQTT Stats
        m_status = "ONLINE" if stats["mqtt"]["connected"] else "OFFLINE"
        m_raw = stats["mqtt"]["last_sec_bytes"]
        m_rtcm = stats["mqtt"]["last_sec_rtcm"]
        m_total_raw = stats["mqtt"]["total_bytes"]
        m_total_rtcm = stats["mqtt"]["rtcm_bytes"]
        _ = now - stats["mqtt"]["last_recv_time"] if stats["mqtt"]["last_recv_time"] > 0 else uptime
        
        # NTRIP Stats
        n_status = "ONLINE" if stats["ntrip"]["connected"] else "OFFLINE"
        n_raw = stats["ntrip"]["last_sec_bytes"]
        n_rtcm = stats["ntrip"]["last_sec_rtcm"]
        n_total_raw = stats["ntrip"]["total_bytes"]
        n_total_rtcm = stats["ntrip"]["rtcm_bytes"]
        _ = stats["ntrip"]["reconnect_count"]
        _ = now - stats["ntrip"]["last_recv_time"] if stats["ntrip"]["last_recv_time"] > 0 else uptime

        logging.info("------------------------------------------------------------------------------------------------")
        logging.info(
            f"Uptime: {int(uptime)}s | "
            f"MQTT: {m_status} [Thô: {m_raw:.1f} B/s, RTCM: {m_rtcm:.1f} B/s] (Tổng thô: {m_total_raw} B, RTCM: {m_total_rtcm} B) | "
            f"NTRIP: {n_status} [Thô: {n_raw:.1f} B/s, RTCM: {n_rtcm:.1f} B/s] (Tổng thô: {n_total_raw} B, RTCM: {n_total_rtcm} B)"
        )
        
        # Phân tích nguyên nhân lệch băng thông
        if m_raw > 20 and m_rtcm < 5:
            logging.warning(
                f"[!] CẢNH BÁO THIẾT BỊ: MQTT nhận tổng cộng {m_raw:.1f} B/s nhưng chỉ có {m_rtcm:.1f} B/s là RTCM hợp lệ.\n"
                f"    -> Khả năng cao thiết bị đang gửi rác, gửi lệnh cấu hình lỗi hoặc chỉ gửi NMEA thô chứ không phát RTCM."
            )
        elif m_rtcm > 30 and n_rtcm < (m_rtcm * 0.5):
            logging.critical(
                f"[!] CẢNH BÁO TRUNG GIAN: Thiết bị phát RTCM chuẩn lên MQTT ({m_rtcm:.1f} B/s) "
                f"nhưng NTRIP nhận được quá ít ({n_rtcm:.1f} B/s).\n"
                f"    -> File trung gian 'geotek_daithanh_aitogy.py' đang nuốt/lọc sai hoặc kết nối từ nó lên Caster bị nghẽn/đứt quãng!"
            )

async def main():
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        client = mqtt.Client(CallbackAPIVersion.VERSION2)
    except ImportError:
        client = mqtt.Client()
        
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        logging.critical(f"Không thể kết nối đến MQTT Broker: {e}")
        return
        
    ntrip_task = asyncio.create_task(start_ntrip_monitor())
    stats_task = asyncio.create_task(print_statistics())
    
    try:
        await asyncio.gather(ntrip_task, stats_task)
    except KeyboardInterrupt:
        logging.info("[!] Đang dừng chương trình...")
    finally:
        client.loop_stop()
        client.disconnect()
        logging.info("[+] Hoàn tất dọn dẹp kết nối.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
