import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import time
import sys

# --- THÔNG TIN CẤU HÌNH ---
BROKER = "aitogy.asia"
PORT = 1883
USER = "mqttUser"
PASSWORD = "MqttPassword123$%^"
IMEI = "860549070087338"
UPLOAD_TOPIC = f"device/{IMEI}/upload"

# Lệnh FRESET, có thể một số thiết bị không cần \r\n, mình sẽ test cả 2
PAYLOAD_CMD_CRLF = b"FRESET\r\n"
PAYLOAD_CMD_RAW = b"FRESET"

# Mở rộng thêm các topic
TEST_TOPICS = [
    "4gdata101",
    f"device/{IMEI}/download",
    f"device/{IMEI}/cmd",
    "4gdata101/download",
    f"device/4gdata101/{IMEI}/download", # Kiểu lồng ghép ID và IMEI
    f"cmd/{IMEI}",
    IMEI # Có một số phần cứng Tàu chỉ sub đúng IMEI của nó
]

device_is_online = False

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("[+] Đã kết nối thành công tới MQTT Broker!")
        client.subscribe(UPLOAD_TOPIC, qos=1)
        print(f"[*] Đang PHỤC KÍCH chờ thiết bị gửi data lên: {UPLOAD_TOPIC}")
        print("    (Hãy cắm điện thiết bị và chờ nó nháy đèn 4G gửi data...)")
        print("-" * 60)
    else:
        print(f"[-] Lỗi kết nối, mã lỗi: {rc}")

def on_message(client, userdata, msg):
    global device_is_online
    print(f"\n[RECEIVED DATA] => Topic: {msg.topic}")
    try:
        print(f"                => Data: {msg.payload.decode('utf-8')}")
    except:
        print(f"                => Data (Raw): {msg.payload}")
    
    # Đánh dấu là thiết bị đã online
    if not device_is_online:
        device_is_online = True

def main():
    # Sử dụng API Version 2 để fix lỗi DeprecationWarning
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id="PythonTestScript")
    client.username_pw_set(USER, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"[*] Đang kết nối tới {BROKER}:{PORT}...")
    try:
        client.connect(BROKER, PORT, 60)
    except Exception as e:
        print(f"[-] Lỗi không thể kết nối: {e}")
        sys.exit(1)

    client.loop_start()
    
    # --- BƯỚC 1: CHỜ THIẾT BỊ ONLINE ---
    timeout = 300 # Chờ tối đa 5 phút
    waited = 0
    while not device_is_online and waited < timeout:
        time.sleep(1)
        waited += 1
        if waited % 10 == 0:
            print(f"    ... Vẫn đang chờ thiết bị gửi data ({waited}s) ...")
            
    if not device_is_online:
        print("\n[-] Quá thời gian 5 phút mà thiết bị chưa gửi gì lên. Hãy kiểm tra lại SIM/nguồn của thiết bị.")
        client.loop_stop()
        client.disconnect()
        return

    # --- BƯỚC 2: THIẾT BỊ ĐÃ ONLINE, BẮT ĐẦU XẢ LỆNH ---
    print("\n[!] THIẾT BỊ ĐÃ ONLINE! BẮT ĐẦU QUÁ TRÌNH BẮN LỆNH TEST...")
    # Tạm nghỉ 2s để thiết bị rảnh rang sau khi gửi data
    time.sleep(2) 
    
    for topic in TEST_TOPICS:
        print(f"[*] Đang gửi lệnh (FRESET\\r\\n) tới topic: {topic}")
        # Gửi kèm QoS=1 để Broker đảm bảo chuyển tới thiết bị (nếu nó có sub)
        client.publish(topic, PAYLOAD_CMD_CRLF, qos=1)
        time.sleep(4)
        
        print(f"[*] Đang gửi lệnh (FRESET thuần) tới topic: {topic}")
        client.publish(topic, PAYLOAD_CMD_RAW, qos=1)
        time.sleep(4)

    print("\n[*] Đã xả xong toàn bộ kịch bản. Theo dõi xem nó có reboot và nhả data lại không (15s)...")
    time.sleep(15)
    
    client.loop_stop()
    client.disconnect()
    print("[*] Kết thúc.")

if __name__ == "__main__":
    main()