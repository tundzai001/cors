# Phục hồi Base và Nginx ngày 29/10/2026

## Nguyên nhân và xử lý trên server

Ba site IIS `Default Web Site`, `cors` và `monitor` đăng ký cổng 80/443 qua HTTP.sys (PID 4), ngăn Nginx bind cổng. Nginx không chạy nên `-s reload` cũng thất bại vì không có master process nhận tín hiệu.

Đã sao lưu IIS bằng AppCmd: `BeforeBasePortRepair-20261029-150418`. Dừng riêng ba site xung đột và đặt `serverAutoStart:false`. Giữ site `aitogy.com` tại cổng 8081. Không xóa port exclusion, không dừng toàn bộ IIS, không thay đổi cấu hình proxy của các website khác.

`nginx -t` đạt sau khi giải phóng cổng. Đã cài `AitogyNginxRecovery` chạy bằng SYSTEM: kích hoạt sau reboot và mỗi phút; bỏ qua lượt chạy trùng. Script kiểm tra tiến trình Nginx theo đường dẫn, chỉ khởi động khi chưa chạy và cấu hình kiểm tra đạt. Task không tự chiếm lại cổng, dừng IIS hay bỏ qua lỗi cấu hình. Chưa thử gây crash Nginx trên production để kiểm tra tự phục hồi.

Các file phục hồi nằm tại `C:/ProgramData/Aitogy/Nginx/`. Nguồn tương ứng: `deploy/nginx/ensure-nginx.ps1`, `deploy/nginx/install-recovery.ps1`. Không cài task lần hai nếu task đã tồn tại.

## Caster

Khi kiểm tra lại từ server, TCP `aitogy.click:2101` kết nối được. Đọc sourcetable bằng cấu hình hiện có: `aitogy.com.vn` trả 140 mountpoint; `aitogy.click` trả sourcetable hợp lệ với 0 mountpoint. Kết quả này không chứng minh mọi luồng RTCM hoặc Rover đều hoạt động.

Sửa kết nối caster để thử tối đa ba lần trước khi gửi dữ liệu, chờ 1 và 2 giây, trong ngân sách thời gian yêu cầu hiện có. Không gửi lại lệnh điều khiển khi đã ghi dữ liệu ra socket. Cảnh báo kết nối được giới hạn một lần mỗi phút cho mỗi host/port và có log khi kết nối phục hồi. Mất kết nối không bị báo nhầm thành sourcetable sai định dạng. Đồng bộ nguồn giữ dữ liệu đã lưu và báo cảnh báo thay cho traceback của tình huống caster không phản hồi. Lỗi lập trình/database vẫn được ghi traceback.

Hai file Python gốc trên server đã được so sánh với HEAD trước sửa và sao lưu tại `C:/ProgramData/Aitogy/BaseReconnect20261029/`. Backend được yêu cầu dừng bằng Ctrl+C trong console, không force-kill; khởi động lại qua task Base hiện có để nạp bản sửa.

## Xác minh

- 18 kiểm tra reconnect, lưu danh sách mountpoint và đồng bộ Nginx đạt tại máy phát triển.
- 4 kiểm tra reconnect đạt trên Python 3.11 của server, gồm thử lại trước khi gửi, không gửi trùng, phục hồi và hủy khi shutdown.
- Base trả HTTPS HTTP 200 từ mạng bên ngoài sau khi Nginx khởi động.

## Quay lại

Khôi phục hai file Python từ bản sao trước sửa rồi khởi động lại Base có kiểm soát. Nếu chuyển quyền phục vụ 80/443 về IIS, dừng và vô hiệu hóa task Nginx trước; dừng Nginx có kiểm soát rồi mới khôi phục trạng thái/tự khởi động của ba site. Bản sao IIS dùng để đối chiếu; không khôi phục toàn bộ cấu hình đè lên thay đổi vận hành khác.

## Vấn đề IIS riêng còn tồn tại

Web công ty có site `aitogy.com` ở trạng thái Started nhưng application pool cùng tên bị Stopped và trả 503. Đã thử khởi động riêng pool; pool lại dừng. Windows Event Log cũng không khởi động ổn định, báo exit code 4201 nên chưa đọc được sự kiện WAS để xác định nguyên nhân pool dừng. Không tắt rapid-fail protection, không sửa quyền hệ thống hoặc khởi động lại toàn bộ IIS để che lỗi. Đây là phần cần chẩn đoán tiếp, không được coi là đã phục hồi cùng Base.
