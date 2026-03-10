#!/usr/bin/env python3
# ==============================================================================
# == test_caster_password.py - Standalone Test Script
# ==============================================================================
#
# MỤC ĐÍCH:
# Test trực tiếp kết nối TCP đến NTRIP Caster để tìm lỗi password
#
# CÁCH CHẠY:
# python3 test_caster_password.py
#
# ==============================================================================

import asyncio
import sys

# ==============================================================================
# CẤU HÌNH (SỬA THEO MÔI TRƯỜNG CỦA BẠN)
# ==============================================================================

CASTER_HOST = "aitogy.com.vn"  # Hoặc aitogy.com.vn
CASTER_PORT = 2101
ADMIN_USER = "admin"  # Hoặc "admin" - tùy config Caster

# ==============================================================================
# CASTER CLIENT CLASS (SIMPLIFIED)
# ==============================================================================

class SimpleCasterClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
    
    async def send_command(self, command: str) -> str:
        """Gửi TCP command và nhận response"""
        reader, writer = None, None
        
        try:
            print(f"\n{'='*70}")
            print(f"Connecting to {self.host}:{self.port}...")
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0
            )
            
            print(f"✓ Connected!")
            print(f"\n{'='*70}")
            print("SENDING TCP COMMAND:")
            print(f"{'='*70}")
            print(command)
            print(f"{'='*70}")
            
            # Hiển thị HEX dump
            import binascii
            hex_dump = binascii.hexlify(command.encode('utf-8')).decode('ascii')
            print(f"HEX: {hex_dump}")
            print(f"{'='*70}")
            
            # Gửi command
            writer.write(command.encode('utf-8'))
            await writer.drain()
            
            print("\n✓ Command sent! Waiting for response...")
            
            # Đọc response
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response = data.decode('utf-8', errors='ignore')
            
            print(f"\n{'='*70}")
            print("CASTER RESPONSE:")
            print(f"{'='*70}")
            print(response)
            print(f"{'='*70}")
            
            # Hiển thí HEX dump response
            response_hex = binascii.hexlify(data).decode('ascii')
            print(f"HEX: {response_hex[:200]}...")
            print(f"{'='*70}\n")
            
            return response
        
        except asyncio.TimeoutError:
            print("\n❌ TIMEOUT: Không nhận được response từ Caster!")
            return "TIMEOUT"
        
        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return f"ERROR: {e}"
        
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass

# ==============================================================================
# TEST CASES
# ==============================================================================

async def test_create_user(client, username, password, mountpoints="MP1", 
                           expiry=31536000, max_users=1):
    """Test tạo user với các tham số khác nhau"""
    
    print(f"\n{'#'*70}")
    print(f"# TEST: Creating user '{username}' with password '{password}'")
    print(f"{'#'*70}")
    print(f"Username:    {username}")
    print(f"Password:    {password} (length: {len(password)})")
    print(f"Mountpoints: {mountpoints}")
    print(f"Expiry:      {expiry} seconds")
    print(f"Max users:   {max_users}")
    
    # Build command (giống backend)
    cmd = f"GET /genuser HTTP/1.0/{ADMIN_USER}/{username}/{password}/{mountpoints}/{expiry}/{max_users}\r\n\r\n"
    
    # Gửi command
    response = await client.send_command(cmd)
    
    # Phân tích kết quả
    if "Error" in response:
        print(f"\n❌ FAILED: Caster báo lỗi!")
        return False
    elif "TIMEOUT" in response:
        print(f"\n⏱️  TIMEOUT: Không nhận được response!")
        return False
    else:
        print(f"\n✅ SUCCESS: User created (hoặc updated)")
        return True

async def test_get_users(client):
    """Lấy danh sách user hiện có trên Caster"""
    
    print(f"\n{'#'*70}")
    print(f"# TEST: Getting user list from Caster")
    print(f"{'#'*70}")
    
    cmd = f"GET /usertable HTTP/1.0/{ADMIN_USER}/mp1/password1/20\r\n"
    
    response = await client.send_command(cmd)
    
    # Parse response
    print("\nParsing user list...")
    users = []
    
    for line in response.split('\n'):
        if line.startswith('STR;'):
            parts = line.split(';')
            if len(parts) >= 9:
                users.append({
                    "username": parts[1],
                    "password": parts[2],
                    "mountpoints": parts[3],
                    "status": parts[5]
                })
    
    print(f"\nFound {len(users)} users:")
    for u in users:
        print(f"  - {u['username']:<15} | Password: {u['password']:<12} | MPs: {u['mountpoints']}")
    
    return users

