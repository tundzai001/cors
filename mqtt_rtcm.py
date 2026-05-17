import paho.mqtt.client as mqtt
import socket
import time
import threading

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# MQTT Config
BROKER = "aitogy.asia"
PORT = 1883
USERNAME = "mqttUser"
PASSWORD = "MqttPassword123$%^"
TOPIC = "device/860549070087338/upload"

# NTRIP Target Config
NTRIP_HOST = "aitogy.com.vn"
NTRIP_PORT = 2101
NTRIP_PASS = "12345"
NTRIP_MP = "humga"

# Device Config
COM_PORT = "com2" # Thay thành com2 nếu không thấy dữ liệu (như yêu cầu của user)

# ==============================================================================
# MQTT CALLBACKS
# ==============================================================================

def on_connect(client, userdata, flags, rc):
    print(f"✓ MQTT Connected: {rc}")
    client.subscribe(TOPIC)
    print(f"✓ Subscribed: {TOPIC}")

def on_message(client, userdata, msg):
    raw = msg.payload
    
    # Nhận diện định dạng
    format_type = "UNKNOWN"
    content_display = ""
    
    if raw.startswith(b'$'):
        format_type = "NMEA"
        try:
            content_display = raw.decode('ascii', errors='ignore').strip()
        except:
            content_display = raw.hex()
    elif len(raw) > 3 and raw[0] == 0xd3:
        format_type = "RTCM3"
        # RTCM3 length is 10 bits: bytes[1].bits[0-1] and bytes[2]
        payload_len = ((raw[1] & 0x03) << 8) | raw[2]
        # RTCM3 Message Type is 12 bits: bytes[3] (8 bits) and bytes[4].bits[4-7]
        if len(raw) >= 5:
            msg_type = (raw[3] << 4) | (raw[4] >> 4)
            content_display = f"Message Type: {msg_type}, Payload Len: {payload_len}"
        else:
            content_display = f"Payload Len: {payload_len} (Too short for Type)"
    else:
        content_display = raw.hex()[:100] + "..."

    print("-" * 40)
    print(f"Topic: {msg.topic}")
    print(f"Format: {format_type} | Size: {len(raw)} bytes")
    print(f"Data: {content_display}")

# ==============================================================================
# COMMAND SENDER
# ==============================================================================

def send_test_commands():
    print(f"\n🚀 Connecting to NTRIP Caster {NTRIP_HOST}:{NTRIP_PORT} for configuration...")
    try:
        sock = socket.create_connection((NTRIP_HOST, NTRIP_PORT), timeout=10)
        
        # NTRIP SOURCE Handshake
        handshake = f"SOURCE {NTRIP_PASS} /{NTRIP_MP}\r\nSource-Agent: NTRIP NtripServerCMD/1.0\r\n\r\n"
        sock.sendall(handshake.encode())
        
        # Wait for response
        resp = sock.recv(1024)
        print(f"📥 Handshake Response: {resp.decode(errors='ignore').strip()}")
        
        if b"ICY 200" not in resp and b"200 OK" not in resp:
            print("⚠️ Caster rejected the connection. Commands might not be sent.")
            # Still try to send commands just in case
        
        # Commands sequence
        commands = [
            "FRESET\r\n",
            "unlogall\r\n",
            "mode base time 60\r\n",
            # GPS Messages
            f"gpgga {COM_PORT} 1\r\n",
            f"gpgsa {COM_PORT} 1\r\n",
            f"gpgsv {COM_PORT} 1\r\n",
            # GLONASS Messages
            f"glgga {COM_PORT} 1\r\n",
            f"glgsa {COM_PORT} 1\r\n",
            f"glgsv {COM_PORT} 1\r\n",
            # Galileo Messages
            f"gagga {COM_PORT} 1\r\n",
            f"gagsa {COM_PORT} 1\r\n",
            f"gagsv {COM_PORT} 1\r\n",
            # BeiDou Messages
            f"bdgga {COM_PORT} 1\r\n",
            f"bdgsa {COM_PORT} 1\r\n",
            f"bdgsv {COM_PORT} 1\r\n",
            # RTCM messages
            f"rtcm1006 {COM_PORT} 1\r\n",
            f"rtcm1033 {COM_PORT} 1\r\n",
            f"rtcm1074 {COM_PORT} 1\r\n",
            f"rtcm1124 {COM_PORT} 1\r\n",
            f"rtcm1084 {COM_PORT} 1\r\n",
            f"rtcm1094 {COM_PORT} 1\r\n",
            f"rtcm1042 {COM_PORT} 1\r\n",
            f"rtcm1019 {COM_PORT} 1\r\n",
            f"rtcm1020 {COM_PORT} 1\r\n",
            f"rtcm1045 {COM_PORT} 1\r\n",
            "saveconfig\r\n"
        ]
        
        print("📡 Starting command sequence...")
        for cmd in commands:
            print(f"   -> Sending: {cmd.strip()}")
            sock.sendall(cmd.encode())
            
            # Apply delays based on command type
            if "FRESET" in cmd:
                time.sleep(2.0)
            elif "unlogall" in cmd:
                time.sleep(1.0)
            elif "mode base" in cmd:
                time.sleep(2.0)
            elif "saveconfig" in cmd:
                time.sleep(1.0)
            else:
                time.sleep(0.2)
        
        print("✅ All commands sent. Waiting for data on MQTT...")
        
        # Keep socket open for a while to ensure all data is flushed
        time.sleep(5)
        sock.close()
        print("🔌 Config socket closed.")
        
    except Exception as e:
        print(f"❌ Error during command sequence: {e}")

# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  NTRIP CONFIG & MQTT MONITOR TEST TOOL")
    print("=" * 60)
    
    client = mqtt.Client()
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    
    print("🔗 Connecting to MQTT...")
    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        
        # Start the command sequence in a separate thread
        threading.Thread(target=send_test_commands, daemon=True).start()
        
        # Keep the main thread alive to see MQTT messages
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n👋 Stopping test...")
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        print(f"❌ MQTT Error: {e}")