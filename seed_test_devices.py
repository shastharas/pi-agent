"""Seed SQLite with public IPs for standalone testing (UUID ids match central style)."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "/data/local.db")

TEST_DEVICES: list[tuple[str, str, str, str, str | None]] = [
    ("11111111-1111-1111-1111-111111111101", "ISP 1 router", "1.1.1.1", "router", None),
    ("11111111-1111-1111-1111-111111111102", "ISP 2 router", "8.8.8.8", "router", None),
    ("11111111-1111-1111-1111-111111111103", "Likely down", "192.168.1.254", "printer", None),
    ("11111111-1111-1111-1111-111111111104", "Google DNS", "8.8.4.4", "other", None),
]


def main() -> None:
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
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    for did, name, ip, dtype, parent in TEST_DEVICES:
        conn.execute(
            """INSERT OR REPLACE INTO devices
               (id, name, ip_address, device_type, parent_id, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (did, name, ip, dtype, parent, now),
        )
    conn.commit()
    conn.close()
    print(f"Seeded {len(TEST_DEVICES)} test devices into {DB_PATH}")


if __name__ == "__main__":
    main()
