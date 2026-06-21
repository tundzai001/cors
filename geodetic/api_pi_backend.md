# Tài liệu Giao tiếp giữa Raspberry Pi (Agent) và Backend (Nâng cao)

Agent chạy trên Raspberry Pi giao tiếp với Backend thông qua **2 giao thức song song**: **MQTT** và **WebSocket**. 
> [!IMPORTANT]
> **MQTT là KÊNH ƯU TIÊN SỐ 1 (PRIMARY) ĐỂ ĐIỀU KHIỂN.** Backend luôn cố gắng gửi các lệnh điều khiển qua MQTT trước tiên. Chỉ khi MQTT bị sập hoặc rớt mạng, Backend mới chuyển qua sử dụng WebSocket làm kênh dự phòng (Fallback).

---

## 1. Cơ chế Xác thực & Tối ưu hiệu năng (Hot-path)

Hệ thống CORS xử lý hàng ngàn gói tin mỗi giây, do đó cơ chế xác thực được chia làm 2 luồng rõ rệt:

### 1.1. Luồng WebSocket & Trạng thái (Bảo mật cao)
- **WebSocket:** Bắt buộc phải có `token` chuẩn trong URL (`?token=...`). Nếu sai, Backend sẽ ngắt kết nối ngay lập tức (Mã lỗi 1008).
- **MQTT Status (`/status`):** Gói tin JSON mang theo trường `agent_token_fingerprint`. Backend sẽ đối chiếu với Database, nếu sai, trạm sẽ bị đổi sang trạng thái `awaiting_license` (Offline) và báo lỗi đỏ trên Admin.

### 1.2. Luồng Hot-path (`raw_data` & `nmea`)
- **Tối ưu tốc độ:** Để tránh nghẽn cổ chai khi check Database hàng ngàn lần/giây, Backend **KHÔNG check token** đối với 2 topic `raw_data` và `nmea` qua MQTT. 
- **Tin tưởng Broker:** Backend giao phó hoàn toàn việc bảo mật cho tài khoản MQTT (`mqttUser`/`MqttPassword123$%^`). Bất kỳ ai có tài khoản này đều có thể đẩy dữ liệu thẳng lên UI. Thực tế production cần dùng ACL (Access Control List) trên MQTT Broker để siết quyền từng Client.

---

## 2. Kết nối MQTT (Kênh Chính - Primary)

MQTT đóng vai trò xương sống cho việc trao đổi cấu hình, gửi lệnh điều khiển (Wake/Sleep), và đẩy dữ liệu RTCM.

- **Broker**: `aitogy.click` 
- **Port**: `1883`
- **Xác thực**:  `mqttUser` / `MqttPassword123$%^`

### 2.1. Các Topic Pi Publish (Gửi đi từ Pi)
- `/cors/devices/{serial}/status`: Gửi Payload telemetry (Thông số phần cứng CPU/RAM, Trạng thái Fix, Số lượng vệ tinh). Cấu hình `QoS=1`, `Retain=True`.
- `/cors/devices/{serial}/nmea`: Gửi dữ liệu NMEA thô (Raw text) lên Backend (thường 1Hz).

### 2.2. Các Topic Pi Subscribe (Lắng nghe từ Backend)
- `pi/devices/{serial}/command`: Lắng nghe lệnh điều khiển từ Backend. Tất cả các lệnh như Cập nhật Config, Bật/Tắt Sleep, Khởi động lại đều gửi qua đây.

---

## 3. Kết nối WebSocket (Kênh Dự phòng - Fallback)

Được thiết lập đồng thời với MQTT để đảm bảo không rớt lệnh.
- **Endpoint**: `ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}?token={auth_token}`
- Chỉ khi Backend gọi hàm check MQTT bị fail, nó mới đẩy lệnh điều khiển qua kênh WebSocket này. Pi sẽ phản hồi y hệt như nhận từ MQTT.

---

## 4. Lệnh Bật / Tắt Ngủ (Wake / Sleep Mode)

Tính năng **Stream On Demand** giúp trạm tự tắt truyền dữ liệu RTCM để tiết kiệm băng thông khi không có Rover nào kết nối.

### 4.1. Lệnh Đánh thức (Wake Command)
Khi một Rover kết nối vào Backend, Backend sẽ gửi lệnh qua MQTT (hoặc WebSocket) xuống Pi để "Bật" luồng RTCM:

**Topic:** `pi/devices/{serial}/command`
**Payload (JSON):**
```json
{
  "command": "SET_RTCM_STREAM_ACTIVE",
  "payload": {
    "active": true,
    "mountpoint": "HN01",
    "address": "14.238.1.125",
    "server_id": 1
  }
}
```
*Pi nhận lệnh sẽ lập tức khởi động tiến trình NTRIP Server bên trong nó và bơm RTCM lên Caster (Server ID 1 hoặc 2).*

### 4.2. Lệnh Cho Ngủ (Sleep Command)
Khi Rover ngắt kết nối quá 90s, Backend gửi lệnh tắt luồng:
```json
{
  "command": "SET_RTCM_STREAM_ACTIVE",
  "payload": {
    "active": false,
    "server_id": 1
  }
}
```
*Pi sẽ lập tức ngắt kết nối TCP truyền RTCM lên Caster, chuyển sang trạng thái chờ (Sleep).*

---

## 5. Setup Config Trạm Đầy Đủ (Dành cho bên thứ 3 test)

Để giả lập một trạm CORS hoặc build 1 app test, bên thứ 3 cần lắng nghe lệnh `DEPLOY_SERVICE_CONFIG`. Đây là bộ config đầy đủ mà Backend sẽ gửi xuống để thiết lập thông số truyền RTCM:

**Topic:** `pi/devices/{serial}/command`
**Payload (JSON):**
```json
{
  "command": "DEPLOY_SERVICE_CONFIG",
  "payload": {
    "stream_on_demand": true,
    "stream_active": false,
    
    "server1_enabled": true,
    "ip": "14.238.1.125",
    "port": 2101,
    "user": "caster_admin",
    "password": "caster_password_123",
    "mountpoint": "TEST_STATION_01",
    "server1_stream_on_demand": true,
    
    "server2_enabled": false,
    "ip2": "1.1.1.1",
    "port2": 2101,
    "user2": "backup_user",
    "password2": "backup_pass",
    "mountpoint2": "TEST_STATION_01_BK",
    "server2_stream_on_demand": true,
    
    "mqtt_enabled": true,
    "mqtt_topic": "pi/devices/{serial}/raw_data"
  }
}
```

> [!TIP]
> **Hướng dẫn Test cho bên thứ 3:** 
> 1. Xin Admin bật cờ `auth_license_bypass = true` cho thiết bị.
> 2. Đăng nhập phần mềm MQTT Explorer với Username/Pass.
> 3. Subscribe vào topic `pi/devices/{serial}/command` để hứng lệnh Bật/Tắt ngủ.
> 4. Publish NMEA thô vào topic `pi/devices/{serial}/nmea` và dữ liệu RTCM vào `pi/devices/{serial}/raw_data` để thấy giao diện nhảy vệ tinh!
