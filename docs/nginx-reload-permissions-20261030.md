# Khôi phục quyền reload Nginx trên Windows — 30/10/2026

Đã xử lý qua SSH trên máy Windows `aitogy.click`. Nginx 1.28.0 được task `AitogyNginxRecovery` khởi chạy dưới SYSTEM; backend Base chạy dưới tài khoản Administrator. Lệnh reload của backend thất bại tại `OpenEvent("Global\\ngx_reload_7396")` với lỗi Windows 5, Access is denied. Kiểm tra cấu hình `nginx -t` vẫn thành công.

## Thay đổi

Cập nhật `deploy/nginx/ensure-nginx.ps1`, đồng thời triển khai tới `C:\ProgramData\Aitogy\Nginx\ensure-nginx.ps1`:

- Giữ Nginx dưới SYSTEM và backend dưới Administrator.
- Cấp riêng `EventWaitHandleRights.Modify` trên event reload của master cho tài khoản `$env:COMPUTERNAME\Administrator`. Có thể chọn tài khoản khác bằng tham số `-ReloadAccount` khi triển khai.
- Không cấp quyền FullControl trên event, không cấp quyền cho Everyone, không đổi quyền stop/quit của Nginx.
- Đọc PID master từ `logs\nginx.pid`, xác minh đúng executable rồi mới mở event. Thử tối đa 20 lần, nghỉ 500 ms giữa các lần khi event chưa xuất hiện trong lúc khởi động; thời gian truy vấn process có thể làm tổng thời gian dài hơn 10 giây.
- Khi cần khởi chạy Nginx, dùng `Start-Process` để recovery script tiếp tục cấp quyền sau khi master tạo event. Bản cũ chờ Nginx suốt vòng đời process, khiến task luôn Running và các lần chạy lặp bị IgnoreNew.
- Recovery script cấp lại quyền theo PID hiện tại ở các lần chạy sau, gồm sau reboot. Chưa thử reboot hoặc tạo lại master trong lần sửa này.

Chạy một task SYSTEM tạm thời để thực thi recovery script mới trên master đang chạy; task hoàn tất với `LastTaskResult=0` rồi được xóa. Không dừng master và không khởi động lại backend. Lệnh reload tạo worker mới và cho worker cũ kết thúc theo cơ chế graceful của Nginx.

## Xác minh thực tế

- PowerShell parser: đạt.
- SYSTEM repair task: exit 0.
- Administrator mở reload event với quyền Modify: đạt.
- `nginx -t`: đạt; `nginx -s reload` từ Administrator: exit 0.
- Master vẫn PID 7396; backend Base vẫn PID 3976.
- Backend tự ghi lại blocklist lúc 23:03:10 giờ máy chủ, gồm 19 quy tắc IP. Nginx ghi nhận signal cùng thời điểm và có worker mới; không còn Access is denied trong các log mới kiểm tra.
- HTTPS `base.aitogy.com`, `monitoring.aitogy.com` và `aitogy.com`: HTTP 200 sau reload.
- Chưa kiểm tra một yêu cầu từ IP bị chặn thật; việc đã reload không thay thế bằng chứng chặn lưu lượng ngoài thực tế.

File triển khai và file trong repo có cùng SHA256 `bdcafb0b287d99b0c021ada8ea3a3fee22ec76d7e9a3824f875cc39e69116675`.

## Sao lưu và hoàn tác

Trên máy Windows:

- Script cũ: `C:\ProgramData\Aitogy\Nginx\ensure-nginx.20261030-230210.bak.ps1`.
- Task XML: `C:\ProgramData\Aitogy\Nginx\AitogyNginxRecovery.20261030-230210.xml`.
- DACL gốc của event master 7396: `C:\ProgramData\Aitogy\Nginx\reload-event-7396.original.sddl`.

Khôi phục script cũ sẽ ngừng cấp quyền ở các lần chạy sau. Muốn hoàn tác ngay quyền event của master đang chạy, thực thi dưới SYSTEM: mở đúng event với ReadPermissions/ChangePermissions, tạo `EventWaitHandleSecurity` từ SDDL đã lưu và gọi `SetAccessControl`. Không áp dụng SDDL này cho một event/PID khác. Việc thu hồi quyền khiến backend Administrator không reload được như trước.
