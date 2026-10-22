"""Share probe results and polling cooldowns across workers on one server."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3


CACHE_PATH = Path(__file__).resolve().parent / "data" / "reference_network_cache.sqlite3"


def claim_probe(key, interval, now):
    """Reserve before connecting so failures and restarts cannot trigger retries."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(CACHE_PATH, timeout=1)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS probes (name TEXT PRIMARY KEY, attempted_at REAL NOT NULL, result TEXT)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT attempted_at, result FROM probes WHERE name = ?", (key,)).fetchone()
        cached = json.loads(row[1]) if row and row[1] else None
        if row and now < row[0] + interval:
            db.commit()
            return cached, None
        db.execute("INSERT INTO probes (name, attempted_at) VALUES (?, ?) "
                   "ON CONFLICT(name) DO UPDATE SET attempted_at = excluded.attempted_at", (key, now))
        db.commit()
        return cached, now


def save_probe(key, reservation, result):
    with closing(sqlite3.connect(CACHE_PATH, timeout=1)) as db:
        with db:
            db.execute("UPDATE probes SET result = ? WHERE name = ? AND attempted_at = ?",
                       (json.dumps(result, ensure_ascii=False), key, reservation))
