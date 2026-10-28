# Đối chiếu lịch sử đăng nhập ngày 28/10/2026

Ba file Desktop bao phủ 24/10/2026 15:25:47 tới 28/10/2026 22:14:37 (UTC+7).
Chỉ đếm bản ghi `uvicorn.access` cho `POST /api/auth/login` với kết quả cuối cùng.
Không cộng dòng bắt đầu request, dòng hoàn tất middleware hoặc `Login attempt`.

| File | HTTP 200 | HTTP 401 |
| --- | ---: | ---: |
| app.log.2 | 23 | 1 |
| app.log.1 | 10 | 0 |
| app.log | 3 | 0 |
| Tổng | 36 | 1 |

Có 37 dòng kết quả HTTP riêng biệt, không có dòng trùng giữa các file. Lượt thành
công theo ngày: 24/10: 4; 25/10: 4; 26/10: 14; 27/10: 9; 28/10: 5.

Trong cùng cửa sổ thời gian, `user_sessions` có đúng 36 phiên và `activity_logs`
có đúng 36 sự kiện đăng nhập thành công, cùng phân bố theo ngày. Vì vậy không
nhập lại các log này vào thống kê: nhập lại sẽ cộng trùng các lần đăng nhập.

Tại thời điểm đối chiếu, toàn bộ lịch sử có 382 phiên từ 14/10 11:30:40 tới
28/10 23:09:38. Bộ đếm lượt xem mới có 9 lượt từ một hồ sơ trình duyệt, bắt đầu
28/10 22:54:27. Hai tập số liệu đo những hành vi khác nhau và có thời gian ghi
nhận khác nhau. Chúng không được dùng thay thế cho nhau.

File đầu bắt đầu giữa ngày 24/10: trong cả ngày này có 26 phiên, còn cửa sổ
log chỉ có 4. Ngày 28/10 có 7 phiên tại lúc kiểm tra API mới, còn bản log Desktop
chốt lúc 22:14 chỉ bao phủ 5 phiên. So sánh phải dùng cùng cửa sổ thời gian.

Bản sửa lấy lượt đăng nhập từ phiên đã lưu, giữ lượt xem theo tracker, hiển thị
mốc ghi nhận và để trống phần biểu đồ chưa có dữ liệu thay vì tự gán 0. Chọn
7/28/90 ngày áp dụng cho cả hai tập số liệu theo lịch Việt Nam. Phiên hết hạn hoặc
bị thu hồi vẫn là một lần đăng nhập trong lịch sử; heartbeat không tạo phiên mới.

File JSON đối chiếu trên Desktop lưu SHA-256 từng log và các tổng số; không chứa
mật khẩu, token, tên tài khoản hoặc địa chỉ IP.
