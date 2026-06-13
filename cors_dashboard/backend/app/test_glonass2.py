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
    
    while time.time() - start_time < 45:
        try:
            data = s.recv(4096)
            if not data: break
            try:
                result = await rtcm_signal_decoder.decode("test_glonass", data)
                if result and result.get("satellites"):
                    for sat in result["satellites"]:
                        if sat["system"] == "ru" and sat["elevation"] != 0.0:
                            print(f"Got GLONASS: PRN {sat['prn']}, El={sat['elevation']:.2f}, Az={sat['azimuth']:.2f}")
                            return
            except Exception as inner_e:
                print("Decode Error:", inner_e)
                traceback.print_exc()
        except Exception as e:
            print("Socket Error:", e)
            break
            
    # Check if we at least got 1020
    ephems = rtcm_signal_decoder._ephemeris_data.get("test_glonass", {})
    ru_ephs = {k: v for k,v in ephems.items() if "ru" in k}
    print("Ephemeris cache for ru:", ru_ephs.keys())
    
asyncio.run(test())
