import socket
import time

def push_config():
    host = "aitogy.com.vn"
    port = 2101
    password = "12345"
    mountpoint = "test_lte"

    print(f"Connecting to COMMAND SOURCE: {host}:{port}/{mountpoint}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((host, port))

    request = f"SOURCE {password} /{mountpoint}\r\nSource-Agent: NTRIP NtripServerCMD/1.0\r\n\r\n"
    sock.sendall(request.encode())

    response = sock.recv(4096)
    print("Handshake response:", response.decode(errors='ignore').strip()[:120])
    
    if b"ICY 200" not in response and b"200 OK" not in response:
        print("Command SOURCE rejected!")
        return

    print("Connected! Sending config commands...")
    
    commands = [
        b"unlogall\r\n",
        b"gpgga com2 1\r\n",
        b"rtcm1006 com2 1\r\n",
        b"rtcm1033 com2 1\r\n",
        b"rtcm1074 com2 1\r\n",
        b"rtcm1124 com2 1\r\n",
        b"rtcm1084 com2 1\r\n",
        b"rtcm1094 com2 1\r\n",
        b"rtcm1114 com2 1\r\n",
        b"rtcm1042 com2 1\r\n",
        b"rtcm1019 com2 1\r\n",
        b"rtcm1020 com2 1\r\n",
        b"saveconfig\r\n"
    ]

    for cmd in commands:
        print(f"Sending: {cmd.decode().strip()}")
        sock.sendall(cmd)
        time.sleep(0.2)

    print("Configuration sent successfully! Waiting 2 seconds before closing...")
    time.sleep(2)
    sock.close()

if __name__ == "__main__":
    push_config()
