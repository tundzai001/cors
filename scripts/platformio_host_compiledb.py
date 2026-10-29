"""Include the standalone native MQTT check in the firmware IntelliSense DB."""
import json
from pathlib import Path
import shutil

from SCons.Script import Import

Import("env")


def include_host_check(source, target, env):
    root = Path(env.subst("$PROJECT_DIR"))
    database = Path(env.subst("$COMPILATIONDB_PATH"))
    compiler = shutil.which("clang++") or shutil.which("c++") or shutil.which("g++")
    if compiler is None:
        print("Host C++ compiler unavailable; native test IntelliSense entry skipped")
        return
    test = root / "geodetic/esp32/test/host/mqtt_write_checks.cpp"
    entries = json.loads(database.read_text(encoding="utf-8"))
    entries = [entry for entry in entries if Path(entry["file"]) != test]
    entries.append({
        "directory": str(root),
        "file": str(test),
        "arguments": [compiler, "-std=c++17", "-I", str(root / "geodetic/esp32/include"),
                      "-fsyntax-only", str(test)],
    })
    database.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


env.AddPostAction("compiledb", include_host_check)
