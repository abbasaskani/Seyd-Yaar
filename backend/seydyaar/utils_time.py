from __future__ import annotations
import datetime as dt
import json
from typing import Tuple, Optional
import requests

def trusted_utc_now(timeout_s: float = 3.5) -> Tuple[dt.datetime, str]:
    """
    Tries to fetch a trusted UTC time from public time APIs; falls back to local system UTC.

    Returns:
      (utc_dt, source)
    """
    # Keep it robust: try 2 sources quickly, then fallback.
    sources = [
        ("worldtimeapi", "https://worldtimeapi.org/api/timezone/Etc/UTC"),
        ("timeapi", "https://timeapi.io/api/Time/current/zone?timeZone=UTC"),
    ]
    for name, url in sources:
        try:
            r = requests.get(url, timeout=timeout_s)
            r.raise_for_status()
            data = r.json()
            if name == "worldtimeapi":
                iso = data.get("datetime")
            else:
                # timeapi returns: {"dateTime":"2026-02-10T...","timeZone":"UTC",...}
                iso = data.get("dateTime")
            if iso:
                # normalize Z
                iso = iso.replace("Z", "+00:00")
                return dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc), name
        except Exception:
            continue

    return dt.datetime.now(dt.timezone.utc), "local_system_fallback"


def _parse_anchor_date(anchor_date: str, now_utc: dt.datetime) -> dt.datetime:
    if anchor_date.lower() == "today":
        d = now_utc.date()
        return dt.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=dt.timezone.utc)
    # YYYY-MM-DD
    d = dt.date.fromisoformat(anchor_date)
    return dt.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=dt.timezone.utc)

def timestamps_for_range(anchor_date: str = "today", past_days: int = 0, future_days: int = 0, step_hours: int =  6) -> list[str]:
    now_utc, _ = trusted_utc_now()
    anchor = _parse_anchor_date(anchor_date, now_utc)
    start = anchor - dt.timedelta(days=max(past_days, 0))
    end = anchor + dt.timedelta(days=max(future_days, 0))
    step = dt.timedelta(hours=max(step_hours, 1))
    # include end day 23:59 by stepping until <= end+23h
    t = start
    out: list[str] = []
    while t <= end + dt.timedelta(hours=23):
        out.append(t.isoformat())
        t += step
    return out

def build_time_index(timestamps: list[str]) -> dict:
    # stable ids: 0000, 0001, ...
    id_by_ts = {ts: f"{i:04d}" for i, ts in enumerate(timestamps)}
    ts_by_id = {v: k for k, v in id_by_ts.items()}
    return {"timestamps": timestamps, "id_by_ts": id_by_ts, "ts_by_id": ts_by_id}


def time_id_from_iso(iso_ts: str) -> str:
    """Filesystem-safe time id for per-time folders.
    Format: YYYYMMDD_HHMMZ (UTC).
    """
    s = iso_ts.replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc)
    return t.strftime("%Y%m%d_%H%MZ")

