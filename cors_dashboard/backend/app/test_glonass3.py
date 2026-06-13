import asyncio
import socket
import base64
import time
import traceback
from rtcm_signal_decoder import rtcm_signal_decoder

async def test():
    host = 'aitogy.com.vn'
    port = 2101
    mountpoint = 'PQ01'
    user = 'aitogy'
    password = 'aitogy'
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10.0)
    s.connect((host, port))
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    
    req = f"GET /{mountpoint} HTTP/1.0\r\nUser-Agent: NTRIP Client\r\nAuthorization: Basic {auth}\r\n\r\n"
    s.sendall(req.encode())
    s.recv(1024)
    
    print("Listening for GLONASS...")
    start_time = time.time()
    
    while time.time() - start_time < 20:
        try:
            data = s.recv(4096)
            if not data: break
            try:
                result = await rtcm_signal_decoder.decode("test_glonass", data)
                if result and result.get("satellites"):
                    for sat in result["satellites"]:
                        if sat["system"] == "ru" and sat["elevation"] > 0:
                            print(f"Got GLONASS: PRN {sat['prn']}, El={sat['elevation']:.2f}, Az={sat['azimuth']:.2f}")
            except Exception as inner_e:
                pass
        except Exception as e:
            pass
            
asyncio.run(test())
