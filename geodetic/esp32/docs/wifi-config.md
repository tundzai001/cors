# Cấu hình Wi-Fi cục bộ

ESP luôn phát một Wi-Fi riêng tên `AITOGY-xxxxxx`, kể cả khi chưa có SIM hoặc chưa kết nối Wi-Fi bên ngoài. Dùng điện thoại kết nối mạng này và mở **http://192.168.4.1**. Trang được lưu trong firmware, không cần Internet, app hoặc dịch vụ web Base.

Mỗi ESP có mật khẩu AP ngẫu nhiên riêng, được lưu trong NVS. Khi khởi động, Serial 115200 in tên mạng và mật khẩu AP tại dòng `[WIFI CONFIG]`. Ghi lại mật khẩu/nhãn thiết bị trong lần cài đặt đầu. Mật khẩu AP chỉ hiện ở Serial tại máy, không đưa vào MQTT hay API trang cấu hình.

## Sử dụng

1. Kết nối điện thoại vào Wi-Fi `AITOGY-xxxxxx` bằng mật khẩu riêng.
2. Mở `192.168.4.1`; nếu điện thoại cảnh báo mạng không có Internet, chọn tiếp tục kết nối.
3. Bấm **Quét mạng** và chọn Wi-Fi, hoặc nhập tên mạng/hotspot thủ công.
4. Nhập mật khẩu rồi chọn **Lưu & ưu tiên mạng này**. Mạng vừa lưu được thử trước; tối đa 8 mạng.
5. Theo dõi tên Wi-Fi đang kết nối ở đầu trang. ESP giữ mạng cấu hình bật trong lúc thử mạng bên ngoài.

Khi sửa một mạng đã lưu, để trống mật khẩu để giữ mật khẩu cũ. Mạng mở phải được chọn rõ bằng ô **Mạng không có mật khẩu**. Nút **Quên** xóa mạng khỏi danh sách; quên tất cả sẽ không tự khôi phục mạng mặc định cũ.

Hotspot điện thoại cần bật 2.4 GHz. Nếu dùng chính điện thoại đó để cấu hình, sau khi lưu cần bật lại hotspot để ESP kết nối. ESP phát AP liên tục không thay đổi giới hạn đồng thời phát hotspot/kết nối Wi-Fi của điện thoại.

## Hành vi và tương thích

- AP được khởi động trước phần thử mạng và phục vụ HTTP trên task riêng, nên trang vẫn hoạt động trong các lượt chờ GSM/Wi-Fi.
- Trang không reboot ESP, thay đổi cấu hình GNSS, license hoặc lựa chọn ưu tiên SIM/Wi-Fi đã lưu. Áp dụng mạng mới có thể ngắt các kết nối MQTT/NTRIP hiện tại trong lúc nối lại.
- Khi đang ưu tiên SIM, mạng lưu từ portal là mạng dự phòng; cơ chế trở về SIM vẫn áp dụng. Khi đã chọn Wi-Fi thủ công, lưu mạng không tự bật chế độ ưu tiên SIM.
- Mạng `WIFI_SSID`/`WIFI_PASS` cũ được đọc nếu chưa có danh sách portal. Các lệnh `ESP SET WIFI SSID ...` và `ESP SET WIFI PASS ...` dùng cùng bộ lưu mới để không tạo hai nguồn cấu hình lệch nhau.
- Bản tin trạng thái Wi-Fi trên trang mô tả liên kết Wi-Fi, không chứng minh đường SIM đang chở dữ liệu. Xem `connected_via` trong status để xác định transport đang dùng.
- Hai client tối đa được phép kết nối AP. AP có thể đổi kênh theo Wi-Fi STA; điện thoại có thể phải kết nối lại AP khi ESP đổi mạng ngoài.

## Giới hạn truy cập

HTTP chỉ bind địa chỉ AP `192.168.4.1`. Mỗi yêu cầu API cần token phiên ngẫu nhiên; nguồn IP và Host phải thuộc đường cấu hình cục bộ. Không bật CORS. API không trả mật khẩu đã lưu. SSID được dựng bằng text, không thực thi HTML từ tên mạng.

Mật khẩu mạng được lưu trong NVS theo cơ chế hiện tại của firmware; thay đổi này không bật mã hóa flash/NVS. Chỉ người có mật khẩu AP của thiết bị mới nên được truy cập trang cấu hình.

## Kiểm tra

```sh
platformio run -e tdm240x_dev_general
c++ -std=c++17 -Wall -Wextra -Werror -Igeodetic/esp32/test/host -Igeodetic/esp32/include \
  geodetic/esp32/test/host/wifi_profiles_checks.cpp -o /tmp/wifi-profiles-checks
/tmp/wifi-profiles-checks
```

Lệnh trên chạy từ workspace `cors`. Kiểm tra host không thay thế thử AP+STA, mất mạng và đổi hotspot trên ESP thực tế.
