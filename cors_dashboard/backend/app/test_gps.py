import asyncio
import socket
import base64
import time
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
    
    print("Listening for GPS (us)...")
    start_time = time.time()
    
    while time.time() - start_time < 30:
        try:
            data = s.recv(4096)
            if not data: break
            result = await rtcm_signal_decoder.decode("test_pq01_gps", data)
            
            if result and result.get("satellites"):
                for sat in result["satellites"]:
                    if sat["system"] == "us" and sat["elevation"] != 0.0:
                        print(f"Got GPS: PRN {sat['prn']}, El={sat['elevation']}")
                        return
        except Exception as e:
            print("Error:", e)
            break
    print("No valid GPS found.")
    print("Ephemeris Cache for us:", [k for k in rtcm_signal_decoder._ephemeris_data.get("test_pq01_gps", {}).keys() if "us" in k])

asyncio.run(test())
