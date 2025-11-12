# trong file: backend/app/utils.py
import time

class NMEAParser:
    """
    Phiên bản NMEAParser nâng cấp (v2.0)
    - Xử lý và gộp các khối tin nhắn GSV từ nhiều hệ thống (multi-constellation).
    - Sử dụng bộ đệm thông minh với timeout để đảm bảo hiển thị tất cả vệ tinh.
    """
    def __init__(self):
        # Bộ đệm cho các vệ tinh từ nhiều hệ thống, key là 'talker ID' (GP, GL, GA...)
        self.gsv_sats_buffer = {}
        # Thời điểm cuối cùng nhận được một gói GSV
        self.last_gsv_package_time = 0
        # Đếm số lượng tin nhắn trong mỗi khối GSV
        self.gsv_message_count = {}

    def parse(self, sentence: str) -> dict | None:
        """
        Phân tích một câu NMEA và trả về dictionary nếu hợp lệ.
        Logic chính được chuyển ra đây để xử lý bộ đệm GSV.
        """
        # --- BƯỚC 1: PHÂN TÍCH CÂU RIÊNG LẺ ---
        parsed_result = None
        if sentence and sentence.startswith('$') and '*' in sentence:
            try:
                parts = sentence.split('*')[0].split(',')
                if parts:
                    message_type = parts[0][3:]
                    if message_type == 'GGA':
                        parsed_result = self._parse_gga(parts)
                    elif message_type == 'GSA':
                        parsed_result = self._parse_gsa(parts)
                    elif message_type == 'GSV':
                        # Hàm _parse_gsv giờ chỉ thu thập dữ liệu, không trả về gì cả
                        self._parse_gsv(parts)
            except (ValueError, IndexError):
                pass # Bỏ qua các câu bị lỗi

        # --- BƯỚC 2: KIỂM TRA VÀ GỘP BỘ ĐỆM GSV ---
        # Nếu đã có tin nhắn GSV được xử lý và đã qua 100ms kể từ tin cuối,
        # tức là "cơn mưa" tin nhắn GSV đã kết thúc.
        now = time.time()
        if self.last_gsv_package_time > 0 and (now - self.last_gsv_package_time > 0.1):
            
            all_sats_in_view = []
            # Gộp tất cả vệ tinh từ các hệ thống khác nhau vào một danh sách
            for talker_id in self.gsv_sats_buffer:
                all_sats_in_view.extend(self.gsv_sats_buffer[talker_id])

            # Nếu có vệ tinh trong danh sách, tạo một kết quả GSV tổng hợp
            if all_sats_in_view:
                gsv_final_result = {"type": "GSV", "satellites": all_sats_in_view}
                
                # Dọn dẹp bộ đệm để chuẩn bị cho lần tiếp theo
                self.gsv_sats_buffer = {}
                self.gsv_message_count = {}
                self.last_gsv_package_time = 0
                
                # Trả về kết quả GSV tổng hợp
                return gsv_final_result
        
        # Nếu không có gì để gộp, trả về kết quả phân tích của câu riêng lẻ (GGA, GSA)
        return parsed_result

    def _parse_gsv(self, parts: list) -> None:
        """
        Thu thập dữ liệu từ một câu GSV và lưu vào bộ đệm. Không trả về gì.
        """
        if len(parts) < 4: return
        
        try:
            talker_id = parts[0][1:3] # GP, GL, GA...
            num_messages = int(parts[1])
            msg_num = int(parts[2])
        except (ValueError, IndexError):
            return

        # Nếu đây là tin nhắn đầu tiên của một hệ thống, khởi tạo bộ đệm cho nó
        if msg_num == 1:
            self.gsv_sats_buffer[talker_id] = []
            self.gsv_message_count[talker_id] = num_messages
        
        # Đảm bảo chúng ta không xử lý tin nhắn lạc
        if talker_id not in self.gsv_sats_buffer:
            return

        # Phân tích thông tin 4 vệ tinh trong câu
        sats_in_message = []
        sats_raw = parts[4:]
        for i in range(0, len(sats_raw), 4):
            if len(sats_raw[i:i+4]) < 4 or not sats_raw[i]: continue
            try:
                 sats_in_message.append({
                    "prn": int(sats_raw[i]),
                    "elevation": int(sats_raw[i+1]) if sats_raw[i+1] else 0,
                    "azimuth": int(sats_raw[i+2]) if sats_raw[i+2] else 0,
                    "snr": int(sats_raw[i+3].split('*')[0]) if sats_raw[i+3] else 0,
                })
            except (ValueError, IndexError):
                continue
        
        # Thêm các vệ tinh vừa phân tích vào bộ đệm
        self.gsv_sats_buffer[talker_id].extend(sats_in_message)
        
        # Cập nhật thời điểm nhận tin nhắn GSV cuối cùng
        self.last_gsv_package_time = time.time()

    def _parse_gga(self, parts: list) -> dict | None:
        if len(parts) < 11 or not all(parts[i] for i in [2, 3, 4, 5, 6, 7, 9]):
            return None
        fix_quality = int(parts[6])
        fix_map = { 0: "INVALID", 1: "GPS (SPS)", 2: "DGPS", 3: "PPS", 4: "RTK_FIXED", 5: "RTK_FLOAT", 6: "ESTIMATED" }
        return {
            "type": "GGA", "timestamp_utc": parts[1],
            "latitude": self._dms_to_dd(parts[2], parts[3]), "longitude": self._dms_to_dd(parts[4], parts[5]),
            "fix_status": fix_map.get(fix_quality, f"UNKNOWN_{fix_quality}"), "satellites": int(parts[7]),
            "hdop": float(parts[8]) if parts[8] else 99.99, "altitude": float(parts[9]),
        }

    def _parse_gsa(self, parts: list) -> dict | None:
        if len(parts) < 18: return None
        active_sats = [int(p) for p in parts[3:15] if p]
        try:
            pdop = float(parts[15]) if parts[15] else 99.99
            hdop = float(parts[16]) if parts[16] else 99.99
            vdop = float(parts[17].split('*')[0]) if parts[17] else 99.99
        except (ValueError, IndexError): return None
        return {"type": "GSA", "active_sats": active_sats, "pdop": pdop, "hdop": hdop, "vdop": vdop}

    def _dms_to_dd(self, dms: str, direction: str) -> float | None:
        if not dms or not direction: return None
        dms_float = float(dms)
        degrees = int(dms_float / 100)
        minutes = dms_float % 100
        decimal_degrees = degrees + (minutes / 60)
        if direction in ['S', 'W']:
            decimal_degrees *= -1
        return round(decimal_degrees, 8)