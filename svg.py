import cv2
import numpy as np

def convert_map_to_svg(input_path, output_path):
    # 1. Đọc ảnh
    img = cv2.imread(input_path)
    if img is None:
        print(f"Lỗi: Không thể đọc được ảnh từ {input_path}")
        return

    h, w = img.shape[:2]

    # Chuyển đổi sang hệ màu HSV để lọc màu chính xác
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # ==========================================
    # BƯỚC 1: TÁCH MÀU ĐỎ (BẢN ĐỒ & CÁC ĐẢO)
    # ==========================================
    lower_red_1 = np.array([0, 70, 50])
    upper_red_1 = np.array([10, 255, 255])
    lower_red_2 = np.array([170, 70, 50])
    upper_red_2 = np.array([180, 255, 255])
    
    mask_red_1 = cv2.inRange(hsv, lower_red_1, upper_red_1)
    mask_red_2 = cv2.inRange(hsv, lower_red_2, upper_red_2)
    mask_red = mask_red_1 + mask_red_2

    # Tìm đường viền phần màu đỏ (dùng RETR_TREE để lấy ranh giới tỉnh bên trong)
    contours_red, _ = cv2.findContours(mask_red, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    # ==========================================
    # BƯỚC 2: TÁCH MÀU VÀNG (NGÔI SAO THỦ ĐÔ)
    # ==========================================
    # Dải màu vàng trong hệ HSV
    lower_yellow = np.array([20, 100, 100])
    upper_yellow = np.array([40, 255, 255])
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
    
    # Tìm đường viền phần màu vàng
    contours_yellow, _ = cv2.findContours(mask_yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # ==========================================
    # BƯỚC 3: GHI RA FILE SVG
    # ==========================================
    with open(output_path, "w", encoding="utf-8") as f:
        # Khai báo chuẩn file SVG
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n')

        # ---- Vẽ viền bản đồ (Màu xanh Cyan tối giống ảnh 2 của bạn) ----
        for cnt in contours_red:
            if cv2.contourArea(cnt) < 2:  # Bỏ qua các hạt nhiễu quá nhỏ
                continue

            path_data = ""
            for i, point in enumerate(cnt):
                x, y = point[0]
                if i == 0:
                    path_data += f"M {x} {y} "
                else:
                    path_data += f"L {x} {y} "
            path_data += "Z"
            # stroke: màu viền (Mã Hex của màu xanh ngọc), fill="none": trong suốt
            f.write(f'  <path d="{path_data}" fill="none" stroke="#005A5A" stroke-width="1.5" />\n')

        # ---- Vẽ viền ngôi sao (Màu vàng) ----
        for cnt in contours_yellow:
            if cv2.contourArea(cnt) < 2:
                continue

            path_data = ""
            for i, point in enumerate(cnt):
                x, y = point[0]
                if i == 0:
                    path_data += f"M {x} {y} "
                else:
                    path_data += f"L {x} {y} "
            path_data += "Z"
            # Viền ngôi sao màu vàng đậm
            f.write(f'  <path d="{path_data}" fill="none" stroke="#FFD700" stroke-width="2" />\n')

        f.write('</svg>\n')
        
    print(f"Tuyệt vời! Đã xuất SVG thành công tại: {output_path}")

# ==========================================
# CÁCH CHẠY TOOL
# ==========================================
input_image = "vietnam-map-detailed.png"  # File đầu vào (ảnh gốc)
output_svg = "vietnam-map-final.svg"      # File đầu ra (vector)

convert_map_to_svg(input_image, output_svg)