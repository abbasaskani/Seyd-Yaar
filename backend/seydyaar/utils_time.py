from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Tuple


def trusted_utc_now() -> Tuple[datetime, str]:
    return datetime.now(timezone.utc), "system"


def timestamps_for_range(anchor_date: str, past_days: int, future_days: int, step_hours: int) -> List[str]:
    if anchor_date == "today":
        base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        base = datetime.fromisoformat(anchor_date).replace(tzinfo=timezone.utc)
    start = base - timedelta(days=int(past_days))
    end = base + timedelta(days=int(future_days))
    out: List[str] = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%dT%H:%M:%SZ"))
        cur += timedelta(hours=int(step_hours))
    return out


def time_id_from_iso(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y%m%d_%H%MZ")
