#!/usr/bin/env python3
"""Update Seyd-Yaar time window defaults (past/future days).

This script is OPTIONAL. Use it if you want the *code defaults* to be 2 past / 10 future,
in addition to the workflow command line.

It edits:
- .github/workflows/run_daily.yml  (command line args)
- backend/seydyaar/__main__.py     (argparse defaults if present)

Run from repo root:
  python tools/update_time_window.py
"""

from __future__ import annotations
from pathlib import Path
import re

PAST = 2
FUTURE = 10

def patch_workflow(path: Path) -> None:
    if not path.exists():
        print(f"[workflow] not found: {path}")
        return
    s = path.read_text(encoding="utf-8")
    s2 = re.sub(r'--past-days\s+\d+', f'--past-days {PAST}', s)
    s2 = re.sub(r'--future-days\s+\d+', f'--future-days {FUTURE}', s2)
    if s2 != s:
        path.write_text(s2, encoding="utf-8")
        print(f"[workflow] updated: {path}")
    else:
        print(f"[workflow] no change needed: {path}")

def patch_main(path: Path) -> None:
    if not path.exists():
        print(f"[main] not found: {path}")
        return
    s = path.read_text(encoding="utf-8")
    s2 = re.sub(r'(--past-days[^\n]*default=)\s*\d+', rf'\g<1>{PAST}', s)
    s2 = re.sub(r'(--future-days[^\n]*default=)\s*\d+', rf'\g<1>{FUTURE}', s2)
    s2 = re.sub(r'(past_days\s*=)\s*\d+', rf'\g<1>{PAST}', s2)
    s2 = re.sub(r'(future_days\s*=)\s*\d+', rf'\g<1>{FUTURE}', s2)
    if s2 != s:
        path.write_text(s2, encoding="utf-8")
        print(f"[main] updated: {path}")
    else:
        print(f"[main] no change needed (or patterns not found): {path}")

def main() -> None:
    repo = Path(".")
    patch_workflow(repo / ".github" / "workflows" / "run_daily.yml")
    patch_main(repo / "backend" / "seydyaar" / "__main__.py")
    print("Done. Commit & push, then re-run the workflow.")

if __name__ == "__main__":
    main()
