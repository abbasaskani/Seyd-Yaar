"""
Seyd‑Yaar run_daily pipeline (v4 patch)

What this fixes (per your requirement):
- NEVER hard-code depth. For datasets with a depth axis, we auto-detect available depth range via:
    copernicusmarine describe --dataset-id <ID> -c depth -r coordinates
  then pick the depth closest to 0m using the returned minimum_value/maximum_value.
  (For many surface-layer datasets this is a single value like ~0.494m.)
- Always use coordinates_selection_method="nearest" for time/lat/lon selection.
- Writes audit trail: docs/latest/logs/download_manifest.jsonl
- Writes verification NetCDFs for today's 00:00Z: docs/latest/verify/<TIME_ID>/{sst,chl,ssh,currents,waves}.nc
- True rewrite mode: if SEYDYAAR_REWRITE=1, deletes docs/latest/runs/main before writing.

Note: This file is a drop-in replacement for backend/seydyaar/pipeline/run_daily.py.
"""

from __future__ import annotations

import json
import os
import shutil
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import copernicusmarine


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _load_datasets_cfg() -> Dict[str, Any]:
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "datasets.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))["cmems"]


def _walk_find_key(obj: Any, key: str) -> List[Any]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(_walk_find_key(v, key))
    elif isinstance(obj, list):
        for it in obj:
            found.extend(_walk_find_key(it, key))
    return found


class DepthResolver:
    """Resolve 'surface' depth for a dataset_id by querying copernicusmarine describe once and caching."""
    def __init__(self):
        self._cache: Dict[str, Optional[float]] = {}

    def surface_depth(self, dataset_id: str, target_m: float = 0.0) -> Optional[float]:
        if dataset_id in self._cache:
            return self._cache[dataset_id]

        # Run CLI describe to avoid relying on internal python schema.
        cmd = [
            "copernicusmarine", "describe",
            "--dataset-id", dataset_id,
            "-c", "depth",
            "-r", "coordinates",
        ]
        try:
            cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
            data = json.loads(cp.stdout)
        except Exception:
            # No depth axis or describe failed -> treat as no depth
            self._cache[dataset_id] = None
            return None

        # Robustly find numeric min/max depths.
        mins = _walk_find_key(data, "minimum_value")
        maxs = _walk_find_key(data, "maximum_value")

        def _to_float_list(vals):
            out = []
            for v in vals:
                try:
                    out.append(float(v))
                except Exception:
                    pass
            return out

        mins_f = _to_float_list(mins)
        maxs_f = _to_float_list(maxs)

        if not mins_f and not maxs_f:
            self._cache[dataset_id] = None
            return None

        # Candidate depths: use min/max; if dataset has single depth, both are same.
        cands = []
        cands += mins_f
        cands += maxs_f

        # Choose closest to target (usually 0.0)
        best = min(cands, key=lambda d: abs(d - target_m))
        self._cache[dataset_id] = best
        return best


def _subset_one(
    key: str,
    ts: datetime,
    bbox: Tuple[float, float, float, float],
    datasets_cfg: Dict[str, Any],
    tmpdir: Path,
    manifest_path: Path,
    depth_resolver: DepthResolver,
) -> Tuple[Optional[Path], Optional[str]]:
    spec = datasets_cfg[key]
    dataset_id = spec["dataset_id"]
    variables = spec.get("variables") or [spec.get("variable")]
    depth_target = spec.get("depth_target_m", None)

    lon_min, lat_min, lon_max, lat_max = bbox
    t0 = _utc(ts)

    out_name = f"{key}_{t0.strftime('%Y%m%d_%H%MZ')}.nc"
    out_path = tmpdir / out_name

    rec: Dict[str, Any] = {
        "layer": key,
        "dataset_id": dataset_id,
        "variables": variables,
        "requested_time_utc": t0.isoformat(),
        "coordinates_selection_method": "nearest",
        "depth_target_m": depth_target,
        "depth_selected_m": None,
        "output_nc": str(out_path),
        "ok": False,
        "bytes": 0,
        "sha256": None,
        "error": None,
    }

    try:
        kwargs = dict(
            dataset_id=dataset_id,
            variables=variables,
            minimum_longitude=float(lon_min),
            maximum_longitude=float(lon_max),
            minimum_latitude=float(lat_min),
            maximum_latitude=float(lat_max),
            start_datetime=t0.isoformat(),
            end_datetime=t0.isoformat(),
            output_directory=str(tmpdir),
            output_filename=out_name,
            file_format="netcdf",
            overwrite=True,
            skip_existing=False,
            coordinates_selection_method="nearest",
        )

        # Depth: auto-resolve (closest to target, usually 0m). If no depth axis -> skip.
        if depth_target is not None:
            sd = depth_resolver.surface_depth(dataset_id, float(depth_target))
            if sd is not None:
                rec["depth_selected_m"] = sd
                kwargs["minimum_depth"] = float(sd)
                kwargs["maximum_depth"] = float(sd)

        copernicusmarine.subset(**kwargs)

        if not out_path.exists():
            raise RuntimeError(f"subset returned but file missing: {out_path}")

        rec["ok"] = True
        rec["bytes"] = out_path.stat().st_size
        rec["sha256"] = _sha256(out_path)
        _append_jsonl(manifest_path, rec)
        return out_path, None

    except Exception as e:
        rec["error"] = str(e)
        _append_jsonl(manifest_path, rec)
        return None, str(e)


