# Cơ chế "Sleep" của Trạm CORS (Raspberry Pi 4 - LAN - GNSS)

Tài liệu này giải thích cơ chế "Ngủ" (Sleep) của trạm CORS dựa trên phần cứng Raspberry Pi 4 Model B, kết nối mạng LAN và chip GNSS xuất dữ liệu liên tục.

Đây là cơ chế **"Ngủ bằng phần mềm" (Software-defined Sleep)**, mục đích chính là để **tiết kiệm băng thông mạng và giảm tải cho máy chủ NTRIP Caster**, chứ không phải là chế độ tiết kiệm điện năng phần cứng (Deep Sleep) như các thiết bị IoT chạy pin.

### 1. Thành phần nào vẫn hoạt động trong chế độ ngủ?
**Tất cả các phần cứng đều hoạt động 100%:**
*   **Raspberry Pi 4 và Cổng LAN (Ethernet):** Vẫn thức và duy trì kết nối mạng liên tục với máy chủ.
*   **Chip GNSS:** Vẫn hoạt động và liên tục xuất dữ liệu (NMEA, RTCM, UBX) qua cổng Serial (UART/USB).
*   **Tiến trình phần mềm (`agent_universal.py`):** Vòng lặp đọc cổng Serial (hàm đọc buffer) vẫn chạy liên tục để phân tích cú pháp các gói tin.
*   **Cơ chế "Ngủ" (Chặn dữ liệu):** Trong code, có một biến cờ toàn cục là `rtcm_stream_active_flag`. Khi trạm ở trạng thái "Sleep" (On-Demand nhưng chưa có client yêu cầu), biến này bằng `False`. Tại hàm `dispatch_rtcm_data()`, khi code nhận gói RTCM từ GNSS, nó sẽ kiểm tra cờ này. Nếu cờ là `False`, nó lập tức **thả trôi (drop) gói dữ liệu đó** (`return` ngay lập tức) thay vì đẩy vào hàng đợi (queue) để bắn lên máy chủ NTRIP.

### 2. Lệnh kích hoạt được nhận bằng giao thức mạng gì?
Lệnh kích hoạt từ máy chủ được nhận thông qua kiến trúc truyền thông kép nhằm đảm bảo độ tin cậy và thời gian thực:
*   **Giao thức chính (Primary):** Sử dụng **MQTT** (chạy trên nền TCP/IP qua mạng LAN). Raspberry Pi luôn duy trì một kết nối liên tục (socket) tới MQTT Broker. Khi có yêu cầu kết nối từ người dùng (Rover) vào trạm, máy chủ trung tâm sẽ gửi một bản tin MQTT chứa lệnh `SET_RTCM_STREAM_ACTIVE` với giá trị `active: true`.
*   **Giao thức dự phòng (Backup):** Hệ thống có cơ chế dự phòng bằng kết nối **WebSocket**. Trong trường hợp kết nối MQTT bị gián đoạn, cổng bị chặn, hoặc mạng gặp sự cố làm rớt gói tin MQTT, Raspberry Pi vẫn duy trì luồng giao tiếp thứ hai qua WebSocket (thường là qua cổng 80/443). Lệnh điều khiển trạng thái (Sleep/Streaming) có thể được máy chủ đẩy trực tiếp (push) qua kênh WebSocket này để kích hoạt trạm.
*   **Cơ chế đánh thức:** Ngay khi nhận được lệnh hợp lệ từ một trong hai kênh trên, tiến trình Python trên Pi 4 sẽ đổi cờ trạng thái phần mềm (`rtcm_stream_active_flag = True`). Dòng dữ liệu RTCM (vốn vẫn đang được chip GNSS liên tục xuất ra cổng Serial) sẽ ngay lập tức được hệ thống "mở cổng" (bỏ chặn) và đẩy vào hàng đợi để truyền lên máy chủ NTRIP Caster.

### 3. Có sử dụng mô-đun truyền thông tiêu thụ thấp luôn bật hay không?
*   **KHÔNG.** Vì hệ thống đang sử dụng cổng mạng LAN (Ethernet) cắm trực tiếp trên bo mạch Raspberry Pi 4. Mạng LAN và bản thân Pi 4 là các thiết bị tiêu thụ năng lượng tiêu chuẩn (vài Watts), không phải là mô-đun siêu tiết kiệm điện (Low-Power). Nó luôn bật để duy trì socket mạng.
*   **Giải trình kỹ thuật:** *"Hệ thống sử dụng mạng có dây (LAN) kết nối nguồn điện ổn định nên không yêu cầu tối ưu hóa năng lượng phần cứng. Chế độ Sleep ở đây được áp dụng ở tầng ứng dụng (Application Layer) nhằm chặn luồng dữ liệu dư thừa bắn lên máy chủ khi không có thiết bị Rover nào yêu cầu (chế độ On-Demand), qua đó tối ưu chi phí băng thông đường truyền (3G/4G/LAN) và hiệu năng xử lý của máy chủ trung tâm."*
