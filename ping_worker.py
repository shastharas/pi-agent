"""
Ping worker — reads devices from SQLite, pings on interval, writes results locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from ping_parser import parse_ping_output

DB_PATH = os.getenv("DB_PATH", "/data/local.db")
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "20"))
PING_COUNT = int(os.getenv("PING_COUNT", "3"))
PING_TIMEOUT = int(os.getenv("PING_TIMEOUT", "2"))
STORE_ID = os.getenv("STORE_ID", "unknown")
PI_ID = os.getenv("PI_ID", "pi-001")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "20"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ping_worker: %(message)s",
)
log = logging.getLogger(__name__)


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            device_type TEXT,
            parent_id TEXT,
            enabled INTEGER DEFAULT 1,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ping_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            pinged_at TEXT NOT NULL,
            is_reachable INTEGER NOT NULL,
            packets_sent INTEGER NOT NULL,
            packets_received INTEGER NOT NULL,
            rtt_min_ms REAL,
            rtt_avg_ms REAL,
            rtt_max_ms REAL,
            rtt_samples TEXT,
            total_duration_ms REAL,
            synced INTEGER DEFAULT 0,
            FOREIGN KEY (device_id) REFERENCES devices(id)
        );

        CREATE INDEX IF NOT EXISTS idx_ping_results_synced ON ping_results(synced);
        CREATE INDEX IF NOT EXISTS idx_ping_results_device_time ON ping_results(device_id, pinged_at);

        CREATE TABLE IF NOT EXISTS app_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'pi-agent',
            level TEXT NOT NULL,
            component TEXT NOT NULL,
            message TEXT NOT NULL,
            synced INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_app_logs_synced ON app_logs(synced);

        CREATE TABLE IF NOT EXISTS agent_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    log.info("Database initialized at %s", DB_PATH)


def get_ping_interval_seconds() -> int:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        row = conn.execute(
            "SELECT value FROM agent_meta WHERE key = 'ping_interval_seconds'",
        ).fetchone()
        conn.close()
        if row and row[0] is not None:
            return max(5, min(86400, int(str(row[0]))))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        pass
    return max(5, min(86400, PING_INTERVAL))


def log_to_db(level: str, message: str) -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute(
            "INSERT INTO app_logs (logged_at, source, level, component, message) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), "pi-agent", level, "ping_worker", message),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        log.error("Failed to write log to DB: %s", e)


async def ping_device(ip: str) -> dict:
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            str(PING_COUNT),
            "-W",
            str(PING_TIMEOUT),
            "-i",
            "0.3",
            ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=(PING_COUNT * PING_TIMEOUT) + 3
        )
        output = stdout_bytes.decode("utf-8", errors="ignore")
    except asyncio.TimeoutError:
        return _failed_result(start, "timeout")
    except Exception as e:  # noqa: BLE001
        return _failed_result(start, f"error: {e}")

    duration_ms = (time.monotonic() - start) * 1000
    return parse_ping_output(output, duration_ms, PING_COUNT)


def _failed_result(start: float, reason: str) -> dict:
    return {
        "is_reachable": 0,
        "packets_sent": PING_COUNT,
        "packets_received": 0,
        "rtt_samples": [],
        "rtt_min_ms": None,
        "rtt_avg_ms": None,
        "rtt_max_ms": None,
        "total_duration_ms": (time.monotonic() - start) * 1000,
        "error": reason,
    }


def load_devices() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, ip_address FROM devices WHERE enabled = 1",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_result(device_id: str, result: dict) -> None:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        """INSERT INTO ping_results
           (device_id, pinged_at, is_reachable, packets_sent, packets_received,
            rtt_min_ms, rtt_avg_ms, rtt_max_ms, rtt_samples, total_duration_ms, synced)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            device_id,
            datetime.now(timezone.utc).isoformat(),
            result["is_reachable"],
            result["packets_sent"],
            result["packets_received"],
            result["rtt_min_ms"],
            result["rtt_avg_ms"],
            result["rtt_max_ms"],
            json.dumps(result["rtt_samples"]),
            result["total_duration_ms"],
        ),
    )
    conn.commit()
    conn.close()


async def ping_round() -> None:
    devices = load_devices()
    if not devices:
        log.warning("No devices configured — waiting for config sync.")
        return

    log.info("Pinging %s device(s)", len(devices))
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _ping_one(device: dict) -> None:
        async with sem:
            result = await ping_device(device["ip_address"])
            save_result(device["id"], result)
            status = "UP" if result["is_reachable"] else "DOWN"
            log.info(
                "  %-20s (%-15s) %4s avg=%sms",
                device["name"],
                device["ip_address"],
                status,
                result["rtt_avg_ms"],
            )

    await asyncio.gather(*[_ping_one(d) for d in devices])


async def main() -> None:
    init_db()
    interval = get_ping_interval_seconds()
    log.info("Pi agent starting — store=%s, pi=%s, interval=%ss (env fallback %ss)", STORE_ID, PI_ID, interval, PING_INTERVAL)
    log_to_db("INFO", f"Ping worker started for store {STORE_ID}")

    while True:
        interval = get_ping_interval_seconds()
        round_start = time.monotonic()
        try:
            await ping_round()
        except Exception as e:  # noqa: BLE001
            log.exception("Ping round failed: %s", e)
            log_to_db("ERROR", f"Ping round failed: {e}")

        elapsed = time.monotonic() - round_start
        sleep_for = max(0, interval - elapsed)
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    asyncio.run(main())
