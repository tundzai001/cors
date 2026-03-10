import asyncio
import time

# === CẤU HÌNH SERVER ===
HOST = "aitogy.com.vn" 
PORT = 2101

# === THÔNG TIN USER MUỐN TẠO ===
# Dùng đúng thông tin ban đầu của bạn (admin_user="id")
CREATOR = "id"           
TEST_USERNAME = "test_cuoi_cung" 
TEST_PASSWORD = "123"
TEST_MPS_RAW = "BG.LucNgan; HN.SocSon"

# Xử lý chuỗi Mountpoint cho sạch
CLEAN_MPS = TEST_MPS_RAW.replace(";", ",").replace(" ", "").strip()

async def debug_add_user():
    print(f"\n{'='*60}")
    print(f"🕵️  DEBUG: THỬ TẠO USER TRÊN {HOST}:{PORT}")
    print(f"{'='*60}")

    # --- LỆNH CHUẨN (GIỐNG CODE CŨ CỦA BẠN) ---
    # Lưu ý: Kết thúc bằng \r\n\r\n như chuẩn BKG
    cmd = f"GET /genuser HTTP/1.0/{CREATOR}/{TEST_USERNAME}/{TEST_PASSWORD}/{CLEAN_MPS}/3600/1\r\n\r\n"
    
    print(f"[1] Lệnh sẽ gửi đi:\n    {repr(cmd)}")

    print(f"\n[2] Đang kết nối...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT), 
            timeout=5.0
        )
        print(f"    ✅ Kết nối TCP thành công!")

        # Gửi dữ liệu
        print(f"[3] Đang gửi lệnh...")
        writer.write(cmd.encode('utf-8'))
        await writer.drain()

        # Đọc phản hồi
        print(f"[4] Đang chờ phản hồi...")
        data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        
        if not data:
            print("    ⚠️  Server đóng kết nối và không trả lời gì cả.")
        else:
            decoded = data.decode('utf-8', errors='ignore')
            print(f"\n🎉 SERVER PHẢN HỒI ({len(data)} bytes):")
            print(f"{'-'*40}")
            print(decoded.strip())
            print(f"{'-'*40}")
            
            # Phân tích sơ bộ phản hồi
            if "SOURCETABLE" in decoded:
                print("\n❌ KẾT QUẢ: THẤT BẠI.")
                print("    Server không hiểu lệnh này nên đã trả về danh sách trạm (SourceTable).")
                print("    => Có thể server Caster này không hỗ trợ lệnh 'GET /genuser'.")
            elif "Error" in decoded:
                 print("\n❌ KẾT QUẢ: LỖI TỪ CASTER.")
                 print("    Server hiểu lệnh nhưng từ chối (Sai pass admin? Sai cú pháp?)")
            else:
                print("\n✅ KẾT QUẢ: CÓ VẺ THÀNH CÔNG (Kiểm tra lại trên Web xem có user chưa).")

    except ConnectionResetError:
        print("\n❌ LỖI 10054: Server đá kết nối ngay lập tức.")
        print("    => Server ghét định dạng lệnh 'GET /genuser' này.")
    except Exception as e:
        print(f"\n❌ LỖI KHÁC: {e}")

    finally:
        if 'writer' in locals() and writer:
            writer.close()
            try:
                await writer.wait_closed()
            except: pass

if __name__ == "__main__":
    asyncio.run(debug_add_user())