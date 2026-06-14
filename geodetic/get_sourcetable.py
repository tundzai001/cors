import socket
import base64

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(10.0)
s.connect(('aitogy.com.vn', 2101))
s.sendall(b"GET / HTTP/1.0\r\n\r\n")

data = b""
while True:
    chunk = s.recv(4096)
    if not chunk: break
    data += chunk

lines = data.decode('utf-8', errors='ignore').split('\n')
for line in lines:
    if 'VuQuang' in line or 'HT.' in line:
        print(line.strip())
