#!/usr/bin/env python3
"""
Record raw RTCM/NTRIP streams from one caster for offline datasets.

Each mountpoint is recorded by an independent NTRIP client connection. Files are
written as one raw byte file per station using a .txt extension as requested.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


END_SOURCETABLE = b"ENDSOURCETABLE"
DEFAULT_USER_AGENT = "NTRIP AitogyRawRecorder/1.0"
DEFAULT_MOUNTPOINTS = [
    "humga",
    "ATG.CG",
    "ATG.NIEN",
    "ATG.PHU",
    "BG.LucNam",
    "BG.LucNgan",
    "BG.TP",
    "BK.TP",
    "BRVT.EC",
    "CM.NgocHien",
    "HB.CaoPhong",
    "HB.LuongSon",
    "HB.TP",
    "HD.ChiLinh",
    "hhks",
    "HN.QuocOai",
    "HN.SocSon",
    "HP.HaiAn",
    "HT.HongLinh",
    "HT.TP",
    "meiji",
    "ND.NamTruc",
    "PQ01",
    "PQ02",
    "PT.DDV",
    "PT.DoanHung",
    "PT.HaHoa",
    "PT.TieuSon",
    "QB.BaDon",
    "QB.QuangNinh",
    "QN.ATG",
    "QN.CP",
    "QN.DamHa",
    "SinQuyen1",
    "SinQuyen2",
    "SL.MocChau",
    "TDBD02",
    "TDBD03",
    "TDBD04",
    "test",
    "TN.BaXuyen",
    "TN.DaiTu",
    "TN.PhoYen",
    "TN.TP",
    "TN.VoNhai",
    "TQ.BinhThuan",
    "TV869",
    "VP.LapThach",
    "VP.VinhYen",
    "YB.LucYen",
    "YB.LY.pl",
    "YB.TP",
    "YB.VanYen",
]
DEFAULT_CONFIG = {
    "host": "aitogy.com.vn",
    "port": 2101,
    "record_all_from_sourcetable": False,
    "output": "rtcm_raw_dataset",
    "duration_hours": 48.0,
    "max_parallel": 100,
    "reconnect_seconds": 10.0,
    "max_reconnect_seconds": 300.0,
    "unavailable_retry_seconds": 600.0,
    "read_timeout": 0.0,
    "report_seconds": 60.0,
    "chunk_size": 4096,
    "gga": None,
}


def disable_windows_quick_edit() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        enable_extended_flags = 0x0080
        enable_quick_edit_mode = 0x0040
        enable_insert_mode = 0x0020
        new_mode = (mode.value | enable_extended_flags) & ~enable_quick_edit_mode & ~enable_insert_mode
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception as exc:
        print(f"Warning: could not disable Windows QuickEdit mode: {exc}", file=sys.stderr, flush=True)


def utc_stamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:120]
    return name if name not in {"", ".", ".."} else "mountpoint"


@dataclass
class RecorderStats:
    bytes_written: int = 0
    chunks: int = 0
    reconnects: int = 0
    consecutive_failures: int = 0
    last_bytes: int = 0
    last_report_ts: float = 0.0
    connected_at: float = 0.0


class StationRawWriter:
    def __init__(self, base_dir: Path, mountpoint: str):
        self.base_dir = base_dir
        self.mountpoint = mountpoint
        self.stream_dir = base_dir / safe_name(mountpoint)
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.stream_dir / f"{safe_name(self.mountpoint)}.txt"
        self.event_path = self.stream_dir / f"{safe_name(self.mountpoint)}.events.jsonl"
        self.file = self.path.open("ab", buffering=1024 * 1024)
        self.event("file_open", file=self.path.name)

    def event(self, event: str, **fields) -> None:
        row = {"type": "event", "event": event, **fields}
        row.setdefault("mountpoint", self.mountpoint)
        row.setdefault("ts", time.time())
        with self.event_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def write(self, data: bytes) -> None:
        self.file.write(data)

    def flush(self) -> None:
        if self.file is not None:
            self.file.flush()

    def close(self) -> None:
        if self.file is not None:
            try:
                self.file.flush()
                self.event("file_close")
                self.file.close()
            finally:
                self.file = None


async def read_until_close_or_marker(reader: asyncio.StreamReader, marker: bytes, timeout: float = 10.0) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        if marker in chunk:
            break
    return b"".join(chunks)


async def fetch_sourcetable(host: str, port: int, user_agent: str) -> list[str]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        request = f"GET / HTTP/1.0\r\nUser-Agent: {user_agent}\r\n\r\n"
        writer.write(request.encode("ascii", errors="ignore"))
        await writer.drain()
        raw = await read_until_close_or_marker(reader, END_SOURCETABLE)
    finally:
        writer.close()
        await writer.wait_closed()

    mountpoints: list[str] = []
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if "STR;" not in line or "ENDSOURCETABLE" in line:
            continue
        line = line[line.find("STR;") :]
        parts = [part.strip() for part in line.split(";")]
        if len(parts) >= 3 and parts[1] and parts[2]:
            if len(parts) >= 10 and parts[7].lower() in {"true", "false", "0", "1", "yes", "no"}:
                mountpoints.append(parts[2])
            else:
                mountpoints.append(parts[1])
    return sorted(set(mountpoints))


def auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Authorization: Basic {token}\r\n"


async def open_ntrip_stream(
    host: str,
    port: int,
    mountpoint: str,
    username: str,
    password: str,
    user_agent: str,
    gga: str | None,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    reader, writer = await asyncio.open_connection(host, port)
    request = (
        f"GET /{mountpoint} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: {user_agent}\r\n"
        f"{auth_header(username, password) if username or password else ''}"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(request.encode("ascii", errors="ignore"))
    await writer.drain()

    header, pending_data = await read_ntrip_response_header(reader)
    header_text = header.decode("iso-8859-1", errors="replace")
    pending_preview = pending_data[:128].decode("iso-8859-1", errors="ignore")
    if "SOURCETABLE 200 OK" in header_text or pending_preview.startswith("STR;"):
        writer.close()
        await writer.wait_closed()
        raise RuntimeError("caster returned sourcetable instead of RTCM stream")

    ok = (
        "ICY 200 OK" in header_text
        or "Content-Type: gnss/data" in header_text
        or header_text.startswith("HTTP/1.0 200")
        or header_text.startswith("HTTP/1.1 200")
    )
    if not ok:
        writer.close()
        await writer.wait_closed()
        first_line = header_text.splitlines()[0] if header_text else "empty NTRIP response"
        raise RuntimeError(first_line)

    if gga:
        writer.write((gga.rstrip("\r\n") + "\r\n").encode("ascii", errors="ignore"))
        await writer.drain()
    return reader, writer, pending_data


async def read_ntrip_response_header(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    buffer = bytearray()
    deadline = time.monotonic() + 10.0

    while time.monotonic() < deadline:
        timeout = max(0.1, deadline - time.monotonic())
        chunk = await asyncio.wait_for(reader.read(512), timeout=timeout)
        if not chunk:
            if buffer:
                return bytes(buffer), b""
            raise RuntimeError("empty NTRIP response")
        buffer.extend(chunk)

        full_header_idx = buffer.find(b"\r\n\r\n")
        if full_header_idx >= 0:
            split_at = full_header_idx + 4
            return bytes(buffer[:split_at]), bytes(buffer[split_at:])

        # Many NTRIP v1 casters send only "ICY 200 OK\r\n" and then RTCM bytes,
        # without a blank header terminator. Accept the status line immediately.
        first_line_end = buffer.find(b"\r\n")
        if first_line_end >= 0:
            first_line = bytes(buffer[:first_line_end]).decode("iso-8859-1", errors="replace")
            if first_line.startswith("ICY 200 OK"):
                blank_end = buffer.find(b"\r\n\r\n", first_line_end)
                split_at = blank_end + 4 if blank_end >= 0 else first_line_end + 2
                return bytes(buffer[:split_at]), bytes(buffer[split_at:])
            if (
                first_line.startswith("HTTP/")
                and " 200 " not in first_line
                and not first_line.endswith(" 200")
            ):
                return bytes(buffer[: first_line_end + 2]), bytes(buffer[first_line_end + 2 :])

    preview = bytes(buffer[:160]).decode("iso-8859-1", errors="replace")
    raise RuntimeError(f"NTRIP response header timeout: {preview!r}")


async def read_stream_chunk(reader: asyncio.StreamReader, chunk_size: int, read_timeout: float) -> bytes:
    if read_timeout and read_timeout > 0:
        return await asyncio.wait_for(reader.read(chunk_size), timeout=read_timeout)
    return await reader.read(chunk_size)


def describe_exception(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "read timeout"
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


def reconnect_delay(args, stats: RecorderStats, reason: str) -> float:
    if "sourcetable" in reason.lower():
        return float(args.unavailable_retry_seconds)

    base = float(args.reconnect_seconds)
    cap = float(args.max_reconnect_seconds)
    delay = min(cap, base * (2 ** max(0, stats.consecutive_failures - 1)))

    # Spread reconnect bursts so dozens of stations do not hammer the caster at once.
    jitter = (sum(ord(ch) for ch in reason) % 7) * 0.37
    return delay + jitter


async def record_mountpoint(args, mountpoint: str, stop_at: float, stop_event: asyncio.Event, sem: asyncio.Semaphore) -> None:
    stats = RecorderStats(last_report_ts=time.time())
    writer = StationRawWriter(Path(args.output), mountpoint)

    while not stop_event.is_set() and time.time() < stop_at:
        async with sem:
            stream_writer = None
            try:
                reader, stream_writer, pending_data = await open_ntrip_stream(
                    args.host,
                    args.port,
                    mountpoint,
                    args.username,
                    args.password,
                    args.user_agent,
                    args.gga,
                )
                print(f"[{utc_stamp()}] {mountpoint}: connected", flush=True)
                stats.connected_at = time.time()
                writer.event("connected", reconnects=stats.reconnects)
                if pending_data:
                    writer.write(pending_data)
                    stats.bytes_written += len(pending_data)
                    stats.chunks += 1
                    stats.consecutive_failures = 0
                while not stop_event.is_set() and time.time() < stop_at:
                    data = await read_stream_chunk(reader, args.chunk_size, args.read_timeout)
                    if not data:
                        raise RuntimeError("stream closed")
                    writer.write(data)
                    stats.bytes_written += len(data)
                    stats.chunks += 1
                    stats.consecutive_failures = 0

                    now = time.time()
                    if now - stats.last_report_ts >= args.report_seconds:
                        delta_bytes = stats.bytes_written - stats.last_bytes
                        bps = delta_bytes / max(1.0, now - stats.last_report_ts)
                        print(
                            f"[{utc_stamp(now)}] {mountpoint}: {stats.bytes_written} bytes, {bps:.0f} B/s",
                            flush=True,
                        )
                        stats.last_bytes = stats.bytes_written
                        stats.last_report_ts = now
                        writer.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                stats.reconnects += 1
                reason = describe_exception(exc)
                stats.consecutive_failures += 1
                wait_seconds = reconnect_delay(args, stats, reason)
                writer.event("stream_disconnected", reconnects=stats.reconnects, reason=reason)
                writer.flush()
                print(
                    f"[{utc_stamp()}] {mountpoint}: reconnect {stats.reconnects} in {wait_seconds:.0f}s after disconnect: {reason}",
                    flush=True,
                )
                await asyncio.sleep(wait_seconds)
            finally:
                if stream_writer is not None:
                    stream_writer.close()
                    try:
                        await stream_writer.wait_closed()
                    except Exception:
                        pass

    writer.close()
    print(f"[{utc_stamp()}] {mountpoint}: stopped, total={stats.bytes_written} bytes", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record raw RTCM/NTRIP streams for datasets.",
        epilog="File arguments must stay within RTCM_RECORDER_ROOT (default: working directory). "
               "Set NTRIP_USERNAME and NTRIP_PASSWORD for authenticated streams.",
    )
    parser.add_argument("--host", default=os.getenv("NTRIP_HOST") or os.getenv("CASTER_HOST") or DEFAULT_CONFIG["host"])
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NTRIP_PORT") or os.getenv("CASTER_PORT") or DEFAULT_CONFIG["port"]),
    )
    parser.add_argument("--username", default=os.getenv("NTRIP_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("NTRIP_PASSWORD", ""))
    parser.add_argument("--mountpoint", action="append", default=[], help="Mountpoint to record; repeatable.")
    parser.add_argument("--mountpoints-file", help="Text file with one mountpoint per line.")
    parser.add_argument(
        "--all-from-sourcetable",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_CONFIG["record_all_from_sourcetable"],
        help="Record every mountpoint visible in sourcetable.",
    )
    parser.add_argument("--output", default=DEFAULT_CONFIG["output"])
    parser.add_argument("--duration-hours", type=float, default=DEFAULT_CONFIG["duration_hours"])
    parser.add_argument("--max-parallel", type=int, default=DEFAULT_CONFIG["max_parallel"])
    parser.add_argument("--reconnect-seconds", type=float, default=DEFAULT_CONFIG["reconnect_seconds"])
    parser.add_argument("--max-reconnect-seconds", type=float, default=DEFAULT_CONFIG["max_reconnect_seconds"])
    parser.add_argument("--unavailable-retry-seconds", type=float, default=DEFAULT_CONFIG["unavailable_retry_seconds"])
    parser.add_argument("--read-timeout", type=float, default=DEFAULT_CONFIG["read_timeout"])
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_CONFIG["report_seconds"])
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CONFIG["chunk_size"])
    parser.add_argument("--gga", default=DEFAULT_CONFIG["gga"], help="Optional NMEA GGA line for VRS/GGA-required mountpoints.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args()
    workspace = Path(os.getenv("RTCM_RECORDER_ROOT", ".")).resolve()
    for field in ("mountpoints_file", "output"):
        value = getattr(args, field)
        if value is None:
            continue
        try:
            path = (workspace / value).resolve()
        except (OSError, RuntimeError):
            parser.error(f"--{field.replace('_', '-')} cannot be resolved")
        if not path.is_relative_to(workspace):
            parser.error(f"--{field.replace('_', '-')} must stay within RTCM_RECORDER_ROOT")
        setattr(args, field, path)
    if not args.host:
        parser.error("--host or NTRIP_HOST/CASTER_HOST is required")
    return args


async def main() -> None:
    args = parse_args()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    mountpoints = list(args.mountpoint)
    if args.mountpoints_file:
        mountpoints.extend(
            line.strip()
            for line in args.mountpoints_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if args.all_from_sourcetable:
        mountpoints.extend(await fetch_sourcetable(args.host, args.port, args.user_agent))
    if not mountpoints:
        mountpoints.extend(DEFAULT_MOUNTPOINTS)

    mountpoints = sorted(set(mountpoints))
    if not mountpoints:
        raise SystemExit("No mountpoints found.")

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as metadata:
        temporary_path = Path(metadata.name)
        try:
            json.dump(
                {
                    "host": args.host,
                    "port": args.port,
                    "mountpoints": mountpoints,
                    "started_at": time.time(),
                    "duration_hours": args.duration_hours,
                    "file_format": "one_raw_rtcm_byte_file_per_mountpoint_txt_extension",
                },
                metadata,
                ensure_ascii=False,
                indent=2,
            )
            metadata.close()
            temporary_path.replace(output / "run.json")
        finally:
            metadata.close()
            temporary_path.unlink(missing_ok=True)

    print(f"Recording {len(mountpoints)} mountpoint(s) into {output.resolve()}", flush=True)
    stop_at = time.time() + args.duration_hours * 3600
    sem = asyncio.Semaphore(max(1, args.max_parallel))
    tasks = [asyncio.create_task(record_mountpoint(args, mp, stop_at, stop_event, sem)) for mp in mountpoints]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    disable_windows_quick_edit()
    asyncio.run(main())
