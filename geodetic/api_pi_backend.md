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


### u-blox satellite angles without RTCM ephemeris

Pi and ESP32 now poll UBX-NAV-SAT (01/35) with an empty payload every 30 seconds
through the existing serial owner. This reads angles already calculated by the
receiver; it does not require RTCM 1019/1020 or enable periodic UBX output.
Frames must pass UBX checksum, version 1, exact `8 + 12*numSvs` length and a
128-satellite cap. The normal ESP32 UBX reader stays at 256 bytes; only the
NAV-SAT query allocates a temporary larger reply buffer.

`gnss_satellites` contains `source: ubx_nav_sat`, `payload_b64` (validated UBX
payload, no frame header/checksum) and `age_seconds`, or null after 45 seconds.
Base64 keeps the ESP32 snapshot compact instead of expanding 128 JSON objects;
it is transient RAM state and is never written to NVS. Its JSON pool allowance
is allocated only while the snapshot exists.

The dashboard satellite count, C/N0, sky view and signal bars use only fresh
RTCM observations with positive decoded C/N0, deduplicated by constellation/SV.
NMEA and NAV-SAT never add satellites to these displays. Fresh NAV-SAT can
provide elevation/azimuth for an existing RTCM observation matched by normalized
constellation + PRN; RTCM C/N0 and observation time remain unchanged. When RTCM is empty
or expired the count is zero. Unknown angles are not plotted; the chart explains
when observations exist without geometry. A missing, invalid or expired NAV-SAT match leaves the RTCM observation
without extra geometry.


The UI keeps the complete unsigned NAV-DOP range (0–655.35) in its readback
rather than applying the NMEA fallback threshold to UBX values. A fresh
NAV-PVT fixType=5 is explicitly labelled Time-only: position DOP does not
independently assess the fixed Base coordinates. The original NAV-DOP pair
remains visible so a reported placeholder is distinguishable from a missing
reply. This interpretation must not be inferred from saved FIXED_LLA alone.
Technical UBX identifiers are excluded from weekday translation, including
NAV-SAT and MON-VER.


### ZED-F9P RF diagnostics

Universal Pi agents poll read-only UBX-MON-RF (0A/38) and UBX-MON-SPAN (0A/31)
alongside the existing 30-second navigation readback. A failure in one query
does not prevent the remaining queries. No CFG command, NVS write or receiver
firmware update is performed. ESP32 retains MON-VER only. This requires updating the station agent
application as well as the web application; updating the web alone cannot
create RF telemetry from an older agent.

Status carries `gnss_rf` and `gnss_spectrum` independently as
`{source, payload_b64, age_seconds}`, or null when older than 45 seconds.
Version-0 packets are bounded to four RF paths: 4+24*n and 4+272*n bytes.
The receiver UART owner validates the UBX checksum before delivery. Per-path
noise and AGC are raw receiver counts; CW suppression is scaled 0..255.
Jamming state 0 is unknown/disabled, never a clean-RF result. MON-RF's legacy
jamming flag may be deprecated on newer protocols supporting SEC-SIG v2;
modern firmware instead uses SEC-SIG v2 as documented below.
MON-SPAN bins are retained as raw 0..255 values, never labeled as calibrated
dBm. The current interface reference describes dB while the newer integration
manual specifies 0.25-dB increments; the UI does not guess a conversion without
a firmware-specific scale. Frequency
is center + span*(bin-127)/256. PGA is displayed separately and a changed PGA
is flagged when comparing a saved reference.

The web panel shows current source/age and clears expired readings. The reference
is local to the open station panel and is cleared on station change or reload.
Download captures serial, capture/receive times, current decoded readings and the
reference; it is a snapshot, not continuous logging. Thirty-second polling can
miss short interference events. Empty/unavailable RF data is never presented as
absence of interference. These readings cannot identify a BTS or prove absolute
Base/rover accuracy; compare positions, datum/height convention, antenna setup,
rover behavior and alternate correction sources independently.

Reference: u-blox F9 HPG 1.32 Interface Description, UBX-22008968, sections
3.14.9 and 3.14.12 (MON-RF and MON-SPAN).


### Unicore N4 RF diagnostics

UM980/UM982 use AGCA and FREQJAMSTATUSA; JAMSTATUSA is an overall CW fallback
if the per-frequency query is unavailable. Queries omit port and rate, using
the N4 default single output at the current port; no persistent 1-Hz log,
CONFIG ANTIJAM, UNLOG or SAVECONFIG is issued. Firmware that rejects a query
leaves that reading unavailable. Unicore's CW logs document 1-Hz periodic
output; the single-output query path still requires confirmation on the actual
receiver firmware. Do not automatically change existing output rates to obtain it.

