#!/usr/bin/env python3
# ==============================================================================
# == verify_rover_password.py - Kiểm tra password thực tế trên Caster
# ==============================================================================

import asyncio
import sys

CASTER_HOST = "aitogy.com.vn"
CASTER_PORT = 2101
ADMIN_USER = "admin"

class CasterClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
    
    async def send_command(self, command: str) -> str:
        reader, writer = None, None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0
            )
            
            writer.write(command.encode('utf-8'))
            await writer.drain()
            
            data = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response = data.decode('utf-8', errors='ignore')
            
            return response
        
        except Exception as e:
            return f"ERROR: {e}"
        
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except:
                    pass
    
    async def get_user_info(self, username: str):
        """Lấy thông tin chi tiết của 1 user cụ thể"""
        
        cmd = f"GET /usertable HTTP/1.0/{ADMIN_USER}/mp1/password1/20\r\n"
        response = await self.send_command(cmd)
        
        for line in response.split('\n'):
            if line.startswith('STR;'):
                parts = line.split(';')
                if len(parts) >= 9 and parts[1] == username:
                    return {
                        "username": parts[1],
                        "password": parts[2],
                        "mountpoints": parts[3],
                        "status": parts[5]
                    }
        
        return None
    
    async def create_test_user(self, username: str, password: str, mps: str):
        """Tạo user test"""
        
        cmd = f"GET /genuser HTTP/1.0/{ADMIN_USER}/{username}/{password}/{mps}/31536000/1\r\n\r\n"
        
        print(f"\n{'='*70}")
        print(f"Creating user: {username}")
        print(f"Password: '{password}' (length: {len(password)})")
        print(f"TCP Command:")
        print(cmd.strip())
        print(f"{'='*70}")
        
        response = await self.send_command(cmd)
        
        print(f"\nCaster Response:")
        print(response[:200])
        
        return "Error" not in response
    
    async def test_login(self, username: str, password: str):
        """Test login (giả lập RTK Client)"""
        
        import base64
        auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        
        # Giả lập NTRIP Client request
        cmd = f"GET /MP1 HTTP/1.1\r\n"
        cmd += f"Host: {self.host}\r\n"
        cmd += f"Authorization: Basic {auth}\r\n"
        cmd += f"User-Agent: TestClient/1.0\r\n"
        cmd += f"Connection: close\r\n\r\n"
        
        print(f"\n{'='*70}")
        print(f"Testing login: {username} / {password}")
        print(f"{'='*70}")
        
        response = await self.send_command(cmd)
        
        if "200 OK" in response or "ICY 200" in response:
            print("✅ LOGIN SUCCESS!")
            return True
        else:
            print(f"❌ LOGIN FAILED!")
            print(f"Response: {response[:200]}")
            return False


async def main():
    print("\n" + "="*70)
    print(" ROVER PASSWORD VERIFICATION TEST")
    print("="*70)
    
    client = CasterClient(CASTER_HOST, CASTER_PORT)
    
    # SCENARIO 1: Tạo user với password "123456"
    test_username = "verify_test"
    test_password = "123456"
    test_mps = "MP1"
    
    print("\n### STEP 1: Create test user")
    success = await client.create_test_user(test_username, test_password, test_mps)
    
    if not success:
        print("\n❌ Failed to create user! Exiting...")
        return
    
    await asyncio.sleep(1)
    
    # SCENARIO 2: Verify password trên Caster
    print("\n### STEP 2: Verify password on Caster")
    user_info = await client.get_user_info(test_username)
    
    if user_info:
        print(f"\n✅ User found on Caster:")
        print(f"   Username:    {user_info['username']}")
        print(f"   Password:    '{user_info['password']}'")
        print(f"   Password len: {len(user_info['password'])}")
        print(f"   Mountpoints: {user_info['mountpoints']}")
        
        # SO SÁNH PASSWORD
        if user_info['password'] == test_password:
            print(f"\n✅ Password MATCH! ('{test_password}' == '{user_info['password']}')")
        else:
            print(f"\n❌ Password MISMATCH!")
            print(f"   Expected: '{test_password}'")
            print(f"   Got:      '{user_info['password']}'")
            print(f"\n🔍 Analyzing difference:")
            print(f"   Expected bytes: {test_password.encode('utf-8')}")
            print(f"   Got bytes:      {user_info['password'].encode('utf-8')}")
    else:
        print(f"\n❌ User '{test_username}' NOT FOUND on Caster!")
    
    # SCENARIO 3: Test login với password ĐÚNG
    print("\n### STEP 3: Test login with CORRECT password")
    await client.test_login(test_username, test_password)
    
    await asyncio.sleep(1)
    
    # SCENARIO 4: Test login với password SAI (thiếu 1 ký tự)
    print("\n### STEP 4: Test login with WRONG password (missing 1 char)")
    wrong_password = test_password[:-1]  # "12345"
    await client.test_login(test_username, wrong_password)
    
    await asyncio.sleep(1)
    
    # SCENARIO 5: Test login với password có space
    print("\n### STEP 5: Test login with password + space")
    space_password = test_password + " "
    await client.test_login(test_username, space_password)
    
    # CLEANUP
    print("\n### CLEANUP: Deleting test user")
    cmd = f"GET /deluser HTTP/1.0/{ADMIN_USER}/{test_username}\r\n"
    await client.send_command(cmd)
    print(f"✓ User '{test_username}' deleted")
    
    print("\n" + "="*70)
    print(" CONCLUSION:")
    print("="*70)
    print("""
    Nếu password trên Caster ĐÚNG là '123456':
    → Vấn đề ở CLIENT (RTK app) - bạn nhập sai password
    
    Nếu password trên Caster bị SAI (vd: '12345'):
    → Vấn đề ở BACKEND - backend gửi sai password lên Caster
    """)
    
    print("\n✅ Test completed!\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)