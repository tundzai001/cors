import argparse
import json
import os
import socket
import time


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


def load_command_source(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = json.load(file)

    sources = config.get("mqtt_source_list") or []
    if not sources:
        raise RuntimeError(f"No mqtt_source_list found in {config_path}")

    source = sources[0]
    return {
        "host": source.get("command_host") or source.get("source_host"),
        "port": int(source.get("command_port") or source.get("source_port") or 2101),
        "password": source.get("command_pass") or source.get("source_pass") or "",
        "mountpoint": source.get("command_mp") or source.get("source_mp"),
    }


def main():
    parser = argparse.ArgumentParser(description="Send raw commands to the Geotek command SOURCE.")
    parser.add_argument("--config", default=find_default_config())
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("commands", nargs="+", help="Commands, for example: 'gpgsv com2 1'")
    args = parser.parse_args()

    source = load_command_source(args.config)
    if not source["host"] or not source["mountpoint"]:
        raise RuntimeError("Missing command host/mountpoint")

    print(f"Connecting SOURCE {source['host']}:{source['port']}/{source['mountpoint']}")
    sock = socket.create_connection((source["host"], source["port"]), timeout=10)
    try:
        request = (
            f"SOURCE {source['password']} /{source['mountpoint']}\r\n"
            "Source-Agent: NTRIP ProbeCMD/1.0\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = sock.recv(4096)
        print(f"Handshake: {response.decode(errors='ignore').strip()}")
        if b"ICY 200" not in response and b"200 OK" not in response:
            raise RuntimeError("SOURCE handshake rejected")

        for command in args.commands:
            wire = command.strip()
            if not wire:
                continue
            print(f"Send: {wire}")
            sock.sendall((wire + "\r\n").encode("ascii"))
            time.sleep(args.delay)
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        sock.close()
        print("Closed")


if __name__ == "__main__":
    main()
