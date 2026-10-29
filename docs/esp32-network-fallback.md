# ESP32 V2.3.5: SIM và Wi-Fi dự phòng

Nếu lựa chọn đã lưu là 4G, firmware thử SIM trước. Khi khởi động, mỗi lượt kết nối GSM chỉ thử một lần trước khi chuyển sang thử Wi-Fi đã lưu nếu không đăng ký được GPRS.

GPRS đã kết nối không chứng minh truy cập được dịch vụ. Khi cả MQTT và NTRIP không kết nối được liên tục ít nhất 30 giây, firmware thử Wi-Fi. Kiểm tra này chạy trước/sau lượt kết nối MQTT trong chính task MQTT, cùng với vòng quản lý mạng, để không bị bỏ lỡ do các lượt reconnect đang giữ mutex modem. Thời gian timeout của các lệnh modem có thể làm việc chuyển mạng muộn hơn mốc 30 giây.

Wi-Fi phải kết nối thành công và mở được TCP tới MQTT broker đã cấu hình trong tối đa 3 giây trước khi được chọn. Nếu Wi-Fi chưa dùng được, giữ SIM và thử lại có khoảng nghỉ. Không dùng socket probe SIM mux 4 hay IP Internet công cộng để quyết định fallback. Thiết bị không khởi động lại chỉ vì mất GPRS hoặc caster từ chối mountpoint.

Khi chuyển, đóng socket MQTT/NTRIP trên SIM trước khi đổi transport, cấu hình lại MQTT/NTRIP và WebSocket trên Wi-Fi. Dùng chung mutex của modem để tránh lẫn phản hồi AT.

Fallback không sửa `CONNECTION_TYPE` trong NVS. Thiết bị tiếp tục dùng Wi-Fi dự phòng khi Wi-Fi ổn định; lần khởi động sau lại đọc lựa chọn SIM đã lưu. Chưa có chức năng chủ động dò SIM để chuyển ngược trong lúc Wi-Fi đang chạy tốt.

Gói MQTT status khoảng 5 KB được chia thành các lần ghi modem tối đa 1024 byte; giữ nguyên một gói MQTT. Khi modem ghi thiếu, đóng stream TCP để vòng MQTT kết nối lại, tránh gửi gói mới vào giữa gói cũ. Bộ đệm MQTT vẫn 16384 byte và có log nếu cấp phát thất bại.

## Kiểm tra

```sh
python3 geodetic/esp32/test/host/check_network_fallback.py
python3 geodetic/esp32/test/host/check_base_startup.py
c++ -std=c++17 -Wall -Wextra -Werror -I geodetic/esp32/include geodetic/esp32/test/host/mqtt_write_checks.cpp -o /private/tmp/mqtt-write-checks
/private/tmp/mqtt-write-checks
platformio run -e tdm240x_dev_general
```

Các kiểm tra host giả lập I/O; chưa thay thế thử nghiệm rút SIM/mất Internet trên thiết bị thật. Chưa tự nạp firmware lên ESP trong lần sửa này.

Lỗi `ERROR - Mountpoint already in using` vẫn được caster trả khi nguồn đang được đánh dấu sử dụng. Firmware không bỏ qua xác thực hoặc tự ngắt nguồn khác để chiếm mountpoint.

## IntelliSense cho bài kiểm tra MQTT trên máy tính

Chạy `platformio run -e tdm240x_dev_general -t compiledb` từ thư mục repo. Hook trong `scripts/platformio_host_compiledb.py` bổ sung `mqtt_write_checks.cpp` với compiler C++ của máy tính và include của firmware. PlatformIO tiếp tục tạo các mục firmware bằng toolchain ESP32. `compile_commands.json` là file được sinh tại máy và không commit.
