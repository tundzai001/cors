# ESP32 V2.3.5: SIM và Wi-Fi dự phòng

Nếu lựa chọn đã lưu là 4G, firmware thử SIM trước. Khi khởi động, mỗi lượt kết nối GSM chỉ thử một lần trước khi chuyển sang thử Wi-Fi đã lưu. Khi đang chạy, mất GPRS sẽ kích hoạt thử Wi-Fi; nếu GPRS còn nhưng cả MQTT và NTRIP đều mất kết nối, firmware kiểm tra đường TCP Internet mỗi 15 giây và thử Wi-Fi sau hai lần kiểm tra thất bại. Broker/caster lỗi trong khi Internet vẫn đi được không tự ép chuyển mạng.

Kiểm tra Internet dùng kết nối TCP tới `1.1.1.1:443`, giới hạn 3 giây. Đây là phép kiểm tra đường đi cụ thể; một mạng chặn riêng đích này có thể không vượt qua phép kiểm tra dù truy cập được đích khác. Wi-Fi phải kết nối thành công và vượt qua kiểm tra trước khi được chọn. Nếu Wi-Fi chưa dùng được, giữ SIM để tiếp tục thử; không khởi động lại ESP chỉ vì mất GPRS hoặc caster từ chối mountpoint.

Khi chuyển, đóng socket MQTT/NTRIP trên SIM trước khi đổi transport, cấu hình lại MQTT/NTRIP và WebSocket trên Wi-Fi. Dùng chung mutex của modem để tránh lẫn phản hồi AT. Socket kiểm tra SIM dùng mux 4 với đối tượng sống suốt chương trình vì TinyGSM giữ con trỏ tới socket.

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
