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
    
    print(f"Connected to {mountpoint}. Listening for RTCM to decode Azimuth/Elevation...")
    start_time = time.time()
    packet_count = 0
    
    while True:
        try:
            data = s.recv(4096)
            if not data: break
            
            result = await rtcm_signal_decoder.decode("test_pq01", data)
            packet_count += 1
            
            if result and result.get("satellites"):
                # Check if we have valid non-zero elevations
                has_valid = any(sat["elevation"] != 0.0 for sat in result["satellites"])
                if has_valid:
                    print("\n--- SATELLITE CALCULATION RESULTS ---")
                    for sat in result["satellites"]:
                        if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0:
                            print(f"[{sat['system']}] PRN {sat['prn']:>2}: El = {sat['elevation']:>6.2f} deg, Az = {sat['azimuth']:>6.2f} deg, SNR = {sat.get('snr', 0)}")
                    print("-------------------------------------")
                    return
            
            if time.time() - start_time > 120:
                print("Timeout waiting for Ephemeris+MSM to compute coordinates.")
                break
        except Exception as e:
            print("Error:", e)
            break

asyncio.run(test())
