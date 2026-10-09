# device_lock theo server

Admin mở **Khoá host / server**. Nút gạt **Khoá Server 2** bật nghĩa là khoá Server 2; tắt nghĩa là bỏ khoá Server 2. Không đổi `server1_enabled`/`server2_enabled`, host/cổng, chế độ ngủ hoặc cấu hình dịch vụ. Tag ghi rõ server bị khoá, và chỉ báo đã áp dụng khi telemetry agent xác nhận; trước đó hiển thị chờ áp dụng.

Backend gửi `LOCK_DEVICE` với payload `{"locked_servers":[2]}`. `[1]` khoá S1; `[1,2]` khoá cả hai; `[]` mở cả hai. Agent lưu JSON trong `remote.lock`, chặn gửi RTCM của server được chọn kể cả fail-open; GNSS và server còn lại tiếp tục chạy. Lệnh khoá không restart dịch vụ. Lock tồn tại qua restart, thay đổi cấu hình dịch vụ không xoá lock. File khoá toàn trạm cũ vẫn được đọc và thực thi; lệnh scoped mới thay thế file đó.

Phải cập nhật `geodetic/agent_universal.py` trên trạm trước. Agent mới báo `system_info.device_lock.scoped=true` và danh sách khoá đang áp dụng. Backend từ chối khoá từng server trên agent chưa hỗ trợ để tránh agent cũ hiểu `LOCK_DEVICE` thành khoá toàn trạm. Các agent LTE/relay khác chưa báo capability này cũng không nhận scoped lock.

- `POST /api/devices/{serial}/lock`, JWT admin, body `{"locked_servers":[2]}`. Trả về `delivered`, `server_config_lock`, `service_config`. Payload cũ `allowed_servers` bị từ chối.
- `POST /api/devices/{serial}/unlock` giữ cho callers cũ: tương đương chọn `locked_servers=[]`. Giao diện không có nút mở khoá riêng; dùng nút gạt trong hộp thoại.
- Bulk/Zalo lock không truyền lựa chọn khoá cả hai server.

Khi có server bị khoá, người dùng và API key không được sửa host/cổng/cờ enabled hoặc chế độ stream. Server bị khoá cũng không được sửa mountpoint/tài khoản/mật khẩu/NTRIP version. Server còn lại vẫn sửa được tài khoản/mountpoint. Template tuân thủ cùng kiểm tra. Telemetry không thay chính sách admin hoặc ghi đè cấu hình dịch vụ đang bảo vệ.

`server_config_lock` lưu snapshot host/cổng/cờ enabled và `locked_servers`. Chính sách chưa gửi được giữ trong DB và retry khi kết nối lại. Mở khoá offline cũng được retry đến khi agent xác nhận. Các lần sửa/retry dùng khoá hàng DB. Cấu hình cũ từng bị tắt bằng `server_enabled=false` giữ nguyên, admin có thể bật lại trong Dịch vụ; mở device_lock không tự bật cấu hình vốn đã tắt.

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

Rollback về agent cũ khi còn scoped lock sẽ khiến file JSON được hiểu là khoá toàn trạm. Mở khoá và xác nhận trên agent trước khi rollback agent. Giữ cột JSONB khi rollback code để bảo toàn dữ liệu chính sách.

Kiểm tra: `tests.test_station_host_lock`, `geodetic/tests/test_scoped_device_lock.py`, `scripts/station_host_lock_selftest.js`, kiểm tra trình duyệt desktop/mobile. Các test DB/HTTP dùng mock; chưa xác minh trên thiết bị production.
