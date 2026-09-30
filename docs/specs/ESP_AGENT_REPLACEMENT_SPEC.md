# Yêu cầu triển khai Agent mới thay thế `geodetic/agent_universal.py`

**Đối tượng thực hiện:** đội ESP/agent và đội backend phối hợp  
**Mục tiêu:** tạo một agent chạy ổn định trên Windows và Linux, tương thích với CORS backend hiện tại.  
**Phạm vi:** agent host-side giao tiếp với GNSS receiver/ESP qua serial, không phải firmware ESP32.  
**Nguyên tắc:** giữ nguyên contract đang được dashboard/backend sử dụng; chỉ bỏ RTCM semantic decoder tại agent.

## 1. Tóm tắt yêu cầu

Agent mới phải thay thế được `geodetic/agent_universal.py` mà không cần sửa lại luồng dashboard:

```text
GNSS/ESP serial
    ├─ NMEA  ──> MQTT raw_data (primary) ──> backend parser/UI
    │          └> WebSocket nmea_update (fallback)
    ├─ RTCM  ──> NTRIP caster server 1/2
    │          └> raw telemetry tùy cấu hình; agent KHÔNG decode message type
    └─ UBX/bytes ─> health/forwarding tối thiểu, không làm mất dữ liệu

Dashboard REST ─> backend ─> MQTT command (primary)
                           └> WebSocket command (fallback)

Agent ── status MQTT retained + status WebSocket
Backend ── control_plane_ack về cả hai kênh
```

Agent phải chạy được ở các chế độ:

- chưa có license/token;
- đã provision nhưng chưa cấu hình GNSS;
- online/streaming;
- sleep/on-demand;
- locked;
- đang cấu hình, auto-base, reference-check hoặc reboot.

## 2. Phần được bỏ và phần vẫn bắt buộc

### Không làm trong agent mới

- Không dùng `RTCMSignalDecoder`.
- Không decode các message RTCM để suy ra vệ tinh, SNR, skyview hoặc loại message.
- Không tự tạo `rtcm_skyview_calculated` hoặc `rtcm_signal_update` từ RTCM nếu không có decoder.
- Không để việc decode làm chậm hoặc chặn đường truyền RTCM tới NTRIP caster.

### Vẫn được phép và bắt buộc

- Tách frame RTCM tối thiểu để không trộn dữ liệu giữa các lần đọc serial.
- Có thể kiểm tra preamble/length/CRC để loại frame hỏng; đây là framing/validation, không phải semantic decoding.
- Parse tối thiểu NMEA checksum/type để forward GGA/GSA/GSV/GST và tạo health status.
- Có thể đọc các trường UBX tối thiểu cho health nếu cần, nhưng không triển khai RTCM decoder.
- Forward nguyên bytes RTCM tới NTRIP server và raw telemetry khi được bật.

Nếu backend cần skyview từ RTCM thì backend phải nhận raw RTCM và tự decode; agent không chịu trách nhiệm cho việc đó.

## 3. Địa chỉ và kênh kết nối

Tất cả địa chỉ phải cấu hình được, không hard-code trong source. Các giá trị dưới đây là giá trị tương thích với repo hiện tại.

| Thành phần | Giá trị hiện tại | Yêu cầu agent |
|---|---|---|
| Dashboard/public API | `https://cors.aitogy.com` | Chỉ dùng cho người dùng/dashboard; agent không đăng nhập bằng JWT dashboard |
| Backend host hiện tại | `aitogy.click` | Dùng làm host mặc định nếu không có config |
| WebSocket hiện tại | `ws://aitogy.click:8000/ws/pi/{serial}?token={license_key}` | Hỗ trợ `wss://` và URL tùy cấu hình cho production |
| MQTT broker hiện tại | `45.117.179.134:1883` | Host/port/username/password/TLS phải lấy từ config hoặc secret của deployment |
| NTRIP caster server 1/2 | Không cố định | Lấy từ `service_config.serverhost1/serverhost2`, port và credential tương ứng |

Lưu ý: `cors_dashboard/backend/.env.example` dùng `MQTT_HOST=127.0.0.1` vì đó là cấu hình broker nhìn từ backend. Agent ở máy khác không được tự suy ra `127.0.0.1`; phải nhận địa chỉ broker external từ deployment.

Các credential MQTT legacy đang tồn tại trong agent cũ chỉ để tương thích môi trường cũ. Agent mới **không được copy secret vào source**, không in secret ra log và phải cho phép thay secret mà không build lại binary/script.

## 4. Cấu hình và dữ liệu cục bộ

### 4.1. Thư mục mặc định

