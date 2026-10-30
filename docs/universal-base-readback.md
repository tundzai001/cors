# Đọc trạng thái Base trên Python universal agent

`geodetic/agent_universal.py` hỗ trợ `CHECK_BASE_STATUS` cho u-blox có CFG-TMODE3 và Unicore dùng MODE/BESTNAVA. Nút **Cập nhật ngay** trả về kết quả đọc chip, thay vì chỉ ghi log trên máy trạm. Timeout, checksum sai, chip không hỗ trợ hoặc reader đang bận trả command error.

Agent tự đọc lần đầu trong chu kỳ status tiếp theo sau khi khởi tạo và lặp lại mỗi 30 giây. Tạm bỏ qua polling khi có lệnh đang xử lý, Auto Base đang chạy hoặc thiết bị bị khóa toàn bộ. Việc đọc không đổi chế độ GNSS, không khởi động lại Survey-In và không bật NMEA.

Truy vấn được đưa vào hàng đợi giới hạn một mục của `GNSSReader`. Chính thread sở hữu cổng serial gửi truy vấn và tách phản hồi khỏi luồng RTCM/NMEA hiện tại; không mở thêm serial reader, không pause RTCM và không xóa input buffer. Mỗi truy vấn hết hạn sau 4 giây. Các log ASCII của UM dùng checksum XOR/CRC32; phản hồi UBX được kiểm tra header, độ dài, phiên bản và checksum.

## Dữ liệu dashboard

Agent bổ sung các trường tương thích với UI ESP32, không thay đổi những trường status đã có:

- `system_info.receiver_base_readback`: mode, tọa độ, revision tăng sau mỗi lượt đọc, tuổi kết quả và lỗi nếu có.
- `system_info.survey_in_status`: `waiting_base`, `base_ready`, `awaiting_readback` hoặc `not_monitoring`.
- `system_info.survey_in_result`: kết quả Survey-In đã xác nhận, lưu cùng cấu hình agent và giữ qua khởi động lại/chuyển Fixed LLA.

u-blox xác nhận hoàn tất khi NAV-SVIN `valid=1`, `active=0` và tọa độ hợp lệ. CFG-TMODE3 cung cấp thời gian tối thiểu và ngưỡng sai số thực tế. UI nhận tọa độ WGS84 dạng chuỗi để giữ độ chính xác phần lẻ.

UM dùng MODE để đọc cấu hình, BESTNAVA để đọc vị trí. Theo hợp đồng hiện tại của UI, chỉ `SOL_COMPUTED,FIXEDPOS` được coi là tọa độ Base đã xác nhận; `SINGLE` vẫn là vị trí tức thời. Có RTCM 1005/1006 không tự làm trạng thái này chuyển thành `base_ready`. Nếu một firmware UM tiếp tục báo SINGLE khi đang phát Base, cần đối chiếu thêm trạng thái đặc thù của model đó; không suy diễn lỗi ăng-ten từ trường này.

Kết quả quá 45 giây không được báo Survey-In sẵn sàng. Lượt đọc mới bị lỗi không dùng lại tọa độ trước đó để báo thành công. Kết quả Survey-In đã lưu vẫn được giữ để đối chiếu; bắt đầu Survey-In mới qua cấu hình sẽ xóa kết quả cũ và chỉ lưu lại khi có xác nhận mới. Không ghi lại file cấu hình chỉ vì timestamp trong header BESTNAV thay đổi.

## Triển khai và kiểm tra

Thay file agent trên trạm rồi khởi động lại dịch vụ agent theo cách vận hành hiện tại. Backend/dashboard hiện tại không cần đổi schema. Quay về file agent cũ sẽ ngừng phát các trường mới; cấu hình Base và dịch vụ hiện có vẫn tương thích. Thay đổi này không tự triển khai lên trạm.

Từ thư mục repo:

```sh
python3 -m unittest discover -s geodetic/tests -p test_receiver_base_readback.py
python3 -m unittest discover -s geodetic/tests -p test_auto_base_lifecycle.py
python3 -m unittest discover -s geodetic/tests -p test_status_publish.py
```

Các kiểm tra chạy parser và logic agent thật với UART giả lập: packet phân mảnh, RTCM xen kẽ, timeout/hủy, checksum, chuyển trạng thái, lưu kết quả, polling và phản hồi command. Cần kiểm tra thêm trên trạm thật với nút **Cập nhật ngay**, sau đó đối chiếu mode/tọa độ và tuổi kết quả trên dashboard.
