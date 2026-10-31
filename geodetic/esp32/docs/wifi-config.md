# Wi-Fi trên ESP32

Firmware không phát AP và không phục vụ trang cấu hình Wi-Fi. Chỉ giữ một
SSID/mật khẩu đang dùng trong cấu hình kết nối. Cài qua Serial 115200:

```text
ATG ESP SET WIFI SSID <ten-mang>
ATG ESP SET WIFI PASS <mat-khau>
```

Bộ phân tích lệnh hiện dùng dấu cách để tách tham số; tên mạng/mật khẩu nhập
qua ATG không hỗ trợ khoảng trắng. Thiết lập được áp dụng ở lần kết nối lại.
Kết nối Wi-Fi/4G dự phòng vẫn hoạt động theo cấu hình thiết bị.

Khi nâng từ firmware có portal, lần thử Wi-Fi đầu kiểm tra danh sách cũ và
chọn mạng đầu tiên kết nối thành công. Chỉ giữ mạng đó sau khi đọc lại đúng
SSID và mật khẩu vừa lưu. Mạng ưu tiên bị lỗi vẫn có thể chuyển sang mạng kế
tiếp trong bước chuyển đổi này. Nếu chưa kết nối được hoặc lưu bị lỗi, giữ
danh sách cũ để thử lại lần sau. Khi chuyển đổi xong, không dùng danh sách
nhiều mạng nữa. Có thể xóa dữ liệu portal cũ từ System
Monitor trên web sau khi thiết bị chạy firmware mới.

Thông tin NVS chỉ báo số ô đã dùng/còn trống, không gửi SSID, mật khẩu hoặc
license lên web. Xem thêm [bộ nhớ ESP32](memory-storage.md).
