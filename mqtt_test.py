import paho.mqtt.client as mqtt
import time
import random
import json
import ssl
from datetime import datetime

# Cấu hình MQTT Broker
BROKER = "mqtt.watertech.vn"
PORT = 8883
USERNAME = "watertech"
PASSWORD = "watertech@2026"
TOPIC = "test/sensor/rain"  # Bạn có thể đổi topic theo chuẩn hệ thống

# Các hàm callback để kiểm tra trạng thái kết nối
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("✅ Đã kết nối thành công tới Broker!")
    else:
        print(f"❌ Kết nối thất bại, mã lỗi: {rc}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    print(f"   -> Broker đã nhận tin nhắn (mid={mid})")

# Khởi tạo client (Hỗ trợ paho-mqtt v2.x)
try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
except AttributeError:
    # Fallback cho paho-mqtt v1.x
    client = mqtt.Client()

client.on_connect = on_connect
client.on_publish = on_publish

# Cấu hình xác thực và TLS
client.username_pw_set(USERNAME, PASSWORD)

# Khởi tạo TLS tiêu chuẩn
client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)

# Chú ý: Nếu broker của bạn sử dụng chứng chỉ tự ký (self-signed certificate) 
# và bị lỗi SSL Certificate Verify Failed, hãy bỏ comment dòng dưới đây:
# client.tls_insecure_set(True)

try:
    print(f"Đang kết nối tới tls://{BROKER}:{PORT}...")
    client.connect(BROKER, PORT, 60)
    
    # Chạy vòng lặp xử lý ngầm
    client.loop_start()
    
    # Chờ 2 giây để đảm bảo callback on_connect được gọi
    time.sleep(2)

    while True:
        # Tạo dữ liệu đo mưa giả lập (ví dụ từ 0.0 đến 45.5 mm)
        rain_value = round(random.uniform(0.0, 45.5), 1)
        
        payload = {
            "station_id": "ST-TEST-01",
            "rainfall_mm": rain_value,
            "timestamp": datetime.now().isoformat()
        }
        
        msg = json.dumps(payload)
        print(f"\nĐang publish: {msg} vào topic '{TOPIC}'")
        
        # Publish dữ liệu
        client.publish(TOPIC, msg, qos=1)
        
        time.sleep(5)

except KeyboardInterrupt:
    print("\n⚠️ Đã dừng gửi dữ liệu test.")
except Exception as e:
    print(f"\n❌ Đã xảy ra lỗi: {e}")
finally:
    client.loop_stop()
    client.disconnect()
    print("Đã ngắt kết nối MQTT.")