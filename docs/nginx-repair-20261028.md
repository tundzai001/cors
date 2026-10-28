# Xử lý lỗi Nginx ngày 28/10/2026

Đã xử lý trực tiếp trên máy chủ Windows qua SSH. Không khởi động dịch vụ Connect.

## Nguyên nhân và thay đổi

- `C:/Certbot/live/connect.aitogy.com/fullchain.pem` trỏ tới chứng chỉ trong `archive/connect.aitogy.com/fullchain2.pem` nhưng Windows từ chối quyền đọc. `cert2.pem` và `chain2.pem` cũng không đọc được. Chứng chỉ mới tồn tại; không cần cấp lại chứng chỉ. Khôi phục quyền trên ba file chứng chỉ công khai cho Administrators và SYSTEM. Không thay đổi private key.
- Lỗi chứng chỉ làm mọi lần `nginx -t` thất bại, chặn reload và đồng bộ danh sách IP cho Base. Sau sửa, `nginx -t` đạt, reload được yêu cầu thành công và danh sách đã xuất 17 IP.
- Cập nhật deploy hook Certbot tại `C:/Certbot/renewal-hooks/deploy/reload-nginx.bat` để gọi `C:/ProgramData/Aitogy/Nginx/certbot-reload.ps1`. Hook xử lý quyền trên file chứng chỉ công khai trong archive rồi chỉ reload khi kiểm tra cấu hình đạt. Không thay đổi quyền private key. Đã chạy hook với lineage thực; chưa thực hiện một lần gia hạn chứng chỉ mới.
- Monitoring dùng `websockets-sansio` với Uvicorn cũ, trả hai header `Date` trong handshake được chấp nhận. Tái hiện trên Uvicorn 0.38.0; Uvicorn 0.52.4 trả một header. Cập nhật Uvicorn trong hai môi trường Python của Monitoring lên 0.52.4; môi trường legacy cũng được đưa về websockets 16.1.1 theo requirements. Không thay đổi Python toàn hệ thống.
- Monitoring đã dừng có kiểm soát bằng Ctrl+C trong phiên console của ứng dụng, sau đó chạy lại qua task có sẵn `LandslideWebVisible` và môi trường `C:/ProgramData/Aitogy/Landslide/venv`. Không buộc kết thúc tiến trình. PID nghe cổng 8001 đổi từ 5404 sang 19012; Base giữ PID 10688.

Các lỗi 10061 của Base trong ngày trùng với các lần dừng/khởi động backend: 15:01–15:02 và 16:04–16:06. Monitoring cũng có lần dừng lúc 21:09 và khởi động lúc 21:11. Những bản ghi này không chứng minh Nginx tự gây mất kết nối. Các lỗi đọc receiver/caster trong log ứng dụng thuộc phạm vi khác, chưa được thay đổi trong lần sửa Nginx này.

## Xác minh

- `nginx -t`: thành công trên cấu hình thật của Windows.
- Hook Certbot: đã chạy và reload thành công với chứng chỉ hiện có.
- HTTPS Base và Monitoring: HTTP 200, xác minh chứng chỉ bằng CA tin cậy.
- Monitoring `/api/health/live`: HTTP 200, `live`.
- Monitoring `/api/health/ready`: HTTP 200, `ready`.
- 22 bài kiểm tra handshake/quyền WebSocket/quản lý kết nối đạt trong cả hai môi trường Python trên server. Một handshake thực qua TCP của ứng dụng kiểm thử chỉ có một header Date.
- Từ mốc khởi động lại 22:45:59 tới kiểm tra cuối khoảng 22:47, ba error log Nginx không có bản ghi mới. Chưa quan sát một WebSocket admin đã xác thực mới trên ứng dụng production trong khoảng kiểm tra này.

Bản sao và các wheel phiên bản cũ để quay lại nằm tại `C:/ProgramData/Aitogy/NginxRepair20261028`. Task tạm phục vụ khởi động lại được gỡ sau khi chạy; giữ task ứng dụng có sẵn. Không xóa hay sửa các file log gốc trên Desktop.

## Tham khảo

- [Uvicorn 0.52.4 sửa Date trùng trong WebSocket SansIO](https://github.com/Kludex/uvicorn/releases/tag/0.52.4).
- [Nginx kiểm tra và reload cấu hình](https://nginx.org/en/docs/beginners_guide.html).
- [Certbot deploy hooks và thư mục chứng chỉ](https://eff-certbot.readthedocs.io/en/stable/using.html).
