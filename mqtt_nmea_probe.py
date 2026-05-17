import argparse
import json
import os
import signal
import sys
import time
from collections import Counter

import paho.mqtt.client as mqtt


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_default_config():
    candidates = [
        os.path.join(SCRIPT_DIR, "geotek_config.json"),
        os.path.join(SCRIPT_DIR, "geodetic", "geotek_config.json"),
        os.path.join(os.getcwd(), "geotek_config.json"),
        os.path.join(os.getcwd(), "geodetic", "geotek_config.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


DEFAULT_CONFIG = find_default_config()


def load_default_source(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = json.load(file)

    sources = config.get("mqtt_source_list") or []
    if not sources:
        raise RuntimeError(f"No mqtt_source_list found in {config_path}")

    source = sources[0]
    return {
        "host": source.get("mqtt_broker") or config.get("mqtt", {}).get("broker"),
        "port": int(source.get("mqtt_port") or config.get("mqtt", {}).get("port", 1883)),
        "user": source.get("mqtt_user") or config.get("mqtt", {}).get("user"),
        "password": source.get("mqtt_pass") or config.get("mqtt", {}).get("pass"),
        "topic": source.get("topic"),
    }


def iter_ascii_lines(payload):
    text = payload.decode("ascii", errors="ignore")
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line:
            yield line


def is_probably_rtcm(payload):
    return bool(payload) and payload[0] == 0xD3


def strip_rtcm3_frames(payload):
    cleaned = bytearray()
    skipped = 0
    index = 0
    length = len(payload)
    while index < length:
        if payload[index] == 0xD3 and index + 6 <= length and not (payload[index + 1] & 0xFC):
            payload_len = ((payload[index + 1] & 0x03) << 8) | payload[index + 2]
            total_len = 3 + payload_len + 3
            if 0 < payload_len <= 1023 and index + total_len <= length:
                skipped += total_len
                index += total_len
                continue
        cleaned.append(payload[index])
        index += 1
    return bytes(cleaned), skipped


def nmea_type(line):
    if not line.startswith("$") or len(line) < 6:
        return None
    head = line.split(",", 1)[0].split("*", 1)[0]
    if len(head) < 6:
        return None
    talker = head[1:3]
    msg_type = head[-3:]
    if not talker.isalpha() or not msg_type.isalpha():
        return None
    if msg_type not in {"GGA", "GSA", "GSV", "RMC", "VTG", "GST", "ZDA"}:
        return None
    return msg_type


def main():
    parser = argparse.ArgumentParser(description="Probe MQTT upload topic and print non-RTCM sentences.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to geotek_config.json")
    parser.add_argument("--host", help="MQTT broker host")
    parser.add_argument("--port", type=int, help="MQTT broker port")
    parser.add_argument("--user", help="MQTT username")
    parser.add_argument("--password", help="MQTT password")
    parser.add_argument("--topic", help="MQTT topic to subscribe")
    parser.add_argument("--show-all", action="store_true", help="Print all non-RTCM lines, including unknown text")
    parser.add_argument("--summary-seconds", type=float, default=5.0, help="Summary interval")
    args = parser.parse_args()

    defaults = load_default_source(args.config)
    host = args.host or defaults["host"]
    port = args.port or defaults["port"]
    user = args.user if args.user is not None else defaults["user"]
    password = args.password if args.password is not None else defaults["password"]
    topic = args.topic or defaults["topic"]

    if not host or not topic:
        raise RuntimeError("Missing MQTT host/topic")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except Exception:
        client = mqtt.Client()

    if user and password:
        client.username_pw_set(user, password)

    counts = Counter()
    rtcm_bytes = 0
    raw_bytes = 0
    last_summary = time.time()
    stop = False

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print(f"CONNECTED host={host}:{port} topic={topic} rc={reason_code}")
        client.subscribe(topic, qos=0)

    def on_message(client, userdata, msg):
        nonlocal rtcm_bytes, raw_bytes, last_summary
        payload = msg.payload or b""
        raw_bytes += len(payload)

        payload, skipped = strip_rtcm3_frames(payload)
        rtcm_bytes += skipped
        if not payload:
            return

        printed = False
        for line in iter_ascii_lines(payload):
            if line.startswith("$command,"):
                counts["COMMAND_RESPONSE"] += 1
                print(f"[COMMAND] {line}")
                printed = True
                continue

            msg_type = nmea_type(line)
            if msg_type:
                counts[msg_type] += 1
                print(f"[NMEA {msg_type}] {line}")
                printed = True
                continue

            if line.startswith("{") and line.endswith("}"):
                counts["JSON"] += 1
                print(f"[JSON] {line[:300]}")
                printed = True
                continue

            if args.show_all:
                counts["UNKNOWN_TEXT"] += 1
                print(f"[TEXT] {line[:300]}")
                printed = True

        now = time.time()
        if now - last_summary >= args.summary_seconds:
            nmea_summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
            print(f"[SUMMARY] raw={raw_bytes}B rtcm_skipped={rtcm_bytes}B counts: {nmea_summary}")
            last_summary = now

    def handle_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"Connecting MQTT {host}:{port}, topic={topic}")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 60)
    client.loop_start()

    try:
        while not stop:
            time.sleep(0.2)
    finally:
        client.loop_stop()
        client.disconnect()
        nmea_summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
        print(f"FINAL raw={raw_bytes}B rtcm_skipped={rtcm_bytes}B counts: {nmea_summary}")


if __name__ == "__main__":
    main()
