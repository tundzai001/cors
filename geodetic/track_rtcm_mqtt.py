import os
import time
from datetime import datetime
import paho.mqtt.client as mqtt

# Cấu hình MQTT
MQTT_HOST = "aitogy.click"
MQTT_PORT = 1883
MQTT_USER = os.getenv("MQTT_USERNAME", "").strip()
MQTT_PASS = os.getenv("MQTT_PASSWORD", "").strip()
MQTT_TOPIC = "device/860549070087338/upload"

last_msg_time = None
msg_count = 0

def parse_payload(payload):
    results = []
    i = 0
    while i < len(payload):
        # Kiểm tra chuẩn NMEA (bắt đầu bằng ký tự '$' - 0x24)
        if payload[i] == 0x24:
            end = payload.find(b'\r\n', i)
            if end != -1:
                nmea_str = payload[i:end].decode('ascii', errors='ignore')
                nmea_type = nmea_str.split(',')[0]
                results.append(f"NMEA ({nmea_type})")
                i = end + 2
                continue
            else:
                nmea_str = payload[i:].decode('ascii', errors='ignore')
                nmea_type = nmea_str.split(',')[0]
                results.append(f"NMEA ({nmea_type})")
                break
        
        # Kiểm tra chuẩn RTCM3 (bắt đầu bằng byte 0xD3 - 211)
        elif payload[i] == 0xD3:
            if i + 4 < len(payload):
                # 2 bits cuối của byte 1 và 8 bits của byte 2 chứa Chiều dài payload (10 bits)
                length = ((payload[i+1] & 0x03) << 8) | payload[i+2]
                
                # Byte 3 và 4 chứa Message Type (12 bits)
                msg_type = (payload[i+3] << 4) | (payload[i+4] >> 4)
                
                results.append(f"RTCM3 (Type: {msg_type})")
                
                # Bước nhảy tới frame tiếp theo: Preamble(1) + Header(2) + Payload(length) + CRC(3) = length + 6
                packet_len = length + 6
                if i + packet_len <= len(payload):
                    i += packet_len
                    continue
                else:
                    results.append(f"RTCM3 (Type: {msg_type} - Bị phân mảnh)")
                    break
            else:
                break
        
        # Bỏ qua byte rác, tiếp tục dò byte tiếp theo
        i += 1
    
    if not results:
        return "Dữ liệu Hex không xác định (Unknown Binary)"
    return " + ".join(results)

def on_connect(client, userdata, flags, rc, *args):
    if rc == 0:
        print(f"[*] Kết nối thành công đến MQTT Broker {MQTT_HOST}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC)
        print(f"[*] Đã subscribe topic: {MQTT_TOPIC}")
        print("[*] Đang chờ và phân tích các mẫu tin nhắn (1005, 1006...)\n")
        print("-" * 60)
    else:
        print(f"[!] Kết nối thất bại, mã lỗi: {rc}")

def on_message(client, userdata, msg):
    global last_msg_time, msg_count
    
    current_time = time.time()
    current_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    msg_count += 1
    
    if last_msg_time is not None:
        time_diff = current_time - last_msg_time
    else:
        time_diff = 0.0
    last_msg_time = current_time
    
    payload_len = len(msg.payload)
    parsed_info = parse_payload(msg.payload)
    
    print(f"[{current_dt}] Tin nhắn #{msg_count}")
    print(f" - Kích thước : {payload_len} bytes")
    
    if time_diff > 0:
        print(f" - Độ trễ     : {time_diff:.3f} giây")
    else:
        print(f" - Độ trễ     : N/A (Tin nhắn đầu tiên)")
        
    print(f" - Nội dung   : {parsed_info}")
    print("-" * 60)

def main():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        client = mqtt.Client()

    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message

    print("[*] Đang khởi tạo kết nối MQTT...")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[*] Đã dừng tracking.")
        client.disconnect()
    except Exception as e:
        print(f"[!] Lỗi xảy ra: {e}")

if __name__ == "__main__":
    main()
