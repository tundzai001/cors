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

### Xác minh cấu hình GNSS

`VERIFY_GNSS_CONFIG` hỗ trợ u-blox và Unicore/UM ở cả ESP32 và
`agent_universal.py`. UI dùng nút **Xác minh cấu hình**. Backend tạo expectation
chỉ từ `base_config` đã lưu, kiểm tra quyền EDIT_CHIP_CONFIG và capability
mới nhất của đúng thiết bị; không chuyển expectation tùy ý từ trình duyệt.

- `system_info.gnss_verification_supported`: agent đã hỗ trợ lệnh.
- `system_info.gnss_verification_uart1`: true cho ESP32 có quy tắc chuyển cổng;
  false cho Universal giữ cổng của bộ tạo lệnh trên backend.
- u-blox: payload `tmode_b64` chứa 40 byte TMODE3 mong muốn, `output_b64` chứa
  các cặp key U4/value U1. Chỉ dùng để so sánh, không phát lại thành lệnh set.
- UM: payload `coords`, `altitude_reference`, `logs`, `port`. Đọc MODE,
  BESTNAVA và danh sách UNILOGLIST có CRC để so sánh tọa độ, bản tin, chu kỳ.
- Kết quả ở `system_info.gnss_verification`: `command_id`, `state`
  (`match`/`mismatch`/`unread`), các nhóm có `checked`, `mismatched`, `unread`,
  `age_seconds` và ghi chú nếu chưa đủ dữ liệu. `success` của command chỉ
  nghĩa là đã hoàn tất đọc; UI phải dùng kết quả đối chiếu riêng.

Snapshot chỉ tồn tại trong RAM 60 giây, không ghi cấu hình. Auto Base đang
chạy/thiết bị khóa thì từ chối xác minh. Việc lưu qua mất nguồn và đầu ra
RTCM thực tế cần kiểm tra riêng trên phần cứng. Với ESP32 UM dùng cổng hiện
tại chưa biết số COM, chỉ xác nhận các phần đã đọc đủ; không suy đoán cổng
để báo khớp bản tin.

Kiểm tra không cần thiết bị: `python3 -B -m unittest discover -s geodetic/tests
-p test_configuration_verification.py` (từ thư mục gốc repo).

`gnss_verification.details` chứa dữ liệu thực tế để giao diện trình bày:

- `kind`, `fixed.actual`, `fixed.expected`: mode và tọa độ/accuracy đã giải mã.
- u-blox: `fields`, `expected`, `compare_ports`, `ports[]` gồm `name`, `state`,
  `values` (cùng thứ tự fields), `enabled`, `baud` nếu là UART. Backend gửi
  catalogue `readback` để poll cả 5 cổng; expectation chỉ áp dụng các cổng đã
  chọn, còn cổng khác được hiển thị đúng giá trị chip trả về. Không dùng dữ
  liệu expectation làm giá trị dự phòng khi timeout.
- UM: `logs_state`, `logs[]` gồm `port`, `name`, `period` (null là ONCE),
  `expected_logs`, `compare_ports`. Giữ tất cả cổng trong readback, không thay
  tên COM bằng cổng mong muốn.

Capability `gnss_verification_details_supported` cho biết agent đã hỗ trợ bảng
chi tiết. Agent cũ chỉ trả nhóm kết quả thì UI yêu cầu cập nhật, không tự tạo
bảng giá trị từ cấu hình web. Kết quả chi tiết cũng chỉ là cấu hình chip,
không phải phép đo điện trên chân I2C/UART/SPI hoặc xác nhận rover nhận RTCM.


### GNSS accuracy telemetry

`gnss_quality` is a transient readback snapshot, or `null` when unavailable.
`hacc_m` is always metres; `source` is `ubx_nav_pvt`, `nmea_gst` or
`unicore_bestnav_sigma`; `age_seconds` is its age when the status was sent.
The legacy top-level `hacc` field is consistently millimetres, or `null`.
HDOP is never an accuracy in metres. GST/BESTNAV horizontal sigma is
`sqrt(latitude_sigma² + longitude_sigma²)`, not a guaranteed error radius.

Pi and ESP32 poll u-blox NAV-PVT every 30 seconds through the existing serial
owner, with UBX checksum and fix/invalid-LLH validation. The snapshot includes
satellites, fix type and PDOP. Unicore uses the existing MODE/BESTNAVA poll and
reads the optional latitude/longitude standard deviations. No periodic message
rates, output protocol gates or persisted configuration are changed by these
queries. The connected port must accept receiver commands and permit replies;
a port restricted to RTCM cannot supply UBX readbacks without reconfiguration.

Receiver snapshots expire after 45 seconds; GST expires after 15 seconds.
The UI also adds time since server receipt and suppresses offline or expired
values. Legacy untagged hacc is hidden because old agents mixed mm, m and HDOP.
Fixed LLA hAcc does not independently validate the declared Base coordinates,
and a fixed Base does not need the rover's RTK Fixed convergence state.


`gnss_dop` is an independent NAV-DOP (01/04) readback with `source`,
`age_seconds`, dimensionless `hdop` and `pdop`. Pi and ESP32 poll it without
changing periodic output rates. It expires after 45 seconds independently of
NAV-PVT. A failed DOP query does not discard a successful PVT read. The UI
prefers valid NAV-DOP, then valid NMEA GSA/GGA or PVT PDOP; NMEA sentinel values
must not mask valid receiver readback. Missing DOP remains explicitly unknown.

The dashboard labels NAV-PVT numSV as receiver-used satellites separately
from decoded satellites. Decoded counters, sky-view/SNR badges and average
SNR use the same constellation/PRN deduplication and seven-second expiry.
`has_geometry: false` means RTCM observations have no fresh azimuth/elevation;
these satellites remain in signal bars but are not plotted at fake 0°/0°.
The position strip and map share the same selection: valid GGA, receiver Base
readback, RTCM Base coordinates, then saved Base coordinates, with source labels.
