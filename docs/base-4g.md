# Base 4G theo mountpoint

Mountpoint bắt đầu bằng `7` được nhận diện là Base 4G chỉ xem. Trạm chỉ có Overview, không kích hoạt, không token/license, không sửa cấu hình, đổi tên, reset, khóa hoặc xóa qua UI/API.

Backend dùng danh sách mountpoint đã đồng bộ từ các Caster cấu hình sẵn. Trạm mới có serial `MONITOR_<mountpoint>`; nếu đã có thiết bị dùng mountpoint đó thì giữ serial hiện tại. Các bản ghi trùng mountpoint chỉ chạy một decoder, chọn Caster theo thứ tự host/port ổn định để tránh đổi kết nối sau mỗi lần quét.

Mỗi 15 giây, backend gửi cấu hình qua MQTT `pi/decoder/esp_base` cho `geodetic/geotek_daithanh_aitogy.py`. Username phải trùng mountpoint; mật khẩu lấy từ RoverAccount. Tài khoản phải đang bật, chưa hết hạn, có quyền mountpoint và Caster tương ứng. Luồng này không tạo, sửa hoặc xóa tài khoản Rover. Thiếu tài khoản hợp lệ thì dừng decoder.

Decoder giữ nguyên kết nối khi cấu hình không đổi. Đổi mật khẩu hoặc endpoint thì đóng worker cũ trước khi tạo worker mới. Các lần đăng nhập cách nhau ít nhất 1 giây và dùng chung giới hạn kết nối nguồn. Khi mất dữ liệu, đóng socket trước khi chờ kết nối lại, tối thiểu 60 giây. Base đầu `7` trong monitor_list tĩnh được bỏ qua để backend quản lý bằng mật khẩu Rover hiện tại.

Decoder kiểm tra phản hồi HTTP/ICY và chỉ tính lưu lượng từ khung RTCM3 đúng CRC24Q. Tọa độ lấy từ 1005/1006, vệ tinh/SNR dùng bộ giải mã MSM hiện có. ONLINE xác nhận có RTCM, không đồng nghĩa RTK Fixed. Chưa giải mã tọa độ thì Overview hiển thị trạng thái chờ; không dùng vị trí GGA gửi đi làm tọa độ quan sát.

Máy đầu `7` không có sleep/wake hoặc stream On-Demand. Chỉ có `online` hoặc `offline`. Đã xác thực nhưng chưa có RTCM vẫn là `offline`; sau khung RTCM hợp lệ đầu tiên là `online`; mất kết nối hoặc không có khung hợp lệ trong 12 giây trở lại `offline`. Log xác thực thành công không được xem là bằng chứng có RTCM.

Callback wake/stop cho MP đầu `7` chỉ trả kết quả tương thích, không tạo lease, không gửi lệnh tới máy và không chặn dữ liệu theo trạng thái decoder. Blocklist Rover vẫn được kiểm tra. Các lease cũ hết hạn cũng không được bật/tắt luồng của Base đầu `7`.

Trong repo `Aicaster` (không phải `Ai-caster dev`), `PointNav/CASTER/Server/NTRIPServer.cs` bỏ callback wake và hai điều kiện chặn theo status backend cho MP đầu `7`, đồng thời không nhận trạng thái decoder làm trạng thái nguồn. Xác thực, quyền MP, giới hạn kết nối và kiểm tra socket vẫn giữ nguyên. Cần build lại và triển khai Caster để gỡ cả trường hợp nguồn đã bị kẹt status 2 trước lần cập nhật này.

Cần chạy cả backend và decoder mới để sử dụng luồng này. Không cần thay firmware các máy Base 4G đầu `7`.

## Kiểm tra cục bộ

- Backend: `python -m unittest tests.test_prefix7_bases tests.test_esp_base_manager` trong `cors_dashboard/backend`.
- Decoder: `python -m unittest discover -s geodetic/tests` tại thư mục gốc.
- UI: chạy `node scripts/build_frontend_assets.mjs`, rồi `node scripts/prefix7_ui_selftest.js` và `node scripts/station_type_selftest.js`.

Kiểm thử dùng socket, MQTT và tài khoản giả lập; không xác nhận dữ liệu của thiết bị thật trên production.
