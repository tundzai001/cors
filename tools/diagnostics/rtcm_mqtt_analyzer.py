import os
import paho.mqtt.client as mqtt
from pyrtcm import RTCMReader
from io import BytesIO

# --- Cấu hình MQTT ---
MQTT_HOST = "aitogy.asia"
MQTT_PORT = 1883  # Port mặc định (sửa lại nếu server của bạn dùng port khác, vd: 8883 cho TLS)
MQTT_USER = os.getenv("MQTT_USERNAME", "").strip()
MQTT_PASS = os.getenv("MQTT_PASSWORD", "").strip()
MQTT_TOPIC = "device/860549070374009/upload"

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("[+] Kết nối MQTT thành công!")
        client.subscribe(MQTT_TOPIC)
        print(f"[+] Đã theo dõi (subscribe) topic: {MQTT_TOPIC}")
    else:
        print(f"[-] Kết nối thất bại, mã lỗi: {rc}")

buffer = bytearray()

def on_message(client, userdata, msg):
    global buffer
    # Thêm dữ liệu mới nhận được vào buffer
    buffer.extend(msg.payload)
    
    # RTCM3 frame có ít nhất 6 bytes (3 byte header + tối thiểu 0 byte payload + 3 byte CRC)
    while len(buffer) >= 6:
        # Tìm byte bắt đầu của bản tin RTCM3 (Preamble = 0xD3)
        idx = buffer.find(0xD3)
        if idx == -1:
            # Nếu không thấy 0xD3, xóa toàn bộ buffer vì toàn là rác
            buffer.clear()
            break
            
        if idx > 0:
            # Cắt bỏ các byte rác nằm trước 0xD3
            buffer = buffer[idx:]
            continue
            
        # Lúc này buffer[0] đang là 0xD3
        if len(buffer) < 3:
            break # Chờ thêm dữ liệu để đọc được chiều dài (length)
            
        # Tính toán chiều dài payload của bản tin RTCM (10 bit)
        # Byte 1 chứa 6 bit rỗng và 2 bit cao của length. Byte 2 chứa 8 bit thấp.
        payload_length = ((buffer[1] & 0x03) << 8) | buffer[2]
        frame_len = payload_length + 6 # Tổng chiều dài = 3 (header) + payload + 3 (CRC)
        
        if len(buffer) < frame_len:
            break # Chưa nhận đủ bytes cho toàn bộ bản tin này, chờ MQTT gửi thêm
            
        # Trích xuất toàn bộ frame
        frame_bytes = buffer[:frame_len]
        
        parsed_successfully = False
        try:
            # Đưa frame hoàn chỉnh vào RTCMReader để parse
            stream = BytesIO(frame_bytes)
            # quitonerror=True để nếu sai CRC nó sẽ throw error thay vì cố đọc tiếp
            rtr = RTCMReader(stream, quitonerror=True)
            for raw_data, parsed_data in rtr:
                if parsed_data:
                    print(f"[+] Phát hiện bản tin RTCM Type: {parsed_data.identity:<4} (ghép nối thành công, kích thước {frame_len} bytes)")
                    parsed_successfully = True
        except Exception:
            pass # Lỗi parse (có thể do 0xD3 giả mạo, hoặc sai CRC)
            
        if parsed_successfully:
            # Xóa frame hợp lệ khỏi buffer
            buffer = buffer[frame_len:]
        else:
            # Nếu parse lỗi, dịch đi 1 byte để tiếp tục tìm 0xD3 thực sự
            buffer = buffer[1:]

def main():
    # Khởi tạo client MQTT (Sử dụng protocol v5 hoặc v3.1.1 tuỳ broker, ở đây dùng mặc định)
    # Nếu dùng paho-mqtt bản mới (v2.0+), cần truyền CallbackAPIVersion.VERSION2
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        client = mqtt.Client(CallbackAPIVersion.VERSION2)
    except ImportError:
        # Fallback cho paho-mqtt v1.x
        client = mqtt.Client()

    # Thiết lập tài khoản / mật khẩu
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    
    # Gán các hàm callback
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        print(f"Đang kết nối tới MQTT Broker {MQTT_HOST}:{MQTT_PORT}...")
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        
        # Bắt đầu vòng lặp lắng nghe vô tận
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[!] Đã ngắt kết nối bởi người dùng.")
        client.disconnect()
    except Exception as e:
        print(f"[-] Lỗi kết nối: {e}")

if __name__ == "__main__":
    main()