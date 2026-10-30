# ESP32 V2.3.5: SIM và Wi-Fi dự phòng

Nếu lựa chọn đã lưu là 4G, firmware thử SIM trước. Khi khởi động, mỗi lượt kết nối GSM chỉ thử một lần trước khi chuyển sang thử Wi-Fi đã lưu nếu không đăng ký được GPRS.

GPRS đã kết nối không chứng minh truy cập được dịch vụ. Khi cả MQTT và NTRIP không kết nối được liên tục ít nhất 30 giây, firmware thử Wi-Fi. Kiểm tra này chạy trước/sau lượt kết nối MQTT trong chính task MQTT, cùng với vòng quản lý mạng, để không bị bỏ lỡ do các lượt reconnect đang giữ mutex modem. Thời gian timeout của các lệnh modem có thể làm việc chuyển mạng muộn hơn mốc 30 giây.

Wi-Fi phải kết nối thành công và bắt tay MQTT với broker đã cấu hình trước khi được chọn. TCP Wi-Fi chờ tối đa 3 giây; bắt tay MQTT chờ tối đa 3 giây. Nếu Wi-Fi chưa dùng được, giữ SIM và thử lại có khoảng nghỉ. Không dùng socket probe SIM mux 4 hay IP Internet công cộng để quyết định fallback. Thiết bị không khởi động lại chỉ vì mất GPRS hoặc caster từ chối mountpoint.

Khi chuyển, đóng socket MQTT/NTRIP trên SIM trước khi đổi transport, cấu hình lại MQTT/NTRIP và WebSocket trên Wi-Fi. Dùng chung mutex của modem để tránh lẫn phản hồi AT.

Fallback không sửa `CONNECTION_TYPE` trong NVS. Thiết bị tiếp tục dùng Wi-Fi dự phòng khi Wi-Fi ổn định; lần khởi động sau lại đọc lựa chọn SIM đã lưu. Chỉ Wi-Fi được chọn qua fallback mới được tự chuyển về SIM; Wi-Fi chọn thủ công không bị đổi. Khi SIM đã đăng ký mạng, mỗi 60 giây firmware thử bắt tay MQTT qua socket SIM riêng (TCP tối đa 5 giây, MQTT tối đa 3 giây). Hai lần thành công liên tiếp sẽ chuyển về SIM. Khi Wi-Fi mất kết nối, khoảng thử giảm còn 15 giây và chỉ cần một lần thành công. Probe không có LWT, không publish/subscribe và không sửa status trạm. Modem vẫn được cấp điện để theo dõi khả năng khôi phục SIM; đèn modem có thể tiếp tục nháy khi đang truyền dữ liệu qua Wi-Fi. Các lệnh AT/GPRS vẫn có thể làm chu kỳ thử kéo dài hơn dự kiến.

Gói MQTT status khoảng 5 KB được chia thành các lần ghi modem tối đa 1024 byte; giữ nguyên một gói MQTT. Khi modem ghi thiếu, đóng stream TCP để vòng MQTT kết nối lại, tránh gửi gói mới vào giữa gói cũ. Bộ đệm MQTT vẫn 16384 byte và có log nếu cấp phát thất bại.

## Kiểm tra

```sh
c++ -std=c++17 -Wall -Wextra -Werror -I geodetic/esp32/include geodetic/esp32/test/host/mqtt_write_checks.cpp -o /private/tmp/mqtt-write-checks
/private/tmp/mqtt-write-checks
platformio run -e tdm240x_dev_general
```

Các kiểm tra host giả lập I/O; chưa thay thế thử nghiệm rút SIM/mất Internet trên thiết bị thật. Chưa tự nạp firmware lên ESP trong lần sửa này.

Lỗi `ERROR - Mountpoint already in using` vẫn được caster trả khi nguồn đang được đánh dấu sử dụng. Firmware không bỏ qua xác thực hoặc tự ngắt nguồn khác để chiếm mountpoint.

Khi áp dụng danh sách Wi-Fi mới trên transport Wi-Fi, đóng MQTT/NTRIP trước khi `setupWiFi()` ngắt STA. Đóng sau khi đổi mạng có thể khiến caster còn giữ phiên nguồn cũ. Khi đang dùng SIM, giữ socket SIM đến khi Wi-Fi vượt qua kiểm tra broker. Khi Wi-Fi đã rớt, đóng socket cũ trước khi thử kết nối lại; không bảo đảm caster nhận được TCP FIN nếu đường truyền đã mất.

Kiểm tra thứ tự handover bằng `python3 geodetic/esp32/test/host/check_wifi_handover.py`. Bài kiểm tra chạy hàm handover thật với socket giả lập, kiểm tra đóng socket trước mất route và giữ SIM khi Wi-Fi thất bại. Cần thử lại trên ESP để xác nhận caster giải phóng phiên trong điều kiện mạng thực tế.

## IntelliSense cho bài kiểm tra MQTT trên máy tính

Chạy `platformio run -e tdm240x_dev_general -t compiledb` từ thư mục repo. Hook trong `scripts/platformio_host_compiledb.py` bổ sung `mqtt_write_checks.cpp` với compiler C++ của máy tính và include của firmware. PlatformIO tiếp tục tạo các mục firmware bằng toolchain ESP32. `compile_commands.json` là file được sinh tại máy và không commit.
