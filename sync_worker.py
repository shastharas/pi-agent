"""
Sync worker — pushes queued SQLite rows to central and pulls device config.
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

import httpx

DB_PATH = os.getenv("DB_PATH", "/data/local.db")
CENTRAL_URL = os.getenv("CENTRAL_URL", "https://monitor.example.com").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
STORE_ID = os.getenv("STORE_ID", "unknown")
PI_ID = os.getenv("PI_ID", "pi-001")
SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "30"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))
CONFIG_SYNC_INTERVAL = int(os.getenv("CONFIG_SYNC_INTERVAL", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] sync_worker: %(message)s",
)
log = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "X-Store-Id": STORE_ID,
        "X-Pi-Id": PI_ID,
        "Content-Type": "application/json",
    }


async def push_ping_results(client: httpx.AsyncClient) -> int:
    conn = _conn()
    rows = conn.execute(
        """SELECT id, device_id, pinged_at, is_reachable,
                  packets_sent, packets_received, rtt_min_ms, rtt_avg_ms,
                  rtt_max_ms, rtt_samples, total_duration_ms
           FROM ping_results WHERE synced = 0
           ORDER BY id LIMIT ?""",
        (BATCH_SIZE,),
    ).fetchall()

    if not rows:
        conn.close()
        return 0

    payload = {
        "store_id": STORE_ID,
        "pi_id": PI_ID,
        "pings": [
            {
                "device_id": r["device_id"],
                "pinged_at": r["pinged_at"],
                "is_reachable": bool(r["is_reachable"]),
                "packets_sent": r["packets_sent"],
                "packets_received": r["packets_received"],
                "rtt_min_ms": r["rtt_min_ms"],
                "rtt_avg_ms": r["rtt_avg_ms"],
                "rtt_max_ms": r["rtt_max_ms"],
                "rtt_samples": json.loads(r["rtt_samples"]) if r["rtt_samples"] else [],
                "total_duration_ms": r["total_duration_ms"],
            }
            for r in rows
        ],
    }

    try:
        resp = await client.post(
            f"{CENTRAL_URL}/api/ingest/pings",
            json=payload,
            headers=_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Push failed (will retry): %s", e)
        conn.close()
        return -1

    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE ping_results SET synced = 1 WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    return len(rows)


async def push_logs(client: httpx.AsyncClient) -> int:
    conn = _conn()
    rows = conn.execute(
        """SELECT id, logged_at, source, level, component, message
           FROM app_logs WHERE synced = 0 LIMIT ?""",
        (BATCH_SIZE,),
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    payload = {
        "store_id": STORE_ID,
        "pi_id": PI_ID,
        "logs": [
            {
                "logged_at": r["logged_at"],
                "source": r["source"],
                "level": r["level"],
                "component": r["component"],
                "message": r["message"],
            }
            for r in rows
        ],
    }

    try:
        resp = await client.post(
            f"{CENTRAL_URL}/api/ingest/logs",
            json=payload,
            headers=_headers(),
            timeout=15.0,
        )
        resp.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Log push failed: %s", e)
        conn.close()
        return -1

    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE app_logs SET synced = 1 WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    return len(rows)


async def pull_device_config(client: httpx.AsyncClient) -> None:
    try:
        resp = await client.get(
            f"{CENTRAL_URL}/api/stores/{STORE_ID}/devices",
            headers=_headers(),
            timeout=15.0,
        )
        resp.raise_for_status()
        raw = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("Config pull failed (will use cached): %s", e)
        return

    devices = raw.get("devices") or []
    ping_iv = raw.get("ping_interval_seconds")

    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        if ping_iv is not None:
            try:
                n = max(5, min(86400, int(ping_iv)))
                conn.execute(
                    """
                    INSERT INTO agent_meta(key, value) VALUES('ping_interval_seconds', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(n),),
                )
                log.info("Stored ping_interval_seconds=%s from central", n)
            except (TypeError, ValueError):
                pass

        now = datetime.now(timezone.utc).isoformat()
        for d in devices:
            enabled = d.get("enabled", True)
            enabled_int = 1 if bool(enabled) else 0
            conn.execute(
                """INSERT INTO devices (id, name, ip_address, device_type, parent_id, enabled, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, ip_address=excluded.ip_address,
                     device_type=excluded.device_type, parent_id=excluded.parent_id,
                     enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (
                    str(d["id"]),
                    d["name"],
                    d["ip_address"],
                    d.get("device_type"),
                    str(d["parent_id"]) if d.get("parent_id") else None,
                    enabled_int,
                    now,
                ),
            )
        if devices:
            ids = [str(d["id"]) for d in devices]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE devices SET enabled = 0 WHERE id NOT IN ({placeholders})",
                ids,
            )
        conn.commit()
    finally:
        conn.close()
    log.info("Synced %s device(s) from central", len(devices))


def cleanup_old_synced_rows() -> None:
    try:
        conn = _conn()
        cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        conn.execute(
            "DELETE FROM ping_results WHERE synced = 1 AND pinged_at < ?",
            (cutoff_iso,),
        )
        conn.execute(
            "DELETE FROM app_logs WHERE synced = 1 AND logged_at < ?",
            (cutoff_iso,),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        log.error("Cleanup failed: %s", e)


def unsynced_count() -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM ping_results WHERE synced = 0").fetchone()[0]
    conn.close()
    return int(n)


async def main() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    log.info("Sync worker starting — central=%s, store=%s", CENTRAL_URL, STORE_ID)

    last_config_sync = 0.0
    last_cleanup = 0.0

    async with httpx.AsyncClient(verify=True) as client:
        await pull_device_config(client)
        last_config_sync = time.monotonic()

        while True:
            now = time.monotonic()

            ping_synced = await push_ping_results(client)
            log_synced = await push_logs(client)

            if ping_synced > 0:
                log.info("Synced %s ping result(s)", ping_synced)
            if log_synced > 0:
                log.info("Synced %s log(s)", log_synced)

            if now - last_config_sync > CONFIG_SYNC_INTERVAL:
                await pull_device_config(client)
                last_config_sync = now

            if now - last_cleanup > 86400:
                cleanup_old_synced_rows()
                last_cleanup = now

            backlog = unsynced_count()
            if backlog > 1000:
                log.warning("Backlog growing: %s unsynced ping result(s)", backlog)

            await asyncio.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
