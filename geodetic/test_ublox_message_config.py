#!/usr/bin/env python3
"""
Test ZED-F9P message output configuration before putting it in command_builder.

Default mode is safe: build packets and print them only.
Use --apply to send CFG-VALSET to RAM only.
Use --save together with --apply only after the live output looks correct.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from collections import Counter
from typing import Any


DEFAULT_MQTT_BROKER = "45.117.179.134"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_USERNAME = "mqttUser"
DEFAULT_MQTT_PASSWORD = "MqttPassword123$%^"
DEFAULT_TOPIC_ROOT = "cors/debug/ublox"


SAVE_CONFIG = bytes.fromhex(
    "B5 62 06 09 0D 00 00 00 00 00 FF FF 00 00 00 00 00 00 03 1D AB"
)


TARGET_KEYS = {
    # NMEA for dashboard.
    "NMEA_GGA_UART1": 0x209100BB,
    "NMEA_GGA_UART2": 0x209100BC,
    "NMEA_GGA_USB": 0x209100BD,
    "NMEA_GSA_UART1": 0x209100C0,
    "NMEA_GSA_UART2": 0x209100C1,
    "NMEA_GSA_USB": 0x209100C2,
    "NMEA_GSV_UART1": 0x209100C5,
    "NMEA_GSV_UART2": 0x209100C6,
    "NMEA_GSV_USB": 0x209100C7,
    "NMEA_GST_UART1": 0x209100D4,
    "NMEA_GST_UART2": 0x209100D5,
    "NMEA_GST_USB": 0x209100D6,
    # Native u-blox accuracy / high precision LLH.
    "UBX_NAV_PVT_UART1": 0x20910007,
    "UBX_NAV_PVT_UART2": 0x20910008,
    "UBX_NAV_PVT_USB": 0x20910009,
    "UBX_NAV_HPPOSLLH_UART1": 0x20910034,
    "UBX_NAV_HPPOSLLH_UART2": 0x20910035,
    "UBX_NAV_HPPOSLLH_USB": 0x20910036,
    # Base RTCM MSM4 stream.
    "RTCM1005_UART1": 0x209102BE,
    "RTCM1005_UART2": 0x209102BF,
    "RTCM1005_USB": 0x209102C0,
    "RTCM1074_UART1": 0x2091035F,
    "RTCM1074_UART2": 0x20910360,
    "RTCM1074_USB": 0x20910361,
    "RTCM1084_UART1": 0x20910364,
    "RTCM1084_UART2": 0x20910365,
    "RTCM1084_USB": 0x20910366,
    "RTCM1094_UART1": 0x20910368,
    "RTCM1094_UART2": 0x20910369,
    "RTCM1094_USB": 0x2091036A,
    "RTCM1124_UART1": 0x2091036E,
    "RTCM1124_UART2": 0x2091036F,
    "RTCM1124_USB": 0x20910370,
    "RTCM1230_UART1": 0x20910304,
    "RTCM1230_UART2": 0x20910305,
    "RTCM1230_USB": 0x20910306,
}


PORT_SUFFIXES = ("UART1", "UART2", "USB")
PORT_OFFSETS = {"UART1": 1, "UART2": 2, "USB": 3}

COMMON_NMEA_BASE_KEYS = {
    "DTM": 0x209100A6,
    "RMC": 0x209100AB,
    "GNS": 0x209100B5,
    "GGA": 0x209100BA,
    "GSA": 0x209100BF,
    "GSV": 0x209100C4,
    "GLL": 0x209100C9,
    "GRS": 0x209100CE,
    "GST": 0x209100D3,
    "ZDA": 0x209100D8,
    "GBS": 0x209100DD,
    "VLW": 0x209100E7,
    "VTG": 0x209100B0,
    "PUBX00": 0x209100EC,
    "PUBX03": 0x209100F1,
    "PUBX04": 0x209100F6,
}

ALLOWED_NMEA_TYPES = {"GGA", "GSA", "GSV", "GST"}


def ubx_packet(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    header = b"\xB5\x62" + bytes([msg_class, msg_id]) + len(payload).to_bytes(2, "little")
    ck_a = 0
    ck_b = 0
    for byte in header[2:] + payload:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return header + payload + bytes([ck_a, ck_b])


def cfg_valset(entries: dict[int, int], layers: int) -> bytes:
    payload = bytearray([0x00, layers & 0x07, 0x00, 0x00])
    for key, value in entries.items():
        payload.extend(key.to_bytes(4, "little"))
        payload.append(value & 0xFF)
    return ubx_packet(0x06, 0x8A, bytes(payload))


def selected_entries(ports: set[str], mode: str = "enable") -> dict[int, int]:
    entries: dict[int, int] = {}
    if mode == "strict":
        for msg_type, base_key in COMMON_NMEA_BASE_KEYS.items():
            for port in ports:
                entries[base_key + PORT_OFFSETS[port]] = 1 if msg_type in ALLOWED_NMEA_TYPES else 0
    for name, key in TARGET_KEYS.items():
        if any(name.endswith(port) for port in ports):
            entries[key] = 1
    return entries


def hex_line(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def parse_nmea_type(line: str) -> str | None:
    if not line.startswith("$") or len(line) < 6:
        return None
    return line[3:6]


def listen_serial(port: str, baud: int, seconds: float, print_output: bool = True) -> dict[str, Any]:
    try:
        import serial  # type: ignore
    except ImportError:
        return {"ok": False, "error": "pyserial is not installed"}

    end = time.time() + seconds
    nmea_counts: Counter[str] = Counter()
    ubx_counts: Counter[str] = Counter()
    rtcm_count = 0
    sample_lines: list[str] = []

    started_at = time.time()
    with serial.Serial(port, baudrate=baud, timeout=0.2) as ser:
        buf = bytearray()
        while time.time() < end:
            chunk = ser.read(4096)
            if not chunk:
                continue
            buf.extend(chunk)
            while buf:
                if buf[0] == 0x24:
                    try:
                        nl = buf.index(0x0A)
                    except ValueError:
                        break
                    raw = bytes(buf[: nl + 1])
                    del buf[: nl + 1]
                    line = raw.decode("ascii", errors="ignore").strip()
                    msg_type = parse_nmea_type(line)
                    if msg_type:
                        nmea_counts[msg_type] += 1
                        if len(sample_lines) < 8:
                            sample_lines.append(line)
                    continue
                if len(buf) >= 6 and buf[0] == 0xB5 and buf[1] == 0x62:
                    length = int.from_bytes(buf[4:6], "little")
                    total = 8 + length
                    if len(buf) < total:
                        break
                    cls = buf[2]
                    mid = buf[3]
                    ubx_counts[f"{cls:02X}-{mid:02X}"] += 1
                    del buf[:total]
                    continue
                if len(buf) >= 3 and buf[0] == 0xD3:
                    length = ((buf[1] & 0x03) << 8) | buf[2]
                    total = length + 6
                    if len(buf) < total:
                        break
                    rtcm_count += 1
                    del buf[:total]
                    continue
                del buf[0]

    summary = {
        "ok": True,
        "port": port,
        "baud": baud,
        "seconds": round(time.time() - started_at, 2),
        "nmea": dict(nmea_counts),
        "ubx": dict(ubx_counts),
        "rtcm_packets": rtcm_count,
        "samples": sample_lines,
    }

    if print_output:
        print("\nLive output summary")
        print("NMEA:", summary["nmea"])
        print("UBX:", summary["ubx"])
        print("RTCM packets:", rtcm_count)
        if sample_lines:
            print("\nSample NMEA:")
            for line in sample_lines:
                print(line)
    return summary


def send_packets(port: str, baud: int, packets: list[bytes], listen: float, print_output: bool = True) -> dict[str, Any]:
    try:
        import serial  # type: ignore
    except ImportError:
        return {"ok": False, "error": "pyserial is not installed"}

    with serial.Serial(port, baudrate=baud, timeout=1, write_timeout=2) as ser:
        for packet in packets:
            ser.write(packet)
            ser.flush()
            time.sleep(0.25)
    result: dict[str, Any] = {
        "ok": True,
        "port": port,
        "baud": baud,
        "packets_sent": len(packets),
        "packet_lengths": [len(packet) for packet in packets],
    }
    if listen > 0:
        result["listen"] = listen_serial(port, baud, listen, print_output=print_output)
    return result


def build_config_result(ports: set[str], save: bool, mode: str = "enable") -> tuple[list[bytes], dict[str, Any]]:
    if mode not in ("enable", "strict"):
        raise ValueError("mode must be enable or strict")
    entries = selected_entries(ports, mode=mode)
    valset = cfg_valset(entries, layers=0x01)
    packets = [valset]
    if save:
        packets.append(SAVE_CONFIG)
    return packets, {
        "ports": sorted(ports),
        "mode": mode,
        "save": save,
        "keys_enabled": len(entries),
        "keys_set_to_zero": sum(1 for value in entries.values() if value == 0),
        "cfg_valset_len": len(valset),
        "cfg_valset_hex": hex_line(valset),
        "save_hex": hex_line(SAVE_CONFIG) if save else None,
    }


def parse_ports(raw: Any) -> set[str]:
    if raw is None:
        raw = "UART1,UART2,USB"
    if isinstance(raw, str):
        ports = {part.strip().upper() for part in raw.split(",") if part.strip()}
    elif isinstance(raw, list):
        ports = {str(part).strip().upper() for part in raw if str(part).strip()}
    else:
        raise ValueError("ports must be string or list")
    bad_ports = ports - set(PORT_SUFFIXES)
    if bad_ports:
        raise ValueError(f"invalid ports: {', '.join(sorted(bad_ports))}")
    return ports


def publish_json(client: Any, topic: str, payload: dict[str, Any]) -> None:
    payload = {
        "ts": time.time(),
        **payload,
    }
    client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str), qos=0, retain=False)


class MqttUbxTester:
    def __init__(self, args: argparse.Namespace):
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise SystemExit("paho-mqtt is not installed on this Pi") from exc

        self.args = args
        self.test_id = args.test_id or socket.gethostname()
        self.base_topic = f"{args.topic_root.rstrip('/')}/{self.test_id}"
        self.cmd_topic = f"{self.base_topic}/cmd"
        self.status_topic = f"{self.base_topic}/status"
        self.result_topic = f"{self.base_topic}/result"
        self.log_topic = f"{self.base_topic}/log"
        self.busy = threading.Lock()
        self.client = mqtt.Client(client_id=f"ublox-test-{self.test_id}-{int(time.time())}", clean_session=True)
        self.client.username_pw_set(args.mqtt_username, args.mqtt_password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        publish_json(client, self.status_topic, {
            "event": "online",
            "rc": rc,
            "test_id": self.test_id,
            "port": self.args.port,
            "baud": self.args.baud,
            "cmd_topic": self.cmd_topic,
            "result_topic": self.result_topic,
            "supported_actions": ["dry_run", "listen", "apply", "save", "stop"],
        })
        client.subscribe(self.cmd_topic, qos=0)

    def on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        try:
            command = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except Exception as exc:
            publish_json(client, self.result_topic, {"ok": False, "error": f"invalid json: {exc}"})
            return
        threading.Thread(target=self.handle_command, args=(command,), daemon=True).start()

    def handle_command(self, command: dict[str, Any]) -> None:
        if not self.busy.acquire(blocking=False):
            publish_json(self.client, self.result_topic, {"ok": False, "error": "busy", "command": command})
            return
        try:
            action = str(command.get("action", "")).strip().lower()
            if action == "stop":
                publish_json(self.client, self.status_topic, {"event": "stopping"})
                self.client.disconnect()
                return
            if action == "dry_run":
                ports = parse_ports(command.get("ports"))
                mode = str(command.get("mode", "enable")).strip().lower()
                packets, cfg = build_config_result(ports, save=bool(command.get("save", False)), mode=mode)
                publish_json(self.client, self.result_topic, {"ok": True, "action": action, **cfg, "packet_lengths": [len(p) for p in packets]})
                return
            if action == "listen":
                seconds = float(command.get("seconds", command.get("listen", self.args.listen)))
                result = listen_serial(self.args.port, self.args.baud, seconds, print_output=False)
                publish_json(self.client, self.result_topic, {"action": action, **result})
                return
            if action in ("apply", "save"):
                ports = parse_ports(command.get("ports"))
                mode = str(command.get("mode", "enable")).strip().lower()
                save = action == "save" or bool(command.get("save", False))
                listen = float(command.get("listen", self.args.listen))
                packets, cfg = build_config_result(ports, save=save, mode=mode)
                publish_json(self.client, self.status_topic, {"event": "applying", **cfg})
                result = send_packets(self.args.port, self.args.baud, packets, listen, print_output=False)
                publish_json(self.client, self.result_topic, {"action": action, "config": cfg, **result})
                return
            publish_json(self.client, self.result_topic, {"ok": False, "error": f"unknown action: {action}", "command": command})
        except Exception as exc:
            publish_json(self.client, self.result_topic, {"ok": False, "error": str(exc), "command": command})
        finally:
            try:
                self.busy.release()
            except RuntimeError:
                pass

    def run(self) -> None:
        self.client.connect(self.args.mqtt_host, self.args.mqtt_port, keepalive=30)
        self.client.loop_start()
        print(f"MQTT UBX tester online: {self.base_topic}")
        print(f"Command topic: {self.cmd_topic}")
        print(f"Result topic:  {self.result_topic}")
        try:
            while True:
                publish_json(self.client, self.status_topic, {
                    "event": "heartbeat",
                    "test_id": self.test_id,
                    "port": self.args.port,
                    "baud": self.args.baud,
                    "cmd_topic": self.cmd_topic,
                })
                time.sleep(10)
        except KeyboardInterrupt:
            publish_json(self.client, self.status_topic, {"event": "offline"})
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test u-blox ZED-F9P NMEA/UBX/RTCM output config")
    parser.add_argument("--port", help="Serial port, for example COM14 or /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--ports", default="UART1,UART2,USB", help="Comma list: UART1,UART2,USB")
    parser.add_argument("--mode", choices=("enable", "strict"), default="enable", help="strict also disables noisy NMEA on selected ports")
    parser.add_argument("--apply", action="store_true", help="Send CFG-VALSET to receiver RAM")
    parser.add_argument("--save", action="store_true", help="Also send UBX-CFG-CFG save after apply")
    parser.add_argument("--listen", type=float, default=10.0, help="Listen seconds after apply, or with --listen-only")
    parser.add_argument("--listen-only", action="store_true", help="Only inspect current serial output")
    parser.add_argument("--mqtt", action="store_true", help="Run as MQTT-controlled tester on the Pi")
    parser.add_argument("--mqtt-host", default=DEFAULT_MQTT_BROKER)
    parser.add_argument("--mqtt-port", type=int, default=DEFAULT_MQTT_PORT)
    parser.add_argument("--mqtt-username", default=DEFAULT_MQTT_USERNAME)
    parser.add_argument("--mqtt-password", default=DEFAULT_MQTT_PASSWORD)
    parser.add_argument("--topic-root", default=DEFAULT_TOPIC_ROOT)
    parser.add_argument("--test-id", default="", help="Topic suffix; defaults to Pi hostname")
    args = parser.parse_args()

    if args.mqtt:
        if not args.port:
            raise SystemExit("--mqtt requires --port")
        MqttUbxTester(args).run()
        return 0

    ports = {part.strip().upper() for part in args.ports.split(",") if part.strip()}
    bad_ports = ports - set(PORT_SUFFIXES)
    if bad_ports:
        raise SystemExit(f"Invalid --ports values: {', '.join(sorted(bad_ports))}")

    if args.listen_only:
        if not args.port:
            raise SystemExit("--listen-only requires --port")
        listen_serial(args.port, args.baud, args.listen)
        return 0

    packets, cfg = build_config_result(ports, save=args.save, mode=args.mode)

    print(f"Selected ports: {', '.join(sorted(ports))}")
    print(f"Keys enabled: {cfg['keys_enabled']}")
    print(f"Keys set to zero: {cfg['keys_set_to_zero']}")
    print(f"CFG-VALSET length: {cfg['cfg_valset_len']} bytes")
    print("CFG-VALSET hex:")
    print(cfg["cfg_valset_hex"])
    if args.save:
        print("\nSAVE-CONFIG hex:")
        print(cfg["save_hex"])

    if not args.apply:
        print("\nDry run only. Add --apply --port COMx to send to receiver RAM.")
        return 0
    if not args.port:
        raise SystemExit("--apply requires --port")

    send_packets(args.port, args.baud, packets, args.listen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
