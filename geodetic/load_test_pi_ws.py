import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import List

import websockets


@dataclass
class WorkerStats:
    sent: int = 0
    send_errors: int = 0
    connect_errors: int = 0


def _ws_url(base_http_url: str, serial: str) -> str:
    normalized = base_http_url.rstrip("/")
    if normalized.startswith("https://"):
        normalized = "wss://" + normalized[len("https://"):]
    elif normalized.startswith("http://"):
        normalized = "ws://" + normalized[len("http://"):]
    return f"{normalized}/ws/pi/{serial}"


def _make_payload(serial: str, seq: int) -> dict:
    now = int(time.time())
    return {
        "serial": serial,
        "name": f"LoadTest-{serial}",
        "status": "online",
        "ntrip_connected": True,
        "bps": random.randint(500, 5000),
        "detected_chip_type": "load-test-chip",
        "base_config": {"mode": "rover", "seq": seq},
        "service_config": {"profile": "load-test"},
        "ntrip_status": {
            "server1": {"connected": True, "bps": random.randint(200, 2500)},
            "server2": {"connected": True, "bps": random.randint(200, 2500)},
        },
        "timestamp": now,
    }


async def pi_sender(
    base_url: str,
    serial: str,
    duration_s: int,
    rate_per_sec: float,
    stats: WorkerStats,
):
    ws = _ws_url(base_url, serial)
    interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
    deadline = time.monotonic() + duration_s

    try:
        async with websockets.connect(ws, ping_interval=20, ping_timeout=20) as conn:
            seq = 0
            while time.monotonic() < deadline:
                message = {
                    "type": "status_update",
                    "payload": _make_payload(serial, seq),
                }
                try:
                    await conn.send(json.dumps(message, separators=(",", ":")))
                    stats.sent += 1
                except Exception:
                    stats.send_errors += 1

                seq += 1
                if interval > 0:
                    await asyncio.sleep(interval)
    except Exception:
        stats.connect_errors += 1


async def ui_receiver(base_url: str, duration_s: int) -> int:
    ws = _ws_url(base_url, "").replace("/ws/pi/", "/ws/updates")
    received = 0
    deadline = time.monotonic() + duration_s
    try:
        async with websockets.connect(ws, ping_interval=20, ping_timeout=20) as conn:
            while time.monotonic() < deadline:
                timeout = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(conn.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                try:
                    data = json.loads(raw)
                except Exception:
                    continue

                if data.get("type") == "status_update":
                    received += 1
    except Exception:
        return received

    return received


async def run_load_test(
    base_url: str,
    clients: int,
    duration_s: int,
    rate_per_sec: float,
    serial_prefix: str,
    watch_ui: bool,
):
    stats_list: List[WorkerStats] = []
    tasks: List[asyncio.Task] = []

    ui_task = None
    if watch_ui:
        ui_task = asyncio.create_task(ui_receiver(base_url, duration_s))

    for i in range(clients):
        serial = f"{serial_prefix}{i:04d}"
        stats = WorkerStats()
        stats_list.append(stats)
        tasks.append(asyncio.create_task(pi_sender(base_url, serial, duration_s, rate_per_sec, stats)))

    t0 = time.monotonic()
    await asyncio.gather(*tasks)
    elapsed = max(0.001, time.monotonic() - t0)

    ui_received = None
    if ui_task is not None:
        ui_received = await ui_task

    total_sent = sum(s.sent for s in stats_list)
    total_send_errors = sum(s.send_errors for s in stats_list)
    total_connect_errors = sum(s.connect_errors for s in stats_list)

    target_total_rate = clients * rate_per_sec
    actual_total_rate = total_sent / elapsed

    print("=== PI WS Load Test Summary ===")
    print(f"base_url: {base_url}")
    print(f"clients: {clients}")
    print(f"duration_s: {duration_s}")
    print(f"target_rate_per_client: {rate_per_sec:.2f} msg/s")
    print(f"target_total_rate: {target_total_rate:.2f} msg/s")
    print(f"actual_total_rate: {actual_total_rate:.2f} msg/s")
    print(f"total_sent: {total_sent}")
    print(f"send_errors: {total_send_errors}")
    print(f"connect_errors: {total_connect_errors}")
    if ui_received is not None:
        print(f"ui_status_updates_received: {ui_received}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress test /ws/pi/{serial} status_update hot path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--clients", type=int, default=100, help="Number of concurrent PI websocket clients")
    parser.add_argument("--duration", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--rate", type=float, default=5.0, help="Messages per second per client")
    parser.add_argument("--serial-prefix", default="LTPI", help="Serial prefix for generated devices")
    parser.add_argument("--watch-ui", action="store_true", help="Also open /ws/updates and count status_update messages")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_load_test(
            base_url=args.base_url,
            clients=max(1, args.clients),
            duration_s=max(1, args.duration),
            rate_per_sec=max(0.1, args.rate),
            serial_prefix=args.serial_prefix,
            watch_ui=args.watch_ui,
        )
    )


if __name__ == "__main__":
    main()