def run_daily(out_dir: str, past_days: int = 2, future_days: int = 8, step_hours: int = 6, grid: str = "220x220") -> None:
    out_root = Path(out_dir)
    _ensure_dir(out_root)

    # Rewrite old bad outputs if requested
    if os.environ.get("SEYDYAAR_REWRITE", "0") == "1":
        runs_main = out_root / "runs" / "main"
        if runs_main.exists():
            shutil.rmtree(runs_main, ignore_errors=True)

    log_dir = Path(os.environ.get("SEYDYAAR_LOG_DIR", out_root / "logs"))
    verify_dir = Path(os.environ.get("SEYDYAAR_VERIFY_DIR", out_root / "verify"))
    tmpdir = Path(os.environ.get("SEYDYAAR_TMPDIR", "backend/.seydyaar_tmp"))

    _ensure_dir(log_dir)
    _ensure_dir(verify_dir)
    _ensure_dir(tmpdir)

    manifest_path = log_dir / "download_manifest.jsonl"

    # bbox defaults (same as your meta)
    lon_min = float(os.environ.get("SEYDYAAR_LON_MIN", "43.979482811146084"))
    lat_min = float(os.environ.get("SEYDYAAR_LAT_MIN", "0.0385897597175813"))
    lon_max = float(os.environ.get("SEYDYAAR_LON_MAX", "66.10835728649252"))
    lat_max = float(os.environ.get("SEYDYAAR_LAT_MAX", "23.66176404036068"))
    bbox = (lon_min, lat_min, lon_max, lat_max)

    datasets_cfg = _load_datasets_cfg()

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=past_days)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=future_days)

    times: List[datetime] = []
    t = start
    while t <= end:
        times.append(t)
        t += timedelta(hours=step_hours)

    strict = os.environ.get("SEYDYAAR_STRICT_COPERNICUS", "0") == "1"

    verify_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    verify_time_id = verify_time.strftime("%Y%m%d_0000Z")

    depth_resolver = DepthResolver()

    for ts in times:
        errs: List[str] = []
        for key in ("sst", "chl", "ssh", "currents", "waves"):
            nc, err = _subset_one(key, ts, bbox, datasets_cfg, tmpdir, manifest_path, depth_resolver)
            if err:
                errs.append(f"{key}: {err}")

            # Copy verify files for today's 00:00Z
            if nc and _utc(ts) == _utc(verify_time):
                dest_dir = verify_dir / verify_time_id
                _ensure_dir(dest_dir)
                shutil.copy2(nc, dest_dir / f"{key}.nc")

        if errs and strict:
            raise RuntimeError("Copernicus download failed (strict mode). Errors: " + "; ".join(errs))

    # Minimal meta
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bbox": list(bbox),
        "available_time_ids": [dt.strftime("%Y%m%d_%H%MZ") for dt in times],
        "verify_time_id": verify_time_id,
        "log_dir": str(log_dir),
        "verify_dir": str(verify_dir),
        "tmpdir": str(tmpdir),
        "strict": strict,
    }
    (out_root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
