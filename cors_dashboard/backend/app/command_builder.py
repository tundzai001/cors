import logging

def build_base_survey_in_command(sensor_type: str, duration: int, accuracy: float) -> list[bytes]:
    commands = []
    
    if sensor_type == 'Ublox':
        # Tạo message với cấu trúc chính xác
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 1 (Survey-In)
        message[8] = 1
        
        # Chuyển đổi duration và accuracy (ép kiểu float đề phòng dữ liệu là string)
        svinMinDur_bytes = int(duration).to_bytes(4, byteorder='little')
        svinAccLimit_bytes = int(float(accuracy) * 10000).to_bytes(4, byteorder='little')
        
        # Ghi vào vị trí đúng
        for i in range(4):
            message[30 + i] = svinMinDur_bytes[i]
            message[34 + i] = svinAccLimit_bytes[i]
        
        # Tính checksum
        CK_A, CK_B = 0, 0
        for i in range(2, 46):
            CK_A = (CK_A + message[i]) & 0xff
            CK_B = (CK_B + CK_A) & 0xff
        
        message[46] = CK_A
        message[47] = CK_B
        
        commands.append(bytes(message))
        # Lệnh Save Config
        commands.append(b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab')
        
    elif sensor_type == 'Unicorecomm':
        # BƯỚC 1: Clear all logs (TẮT TẤT CẢ OUTPUT)
        commands.append(b'unlogall\r\n')
        commands.append(b'$DELAY_1000$')
        
        # BƯỚC 2: Set base mode (Survey-In)
        cmd_str = f'mode base time {duration}\r\n'
        logging.info(f"🔍 Survey-In command: {repr(cmd_str)}")
        commands.append(cmd_str.encode('ascii'))
        commands.append(b'$DELAY_2000$')
        
        logging.info("📡 Configuring NMEA output on COM3 (same as RTCM port)")
        
        # GPS Messages
        commands.append(b'gpgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gpgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gpgsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # GLONASS Messages 
        commands.append(b'glgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'glgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'glgsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # Galileo Messages
        commands.append(b'gagga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gagsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gagsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # BeiDou Messages
        commands.append(b'bdgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'bdgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'bdgsv com3 1\r\n')
        commands.append(b'$DELAY_500$')
        
        # ========================================
        # BƯỚC 4: RTCM messages 
        # ========================================
        logging.info("📡 Configuring RTCM output messages on COM3")
        commands.append(b'rtcm1006 com3 1\r\n')
        commands.append(b'rtcm1033 com3 1\r\n')
        commands.append(b'rtcm1074 com3 1\r\n')
        commands.append(b'rtcm1124 com3 1\r\n')
        commands.append(b'rtcm1084 com3 1\r\n')
        commands.append(b'rtcm1094 com3 1\r\n')
        commands.append(b'rtcm1042 com3 1\r\n')
        commands.append(b'rtcm1019 com3 1\r\n')
        commands.append(b'rtcm1020 com3 1\r\n')
        commands.append(b'rtcm1045 com3 1\r\n')
        
        # BƯỚC 5: Save config
        commands.append(b'saveconfig\r\n')
        
    return commands


def build_base_fixed_lla_command(sensor_type: str, lat: float, lon: float, alt: float, accuracy: float) -> list[bytes]:
    commands = []
    
    if sensor_type == 'Ublox':
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 2 (Fixed)
        message[8] = 2
        # Byte 9: LLA mode = 1
        message[9] = 1
        
        # Ép kiểu float cho tọa độ (đề phòng trường hợp đầu vào là string để giữ precision)
        f_lat = float(lat)
        f_lon = float(lon)
        f_alt = float(alt)
        f_acc = float(accuracy)

        multiplier = 10000000  # LLA multiplier
        
        # === XỬ LÝ LATITUDE ===
        XOrLat_int = int(f_lat * multiplier)
        XOrLat_hp_value = int((f_lat * multiplier - XOrLat_int) * 100)
        XOrLat_hp_bytes = XOrLat_hp_value.to_bytes(1, byteorder='little', signed=True)
        XOrLat_bytes = XOrLat_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ LONGITUDE ===
        YOrLon_int = int(f_lon * multiplier)
        YOrLon_hp_value = int((f_lon * multiplier - YOrLon_int) * 100)
        YOrLon_hp_bytes = YOrLon_hp_value.to_bytes(1, byteorder='little', signed=True)
        YOrLon_bytes = YOrLon_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ ALTITUDE ===
        ZOrAlt_int = int(f_alt * 100)
        ZOrAlt_hp_value = int((f_alt * 100 - ZOrAlt_int) * 100)
        ZOrAlt_hp_bytes = ZOrAlt_hp_value.to_bytes(1, byteorder='little', signed=True)
        ZOrAlt_bytes = ZOrAlt_int.to_bytes(4, byteorder='little', signed=True)
        
        # Fixed Position Accuracy
        fixedPosAcc_bytes = int(f_acc * 10000).to_bytes(4, byteorder='little')
        
        # Ghi vào message
        for i in range(4):
            message[10 + i] = XOrLat_bytes[i]
            message[14 + i] = YOrLon_bytes[i]
            message[18 + i] = ZOrAlt_bytes[i]
            message[26 + i] = fixedPosAcc_bytes[i]
        
        # Ghi HP bytes
        message[22] = XOrLat_hp_bytes[0]
        message[23] = YOrLon_hp_bytes[0]
        message[24] = ZOrAlt_hp_bytes[0]
        
        # Tính checksum
        CK_A, CK_B = 0, 0
        for i in range(2, 46):
            CK_A = (CK_A + message[i]) & 0xff
            CK_B = (CK_B + CK_A) & 0xff
        
        message[46] = CK_A
        message[47] = CK_B
        
        commands.append(bytes(message))
        # Lệnh Save Config
        commands.append(b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab')
        
    elif sensor_type == 'Unicorecomm':
        # BƯỚC 1: Clear all logs (TẮT TẤT CẢ OUTPUT)
        commands.append(b'unlogall\r\n')
        commands.append(b'$DELAY_1000$')
        
        # BƯỚC 2: Set fixed position - GIỮ NGUYÊN FORMAT NGƯỜI DÙNG NHẬP
        lat_str = str(lat)
        lon_str = str(lon)
        alt_str = str(alt)
        
        cmd_str = f'mode base {lat_str} {lon_str} {alt_str}\r\n'
        logging.info(f"🔍 Fixed position command: {repr(cmd_str)}")
        commands.append(cmd_str.encode('ascii'))
        commands.append(b'$DELAY_2000$')
        
        # ========================================
        # ✅ FIX: BẬT NMEA TRÊN COM3 (CÙNG PORT VỚI RTCM)
        # ========================================
        logging.info("📡 Configuring NMEA output on COM3 (same as RTCM port)")
        
        # GPS Messages
        commands.append(b'gpgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gpgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gpgsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # GLONASS Messages
        commands.append(b'glgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'glgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'glgsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # Galileo Messages
        commands.append(b'gagga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gagsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'gagsv com3 1\r\n')
        commands.append(b'$DELAY_200$')
        
        # BeiDou Messages
        commands.append(b'bdgga com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'bdgsa com3 1\r\n')
        commands.append(b'$DELAY_200$')
        commands.append(b'bdgsv com3 1\r\n')
        commands.append(b'$DELAY_500$')
        
        # ========================================
        # BƯỚC 4: RTCM messages (CÙNG PORT COM3)
        # ========================================
        logging.info("📡 Configuring RTCM output messages on COM3")
        commands.append(b'rtcm1006 com3 1\r\n')
        commands.append(b'rtcm1033 com3 1\r\n')
        commands.append(b'rtcm1074 com3 1\r\n')
        commands.append(b'rtcm1124 com3 1\r\n')
        commands.append(b'rtcm1084 com3 1\r\n')
        commands.append(b'rtcm1094 com3 1\r\n')
        commands.append(b'rtcm1042 com3 1\r\n')
        commands.append(b'rtcm1019 com3 1\r\n')
        commands.append(b'rtcm1020 com3 1\r\n')
        commands.append(b'rtcm1045 com3 1\r\n')
        
        # BƯỚC 5: Save config
        commands.append(b'saveconfig\r\n')
        
    return commands


def build_geotek_lte_unicore_config(setup_method: str, duration: int = 60, lat: float = 0, lon: float = 0, alt: float = 0) -> list[bytes]:
    """
    Hàm chuyên biệt dành riêng cho chip Unicorecomm LTE (Geotek).
    Sử dụng mặc định cổng COM2 và chuỗi lệnh tối ưu theo yêu cầu người dùng.
    Trình tự: FRESET -> unlogall -> mode base -> NMEA -> RTCM -> saveconfig
    """
    commands = []
    port = "com2"
    
    # 1. Reset vật lý chip và xóa hết log cũ
    commands.append(b'FRESET\r\n')
    commands.append(b'unlogall\r\n')
    
    # 2. Cấu hình chế độ Base (Survey-In hoặc Fixed)
    if setup_method.upper() == 'SURVEY_IN':
        commands.append(f'mode base time {duration}\r\n'.encode('ascii'))
    else:
        # Giữ nguyên độ chính xác của tọa độ
        commands.append(f'mode base {lat} {lon} {alt}\r\n'.encode('ascii'))
    
    # 3. Bật các bản tin NMEA cần thiết trên COM2 (12 bản tin)
    # Unicore LTE: chi bat GGA. GSA/GSV lam stream MQTT/RTCM khong on dinh.
    nmea_msgs = [
        'gpgga'
    ]
    for msg in nmea_msgs:
        commands.append(f'{msg} {port} 1\r\n'.encode('ascii'))
        
    # 4. Bật các bản tin RTCM cần thiết trên COM2 (10 bản tin)
    rtcm_msgs = [
        '1006', '1033', '1074', '1124', '1084', '1094', '1042', '1019', '1020', '1045'
    ]
    for msg in rtcm_msgs:
        commands.append(f'rtcm{msg} {port} 1\r\n'.encode('ascii'))
    
    # 5. Lưu cấu hình
    commands.append(b'saveconfig\r\n')
    
    return commands


# === HÀM DEBUG ===
def debug_command(command_bytes: bytes) -> str:
    """In ra chuỗi byte dưới dạng hex để debug"""
    return ' '.join(f'{b:02x}' for b in command_bytes)
