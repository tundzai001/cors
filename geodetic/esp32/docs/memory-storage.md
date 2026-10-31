# Bộ nhớ ESP32 — 31/10/2026

## Thay đổi

Firmware bỏ AP cấu hình, HTTP server, trang HTML, quét mạng và task phục vụ
portal. Chỉ một SSID/mật khẩu kết nối được giữ trong cấu hình đang dùng.
Khi nâng firmware, danh sách cũ chỉ dùng cho bước chuyển đổi: thử lần lượt
để chọn mạng kết nối được, ghi và đọc lại thông tin mạng đó, sau đó mới xóa
`wifi_config/profiles` và `wifi_config/ap_pass`. Nếu chưa kết nối được hoặc
chưa lưu được thì giữ dữ liệu cũ để lần sau thử lại. Xem [Wi-Fi](wifi-config.md).

Các khoản RAM bỏ đi:

- Stack portal: 8.192 byte cùng tài nguyên task/HTTP server/AP.
- Hàng đợi raw RTCM: 4 × 1.032 byte payload khi `BACKEND_PUBLISH_RAW_RTCM`
  tắt. Hàng đợi NMEA và buffer UART giữ nguyên để bảo toàn luồng dữ liệu.
- Không tạo đối tượng WebSocket khi tính năng tắt. Khi bật, gói status mượn
  JSON đã serialize thay cho hai document tạm 8.704 và 8.192 byte; document
  bọc chỉ còn 256 byte.
- Bộ đệm WebSocket được giải phóng khi ngắt kết nối và khi xử lý xong một
  batch lớn hơn 4 KB. Giới hạn dung lượng được kiểm tra trước khi chèn byte.
- Khi xóa lịch sử Auto Base, giải phóng buffer String cũ thay vì chỉ đặt độ
  dài về 0. Không giảm stack các task GNSS/mạng khi chưa có đo đạc trên máy.

## Số liệu build

Cùng cấu hình `tdm240x_dev_general`, trước/sau khi bỏ portal và thêm quản lý
bộ nhớ:

| Chỉ số | Trước | Sau |
| --- | ---: | ---: |
| Firmware | 1.092.881 byte (83,4%) | 1.036.805 byte (79,1%) |
| RAM tĩnh | 49.860 byte | 49.396 byte |

Giảm 56.076 byte firmware và 464 byte RAM tĩnh. Những vùng cấp phát động ở
trên không được tính trong số RAM tĩnh của linker. Chưa nạp phần cứng để đo
heap hoặc mức sử dụng stack thực tế; không cộng các khoản lý thuyết thành một
con số RAM thực tế đã tiết kiệm.

Sau đợt tách trách nhiệm NVS/nhận diện chip/đổi mạng, build general là
1.037.849 byte firmware (79,2%) và 49.388 byte RAM tĩnh. Việc chia đơn vị biên
dịch có thể thay đổi kích thước mã máy; phần dọn cấu trúc này không được tính
là một khoản tiết kiệm flash mới.

## System Monitor trên web

Mục **Bộ nhớ ESP32** hiển thị:

- Heap trống hiện tại, mức trống thấp nhất từ khi bật và khối trống lớn nhất.
- Dung lượng flash vật lý và firmware/vùng ứng dụng đang chạy.
- Dung lượng hai phân vùng NVS; số ô đã dùng, còn trống và tổng ô.
- Khả năng dọn từng nhóm dữ liệu, trạng thái yêu cầu và ACK của thiết bị.

NVS dùng các ô lưu trữ với overhead riêng. Số ô không phải dung lượng byte của
các giá trị. Xóa dữ liệu cho phép NVS tái sử dụng ô; không thu nhỏ phân vùng
flash và không biến flash thành RAM. Vùng OTA/dữ liệu cũng không phải khoảng
trống để firmware đang chạy tùy ý sử dụng.

Chỉ quản trị viên có quyền gửi hai lệnh qua endpoint command hiện có:

- `CHECK_STORAGE_STATUS`: yêu cầu trạng thái mới.
- `CLEAR_NVS_DATA`: payload `group` và `confirm` phải trùng nhau và nằm trong
  danh sách cố định. `unused_wifi` chỉ xóa hai key portal cũ; `auto_base_history`
  chỉ xóa kết quả Auto Base đã lưu. Bản ghi lệnh cuối cùng vẫn được giữ để ACK.

Luồng xóa giữ nguyên license, serial/tên trạm, khóa, mạng đang dùng,
cấu hình MQTT/NTRIP, cấu hình Base và kết quả Survey-In. Không cung cấp lệnh
xóa toàn bộ NVS/phân vùng hoặc namespace tùy ý. Firmware từ chối dọn trong
workflow GNSS hoặc Survey-In chưa hoàn tất. Firmware cũ, thiết bị ngoại tuyến
hoặc bị khóa không có nút thao tác khả dụng. Web chỉ báo hoàn tất khi nhận
đúng command ID từ thiết bị; hết thời gian chờ không được coi là xóa thành công.

## Kiểm tra

Ba cấu hình `tdm240x_dev_general`, `tdm240x_dev_general_debug` và
`tdm240x_dev_general_test` build thành công. Các kiểm tra GNSS/CORS, GGA,
chuyển mạng, ACK vị trí, VN2000, bảo mật và chuyển đổi dữ liệu đều qua.

Đã kiểm tra giao diện desktop/390 px, xác nhận và hủy xóa, trạng thái chờ,
ACK, firmware cũ, thiết bị ngoại tuyến và không tràn ngang. Số liệu trong
kiểm tra giao diện là fixture, không phải telemetry phần cứng.

Chưa triển khai backend/frontend hoặc flash ESP32 trong lần làm này. Chỉ
firmware mới mới gửi các trường bộ nhớ và xử lý hai lệnh trên.
