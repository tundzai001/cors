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
    
    print(f"Connected to {mountpoint}. Listening for 45s to get all constellations...")
    start_time = time.time()
    
    last_valid_sats = []
    
    while time.time() - start_time < 45:
        try:
            data = s.recv(4096)
            if not data: break
            
            result = await rtcm_signal_decoder.decode("test_ui_pq01_all", data)
            
            if result and result.get("satellites"):
                last_valid_sats = [sat for sat in result["satellites"] if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0]
                
        except Exception as e:
            print("Error:", e)
            break
            
    print("\n--- DECODED SATELLITE DATA FOR UI ---")
    for sat in sorted(last_valid_sats, key=lambda x: (x['system'], x['prn'])):
        print(f"System: {sat['system']:<2} | PRN: {sat['prn']:<2} | Elevation: {sat['elevation']:>5.1f} | Azimuth: {sat['azimuth']:>5.1f} | SNR: {sat.get('snr', 0)}")
    print("-------------------------------------")
    print(f"Total satellites ready for UI Skyplot: {len(last_valid_sats)}")

asyncio.run(test())
