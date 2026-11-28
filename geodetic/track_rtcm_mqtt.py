import time
from datetime import datetime
import paho.mqtt.client as mqtt

# Cấu hình MQTT
MQTT_HOST = "aitogy.click"
MQTT_PORT = 1883
MQTT_USER = "mqttUser"
MQTT_PASS = "MqttPassword123$%^"
MQTT_TOPIC = "device/860549070087338/upload"

last_msg_time = None
msg_count = 0

def _parse_nmea_sentence(payload, start):
    end = payload.find(b'\r\n', start)
    sentence_end = end if end != -1 else len(payload)
    sentence = payload[start:sentence_end].decode('ascii', errors='ignore')
    return f"NMEA ({sentence.split(',')[0]})", end + 2 if end != -1 else len(payload), end != -1


def _parse_rtcm_message(payload, start):
    if start + 4 >= len(payload):
        return None, start, False
    length = ((payload[start + 1] & 0x03) << 8) | payload[start + 2]
    msg_type = (payload[start + 3] << 4) | (payload[start + 4] >> 4)
    packet_len = length + 6
    result = f"RTCM3 (Type: {msg_type})"
    if start + packet_len <= len(payload):
        return [result], start + packet_len, True
    return [result, f"RTCM3 (Type: {msg_type} - Bị phân mảnh)"], len(payload), False


def parse_payload(payload):
    results = []
    i = 0
    while i < len(payload):
        if payload[i] == 0x24:
            result, i, continue_parsing = _parse_nmea_sentence(payload, i)
            results.append(result)
            if continue_parsing:
                continue
            break
        if payload[i] == 0xD3:
            messages, i, continue_parsing = _parse_rtcm_message(payload, i)
            if messages is None:
                break
            results.extend(messages)
            if continue_parsing:
                continue
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
