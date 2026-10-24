# Chuyển web sang base.aitogy.com

Cấu hình cuối chỉ phục vụ `base.aitogy.com`, dùng backend hiện tại
`http://127.0.0.1:8000` cho web, API và WebSocket. Domain cũ không còn trong
`server_name` và không chuyển hướng sang domain mới.

## Trình tự trên máy chủ Windows

1. Cập nhật repo lên bản cấu hình domain mới. Sao lưu file đang chạy
   `C:\nginx-1.28.0\conf\nginx.conf`; giữ nguyên các virtual host của sản phẩm khác.
2. Trước khi có chứng chỉ mới, chỉ sửa block HTTP cổng 80 của web thành
   `server_name base.aitogy.com;`. Giữ location ACME hiện có:

   ```nginx
   location /.well-known/acme-challenge/ {
       root C:/nginx-1.28.0/html;
       allow all;
   }
   ```

   Chạy trong PowerShell, chỉ reload nếu kiểm tra thành công:

   ```powershell
   & C:\nginx-1.28.0\nginx.exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -t
   if ($LASTEXITCODE -eq 0) {
       & C:\nginx-1.28.0\nginx.exe -p C:/nginx-1.28.0/ -c conf/nginx.conf -s reload
   }
   ```

3. Dùng Certbot đang cài trên máy để cấp chứng chỉ riêng cho domain mới:

   ```powershell
   certbot certonly --webroot -w C:/nginx-1.28.0/html --cert-name base.aitogy.com -d base.aitogy.com
   ```

   Xác nhận chứng chỉ có SAN `base.aitogy.com`, chưa hết hạn và lưu tại
   `C:/Certbot/live/base.aitogy.com/`. Nếu Certbot dùng config directory khác,
   cập nhật đúng đường dẫn chứng chỉ/key mà Certbot trả về trong Nginx.
   Kiểm tra tác vụ gia hạn tự động có bao gồm chứng chỉ mới và reload Nginx sau gia hạn.
4. Áp dụng block web hoàn chỉnh từ `nginx.conf` cùng thư mục này. HTTP và HTTPS đều chỉ có:

   ```nginx
   server_name base.aitogy.com;
   ```

   HTTPS dùng chứng chỉ `C:/Certbot/live/base.aitogy.com/fullchain.pem`
   và key `C:/Certbot/live/base.aitogy.com/privkey.pem`.
   Chạy lại `-t`, chỉ reload khi thành công. Không stop Nginx hoặc tạo backend mới.
5. Triển khai backend/frontend cùng phiên bản mới và restart backend theo cơ chế đang dùng.
   Trong `.env` trên server, đổi hostname cũ sang `base.aitogy.com` cho
   `ZALO_WEBHOOK_URL`, `ZALO_TB_WEBHOOK_URL` và các URL công khai đã cấu hình.
   Đổi webhook ở phía Zalo và các URL wake-station/wake-listen của AiCaster nếu đang dùng.
   Xem `docs/security-monitoring.md`. Cập nhật allowed URLs của token Mapbox theo
   `docs/mapbox-production.md`.
6. Các Pi có override `BACKEND_WS_HOST` cần đổi thành `base.aitogy.com` rồi restart
   agent. Default mới của Python và firmware ESP32 cũng dùng domain này;
   firmware đang chạy cần được cập nhật để nhận default mới.
   MQTT và NTRIP dùng hostname độc lập, giữ nguyên thông số broker/caster.
7. Kiểm tra HTTPS, đăng nhập, API, WebSocket và bản đồ tại `https://base.aitogy.com`.
   Đăng nhập lại trên domain mới. Không bỏ qua kiểm tra chứng chỉ khi xác minh.
   Sau khi xác nhận, xóa bản ghi DNS của domain cũ nếu không còn dịch vụ nào sử dụng.

## Quay lại khi triển khai lỗi

Khôi phục cấu hình Nginx đã sao lưu, chạy `-t` rồi reload. Khôi phục bản backend/frontend
và các URL môi trường trước đó nếu chúng đã đổi. Khôi phục DNS cũ nếu đã xóa.
Giữ các chứng chỉ hiện có trong quá trình kiểm tra để có thể quay lại.
