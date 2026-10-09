# API key cho tích hợp CORS

Trang quản trị: `/api-keys.html` (chỉ admin). Admin tạo key, chọn Vĩnh viễn hoặc thời điểm hết hạn, trạm, trường dữ liệu và quyền sửa cấu hình. Database lưu SHA-256 để xác thực và bản Fernet mã hóa để admin xem lại key. Danh sách không trả token; chỉ endpoint xem key riêng được phép trả token, với Cache-Control: no-store. Admin có thể sửa quyền hoặc thời hạn mà không đổi token, hoặc thu hồi key ngay.

`expires_at` là Unix timestamp UTC; giá trị `0` nghĩa là vĩnh viễn (dùng đến khi thu hồi). Các key có thời hạn hiện tại giữ nguyên hiệu lực. Lựa chọn vĩnh viễn không cần đổi schema. Khả năng xem lại key thêm cột nullable `token_encrypted`. Mỗi request so sánh với đồng hồ hệ thống hiện tại. Server tắt bao lâu không làm chậm hạn: không cần lưu thời điểm tắt hay trừ thời gian offline. Máy chủ cần đồng bộ giờ qua NTP.

## API

- `POST /api/admin/api-keys`: tạo key, chỉ trả metadata; không trả token. JSON gồm `name`, `expires_at`, `all_stations`, `station_serials`, `data_fields`, `edit_config`.
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

Tạo key xong hiển thị thông báo tự tắt sau 5 giây. Key mới nằm trong mục Hiển thị key của hàng tương ứng tại Key đã cấp. Mỗi lần xem phải nhập mật khẩu tài khoản admin đang đăng nhập; xác nhận đúng mới tải token, hiện nút sao chép và đếm ngược 30 giây. Hết thời gian, đóng mục, đổi bộ lọc hoặc chuyển tab sẽ xóa token khỏi giao diện; xem tiếp phải nhập lại mật khẩu. Key mới được lưu mã hóa và xem lại được sau khi tải trang. Với key cũ chỉ còn hash, admin dán key đã lưu vào mục Hiển thị key để lưu mã hóa; server xác minh hash, không đổi token hoặc quyền. Mỗi hàng có cột Hướng dẫn. Hướng dẫn gồm URL theo origin hiện tại, method, header Bearer, cURL và JSON minh họa chỉ chứa serial cùng các trường được cấp. Ví dụ không chứa token thật hoặc dữ liệu trạm thật.

Trạng thái API trạm dùng cùng ngưỡng offline với giao diện: mất heartbeat quá 180 giây trả `status: "offline"`, `bps: 0`, `ntrip_connected: false`. Kiểm tra này áp dụng trên cả cache HIT/MISS, trước khi lọc trường theo quyền key. Trạm đang gửi heartbeat vẫn giữ trạng thái hiện tại, kể cả `awaiting_license`.

## Lưu và xem lại key

- `POST /api/admin/api-keys/{id}/token`: JSON `{"password":"<mật khẩu admin hiện tại>"}`. Kiểm tra admin đang hoạt động từ DB và xác minh mật khẩu mỗi request; trả `{"token":"cors_...","expires_in":30}` với `Cache-Control: no-store`. GET không được hỗ trợ. Sai mật khẩu trả 400; giới hạn 10 lượt xác nhận/phút/tài khoản trên mỗi tiến trình trả 429. Key thu hồi trả 404, key cũ chưa có bản mã hóa trả 409. Mật khẩu không được lưu trong trình duyệt, không truyền qua URL. Không có vé xem key tái sử dụng; 30 giây là thời gian hiển thị, không phải hạn dùng của key tích hợp.
- `PUT /api/admin/api-keys/{id}/token`: JSON `{"token":"cors_..."}`. Chỉ lưu nếu token khớp SHA-256 đang có; không đổi key đang dùng.
- Migration `20261009_api_key_reveal` bổ sung cột nullable. Kiểm tra schema khi khởi động cũng thêm cột bằng `IF NOT EXISTS`, hỗ trợ rollout trước Alembic. Chạy `alembic -c alembic_auth.ini upgrade head` để đồng bộ revision.
- Fernet dùng khóa suy ra từ `SECRET_KEY` với namespace `api-key`; giữ nguyên SECRET_KEY để đọc các token đã lưu. Không lưu token thô hay log token.
- Rollback code giữ nguyên cột và hash. Downgrade revision chỉ bỏ khả năng xem lại; xác thực bằng hash vẫn hoạt động. Token cũ không thể khôi phục từ hash nếu chưa lưu bản mã hóa hoặc không còn bản token ở phía tích hợp.
