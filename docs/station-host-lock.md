# Khoá host/server của trạm

Admin mở **Khoá host / server** và chọn server được phép gửi. Ví dụ giữ Server 1, bỏ Server 2: cấu hình `server1_enabled=true`, `server2_enabled=false`; Server 2 ngừng gửi, Server 1 vẫn được phép gửi theo chế độ Always-on/On-demand hiện tại. Không gửi `LOCK_DEVICE`, không dừng GNSS hoặc khoá toàn bộ thiết bị. Agent hiện có đã tôn trọng cờ enabled, kể cả chế độ fail-open; không cần nâng agent để áp dụng chính sách này.

Sau khi khoá, người dùng và API key không được đổi host/cổng hoặc bật/tắt cả hai server, không được đổi chế độ gửi qua configure-service. Server bị chặn cũng không được sửa mountpoint, tài khoản, mật khẩu hoặc NTRIP version. Server còn được phép vẫn sửa được mountpoint/tài khoản; cấu hình GNSS và chức năng khác giữ nguyên quyền. Admin được sửa cấu hình và cập nhật chính sách. Áp template cũng phải tuân thủ khoá.

- `POST /api/devices/{serial}/lock`, JWT admin, body `{"allowed_servers":[1]}`. `[1,2]` cho phép cả hai, `[]` chặn cả hai. Host/cổng server được phép phải được cấu hình trước. Phản hồi có `delivered`, `server_config_lock`, `service_config`.
- `POST /api/devices/{serial}/unlock`, JWT admin: bỏ giới hạn sửa; giữ nguyên cấu hình bật/tắt, không tự bật server từng bị chặn.
- Các lời gọi bulk/Zalo cũ không có lựa chọn server giữ và khoá trạng thái bật/tắt hiện tại. Muốn chặn một host, chọn rõ trong giao diện mới.

`server_config_lock` là JSONB nullable, lưu snapshot host/cổng/cờ enabled do admin chọn, không chứa mật khẩu. Heartbeat không sửa cột này hoặc ghi đè cấu hình dịch vụ khi có chính sách. Ghi cấu hình và retry dùng khoá hàng DB để tránh một request cũ bật lại host vừa bị chặn. Trạm offline lưu chính sách; vòng kiểm tra đồng bộ gửi lại cấu hình khi có kết nối. Gửi lệnh thành công là đã chuyển lệnh qua MQTT/WebSocket; trạng thái đồng bộ/telemetry xác nhận agent áp dụng. Trạm từng bị khoá toàn bộ sẽ được gửi UNLOCK trước khi triển khai chính sách host mới, kể cả qua vòng retry khi kết nối lại.

## Migration và rollback

Revision `20261009_station_host_lock`, sau `20261001_unique_rtcm_client_leases`, chỉ thêm cột nullable; không sửa dữ liệu cũ hay cờ `is_locked`. Backend không tự thêm cột `server_config_lock` khi khởi động. Chạy migration trước khi khởi động backend mới, từ `cors_dashboard/backend`:

```sh
alembic -c alembic_devices.ini upgrade head
```

Migration này chạy trên DB CORS/devices theo `DATABASE_URL`, không phải DB Auth (`AUTH_DATABASE_URL`). Nếu thêm cột thủ công trong Query Tool của pgAdmin, chọn DB CORS có bảng `devices` rồi chạy:

```sql
ALTER TABLE devices ADD COLUMN IF NOT EXISTS server_config_lock JSONB;
```

Sau khi chạy SQL thủ công vẫn có thể chạy lệnh Alembic trên để ghi nhận revision: `IF NOT EXISTS` giữ nguyên cột đã có.

Rollback code giữ cột và cấu hình server đang tắt; code cũ không thực thi giới hạn sửa và nút khoá cũ lại khoá toàn thiết bị. Không coi rollback code là bảo toàn bảo vệ host. Downgrade schema chỉ bỏ snapshot chính sách, không bật lại server hay xoá cấu hình dịch vụ. Chỉ downgrade khi đã chủ động bỏ chính sách; giữ cột khi rollback là lựa chọn bảo toàn dữ liệu.

Kiểm tra: `tests.test_station_host_lock`, `scripts/station_host_lock_selftest.js`. Test HTTP/DB dùng mock và AST các handler thật; test agent chạy hàm quyết định phát thật, không cần thiết bị vật lý. Chưa xác minh trạm production.
