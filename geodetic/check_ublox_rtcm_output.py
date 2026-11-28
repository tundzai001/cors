import serial
import time
import sys
from agent_universal import RTCMSignalDecoder

def _print_decoded(decoded):
    print(f"[{time.strftime('%H:%M:%S')}] Đã decode thành công một cụm bản tin!")
    packets = decoded.get("packets", [])
    msg_types = [packet.get("messageType") for packet in packets]
    print(f"   -> Các ID nhận được: {msg_types}")

    msm7_packets = [packet for packet in packets if packet.get("messageType") in (1077, 1087, 1097, 1127)]
    if msm7_packets:
        print("   -> [!!!] PHÁT HIỆN BẢN TIN MSM7 [!!!]")
        for packet in msm7_packets:
            print(f"      + Type {packet['messageType']}: {packet.get('satelliteCount', 0)} vệ tinh")

    satellites = decoded.get("satellites", [])
    if satellites:
        print(f"   -> Đã trích xuất thông tin {len(satellites)} vệ tinh từ các bản tin MSM:")
        for satellite in satellites[:5]:
            print(f"      * {satellite['id']}: CNR = {satellite.get('cnr', 0)} dBHz")
        if len(satellites) > 5:
            print(f"      * ... (và {len(satellites) - 5} vệ tinh khác)")
    print("-" * 50)


def _listen_for_rtcm(serial_port, decoder):
    while True:
        chunk = serial_port.read(2048)
        if not chunk:
            continue
        decoded = decoder.decode_sync("test_device", chunk)
        if decoded and decoded.get("packets"):
            _print_decoded(decoded)


def _print_stats(decoder):
    print("\n\n[*] TỔNG KẾT THỐNG KÊ (rtcm_stats) CỦA AGENT:")
    print("-" * 40)
    stats = decoder.get_stats("test_device")
    if not stats:
        print("Không đếm được bản tin nào.")
    else:
        for msg_type, info in sorted(stats.items(), key=lambda item: int(item[0])):
            print(f"Type {msg_type:<5} : {info['count']} lần (chu kỳ {info['interval']}s)")
    print("-" * 40)


def main(port="/dev/ttyUSB0", baudrate=115200):
    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        print(f"[+] Đã kết nối tới {port} (Baudrate: {baudrate})")
    except Exception as error:
        print(f"[-] Lỗi kết nối tới {port}: {error}")
        print("Sử dụng: python check_ublox_rtcm_output.py <port> <baudrate>")
        return

    decoder = RTCMSignalDecoder()
    print("[*] Đang lắng nghe và DECODE bản tin RTCM3... (Bấm Ctrl+C để dừng)\n")
    try:
        _listen_for_rtcm(ser, decoder)
    except KeyboardInterrupt:
        _print_stats(decoder)

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    main(port, baud)
