# license_manager.py
import math

def get_license_code_from_string(string_number: str) -> str:
    """Tạo mã license từ một chuỗi số, thuật toán xáo trộn."""
    try:
        length = len(string_number)
        if length == 0: return ""
        arr = [0] * (length + 1)
        narr = [0.0] * (length + 1)
        number = float(string_number)
        r_string = ""
        n = length
        number *= n
        arr[0] = int(number * math.pow(10.0, -float(n)))
        for i in range(1, length):
            number = number - float(arr[i - 1]) / math.pow(10.0, -float(n))
            n -= 1
            arr[i] = int(number * math.pow(10, -float(n)))
        arr[length] = (arr[length - 2] + arr[length - 1]) / 2 + 1
        narr[0] = float(arr[0])
        for i in range(1, length + 1):
            narr[i] = (narr[i - 1] + float(arr[i])) / 2.0
        for i in range(length):
            A = narr[i + 1] * math.exp(-0.2)
            B = (math.log(arr[length], 10)) * math.pow(arr[i + 1], 0.2)
            r_string += str(int(round(A + B)))
        return r_string
    except (ValueError, IndexError, TypeError, ZeroDivisionError):
        return "Lỗi: Đầu vào không hợp lệ"

def generate_pi_license_base(serial_number: str) -> str:
    """Tạo chuỗi số cơ sở từ Serial Number của Pi."""
    base_code_str = ""
    temp_str = ""
    # Chỉ lấy 10 ký tự cuối của serial để tạo mã ngắn gọn và nhất quán
    s_number_part = serial_number[-10:]
    
    for c in s_number_part:
        temp_str += c.upper() if 'a' <= c.lower() <= 'z' else c
    
    for char in temp_str:
        base_code_str += str(ord(char))
        
    # Đảm bảo chuỗi số luôn có độ dài 12
    if len(base_code_str) > 12:
        return base_code_str[:12]
    else:
        return base_code_str.ljust(12, '0')

def generate_customer_license_base(identifier: str) -> str:
    """Tạo chuỗi số cơ sở từ định danh của khách hàng."""
    processed_id = ''.join(filter(str.isalnum, identifier.upper()))
    if len(processed_id) > 12:
        return processed_id[:12]
    return processed_id.ljust(12, '0')