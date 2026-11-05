#!/usr/bin/env python3
"""
Standalone u-blox Rover -> NTRIP -> fixed Base -> RTCM decoder test.

The script deliberately does not start the production agent and does not
change agent_universal.py. It configures one receiver on the selected serial
port, waits for RTK fixed from the NTRIP corrections, averages GGA positions,
switches the receiver to fixed Base, and reports decoded RTCM message counts.

Example (Linux/Raspberry Pi):
    python geodetic/ublox_rover_base_decode_test.py --port /dev/ttyUSB0

Example (Windows):
    python geodetic/ublox_rover_base_decode_test.py --port COM3 --ublox-port UART1

The physical serial connection must match --ublox-port:
UART1/UART2 are receiver UARTs; USB is for a direct u-blox USB connection.
"""

from __future__ import annotations

import math
import argparse
import base64
import json
import logging
import os
import socket
import statistics
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

try:
    import serial
except ModuleNotFoundError:  # Allows --self-test on a development machine without pyserial.
    serial = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cors_dashboard.backend.app.command_builder import (  # noqa: E402
    UBLOX_SAVE_CONFIG,
    _ubx_packet,
    build_base_fixed_lla_command,
    build_ublox_output_config_command,
)
RTCMSignalDecoder = None


LOG = logging.getLogger("ublox_rover_base_test")
DECODER_SERIAL = "ublox-rover-base-test"
RTK_FIXED = 4
RTK_FLOAT = 5

EXPECTED_RTCM = (
    1005,
    1074,
    1084,
    1094,
    1124,
    1077,
    1087,
    1097,
    1127,
    1230,
)


def load_runtime_dependencies() -> None:
    global serial, RTCMSignalDecoder
    if serial is None:
        import importlib

        serial = importlib.import_module("serial")
    if RTCMSignalDecoder is None:
        from geodetic.agent_universal import RTCMSignalDecoder as decoder_class

        RTCMSignalDecoder = decoder_class


@dataclass
class GGAReading:
    seq: int
    raw: str
    fix_quality: int
    status: str
    lat: float | None
    lon: float | None
    alt: float | None
    timestamp: float


@dataclass
class NtripStats:
    attempts: int = 0
    reconnects: int = 0
    rx_bytes: int = 0
    injected_bytes: int = 0
    gga_sent: int = 0
    connected: bool = False
    last_error: str = ""


def nmea_checksum(body: str) -> str:
    value = 0
    for char in body:
        value ^= ord(char)
    return f"{value:02X}"


