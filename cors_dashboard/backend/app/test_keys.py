import asyncio
import socket
import base64
import time
from rtcm_signal_decoder import rtcm_signal_decoder

async def test():
    host = 'aitogy.com.vn'
    port = 2101
    mountpoint = 'BK.BaBe'
    user = 'aitogy'
    password = 'aitogy'
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10.0)
    s.connect((host, port))
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = f"GET /{mountpoint} HTTP/1.0\r\nUser-Agent: NTRIP Client\r\nAuthorization: Basic {auth}\r\n\r\n"
    s.sendall(req.encode())
    s.recv(1024)
    
    while True:
        data = s.recv(4096)
        if not data: break
        
        result = await rtcm_signal_decoder.decode("test_serial", data)
        if rtcm_signal_decoder._ephemeris_data.get("test_serial"):
            print("KEYS:", rtcm_signal_decoder._ephemeris_data["test_serial"].keys())
            if result and result.get("satellites"):
                for sat in result["satellites"]:
                    if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0:
                        print(f"[{sat['system']}] PRN {sat['prn']}: El={sat['elevation']:.2f}, Az={sat['azimuth']:.2f}")
            break

asyncio.run(test())
