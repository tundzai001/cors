import os
import re

files_to_fix = [
    r"app\main.py",
    r"app\mqtt.py",
    r"app\crud.py"
]

# Đây là tất cả các ký tự rác sinh ra do đọc byte 0x80-0xBF bằng bảng mã ansi/cp1252
continuation_chars = (
    "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
    " ¡¢£¤¥¦§¨©ª«¬­®¯°±²³´µ¶·¸¹º»¼½¾¿"
    "\x81\x8d\x8f\x90\x9d" # Các byte control bị lọt vào
)

# Regex tìm chính xác các cụm từ bị mã hóa lỗi (bắt đầu bằng Ã, Ä, á, Æ, Å theo sau là các ký tự rác)
mojibake_pattern = re.compile(r'[ÃÄáÆÅ][' + re.escape(continuation_chars) + r']{1,2}')

def to_byte(c):
    try:
        return c.encode('cp1252')
    except UnicodeEncodeError:
        # Nhóm \x81, \x8d, \x8f, \x90, \x9d không có trong cp1252, lấy thẳng byte của nó
        return c.encode('latin1')

def fix_match(m):
    s = m.group(0)
    b = bytearray()
    for c in s:
        b.extend(to_byte(c))
    try:
        return b.decode('utf-8')
    except:
        return s # Nếu giải mã thất bại thì trả về nguyên xi

def fix_mojibake(filepath):
    if not os.path.exists(filepath):
        print(f"Không tìm thấy {filepath}")
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Chỉ thay thế đúng những cái cụm bị hỏng bằng regex, tuyệt đối không chạm vào phần code hay tiếng Việt khác!
    fixed_content = mojibake_pattern.sub(fix_match, content)

    if fixed_content != content:
        try:
            with open(filepath + ".bak", 'w', encoding='utf-8') as f:
                f.write(content)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            print(f"✅ Đã dọn sạch bong lỗi font trong: {filepath}")
        except Exception as e:
            print(f"❌ Không thể ghi file {filepath}: {e}")
    else:
        print(f"✨ File {filepath} hoàn toàn sạch sẽ!")

if __name__ == "__main__":
    print("Bắt đầu xử lý dẻ lau dọn rác font (Chế độ Phẫu thuật chính xác)...")
    for file in files_to_fix:
        fix_mojibake(file)
    print("Hoàn tất thi công!")