Both Pi and ESP32 validate the ASCII CRC32 and exact field counts. AGCA has ten
signed values (-1..255), of which indices 0/1/2 are ANT1 L1/L2/L5 and 5/6/7 are
ANT2 L1/L2/L5; other slots are reserved. -1 is invalid, never a low AGC measurement.
CW ratios use 0..255 and flags 0=no CW, 1=CW, 2=strong CW. The per-frequency log
has three ratio/flag pairs; the overall fallback has one, never duplicated into
three bands. These are not u-blox's AGC scale or MON-RF jamming states.

`gnss_rf` with source `unicore_rf` carries independent `agc` and `jamming`
readings with age_seconds; stale sub-readings become null independently after
45 seconds. Both paths support the same reference/export controls as u-blox.
No calibrated spectrum, noise-floor measurement or BTS identification is inferred
from AGC. The reference remains local, and telemetry polling is not continuous RF logging.

Reference: Unicore N4 Reference Commands Manual V2 EN R1.4, output syntax section
and sections 7.3.54 JAMSTATUS, 7.3.55 FREQJAMSTATUS, 7.3.58 AGC:
https://en.unicore.com/uploads/file/20241219/Unicore_Reference_Commands_Manual_For_N4_High_Precision_Products_V2_EN_R1.4.pdf


RF interpretation and read errors:
- The summary follows fresh receiver flags only. MON-RF 0 remains unknown;
  1 means no significant interference reported at that reading, 2/3 report
  interference. Antenna open/short flags are reported separately. UM CW flags
  do not classify broadband RF noise. Spectrum shape, C/N0 and raw AGC never
  independently produce a clean/noisy verdict.
- MON-SPAN-only data explicitly reports insufficient evidence even with visible
  peaks. Charts identify L1 and L2/L5 and label the 0..255 amplitude as raw.
- Failed u-blox MON-RF queries are represented by source=ubx_mon_rf,
  state=read_error, error_kind (timeout / invalid_response / query_failed), error
  and age_seconds. If an older successful reading is still fresh, failure is in
  last_error instead. The Pi includes up to 128 bytes of the rejected UBX frame
  as frame_b64; native errors contain version/count/length or offending AGC.
  A successful read clears the failure, and errors expire after 45 seconds.
- Download includes raw_rf/raw_spectrum alongside interpreted readings, summary
  and read_error so a parser failure is not hidden by its own decoded export.
  Raw details are collapsed by default; next-step guidance is visible first.


Legacy ZED-F9P MON-RF compatibility: HPG 1.30 release notes (UBX-21047459)
explicitly fix earlier firmware returning blockId=0 in every repeated block.
Neither agent nor UI requires unique RF block IDs. All blocks are retained in
wire order. Duplicate IDs are labeled RF 1/2/... with their reported ID, rather
than guessing a band or merging records. AGC is retained over its full U2 wire
range; values above the documented 8191 are marked unusable for assessment,
without losing the remaining RF flags. Packet CRC, version, exact length and
bounded block count remain mandatory.
https://content.u-blox.com/sites/default/files/ZED-F9P-FW100-HPG130_RN_UBX-21047459.pdf

### Receiver firmware and interference enable readback

Pi and ESP32 publish `gnss_receiver` (45-second TTL): `source=ubx_mon_ver`, bounded MON-VER `payload_b64`, `age_seconds`, nullable `itfm_enabled`, and independent `error` / `itfm_error` on failed reads. MON-VER contains 30-byte software, 10-byte hardware and 30-byte extensions (FWVER/PROTVER/MOD). The UI shows receiver firmware, not agent version, and includes raw data in diagnostic downloads.

ITFM is read from running RAM using CFG-VALGET key `0x1041000d`; legacy CFG-ITFM poll is a fallback (enable bit 31). No VALSET, persistence, antenna power change or receiver reset is performed. A failed query is unknown, never disabled. For MON-RF state 0 the UI distinguishes confirmed disabled, confirmed enabled-but-unknown and unread configuration. Firmware before HPG 1.30 is annotated for the documented duplicate RF block ID issue.

