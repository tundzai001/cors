# API key cho tích hợp CORS

Trang quản trị: `/api-keys.html` (chỉ admin). Admin tạo key, chọn Vĩnh viễn hoặc thời điểm hết hạn, trạm, trường dữ liệu và quyền sửa cấu hình. Token thô chỉ trả một lần khi tạo; database chỉ lưu SHA-256. Admin có thể sửa quyền hoặc thời hạn mà không đổi token, hoặc thu hồi key ngay.

`expires_at` là Unix timestamp UTC; giá trị `0` nghĩa là vĩnh viễn (dùng đến khi thu hồi). Các key có thời hạn hiện tại giữ nguyên hiệu lực. Không cần thay đổi schema database. Mỗi request so sánh với đồng hồ hệ thống hiện tại. Server tắt bao lâu không làm chậm hạn: không cần lưu thời điểm tắt hay trừ thời gian offline. Máy chủ cần đồng bộ giờ qua NTP.

## API

- `POST /api/admin/api-keys`: tạo key. JSON gồm `name`, `expires_at`, `all_stations`, `station_serials`, `data_fields`, `edit_config`.
- `GET /api/admin/api-keys`: xem policy và trạng thái, không trả token.
- `PUT /api/admin/api-keys/{id}`: sửa policy hoặc gia hạn; token cũ vẫn dùng được nếu chưa thu hồi.
- `DELETE /api/admin/api-keys/{id}`: thu hồi.
- `GET /api/integrations/stations`: `Authorization: Bearer cors_...`. Trả `serial` và đúng các trường/trạm trong policy hiện tại.
- `POST /api/integrations/stations/{serial}/configure-service`: chỉ khi key có `edit_config` và serial trong phạm vi.
- `POST /api/integrations/stations/{serial}/configure-chip`: cùng điều kiện.

Public map (`/api/public/map-stations`) vẫn phục vụ bản đồ khách theo lựa chọn giữ bản đồ công khai. Không thể ngăn người xem trích xuất dữ liệu đã gửi tới trình duyệt; chỉ endpoint tích hợp có dữ liệu và quyền được kiểm soát riêng.

## Chuyển đổi `land_slide_web`

1. Trong `cors_dashboard/backend`, chạy `alembic -c alembic_auth.ini upgrade head` để áp dụng revision `20261008_api_keys` trước khi chạy backend mới.
2. Tạo key cho `land_slide_web`, cấp đúng trạm. Phần bản đồ cần `name`, `status`, `lat`, `lon`, `alt`, `mountpoint`, `ntrip_connected`, `bps` và `streams`. Luồng GNSS cần thêm `service_config`; bật `edit_config` nếu tích hợp điều khiển trạm.
3. Đặt `CORS_BASE_STATIONS_URL=https://cors.aitogy.com/api/integrations/stations` và `CORS_BASE_STATIONS_TOKEN=<key>` trong môi trường server `land_slide_web`. Không đặt key trong JavaScript trình duyệt.
4. Đặt `WEB_CORS_API_TOKEN=<key>` nếu Round Robin cũng dùng key này; lệnh đọc và sửa dịch vụ sẽ đi qua API tích hợp, theo cùng policy. Nếu tiếp tục dùng `WEB_CORS_API_USERNAME`/`WEB_CORS_API_PASSWORD`, Round Robin dùng JWT và `/api/map/stations` cho lệnh đọc.

Rollback code: giữ bảng `api_keys` và policy trong DB, trả ứng dụng về phiên bản cũ. Chỉ chạy Alembic downgrade khi đã chắc không còn tích hợp dùng key; downgrade sẽ xóa toàn bộ key.

Tạo key xong hiển thị thông báo tự tắt sau 5 giây. Key mới nằm trong mục Hiển thị key của hàng tương ứng tại Key đã cấp, có nút sao chép; chỉ giữ trong bộ nhớ đến khi tải lại trang. Mỗi hàng có cột Hướng dẫn. Hướng dẫn gồm URL theo origin hiện tại, method, header Bearer, cURL và JSON minh họa chỉ chứa serial cùng các trường được cấp. Ví dụ không chứa token thật hoặc dữ liệu trạm thật.
