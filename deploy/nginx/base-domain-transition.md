# Chạy song song base.aitogy.com và cors.aitogy.com

Hai tên miền dùng chung các location và backend `http://127.0.0.1:8000`.
Không chuyển hướng giữa hai tên miền. HTTP chuyển sang HTTPS trên chính tên miền nhận request.
Các route API, WebSocket, cấu hình chặn IP và endpoint thiết bị được giữ nguyên.

## Áp dụng trên máy chủ Windows

1. Sao lưu `C:\nginx-1.28.0\conf\nginx.conf`.
2. Trong block HTTP cổng 80 của CORS, đổi thành:

   ```nginx
   server_name cors.aitogy.com base.aitogy.com;
   ```

   Giữ block HTTPS hiện tại trong bước này. Kiểm tra và reload:

   ```powershell
   & C:\nginx-1.28.0\nginx.exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -t
   # Chỉ chạy khi lệnh kiểm tra thành công:
   & C:\nginx-1.28.0\nginx.exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -s reload
   ```

3. Mở rộng chứng chỉ hiện có để bao gồm cả hai domain, dùng Certbot đang cài trên server:

   ```powershell
   certbot certonly --webroot -w C:/nginx-1.28.0/html --cert-name cors.aitogy.com --expand -d cors.aitogy.com -d base.aitogy.com
   ```

   Xác nhận chứng chỉ được lưu tại `C:/Certbot/live/cors.aitogy.com/` và có cả hai domain.
   Nếu Certbot trên máy dùng config directory khác, dùng đúng đường dẫn Certbot báo; không đổi nhầm lineage của domain cũ.

4. Thêm `base.aitogy.com` vào `server_name` của block HTTPS giống cấu hình trong `nginx.conf` cùng thư mục này.
   Chạy lại `-t`; chỉ reload khi kiểm tra thành công.

5. Kiểm tra HTTPS của cả hai domain, đăng nhập và WebSocket.
   Không dùng tùy chọn bỏ qua xác minh chứng chỉ để kết luận HTTPS hoạt động.

## Quay lại

Khôi phục bản sao `nginx.conf`, chạy `-t`, rồi reload. Chứng chỉ có thêm domain vẫn dùng được cho `cors.aitogy.com`.
Chưa xóa domain cũ, đổi URL thiết bị hay thay domain chính trong ứng dụng ở giai đoạn này.
