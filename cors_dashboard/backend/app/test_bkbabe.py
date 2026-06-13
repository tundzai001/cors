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
    
    print(f"Connected to {mountpoint}. Listening for RTCM...")
    start_time = time.time()
    packet_count = 0
    msg_types = set()
    
    buffer = b""
    while True:
        try:
            data = s.recv(4096)
            if not data: break
            
            buffer += data
            # Manually extract message types
            temp_buf = buffer
            while len(temp_buf) >= 3:
                start = temp_buf.find(b"\xD3")
                if start < 0:
                    temp_buf = b""
                    break
                if start > 0:
                    temp_buf = temp_buf[start:]
                if len(temp_buf) < 3:
                    break
                
                message_length = ((temp_buf[1] & 0x03) << 8) | temp_buf[2]
                packet_length = 3 + message_length + 3
                if len(temp_buf) < packet_length:
                    break
                
                packet = temp_buf[:packet_length]
                temp_buf = temp_buf[packet_length:]
                
                payload_bytes = packet[3:-3]
                if len(payload_bytes) >= 2:
                    msg_type = (payload_bytes[0] << 4) | (payload_bytes[1] >> 4)
                    msg_types.add(msg_type)
            
            result = await rtcm_signal_decoder.decode("test_serial", data)
            packet_count += 1
            
            if packet_count % 10 == 0:
                print(f"Packets: {packet_count}. Msg Types Seen: {sorted(list(msg_types))}. Base: {len(rtcm_signal_decoder._base_positions)}. Eph: {len(rtcm_signal_decoder._ephemeris_data.get('test_serial', {}))}")
            
            if result and result.get("satellites"):
                for sat in result["satellites"]:
                    if sat["elevation"] != 0.0 or sat["azimuth"] != 0.0:
                        print(f"SUCCESS! [{sat['system']}] PRN {sat['prn']}: El={sat['elevation']:.2f}, Az={sat['azimuth']:.2f}")
                        return
                        
            if time.time() - start_time > 120:
                print("Timeout. Final State:")
                print("Msg Types Received:", sorted(list(msg_types)))
                print("Eph keys:", rtcm_signal_decoder._ephemeris_data.get("test_serial", {}).keys())
                break
        except Exception as e:
            print("Error:", e)
            break

asyncio.run(test())
