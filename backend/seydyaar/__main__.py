"""Seyd‑Yaar CLI entrypoint.

This repo ships **without committed runs** (no demo data). The PWA reads from `docs/latest/`.

Commands:
- `init-latest`: create safe placeholder files under docs/latest so the UI never crashes.

To generate real runs, wire the backend to real data sources and add a `run` command
(or schedule your own pipeline script) that writes the expected folder structure under `docs/latest/`.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _try_load_dotenv() -> None:
    """Load .env if python-dotenv is installed (optional)."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        return


def main() -> None:
    _try_load_dotenv()

    parser = argparse.ArgumentParser(prog="seydyaar")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser(
        "init-latest",
        help="Initialize docs/latest with placeholder meta_index.json + preview.png (no demo data)",
    )
    p_init.add_argument(
        "--out",
        default=str(Path("..") / "docs" / "latest"),
        help="Output folder (recommended: ../docs/latest)",
    )

    args = parser.parse_args()

    if args.cmd == "init-latest":
        from seydyaar.pipeline.init_latest import init_latest

        out = init_latest(args.out)
        print(f"Initialized: {out}")


if __name__ == "__main__":
    main()
