"""
Parse GNU/Linux `ping` command output (iputils-ping).
Tested against Debian/Ubuntu/Raspberry Pi OS style summaries.
"""

from __future__ import annotations

import re
from typing import Any


def parse_ping_output(output: str, duration_ms: float, ping_count: int) -> dict[str, Any]:
    """
    Return a dict compatible with ping_worker / central ingest:
    is_reachable (0|1 int for sqlite), packets_*, rtt_*, rtt_samples, total_duration_ms
    """
    samples = [float(m) for m in re.findall(r"time=([\d.]+)\s*ms", output)]
    sent_match = re.search(r"(\d+) packets transmitted", output)
    recv_match = re.search(r"(\d+)\s*(?:packets)?\s*received", output)
    sent = int(sent_match.group(1)) if sent_match else ping_count
    received = int(recv_match.group(1)) if recv_match else 0

    return {
        "is_reachable": 1 if received > 0 else 0,
        "packets_sent": sent,
        "packets_received": received,
        "rtt_samples": samples,
        "rtt_min_ms": min(samples) if samples else None,
        "rtt_avg_ms": sum(samples) / len(samples) if samples else None,
        "rtt_max_ms": max(samples) if samples else None,
        "total_duration_ms": round(duration_ms, 2),
    }
