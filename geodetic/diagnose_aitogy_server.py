#!/usr/bin/env python3
"""
Test xem server aitogy.com.vn:2101 có chấp nhận SOURCE request không
"""

import socket
import base64
import time

TARGET_HOST = "aitogy.com.vn"
TARGET_PORT = 2101
TEST_MOUNTPOINTS = ["TN.PhoYen", "YB.VanYen", "humga", "QN.CP"]
TEST_PASSWORDS = ["12345", "humga", "aitogy123", ""]

print("="*70)
print(" DIAGNOSTIC: Testing aitogy.com.vn:2101 SOURCE mode")
print("="*70)

def test_get_sourcetable():
    """Lấy danh sách mountpoint có sẵn"""
    print("\n1️⃣  Fetching SOURCETABLE...")
    print("-"*70)
    
    try:
        sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
        request = f"GET / HTTP/1.1\r\nHost: {TARGET_HOST}\r\n\r\n"
        sock.sendall(request.encode())
        time.sleep(1)
        
        response = sock.recv(8192).decode('ascii', errors='ignore')
        sock.close()
        
        print(response[:500])
        
        # Parse mountpoints
        lines = response.split('\n')
        mountpoints = []
        for line in lines:
            if line.startswith('STR;'):
                parts = line.split(';')
                if len(parts) > 1:
                    mountpoints.append(parts[1])
        
        if mountpoints:
            print(f"\n📋 Available mountpoints: {', '.join(mountpoints[:10])}")
            return mountpoints
        else:
            print("\n⚠️  No mountpoints found")
            return []
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

def test_source_request(mountpoint, password):
    """Test SOURCE request với mountpoint cụ thể"""
    print(f"\n2️⃣  Testing SOURCE → {mountpoint} (password: '{password or '(empty)'}')")
    print("-"*70)
    
    formats_to_test = [
        ("NTRIP v2.0 POST", lambda mp, pw: (
            f"POST /{mp} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"Authorization: Basic {base64.b64encode(f'source:{pw}'.encode()).decode()}\r\n"
            f"Transfer-Encoding: chunked\r\n\r\n"
        )),
        ("NTRIP v1.0 SOURCE", lambda mp, pw: (
            f"SOURCE {pw} /{mp} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
            f"Ntrip-Version: Ntrip/2.0\r\n"
            f"Authorization: Basic {base64.b64encode(f'source:{pw}'.encode()).decode()}\r\n\r\n"
        )),
    ]
    
    for format_name, builder in formats_to_test:
        print(f"\n   Testing format: {format_name}")
        
        try:
            sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
            request = builder(mountpoint, password)
            
            print(f"   Request:\n{request[:200]}")
            
            sock.sendall(request.encode())
            time.sleep(1)
            
            response = sock.recv(4096).decode('ascii', errors='ignore')
            sock.close()
            
            print(f"   Response: {response[:200]}")
            
            if "SOURCETABLE" in response:
                print(f"   ❌ Failed: Server returned SOURCETABLE")
            elif "200 OK" in response or "ICY 200" in response:
                print(f"   ✅ SUCCESS! Server accepted connection!")
                return True
            elif "401" in response or "Unauthorized" in response:
                print(f"   ⚠️  Wrong password")
            elif "404" in response:
                print(f"   ⚠️  Mountpoint not found")
            else:
                print(f"   ❓ Unknown response")
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    return False

def test_client_mode(mountpoint):
    """Test xem có thể GET (client mode) mountpoint không"""
    print(f"\n3️⃣  Testing CLIENT mode (GET) → {mountpoint}")
    print("-"*70)
    
    try:
        sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
        request = f"GET /{mountpoint} HTTP/1.1\r\nHost: {TARGET_HOST}\r\n\r\n"
        sock.sendall(request.encode())
        time.sleep(1)
        
        response = sock.recv(4096)
        sock.close()
        
        if b"200 OK" in response or b"ICY 200" in response:
            print(f"   ✅ Mountpoint exists and streams data (CLIENT mode works)")
            return True
        else:
            print(f"   ❌ Mountpoint doesn't work in CLIENT mode")
            print(f"   Response: {response[:200]}")
            return False
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False

if __name__ == "__main__":
    # Bước 1: Lấy danh sách mountpoint
    available_mountpoints = test_get_sourcetable()
    
    input("\nPress Enter to continue testing SOURCE mode...")
    
    # Bước 2: Test SOURCE với các mountpoint
    success = False
    
    for mp in TEST_MOUNTPOINTS:
        if success:
            break
        
        for pw in TEST_PASSWORDS:
            if test_source_request(mp, pw):
                print(f"\n{'='*70}")
                print(f"🎉 FOUND WORKING CONFIGURATION!")
                print(f"   Mountpoint: {mp}")
                print(f"   Password: {pw}")
                print(f"{'='*70}")
                success = True
                break
    
    if not success:
        print(f"\n{'='*70}")
        print("❌ NO WORKING SOURCE CONFIGURATION FOUND")
        print(f"{'='*70}")
        print("\nPossible reasons:")
        print("1. Server doesn't support SOURCE mode (client-only)")
        print("2. Need to create mountpoints first via admin panel")
        print("3. SOURCE requires different authentication")
        print("4. IP whitelist required")
        print("\nTesting if CLIENT mode works...")
        
        # Test CLIENT mode
        for mp in available_mountpoints[:3]:
            test_client_mode(mp)
        
        print("\n" + "="*70)
        print("RECOMMENDATION:")
        print("="*70)
        print("Contact aitogy.com.vn admin to:")
        print("1. Verify SOURCE mode is enabled")
        print("2. Create mountpoints: TN.PhoYen, YB.VanYen, TN.TP, TN.DaiTu, TN.VoNhai")
        print("3. Provide correct SOURCE password")
        print("4. Check if your IP needs to be whitelisted")