def valid_nmea(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return line.startswith("$")
    body, checksum = line[1:].split("*", 1)
    return nmea_checksum(body) == checksum[:2].upper()


def nmea_coord(raw: str, hemisphere: str) -> float | None:
    if not raw or not hemisphere:
        return None
    value = float(raw)
    degrees = int(value / 100)
    minutes = value - degrees * 100
    result = degrees + minutes / 60.0
    if hemisphere in {"S", "W"}:
        result = -result
    return result


def format_outbound_gga(raw: str) -> str:
    body = raw.strip().lstrip("$").split("*", 1)[0]
    return f"${body}*{nmea_checksum(body)}\r\n"


class SerialMonitor:
    """Collect NMEA health and decode every RTCM frame seen on the receiver output."""

    def __init__(self) -> None:
        self.nmea_buffer = bytearray()
        self.nmea_counts: Counter[str] = Counter()
        self.nmea_invalid = 0
        self.bytes_read = 0
        self.gga: GGAReading | None = None
        self._gga_seq = 0
        self._lock = threading.Lock()

        self.decoder = RTCMSignalDecoder()
        self.rtcm_counts: Counter[str] = Counter()
        self.rtcm_frames = 0
        self.decode_errors = 0
        self._seen_rtcm_types: set[str] = set()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.bytes_read += len(data)

        decoded = self.decoder.decode_sync(DECODER_SERIAL, data)
        if decoded:
            self.decode_errors += int(decoded.get("errors") or 0)
            for packet in decoded.get("packets") or []:
                message_type = str(packet.get("messageType"))
                self.rtcm_counts[message_type] += 1
                self.rtcm_frames += 1
                if message_type not in self._seen_rtcm_types:
                    self._seen_rtcm_types.add(message_type)
                    LOG.info("Decoded RTCM message %s", message_type)

        self.nmea_buffer.extend(data)
        while True:
            end = self.nmea_buffer.find(b"\n")
            if end < 0:
                break
            line = bytes(self.nmea_buffer[:end]).rstrip(b"\r")
            del self.nmea_buffer[: end + 1]
            self._handle_nmea(line.decode("ascii", errors="ignore").strip())

        # Keep binary/no-newline garbage from making the NMEA buffer unbounded.
        if len(self.nmea_buffer) > 8192:
            marker = self.nmea_buffer.rfind(b"$")
            self.nmea_buffer = self.nmea_buffer[marker:] if marker >= 0 else self.nmea_buffer[-512:]

    def _handle_nmea(self, line: str) -> None:
        if not line.startswith("$"):
            return
        if not valid_nmea(line):
            self.nmea_invalid += 1
            return

        body = line[1:].split("*", 1)[0]
        fields = body.split(",")
        talker_type = fields[0][-3:] if fields else ""
        if not talker_type:
            return
        self.nmea_counts[talker_type] += 1
        if talker_type != "GGA" or len(fields) < 10:
            return

        try:
            fix_quality = int(fields[6] or 0)
        except ValueError:
            fix_quality = 0
        status = {
            0: "NO_FIX",
            1: "GPS_FIX",
            2: "DGPS",
            4: "RTK_FIXED",
            5: "RTK_FLOAT",
        }.get(fix_quality, "OTHER")

        try:
            lat = nmea_coord(fields[2], fields[3])
            lon = nmea_coord(fields[4], fields[5])
            alt = float(fields[9]) if fields[9] else None
            if alt is not None and len(fields) > 11 and fields[11]:
                # GGA altitude is MSL; add geoid separation to get ellipsoidal h.
                alt += float(fields[11])
        except (TypeError, ValueError):
            lat = lon = alt = None

        with self._lock:
            self._gga_seq += 1
            self.gga = GGAReading(
                seq=self._gga_seq,
                raw=line,
                fix_quality=fix_quality,
                status=status,
                lat=lat,
                lon=lon,
                alt=alt,
                timestamp=time.time(),
            )

    def latest_gga(self) -> GGAReading | None:
        with self._lock:
            return self.gga

    def gga_for_ntrip(self, max_age: float = 5.0) -> str | None:
        with self._lock:
            reading = self.gga
        if not reading or time.time() - reading.timestamp > max_age:
            return None
        return format_outbound_gga(reading.raw)

    def reset_rtcm_stats(self) -> None:
        self.decoder = RTCMSignalDecoder()
        self.rtcm_counts.clear()
        self.rtcm_frames = 0
        self.decode_errors = 0
        self._seen_rtcm_types.clear()


def read_ntrip_header(sock: socket.socket) -> tuple[bytes, bytes]:
    buffer = bytearray()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("NTRIP closed before response header")
        buffer.extend(chunk)

        separator = buffer.find(b"\r\n\r\n")
        if separator >= 0:
            split_at = separator + 4
            return bytes(buffer[:split_at]), bytes(buffer[split_at:])

        first_line_end = buffer.find(b"\r\n")
        if first_line_end >= 0 and bytes(buffer[:first_line_end]).startswith(b"ICY 200 OK"):
            split_at = first_line_end + 2
            return bytes(buffer[:split_at]), bytes(buffer[split_at:])
    raise TimeoutError("NTRIP response header timeout")


class NtripCorrectionClient(threading.Thread):
    def __init__(
        self,
        serial_port: serial.Serial,
        serial_lock: threading.Lock,
        monitor: SerialMonitor,
        host: str,
        port: int,
        username: str,
        password: str,
        mountpoint: str,
    ) -> None:
        super().__init__(name="ublox-ntrip-corrections", daemon=True)
        self.serial_port = serial_port
        self.serial_lock = serial_lock
        self.monitor = monitor
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mountpoint = mountpoint
        self.stop_event = threading.Event()
        self.socket_lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.stats = NtripStats()

    def stop(self) -> None:
        self.stop_event.set()
        with self.socket_lock:
            sock = self.sock
            self.sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _inject(self, data: bytes) -> None:
        if not data:
            return
        with self.serial_lock:
            self.serial_port.write(data)
            self.serial_port.flush()
        self.stats.injected_bytes += len(data)

    def _connect(self) -> tuple[socket.socket, bytes]:
        sock = socket.create_connection((self.host, self.port), timeout=10.0)
        sock.settimeout(1.0)
        with self.socket_lock:
            self.sock = sock

        auth = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        path = "/" + quote(self.mountpoint.strip("/"), safe="")
        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Ntrip-Version: Ntrip/2.0\r\n"
            "User-Agent: NTRIP AitogyUbloxTest/1.0\r\n"
            f"Authorization: Basic {auth}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        header, pending = read_ntrip_header(sock)
        first_line = header.decode("iso-8859-1", errors="replace").splitlines()[0] if header else ""
        if "SOURCETABLE" in first_line.upper():
            raise RuntimeError("caster returned sourcetable instead of RTCM")
        if "200" not in first_line:
            raise RuntimeError(f"NTRIP rejected connection: {first_line}")
        return sock, pending

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.stats.attempts += 1
            sock = None
            try:
                sock, pending = self._connect()
                self.stats.connected = True
                self.stats.last_error = ""
                LOG.info("NTRIP connected: %s:%s/%s", self.host, self.port, self.mountpoint)
                if pending:
                    self.stats.rx_bytes += len(pending)
                    self._inject(pending)

                next_gga = 0.0
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if now >= next_gga:
                        gga = self.monitor.gga_for_ntrip()
                        if gga:
                            sock.sendall(gga.encode("ascii", errors="ignore"))
                            self.stats.gga_sent += 1
                        next_gga = now + 5.0
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("NTRIP stream closed")
                    self.stats.rx_bytes += len(chunk)
                    self._inject(chunk)
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.stats.connected = False
                    self.stats.reconnects += 1
                    self.stats.last_error = str(exc)
                    LOG.warning("NTRIP error: %s; retrying in 3s", exc)
            finally:
                self.stats.connected = False
                with self.socket_lock:
                    current = self.sock
                    self.sock = None
                if current:
                    try:
                        current.close()
                    except OSError:
                        pass
                elif sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self.stop_event.wait(3.0)


def drain_serial(ser: serial.Serial, monitor: SerialMonitor, seconds: float = 0.6) -> bytes:
    response = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        waiting = int(ser.in_waiting or 0)
        if waiting:
            data = ser.read(min(waiting, 4096))
            if data:
                response.extend(data)
                monitor.feed(data)
                continue
        time.sleep(0.03)
    return bytes(response)


def send_ubx_command(
    ser: serial.Serial,
    monitor: SerialMonitor,
    serial_lock: threading.Lock,
    label: str,
    payload: bytes,
) -> None:
    with serial_lock:
        ser.write(payload)
        ser.flush()
    response = drain_serial(ser, monitor)
    acks = response.count(b"\xb5\x62\x05\x01")
    naks = response.count(b"\xb5\x62\x05\x00")
    LOG.info("UBX %-18s sent=%dB ACK=%d NAK=%d", label, len(payload), acks, naks)


def rover_commands(output_port: str) -> list[tuple[str, bytes]]:
    options = {
        "output_mode": "diagnostics_rtcm",
        "msm_level": "both",
        "ports": [output_port],
    }
    return [
        ("TMODE3 rover", _ubx_packet(0x06, 0x71, bytes(40))),
        ("rover outputs", build_ublox_output_config_command(options)),
        ("save rover", UBLOX_SAVE_CONFIG),
    ]


def base_commands(lat: float, lon: float, alt: float, output_port: str, accuracy: float) -> list[tuple[str, bytes]]:
    options = {
        "output_mode": "diagnostics_rtcm",
        "msm_level": "both",
        "ports": [output_port],
    }
    commands = build_base_fixed_lla_command(
        "Ublox",
        lat,
        lon,
        alt,
        accuracy,
        options,
    )
    return [
        ("TMODE3 fixed", commands[0]),
        ("base outputs MSM4+7", commands[1]),
        ("save base", commands[2]),
    ]


def read_once(ser: serial.Serial, monitor: SerialMonitor) -> None:
    data = ser.read(4096)
    if data:
        monitor.feed(data)


def usable_fix(reading: GGAReading | None, allow_float: bool) -> bool:
    if not reading or reading.lat is None or reading.lon is None or reading.alt is None:
        return False
    if reading.status == "RTK_FIXED":
        return True
    return allow_float and reading.status == "RTK_FLOAT"


def wait_for_fixed(
    ser: serial.Serial,
    monitor: SerialMonitor,
    timeout: float,
    fixed_streak: float,
    allow_float: bool,
) -> None:
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last_log = 0.0
    while time.monotonic() < deadline:
        read_once(ser, monitor)
        reading = monitor.latest_gga()
        if usable_fix(reading, allow_float):
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= fixed_streak:
                LOG.info("Stable %s reached", reading.status if reading else "RTK")
                return
        else:
            stable_since = None

        if time.monotonic() - last_log >= 5.0:
            last_log = time.monotonic()
            status = reading.status if reading else "NO_GGA"
            LOG.info("Waiting RTK: status=%s NMEA=%s", status, dict(monitor.nmea_counts))
    raise TimeoutError("RTK fixed timeout; no safe base coordinate was produced")


def collect_samples(
    ser: serial.Serial,
    monitor: SerialMonitor,
    target: int,
    timeout: float,
    allow_float: bool,
) -> list[tuple[float, float, float]]:
    samples: list[tuple[float, float, float]] = []
    deadline = time.monotonic() + timeout
    last_seq = (monitor.latest_gga().seq if monitor.latest_gga() else 0)
    while time.monotonic() < deadline and len(samples) < target:
        read_once(ser, monitor)
        reading = monitor.latest_gga()
        if reading and reading.seq > last_seq:
            last_seq = reading.seq
            if usable_fix(reading, allow_float):
                samples.append((float(reading.lat), float(reading.lon), float(reading.alt)))
                if len(samples) == 1 or len(samples) % 10 == 0 or len(samples) == target:
                    LOG.info(
                        "Coordinate sample %d/%d: %.8f, %.8f, %.3f (%s)",
                        len(samples),
                        target,
                        reading.lat,
                        reading.lon,
                        reading.alt,
                        reading.status,
                    )
    if len(samples) < target:
        raise TimeoutError(f"Only collected {len(samples)}/{target} valid RTK samples")
    return samples


def average_samples(samples: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    return tuple(statistics.fmean(values) for values in zip(*samples))  # type: ignore[return-value]


def capture_base_output(ser: serial.Serial, monitor: SerialMonitor, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    last_log = 0.0
    while time.monotonic() < deadline:
        read_once(ser, monitor)
        if time.monotonic() - last_log >= 5.0:
            last_log = time.monotonic()
            LOG.info("Base decode so far: frames=%d types=%s", monitor.rtcm_frames, dict(monitor.rtcm_counts))


def summary(monitor: SerialMonitor, ntrip_stats: NtripStats, base_coord: tuple[float, float, float]) -> dict:
    counts = {key: int(value) for key, value in sorted(monitor.rtcm_counts.items(), key=lambda item: int(item[0]))}
    missing = [str(message) for message in EXPECTED_RTCM if not counts.get(str(message))]
    reading = monitor.latest_gga()
    return {
        "base_coordinate_used": {
            "lat": base_coord[0],
            "lon": base_coord[1],
            "alt_ellipsoid_m": base_coord[2],
        },
        "serial_bytes_read": monitor.bytes_read,
        "nmea_counts": dict(monitor.nmea_counts),
        "nmea_invalid": monitor.nmea_invalid,
        "last_gga": asdict(reading) if reading else None,
        "ntrip": asdict(ntrip_stats),
        "rtcm": {
            "valid_frames": monitor.rtcm_frames,
            "decode_errors": monitor.decode_errors,
            "message_counts": counts,
            "expected_messages": [str(message) for message in EXPECTED_RTCM],
            "missing_messages": missing,
            "complete_expected_set": not missing and monitor.decode_errors == 0,
        },
        "decoder_stats": monitor.decoder.get_stats(DECODER_SERIAL),
    }


def self_test() -> None:
    packet = _ubx_packet(0x06, 0x71, bytes(40))
    assert len(packet) == 48
    assert packet[:6] == b"\xb5\x62\x06\x71\x28\x00"

    body = "GNGGA,123519,2215.00000,N,10348.00000,E,4,20,0.8,160.0,M,-5.0,M,,"
    line = f"${body}*{nmea_checksum(body)}\r\n".encode("ascii")
    assert valid_nmea(line.decode("ascii").strip())
    fields = body.split(",")
    assert int(fields[6]) == RTK_FIXED
    assert math.isclose(nmea_coord(fields[2], fields[3]), 22.25)
    assert math.isclose(nmea_coord(fields[4], fields[5]), 103.8)
    assert math.isclose(float(fields[9]) + float(fields[11]), 155.0)
    print("self-test: OK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=os.getenv("UBLOX_SERIAL_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--baud", type=int, default=int(os.getenv("UBLOX_BAUD", "115200")))
    parser.add_argument("--ublox-port", choices=("UART1", "UART2", "USB"), default=os.getenv("UBLOX_OUTPUT_PORT", "UART1").upper())
    parser.add_argument("--ntrip-host", default=os.getenv("NTRIP_HOST", "vngeonet.vn"))
    parser.add_argument("--ntrip-port", type=int, default=int(os.getenv("NTRIP_PORT", "2101")))
    parser.add_argument("--ntrip-user", default=os.getenv("NTRIP_USER", "phongkythuat869"))
    parser.add_argument("--ntrip-pass", default=os.getenv("NTRIP_PASS", "86868686"))
    # The existing agent/backend use VRS.105M3 as the canonical caster name.
    parser.add_argument("--mountpoint", default=os.getenv("NTRIP_MOUNTPOINT", "VRS.105M3"))
    parser.add_argument("--wait-timeout", type=float, default=900.0, help="seconds to wait for stable RTK")
    parser.add_argument("--fixed-streak", type=float, default=5.0, help="seconds of continuous RTK fixed")
    parser.add_argument("--samples", type=int, default=60, help="valid GGA samples used for the base coordinate")
    parser.add_argument("--sample-timeout", type=float, default=600.0)
    parser.add_argument("--verify-seconds", type=float, default=60.0)
    parser.add_argument("--fixed-accuracy", type=float, default=0.01, help="fixed-position accuracy in metres")
    parser.add_argument("--allow-float", action="store_true", help="allow RTK float samples; unsafe for a precise base")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.self_test:
        self_test()
        return 0
    load_runtime_dependencies()
    if args.samples < 5:
        raise ValueError("--samples must be at least 5")

    monitor = SerialMonitor()
    serial_lock = threading.Lock()
    ntrip: NtripCorrectionClient | None = None
    ntrip_stats = NtripStats()
    base_coord: tuple[float, float, float] | None = None
    ser = serial.Serial(args.port, args.baud, timeout=0.2, write_timeout=2.0)
    LOG.info("Connected to u-blox serial %s @ %d", args.port, args.baud)

    try:
        ser.reset_input_buffer()
        for label, command in rover_commands(args.ublox_port):
            send_ubx_command(ser, monitor, serial_lock, label, command)
        LOG.info("u-blox set to ROVER; GGA/GSA/GSV/GST/NAV diagnostics enabled")

        ntrip = NtripCorrectionClient(
            ser,
            serial_lock,
            monitor,
            args.ntrip_host,
            args.ntrip_port,
            args.ntrip_user,
            args.ntrip_pass,
            args.mountpoint,
        )
        ntrip.start()
        wait_for_fixed(ser, monitor, args.wait_timeout, args.fixed_streak, args.allow_float)
        samples = collect_samples(ser, monitor, args.samples, args.sample_timeout, args.allow_float)
        base_coord = average_samples(samples)
        LOG.info("Averaged base coordinate: lat=%.8f lon=%.8f alt=%.3f", *base_coord)

        ntrip.stop()
        ntrip.join(timeout=8.0)
        ntrip_stats = ntrip.stats
        ntrip = None
        monitor.reset_rtcm_stats()

        for label, command in base_commands(*base_coord, args.ublox_port, args.fixed_accuracy):
            send_ubx_command(ser, monitor, serial_lock, label, command)
        LOG.info("u-blox set to FIXED BASE; RTCM 1005 + MSM4 + MSM7 output enabled")

        capture_base_output(ser, monitor, args.verify_seconds)
        report = summary(monitor, ntrip_stats, base_coord)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if ntrip is not None:
            ntrip.stop()
            ntrip.join(timeout=8.0)
        ser.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        LOG.warning("Interrupted; current receiver mode is left unchanged")
        raise SystemExit(130)
    except Exception as exc:
        LOG.error("Test failed: %s", exc, exc_info=True)
        raise SystemExit(1)