async def test_delete_user(client, username):
    """Xóa user test sau khi test xong"""
    
    print(f"\n{'#'*70}")
    print(f"# CLEANUP: Deleting test user '{username}'")
    print(f"{'#'*70}")
    
    cmd = f"GET /deluser HTTP/1.0/{ADMIN_USER}/{username}\r\n"
    
    response = await client.send_command(cmd)
    
    if "deleted" in response.lower():
        print(f"\n✓ User '{username}' deleted")
        return True
    else:
        print(f"\n⚠️  Delete result unclear: {response}")
        return False

# ==============================================================================
# MAIN TEST SEQUENCE
# ==============================================================================

async def main():
    print("\n" + "="*70)
    print(" NTRIP CASTER PASSWORD DEBUG TEST")
    print("="*70)
    
    client = SimpleCasterClient(CASTER_HOST, CASTER_PORT)
    
    # Danh sách test cases
    test_cases = [
        # (username, password, mountpoints)
        ("test_pw4", "1234", "MP1"),           # 4 ký tự
        ("test_pw5", "12345", "MP1"),          # 5 ký tự - WORKING
        ("test_pw6", "123456", "MP1"),         # 6 ký tự - BUG!
        ("test_pw8", "12345678", "MP1"),       # 8 ký tự
        ("test_pw10", "1234567890", "MP1"),    # 10 ký tự
        ("test_pw12", "123456789012", "MP1"),  # 12 ký tự
        
        # Test với password có chữ
        ("test_alpha", "abc123", "MP1"),
        ("test_mixed", "AbC123", "MP1"),
        
        # Test với nhiều mountpoints
        ("test_multi", "12345", "MP1,MP2,MP3"),
        ("test_multi6", "123456", "MP1,MP2,MP3"),  # Combo bug?
    ]
    
    results = []
    
    # Chạy từng test
    for username, password, mps in test_cases:
        success = await test_create_user(client, username, password, mps)
        results.append({
            "username": username,
            "password": password,
            "length": len(password),
            "mountpoints": mps,
            "success": success
        })
        
        # Nghỉ 1s giữa các test
        await asyncio.sleep(1)
    
    # Hiển thị tổng kết
    print("\n" + "="*70)
    print(" TEST RESULTS SUMMARY")
    print("="*70)
    print(f"{'Username':<15} | {'Password':<15} | {'Len':<4} | {'MPs':<15} | {'Result'}")
    print("-"*70)
    
    for r in results:
        status = "✅ OK" if r['success'] else "❌ FAIL"
        print(f"{r['username']:<15} | {r['password']:<15} | {r['length']:<4} | {r['mountpoints']:<15} | {status}")
    
    # Thống kê
    success_count = sum(1 for r in results if r['success'])
    fail_count = len(results) - success_count
    
    print("-"*70)
    print(f"Total: {len(results)} tests | Success: {success_count} | Failed: {fail_count}")
    print("="*70)
    
    # Phân tích pattern
    print("\n📊 ANALYSIS:")
    
    failed_tests = [r for r in results if not r['success']]
    if failed_tests:
        print(f"\n❌ Failed tests ({len(failed_tests)}):")
        for r in failed_tests:
            print(f"   - Password '{r['password']}' (len={r['length']}) with MPs: {r['mountpoints']}")
        
        # Tìm pattern
        fail_lengths = set(r['length'] for r in failed_tests)
        print(f"\n🔍 Failed password lengths: {fail_lengths}")
        
        if 6 in fail_lengths:
            print("\n⚠️  CONFIRMED: Password length 6 causes issues!")
    else:
        print("\n✅ All tests passed! No password length limit found.")
    
    # Cleanup
    print("\n" + "="*70)
    print(" CLEANUP: Deleting test users...")
    print("="*70)
    
    for r in results:
        await test_delete_user(client, r['username'])
        await asyncio.sleep(0.5)
    
    # Kiểm tra lại danh sách user
    print("\n" + "="*70)
    print(" FINAL USER LIST CHECK")
    print("="*70)
    
    await test_get_users(client)
    
    print("\n✅ Test completed!\n")

# ==============================================================================
# RUN
# ==============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)