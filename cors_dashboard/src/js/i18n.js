(() => {
    const STORAGE_KEY = 'aitogy_language';
    const ATTRIBUTE_NAMES = ['placeholder', 'title', 'aria-label', 'alt'];
    const TEXT_SOURCES = new WeakMap();
    const TEXT_OUTPUTS = new WeakMap();
    const ATTRIBUTE_SOURCES = new WeakMap();
    const ATTRIBUTE_OUTPUTS = new WeakMap();
    const translations = [
        ['Hệ thống quản lý và giám sát mạng lưới trạm định vị vệ tinh CORS trên toàn lãnh thổ Việt Nam.', 'A system for managing and monitoring the CORS satellite positioning network across Vietnam.'],
        ['Cung cấp hạ tầng RTK/NTRIP chuyên nghiệp cho khảo sát, đo đạc và tự động hóa.', 'Professional RTK/NTRIP infrastructure for surveying, measurement, and automation.'],
        ['Nền tảng CORS duy nhất được thiết kế và vận hành bởi đội ngũ kỹ sư Việt Nam, tối ưu cho điều kiện thực tế trong nước.', 'The only CORS platform designed and operated by Vietnamese engineers, optimized for local conditions.'],
        ['Hệ thống tích hợp toàn diện 4 hệ thống vệ tinh GPS, GLONASS, Galileo và BeiDou. Khả năng thu nhận tín hiệu đa tần số giúp loại bỏ sai số tầng điện ly, đảm bảo độ chính xác mức centimet ngay cả trong môi trường đô thị dày đặc hoặc tán cây.', 'The system integrates four satellite constellations: GPS, GLONASS, Galileo, and BeiDou. Multi-frequency signals reduce ionospheric errors and deliver centimeter-level accuracy, even in dense urban areas or under tree cover.'],
        ['Hạ tầng truyền dẫn RTCM 3.x thời gian thực với băng thông cực lớn. Hệ thống cân bằng tải thông minh đảm bảo độ trễ luôn dưới 200ms trên toàn quốc, hỗ trợ kết nối đồng thời hàng ngàn thiết bị Rover mà không gây tắc nghẽn.', 'Real-time RTCM 3.x delivery with high bandwidth. Intelligent load balancing keeps latency below 200 ms nationwide and supports thousands of simultaneous Rover connections without congestion.'],
        ['Hệ thống AI tự động phân tích sức khỏe trạm Base theo giây. Tự động chuyển đổi sang trạm dự phòng (Failover) khi phát hiện sự cố. Hệ thống cảnh báo qua Telegram giúp kỹ thuật viên xử lý kịp thời mọi tình huống.', 'AI checks Base station health every second and switches to a backup station when it detects a fault. Telegram alerts help technicians respond quickly.'],
        ['Toàn bộ luồng dữ liệu được mã hóa TLS/SSL. Cơ chế phân quyền chi tiết cho phép quản lý từng tài khoản Rover, giới hạn khu vực hoạt động (Geofencing) và thời gian truy cập, bảo vệ tuyệt đối dữ liệu hạ tầng.', 'All data is encrypted with TLS/SSL. Detailed permissions manage each Rover account and restrict operating areas (geofencing) and access times to protect infrastructure data.'],
        ['Liên hệ đội ngũ Aitogy để được tư vấn kiến trúc, hướng dẫn triển khai và hỗ trợ vận hành thực tế.', 'Contact Aitogy for architecture advice, deployment guidance, and operational support.'],
        ['Từ thu tín hiệu vệ tinh đến hiệu chỉnh RTCM và phân phối NTRIP, toàn bộ hệ thống được tinh chỉnh cho độ trễ thấp và tính sẵn sàng cao.', 'From satellite reception to RTCM corrections and NTRIP delivery, the entire system is tuned for low latency and high availability.'],
        ['Dữ liệu được xử lý real-time, tạo ra bản tin RTCM chứa thông số hiệu chỉnh sai số khí quyển và quỹ đạo.', 'Data is processed in real time to generate RTCM messages that correct atmospheric and orbital errors.'],
        ['Bản tin hiệu chỉnh được gửi tới Caster và phân phối tới Rover qua mạng di động với độ trễ cực thấp.', 'Correction messages are sent to the caster and delivered to Rovers over mobile networks with very low latency.'],
        ['Trạm CORS thu tín hiệu từ 4 hệ thống vệ tinh: GPS, GLONASS, Galileo, BeiDou với anten đa tần số chuyên dụng.', 'CORS stations receive signals from GPS, GLONASS, Galileo, and BeiDou using dedicated multi-frequency antennas.'],
        ['Rover kết hợp dữ liệu vệ tinh và hiệu chỉnh để giải bài toán RTK, đạt độ chính xác mức mm-cm.', 'Rovers combine satellite and correction data to solve RTK positioning, achieving millimeter-to-centimeter accuracy.'],
        ['Kiến trúc mở rộng ngang theo khu vực để giảm nghẽn cổ chai trong các đợt vận hành cao điểm.', 'The architecture scales horizontally by region to prevent bottlenecks during peak usage.'],
        ['Mỗi trạm được kiểm tra sức khỏe theo chu kỳ ngắn để cảnh báo sớm trước khi rover mất FIX.', 'Stations are checked frequently to warn operators before a Rover loses FIX.'],
        ['Dữ liệu trạm Base được lọc, chuẩn hóa sang RTCM và phân phối song song tới nhiều mountpoint.', 'Base station data is filtered, normalized into RTCM, and distributed to multiple mountpoints in parallel.'],
        ['Hạ tầng CORS quy mô quốc gia cho khảo sát, xây dựng và giám sát với độ chính xác cao, vận hành ổn định.', 'Nationwide CORS infrastructure for high-precision surveying, construction, and monitoring with reliable operation.'],
        ['Đội ngũ vận hành trực 24 giờ, xử lý sự cố trong vòng 15 phút qua Telegram và hotline.', 'Our operations team is on call 24/7 and responds to incidents within 15 minutes through Telegram and our hotline.'],
        ['Doanh nghiệp khảo sát, xây dựng và viễn thám trên toàn quốc đã lựa chọn nền tảng Aitogy.', 'Surveying, construction, and remote-sensing organizations nationwide rely on the Aitogy platform.'],
        ['Triển khai và vận hành hạ tầng GNSS/CORS quy mô quốc gia với đội ngũ kỹ sư chuyên sâu.', 'Deploying and operating nationwide GNSS/CORS infrastructure with a specialized engineering team.'],
        ['Vui lòng nhập đầy đủ thông tin!', 'Please fill in all required fields!'],
        ['Tỉnh/TP trên toàn quốc', 'Provinces and cities nationwide'],
        ['Mạng lưới CORS Aitogy', 'Aitogy CORS Network'],
        ['Mạng lưới CORS Việt Nam', 'Vietnam CORS Network'],
        ['Bản đồ mạng lưới CORS Việt Nam', 'Vietnam CORS Network Map'],
        ['Mạng CORS', 'CORS Network'],
        ['Mạng lưới', 'Network'],
        ['Bài viết', 'Articles'],
        ['Tài liệu API', 'API Documentation'],
        ['Hạ tầng GNSS thế hệ mới', 'Next-Generation GNSS Infrastructure'],
        ['Trung tâm điều hành CORS', 'CORS Operations Center'],
        ['Định vị chính xác centimet', 'Centimeter-Level Positioning'],
        ['Giải pháp GNSS toàn diện', 'Complete GNSS Solutions'],
        ['Thiết bị và hạ tầng CORS chính xác cao', 'High-Precision CORS Equipment and Infrastructure'],
        ['Hệ thống trạm tham chiếu hoạt động liên tục toàn quốc', 'Nationwide Reference Stations Operating Around the Clock'],
        ['Tính năng nổi bật', 'Key Features'],
        ['Nền tảng CORS thế hệ mới cho hạ tầng đo đạc Việt Nam', 'A next-generation CORS platform for Vietnam’s surveying infrastructure'],
        ['Năm kinh nghiệm', 'Years of Experience'],
        ['Đối tác tin dùng', 'Trusted by Our Partners'],
        ['Hỗ trợ kỹ thuật', 'Technical Support'],
        ['Đồng hành cùng Việt Nam', 'Supporting Vietnam'],
        ['Tại sao chọn Aitogy?', 'Why Choose Aitogy?'],
        ['TRUNG TÂM ĐIỀU HÀNH CORS', 'CORS OPERATIONS CENTER'],
        ['Tên đăng nhập', 'Username'],
        ['Ghi nhớ đăng nhập', 'Remember me'],
        ['Đang xử lý...', 'Processing...'],
        ['Đăng nhập thất bại', 'Login failed'],
        ['Tổng số trạm', 'Total Stations'],
        ['Khu vực phủ sóng', 'Coverage Area'],
        ['Tỷ lệ hoạt động', 'Uptime'],
        ['Đa chòm sao vệ tinh', 'Multi-Constellation GNSS'],
        ['Theo dõi GPS, GLONASS, Galileo, BeiDou để tối ưu độ chính xác định vị.', 'Track GPS, GLONASS, Galileo, and BeiDou for optimal positioning accuracy.'],
        ['NTRIP Caster tốc độ cao', 'High-Speed NTRIP Caster'],
        ['Truyền dữ liệu RTK/RTCM tối ưu với độ trễ cực thấp.', 'Optimized RTK/RTCM delivery with ultra-low latency.'],
        ['Giám sát 24/7', '24/7 Monitoring'],
        ['Tự động cảnh báo và phục hồi khi phát hiện sự cố, đảm bảo uptime tối đa.', 'Automatic alerts and recovery help maximize uptime.'],
        ['Bảo mật cấp doanh nghiệp', 'Enterprise-Grade Security'],
        ['Quản lý phân quyền đa cấp, mã hóa dữ liệu end-to-end.', 'Tiered access control and end-to-end data encryption.'],
        ['Vào bảng điều khiển', 'Open Dashboard'],
        ['Truy cập hệ thống', 'Access the System'],
        ['Xem bản đồ trạm', 'View Station Map'],
        ['Vệ tinh', 'Satellites'],
        ['Độ trễ', 'Latency'],
        ['Quy trình vận hành', 'How It Works'],
        ['Từ vệ tinh đến rover - chỉ trong vài mili giây', 'From satellite to Rover in just a few milliseconds'],
        ['Thu tín hiệu vệ tinh', 'Satellite Signal Reception'],
        ['Xử lý & hiệu chỉnh', 'Processing & Corrections'],
        ['Truyền dẫn NTRIP', 'NTRIP Delivery'],
        ['Định vị centimet', 'Centimeter-Level Positioning'],
        ['Dữ liệu thời gian thực', 'Real-Time Data'],
        ['Lưu lượng mạng toàn hệ thống', 'System-Wide Network Traffic'],
        ['Trực tiếp: RTCM 3.2 MSM7', 'Live: RTCM 3.2 MSM7'],
        ['Độ ổn định Caster', 'Caster Stability'],
        ['Độ ổn định hệ thống', 'System Stability'],
        ['Công nghệ & giao thức hỗ trợ', 'Supported Technologies & Protocols'],
        ['Chuỗi xử lý GNSS tối ưu cho vận hành thực tế', 'GNSS Processing Built for Real-World Operations'],
        ['Chuỗi dữ liệu', 'Data Pipeline'],
        ['Bộ phân tích RTCM MSM thời gian thực', 'Real-Time RTCM MSM Parser'],
        ['Mountpoint dự phòng khi caster quá tải', 'Backup mountpoints prevent caster overload'],
        ['Ưu tiên định tuyến mạng ít dao động', 'Routes prioritize stable network paths'],
        ['Khả năng mở rộng', 'Scalability'],
        ['Mở rộng ngang theo cụm tỉnh', 'Horizontal scaling by province cluster'],
        ['Cân bằng phiên rover đồng thời', 'Balances concurrent Rover sessions'],
        ['Giám sát SNR và mất gói liên tục', 'Continuous SNR and packet-loss monitoring'],
        ['Độ tin cậy vận hành', 'Operational Reliability'],
        ['Mục tiêu uptime 99.9%', '99.9% Uptime Target'],
        ['Cảnh báo đa kênh cho đội vận hành', 'Multi-channel alerts for operations teams'],
        ['Nhật ký kiểm tra cho điều tra sự cố', 'Audit logs for incident investigations'],
        ['Sẵn sàng nâng cấp hạ tầng trắc địa?', 'Ready to upgrade your surveying infrastructure?'],
        ['Bắt đầu ngay', 'Get Started'],
        ['Tư vấn kỹ thuật', 'Technical Consultation'],
        ['Số 16B Ngõ 187 phố Mai Dịch, Phường Phú Diễn, Hà Nội', 'No. 16B, Alley 187, Mai Dich Street, Phu Dien Ward, Hanoi, Vietnam'],
        ['Danh mục trạm CORS', 'CORS Station Directory'],
        ['trạm có tọa độ trên toàn Việt Nam', 'mapped stations across Vietnam'],
        ['trạm có tọa độ bản đồ, băng thông lớn và kết nối ổn định toàn quốc', 'mapped stations with high bandwidth and reliable nationwide connectivity'],
        ['trạm toàn mạng', 'stations across the network'],
        ['trạm có tọa độ khác', 'more mapped stations'],
        ['Quần đảo Hoàng Sa', 'Paracel Islands'],
        ['Quần đảo Trường Sa', 'Spratly Islands'],
        ['Trạng thái mạng', 'Network Status'],
        ['Kiến thức', 'Resources'],
        ['Nền tảng', 'Platform'],
        ['Liên hệ', 'Contact'],
        ['© 2025 Aitogy Network. Bảo lưu mọi quyền.', '© 2025 Aitogy Network. All rights reserved.'],
        ['Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao GNSS/RTK', 'Aitogy CORS Station System | High-Precision GNSS/RTK Positioning'],
        ['Quản lý tài khoản rover, mountpoint, kết nối live và blocklist trên caster.', 'Manage rover accounts, mountpoints, live connections, and the caster blocklist.'],
        ['Chạm vào một ngày để xem receiver và phiên đo.', 'Select a day to view receivers and measurement sessions.'],
        ['Mỗi receiver được tính là một máy; nhiều client dùng cùng receiver chỉ tính một lần.', 'Each unique receiver counts as one device; multiple clients using the same receiver count once.'],
        ['Mỗi receiver khác nhau được tính là một máy.', 'Each unique receiver counts as one device.'],
        ['Bản đồ tuyến điểm FIX theo receiver', 'FIX track map by receiver'],
        ['Máy đo theo receiver', 'Devices grouped by receiver'],
        ['Danh sách điểm FIX', 'FIX point list'],
        ['Phiên này không có điểm FIX hợp lệ.', 'This session has no valid FIX points.'],
        ['Bản đồ chưa tải được. Danh sách tọa độ vẫn có thể mở trên Google Maps.', 'The map could not load. Coordinates can still be opened in Google Maps.'],
        ['Không thể dựng bản đồ. Danh sách tọa độ vẫn có thể mở trên Google Maps.', 'Could not render the map. Coordinates can still be opened in Google Maps.'],
        ['Tọa độ (vĩ độ, kinh độ):', 'Coordinates (latitude, longitude):'],
        ['Điểm gần nhau có thể chồng; bấm + để phóng to.', 'Nearby points may overlap; press + to zoom in.'],
        ['Lọc theo receiver hoặc tài khoản', 'Filter by receiver or account'],
        ['Nhập receiver / username', 'Enter receiver / username'],
        ['Chọn receiver để xem đường đi của các điểm FIX.', 'Select a receiver to view its FIX track.'],
        ['Không có receiver trong ngày này', 'No receivers on this day'],
        ['Không có máy đo trong ngày này.', 'No receivers recorded on this day.'],
        ['Không ghi nhận phiên nào có receiver trong ngày được chọn.', 'No sessions were recorded for the selected day.'],
        ['Không rõ tài khoản', 'Unknown account'],
        ['Chọn khoảng ngày hợp lệ để lọc.', 'Choose a valid date range to filter.'],
        ['Không tải được hoạt động.', 'Could not load activity.'],
        ['Đang tính số receiver hoạt động…', 'Counting active receivers…'],
        ['Đang tải danh sách máy…', 'Loading receivers…'],
        ['Đang tải máy đo…', 'Loading receivers…'],
        ['Đang vẽ tuyến FIX…', 'Drawing FIX track…'],
        ['Đang tải điểm FIX…', 'Loading FIX points…'],
        ['Chưa có receiver hoạt động.', 'No active receivers.'],
        ['Không có receiver trong ngày này.', 'No receivers on this day.'],
        ['Chưa có điểm FIX trong ngày.', 'No FIX points on this day.'],
        ['Máy có phiên hoạt động nhưng chưa lưu điểm RTK Fixed trong ngày.', 'This receiver had active sessions but no RTK Fixed points were saved today.'],
        ['Các điểm RTK Fixed sẽ được nối theo thứ tự ghi nhận.', 'RTK Fixed points are connected in recording order.'],
        ['Các điểm RTK Fixed sẽ được nối theo thời gian.', 'RTK Fixed points are connected in time order.'],
        ['Hệ thống trạm CORS Aitogy', 'Aitogy CORS Station System'],
        ['Trung tâm điều khiển CORS', 'CORS Control Center'],
        ['Số máy theo ngày', 'Receivers per day'],
        ['Receiver trong ngày', 'Receivers on selected day'],
        ['Chọn ngày để xem các máy đã kết nối.', 'Select a day to view connected receivers.'],
        ['Lọc điểm theo phiên', 'Filter points by session'],
        ['Tất cả phiên', 'All sessions'],
        ['Xem toàn bộ trên bản đồ', 'View all on map'],
        ['Đường xanh nối điểm FIX theo thứ tự đo.', 'The blue line connects FIX points in measurement order.'],
        ['Chỉ đường Google Maps:', 'Google Maps directions:'],
        ['Điểm đo đã lưu', 'Saved measurement points'],
        ['Vị trí cuối theo phiên', 'Last location per session'],
        ['Phiên kết nối, vị trí cuối và hoạt động gần đây', 'Connection sessions, last position, and recent activity'],
        ['Chưa lưu điểm đo nào trong phiên này.', 'No measurement points were saved in this session.'],
        ['Chưa ghi nhận tọa độ RTK Fixed cho phiên này.', 'No RTK Fixed coordinates were recorded for this session.'],
        ['Tự lưu RTK Fixed đang bật', 'RTK Fixed auto-save is on'],
        ['Tự lưu RTK Fixed đang tắt cho tài khoản này.', 'RTK Fixed auto-save is off for this account.'],
        ['Tạo rover', 'Create rover'],
        ['Quản lý tài khoản Rover, mountpoint và kết nối caster.', 'Manage Rover accounts, mountpoints, and caster connections.'],
        ['Bảng điều khiển', 'Dashboard'],
        ['Bản đồ trạm', 'Station Map'],
        ['Bảng điều khiển Rover', 'Rover Dashboard'],
        ['Giám sát hệ thống', 'System Monitor'],
        ['Danh sách trạm', 'Station list'],
        ['Bản đồ toàn hệ thống', 'System-wide map'],
        ['Bản đồ Trạm CORS Toàn Quốc', 'National CORS Station Map'],
        ['Quay lại danh sách trạm', 'Back to station list'],
        ['Vận hành', 'Operate'],
        ['Hoạt động', 'Operations'],
        ['Bảo mật', 'Security'],
        ['Người dùng', 'Users'],
        ['Quản trị', 'Admin'],
        ['Hệ thống', 'System'],
        ['Đã kết nối', 'Connected'],
        ['Quản trị viên', 'Administrator'],
        ['Điều phối viên', 'Coordinator'],
        ['Người xem', 'Viewer'],
        ['Đăng xuất', 'Logout'],
        ['Đăng nhập', 'Login'],
        ['Tìm username, tên hoặc vai trò', 'Search username, name, role'],
        ['Tất cả vai trò', 'All roles'],
        ['Tất cả trạng thái', 'All status'],
        ['Tài khoản đang hoạt động', 'Active account'],
        ['Để trống nếu muốn giữ nguyên', 'Leave blank to keep current'],
        ['Quản lý Rover (quyền truy cập trang)', 'Manage Rovers (page access)'],
        ['Tạo và sửa Rover', 'Create & edit rovers'],
        ['Có thể tạo người dùng', 'Can create users'],
        ['Lọc mountpoint', 'Filter mountpoints'],
        ['Hệ thống đang bật', 'System is on'],
        ['Quyền của group', 'Group permissions'],
        ['Group public — chỉ tra cứu', 'Public group — read-only'],
        ['Group admin — toàn quyền', 'Admin group — full access'],
        ['Phạm vi trạm', 'Station scope'],
        ['Tất cả trạm', 'All stations'],
        ['Chỉ các trạm được chọn', 'Selected stations only'],
        ['Không cho xem trạm nào', 'No stations allowed'],
        ['Tạo OTP group 30 giây', 'Generate 30-second group OTP'],
        ['Chọn trạm cho group public', 'Select stations for the public group'],
        ['Sao chép', 'Copy'],
        ['Tìm tên chat, tài khoản hoặc Chat ID', 'Search chat name, account, or Chat ID'],
        ['Cấu hình chat Zalo', 'Zalo chat settings'],
        ['Tên hiển thị', 'Display name'],
        ['Loại quyền', 'Permission type'],
        ['Theo tài khoản dashboard', 'Use dashboard account'],
        ['Group quản trị', 'Admin group'],
        ['Group chỉ tra cứu', 'Read-only group'],
        ['Kích hoạt liên kết', 'Enable binding'],
        ['Nhận cảnh báo', 'Receive alerts'],
        ['Mức tối thiểu', 'Minimum level'],
        ['Bắt đầu im lặng', 'Quiet hours start'],
        ['Kết thúc im lặng', 'Quiet hours end'],
        ['Vẫn gửi cảnh báo nghiêm trọng trong giờ im lặng', 'Send critical alerts during quiet hours'],
        ['Lưu cấu hình', 'Save settings'],
        ['Trung tâm điều hành - Trung tâm điều khiển CORS', 'Operations Center - CORS Control Center'],
        ['Chặn Truy Cập', 'Block access'],
        ['Địa chỉ IP', 'IP address'],
        ['Phát hiện VPN', 'VPN detection'],
        ['Phân tích mẫu', 'Pattern analysis'],
        ['Tường lửa', 'Firewall'],
        ['Từ ngày', 'From'],
        ['Đến ngày', 'To'],
        ['Lọc ngày', 'Filter dates'],
        ['Hôm nay', 'Today'],
        ['Hôm qua', 'Yesterday'],
        ['điểm RTK Fixed', 'RTK Fixed points'],
        ['điểm FIX', 'FIX points'],
        ['receiver duy nhất', 'unique receivers'],
        ['Số receiver', 'Receivers'],
        ['Tổng số phiên', 'Total sessions'],
        ['Phiên hoạt động', 'Active sessions'],
        ['Máy chủ', 'Server'],
        ['Bắt đầu', 'Started'],
        ['Kết thúc', 'Ended'],
        ['Đang hoạt động', 'Active'],
        ['Đã kết thúc', 'Ended'],
        ['Vĩ độ', 'Latitude'],
        ['Kinh độ', 'Longitude'],
        ['Bản đồ', 'Map'],
        ['Tìm kiếm', 'Search'],
        ['Lọc', 'Filter'],
        ['Làm mới', 'Refresh'],
        ['Lưu thay đổi', 'Save changes'],
        ['Hủy bỏ', 'Cancel'],
        ['Hủy', 'Cancel'],
        ['Đóng', 'Close'],
        ['Xóa', 'Delete'],
        ['Sửa', 'Edit'],
        ['Thêm người dùng', 'Add user'],
        ['Thêm', 'Add'],
        ['Tài khoản', 'Account'],
        ['Vai trò', 'Role'],
        ['Trạng thái', 'Status'],
        ['Mật khẩu', 'Password'],
        ['Họ và tên', 'Full name'],
        ['Thông tin định danh', 'Identity'],
        ['Vai trò & Trạng thái', 'Role & Status'],
        ['Quyền trạm', 'Station permissions'],
        ['Quyền truy cập Rover', 'Rover access'],
        ['Quyền truy cập Mountpoint', 'Mountpoint access'],
        ['Đang tải', 'Loading'],
        ['Thành công', 'Success'],
        ['Thất bại', 'Failed'],
        ['Không có dữ liệu', 'No data'],
        ['Không có', 'None'],
        ['API cho tích hợp giám sát CORS, trạng thái mạng và dữ liệu vận hành GNSS theo thời gian thực.', 'An API for integrating CORS monitoring, network status, and real-time GNSS operations data.'],
        ['Đăng ký tài khoản và lấy API token theo môi trường (staging/production).', 'Register an account and get an API token for the staging or production environment.'],
        ['Gọi /api/public/stations để lấy danh sách trạm khả dụng theo khu vực.', 'Call /api/public/stations to get the stations available in a region.'],
        ['Theo dõi /api/public/stats và /api/public/network-health để hiển thị trạng thái realtime.', 'Use /api/public/stats and /api/public/network-health to display live status.'],
        ['Cache dữ liệu read-heavy theo chu kỳ 10-30 giây để giảm tải client.', 'Cache read-heavy data for 10–30 seconds to reduce client load.'],
        ['Danh sách trạm CORS public (không lộ thông tin nhạy cảm).', 'A public list of CORS stations with sensitive information excluded.'],
        ['Lọc theo tỉnh/thành', 'Filter by province or city'],
        ['Chỉ số realtime của toàn mạng CORS.', 'Real-time metrics for the entire CORS network.'],
        ['Tổng hợp chất lượng theo vùng và theo giao thức truyền.', 'A summary of quality by region and transmission protocol.'],
        ['Tải OpenAPI Spec', 'Download OpenAPI spec'],
        ['Liên hệ hỗ trợ Dev', 'Contact developer support'],
        ['Public API ưu tiên tương thích ngược. Thay đổi breaking sẽ được thông báo trước và có giai đoạn song song phiên bản.', 'The public API prioritizes backward compatibility. Breaking changes are announced in advance, with a period when both versions remain available.'],
        ['Các mã lỗi phổ biến:', 'Common error codes:'],
        ['Client nên implement retry có backoff cho lỗi tạm thời và log request-id để truy vết với đội vận hành.', 'Clients should retry transient errors with backoff and log the request ID for troubleshooting with the operations team.'],
        ['Cổng thông tin nhà phát triển', 'Developer Portal'],
        ['Bắt đầu nhanh', 'Quickstart'],
        ['URL cơ sở', 'Base URL'],
        ['Xác thực', 'Auth'],
        ['Phiên bản và tính ổn định', 'Versioning & Stability'],
        ['Tham số', 'Parameters'],
        ['Tên', 'Name'],
        ['Kiểu', 'Type'],
        ['Mô tả', 'Description'],
        ['Không yêu cầu tham số.', 'No parameters required.'],
        ['Phản hồi mẫu', 'Example Response'],
        ['Xử lý lỗi', 'Error Handling'],
        ['Kho tri thức kỹ thuật', 'Technical Knowledge Base'],
        ['NTRIP Caster là gì? Toàn tập về pipeline RTK', 'What Is an NTRIP Caster? A Complete Guide to the RTK Pipeline'],
        ['Giải thích kiến trúc NTRIP Caster, luồng dữ liệu RTCM, mô hình phân phối mountpoint và các lỗi hay gặp khi triển khai.', 'An overview of NTRIP Caster architecture, RTCM data flow, mountpoint distribution, and common deployment issues.'],
        ['Kiến trúc hệ thống', 'System Architecture'],
        ['Từ Base đến Rover: chuỗi xử lý đầy đủ', 'From Base to Rover: The Complete Processing Chain'],
        ['Dữ liệu GNSS thô tại trạm Base được chuẩn hóa theo RTCM, đẩy lên Caster, sau đó phân phối về Rover theo mountpoint phù hợp khu vực vận hành.', 'Raw GNSS data from Base stations is normalized into RTCM, sent to the Caster, then distributed to Rovers through the mountpoint best suited to the operating area.'],
        ['Điểm nghẽn thường xuất hiện ở đâu?', 'Where Do Bottlenecks Usually Occur?'],
        ['Trong thực tế, nút thắt thường nằm ở mạng truyền dẫn (jitter cao), cấu hình reconnect chưa tối ưu, hoặc phân bổ mountpoint chưa đúng mật độ rover.', 'In practice, bottlenecks often stem from high network jitter, poorly optimized reconnect settings, or mountpoint allocation that does not match Rover density.'],
        ['Checklist vận hành ổn định', 'Stable Operation Checklist'],
        ['Tách cụm mountpoint theo vùng địa lý', 'Split mountpoint groups by geographic region'],
        ['Giám sát packet loss, latency và số rover đồng thời', 'Monitor packet loss, latency, and concurrent Rovers'],
        ['Dự phòng Caster để failover nhanh khi tải tăng đột biến', 'Keep a backup Caster for fast failover during sudden traffic spikes'],
        ['Hướng dẫn kết nối CORS cho Drone và máy thi công', 'How to Connect to CORS for Drones and Construction Equipment'],
        ['Quy trình cấu hình tài khoản NTRIP, kiểm tra FIX, tối ưu độ trễ và xử lý các lỗi mất fix khi làm việc ngoài thực địa.', 'Configure NTRIP accounts, verify FIX, optimize latency, and troubleshoot FIX loss in the field.'],
        ['Hướng dẫn triển khai', 'Deployment Guide'],
        ['Thiết lập kết nối theo từng bước', 'Set Up the Connection Step by Step'],
        ['Ưu tiên kiểm tra chất lượng mạng 4G/5G trước khi xác nhận cấu hình NTRIP để tránh lỗi FIX giả do truyền dẫn không ổn định.', 'Check 4G/5G network quality before confirming the NTRIP configuration to avoid false FIX status caused by an unstable connection.'],
        ['Thông số nên giám sát liên tục', 'Metrics to Monitor Continuously'],
        ['Latency trung bình dưới 200ms', 'Average latency below 200 ms'],
        ['Số vệ tinh theo từng chòm GPS/GLO/GAL/BDS', 'Satellite counts for GPS/GLO/GAL/BDS constellations'],
        ['Chu kỳ mất FIX và thời gian phục hồi', 'FIX loss frequency and recovery time'],
        ['Khi nào cần đổi mountpoint?', 'When Should You Change the Mountpoint?'],
        ['Khi rover dịch chuyển xa khỏi vùng phủ tối ưu hoặc chất lượng hiệu chỉnh giảm, nên chuyển mountpoint gần cụm trạm Base hơn để ổn định nghiệm.', 'If a Rover moves beyond optimal coverage or correction quality drops, switch to a mountpoint closer to a Base station cluster to stabilize the solution.'],
        ['Giám sát SNR và trạng thái RTK để giảm downtime', 'Monitor SNR and RTK Status to Reduce Downtime'],
        ['Mô hình cảnh báo sớm dựa trên SNR, latency và packet loss giúp đội vận hành can thiệp trước khi rover mất FIX hàng loạt.', 'Early warnings based on SNR, latency, and packet loss help operations teams respond before multiple Rovers lose FIX.'],
        ['Vận hành & giám sát', 'Operations & Monitoring'],
        ['Vì sao chỉ nhìn trạng thái Online là chưa đủ?', 'Why Is Online Status Alone Not Enough?'],
        ['Trạm có thể Online nhưng chất lượng hiệu chỉnh đã suy giảm. Cần phối hợp SNR, latency và tỉ lệ reconnect để đánh giá đúng health vận hành.', 'A station can be online while correction quality is already declining. Combine SNR, latency, and reconnect rates to assess operational health accurately.'],
        ['Ngưỡng cảnh báo đề xuất', 'Suggested Alert Thresholds'],
        ['SNR trung bình giảm liên tục trong 10-15 phút', 'Average SNR keeps declining for 10–15 minutes'],
        ['Latency tăng > 250ms theo cụm rover', 'Latency exceeds 250 ms across a Rover group'],
        ['Số lần reconnect tăng bất thường trong 1 giờ', 'Reconnects increase abnormally within one hour'],
        ['Bảo mật truyền dẫn CORS: TLS, phân quyền và audit', 'CORS Transport Security: TLS, Access Control, and Auditing'],
        ['Thiết kế lớp bảo mật cho hạ tầng CORS theo hướng thực dụng: mã hóa, tách quyền và theo dõi truy cập có truy vết.', 'A practical security design for CORS infrastructure: encryption, separated permissions, and traceable access monitoring.'],
        ['Bảo mật hạ tầng', 'Infrastructure Security'],
        ['Tối thiểu cần những lớp bảo mật nào?', 'What Are the Minimum Required Security Layers?'],
        ['Một hệ CORS production cần có TLS đầu cuối, kiểm soát quyền theo vai trò và nhật ký truy cập đủ chi tiết để phục vụ điều tra sự cố.', 'A production CORS system needs end-to-end TLS, role-based access control, and detailed access logs for incident investigations.'],
        ['Nguyên tắc phân quyền', 'Access Control Principles'],
        ['Ẩn định danh thiết bị nhạy cảm với người dùng public', 'Hide sensitive device identifiers from public users'],
        ['Tách quyền Viewer / Operator / Admin rõ ràng', 'Clearly separate Viewer, Operator, and Admin permissions'],
        ['Luôn có audit trail cho thao tác thay đổi cấu hình', 'Keep an audit trail for configuration changes'],
        ['Đăng ngày', 'Published on'],
        ['8 phút', '8 minutes'],
        ['6 phút', '6 minutes'],
        ['7 phút', '7 minutes'],
        ['9 phút', '9 minutes'],
        ['Tư vấn kiến trúc CORS theo khu vực triển khai', 'CORS Architecture Consultation by Deployment Region'],
        ['Đội kỹ thuật Aitogy hỗ trợ dựng luồng NTRIP, mountpoint và phương án giám sát theo tải thực tế.', 'Aitogy’s engineering team can help design NTRIP data flows, mountpoints, and monitoring plans based on real workloads.'],
        ['Làm việc với đội triển khai', 'Work with our deployment team'],
        ['Checklist trước khi nghiệm thu', 'Pre-Acceptance Checklist'],
        ['Xác thực ổn định FIX theo ca làm việc dài, kiểm tra tình huống chuyển vùng, và đánh giá tải đồng thời theo kịch bản cao điểm.', 'Verify stable FIX throughout long shifts, test handovers between coverage areas, and assess concurrent load under peak conditions.'],
        ['Bài liên quan', 'Related articles'],
        ['Đọc tiếp', 'Read more'],
        ['Series đề xuất', 'Recommended series'],
        ['Từ khảo sát hiện trạng, dựng hạ tầng Caster đến giám sát production cho mạng CORS nhiều khu vực.', 'From site surveys and Caster deployment to production monitoring for a multi-region CORS network.'],
        ['Case thực tế', 'Real-world case'],
        ['Tối ưu luồng truyền cho dự án có rover di chuyển liên tục, đảm bảo FIX ổn định trong môi trường nhiễu cao.', 'Optimize data delivery for a project with continuously moving Rovers and maintain stable FIX in noisy environments.'],
        ['Ghi chú vận hành', 'Ops Notes'],
        ['Mẫu dashboard SNR, latency và reconnect để đội vận hành theo dõi health theo ca và xử lý chủ động.', 'SNR, latency, and reconnect dashboard examples to help operations teams monitor health by shift and respond proactively.'],
        ['ngày', 'days'],
        ['phiên', 'sessions'],
        ['máy', 'receivers']
    ].sort((a, b) => b[0].length - a[0].length);

    let currentLanguage = 'en';
    let observer = null;
    let languageButton = null;

    const escapePattern = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    function translateText(value, language = currentLanguage) {
        let result = String(value ?? '');
        for (const [vietnamese, english] of translations) {
            const from = language === 'en' ? vietnamese : english;
            const to = language === 'en' ? english : vietnamese;
            const pattern = new RegExp(`(^|[^\\p{L}\\p{N}])${escapePattern(from)}(?=$|[^\\p{L}\\p{N}])`, 'giu');
            result = result.replace(pattern, (_match, prefix) => `${prefix}${to}`);
        }
        return result;
    }

    function detectLanguage() {
        const preferences = Array.isArray(navigator.languages) && navigator.languages.length
            ? navigator.languages
            : [navigator.language || ''];
        const tags = preferences.map(preference => String(preference).replace(/_/g, '-').toLowerCase());
        if (tags.some(tag => tag.split('-').includes('vn'))) return 'vi';
        try {
            if (Intl.DateTimeFormat().resolvedOptions().timeZone === 'Asia/Ho_Chi_Minh') return 'vi';
        } catch (_) {}
        for (const tag of tags) {
            if (tag === 'vi' || tag.startsWith('vi-')) return 'vi';
            if (tag === 'en' || tag.startsWith('en-')) return 'en';
        }
        return 'en';
    }

    function skipTextNode(node) {
        return !node.nodeValue?.trim()
            || node.parentElement?.closest('script,style,textarea,pre,code,noscript,[data-no-translate]');
    }

    function translateTextNode(node) {
        if (skipTextNode(node)) return;
        const current = node.nodeValue;
        const previousOutput = TEXT_OUTPUTS.get(node);
        if (!TEXT_SOURCES.has(node) || current !== previousOutput) TEXT_SOURCES.set(node, current);
        const output = translateText(TEXT_SOURCES.get(node));
        TEXT_OUTPUTS.set(node, output);
        if (current !== output) node.nodeValue = output;
    }

    function translateAttribute(element, name) {
        const current = element.getAttribute(name);
        if (current == null) return;
        let sources = ATTRIBUTE_SOURCES.get(element);
        let outputs = ATTRIBUTE_OUTPUTS.get(element);
        if (!sources) {
            sources = new Map();
            ATTRIBUTE_SOURCES.set(element, sources);
        }
        if (!outputs) {
            outputs = new Map();
            ATTRIBUTE_OUTPUTS.set(element, outputs);
        }
        if (!sources.has(name) || current !== outputs.get(name)) sources.set(name, current);
        const output = translateText(sources.get(name));
        outputs.set(name, output);
        if (current !== output) element.setAttribute(name, output);
    }

    function apply(root = document.documentElement) {
        if (!root) return;
        if (root.nodeType === Node.TEXT_NODE) {
            translateTextNode(root);
            return;
        }
        const elements = root.nodeType === Node.ELEMENT_NODE ? [root, ...root.querySelectorAll('*')] : [];
        elements.forEach(element => ATTRIBUTE_NAMES.forEach(name => translateAttribute(element, name)));
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) translateTextNode(node);
    }

    function updateButton() {
        if (!languageButton) return;
        languageButton.setAttribute('aria-label', currentLanguage === 'en' ? 'Select language' : 'Chọn ngôn ngữ');
        languageButton.querySelectorAll('[data-language]').forEach(option => {
            const selected = option.dataset.language === currentLanguage;
            option.setAttribute('aria-pressed', String(selected));
            option.title = option.dataset.language === 'en' ? 'English' : 'Tiếng Việt';
            option.setAttribute('aria-label', option.title);
        });
    }

    function installButton() {
        if (document.getElementById('app-language-switcher')) {
            languageButton = document.getElementById('app-language-switcher');
            updateButton();
            return;
        }
        languageButton = document.createElement('div');
        languageButton.id = 'app-language-switcher';
        languageButton.className = 'app-language-switcher';
        languageButton.setAttribute('role', 'group');
        languageButton.innerHTML = '<button type="button" class="app-language-option" data-language="vi"><img src="/assets/img/Flag_of_Vietnam.svg" alt=""><span>VI</span></button><button type="button" class="app-language-option" data-language="en"><img src="/assets/img/flags/us.jpg" alt=""><span>EN</span></button>';
        languageButton.setAttribute('data-no-translate', '');
        languageButton.querySelectorAll('[data-language]').forEach(option => {
            option.addEventListener('click', () => setLanguage(option.dataset.language, true));
        });

        const anchor = document.getElementById('logout-btn') || document.getElementById('login-btn');
        const navbar = anchor?.closest('.navbar');
        if (anchor?.parentElement && navbar && getComputedStyle(navbar).display !== 'none') {
            anchor.parentElement.insertBefore(languageButton, anchor);
        } else {
            languageButton.classList.add('app-language-switcher-floating');
            document.body.appendChild(languageButton);
        }
        updateButton();
    }

    function installStyles() {
        if (document.getElementById('app-language-switcher-style')) return;
        const style = document.createElement('style');
        style.id = 'app-language-switcher-style';
        style.textContent = '#app-language-switcher{align-items:center;background:rgba(8,13,24,.88);border:1px solid rgba(56,189,248,.35);border-radius:999px;display:inline-flex;flex:0 0 auto;gap:3px;justify-content:center;padding:3px;white-space:nowrap}#app-language-switcher .app-language-option{align-items:center;background:transparent;border:0;border-radius:999px;color:#a9bbcf;cursor:pointer;display:inline-flex;font-family:inherit;font-size:.72rem;font-weight:700;gap:5px;justify-content:center;line-height:1;min-height:30px;padding:5px 8px}#app-language-switcher .app-language-option img{border-radius:2px;display:block;height:14px;object-fit:cover;width:20px}#app-language-switcher .app-language-option[aria-pressed=true]{background:rgba(14,165,233,.22);box-shadow:inset 0 0 0 1px rgba(56,189,248,.38);color:#e0f2fe}#app-language-switcher .app-language-option:hover,#app-language-switcher .app-language-option:focus-visible{color:#fff;outline:2px solid #38bdf8;outline-offset:1px}.app-language-switcher-floating{position:fixed;right:14px;top:calc(10px + env(safe-area-inset-top,0px));z-index:1090}';
        (document.head || document.documentElement).appendChild(style);
    }

    function setLanguage(language, persist = true) {
        if (language !== 'en' && language !== 'vi') return;
        currentLanguage = language;
        if (persist) {
            try { localStorage.setItem(STORAGE_KEY, language); } catch (_) {}
        }
        document.documentElement.lang = language === 'vi' ? 'vi-VN' : 'en';
        apply(document.documentElement);
        updateButton();
    }

    function init() {
        let savedLanguage = '';
        try { savedLanguage = localStorage.getItem(STORAGE_KEY) || ''; } catch (_) {}
        currentLanguage = savedLanguage === 'vi' || savedLanguage === 'en' ? savedLanguage : detectLanguage();
        document.documentElement.lang = currentLanguage === 'vi' ? 'vi-VN' : 'en';
        installStyles();
        installButton();
        apply(document.documentElement);
        observer = new MutationObserver(records => {
            records.forEach(record => {
                if (record.type === 'characterData') translateTextNode(record.target);
                else if (record.type === 'attributes') translateAttribute(record.target, record.attributeName);
                else record.addedNodes.forEach(node => apply(node));
            });
        });
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ATTRIBUTE_NAMES,
            characterData: true,
            childList: true,
            subtree: true
        });
    }

    window.AppI18n = {
        get language() { return currentLanguage; },
        detectLanguage,
        setLanguage,
        translate: translateText,
        apply
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
