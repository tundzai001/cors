# ==============================================================================
# == backend/app/command_builder.py ==
# ==============================================================================

def build_base_survey_in_command(sensor_type: str, duration: int, accuracy: float) -> list[bytes]:
    """
    Xây dựng chuỗi lệnh cho chế độ Survey-In.
    """
    commands = []
    
    if sensor_type == 'Ublox':
        # Tạo message với cấu trúc chính xác
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 1 (Survey-In)
        message[8] = 1
        
        # Chuyển đổi duration và accuracy
        svinMinDur_bytes = int(duration).to_bytes(4, byteorder='little')
        svinAccLimit_bytes = int(accuracy * 10000).to_bytes(4, byteorder='little')
        
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
        # QUAN TRỌNG: Format đúng như code gốc
        if accuracy > 0:
            cmd_str = f'MODE BASE TIME {duration} {accuracy}\r\nSAVECONFIG\r\n'
        else:
            cmd_str = f'MODE BASE TIME {duration}\r\nSAVECONFIG\r\n'
        commands.append(cmd_str.encode())
        
    return commands


def build_base_fixed_lla_command(sensor_type: str, lat: float, lon: float, alt: float, accuracy: float) -> list[bytes]:
    """
    Xây dựng chuỗi lệnh cho chế độ Fixed LLA.
    
    THAY ĐỔI:
    - Sửa lỗi xử lý high precision bytes (HP)
    - Đảm bảo signed integer conversion đúng
    """
    commands = []
    
    if sensor_type == 'Ublox':
        message = bytearray(b'\xb5\x62\x06\x71\x28\x00' + b'\x00' * 42)
        
        # Byte 8: Mode = 2 (Fixed)
        message[8] = 2
        # Byte 9: LLA mode = 1
        message[9] = 1
        
        multiplier = 10000000  # LLA multiplier
        
        # === XỬ LÝ LATITUDE ===
        XOrLat_int = int(lat * multiplier)
        # Tính HP value với dấu
        XOrLat_hp_value = int((lat * multiplier - XOrLat_int) * 100)
        XOrLat_hp_bytes = XOrLat_hp_value.to_bytes(1, byteorder='little', signed=True)
        XOrLat_bytes = XOrLat_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ LONGITUDE ===
        YOrLon_int = int(lon * multiplier)
        YOrLon_hp_value = int((lon * multiplier - YOrLon_int) * 100)
        YOrLon_hp_bytes = YOrLon_hp_value.to_bytes(1, byteorder='little', signed=True)
        YOrLon_bytes = YOrLon_int.to_bytes(4, byteorder='little', signed=True)
        
        # === XỬ LÝ ALTITUDE ===
        ZOrAlt_int = int(alt * 100)
        ZOrAlt_hp_value = int((alt * 100 - ZOrAlt_int) * 100)
        ZOrAlt_hp_bytes = ZOrAlt_hp_value.to_bytes(1, byteorder='little', signed=True)
        ZOrAlt_bytes = ZOrAlt_int.to_bytes(4, byteorder='little', signed=True)
        
        # Fixed Position Accuracy
        fixedPosAcc_bytes = int(accuracy * 10000).to_bytes(4, byteorder='little')
        
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
        cmd_str = f'MODE BASE {lat} {lon} {alt}\r\nSAVECONFIG\r\n'
        commands.append(cmd_str.encode())
        
    return commands


# === HÀM DEBUG  ===
def debug_command(command_bytes: bytes) -> str:
    """In ra chuỗi byte dưới dạng hex để debug"""
    return ' '.join(f'{b:02x}' for b in command_bytes)


# === TEST CASE ===
if __name__ == "__main__":
    print("=== TEST SURVEY-IN ===")
    commands = build_base_survey_in_command('Ublox', 300, 0.01)
    for i, cmd in enumerate(commands):
        print(f"Command {i+1}: {debug_command(cmd)}")
    
    print("\n=== TEST FIXED LLA ===")
    commands = build_base_fixed_lla_command('Ublox', 21.0285, 105.8542, 10.5, 10.0)
    for i, cmd in enumerate(commands):
        print(f"Command {i+1}: {debug_command(cmd)}")