- Windows: `%USERPROFILE%\GeodeticAgent\`
- Linux: `~/geodetic/`

Cho phép override bằng biến môi trường hoặc tham số khởi động.

Các file tối thiểu:

```text
agent_config.json      # cấu hình agent + base/service config
device_id.txt          # serial ổn định của máy
license.key            # device token/license, không commit/log
agent.lock             # chống chạy hai agent
remote.lock            # khóa từ xa, tồn tại qua restart
agent.log              # log xoay vòng
agent_config.json.bak  # backup cấu hình gần nhất
```

Ghi file cấu hình theo kiểu atomic: ghi `.tmp`, flush/fsync, backup file cũ, sau đó replace. Khi JSON chính hỏng phải thử `.bak` trước khi tạo default.

### 4.2. Cấu hình tối thiểu đề xuất

```json
{
  "agent_version": "V2.2.4",
  "backend": {
    "host": "aitogy.click",
    "ws_scheme": "ws",
    "ws_port": 8000,
    "ws_path": "/ws/pi"
  },
  "mqtt": {
    "host": "45.117.179.134",
    "port": 1883,
    "username": "",
    "password": "",
    "tls": false,
    "ca_file": ""
  },
  "serial": {
    "port": "",
    "baudrate": 115200,
    "auto_detect": true,
    "baud_candidates": [115200, 460800, 921600, 230400, 38400, 9600]
  },
  "telemetry": {
    "status_interval_seconds": 5,
    "nmea_max_messages_per_second": 50,
    "raw_rtcm_enabled": true
  },
  "failover": {
    "stale_after_seconds": 45,
    "recovery_after_seconds": 5,
    "check_interval_seconds": 2
  }
}
```

`license.key` là token riêng của device, không phải JWT của user. Khi chưa có file hoặc token sai, agent vẫn phải chạy control plane ở chế độ hạn chế để nhận `DEPLOY_LICENSE`.

## 5. Định danh device và license/auth

### 5.1. Serial device

Serial phải ổn định qua restart, update và đổi process:

- Windows tương thích hiện tại: `LP_<MAC_HEX_UPPERCASE>`.
- Linux Raspberry Pi: ưu tiên serial phần cứng trong `/proc/cpuinfo`.
- Nếu không đọc được: tạo random ID một lần, lưu vào `device_id.txt`, không tạo lại mỗi lần chạy.
- Không tự đổi serial vì đổi port COM, hostname hoặc process ID.

Serial được dùng trong toàn bộ MQTT topic, WebSocket URL và database record.

### 5.2. Device token/license

Backend tạo token bằng license manager hiện tại và gửi xuống bằng lệnh:

```json
{
  "command": "DEPLOY_LICENSE",
  "payload": {
    "license_key": "<device-token>"
  }
}
```

Agent phải:

1. validate token không rỗng;
2. ghi atomically vào `license.key`;
3. không ghi token raw vào status/log;
4. gửi status `REBOOTING` hoặc trạng thái tương đương;
5. restart process để áp dụng token mới.

Fingerprint gửi trong status:

```text
agent_token_fingerprint = hex(SHA-256(trim(license.key)))[:16]
```

Backend so fingerprint này với SHA-256 của `devices.device_token`. Nếu không khớp, backend chuyển device sang `awaiting_license`, tắt trạng thái NTRIP và đánh dấu chưa sync.

### 5.3. Hai lớp xác thực

| Kênh | Credential | Mục đích |
|---|---|---|
| MQTT | broker username/password, tốt nhất MQTT over TLS | xác thực client với broker |
| WebSocket | device token trong query `?token=...` | backend xác thực đúng device |
| Status MQTT | `agent_token_fingerprint` | backend đối chiếu license trong hot path |
| Dashboard REST | `Authorization: Bearer <user JWT>` | chỉ dành cho user/dashboard, không đưa vào agent |

MQTT hot path hiện không query database để kiểm token cho từng gói `raw_data`/NMEA. Vì vậy ACL broker là ranh giới bảo mật bắt buộc: client của device A chỉ được publish/subscribe topic của A và không được subscribe wildcard của device khác.

File secret trên Linux phải có permission tương đương `0600`; Windows phải giới hạn ACL cho account chạy agent.

## 6. MQTT contract

### 6.1. Client ID, keepalive và LWT

- Client ID phải unique theo device/process, ví dụ `agent-{serial}-{boot_id}`.
- Keepalive đề xuất 60 giây; reconnect với backoff, không busy-loop.
- Bật Last Will and Testament trên topic status:

```json
{
  "serial": "LP_ABC123",
  "status": "offline",
  "timestamp": 1710000000,
  "agent_timestamp": 1710000000,
  "status_boot_id": "<boot-id>",
  "status_event": "lwt"
}
```

LWT publish với QoS 1, retain `true`.

### 6.2. Topic bắt buộc

| Hướng | Topic | QoS | Retain | Nội dung |
|---|---|---:|---:|---|
| Agent → backend | `pi/devices/{serial}/status` | 1 | true | status JSON |
| Agent → backend | `pi/devices/{serial}/raw_data` | 0 | false | NMEA bytes hoặc raw RTCM bytes |
| Backend → agent | `pi/devices/{serial}/command` | 1 | false | command JSON |
| Backend → agent | `pi/devices/{serial}/control_ack` | 1 | false | control-plane ACK |

Để tương thích agent cũ, agent mới có thể subscribe thêm:

```text
pi/devices/{serial}/commands
pi/device/{serial}/command
```

Topic `/nmea` trong tài liệu cũ không phải topic backend hiện tại đang subscribe. NMEA mới phải đi vào `raw_data`; nếu backend muốn hỗ trợ `/nmea` thì phải thêm subscription riêng.

### 6.3. MQTT command flow

1. Dashboard gọi REST backend.
2. Backend kiểm quyền user và tìm device.
3. Backend publish JSON vào `pi/devices/{serial}/command` khi MQTT đang sẵn sàng.
4. Agent parse và thực thi command.
5. Agent gửi status ngay với `last_command_result`.
6. Nếu MQTT backend down, backend gửi cùng command qua WebSocket.

Agent phải xử lý duplicate command an toàn. Nếu envelope có `command_id`, lưu các ID gần nhất và không thực thi lại command đã thành công. Nếu không có, agent tạo UUID để ghi vào `last_command_result`; backend mới nên bổ sung `command_id` cho các command có side effect.

## 7. WebSocket contract và fallback

### 7.1. Kết nối

URL tương thích hiện tại:

```text
ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}?token={url-encoded-license-key}
```

Production ưu tiên:

```text
wss://cors.aitogy.com/ws/pi/{MACHINE_SERIAL}?token={url-encoded-license-key}
```

Không nối token raw vào log. Token phải URL-encode. Khi server đóng với code `1008`, coi là lỗi auth/policy; không retry vô hạn với cùng token mà phải chờ license/config mới. Khi code `1013`, retry sau backoff.

Tham số tương thích hiện tại:

- ping interval: 30 giây;
- ping timeout: 60 giây;
- open timeout: 30 giây;
- close timeout: 10 giây;
- reconnect backoff: tối thiểu 5 giây, tối đa 120 giây.

Agent phải giữ WebSocket mở song song với MQTT. WebSocket là kênh fallback cho command và raw NMEA, không phải lý do để ngắt MQTT.

### 7.2. Frame JSON

Agent gửi status:

```json
{
  "type": "status_update",
  "payload": {
    "serial": "LP_ABC123",
    "status": "online",
    "status_boot_id": "<boot-id>",
    "status_sequence": 17,
    "agent_timestamp": 1710000000.123
  }
}
```

Agent gửi NMEA fallback:

```json
{
  "type": "nmea_update",
  "payload": "$GNGGA,...*hh\r\n"
}
```

Backend gửi command qua WebSocket bằng đúng envelope command ở mục 8, không bọc thêm lớp khác.

Backend gửi ACK:

```json
{
  "type": "control_plane_ack",
  "channel": "websocket",
  "serial": "LP_ABC123",
  "status_boot_id": "<boot-id>",
  "status_sequence": 17,
  "server_timestamp": 1710000000.456
}
```

### 7.3. Raw RTCM fallback không decode

Backend hiện đã xử lý raw RTCM trên MQTT nhưng endpoint WebSocket hiện tại chỉ nhận JSON. Để không mất chức năng khi MQTT down, backend phải bổ sung handler cho envelope sau:

```json
{
  "type": "rtcm_raw",
  "encoding": "base64",
  "payload": "<base64-raw-rtcm-chunk>",
  "timestamp": 1710000000.123
}
```

Yêu cầu handler backend:

- base64 decode rồi đưa vào cùng pipeline `raw_data`;
- không yêu cầu agent decode RTCM;
- giới hạn kích thước frame/chunk và rate để chống memory DoS;
- không broadcast lại `rtcm_signal_update` nếu payload chưa được backend decode;
- nếu chưa kịp triển khai handler này thì phải ghi rõ giới hạn: NTRIP vẫn chạy trực tiếp, nhưng raw RTCM dashboard không có fallback WebSocket. Đây không được coi là hoàn tất đầy đủ.

## 8. Command envelope và danh sách lệnh

### 8.1. Envelope chuẩn

```json
{
  "command": "<COMMAND_NAME>",
  "payload": {},
  "command_id": "optional-uuid",
  "original_config": {}
}
```

Chỉ `command` và `payload` là bắt buộc theo schema backend hiện tại. `command_id` và `original_config` là metadata tùy command.

Agent phải reject nếu:

- envelope không phải JSON object;
- thiếu `command`;
- `payload` không phải object;
- command không nằm trong allowlist;
- field bắt buộc sai kiểu hoặc vượt giới hạn;
- device đang `remote.locked`, trừ `LOCK_DEVICE`/`UNLOCK_DEVICE`;
- đang có command long-running khác chạy.

### 8.2. Allowlist command bắt buộc

| Command | Tác dụng |
|---|---|
| `LOCK_DEVICE` | tạo `remote.lock`, dừng/khóa đường GNSS và NTRIP, vẫn giữ control/status |
| `UNLOCK_DEVICE` | xóa `remote.lock`, khởi động lại pipeline cần thiết |
| `PROVISION_DEVICE` | đặt tên device và `is_provisioned=true` |
| `DEPLOY_LICENSE` | ghi token, báo status, restart process |
| `DEPLOY_SERVICE_CONFIG` | lưu service/NTRIP config và restart workers |
| `SET_RTCM_STREAM_ACTIVE` | bật/tắt toàn cục hoặc server/mountpoint cụ thể |
| `EXECUTE_RAW_COMMANDS` | gửi chuỗi lệnh đã base64 tới GNSS serial |
| `TRIGGER_AUTO_BASE` | chạy quy trình tự khảo sát base qua NTRIP |
| `TRIGGER_BASE_REFERENCE_CHECK` | kiểm tra base với tọa độ tham chiếu rồi restore |
| `STOP_AUTO_BASE` | dừng auto-base/reference-check |
| `APPLY_VN2000_PROVINCE` | áp transform VN2000 theo `province_code` |
| `CHECK_BASE_STATUS` | đọc trạng thái mode/log của GNSS nếu chip hỗ trợ |
| `REBOOT_DEVICE` | restart agent process |
| `DELETE_DEVICE` | xóa config/license cục bộ rồi restart |

`CONFIGURE_CHIP` là command UI cũ, không phải command wire bắt buộc của agent. Backend phải translate thành `TRIGGER_AUTO_BASE` hoặc `EXECUTE_RAW_COMMANDS` như đang làm.

### 8.3. JSON mẫu cho các command chính

#### Provision

```json
{
  "command": "PROVISION_DEVICE",
  "payload": { "name": "BASE_HN_01" },
  "command_id": "2d3d8ed2-0e91-4c10-a1f2-000000000001"
}
```

#### Deploy service config

Tên field phải giữ nguyên với `schemas.ServiceConfig`:

```json
{
  "command": "DEPLOY_SERVICE_CONFIG",
  "payload": {
    "ncomport": "COM7",
    "reconnectioninterval": 10,
    "stream_on_demand": true,
    "stream_active": false,
    "server1_stream_on_demand": true,
    "server1_stream_active": false,
    "server2_stream_on_demand": true,
    "server2_stream_active": false,

    "server1_enabled": true,
    "ntrip_version1": 1,
    "username1": "source_user",
    "serverhost1": "caster.example.com",
    "port1": 2101,
    "mountpoint1": "HN01",
    "password1": "<secret>",

    "server2_enabled": false,
    "ntrip_version2": 1,
    "username2": null,
    "serverhost2": null,
    "port2": null,
    "mountpoint2": null,
    "password2": null,

    "rtcm_enabled": false,
    "rtcmntripversion1": 1,
    "rtcmserver1": null,
    "rtcmport1": null,
    "rtcmmountpoint1": null,
    "rtcmusername1": null,
    "rtcmpassword1": null
  }
}
```

Quy tắc stream:

- `stream_on_demand=false`: server enabled được phép chạy Always-On.
- `stream_on_demand=true`: khởi động ở standby/sleep nếu `stream_active=false`.
- `server{1,2}_stream_on_demand` và `server{1,2}_stream_active` có thể override global.
- sleep phải đóng socket NTRIP và giải phóng queue/worker tương ứng.
- một server Always-On không được bị lệnh sleep global tắt nhầm.

#### Bật/tắt RTCM stream

```json
{
  "command": "SET_RTCM_STREAM_ACTIVE",
  "payload": {
    "active": true,
    "mountpoint": "HN01",
    "address": "caster.example.com",
    "server_id": 1
  }
}
```

`server_id` nhận `1` hoặc `2`; hỗ trợ alias legacy `server_idx`/`server`. Có thể dùng `caster_host`/`ip`/`host` thay `address` để tương thích.

#### Raw GNSS commands

```json
{
  "command": "EXECUTE_RAW_COMMANDS",
  "payload": {
    "commands_b64": [
      "bG9nIGNvbTQgMTo="
    ]
  },
  "original_config": {
    "sensor_type": "Unicorecomm",
    "mode": "BASE",
    "params": {}
  }
}
```

Giới hạn tương thích hiện tại:

- `commands_b64` là list, không rỗng, tối đa 200 item;
- mỗi item tối đa 4096 ký tự;
- phải validate base64 trước khi mở serial;
- hỗ trợ delay marker `$DELAY_<milliseconds>$`;
- pause GNSS reader trước khi ghi, flush, đọc response giới hạn, resume sau khi xong;
- không log nội dung command có thể chứa credential.

#### Auto-base

```json
{
  "command": "TRIGGER_AUTO_BASE",
  "payload": {
    "ip": "caster.example.com",
    "port": 2101,
    "user": "rover_user",
    "password": "<secret>",
    "mountpoint": "VRS01",
    "timeout": 3600,
    "samples": 60,
    "sensor_type": "Ublox",
    "enable_itrf_vn2000_transform": true,
    "l0_deg": null,
    "k0": null
  }
}
```

Các trường `ip`, `port`, `user`, `password`, `mountpoint` bắt buộc. Không cho phép CR/LF trong host/user/mountpoint/password field dùng để dựng request.

#### Reference check

```json
{
  "command": "TRIGGER_BASE_REFERENCE_CHECK",
  "payload": {
    "ip": "caster.example.com",
    "port": 2101,
    "user": "rover_user",
    "password": "<secret>",
    "mountpoint": "VRS01",
    "timeout": 300,
    "samples": 60,
    "reference_coords": {
      "lat": 21.0285,
      "lon": 105.8542,
      "alt": 10.0
    }
  }
}
```

Tọa độ phải validate `lat [-90,90]`, `lon [-180,180]`, `alt` hợp lệ; phải lưu cấu hình base cũ để restore kể cả khi process restart giữa chừng.

#### VN2000, lock, reboot

```json
{ "command": "APPLY_VN2000_PROVINCE", "payload": { "province_code": "HN" } }
{ "command": "LOCK_DEVICE", "payload": {} }
{ "command": "UNLOCK_DEVICE", "payload": {} }
{ "command": "REBOOT_DEVICE", "payload": {} }
{ "command": "DELETE_DEVICE", "payload": {} }
```

`DELETE_DEVICE` phải xóa `agent_config.json` và `license.key` sau khi đã dừng workers, gửi status cuối cùng và đảm bảo file không còn bị giữ bởi process.

## 9. Status contract

### 9.1. Tần suất và đường gửi

- Gửi mỗi 5 giây mặc định.
- Gửi ngay khi MQTT connect, WebSocket connect, sau khi nhận command, sau khi đổi state và trước reboot/delete.
- Gửi status qua cả MQTT và WebSocket để backend có thể ACK từng kênh.
- MQTT status: QoS 1, retain true.
- WebSocket status: wrapper `{"type":"status_update","payload":...}`.
- Không gửi cùng một raw NMEA/RTCM đồng thời cả hai kênh nếu một kênh đã nhận thành công; tránh duplicate hot path. Status là ngoại lệ, phải gửi cả hai.

### 9.2. Trường bắt buộc/tương thích

Agent phải giữ các field sau, kể cả khi giá trị là `null`, `{}` hoặc `false`:

```json
{
  "serial": "LP_ABC123",
  "name": "BASE_HN_01",
  "status": "online",
  "version": "V2.2.4",
  "timestamp": 1710000000.123,
  "agent_timestamp": 1710000000.123,
  "status_boot_id": "<random-per-process>",
  "status_sequence": 17,
  "status_event": "heartbeat",
  "agent_token_fingerprint": "<sha256-16-hex-or-null>",
  "rtk_fix_status": "FIXED",
  "base_mode_active": true,
  "detected_chip_type": "Ublox",
  "detected_chip_port": "COM7",
  "detected_chip_baud": 115200,
  "is_provisioned": true,
  "is_locked": false,
  "is_synced": true,
  "base_config": {},
  "service_config": {},
  "auto_base_progress": {},
  "ntrip_connected": true,
  "ntrip_status": {},
  "ntrip_stats": {},
  "ntrip_stream_state": {},
  "nmea_health": {},
  "system_info": {},
  "transport_status": {},
  "last_command_result": {},
  "reconnect_counters": {},
  "queue_metrics": {},
  "serial_health": {},
  "savings_stats": {}
}
```

`version` phải bằng `APP_VERSION` backend hiện tại (`V2.2.4`) nếu chưa có version negotiation. Backend đánh dấu `is_synced=false` khi agent version khác server.

Các status hợp lệ tối thiểu:

```text
initializing, unprovisioned, awaiting_license, online, sleep, streaming,
configuring, auto_setup_*, locked, rebooting, rebooting_for_reset, offline
```

### 9.3. Sequence và control-plane ACK

- `status_boot_id`: random mỗi process start.
- `status_sequence`: integer tăng dần trong cùng boot, bắt đầu từ 1.
- Không reset sequence giữa các status trong cùng process.
- Backend tạo ACK MQTT vào `pi/devices/{serial}/control_ack` và ACK WebSocket tương ứng.
- Agent chỉ chấp nhận ACK có đúng `status_boot_id`, sequence đã gửi và đúng channel.
- Bỏ qua ACK retained hoặc ACK quá cũ.

Watchdog tương thích hiện tại:

- nếu cả MQTT ACK và WebSocket ACK stale quá 45 giây: bật fail-safe runtime, cho phép các output enabled chuyển Always-On để tránh mất stream;
- sau khi có ACK mới và ổn định 5 giây: tắt fail-safe và restore config On-Demand;
- fail-safe không ghi đè config persistent;
- status phải công bố `control_plane_failover.active`, `reason`, tuổi ACK và sequence cuối.

### 9.4. Kết quả command

Sau mỗi command, status phải có:

```json
{
  "last_command_result": {
    "command": "SET_RTCM_STREAM_ACTIVE",
    "source": "mqtt",
    "status": "success",
    "detail": "stream activated",
    "command_id": "<uuid-or-null>",
    "timestamp": 1710000000
  }
}
```

Các trạng thái kết quả: `running`, `success`, `rejected`, `error`. `detail` tối đa 300 ký tự và không chứa password/token.

## 10. Serial/GNSS và pipeline dữ liệu

### 10.1. Detect chip/port

Phải hỗ trợ Windows COM và Linux `/dev/ttyUSB*`, `/dev/ttyACM*`:

- enumerate port;
- thử baud candidates;
- active probe U-blox và Unicore/UM982;
- fallback passive nếu active probe không thành công;
- bỏ qua Bluetooth/RFCOMM nếu không được cấu hình rõ;
- tự detect lại khi port biến mất/reconnect;
- chỉ một owner được đọc/ghi serial tại một thời điểm.

Nếu chưa detect được GNSS, agent vẫn phải chạy MQTT/WebSocket/status và retry detect, không crash toàn process.

### 10.2. NMEA

- đọc theo line;
- validate checksum nếu có checksum;
- ưu tiên GGA/GSA/GSV/GST;
- rate limit mặc định 50 message/giây;
- forward raw ASCII, giữ CR/LF nếu có thể;
- update `rtk_fix_status`, `nmea_health`, `base_coords`/health khi có dữ liệu;
- queue có giới hạn, khi đầy phải drop có đếm metric, không block GNSS reader vô hạn.

MQTT NMEA publish dùng topic `raw_data`, QoS 0. Nếu publish không thành công và WebSocket đang connected thì gửi `nmea_update`.

### 10.3. RTCM

- nhận raw bytes từ serial;
- tách frame tối thiểu theo `0xD3` và length hoặc giữ chunk an toàn;
- không decode semantic;
- gửi nguyên frame/chunk tới tất cả NTRIP worker đang active;
- chỉ forward khi server enabled và stream gate cho phép;
- loại hoặc đếm frame lỗi nếu có CRC validation;
- không để telemetry/decoder queue làm mất đường NTRIP;
- expose `rtcm_input_bps`, queue depth, drop count và connection state.

Nếu `raw_rtcm_enabled=true`, publish RTCM raw lên MQTT `raw_data` QoS 0. Khi MQTT không nhận được, dùng `rtcm_raw` WebSocket envelope ở mục 7.3.

## 11. NTRIP và stream on-demand

### 11.1. NTRIP server output

Hỗ trợ tối đa hai output độc lập:

- `server1_enabled`, `serverhost1`, `port1`, `username1`, `password1`, `mountpoint1`, `ntrip_version1`;
- `server2_enabled`, `serverhost2`, `port2`, `username2`, `password2`, `mountpoint2`, `ntrip_version2`.

Yêu cầu:

- NTRIP v1 `SOURCE` và NTRIP v2 HTTP POST/chunked theo config;
- Basic Auth đúng credential;
- reconnect interval cấu hình được;
- socket timeout và keepalive;
- chỉ đẩy RTCM, không đẩy NMEA/UBX vào caster;
- update `ntrip_status.server1/server2`, bps, connected và error;
- stop/standby phải đóng socket để giải phóng tài nguyên;
- retry độc lập, lỗi server 1 không được làm chết server 2.

### 11.2. NTRIP client/inject correction

Nếu `rtcm_enabled=true`, hỗ trợ:

```text
rtcmntripversion1
rtcmserver1
rtcmport1
rtcmmountpoint1
rtcmusername1
rtcmpassword1
```

Các field canonical hiện tại là `rtcmserver1`, `rtcmport1`, `rtcmmountpoint1`, `rtcmusername1` và `rtcmpassword1`. Agent phải giữ đúng tên này khi load/save config và khi nhận `DEPLOY_SERVICE_CONFIG`.

Client phải inject correction bytes vào GNSS serial bằng queue có giới hạn, không chặn thread đọc.

## 12. Base mode và cấu hình chip

Các workflow phải giữ nguyên:

1. **SURVEY_IN:** backend build raw command, agent nhận `EXECUTE_RAW_COMMANDS`.
2. **FIXED_LLA:** backend build raw command, agent nhận `EXECUTE_RAW_COMMANDS`.
3. **AUTO_CORS:** agent nhận `TRIGGER_AUTO_BASE`, lấy correction/NTRIP, average vị trí, cấu hình chip base, lưu kết quả và report progress.
4. **Reference check:** tạm chuyển/kiểm tra theo reference coordinate, lưu config cũ và restore dù có lỗi/restart.
5. **VN2000 province:** nhận `province_code`, áp thông số projection và cập nhật base config.
6. **CHECK_BASE_STATUS:** đọc mode/log GNSS nếu driver hỗ trợ.

Agent phải lưu `auto_base_progress` dạng JSON để dashboard theo dõi; state machine không được chạy song song hai job. `STOP_AUTO_BASE` phải hủy job, đóng client và resume GNSS reader.

## 13. Lock, restart, shutdown và service

- Lock file chống hai instance.
- Signal handling cho Ctrl+C, SIGTERM và Windows shutdown.
- Shutdown theo thứ tự: ngừng publisher → ngừng NTRIP workers → stop serial → gửi offline cleanup status → stop MQTT → đóng WebSocket → xóa process lock.
- Không xóa `remote.lock` khi process restart bình thường.
- Khi `LOCK_DEVICE`, vẫn giữ control/status để nhận `UNLOCK_DEVICE`.
- `REBOOT_DEVICE` phải gửi status trước khi restart.
- `DELETE_DEVICE` phải stop mọi worker trước khi xóa file.
- Linux: cung cấp systemd unit với restart policy, user riêng, WorkingDirectory và EnvironmentFile.
- Windows: cung cấp script chạy foreground và hướng dẫn Task Scheduler/Windows Service; không phụ thuộc console đang mở.
- Log xoay vòng theo kích thước/thời gian; credential, token và raw password không được ghi log.

## 14. REST endpoints liên quan để test end-to-end

Đây là endpoint của backend/dashboard, agent không gọi trực tiếp bằng user JWT:

| Method | Endpoint | Kết quả phía agent |
|---|---|---|
| `POST` | `/api/auth/login` | lấy JWT cho user/dashboard |
| `GET` | `/api/auth/me` | kiểm tra user |
| `POST` | `/api/devices/{serial}/command` | gửi command envelope generic |
| `POST` | `/api/devices/{serial}/configure-chip` | backend translate sang raw/auto-base |
| `POST` | `/api/devices/{serial}/configure-service` | gửi `DEPLOY_SERVICE_CONFIG` |
| `POST` | `/api/devices/{serial}/auto-deploy-token` | gửi `DEPLOY_LICENSE` |
| `POST` | `/api/devices/{serial}/reference-check` | gửi `TRIGGER_BASE_REFERENCE_CHECK` |
| `PUT` | `/api/devices/{serial}/name` | gửi `PROVISION_DEVICE` |
| `POST` | `/api/devices/{serial}/reset` | gửi `REBOOT_DEVICE` |
| `POST` | `/api/devices/{serial}/hard-reset` | gửi `DELETE_DEVICE` |
| `POST` | `/api/devices/{serial}/lock` | gửi `LOCK_DEVICE` |
| `POST` | `/api/devices/{serial}/unlock` | gửi `UNLOCK_DEVICE` |

Dashboard user auth dùng:

```http
Authorization: Bearer <user-jwt>
Content-Type: application/json
```

Không đưa `user-jwt` vào MQTT, WebSocket hoặc `license.key`.

## 15. Bảo mật và giới hạn bắt buộc

- Không hard-code MQTT password, NTRIP password, device token hoặc secret key.
- Production phải dùng TLS: MQTT TLS (`8883` hoặc port deployment) và `wss`.
- Validate hostname/IP/port, giới hạn độ dài string và reject CR/LF trong field dùng dựng protocol.
- Chặn path traversal khi đọc/ghi config/license.
- Raw command chỉ được nhận từ backend đã auth; validate base64, giới hạn số lượng/kích thước và serialize việc ghi serial.
- Không tin `serial` trong payload MQTT để route; serial của topic là identity chính.
- Không subscribe wildcard rộng từ client device.
- Không thực thi command ngoài allowlist.
- Không in token/password vào log, exception hoặc `last_command_result`.
- Khi token không khớp, không chạy NTRIP dù MQTT vẫn còn connected.
- Không để retained raw data làm agent thực thi lại command; command topic phải non-retained.

## 16. Tiêu chí nghiệm thu

### Kết nối/auth

- [ ] Chạy được trên Windows và Linux với cùng source/config schema.
- [ ] Serial ổn định qua restart; tự detect lại COM/TTY bị rút/cắm.
- [ ] MQTT connect/reconnect, LWT offline, subscribe đúng topic.
- [ ] WebSocket connect/reconnect, URL encode token, reject `1008` đúng cách.
- [ ] Token đúng → fingerprint khớp; token sai/mất → `awaiting_license` và không stream.
- [ ] Không có token vẫn nhận được `DEPLOY_LICENSE` qua control plane.

### Command/control plane

- [ ] Mỗi command trong allowlist có test JSON thành công và test payload sai.
- [ ] MQTT up → command nhận qua MQTT.
- [ ] MQTT down → command nhận qua WebSocket.
- [ ] Duplicate command không gây chạy raw command/reboot/delete hai lần.
- [ ] Status sau command có `last_command_result` và `command_id`.
- [ ] ACK hai kênh cập nhật failover watchdog đúng sequence/boot ID.
- [ ] Cả hai ACK stale >45 giây → fail-safe; ACK mới liên tục 5 giây → restore.

### Dữ liệu GNSS/NTRIP

- [ ] NMEA GGA/GSA/GSV/GST đi đúng `raw_data` và fallback `nmea_update`.
- [ ] RTCM không bị decode tại agent nhưng vẫn đi nguyên vẹn tới NTRIP server.
- [ ] Có raw RTCM MQTT và raw RTCM WebSocket fallback theo mục 7.3.
- [ ] Hai NTRIP server chạy/ reconnect độc lập.
- [ ] Sleep đóng đúng stream; wake đúng server/mountpoint; Always-On không bị tắt nhầm.
- [ ] NTRIP client inject correction không chặn serial reader.
- [ ] Auto-base/reference-check có progress, cancel và restore sau lỗi.

### Vận hành/an toàn

- [ ] Lock/unlock giữ control channel sống.
- [ ] Reboot/delete dừng worker và dọn file đúng thứ tự.
- [ ] Config atomic, có backup/recovery.
- [ ] Không có credential/token trong log, status fingerprint hoặc crash report.
- [ ] Queue đầy không làm treo process; metrics cho drop/reconnect/queue được đưa vào status.
- [ ] Có test integration chạy với MQTT broker giả lập và WebSocket backend giả lập; không yêu cầu Internet thật.

## 17. Deliverables đội triển khai phải bàn giao

1. Source agent mới và entry point chạy Windows/Linux.
2. `config.example.json` không chứa secret thật.
3. Hướng dẫn cài dependency, chạy foreground, systemd và Windows Task Scheduler/Service.
4. Protocol test vectors: command JSON, status JSON, ACK JSON, LWT, NMEA, raw RTCM.
5. Unit/integration tests cho command validation, failover, auth fingerprint, queue và NTRIP reconnect.
6. Bảng mapping “tính năng cũ → module mới”.
7. Backend patch cho `rtcm_raw` WebSocket và test raw RTCM fallback.
8. Migration note: cách chuyển `agent_config.json`, `license.key`, serial và service config từ agent cũ.
9. Báo cáo những field nào giữ nguyên, field nào thêm mới, field nào không còn hỗ trợ.

## 18. Definition of Done

Chỉ nghiệm thu khi agent mới chạy được toàn bộ control/data path trên cả Windows và Linux, backend không cần sửa dashboard, MQTT và WebSocket fallback đều hoạt động, token/auth được kiểm tra, NTRIP stream không bị ảnh hưởng, và RTCM semantic decode đã được loại bỏ khỏi agent nhưng raw RTCM vẫn được forward đầy đủ.

## 20. Bản đặc tả rebuild theo source hiện tại

Phần 1–19 là contract ở mức yêu cầu. Phần này là mapping trực tiếp từ source hiện tại để team có thể triển khai lại mà không phải đoán protocol.

Source tham chiếu chính là geodetic/agent_universal.py, hiện đang có AGENT_VERSION = V2.2.4. Các đoạn code trong phần này được trích nguyên văn ở những điểm ảnh hưởng tới compatibility. Không bê nguyên toàn bộ file cũ: các phần decode RTCM phải bị loại bỏ theo mục 27.

Khi tài liệu và code backend khác nhau, ưu tiên theo thứ tự:

1. Topic, JSON và hành vi đang chạy trong geodetic/agent_universal.py.
2. Handler thật trong cors_dashboard/backend/app.
3. geodetic/api_pi_backend.md chỉ dùng để tham khảo vì có một số tên topic cũ.

### 20.1. Bản đồ source → module agent mới

| Chức năng | Hàm/lớp source hiện tại | Module đề xuất |
|---|---|---|
| Định danh và lưu serial | get_machine_serial | identity |
| Sinh/kiểm tra license | generate_pi_license_base, get_license_code_from_string, license_is_valid | auth |
| Fingerprint token | get_license_fingerprint | auth |
| MQTT | setup_mqtt_client, _get_mqtt_runtime_config | transport_mqtt |
| WebSocket | websocket_task | transport_ws |
| Nhận command | process_command, _validate_command_payload | commands |
| Status | AgentManager.get_full_status, send_status | status |
| NMEA | dispatch_nmea_data, NMEAPublisher | nmea |
| RTCM forwarding | dispatch_rtcm_data, extract_valid_rtcm3_packets | rtcm_forwarder |
| NTRIP caster | NTRIPServerWorker | ntrip_uplink |
| NTRIP correction | NTRIPClientWorker | ntrip_client |
| Liveness/fail-open | ControlPlaneFailover | control_plane |
| Lưu cấu hình | AgentManager.load_config, save_config | config |

Không bắt buộc giữ tên module hoặc ngôn ngữ Python. Bắt buộc giữ wire contract, field name, topic, QoS, retain, retry và thứ tự xử lý.

### 20.2. Quy ước JSON và bytes

- Mọi JSON được encode UTF-8.
- JSON command/status hiện tại được tạo bằng json.dumps mặc định; không sort key, không ký JSON, không tự thêm newline.
- MQTT raw_data có thể chứa bytes nhị phân trực tiếp đối với RTCM và ASCII đối với NMEA.
- WebSocket chỉ nhận/gửi text JSON. RTCM qua WebSocket bắt buộc dùng base64 chuẩn RFC 4648.
- Không hash chuỗi JSON. Hash duy nhất trong contract hiện tại là SHA-256 của license token để tạo fingerprint hiển thị.
- Không đổi số thành chuỗi nếu source đang gửi number; đặc biệt status_sequence, timestamp, port, bps, satellite_count.
- Field không có dữ liệu có thể là null, {}, [] hoặc không xuất hiện tùy status branch hiện tại. Team nên giữ field chính ổn định và dùng null cho giá trị chưa có.

Ví dụ serialization tương thích:

~~~python
message = json.dumps(command)
mqtt_client.publish(topic, message, qos=1, retain=False)
~~~

WebSocket status được bọc như sau:

~~~python
ws_message = {"type": "status_update", "payload": status_payload}
await websocket.send(json.dumps(ws_message))
~~~

---

## 21. Địa chỉ, port và cấu hình runtime chính xác

### 21.1. Hằng số hiện tại trong agent_universal.py

Đoạn dưới đây là nguyên văn phần connection configuration hiện tại:

~~~python
DEFAULT_BACKEND_HOST = "aitogy.click"
DEFAULT_MQTT_BROKER = "45.117.179.134"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_USERNAME = "mqttUser"
DEFAULT_MQTT_PASSWORD = "MqttPassword123$%^"

BACKEND_HOST = DEFAULT_BACKEND_HOST
MQTT_BROKER = DEFAULT_MQTT_BROKER
MQTT_PORT = DEFAULT_MQTT_PORT
MQTT_USERNAME = DEFAULT_MQTT_USERNAME
MQTT_PASSWORD = DEFAULT_MQTT_PASSWORD
DEFAULT_BAUDRATE = 115200 if IS_RASPBERRY_PI else 460800
STATUS_PUBLISH_INTERVAL_SECONDS = 5
CONTROL_PLANE_STALE_SECONDS = max(15.0, float(os.getenv("CONTROL_PLANE_STALE_SECONDS", "45")))
CONTROL_PLANE_RECOVERY_SECONDS = max(0.0, float(os.getenv("CONTROL_PLANE_RECOVERY_SECONDS", "5")))
CONTROL_PLANE_CHECK_INTERVAL_SECONDS = max(1.0, float(os.getenv("CONTROL_PLANE_CHECK_INTERVAL_SECONDS", "2")))
NMEA_IDLE_PUBLISH_INTERVAL_SECONDS = 1.0
NMEA_MQTT_PUBLISH_INTERVAL_SECONDS = 1.0
SYSTEM_INFO_INTERVAL_SECONDS = 30
PARSER_DEBUG_ENABLED = False
PARSER_DEBUG_INTERVAL_SECONDS = 2.0
~~~

Đây là credential legacy đang tồn tại trong source. Khi build agent mới:

- Không commit secret thật vào firmware hoặc repository.
- Đưa broker, port, username, password vào config được provision hoặc secret store.
- Nếu đổi credential/broker, phải đổi đồng thời agent và backend MQTT.
- Compatibility hiện tại là MQTT plaintext tại 45.117.179.134:1883; không tự chuyển sang TLS nếu broker chưa được cấu hình TLS.
- Với production nên hỗ trợ mqtts và wss bằng option, nhưng phải giữ ws/MQTT contract hiện tại trong giai đoạn migration.

### 21.2. Cách agent đọc MQTT runtime config

Đoạn source hiện tại:

~~~python
def _get_mqtt_runtime_config(agent=None) -> dict:
    cfg = {}
    if agent is not None and isinstance(getattr(agent, "config", None), dict):
        cfg = agent.config.get("mqtt") or agent.config.get("mqtt_config") or {}
        if not isinstance(cfg, dict):
            cfg = {}

    broker = (
        cfg.get("broker")
        or cfg.get("host")
        or MQTT_BROKER
    )
    port = cfg.get("port") or MQTT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 1883

    return {
        "broker": str(broker or BACKEND_HOST).strip(),
        "port": port,
        "username": cfg.get("username") or cfg.get("user") or DEFAULT_MQTT_USERNAME,
        "password": cfg.get("password") or cfg.get("pass") or DEFAULT_MQTT_PASSWORD,
    }
~~~

Config mới phải chấp nhận cả mqtt và mqtt_config để migrate file cũ. Nếu không có config, dùng default tương thích trong source.

### 21.3. Đường dẫn local

Source hiện tại tạo:

~~~python
if IS_WINDOWS:
    BASE_DIR = os.path.join(user_home, "GeodeticAgent")
elif IS_RASPBERRY_PI:
    BASE_DIR = os.path.join(user_home, "geodetic")
else:
    BASE_DIR = os.path.expanduser("~/geodetic")

os.makedirs(BASE_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(BASE_DIR, "agent_config.json")
LICENSE_PATH = os.path.join(BASE_DIR, "license.key")
LOCK_FILE_PATH = os.path.join(BASE_DIR, "agent.lock")
REMOTE_LOCK_PATH = os.path.join(BASE_DIR, "remote.lock")
~~~

Agent mới phải giữ các file sau:

| File | Ý nghĩa |
|---|---|
| agent_config.json | device_name, is_provisioned, services, base_config, mqtt |
| agent_config.json.bak | bản backup trước lần ghi config |
| license.key | license/device token dạng text, strip khoảng trắng trước khi dùng |
| device_id.txt | serial đã sinh, tránh đổi serial sau reboot |
| agent.lock | PID/instance lock trong lúc command dài hoặc agent chạy |
| remote.lock | khóa từ xa; khi có file này chỉ cho phép LOCK/UNLOCK và status |

Ghi config phải atomic. Source hiện dùng file tạm, fsync, backup rồi os.replace:

~~~python
def save_config(self):
    tmp_path = CONFIG_PATH + '.tmp'
    bak_path = CONFIG_PATH + '.bak'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(CONFIG_PATH):
            import shutil
            try:
                shutil.copy2(CONFIG_PATH, bak_path)
            except Exception:
                pass
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        self.log("ERROR", f"Critical error saving config: {e}")
~~~

---

## 22. Device identity, license và auth: từng byte được xử lý thế nào

### 22.1. Sinh MACHINE_SERIAL

Đây là code hiện tại:

~~~python
def get_machine_serial():
    SERIAL_FILE_PATH = os.path.join(BASE_DIR, "device_id.txt")

    if os.path.exists(SERIAL_FILE_PATH):
        try:
            with open(SERIAL_FILE_PATH, 'r') as f:
                saved_serial = f.read().strip()
                if saved_serial.startswith("LP_") and len(saved_serial) > 10:
                    return saved_serial
        except Exception:
            pass
    final_serial = ""

    if IS_WINDOWS:
        try:
            mac = uuid.getnode()
            mac_str = f"{mac:012X}"
            final_serial = f"LP_{mac_str}"
            logging.info(f"Generated new serial from MAC: {final_serial}")
        except Exception:
            final_serial = f"LP_RND_{uuid.uuid4().hex[:12]}"

    elif IS_RASPBERRY_PI:
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        final_serial = line.strip().split(':')[-1].strip()
        except: pass
        if not final_serial:
            final_serial = f"PI_{uuid.uuid4().hex[:12]}"
    else:
        final_serial = f"UNK_{uuid.uuid4().hex[:12]}"

    try:
        with open(SERIAL_FILE_PATH, 'w') as f:
            f.write(final_serial)
            logging.info(f"Saved new serial to {SERIAL_FILE_PATH}")
    except Exception as e:
        logging.error(f"Cannot save device ID: {e}")

    return final_serial
~~~

Quy tắc bắt buộc:

- Windows: LP_ + MAC 12 ký tự hex viết hoa.
- Raspberry Pi: ưu tiên trường Serial trong /proc/cpuinfo.
- Linux khác: UNK_ + UUID lần đầu; source cũ không lấy machine-id.
- Sau khi đã có device_id.txt, không tự sinh lại serial khi reboot.
- ESP32 dùng eFuse MAC ổn định; không dùng random serial mỗi boot.

### 22.2. Sinh license key từ serial

Đây là hai hàm nguyên văn trong agent. Backend license_manager.py phải tạo cùng kết quả:

~~~python
def get_license_code_from_string(string_number: str) -> str:
    try:
        length = len(string_number)
        if length == 0: return ""
        arr = [0] * (length + 1)
        narr = [0.0] * (length + 1)
        number = float(string_number)
        r_string = ""
        n = length
        number *= n
        arr[0] = int(number * math.pow(10.0, -float(n)))
        for i in range(1, length):
            number = number - float(arr[i - 1]) / math.pow(10.0, -float(n))
            n -= 1
            arr[i] = int(number * math.pow(10, -float(n)))
        arr[length] = (arr[length - 2] + arr[length - 1]) / 2 + 1
        narr[0] = float(arr[0])
        for i in range(1, length + 1):
            narr[i] = (narr[i - 1] + float(arr[i])) / 2.0
        for i in range(length):
            A = narr[i + 1] * math.exp(-0.2)
            B = (math.log(arr[length], 10)) * math.pow(arr[i + 1], 0.2)
            r_string += str(int(round(A + B)))
        return r_string
    except (ValueError, IndexError, TypeError, ZeroDivisionError):
        return "Error: Invalid input"

def generate_pi_license_base(serial_number: str) -> str:
    base_code_str = ""
    s_number_part = serial_number[-10:]
    temp_str = ''.join(c.upper() if 'a' <= c.lower() <= 'z' else c for c in s_number_part)
    for char in temp_str:
        base_code_str += str(ord(char))
    if len(base_code_str) > 12:
        return base_code_str[:12]
    return base_code_str.ljust(12, '0')
~~~

Luồng chính:

1. Lấy 10 ký tự cuối của serial.
2. Chuyển chữ cái ASCII sang uppercase.
3. Với từng ký tự, nối số nguyên ord(char) thành chuỗi decimal.
4. Cắt tối đa 12 ký tự hoặc padding bằng ký tự 0 tới đủ 12.
5. Đưa chuỗi 12 ký tự vào get_license_code_from_string.
6. Ghi kết quả nguyên dạng text vào license.key; khi đọc phải strip.

License này là token compatibility, không phải chữ ký số. Người có serial và thuật toán có thể tính lại. Không dùng nó làm cơ chế chống giả mạo duy nhất cho hệ thống mới; broker ACL và TLS vẫn cần được dùng khi triển khai production.

### 22.3. Kiểm tra license local

~~~python
def license_is_valid():
    global MACHINE_SERIAL
    MACHINE_SERIAL = get_machine_serial()
    if "ERROR" in MACHINE_SERIAL or "UNKNOWN" in MACHINE_SERIAL:
        return False
    try:
        with open(LICENSE_PATH, 'r') as f:
            saved_key = f.read().strip()
    except Exception:
        return False
    expected_base = generate_pi_license_base(MACHINE_SERIAL)
    expected_key = get_license_code_from_string(expected_base)
    return saved_key == expected_key
~~~

Nếu false, agent vẫn được khởi động ở limited mode để nhận DEPLOY_LICENSE/LOCK/UNLOCK và gửi status, nhưng không được coi là online đầy đủ.

### 22.4. Fingerprint hash

Fingerprint không phải license key và không dùng để thay thế token. Nó chỉ gửi một phần hash trong status:

~~~python
def get_license_fingerprint() -> str | None:
    try:
        if not os.path.exists(LICENSE_PATH):
            return None
        with open(LICENSE_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if not token:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None
~~~

Cụ thể:

1. Đọc toàn bộ license.key.
2. strip() ở đầu/cuối.
3. Encode UTF-8.
4. SHA-256.
5. hexdigest() lowercase.
6. Lấy 16 ký tự đầu.

Test vector:

~~~text
token input: 123456
SHA-256 full: 8d969eef6ecad3c2...
fingerprint: 8d969eef6ecad3c2
~~~

Không hash base code, không hash serial, không hash JSON và không base64 fingerprint.

### 22.5. Hai lớp authentication đang tồn tại

| Kênh | Credential | Cách gửi | Server kiểm tra |
|---|---|---|---|
| MQTT | broker username/password | MQTT CONNECT username_pw_set | MQTT broker |
| WebSocket | license.key raw token | query parameter token=URL_ENCODE(token) | device_token trong DB |
| Status | agent_token_fingerprint | field trong status | hiển thị/đối soát, không phải auth |
| REST dashboard | user JWT/session | chỉ dashboard gọi | FastAPI auth; agent không gọi REST hiện tại |

Agent không gửi password MQTT trong JSON command/status. Agent cũng không gọi endpoint login của dashboard.

### 22.6. WebSocket token encoding

Source hiện tại dùng nguyên token và URL-encode:

~~~python
auth_token = ""
if os.path.exists(LICENSE_PATH):
    try:
        with open(LICENSE_PATH, "r") as f:
            auth_token = f.read().strip()
    except Exception as e:
        logging.error(f"Error reading license key: {e}")

if auth_token:
    ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}?token={urllib.parse.quote(auth_token, safe='')}"
else:
    ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}"
~~~

safe='' là quan trọng: mọi ký tự đặc biệt của token phải được percent-encode. Không hash rồi gửi query token. Không encode base64 lần hai.

Backend auth logic hiện tại:

~~~python
@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str, token: str = Query(None)):
    enforce_auth = os.getenv("ENFORCE_AGENT_AUTH", "true").lower() != "false"
    metadata = await mqtt_handler.get_device_metadata(serial)
    expected_token = str(metadata.get("device_token") or "").strip()
    db_sync_state = bool(metadata.get("is_synced", False))
    license_bypass = bool(metadata.get("license_bypass"))
    provided_token = str(token or "").strip()

    if enforce_auth and expected_token and provided_token != expected_token and not license_bypass:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await pi_manager.connect(serial, websocket)
~~~

Đoạn trên là phần quyết định auth; source backend còn có timeout/cache/logging và vòng receive_json phía sau. Agent mới phải tương thích với close code 1008 và retry, không retry dồn dập.

---

## 23. MQTT: connect, subscribe, publish, LWT

### 23.1. Client ID và kết nối

Source hiện tại tạo client ID:

~~~python
if hasattr(mqtt, 'CallbackAPIVersion'):
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}"
    )
else:
    client = mqtt.Client(client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}")

if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
    client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])

client.connect_async(mqtt_cfg["broker"], mqtt_cfg["port"], 30)
client.loop_start()
~~~

ESP32 không có PID; dùng agent-SERIAL-BOOT_ID hoặc agent-SERIAL-random_suffix ổn định trong một session, không dùng client ID giống nhau cho hai process.

### 23.2. Topic chính xác

Với serial là SERIAL:

| Hướng | Topic | QoS | Retain | Payload |
|---|---|---:|---:|---|
| agent → backend | pi/devices/SERIAL/status | 1 | true | status JSON |
| agent → backend | pi/devices/SERIAL/raw_data | 0 | false | NMEA ASCII hoặc raw RTCM bytes |
| backend → agent | pi/devices/SERIAL/command | 1 | false | command JSON |
| backend → agent | pi/devices/SERIAL/commands | 1 | false | alias command JSON |
| backend → agent | pi/device/SERIAL/command | 1 | false | alias command JSON |
| backend → agent | pi/devices/SERIAL/control_ack | 1 | false | control_plane_ack JSON |

Agent phải subscribe cả 3 topic command và 1 topic ACK để tương thích source cũ.

Backend subscribe data:

| Topic backend nghe | Ý nghĩa |
|---|---|
| pi/devices/+/status | status và probe để trả ACK |
| pi/devices/+/raw_data | NMEA, raw RTCM hoặc JSON legacy |
| pi/devices/+/base_config_state | trạng thái sync base |
| pi/devices/+/service_config_state | trạng thái sync service |
| ntrip/rtcm/start | lệnh điều khiển stream từ backend |
| ntrip/rtcm/stop | lệnh điều khiển stream từ backend |

Không dùng topic /nmea trong agent mới nếu cần tương thích code backend hiện tại; tên đang chạy là /raw_data.

### 23.3. setup_mqtt_client — đoạn cốt lõi trích từ source

~~~python
def setup_mqtt_client(loop: asyncio.AbstractEventLoop, agent: AgentManager, gnss_reader: GNSSReader):
    mqtt_cfg = _get_mqtt_runtime_config(agent)
    if hasattr(mqtt, 'CallbackAPIVersion'):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}")
    else:
        client = mqtt.Client(client_id=f"agent-{MACHINE_SERIAL}-{os.getpid()}")
    client.user_data_set({"agent": agent, "gnss_reader": gnss_reader})

    command_topics = [
        f"pi/devices/{MACHINE_SERIAL}/command",
        f"pi/devices/{MACHINE_SERIAL}/commands",
        f"pi/device/{MACHINE_SERIAL}/command",
    ]
    control_ack_topic = f"pi/devices/{MACHINE_SERIAL}/control_ack"

    def on_connect(c, userdata, flags, rc, properties=None):
        if rc == 0:
            _set_transport_state("mqtt", True)
            logging.info("Connected to MQTT Broker.")
            for topic in command_topics:
                result, mid = c.subscribe(topic, qos=1)
                logging.info(f"MQTT subscribe topic='{topic}' result={result} mid={mid}")
            result, mid = c.subscribe(control_ack_topic, qos=1)
            logging.info(f"MQTT control ACK subscription result={result} mid={mid}")
            asyncio.run_coroutine_threadsafe(send_status(userdata["agent"], c), loop)
        else:
            _telemetry_inc("reconnect", "mqtt")
            _set_transport_state("mqtt", False)
            logging.error(f"!!! MQTT connection failed, code: {rc}")

    def on_disconnect(c, userdata, *args):
        rc = args[-2] if len(args) >= 2 else (args[0] if args else 0)
        _set_transport_state("mqtt", False)
        if rc != 0:
            _telemetry_inc("reconnect", "mqtt")
            logging.warning(f"MQTT disconnected unexpectedly (rc={rc}).")

    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            if msg.topic == control_ack_topic and data.get("type") == "control_plane_ack":
                userdata["agent"].control_plane_failover.record_ack(
                    "mqtt",
                    data,
                    retained=bool(getattr(msg, "retain", False)),
                )
                return
            logging.info(f"MQTT command received topic='{msg.topic}' bytes={len(msg.payload)}")
            future = asyncio.run_coroutine_threadsafe(
                process_command('mqtt', data, userdata["agent"], userdata["gnss_reader"], c),
                loop
            )
            def on_done(f):
                try:
                    f.result()
                except Exception as e:
                    logging.error(f"Process command failed: {e}", exc_info=True)
            future.add_done_callback(on_done)
        except Exception as e:
            logging.error(f"Error processing MQTT message: {e}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=5, max_delay=120)

    if mqtt_cfg.get("username") and mqtt_cfg.get("password"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])

    last_will = json.dumps({
        "serial": MACHINE_SERIAL,
        "status": "offline",
        "timestamp": int(time.time()),
        "agent_timestamp": int(time.time()),
        "status_boot_id": STATUS_BOOT_ID,
        "status_event": "lwt"
    })
    client.will_set(
        f"pi/devices/{MACHINE_SERIAL}/status",
        payload=last_will,
        qos=1,
        retain=True
    )

    with telemetry_lock:
        transport_status["mqtt_broker"] = mqtt_cfg["broker"]
    client.connect_async(mqtt_cfg["broker"], mqtt_cfg["port"], 30)
    client.loop_start()
    return client
~~~

Nếu thư viện MQTT ESP32 dùng callback signature khác, chỉ adapter callback được đổi; topic, QoS, retain và JSON không được đổi.

### 23.4. LWT

LWT phải đăng ký trước CONNECT. Payload tương thích:

~~~json
{
  "serial": "LP_001122AABBCC",
  "status": "offline",
  "timestamp": 1710000000,
  "agent_timestamp": 1710000000,
  "status_boot_id": "0123456789abcdef0123456789abcdef",
  "status_event": "lwt"
}
~~~

Khi agent thoát có kiểm soát, vẫn nên gửi một status offline/reason trước khi disconnect; LWT là đường dự phòng cho mất điện/mất mạng.

---

## 24. Status JSON, sequence và ACK

### 24.1. Tạo status envelope

Đoạn cốt lõi của send_status trích từ source:

~~~python
async def send_status(agent: AgentManager, mqtt_client: mqtt.Client):
    global STATUS_SEQUENCE
    await initialization_complete.wait()

    status_payload = agent.get_full_status()
    STATUS_SEQUENCE += 1
    status_payload["status_boot_id"] = STATUS_BOOT_ID
    status_payload["status_sequence"] = STATUS_SEQUENCE
    status_payload["agent_timestamp"] = status_payload.get("timestamp", time.time())
    agent.control_plane_failover.record_probe(STATUS_SEQUENCE)

    try:
        system_info = get_cached_system_info()
        if system_info:
            status_payload['system_info'] = system_info
    except Exception as e:
        logging.error(f"Failed to collect system info: {e}")

    json_payload = json.dumps(status_payload)

    if active_websocket_connection:
        try:
            ws_message = {"type": "status_update", "payload": status_payload}
            await asyncio.wait_for(
                active_websocket_connection.send(json.dumps(ws_message)),
                timeout=2.0,
            )
        except Exception as e:
            _set_transport_state("websocket", False)
            logging.error(f"Failed to send status via WebSocket: {e}")

    if mqtt_client and mqtt_client.is_connected():
        try:
            topic = f"pi/devices/{MACHINE_SERIAL}/status"
            mqtt_client.publish(topic, json_payload, qos=1, retain=True)
        except Exception as e:
            _set_transport_state("mqtt", False)
            logging.warning(f"MQTT publish failed: {e}")
~~~

Status được gửi qua cả WebSocket và MQTT nếu cả hai đang connected. Đây là khác với raw NMEA/RTCM hot path: raw data dùng MQTT trước và chỉ fallback WebSocket khi publish MQTT không nhận được.

### 24.2. Field status tối thiểu

Agent mới phải giữ các field sau:

~~~json
{
  "serial": "LP_001122AABBCC",
  "name": "Station A",
  "status": "online",
  "version": "V2.2.4",
  "agent_token_fingerprint": "8d969eef6ecad3c2",
  "timestamp": 1710000000.123,
  "agent_timestamp": 1710000000.123,
  "status_boot_id": "0123456789abcdef0123456789abcdef",
  "status_sequence": 42,
  "rtk_fix_status": "RTK_FIXED",
  "detected_chip_type": "Unicorecomm",
  "detected_chip_port": "COM3",
  "detected_chip_baud": 460800,
  "is_provisioned": true,
  "is_locked": false,
  "is_synced": true,
  "base_config": {},
  "service_config": {},
  "base_coords": {},
  "auto_base_progress": {},
  "nmea_health": {},
  "ntrip_stats": {},
  "ntrip_connected": false,
  "transport_status": {},
  "last_command_result": {},
  "reconnect_counters": {},
  "queue_metrics": {}
}
~~~

Không decode RTCM nên không gửi semantic rtcm_skyview_calculated, rtcm_signal_update, satellite SNR/azimuth/elevation lấy từ RTCM. Có thể gửi counters:

~~~json
{
  "rtcm_stats": {
    "input_bps": 2480,
    "packets_forwarded": 120,
    "bytes_forwarded": 245760,
    "crc_invalid": 0,
    "queue_drops": 0
  }
}
~~~

Các field trên chỉ là transport counters, không phải kết quả giải mã message RTCM.

### 24.3. Backend tạo control_plane_ack

Backend hiện dùng source sau:

~~~python
def build_control_plane_ack(
    payload: Mapping[str, Any] | None,
    *,
    channel: str,
    serial: str,
    server_timestamp: float | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    boot_id = str(payload.get("status_boot_id") or "").strip()
    try:
        sequence = int(payload.get("status_sequence"))
    except (TypeError, ValueError):
        return None

    normalized_channel = str(channel or "").strip().lower()
    if not boot_id or len(boot_id) > 128 or sequence < 0:
        return None
    if normalized_channel not in {"websocket", "mqtt"}:
        return None

    return {
        "type": "control_plane_ack",
        "channel": normalized_channel,
        "serial": str(serial or ""),
        "status_boot_id": boot_id,
        "status_sequence": sequence,
        "server_timestamp": round(
            time.time() if server_timestamp is None else float(server_timestamp),
            3,
        ),
    }
~~~

MQTT ACK publish:

- topic: pi/devices/SERIAL/control_ack
- QoS: 1
- retain: false
- payload: object trên được json.dumps

WebSocket ACK là object control_plane_ack gửi ngược trên cùng socket.

Agent phải:

- chỉ accept ACK có đúng status_boot_id của boot hiện tại;
- chỉ accept sequence không lớn hơn sequence đã gửi;
- bỏ qua retained ACK;
- ghi nhận ACK riêng cho mqtt và websocket;
- stale mặc định sau 45 giây;
- recovery cần ACK mới liên tục ít nhất 5 giây;
- watchdog kiểm tra mặc định mỗi 2 giây;
- chỉ fail-open khi cả hai kênh đều stale.

## 25. WebSocket fallback

### 25.1. Kết nối và retry

Source hiện tại:

~~~python
async def websocket_task(agent: AgentManager, gnss_reader: GNSSReader, mqtt_client: mqtt.Client):
    global active_websocket_connection

    auth_token = ""
    if os.path.exists(LICENSE_PATH):
        try:
            with open(LICENSE_PATH, "r") as f:
                auth_token = f.read().strip()
        except Exception as e:
            logging.error(f"Error reading license key: {e}")

    if auth_token:
        ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}?token={urllib.parse.quote(auth_token, safe='')}"
    else:
        ws_uri = f"ws://{BACKEND_HOST}:8000/ws/pi/{MACHINE_SERIAL}"

    while True:
        try:
            async with websockets.connect(
                ws_uri,
                ping_interval=30,
                ping_timeout=60,
                close_timeout=10,
                open_timeout=30
            ) as websocket:
                active_websocket_connection = websocket
                _set_transport_state("websocket", True)
                await send_status(agent, mqtt_client)

                async for message in websocket:
                    data = json.loads(message)
                    if data.get("type") == "control_plane_ack":
                        agent.control_plane_failover.record_ack("websocket", data)
                        continue
                    await process_command(
                        'websocket',
                        data,
                        agent,
                        gnss_reader,
                        mqtt_client
                    )

        except asyncio.CancelledError:
            break
        except Exception as e:
            _telemetry_inc("reconnect", "websocket")
            logging.warning(f"WebSocket error: {e}. Retrying in 10s...")
        finally:
            active_websocket_connection = None
            _set_transport_state("websocket", False)
            await asyncio.sleep(10)
~~~

Tại thời điểm hiện tại URL là ws://aitogy.click:8000/ws/pi/SERIAL. Nếu backend chuyển sang TLS, thêm option wss và port TLS, không tự đổi trong agent khi chưa đổi server.

### 25.2. Quy tắc priority/fallback

- Command backend: MQTT trước; nếu MQTT down thì WebSocket.
- Ngoại lệ hiện tại: DEPLOY_LICENSE được gửi WebSocket trước để agent có thể nhận token khi MQTT/license path đang chưa sẵn sàng.
- Status: gửi cả MQTT và WebSocket khi cả hai connected.
- NMEA/raw RTCM: MQTT QoS 0 trước; chỉ gửi WebSocket khi publish MQTT thất bại hoặc MQTT disconnected, tránh duplicate.
- Nếu cả hai mất: buffer có giới hạn; không được giữ vô hạn làm tràn RAM.
- Retry WebSocket: 10 giây; MQTT reconnect delay: 5–120 giây.

Đừng suy luận active_channel là command priority. Source _set_transport_state ưu tiên hiển thị websocket nếu cả hai connected:

~~~python
def _set_transport_state(channel: str, connected: bool) -> None:
    now_ts = int(time.time())
    with telemetry_lock:
        if channel == "mqtt":
            transport_status["mqtt_connected"] = bool(connected)
        elif channel == "websocket":
            transport_status["websocket_connected"] = bool(connected)

        if transport_status.get("websocket_connected"):
            transport_status["active_channel"] = "websocket"
        elif transport_status.get("mqtt_connected"):
            transport_status["active_channel"] = "mqtt"
        else:
            transport_status["active_channel"] = None
~~~

active_channel chỉ là telemetry; không dùng nó để chọn command path.

---

## 26. Command API, envelope và xử lý

### 26.1. REST API không phải kênh agent gọi

Dashboard/backend nhận REST từ user rồi chuyển thành MQTT hoặc WebSocket. Agent chỉ cần MQTT/WebSocket.

Backend route hiện có:

| REST API | Agent command tạo ra |
|---|---|
| POST /api/devices/{serial}/command | command bất kỳ trong allowlist |
| POST /api/devices/{serial}/reference-check | TRIGGER_BASE_REFERENCE_CHECK |
| POST /api/devices/{serial}/configure-chip | TRIGGER_AUTO_BASE hoặc EXECUTE_RAW_COMMANDS |
| POST /api/devices/{serial}/configure-service | DEPLOY_SERVICE_CONFIG |
| POST /api/devices/{serial}/auto-deploy-token | DEPLOY_LICENSE |
| PUT /api/devices/{serial}/name | PROVISION_DEVICE |
| POST /api/devices/{serial}/reset | REBOOT_DEVICE |
| POST /api/devices/{serial}/hard-reset | DELETE_DEVICE |
| POST /api/devices/{serial}/lock | LOCK_DEVICE |
| POST /api/devices/{serial}/unlock | UNLOCK_DEVICE |

Schema generic hiện tại:

~~~python
class Command(BaseModel):
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)
~~~

Backend generic route gọi:

~~~python
@router.post("/api/devices/{serial}/command")
async def send_generic_command(
    serial: str,
    command: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return await send_command_to_pi(serial, command.model_dump())
~~~

Command producer:

~~~python
async def send_command_to_pi(serial: str, command: dict) -> dict:
    command_name = command.get("command")

    if command_name == "DEPLOY_LICENSE":
        success = await pi_manager.send_personal_message(serial, command)
        if success:
            return {"status": "command_sent", "channel": "websocket", "command": command_name}

    if mqtt_client and mqtt_client.is_connected():
        topic = f"pi/devices/{serial}/command"
        message = json.dumps(command)
        mqtt_handler.publish_message(topic, message)
        return {"status": "command_sent", "channel": "mqtt", "command": command_name}

    logging.warning("MQTT down. Trying WebSocket for '%s'.", serial)
    success = await pi_manager.send_personal_message(serial, command)
    if success:
        return {"status": "command_sent", "channel": "websocket", "command": command_name}

    raise HTTPException(
        status_code=503,
        detail=f"Cannot send command to '{serial}'. Both MQTT and WebSocket are unavailable.",
    )
~~~

### 26.2. Envelope chuẩn

Agent đọc chính xác:

~~~python
if not isinstance(data, dict):
    reject("command envelope must be an object")
command = data.get("command")
payload = data.get("payload", {})
if payload is None:
    payload = {}
command_id = (
    data.get("command_id")
    or (payload.get("command_id") if isinstance(payload, dict) else None)
    or str(uuid.uuid4())
)
~~~

Wire JSON tối thiểu:

~~~json
{
  "command": "SET_RTCM_STREAM_ACTIVE",
  "payload": {
    "active": true
  }
}
~~~

command_id là optional trong backend schema hiện tại. Agent phải tự tạo UUID nếu không có và đưa nó vào last_command_result.

Không chấp nhận:

- payload là array/string/number;
- command rỗng;
- command ngoài allowlist;
- command dài hoặc field chứa CR/LF tại các trường host/mountpoint/user/password;
- EXECUTE_RAW_COMMANDS có quá 200 item, item không phải base64 hoặc dài hơn 4096 ký tự;
- command cấu hình chip khi chưa detect serial port.

### 26.3. Validator nguyên văn

~~~python
def _validate_command_payload(command: str, payload: dict, data: dict, agent: AgentManager, gnss_reader: GNSSReader) -> tuple[bool, str]:
    if not command:
        return False, "missing command"
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return False, "payload must be an object"

    if command == "PROVISION_DEVICE" and not str(payload.get("name") or "").strip():
        return False, "missing payload.name"
    if command == "DEPLOY_LICENSE" and not str(payload.get("license_key") or "").strip():
        return False, "missing payload.license_key"
    if command == "DEPLOY_SERVICE_CONFIG" and not payload:
        return False, "missing service config payload"
    if command == "SET_RTCM_STREAM_ACTIVE" and "active" not in payload:
        return False, "missing payload.active"
    if command == "APPLY_VN2000_PROVINCE" and not str(payload.get("province_code") or "").strip():
        return False, "missing payload.province_code"
    if command == "TRIGGER_AUTO_BASE":
        required_fields = ("ip", "port", "user", "password", "mountpoint")
        if any(not str(payload.get(key) or "").strip() for key in required_fields if key != "password"):
            return False, "missing CORS connection fields"
        if not str(payload.get("password") or ""):
            return False, "missing CORS connection fields"
        try:
            port = int(payload.get("port"))
        except (TypeError, ValueError):
            return False, "invalid CORS port"
        if not 1 <= port <= 65535:
            return False, "invalid CORS port"
        if any(char in "\r\n" for char in "".join(str(payload.get(key) or "") for key in required_fields)):
            return False, "invalid CORS connection fields"
    if command == "EXECUTE_RAW_COMMANDS":
        commands_b64 = payload.get("commands_b64")
        if not isinstance(commands_b64, list) or not commands_b64:
            return False, "missing payload.commands_b64"
        if len(commands_b64) > 200:
            return False, "too many raw commands"
        for item in commands_b64:
            if not isinstance(item, str) or len(item) > 4096:
                return False, "invalid raw command item"
            try:
                base64.b64decode(item, validate=True)
            except Exception:
                return False, "raw command is not valid base64"
        if not agent.detected_chip.get("port"):
            return False, "no detected serial port"

    known_commands = {
        "LOCK_DEVICE",
        "UNLOCK_DEVICE",
        "PROVISION_DEVICE",
        "DEPLOY_LICENSE",
        "TRIGGER_AUTO_BASE",
        "TRIGGER_BASE_REFERENCE_CHECK",
        "STOP_AUTO_BASE",
        "APPLY_VN2000_PROVINCE",
        "EXECUTE_RAW_COMMANDS",
        "DEPLOY_SERVICE_CONFIG",
        "SET_RTCM_STREAM_ACTIVE",
        "DELETE_DEVICE",
        "CHECK_BASE_STATUS",
        "REBOOT_DEVICE",
    }
    if command not in known_commands:
        return False, f"unsupported command: {command}"
    return True, ""
~~~

### 26.4. Command behavior

| Command | Payload quan trọng | Hành vi |
|---|---|---|
| LOCK_DEVICE | {} | tạo remote.lock, status locked |
| UNLOCK_DEVICE | {} | xóa remote.lock |
| PROVISION_DEVICE | name | lưu tên, is_provisioned=true |
| DEPLOY_LICENSE | license_key | ghi license.key, status rebooting, restart process |
| TRIGGER_AUTO_BASE | ip, port, user, password, mountpoint, timeout, samples | lưu auto_base_setup, chạy state machine |
| TRIGGER_BASE_REFERENCE_CHECK | NTRIP fields, reference_coords | kiểm tra tọa độ, chạy reference check |
| STOP_AUTO_BASE | {} | dừng auto base và khôi phục stream |
| APPLY_VN2000_PROVINCE | province_code | áp transform/config theo backend province table |
| EXECUTE_RAW_COMMANDS | commands_b64[] | pause reader, base64 decode để ghi bytes vào chip, resume |
| DEPLOY_SERVICE_CONFIG | service config object | lưu services và restart NTRIP workers |
| SET_RTCM_STREAM_ACTIVE | active, server_id/mountpoint/address | bật/tắt fanout RTCM tới NTRIP |
| DELETE_DEVICE | {} | xóa config/license rồi thoát/restart theo source |
| CHECK_BASE_STATUS | {} | gửi lệnh kiểm tra mode/log cho chip phù hợp |
| REBOOT_DEVICE | {} | restart agent/device theo platform |

LOCK_DEVICE và UNLOCK_DEVICE là hai command được xử lý ngay cả khi remote.lock đang có. Các command khác bị từ chối khi locked.

Command dài dùng agent.lock để chống chạy đồng thời; nếu lock đã tồn tại thì trả last_command_result status=rejected, detail=device is busy.

Ví dụ gửi raw chip command:

~~~json
{
  "command": "EXECUTE_RAW_COMMANDS",
  "command_id": "3f3c8a9d-87e0-4fb1-9a61-1d4a24d6f2ab",
  "payload": {
    "commands_b64": [
      "bW9kZSBzdXJ2ZXk7DQo=",
      "JERFTEFZXzUwJA=="
    ]
  },
  "original_config": {
    "mode": "BASE",
    "params": {}
  }
}
~~~

Trong source, chuỗi bytes sau base64 có thể là marker $DELAY_50$; marker chỉ sleep 50 ms và không ghi vào chip. Những bytes khác được write nguyên dạng.

### 26.5. ServiceConfig chính xác

~~~python
class ServiceConfig(BaseModel):
    ncomport: str | None = None
    reconnectioninterval: int = 10
    stream_on_demand: bool = False
    stream_active: Optional[bool] = None
    server1_stream_on_demand: Optional[bool] = None
    server1_stream_active: Optional[bool] = None
    server2_stream_on_demand: Optional[bool] = None
    server2_stream_active: Optional[bool] = None

    server1_enabled: bool = False
    ntrip_version1: int = 1
    username1: str | None = None
    serverhost1: str | None = None
    port1: int | None = None
    mountpoint1: str | None = None
    password1: str | None = None

    server2_enabled: bool = False
    ntrip_version2: int = 1
    username2: str | None = None
    serverhost2: str | None = None
    port2: int | None = None
    mountpoint2: str | None = None
    password2: str | None = None

    rtcm_enabled: bool = False
    rtcmntripversion1: int = 1
    rtcmserver1: str | None = None
    rtcmport1: int | None = None
    rtcmmountpoint1: str | None = None
    rtcmusername1: str | None = None
    rtcmpassword1: str | None = None
~~~

Tên server1/server2 là RTCM uplink từ agent lên caster. Tên rtcmserver1/rtcmport1/... là correction client từ caster về receiver. Không đổi tên để tránh làm mất config cũ.

---

## 27. NMEA và RTCM raw: giữ forwarding, bỏ semantic decode

### 27.1. NMEA filter và topic

NMEA được filter tối đa 50 message/giây, chỉ forward khi đầu message có GGA/GSA/GSV/GST:

~~~python
def _should_forward_nmea_to_backend(data: bytes) -> bool:
    global nmea_rate_limit_counter, nmea_rate_limit_reset_ts
    now = time.time()
    if now - nmea_rate_limit_reset_ts > 1.0:
        nmea_rate_limit_counter = 0
        nmea_rate_limit_reset_ts = now
    if nmea_rate_limit_counter >= 50:
        return False
    nmea_rate_limit_counter += 1
    try:
        text = data.decode("ascii", errors="ignore")
    except Exception:
        return False
    return any(token in text[:12] for token in ("GGA", "GSA", "GSV", "GST"))
~~~

NMEA publisher source:

~~~python
data_chunk = self.queue.get(timeout=1.0)
should_forward = _should_forward_nmea_to_backend(data_chunk)
mqtt_delivered = False
if self.mqtt_client and self.mqtt_client.is_connected() and should_forward:
    result = self.mqtt_client.publish(
        f"pi/devices/{self.serial_number}/raw_data",
        data_chunk,
        qos=0
    )
    mqtt_delivered = result.rc == mqtt.MQTT_ERR_SUCCESS

if not mqtt_delivered and active_websocket_connection and should_forward:
    ws_message = {
        "type": "nmea_update",
        "payload": data_chunk.decode("ascii", errors="ignore")
    }
    asyncio.run_coroutine_threadsafe(
        active_websocket_connection.send(json.dumps(ws_message)),
        self.loop
    )
~~~

Agent vẫn có thể parse GGA để cập nhật local status/định vị NTRIP. Đây không phải RTCM semantic decode và cần giữ vì NTRIP VRS gửi GGA ngược lên caster.

### 27.2. RTCM framing/CRC được phép giữ

Không decode message type, nhưng được phép tách frame và kiểm CRC để tránh đẩy bytes lỗi. Source framing hiện tại:

~~~python
def is_valid_rtcm3_packet(packet: bytes) -> bool:
    if not packet or len(packet) < 6 or packet[0] != 0xD3:
        return False

    payload_len = ((packet[1] & 0x03) << 8) | packet[2]
    if len(packet) != payload_len + 6:
        return False

    expected_crc = _crc24q(packet[:-3])
    packet_crc = (packet[-3] << 16) | (packet[-2] << 8) | packet[-1]
    return expected_crc == packet_crc

def extract_valid_rtcm3_packets(stream_buffer: bytearray, max_packet_len: int = 2048) -> list[bytes]:
    packets: list[bytes] = []
    while True:
        if len(stream_buffer) < 3:
            return packets
        preamble_idx = stream_buffer.find(0xD3)
        if preamble_idx < 0:
            stream_buffer.clear()
            break
        if preamble_idx > 0:
            del stream_buffer[:preamble_idx]
        if len(stream_buffer) < 3:
            break
        payload_len = ((stream_buffer[1] & 0x03) << 8) | stream_buffer[2]
        packet_len = payload_len + 6
        if packet_len < 6 or packet_len > max_packet_len:
            del stream_buffer[0]
            continue
        if len(stream_buffer) < packet_len:
            break
        candidate = bytes(stream_buffer[:packet_len])
        if is_valid_rtcm3_packet(candidate):
            packets.append(candidate)
            del stream_buffer[:packet_len]
        else:
            del stream_buffer[0]
    return packets
~~~

Đây là framing/CRC, không phải decode. Không đọc message number, satellite, SNR, ephemeris, datum, tọa độ hoặc skyview.

### 27.3. RTCM dispatcher hiện tại và thay đổi bắt buộc

Source hiện tại có dòng semantic decode cần xóa:

~~~python
def dispatch_rtcm_data(data):
    _update_datum_from_rtcm_packet(data)
    ...
~~~

Agent mới phải thay bằng:

~~~python
def dispatch_rtcm_data(raw_packet: bytes):
    if not is_valid_rtcm3_packet(raw_packet):
        counters["crc_invalid"] += 1
        return

    counters["packets_received"] += 1
    counters["bytes_received"] += len(raw_packet)

    # Fanout raw bytes cho NTRIP uplink nếu stream đang active.
    if rtcm_stream_active_flag:
        for subscriber_queue in rtcm_subscribers:
            try:
                subscriber_queue.put_nowait(raw_packet)
            except QueueFull:
                counters["queue_drops"] += 1

    # Đưa raw bytes vào publisher tới backend.
    try:
        rtcm_backend_queue.put_nowait(raw_packet)
    except QueueFull:
        counters["backend_queue_drops"] += 1
~~~

Nếu muốn queue backend và NTRIP dùng cùng packet object, packet vẫn phải là immutable bytes; không sửa nội dung sau khi nhận.

### 27.4. Những thứ bắt buộc không có trong agent mới

Không copy hoặc gọi:

- RTCMSignalDecoder;
- _rtcm_getbitu, _rtcm_getbits, _rtcm_getbits_38;
- _decode_type1021, _decode_type1023, _decode_type1025;
- _update_datum_from_rtcm_packet;
- decoder.get_stats;
- rtcm_skyview_calculated;
- rtcm_signal_update;
- việc map sat system, PRN, CNR, azimuth, elevation từ RTCM.

Nếu backend vẫn cần skyview, backend phải thực hiện ở một service riêng; việc này nằm ngoài agent replacement và không được chặn raw forwarding.

### 27.5. Raw RTCM publish MQTT và WebSocket

MQTT: publish bytes trực tiếp:

~~~python
topic = f"pi/devices/{serial}/raw_data"
result = mqtt_client.publish(topic, raw_packet, qos=0, retain=False)
~~~

WebSocket: encode từng packet hoặc chunk thành base64:

~~~python
envelope = {
    "type": "rtcm_raw",
    "payload": {
        "encoding": "base64",
        "data_b64": base64.b64encode(raw_packet).decode("ascii"),
        "timestamp": round(time.time(), 3)
    }
}
await websocket.send(json.dumps(envelope))
~~~

Backend phải bổ sung handler rtcm_raw. Handler không được json.loads bytes raw như NMEA:

~~~python
if message_type == "rtcm_raw":
    body = data.get("payload") or {}
    if body.get("encoding") != "base64":
        reject("unsupported rtcm encoding")
    raw_packet = base64.b64decode(body.get("data_b64", ""), validate=True)
    if not is_valid_rtcm3_packet(raw_packet):
        reject("invalid rtcm frame")
    await ingest_raw_rtcm(serial, raw_packet)
~~~

Nếu WebSocket raw fallback chưa được backend patch, agent vẫn phải gửi MQTT raw_data và NTRIP; không được phát sinh rtcm_signal_update giả để lấp chỗ.

---

## 28. NTRIP uplink và correction client

### 28.1. NTRIP server/uplink

Config server 1/2 đọc:

~~~python
host = self.config.get(f"serverhost{self.server_id}")
port = int(self.config.get(f"port{self.server_id}"))
mountpoint = self.config.get(f"mountpoint{self.server_id}")
password = self.config.get(f"password{self.server_id}")
username = self.config.get(f"username{self.server_id}") or "source"
version = int(self.config.get(f"ntrip_version{self.server_id}", 1))
~~~

Basic auth và header source:

~~~python
auth_str = f"{user}:{pw or ''}"
auth = base64.b64encode(auth_str.encode('ascii')).decode('ascii')

if version == 2:
    headers = (
        f"POST /{mp} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP GeodeticAgent/4.1\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Transfer-Encoding: chunked\r\n"
        f"Connection: Keep-Alive\r\n\r\n"
    )
else:
    headers = (
        f"SOURCE {pw or ''} /{mp} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP GeodeticAgent/4.1\r\n"
        f"Authorization: Basic {auth}\r\n"
        f"Connection: Keep-Alive\r\n\r\n"
    )

client_socket.sendall(headers.encode('ascii'))
response = client_socket.recv(4096)
if not (b"ICY 200 OK" in response or b"HTTP/1.1 200 OK" in response):
    raise ConnectionError(f"Caster rejected: {response.decode(errors='ignore')}")
~~~

Streaming:

- NTRIP v2: mỗi packet gửi chunk_size hex + CRLF + raw packet + CRLF.
- NTRIP v1: gửi raw packet trực tiếp.
- Khi queue rỗng, v2 gửi 0\r\n\r\n keepalive; v1 gửi CRLF.
- Chỉ gửi packet hợp lệ RTCM3; không gửi NMEA/raw command lên caster.
- Queue max hiện tại 3000 cho từng server.
- Reconnect theo reconnectioninterval.
- stream_active có thể bị SET_RTCM_STREAM_ACTIVE tắt; khi tắt phải dừng push và xả queue phù hợp.

### 28.2. Correction client

Correction client đọc chính xác:

~~~text
rtcm_enabled
rtcmntripversion1
rtcmserver1
rtcmport1
rtcmmountpoint1
rtcmusername1
rtcmpassword1
~~~

Correction flow:

1. GET /mountpoint bằng Basic Authorization.
2. Nhận header ICY 200 OK hoặc HTTP/1.1 200 OK.
3. Gửi GGA hiện tại ngay sau auth nếu có.
4. Gửi GGA mới mỗi 5 giây cho VRS.
5. Nhận stream bytes, tách frame RTCM3 hợp lệ bằng preamble/length/CRC.
6. Đưa raw correction bytes vào queue injection của GNSS reader.

---

## 29. Base mode, serial command và chip I/O

### 29.1. Serial settings

- Windows agent: default 460800 baud.
- Raspberry Pi: default 115200 baud.
- Nếu chip detection tìm baud khác, status phải gửi detected_chip_baud và raw command phải dùng baud runtime đã detect.
- Chỉ một thread được access serial tại một thời điểm; source dùng serial_port_lock.
- Khi EXECUTE_RAW_COMMANDS: pause GNSS reader, mở port, write bytes, flush, chờ/read response, đóng port, chờ chip ổn định, resume reader.

### 29.2. Không gửi command qua backend REST trực tiếp tới chip

Luồng đúng:

~~~text
dashboard user
  → REST FastAPI
  → command_dispatcher
  → MQTT pi/devices/SERIAL/command
     hoặc WebSocket pi manager fallback
  → agent validator
  → serial chip
  → status last_command_result
~~~

### 29.3. Base modes

Agent cần giữ các mode đang có:

- AUTO_CORS: nhận TRIGGER_AUTO_BASE, kết nối NTRIP caster, thu mẫu, lưu base config.
- SURVEY_IN/FIXED_LLA: backend tạo command bytes và gửi EXECUTE_RAW_COMMANDS.
- APPLY_VN2000_PROVINCE: áp province code theo config backend; không tự decode RTCM datum để suy ra tham số.
- STOP_AUTO_BASE: dừng task/client và đưa reader về trạng thái bình thường.

Các command bytes cho chip do backend command_builder tạo. Agent không được tự sửa bytes trừ xử lý delay marker đã nêu.

---

## 30. Startup, state machine và shutdown

### 30.1. Startup bắt buộc

Thứ tự tương thích:

1. Xác định platform và BASE_DIR.
2. Xử lý stale agent.lock; nếu process khác còn chạy thì thoát.
3. Đọc/generate serial và persist device_id.txt.
4. Load agent_config.json; nếu hỏng thử .bak; nếu vẫn hỏng tạo config default.
5. Đọc license.key; chưa valid thì limited mode.
6. Detect GNSS chip/serial port/baud.
7. Khởi động GNSS reader và queues.
8. Tạo MQTT client, đăng ký LWT, connect_async, subscribe command/ACK.
9. Khởi động WebSocket retry task.
10. Khởi động NMEA publisher và raw RTCM publisher.
11. Khởi động NTRIP workers theo service config.
12. Set initialization_complete.
13. Gửi status đầu tiên; status chưa provision là unprovisioned, thiếu license là awaiting_license.
14. Khởi động status publisher, control-plane watchdog, auto-base task và memory guard.

Không gửi status trước initialization_complete nếu status phụ thuộc chip/config chưa sẵn sàng.

### 30.2. State values

Các status chính:

- initializing
- awaiting_license
- unprovisioned
- online
- sleep
- configuring
- auto_setup_* hoặc reference_check_*
- rebooting
- locked
- offline

Frontend có thể phân biệt status state với transport_status. MQTT disconnected không đồng nghĩa device offline nếu WebSocket vẫn có ACK/status.

### 30.3. Shutdown

Khi SIGINT/SIGTERM hoặc reset:

- stop command/auto-base/NTRIP/GNSS workers;
- gửi status offline có status_event=shutdown nếu còn transport;
- disconnect MQTT để LWT không bị nhầm là crash;
- remove agent.lock;
- không xóa license/config trừ DELETE_DEVICE.

---

## 31. JSON test vectors để team ESP32 và backend dùng chung

### 31.1. Provision

~~~json
{
  "command": "PROVISION_DEVICE",
  "command_id": "cmd-provision-001",
  "payload": {
    "name": "ESP32 Station 01"
  }
}
~~~

### 31.2. License deployment

~~~json
{
  "command": "DEPLOY_LICENSE",
  "payload": {
    "license_key": "123456"
  }
}
~~~

Agent phải ghi chính xác chuỗi 123456, không thêm quote vào file và không hash trước khi ghi. Đây chỉ là test token; không dùng trong production.

### 31.3. Service config

~~~json
{
  "command": "DEPLOY_SERVICE_CONFIG",
  "payload": {
    "ncomport": "COM3",
    "reconnectioninterval": 10,
    "stream_on_demand": false,
    "server1_enabled": true,
    "ntrip_version1": 2,
    "username1": "source",
    "serverhost1": "caster.example.com",
    "port1": 2101,
    "mountpoint1": "BASE01",
    "password1": "secret",
    "rtcm_enabled": false
  }
}
~~~

### 31.4. Stream active

~~~json
{
  "command": "SET_RTCM_STREAM_ACTIVE",
  "payload": {
    "active": true,
    "server_id": 1,
    "mountpoint": "BASE01",
    "address": "caster.example.com"
  }
}
~~~

### 31.5. Status MQTT

~~~json
{
  "serial": "LP_001122AABBCC",
  "name": "Station A",
  "status": "online",
  "version": "V2.2.4",
  "agent_token_fingerprint": "8d969eef6ecad3c2",
  "timestamp": 1710000000.123,
  "agent_timestamp": 1710000000.123,
  "status_boot_id": "0123456789abcdef0123456789abcdef",
  "status_sequence": 7,
  "transport_status": {
    "mqtt_connected": true,
    "websocket_connected": true,
    "active_channel": "websocket"
  },
  "last_command_result": {}
}
~~~

MQTT payload là object JSON trên; WebSocket payload là:

~~~json
{
  "type": "status_update",
  "payload": {
    "serial": "LP_001122AABBCC",
    "status": "online",
    "status_boot_id": "0123456789abcdef0123456789abcdef",
    "status_sequence": 7
  }
}
~~~

### 31.6. Control ACK

~~~json
{
  "type": "control_plane_ack",
  "channel": "mqtt",
  "serial": "LP_001122AABBCC",
  "status_boot_id": "0123456789abcdef0123456789abcdef",
  "status_sequence": 7,
  "server_timestamp": 1710000000.456
}
~~~

### 31.7. NMEA raw_data

MQTT payload là bytes ASCII:

~~~text
$GNGGA,123519.00,1042.1234,N,10641.5678,E,4,20,0.8,12.3,M,0.0,M,,*hh
~~~

WebSocket fallback:

~~~json
{
  "type": "nmea_update",
  "payload": "$GNGGA,123519.00,1042.1234,N,10641.5678,E,4,20,0.8,12.3,M,0.0,M,,*hh"
}
~~~

### 31.8. RTCM raw_data và WebSocket

MQTT gửi raw bytes bắt đầu bằng 0xD3. WebSocket:

~~~json
{
  "type": "rtcm_raw",
  "payload": {
    "encoding": "base64",
    "data_b64": "REPLACE_WITH_BASE64_OF_FULL_RTCM_FRAME",
    "timestamp": 1710000000.789
  }
}
~~~

Không gửi object rtcm_skyview_calculated hoặc rtcm_signal_update trong test của agent mới.

---

## 32. Checklist tương thích dành cho team ESP32

### 32.1. Auth

- [ ] Serial ổn định sau reboot.
- [ ] License key đọc/ghi nguyên dạng text.
- [ ] License algorithm khớp exact source.
- [ ] Fingerprint khớp SHA-256 UTF-8, lowercase, 16 ký tự đầu.
- [ ] WebSocket query token là raw license URL-encoded.
- [ ] MQTT username/password chỉ dùng CONNECT.
- [ ] Không đưa MQTT password vào JSON.

### 32.2. Transport

- [ ] MQTT broker 45.117.179.134:1883 configurable.
- [ ] Backend host aitogy.click:8000 configurable.
- [ ] Subscribe đủ 3 command topic và 1 ACK topic.
- [ ] Status QoS 1 retain true.
- [ ] raw_data QoS 0 retain false.
- [ ] LWT status offline QoS 1 retain true.
- [ ] MQTT reconnect 5–120 giây.
- [ ] WebSocket ping 30, timeout 60, reconnect 10 giây.
- [ ] MQTT raw/NMEA là primary; WS là fallback, không duplicate nếu MQTT publish thành công.

### 32.3. Command/status

- [ ] Parse JSON object UTF-8.
- [ ] Generate command_id nếu thiếu.
- [ ] Validate allowlist và payload trước khi ghi serial.
- [ ] Gửi last_command_result trong status.
- [ ] status_boot_id mới mỗi boot.
- [ ] status_sequence tăng đơn điệu trong một boot.
- [ ] ACK đúng channel/boot/sequence.
- [ ] Fail-open chỉ khi MQTT và WebSocket cùng stale.

### 32.4. RTCM/NTRIP

- [ ] Có framing preamble/length và CRC24Q.
- [ ] Không có semantic decoder.
- [ ] Không tạo skyview từ RTCM.
- [ ] Raw packet vẫn tới NTRIP server.
- [ ] Raw packet MQTT tới raw_data.
- [ ] Raw packet WebSocket dùng base64 nếu fallback.
- [ ] Correction client inject raw RTCM vào GNSS reader.
- [ ] NTRIP v1/v2 header và keepalive khớp source.

### 32.5. Reliability

- [ ] Queue có giới hạn và counter drop.
- [ ] Serial không bị hai worker ghi đồng thời.
- [ ] Config atomic + backup.
- [ ] Agent lock và remote lock.
- [ ] Không retry busy-loop.
- [ ] Shutdown sạch và remove agent.lock.

---

## 33. Những thay đổi backend cần làm cùng agent mới

1. Thêm handler WebSocket message type rtcm_raw với base64 decode và raw ingest.
2. Không yêu cầu rtcm_skyview_calculated để đánh dấu agent online.
3. Cho phép status rtcm_stats chỉ có counters, không bắt buộc decoder-derived fields.
4. Giữ handler MQTT raw_data cho payload binary bắt đầu 0xD3.
5. Giữ control_plane_ack như hiện tại, không ACK retained status.
6. Nếu dùng ESP32 serial mới, thêm device token/serial vào metadata trước khi mở WebSocket.
7. Nếu đổi credential, cập nhật broker ACL và config agent đồng thời.
8. Cập nhật dashboard để không hiển thị skyview RTCM nếu không còn service decode; nếu cần skyview thì lấy từ backend decoder riêng.

---

## 34. Tiêu chí nghiệm thu code thật

Team chỉ bàn giao khi có:

- một binary/script chạy được trên Windows và Linux hoặc firmware ESP32 theo target đã thống nhất;
- config.example không chứa secret thật;
- unit test license generation, fingerprint, JSON serialization, validator, CRC framing;
- integration test MQTT command/status/LWT;
- integration test WebSocket auth, command fallback và rtcm_raw;
- test broker down → WebSocket command;
- test MQTT raw publish fail → WebSocket NMEA/raw fallback;
- test cả hai kênh stale → fail-open, một kênh hồi phục → recovery;
- test reboot không đổi serial;
- test lock chặn command;
- test NTRIP v1/v2 và reconnect;
- chứng minh agent không import, instantiate hoặc gọi bất kỳ RTCM semantic decoder nào;
- log/status có counters raw RTCM nhưng không có dữ liệu vệ tinh suy ra từ RTCM.

Kết quả nghiệm thu phải đính kèm packet capture hoặc log cho các topic:

~~~text
pi/devices/SERIAL/status
pi/devices/SERIAL/raw_data
pi/devices/SERIAL/command
pi/devices/SERIAL/control_ack
~~~

---

## 35. Kết luận triển khai

Phần khó nhất để giữ compatibility không phải thuật toán chip mà là bốn contract sau:

1. Serial ổn định và license key exact.
2. MQTT topic/QoS/retain/LWT exact.
3. WebSocket token, frame type và fallback exact.
4. Command/status/ACK JSON exact.

RTCM chỉ cần framing + CRC + forward raw bytes. Agent mới tuyệt đối không decode RTCM. Mọi semantic decode, skyview hoặc datum extraction nếu còn cần phải tách sang backend/service riêng để không làm thay đổi data path và không làm mất raw stream.

---

## 36. Tài liệu/code cần đối chiếu trong repo

- `geodetic/agent_universal.py`
- `geodetic/api_pi_backend.md` — tài liệu cũ, cần ưu tiên đối chiếu với code backend khi có khác biệt
- `cors_dashboard/backend/app/mqtt.py`
- `cors_dashboard/backend/app/pi_websocket.py`
- `cors_dashboard/backend/app/command_dispatcher.py`
- `cors_dashboard/backend/app/control_plane_ack.py`
- `cors_dashboard/backend/app/status_reconciler.py`
- `cors_dashboard/backend/app/routers/devices.py`
- `cors_dashboard/backend/app/routers/users.py`
- `cors_dashboard/backend/app/schemas.py`
- `cors_dashboard/backend/app/license_manager.py`
