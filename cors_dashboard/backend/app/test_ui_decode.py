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
    
    print(f"Connected to {mountpoint}. Listening and decoding for UI...")
    start_time = time.time()
    
    while True:
        try:
            data = s.recv(4096)
            if not data: break
            
            result = await rtcm_signal_decoder.decode("test_ui_pq01", data)
            
            if result and result.get("satellites"):
                # Filter satellites that have valid elevation (i.e. Ephemeris was received)
                valid_sats = [sat for sat in result["satellites"] if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0]
                
                # If we've successfully computed coordinates for at least 8 satellites
                if len(valid_sats) >= 8:
                    print("\n--- DECODED SATELLITE DATA FOR UI ---")
                    for sat in valid_sats:
                        print(f"System: {sat['system']:<2} | PRN: {sat['prn']:<2} | Elevation: {sat['elevation']:>5.1f} | Azimuth: {sat['azimuth']:>5.1f} | SNR: {sat.get('snr', 0)}")
                    print("-------------------------------------")
                    print(f"Total satellites ready for UI Skyplot: {len(valid_sats)}")
                    return
            
            if time.time() - start_time > 120:
                print("Timeout waiting for enough Ephemeris data.")
                break
        except Exception as e:
            print("Error:", e)
            break

asyncio.run(test())