References: [MON-VER / CFG-ITFM interface](https://content.u-blox.com/sites/default/files/documents/u-blox-F9-HPG-1.32_InterfaceDescription_UBX-22008968.pdf), [HPG 1.30 release note](https://content.u-blox.com/sites/default/files/ZED-F9P-FW100-HPG130_RN_UBX-21047459.pdf).

### Persisted u-blox interference setting

`base_config.gnss_options.interference_detection_enabled` is a boolean, default true when omitted; explicit false is preserved. The u-blox BASE form applies it only through the existing Apply BASE action. Fixed LLA and Survey-in builders append a separate VALSET of `CFG-ITFM-ENABLE` (`0x1041000d`) with RAM/BBR/Flash layers `0x07`, before the existing save command. This key is not a per-port output key and is excluded from output verification payloads. UM commands are unchanged. The diagnostics compare saved intent against fresh `gnss_receiver.itfm_enabled`; matching runtime readback does not prove persistence after power loss. No automatic fleet changes or firmware flashing occurs.

The diagnostics UI displays the complete decoded MON-VER by default: software/hardware, ROM BASE when supplied, and every extension in reply order, including unknown or empty extensions. RF1/RF2 are block ordinals when IDs are duplicated, not antenna or port numbers; no band is inferred for those ambiguous records.

### Automatic RF monitoring on universal Pi agents

The agent emits `gnss_rf_monitor` automatically after existing RF readback cycles; no browser or start command is needed. It keeps up to 120 distinct samples in RAM over a rolling ten-minute window. Agent restart resets the live ten-minute window; the 24-hour archive described below is retained. RF polling frequency and receiver configuration are unchanged. Samples less than five seconds apart are deduplicated; unknown, disabled, failed and expired RF readings do not count as clear samples. Warning episodes are separated by a clear sample or a gap longer than 45 seconds. Counts describe sampled observations, not continuous interference duration.

u-blox NAV-SAT samples within ten seconds of RF are compared against the preceding sample within 45 seconds using matching GNSS/SV identifiers and code-locked signals. A median C/N0 decrease of at least 3 dB-Hz across at least four matching satellites is counted when RF also warns. This is an observation threshold, not a calibrated interference or accuracy limit. Sustained interference may produce no new drop. UM monitoring counts fresh JAMSTATUS/FREQJAMSTATUS flags; no NAV-SAT comparison is inferred for UM. Neither result identifies a BTS or proves rover position error.

The server only forwards this report. ESP32 does not collect RF, spectrum, UM AGC/jamming or ITFM diagnostic readbacks; it retains full MON-VER, ordinary navigation and configuration verification. Explicit null RF/spectrum/report fields clear obsolete UI data.

The RF monitor report also includes `observed_seconds` (elapsed tracking window, capped at 600) and up to 120 `samples` tuples `[seconds_before_latest_sample, severity, cno_delta_dbhz_or_null]`. Severity is 0/1/2 for no warning/warning/strong warning. The UI plots only received samples with their actual time gaps; it does not interpolate coverage. A full time window does not imply continuous measurement. Older reports without tuples still show summary counts and elapsed time without fabricated history.

### RF archive and C/N0 spread

Pi agents keep raw monitor observations in `gnss_rf_history.sqlite3` under the existing agent data directory. SQLite transactions retain the most recent 24 hours, capped at 20,000 observations; hourly aggregates (at most 25 partial/full hours) are included in `history_24h`. History survives agent restart; it is collected from installation of this feature onward, not reconstructed for earlier nights. Failed storage is reported separately and does not stop live RF monitoring. Browsers only display these edge-generated aggregates.

Sample tuples may contain seven fields: `[seconds_before_latest_sample, severity, median_delta, matched_count, changed_count, minimum_delta, maximum_delta]`. Three-field older samples remain supported. NAV-SAT iTOW must change before a C/N0 delta is computed. A zero median does not imply every satellite is unchanged.

`block_ranges` summarizes each u-blox RF block in receiver order over the live ten-minute window: `[ordinal, block_id, samples, warning_samples, critical_samples, noise_min, noise_max, agc_min, agc_max, jam_min, jam_max]`. Duplicate IDs remain separate. These are raw receiver counters, not calibrated power or source identification. A window containing only warnings has no clear reference; consecutive C/N0 comparisons cannot detect degradation already present before monitoring started. Block ranges reset with the live window and are not part of the hourly archive.


### Firmware-aware u-blox BASE commands

`system_info.gnss_valset_supported=true` identifies the updated universal agent; agent release numbers do not identify receiver firmware. Before configuring Fixed LLA/Survey-in, both configure routes select the receiver profile from fresh agent-reported `gnss_receiver` MON-VER (including its raw extensions), not browser input. Supported ZED-F9P HPG profiles: 1.13, 1.30, 1.32, 1.50 and 1.51. Missing/expired/unknown firmware is rejected before saving the desired configuration or dispatching setters.

Updated Pi agents receive typed CFG-TMODE VALSET entries plus output keys, all targeting RAM/BBR/Flash. They re-read MON-VER immediately before setters to reject receiver swaps/firmware changes and compare written keys in all three layers before acknowledging success. No CFG-CFG or CFG-MSG fallback is appended on this path. Unselected RTCM output protocol gates are disabled to avoid different MSM configurations on multiple emitting ports. Original coordinate quantization and signed HP precision are preserved.

For HPG 1.50/1.51 the removed ITFM keys are never emitted/polled; the saved preference is preserved for older receivers but is inapplicable on those firmwares. The RF diagnostic polls SEC-SIG v2 and SEC-SIGLOG v1 on these firmware profiles. Unknown or unsupported response versions remain unavailable; deprecated MON-RF state zero is never treated as clean. Fixed verification uses VALGET, including UART baud readback. Existing agents without the new capability retain their legacy 1.13/1.30/1.32 path; 1.50/1.51 commands require an updated universal agent. ESP32 firmware and UM commands are unchanged.

Roll out the Pi agent first, then the backend/web. This does not upgrade receiver firmware, automatically reconfigure stations, or reset stored BASE settings. Rollback is code-only; old desired configuration remains valid. Flash persistence across actual power loss still requires a connected test receiver.


### HPG 1.51 automatic signal diagnostics

For HPG 1.50/1.51, `gnss_options.jamming_sensitivity_high` selects `CFG-SEC-JAMDET_SENSITIVITY_HI` (`0x10f60051`): boolean true (default, u-blox high sensitivity) or false (normal sensitivity; not detection disabled). It is persisted with BASE Fixed LLA/Survey-in VALSET to RAM/BBR/Flash, and checked by the existing write/readback path. Older firmware and UM never receive this key. Changing the form alone sends no command. The separate running RAM readback appears as `gnss_receiver.jamming_sensitivity_high`; missing replies are explicit `sensitivity_error`, never inferred from the saved setting. The key stays outside per-port message verification.

Stream-only `MONITOR_*` stations do not expose chip RF diagnostic panels. RTCM satellite identity must be preserved when a status snapshot wraps decoded satellites in `nmea_data`; it remains eligible for Signal Strength without MON-RF/NAV-SAT.

Universal agents poll SEC-SIG (`27 09`, version 2) and SEC-SIGLOG (`27 10`, version 1) after fresh MON-VER identifies HPG 1.50/1.51. Polling is read-only and works with RTCM-only output; it does not change detection sensitivity, antenna power, message rates, or stored BASE configuration. Existing firmware keeps MON-RF monitoring and UM keeps its own AGC/jamming path. ESP32 is unchanged.

`gnss_security` carries the bounded raw payload plus jamming/spoofing enabled flags, their independent states and per-center-frequency jamming flags (frequency in kHz). `gnss_security_log` carries up to 16 chip events with elapsed seconds, detection type and event type. Both expire after 45 seconds. A failed poll replaces the corresponding snapshot with an explicit error. The chip log resets on receiver restart; it is not a persistent full-night log.

The edge monitor prefers SEC-SIG on modern firmware, never MON-RF fallback. Warning samples include chip-indicated jamming or spoofing; affirmed spoofing maps to the strongest severity. Unknown/disabled detection without another positive warning is not counted as clear. Per-period `jamming_samples` and `spoofing_samples` expose the distinction. The generic 24-hour archive counts the combined severity, not independent spoof/jamming event totals.

The archive also retains earlier MON-RF samples when a firmware upgrade switches diagnostics to SEC-SIG. The 24-hour expiry and 20,000-row cap apply across sources.

Updated reports use `window_mode="fixed"`, `window_started_at` and `window_ends_at`. Periods last 600 seconds, anchored to the first accepted sample. A subsequent accepted sample starts the next period (skipping empty periods after gaps), resets the live counts and timeline, and leaves the 24-hour archive intact. UI progress follows the period clock while data is fresh; it does not generate samples or restart a period before Pi reports it. Older agents retain their rolling-window presentation.


### Independent satellite diagnostics

The reception panel reads C/N₀, tracking and `svUsed` only from fresh UBX-NAV-SAT. The sky plot and signal bars use only fresh decoded RTCM observations (7-second window). NAV-SAT joins by normalized constellation/PRN to supply angles only; it must not add RTCM members, overwrite RTCM C/N₀, refresh RTCM timestamps, or mutate either input store. The source comparison table shows each source separately, including missing entries, and does not imply the snapshots share a navigation epoch. A receiver-used count is not an RTCM-exported count.
