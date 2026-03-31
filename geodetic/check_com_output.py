#!/usr/bin/env python3
import argparse
import sys
import time

import serial


def main():
    parser = argparse.ArgumentParser(description="Check realtime output from COM/serial port.")
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM3 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200, help="Baudrate (default: 115200)")
    parser.add_argument("--timeout", type=float, default=1.0, help="Read timeout seconds (default: 1.0)")
    parser.add_argument("--duration", type=int, default=0, help="Run seconds, 0 = infinite")
    parser.add_argument("--hex", action="store_true", dest="show_hex", help="Print hex dump for non-ASCII chunks")
    args = parser.parse_args()

    total_bytes = 0
    nmea_count = 0
    rtcm_count = 0
    ubx_count = 0
    unknown_count = 0

    start_ts = time.time()
    last_stat_ts = start_ts

    print(f"[INFO] Opening {args.port} @ {args.baud} ...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    except Exception as e:
        print(f"[ERROR] Cannot open port: {e}")
        sys.exit(1)

    print("[INFO] Reading... Press Ctrl+C to stop.")
    try:
        while True:
            if args.duration > 0 and (time.time() - start_ts) >= args.duration:
                break

            data = ser.read(4096)
            if not data:
                now = time.time()
                if now - last_stat_ts >= 2.0:
                    print("[WARN] No data received in last interval...")
                    last_stat_ts = now
                continue

            total_bytes += len(data)

            # Quick protocol detection
            if data.startswith(b"$"):
                nmea_count += 1
                line = data.decode("ascii", errors="ignore").strip()
                if line:
                    print(f"[NMEA] {line[:180]}")
            elif len(data) >= 1 and data[0] == 0xD3:
                rtcm_count += 1
                print(f"[RTCM] chunk={len(data)}B")
            elif len(data) >= 2 and data[0] == 0xB5 and data[1] == 0x62:
                ubx_count += 1
                print(f"[UBX]  chunk={len(data)}B")
            else:
                unknown_count += 1
                txt = data.decode("ascii", errors="ignore").strip()
                if txt:
                    print(f"[TXT]  {txt[:180]}")
                elif args.show_hex:
                    print(f"[HEX]  {data[:64].hex(' ')}")

            now = time.time()
            if now - last_stat_ts >= 5.0:
                elapsed = max(1e-6, now - start_ts)
                bps = total_bytes / elapsed
                print(
                    f"[STAT] bytes={total_bytes} rate={bps:.1f} B/s "
                    f"NMEA={nmea_count} RTCM={rtcm_count} UBX={ubx_count} UNKNOWN={unknown_count}"
                )
                last_stat_ts = now

    except KeyboardInterrupt:
        pass
    finally:
        try:
            ser.close()
        except Exception:
            pass

    elapsed = max(1e-6, time.time() - start_ts)
    print("\n[DONE]")
    print(f"  elapsed : {elapsed:.1f}s")
    print(f"  bytes   : {total_bytes}")
    print(f"  avg bps : {total_bytes / elapsed:.1f} B/s")
    print(f"  NMEA    : {nmea_count}")
    print(f"  RTCM    : {rtcm_count}")
    print(f"  UBX     : {ubx_count}")
    print(f"  UNKNOWN : {unknown_count}")


if __name__ == "__main__":
    main()

