import asyncio
import socket
import base64
import time
import traceback
from rtcm_signal_decoder import rtcm_signal_decoder

async def test():
    try:
        host = 'aitogy.com.vn'
        port = 2101
        mountpoint = 'TN.DaiTu'
        user = 'aitogy'
        password = 'aitogy'
        
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10.0)
        s.connect((host, port))
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        req = f"GET /{mountpoint} HTTP/1.0\r\nUser-Agent: NTRIP Client\r\nAuthorization: Basic {auth}\r\n\r\n"
        s.sendall(req.encode())
        print("Response:", s.recv(1024).decode(errors='replace'))
        
        print("Connected to TN.DaiTu. Listening...")
        
        start_time = time.time()
        
        while True:
            try:
                data = s.recv(4096)
            except socket.timeout:
                print("Socket timeout")
                continue
                
            if not data: 
                print("Socket closed by server")
                break
            
            result = await rtcm_signal_decoder.decode("test_serial", data)
            if result and result.get("satellites"):
                has_pos = False
                for sat in result["satellites"]:
                    if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0:
                        print(f"[{sat['system']}] PRN {sat['prn']}: El={sat['elevation']:.2f}, Az={sat['azimuth']:.2f}")
                        has_pos = True
                
                if has_pos:
                    print("Successfully calculated Elevation and Azimuth!")
                    break
                    
            if time.time() - start_time > 60:
                print("Timeout waiting for ephemeris data.")
                break
    except Exception as e:
        traceback.print_exc()

asyncio.run(test())
