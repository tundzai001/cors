import asyncio
import socket
import base64
import time

def test():
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
    s.recv(1024)
    
    buffer = bytearray()
    start_time = time.time()
    
    while time.time() - start_time < 30:
        data = s.recv(4096)
        if not data: break
        buffer.extend(data)
        
        while len(buffer) >= 3:
            start = buffer.find(b"\xD3")
            if start < 0:
                buffer.clear()
                break
            if start > 0:
                buffer = buffer[start:]
            if len(buffer) < 3:
                break
                
            length = ((buffer[1] & 0x03) << 8) | buffer[2]
            packet_len = length + 6
            if len(buffer) < packet_len:
                break
                
            packet = buffer[:packet_len]
            buffer = buffer[packet_len:]
            
            payload = packet[3:-3]
            msg_type = (payload[0] << 4) | (payload[1] >> 4)
            print(f"Received msg_type: {msg_type}")

test()
