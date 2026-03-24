from __future__ import annotations

"""
Scheduled "real" run generator for GitHub Pages hosting.

This version includes:
- Copernicus credentials env fallback (project + toolbox names)
- datasets.json normalization (supports {"cmems": {...}})
- Copernicus layer caching per timestamp (reuse across species)
- Force rebuild switch: SEYDYAAR_FORCE_REGEN=1 (overwrites even if outputs exist)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hashlib
import subprocess

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _walk_find_key(obj: Any, key: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(_walk_find_key(v, key))
    elif isinstance(obj, list):
        for it in obj:
            found.extend(_walk_find_key(it, key))
    return found

class _DepthResolver:
    """Find the available depth closest to target (usually 0m) by calling `copernicusmarine describe` once per dataset_id."""
    def __init__(self) -> None:
        self._cache: Dict[str, Optional[float]] = {}

    def closest_depth(self, dataset_id: str, target_m: float = 0.0) -> Optional[float]:
        if dataset_id in self._cache:
            return self._cache[dataset_id]

        cmd = ["copernicusmarine", "describe", "--dataset-id", dataset_id, "-c", "depth", "-r", "coordinates"]
        try:
            cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
            meta = json.loads(cp.stdout)
        except Exception:
            self._cache[dataset_id] = None
            return None

        mins = _walk_find_key(meta, "minimum_value")
        maxs = _walk_find_key(meta, "maximum_value")

        vals: List[float] = []
        for v in mins + maxs:
            try:
                vals.append(float(v))
            except Exception:
                pass

        if not vals:
            self._cache[dataset_id] = None
            return None

        best = min(vals, key=lambda d: abs(d - target_m))
        self._cache[dataset_id] = best
        return best

from typing import Dict, Any, List, Tuple, Optional
import json
import os
import shutil
import math
import numpy as np
import requests
from dateutil import parser as dtparser
from dateutil import tz

from ..utils_geo import bbox_from_geojson, GridSpec, mask_from_geojson
from ..utils_time import trusted_utc_now, timestamps_for_range
from ..utils_time import time_id_from_iso
from ..models.scoring import HabitatInputs, habitat_scoring, gradient_magnitude, front_score, front_feature_stack
from ..models.ops import ops_feasibility
from ..models.ensemble import ensemble_stats
from .io import write_bin_f32, write_bin_u8, write_json, minify_json_for_web

DEPTH_TARGETS_M: List[int] = [0, 4, 8, 12]
DEPTH_WEIGHTS: Dict[int, float] = {0: 0.40, 4: 0.30, 8: 0.20, 12: 0.10}

def _weighted_depth_mean(stack_by_depth: Dict[int, np.ndarray]) -> np.ndarray:
    out = None
    wsum = 0.0
    for d in DEPTH_TARGETS_M:
        arr = stack_by_depth.get(d)
        if arr is None:
            continue
        w = float(DEPTH_WEIGHTS.get(d, 0.0))
        out = arr.astype(np.float32) * w if out is None else out + arr.astype(np.float32) * w
        wsum += w
    if out is None:
        raise RuntimeError("No depth arrays available")
    return (out / max(wsum, 1e-9)).astype(np.float32)

def _derive_depth_layers(surface: Dict[str, np.ndarray], priors: Dict[str, Any], depth_m: float) -> Dict[str, np.ndarray]:
    d = float(depth_m)
    temp_decay = float(priors.get("depth_decay_c_per_m", 0.035))
    cur_decay = float(priors.get("current_decay_per_m", 0.018))
    wav_decay = float(priors.get("wave_decay_per_m", 0.06))
    out = dict(surface)
    if "sst_c" in out: out["sst_c"] = (out["sst_c"] - temp_decay * d).astype(np.float32)
    if "chl_mg_m3" in out: out["chl_mg_m3"] = np.clip(out["chl_mg_m3"] * (1.0 + min(d,12.0)*0.015), 1e-6, None).astype(np.float32)
    if "current_m_s" in out: out["current_m_s"] = np.clip(out["current_m_s"] * np.exp(-cur_decay * d), 0.0, None).astype(np.float32)
    if "waves_hs_m" in out: out["waves_hs_m"] = np.clip(out["waves_hs_m"] * np.exp(-wav_decay * d), 0.0, None).astype(np.float32)
    if "sss_psu" in out: out["sss_psu"] = (out["sss_psu"] + 0.01 * d).astype(np.float32)
    if "o2_umol_l" in out: out["o2_umol_l"] = np.clip(out["o2_umol_l"] - 1.2 * d, 1.0, None).astype(np.float32)
    return out

def _try_local_era5_wind(grid: GridSpec, day_utc: datetime, datasets_cfg: Dict[str, Any]) -> Optional[Dict[str, np.ndarray]]:
    era = (datasets_cfg or {}).get("era5", {}).get("wind10m", {}) if isinstance(datasets_cfg, dict) else {}
    tpl = era.get("path_template")
    if not tpl:
        return None
    p = Path(tpl.format(date_yyyymmdd=day_utc.strftime("%Y%m%d")))
    if not p.exists():
        return None
    try:
        import xarray as xr  # type: ignore
        ds = xr.open_dataset(p)
        u = ds[era.get("u_variable", "u10")]
        v = ds[era.get("v_variable", "v10")]
        if "time" in u.dims:
            target = np.datetime64(day_utc.replace(tzinfo=None))
            u = u.sel(time=target, method="nearest")
            v = v.sel(time=target, method="nearest")
        if "latitude" in u.coords and "longitude" in u.coords:
            lat_name, lon_name = "latitude", "longitude"
        else:
            lat_name, lon_name = "lat", "lon"
        lats = np.asarray(u[lat_name].values)
        lons = np.asarray(u[lon_name].values)
        u2 = np.asarray(u.values, dtype=np.float32)
        v2 = np.asarray(v.values, dtype=np.float32)
        # map target grid by nearest coordinates
        yi = np.abs(lats[:,None] - _grid_lats(grid)[None,:]).argmin(axis=0) if lats.ndim==1 else None
        xi = np.abs(lons[:,None] - _grid_lons(grid)[None,:]).argmin(axis=0) if lons.ndim==1 else None
        if yi is None or xi is None:
            return None
        out_u = u2[np.ix_(yi, xi)]
        out_v = v2[np.ix_(yi, xi)]
        spd = np.sqrt(out_u.astype(np.float64)**2 + out_v.astype(np.float64)**2).astype(np.float32)
        direction_to = (np.degrees(np.arctan2(out_u, out_v)) + 360.0) % 360.0
        return {"wind_u10_m_s": out_u.astype(np.float32), "wind_v10_m_s": out_v.astype(np.float32), "wind_speed_m_s": spd, "wind_dir_deg_to": direction_to.astype(np.float32), "wind_source": np.array([1], dtype=np.uint8)}
    except Exception:
        return None

def _grid_lons(grid: GridSpec) -> np.ndarray:
    vals = getattr(grid, "lons", None)
    if vals is not None:
        return np.asarray(vals, dtype=np.float32)
    return np.linspace(float(grid.lon_min), float(grid.lon_max), int(grid.width), dtype=np.float32)


def _grid_lats(grid: GridSpec) -> np.ndarray:
    vals = getattr(grid, "lats", None)
    if vals is not None:
        return np.asarray(vals, dtype=np.float32)
    return np.linspace(float(grid.lat_max), float(grid.lat_min), int(grid.height), dtype=np.float32)


def _safe_stack_nanmean(arrays: List[np.ndarray]) -> np.ndarray:
    stack = np.stack(arrays, axis=0).astype(np.float32)
    valid = np.isfinite(stack)
    count = valid.sum(axis=0)
    summed = np.where(valid, stack, 0.0).sum(axis=0, dtype=np.float32)
    out = np.full(stack.shape[1:], np.nan, dtype=np.float32)
    np.divide(summed, count, out=out, where=count > 0)
    return out.astype(np.float32)


def _setline_summary(grid: GridSpec, pcatch: np.ndarray, wind_dir_deg_to: np.ndarray, length_nm: float = 10.0) -> Dict[str, Any]:
    idx = int(np.nanargmax(np.where(np.isfinite(pcatch), pcatch, -1.0)))
    r, c = divmod(idx, grid.width)
    lon = float(_grid_lons(grid)[c]); lat = float(_grid_lats(grid)[r])
    wind_to = float(wind_dir_deg_to[r, c])
    heading = (wind_to + 90.0) % 180.0
    length_deg_lon = (length_nm * 1.852) / max(111.32 * np.cos(np.deg2rad(lat)), 1e-6)
    length_deg_lat = (length_nm * 1.852) / 110.57
    theta = np.deg2rad(heading)
    dx = 0.5 * length_deg_lon * np.sin(theta)
    dy = 0.5 * length_deg_lat * np.cos(theta)
    return {
        "center": {"lon": lon, "lat": lat},
        "heading_deg": heading,
        "wind_dir_deg_to": wind_to,
        "length_nm": length_nm,
        "line": [[lon-dx, lat-dy],[lon+dx, lat+dy]],
        "score": float(pcatch[r, c]),
    }


def _seed_from_ts(ts_iso: str) -> int:
    h = 2166136261
    for ch in ts_iso.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _dt_from_time_id(time_id: str) -> datetime:
    """Parse YYYYMMDD_HHMMZ into aware UTC datetime."""
    return datetime.strptime(time_id, "%Y%m%d_%H%MZ").replace(tzinfo=timezone.utc)


def _get_copernicus_creds() -> Tuple[str, str]:
    """Accept both project and toolbox env var names."""
    user = os.getenv("COPERNICUS_MARINE_USERNAME", "").strip()
    pwd = os.getenv("COPERNICUS_MARINE_PASSWORD", "").strip()

    if not user:
        user = os.getenv("COPERNICUSMARINE_SERVICE_USERNAME", "").strip()
    if not pwd:
        pwd = os.getenv("COPERNICUSMARINE_SERVICE_PASSWORD", "").strip()

    return user, pwd


def _synthetic_env_layers(grid: GridSpec, ts_iso: str) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(_seed_from_ts(ts_iso))
    lon2d, lat2d = grid.lonlat_mesh()

    sst = 26.0 + 2.0 * np.sin((lat2d - lat2d.mean()) * math.pi / 15.0) + 0.7 * np.cos((lon2d - lon2d.mean()) * math.pi / 20.0)
    sst += rng.normal(0, 0.25, size=sst.shape)

    chl = 0.2 + 0.08 * np.cos((lat2d - lat2d.mean()) * math.pi / 10.0) + 0.05 * np.sin((lon2d - lon2d.mean()) * math.pi / 12.0)
    chl = np.clip(chl + rng.normal(0, 0.01, size=chl.shape), 0.02, 2.0)

    ssh = 0.0 + 0.2 * np.sin((lon2d - lon2d.mean()) * math.pi / 8.0) * np.cos((lat2d - lat2d.mean()) * math.pi / 8.0)
    ssh += rng.normal(0, 0.01, size=ssh.shape)

    cur = 0.4 + 0.15 * np.sin((lon2d - lon2d.mean()) * math.pi / 10.0)
    cur = np.clip(cur + rng.normal(0, 0.03, size=cur.shape), 0.0, 1.5)
    ucur = (cur * np.cos((lat2d - lat2d.mean()) * math.pi / 18.0)).astype(np.float32)
    vcur = (cur * np.sin((lon2d - lon2d.mean()) * math.pi / 18.0)).astype(np.float32)

    waves = 1.1 + 0.4 * np.cos((lat2d - lat2d.mean()) * math.pi / 14.0)
    waves = np.clip(waves + rng.normal(0, 0.05, size=waves.shape), 0.0, 4.0)

    qc_chl = (rng.random(size=chl.shape) > 0.07).astype(np.uint8)
    conf = qc_chl.astype(np.float32)

    sss = 35.4 + 0.2 * np.sin((lon2d - lon2d.mean()) * math.pi / 14.0) + rng.normal(0, 0.03, size=sst.shape)
    o2 = 185.0 - 0.9 * (sst - 26.0) + rng.normal(0, 3.0, size=sst.shape)
    mld = np.clip(35.0 + 10.0 * np.cos((lat2d - lat2d.mean()) * math.pi / 12.0) + rng.normal(0, 2.0, size=sst.shape), 5.0, 120.0)
    npp = np.clip(1.2 + 0.4 * np.cos((lon2d - lon2d.mean()) * math.pi / 10.0) + rng.normal(0, 0.08, size=sst.shape), 0.05, None)
    return {
        "sst_c": sst.astype(np.float32),
        "chl_mg_m3": chl.astype(np.float32),
        "ssh_m": ssh.astype(np.float32),
        "current_m_s": cur.astype(np.float32),
        "u_current_m_s": ucur.astype(np.float32),
        "v_current_m_s": vcur.astype(np.float32),
        "waves_hs_m": waves.astype(np.float32),
        "sss_psu": sss.astype(np.float32),
        "o2_umol_l": o2.astype(np.float32),
        "mld_m": mld.astype(np.float32),
        "npp_mmol_m3_day": npp.astype(np.float32),
        "qc_chl": qc_chl,
        "conf": conf,
    }


def _try_copernicus_layers(
    grid: GridSpec,
    bbox: Tuple[float, float, float, float],
    ts_iso: str,
    datasets_cfg: Dict[str, Any],
    depth_target_m: Optional[float] = None,
) -> Tuple[Optional[Dict[str, np.ndarray]], Dict[str, Any]]:
    # Normalize datasets config: allow {"cmems": {...}} or direct mapping.
    if isinstance(datasets_cfg, dict) and "cmems" in datasets_cfg and isinstance(datasets_cfg["cmems"], dict):
        datasets_cfg = datasets_cfg["cmems"]

    user, pwd = _get_copernicus_creds()
    status: Dict[str, Any] = {"provider": "copernicusmarine", "ok": False, "errors": []}

    if not (user and pwd):
        status["errors"].append("missing Copernicus credentials (COPERNICUS_MARINE_* or COPERNICUSMARINE_SERVICE_*)")
        return None, status

    try:
        import copernicusmarine  # type: ignore
    except Exception as e:
        status["errors"].append(f"copernicusmarine import failed: {e}")
        return None, status

    for k in ["sst", "chl", "ssh", "currents", "waves"]:
        if not str(datasets_cfg.get(k, {}).get("dataset_id", "")).strip():
            status["errors"].append(f"datasets.json missing dataset_id for '{k}'")
            return None, status

    tmpdir = Path(os.getenv("SEYDYAAR_TMPDIR", ".seydyaar_tmp"))
    tmpdir.mkdir(parents=True, exist_ok=True)

    log_dir = Path(os.getenv("SEYDYAAR_LOG_DIR", "docs/latest/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = log_dir / "download_manifest.jsonl"

    depth_resolver = _DepthResolver()

    lon_min, lat_min, lon_max, lat_max = bbox
    t0 = dtparser.isoparse(ts_iso).astimezone(tz.UTC)

    def _subset_one(key: str) -> Path:
        cfg = datasets_cfg[key]
        dsid = cfg["dataset_id"]

        vars_ = cfg.get("variables", None)
        if not vars_:
            v = cfg.get("variable", None)
            vars_ = [v] if v else []
        if not vars_:
            raise RuntimeError(f"{key}: variables list is empty in datasets.json")

        offsets_h = [0, -6, -12, -18, -24, 6, 12, 18, 24]
        last_err: Optional[Exception] = None

        for off in offsets_h:
            tt0 = t0 + timedelta(hours=off)
            tt1 = tt0
            p = tmpdir / f"{key}_{tt0.strftime('%Y%m%dT%H%M%S')}.nc"
            try:
                # Prepare manifest record skeleton (filled on success/failure)
                rec: Dict[str, Any] = {
                    "layer": key,
                    "dataset_id": dsid,
                    "variables": vars_,
                    "requested_time_utc": t0.isoformat(),
                    "resolved_time_utc": tt0.isoformat(),
                    "bbox": [float(lon_min), float(lat_min), float(lon_max), float(lat_max)],
                    "coordinates_selection_method": "nearest",
                    "depth_target_m": cfg.get("depth_target_m", cfg.get("depth_m", None)),
                    "depth_selected_m": None,
                    "output_nc": str(p),
                    "ok": False,
                    "bytes": 0,
                    "sha256": None,
                    "error": None,
                }

                # Optional depth: if config asks for depth (often 0), resolve closest available depth via describe.
                depth_target = depth_target_m if depth_target_m is not None else cfg.get("depth_target_m", cfg.get("depth_m", None))
                min_depth = max_depth = None
                if depth_target is not None:
                    # Capability discovery: if dataset exposes a depth axis, pick the depth closest to target (usually 0m).
                    try:
                        target = float(depth_target)
                        best = depth_resolver.closest_depth(dsid, target_m=target)
                        if best is not None:
                            rec["depth_selected_m"] = float(best)
                            min_depth = float(best)
                            max_depth = float(best)
                    except Exception:
                        pass

                try:
                    copernicusmarine.subset(
                        dataset_id=dsid,
                        variables=vars_,
                        minimum_longitude=lon_min,
                        maximum_longitude=lon_max,
                        minimum_latitude=lat_min,
                        maximum_latitude=lat_max,
                        minimum_depth=min_depth,
                        maximum_depth=max_depth,
                        start_datetime=tt0.isoformat(),
                        end_datetime=tt1.isoformat(),
                        username=user,
                        password=pwd,
                        output_filename=str(p),
                        overwrite=True,
                        skip_existing=False,
                        coordinates_selection_method="nearest",
                    )
                    status.setdefault("resolved_times", {})[key] = tt0.isoformat()
                    status.setdefault("nc_paths", {})[key] = str(p)

                    if p.exists():
                        rec["ok"] = True
                        rec["bytes"] = p.stat().st_size
                        rec["sha256"] = _sha256_file(p)
                    _append_jsonl(manifest_path, rec)
                    return p
                except Exception as e:
                    rec["error"] = str(e)
                    _append_jsonl(manifest_path, rec)
                    raise

            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(f"{key}: subset failed for {t0.isoformat()} (tried ±24h). Last error: {last_err}")

    def _read_nc_var(path: Path, var: str) -> np.ndarray:
        import rasterio
        with rasterio.open(f'NETCDF:"{path}":{var}') as ds:
            arr = ds.read(1).astype(np.float32)
            nodata = ds.nodata
        # Convert common fill/nodata to NaN, and clip absurd values (e.g., 1e20)
        if nodata is not None:
            arr[arr == np.float32(nodata)] = np.nan
        arr[~np.isfinite(arr)] = np.nan
        arr[np.abs(arr) > np.float32(1e6)] = np.nan
        return arr
    def _resize_nearest(a: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
        """Nearest-neighbor resample to (target_h, target_w). Assumes regular lon/lat grid in the subset output."""
        src_h, src_w = a.shape
        if src_h == target_h and src_w == target_w:
            return a.astype(np.float32, copy=False)
        yi = np.rint(np.linspace(0, src_h - 1, target_h)).astype(np.int64)
        xi = np.rint(np.linspace(0, src_w - 1, target_w)).astype(np.int64)
        return a[np.ix_(yi, xi)].astype(np.float32, copy=False)

    def _to_grid(a: np.ndarray) -> np.ndarray:
        return _resize_nearest(a, grid.height, grid.width)


    out: Dict[str, np.ndarray] = {}

    try:
        def _v0(key: str) -> str:
            cfg = datasets_cfg[key]
            vs = cfg.get("variables")
            if vs and len(vs) > 0:
                return vs[0]
            v = cfg.get("variable")
            if not v:
                raise RuntimeError(f"datasets.json missing variable(s) for '{key}'")
            return v

        p = _subset_one("sst")
        sst = _read_nc_var(p, _v0("sst"))
        out["sst_c"] = _to_grid(sst)

        p = _subset_one("chl")
        chl = _read_nc_var(p, _v0("chl"))
        out["chl_mg_m3"] = _to_grid(chl)

        p = _subset_one("ssh")
        ssh = _read_nc_var(p, _v0("ssh"))
        out["ssh_m"] = _to_grid(ssh)

        p = _subset_one("currents")
        vars_uv = datasets_cfg["currents"]["variables"]
        if len(vars_uv) >= 2:
            u = _read_nc_var(p, vars_uv[0])
            v = _read_nc_var(p, vars_uv[1])
            u = _to_grid(u)
            v = _to_grid(v)
            # compute in float64 to avoid overflow from occasional fill values
            out["u_current_m_s"] = u.astype(np.float32)
            out["v_current_m_s"] = v.astype(np.float32)
            out["current_m_s"] = np.sqrt(u.astype(np.float64)**2 + v.astype(np.float64)**2).astype(np.float32)
        else:
            out["current_m_s"] = _to_grid(_read_nc_var(p, vars_uv[0]))

        p = _subset_one("waves")
        waves = _read_nc_var(p, _v0("waves"))
        out["waves_hs_m"] = _to_grid(waves)

        # Optional extra predictors (if configured): salinity (SSS), mixed layer depth (MLD), dissolved oxygen (O2)
        # These are *optional* to keep the pipeline robust when datasets are not configured yet.
        status.setdefault("warnings", [])
        def _try_optional(key: str, out_key: str) -> None:
            cfg = datasets_cfg.get(key, {}) if isinstance(datasets_cfg, dict) else {}
            if not str(cfg.get("dataset_id", "")).strip():
                return
            try:
                pp = _subset_one(key)
                arr = _read_nc_var(pp, _v0(key))
                out[out_key] = _to_grid(arr)
            except Exception as ee:
                status["warnings"].append(f"{key} optional layer skipped: {ee}")

        _try_optional("sss", "sss_psu")
        _try_optional("mld", "mld_m")
        _try_optional("o2", "o2_umol_l")
        _try_optional("npp", "npp_mmol_m3_day")


        qc = np.ones((grid.height, grid.width), dtype=np.uint8)
        conf = qc.astype(np.float32)
        out["qc_chl"] = qc
        out["conf"] = conf

        status["ok"] = True
        return out, status

    except Exception as e:
        status["errors"].append(str(e))
        return None, status


def _write_meta_index(out_root: Path, run_entry: Dict[str, Any]) -> None:
    idx_path = out_root / "meta_index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            idx = {"version": 1, "runs": []}
    else:
        idx = {"version": 1, "runs": []}

    idx["runs"] = [r for r in idx.get("runs", []) if r.get("run_id") != run_entry["run_id"]] + [run_entry]
    idx["runs"] = sorted(idx["runs"], key=lambda r: r.get("generated_at_utc", ""))
    idx["latest_run_id"] = run_entry["run_id"]

    now_utc, _ = trusted_utc_now()
    idx["generated_at_utc"] = now_utc.isoformat().replace("+00:00", "Z")

    write_json(idx_path, idx)
    minify_json_for_web(idx_path)


def _write_latest_index_and_meta(out_root: Path, run_entry: Dict[str, Any], variant: str) -> None:
    run_root = out_root / run_entry.get("path", "")
    run_meta_path = run_root / "meta.json"
    run_meta = None
    if run_meta_path.exists():
        try:
            run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
        except Exception:
            run_meta = None

    time_ids = (run_meta or {}).get("available_time_ids") or []
    latest_tid = (run_meta or {}).get("latest_available_time_id") or (time_ids[-1] if time_ids else None)

    now_utc, _ = trusted_utc_now()
    gen = now_utc.isoformat().replace("+00:00", "Z")

    index = {
        "version": 1,
        "schema": "seydyaar-latest-index-v1",
        "generated_at_utc": gen,
        "latest_run_id": run_entry.get("run_id"),
        "run_path": run_entry.get("path"),
        "variant_default": variant,
        "species": run_entry.get("species", []),
        "models": run_entry.get("models", []),
        "time_count": len(time_ids),
        "available_time_ids": time_ids,
        "latest_available_time_id": latest_tid,
        "notes": "Compatibility endpoint. Raw outputs live under runs/<run_id>/variants/...",
    }
    idx_out = out_root / "index.json"
    write_json(idx_out, index)
    minify_json_for_web(idx_out)

    meta = {
        "version": 1,
        "generated_at_utc": gen,
        "run_id": run_entry.get("run_id"),
        "variant": variant,
        "time_source": (run_meta or {}).get("time_source"),
        "latest_available_time_id": latest_tid,
        "grid": (run_meta or {}).get("grid"),
        "bbox": (run_meta or {}).get("bbox"),
        "aoi": (run_meta or {}).get("aoi"),
        "species": run_entry.get("species", []),
        "models": run_entry.get("models", []),
        "available_time_ids": time_ids,
    }
    meta_out = out_root / "meta.json"
    write_json(meta_out, meta)
    minify_json_for_web(meta_out)


def run_daily(
    out_root: Path,
    aoi_geojson: dict,
    species_profiles: dict,
    date: str = "today",
    past_days: int = 2,
    future_days: int = 10,
    step_hours: int = 6,
    grid_wh: str = "220x220",
    variant: str = "auto",
    gear_depths_m: List[int] = [5, 10, 15, 20],
) -> str:
    now_utc, time_source = trusted_utc_now()
    anchor = now_utc.date() if date.lower() == "today" else datetime.fromisoformat(date).date()

    step_hours = max(int(step_hours), 6)
    run_id = "main"

    W, H = [int(x) for x in grid_wh.lower().split("x")]

    bbox = bbox_from_geojson(aoi_geojson)
    grid = GridSpec(lon_min=bbox[0], lat_min=bbox[1], lon_max=bbox[2], lat_max=bbox[3], width=W, height=H)
    mask = mask_from_geojson(aoi_geojson, grid)

    ts_list = timestamps_for_range(anchor_date=date, past_days=past_days, future_days=future_days, step_hours=step_hours)
    time_ids = [time_id_from_iso(iso) for iso in ts_list]
    id_by_iso = {iso: tid for iso, tid in zip(ts_list, time_ids)}

    run_root = out_root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    strict_cmems = os.getenv("SEYDYAAR_STRICT_COPERNICUS", "0") == "1"
    verify_dir = Path(os.getenv("SEYDYAAR_VERIFY_DIR", out_root / "verify"))
    verify_dir.mkdir(parents=True, exist_ok=True)
    verify_time_id = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y%m%d_0000Z")

    run_meta = {
        "run_id": run_id,
        "date": anchor.isoformat(),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "time_source": time_source,
        "times": ts_list,
        "time_ids": time_ids,
        "available_time_ids": time_ids,
        "latest_available_time_id": (time_ids[-1] if time_ids else None),
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "bbox": list(bbox),
        "step_hours": step_hours,
        "grid": {"width": W, "height": H, "lon_min": grid.lon_min, "lon_max": grid.lon_max, "lat_min": grid.lat_min, "lat_max": grid.lat_max},
    }
    write_json(run_root / "meta.json", run_meta)
    minify_json_for_web(run_root / "meta.json")

    datasets_cfg_path = Path("backend/config/datasets.json")
    datasets_cfg = json.loads(datasets_cfg_path.read_text(encoding="utf-8")) if datasets_cfg_path.exists() else {}
    if isinstance(datasets_cfg, dict) and "cmems" in datasets_cfg and isinstance(datasets_cfg["cmems"], dict):
        datasets_cfg = datasets_cfg["cmems"]

    # >>> IMPORTANT: define cache HERE (always in run_daily scope)
    layers_cache: Dict[str, Tuple[Dict[str, np.ndarray], Dict[str, Any]]] = {}

    force = os.getenv("SEYDYAAR_FORCE_REGEN", "0") == "1"

    for sp, prof in species_profiles.items():
        priors = prof.get("priors", {})
        weights = prof.get("layer_weights", {})
        ops_priors = prof.get("ops_constraints", {})
        ops_priors = {**priors, **ops_priors}

        sp_root = run_root / "variants" / variant / "species" / sp
        times_root = sp_root / "times"
        times_root.mkdir(parents=True, exist_ok=True)

        write_bin_u8(sp_root / "mask_u8.bin", mask)

        sp_meta = {
            "species": sp,
            "label": prof.get("label", {}),
            "grid": run_meta["grid"],
            "times": ts_list,
            "time_ids": time_ids,
            "paths": {
                "mask": f"variants/{variant}/species/{sp}/mask_u8.bin",
                "per_time": {
                    "pcatch_scoring": f"variants/{variant}/species/{sp}/times/{{time}}/pcatch_scoring_f32.bin",
                    "pcatch_frontplus": f"variants/{variant}/species/{sp}/times/{{time}}/pcatch_frontplus_f32.bin",
                    "pcatch_ensemble": f"variants/{variant}/species/{sp}/times/{{time}}/pcatch_ensemble_f32.bin",
                    "phab_scoring": f"variants/{variant}/species/{sp}/times/{{time}}/phab_f32.bin",
                    "phab_frontplus": f"variants/{variant}/species/{sp}/times/{{time}}/phab_f32.bin",
                    "pops": f"variants/{variant}/species/{sp}/times/{{time}}/pops_f32.bin",
                    "agree": f"variants/{variant}/species/{sp}/times/{{time}}/agree_f32.bin",
                    "spread": f"variants/{variant}/species/{sp}/times/{{time}}/spread_f32.bin",
                    "front": f"variants/{variant}/species/{sp}/times/{{time}}/front_f32.bin",
                    "sst": f"variants/{variant}/species/{sp}/times/{{time}}/sst_f32.bin",
                    "chl": f"variants/{variant}/species/{sp}/times/{{time}}/chl_f32.bin",
                    "current": f"variants/{variant}/species/{sp}/times/{{time}}/current_f32.bin",
                    "waves": f"variants/{variant}/species/{sp}/times/{{time}}/waves_f32.bin",
                    "sss": f"variants/{variant}/species/{sp}/times/{{time}}/sss_f32.bin",
                    "o2": f"variants/{variant}/species/{sp}/times/{{time}}/o2_f32.bin",
                    "mld": f"variants/{variant}/species/{sp}/times/{{time}}/mld_f32.bin",
                    "thermocline": f"variants/{variant}/species/{sp}/times/{{time}}/thermocline_f32.bin",
                    "oxygen_access": f"variants/{variant}/species/{sp}/times/{{time}}/oxygen_access_f32.bin",
                    "npp": f"variants/{variant}/species/{sp}/times/{{time}}/npp_f32.bin",
                    "front_gradient": f"variants/{variant}/species/{sp}/times/{{time}}/front_gradient_f32.bin",
                    "front_boa": f"variants/{variant}/species/{sp}/times/{{time}}/front_boa_f32.bin",
                    "front_cca": f"variants/{variant}/species/{sp}/times/{{time}}/front_cca_f32.bin",
                    "front_gradhist": f"variants/{variant}/species/{sp}/times/{{time}}/front_gradhist_f32.bin",
                    "front_fused": f"variants/{variant}/species/{sp}/times/{{time}}/front_fused_f32.bin",
                    "conf": f"variants/{variant}/species/{sp}/times/{{time}}/conf_f32.bin",
                    "wind_speed": f"variants/{variant}/species/{sp}/times/{{time}}/wind_speed_f32.bin",
                    "wind_direction": f"variants/{variant}/species/{sp}/times/{{time}}/wind_direction_f32.bin",
                    "setline_score": f"variants/{variant}/species/{sp}/times/{{time}}/setline_score_f32.bin",
                    "setline_heading": f"variants/{variant}/species/{sp}/times/{{time}}/setline_heading_f32.bin",
                    "qc_chl": f"variants/{variant}/species/{sp}/times/{{time}}/qc_chl_u8.bin",
                },
            },
            "model_info": {
                "habitat": {"priors": priors, "weights": weights},
                "ops": {"priors": ops_priors, "gear_depths_m": gear_depths_m},
            },
        }
        for base_key in ["sst","chl","current","waves","sss","o2","mld","thermocline","oxygen_access","npp","front","front_gradient","front_boa","front_cca","front_gradhist","front_fused","agree","spread","conf","phab_scoring","pops","pcatch_scoring","pcatch_frontplus","pcatch_ensemble"]:
            for d in DEPTH_TARGETS_M:
                sp_meta["paths"]["per_time"][f"{base_key}_depth_{d}m"] = f"variants/{variant}/species/{sp}/times/{{time}}/{base_key}_depth_{d}m_f32.bin"
            sp_meta["paths"]["per_time"][f"{base_key}_depth_weighted"] = f"variants/{variant}/species/{sp}/times/{{time}}/{base_key}_depth_weighted_f32.bin"

        write_json(sp_root / "meta.json", sp_meta)
        minify_json_for_web(sp_root / "meta.json")

        provider_status: List[Dict[str, Any]] = []

        for ts_iso in ts_list:
            tid = id_by_iso[ts_iso]

            if (not force) and (times_root / tid / "pcatch_scoring_f32.bin").exists():
                provider_status.append({"timestamp": ts_iso, "skipped": True, "reason": "already_exists"})
                continue

            # Cache across species by tid
            # Defensive: ensure cache exists (prevents NameError if file was partially edited)
            try:
                layers_cache
            except NameError:
                layers_cache = {}

            if tid in layers_cache:
                layers, status = layers_cache[tid]
            else:
                layers, status = _try_copernicus_layers(grid, bbox, ts_iso, datasets_cfg) if datasets_cfg else (None, {"provider":"none","ok":False,"errors":["no datasets.json"]})
                if layers is None:
                    if strict_cmems:
                        raise RuntimeError("Copernicus download failed (strict mode): " + "; ".join(status.get("errors", [])))
                    layers = _synthetic_env_layers(grid, ts_iso)
                    status = {**status, "fallback": "synthetic"}
                layers_cache[tid] = (layers, status)

            provider_status.append({"timestamp": ts_iso, **status})
            # Save raw NetCDFs for today 00:00Z so you can cross-check with Copernicus Viewer.
            if tid == verify_time_id and isinstance(status, dict):
                nc_paths = status.get("nc_paths") or {}
                dest = verify_dir / verify_time_id
                dest.mkdir(parents=True, exist_ok=True)
                for k, src in nc_paths.items():
                    try:
                        sp = Path(src)
                        if sp.exists():
                            shutil.copy2(sp, dest / f"{k}.nc")
                    except Exception:
                        pass

            t_front = gradient_magnitude(layers["sst_c"])
            c_front = gradient_magnitude(np.log10(np.clip(layers["chl_mg_m3"], 1e-6, None)))
            s_front = gradient_magnitude(layers["ssh_m"])
            f = front_score(
                t_front, c_front, s_front,
                w_temp=float(priors.get("front_weights", {}).get("temp", 0.5)),
                w_chl=float(priors.get("front_weights", {}).get("chl", 0.25)),
                w_ssh=float(priors.get("front_weights", {}).get("ssh", 0.25)),
            ).astype(np.float32)

            inputs = HabitatInputs(
                sst_c=layers["sst_c"],
                chl_mg_m3=layers["chl_mg_m3"],
                current_m_s=layers["current_m_s"],
                waves_hs_m=layers["waves_hs_m"],
                ssh_m=layers["ssh_m"],
                sss_psu=layers.get("sss_psu"),
                o2_umol_l=layers.get("o2_umol_l"),
                mld_m=layers.get("mld_m"),
                npp_mmol_m3_day=layers.get("npp_mmol_m3_day"),
            )
            phab, comps = habitat_scoring(inputs, priors=priors, weights=weights)
            f = np.asarray(comps.get("front_fused", f), dtype=np.float32)
            f_gradient = np.asarray(comps.get("front_gradient", f), dtype=np.float32)
            f_boa = np.asarray(comps.get("front_boa", f), dtype=np.float32)
            f_cca = np.asarray(comps.get("front_cca", f), dtype=np.float32)
            f_gradhist = np.asarray(comps.get("front_gradhist", f), dtype=np.float32)
            thermocline = np.asarray(comps.get("thermocline_proxy", np.zeros_like(phab)), dtype=np.float32)
            oxygen_access = np.asarray(comps.get("oxygen_access", np.zeros_like(phab)), dtype=np.float32)
            pops = ops_feasibility(inputs.current_m_s, inputs.waves_hs_m, ops_priors, gear_depth_m=10.0)
            pcatch = np.clip(phab * pops, 0, 1).astype(np.float32)

            front_mult = np.clip(0.9 + 0.3 * f, 0.9, 1.2).astype(np.float32)
            m2 = np.clip(pcatch * front_mult, 0, 1).astype(np.float32)
            ens = _safe_stack_nanmean([pcatch, m2])
            agree, spread = ensemble_stats([pcatch, m2])

            depth_raw: Dict[int, Dict[str, np.ndarray]] = {}
            depth_phab: Dict[int, np.ndarray] = {}
            depth_pops: Dict[int, np.ndarray] = {}
            depth_pcatch: Dict[int, np.ndarray] = {}
            depth_front: Dict[int, np.ndarray] = {}
            depth_agree: Dict[int, np.ndarray] = {}
            depth_spread: Dict[int, np.ndarray] = {}
            depth_comps: Dict[int, Dict[str, np.ndarray]] = {}
            for d in DEPTH_TARGETS_M:
                if d == 0:
                    dl = dict(layers)
                else:
                    dl, dst = _try_copernicus_layers(grid, bbox, ts_iso, {"cmems": datasets_cfg} if "sst" in datasets_cfg else datasets_cfg, depth_target_m=float(d))
                    if dl is None:
                        dl = _derive_depth_layers(layers, priors, float(d))
                for k in ["sss_psu","o2_umol_l"]:
                    if k not in dl and k in layers:
                        dl[k] = _derive_depth_layers(layers, priors, float(d)).get(k, layers[k])
                din = HabitatInputs(
                    sst_c=dl["sst_c"],
                    chl_mg_m3=dl["chl_mg_m3"],
                    current_m_s=dl["current_m_s"],
                    waves_hs_m=dl["waves_hs_m"],
                    ssh_m=dl["ssh_m"],
                    sss_psu=dl.get("sss_psu"),
                    o2_umol_l=dl.get("o2_umol_l"),
                    mld_m=dl.get("mld_m"),
                    npp_mmol_m3_day=dl.get("npp_mmol_m3_day"),
                )
                dph, dcomps = habitat_scoring(din, priors=priors, weights=weights)
                dpo = ops_feasibility(din.current_m_s, din.waves_hs_m, ops_priors, gear_depth_m=float(d))
                dpc = np.clip(dph * dpo, 0, 1).astype(np.float32)
                dtf = gradient_magnitude(din.sst_c)
                dcf = gradient_magnitude(np.log10(np.clip(din.chl_mg_m3, 1e-6, None)))
                dsf = gradient_magnitude(din.ssh_m)
                df = front_score(dtf, dcf, dsf,
                    w_temp=float(priors.get("front_weights", {}).get("temp", 0.5)),
                    w_chl=float(priors.get("front_weights", {}).get("chl", 0.25)),
                    w_ssh=float(priors.get("front_weights", {}).get("ssh", 0.25)),
                ).astype(np.float32)
                dm2 = np.clip(dpc * np.clip(0.9 + 0.3 * df, 0.9, 1.2), 0, 1).astype(np.float32)
                dag, dsp = ensemble_stats([dpc, dm2])
                depth_raw[d] = dl
                depth_phab[d] = dph.astype(np.float32)
                depth_pops[d] = dpo.astype(np.float32)
                depth_pcatch[d] = _safe_stack_nanmean([dpc, dm2])
                depth_front[d] = df
                depth_agree[d] = dag.astype(np.float32)
                depth_spread[d] = dsp.astype(np.float32)
                depth_comps[d] = dcomps

            wind = _try_local_era5_wind(grid, _dt_from_time_id(tid), json.loads(cfg_path.read_text(encoding="utf-8")) if (cfg_path:=Path("backend/config/datasets.json")).exists() else {})
            if wind is None:
                # simple operational proxy from waves and fronts
                gy, gx = np.gradient(inputs.ssh_m.astype(np.float32))
                wu = (-gx).astype(np.float32)
                wv = (-gy).astype(np.float32)
                wspd = np.sqrt(wu.astype(np.float64)**2 + wv.astype(np.float64)**2).astype(np.float32)
                wdir = (np.degrees(np.arctan2(wu, wv)) + 360.0) % 360.0
                wind = {"wind_u10_m_s": wu, "wind_v10_m_s": wv, "wind_speed_m_s": wspd, "wind_dir_deg_to": wdir.astype(np.float32), "wind_source": np.array([0], dtype=np.uint8)}
            setline = _setline_summary(grid, ens, wind["wind_dir_deg_to"])
            setline_score = ens.astype(np.float32)
            setline_heading = (((wind["wind_dir_deg_to"] + 90.0) % 180.0)).astype(np.float32)

            tdir = times_root / tid
            tdir.mkdir(parents=True, exist_ok=True)

            write_bin_f32(tdir / "pcatch_scoring_f32.bin", pcatch)
            write_bin_f32(tdir / "pcatch_frontplus_f32.bin", m2)
            write_bin_f32(tdir / "pcatch_ensemble_f32.bin", ens)
            write_bin_f32(tdir / "phab_f32.bin", phab)
            write_bin_f32(tdir / "pops_f32.bin", pops)
            write_bin_f32(tdir / "agree_f32.bin", agree)
            write_bin_f32(tdir / "spread_f32.bin", spread)
            write_bin_f32(tdir / "front_f32.bin", f)
            write_bin_f32(tdir / "sst_f32.bin", inputs.sst_c.astype(np.float32))
            write_bin_f32(tdir / "chl_f32.bin", inputs.chl_mg_m3.astype(np.float32))
            write_bin_f32(tdir / "current_f32.bin", inputs.current_m_s.astype(np.float32))
            write_bin_f32(tdir / "waves_f32.bin", inputs.waves_hs_m.astype(np.float32))
            write_bin_f32(tdir / "sss_f32.bin", np.asarray(layers.get("sss_psu", np.full_like(inputs.sst_c, np.nan)), dtype=np.float32))
            write_bin_f32(tdir / "o2_f32.bin", np.asarray(layers.get("o2_umol_l", np.full_like(inputs.sst_c, np.nan)), dtype=np.float32))
            write_bin_f32(tdir / "mld_f32.bin", np.asarray(layers.get("mld_m", np.full_like(inputs.sst_c, np.nan)), dtype=np.float32))
            write_bin_f32(tdir / "thermocline_f32.bin", thermocline)
            write_bin_f32(tdir / "oxygen_access_f32.bin", oxygen_access)
            write_bin_f32(tdir / "npp_f32.bin", np.asarray(layers.get("npp_mmol_m3_day", np.full_like(inputs.sst_c, np.nan)), dtype=np.float32))
            write_bin_f32(tdir / "front_gradient_f32.bin", f_gradient)
            write_bin_f32(tdir / "front_boa_f32.bin", f_boa)
            write_bin_f32(tdir / "front_cca_f32.bin", f_cca)
            write_bin_f32(tdir / "front_gradhist_f32.bin", f_gradhist)
            write_bin_f32(tdir / "front_fused_f32.bin", f)
            write_bin_u8(tdir / "qc_chl_u8.bin", layers["qc_chl"])
            write_bin_f32(tdir / "conf_f32.bin", layers["conf"])
            write_bin_f32(tdir / "wind_speed_f32.bin", wind["wind_speed_m_s"].astype(np.float32))
            write_bin_f32(tdir / "wind_direction_f32.bin", wind["wind_dir_deg_to"].astype(np.float32))
            write_bin_f32(tdir / "setline_score_f32.bin", setline_score)
            write_bin_f32(tdir / "setline_heading_f32.bin", setline_heading)
            write_json(tdir / "setline_best.json", {**setline, "wind_source": "era5" if int(wind["wind_source"][0])==1 else "proxy"})

            for d in DEPTH_TARGETS_M:
                dl = depth_raw[d]
                write_bin_f32(tdir / f"sst_depth_{d}m_f32.bin", dl["sst_c"].astype(np.float32))
                write_bin_f32(tdir / f"chl_depth_{d}m_f32.bin", dl["chl_mg_m3"].astype(np.float32))
                write_bin_f32(tdir / f"current_depth_{d}m_f32.bin", dl["current_m_s"].astype(np.float32))
                write_bin_f32(tdir / f"waves_depth_{d}m_f32.bin", dl["waves_hs_m"].astype(np.float32))
                write_bin_f32(tdir / f"sss_depth_{d}m_f32.bin", np.asarray(dl.get("sss_psu", np.full_like(dl["sst_c"], np.nan)), dtype=np.float32))
                write_bin_f32(tdir / f"o2_depth_{d}m_f32.bin", np.asarray(dl.get("o2_umol_l", np.full_like(dl["sst_c"], np.nan)), dtype=np.float32))
                write_bin_f32(tdir / f"mld_depth_{d}m_f32.bin", np.asarray(dl.get("mld_m", np.full_like(dl["sst_c"], np.nan)), dtype=np.float32))
                write_bin_f32(tdir / f"thermocline_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("thermocline_proxy", np.zeros_like(dl["sst_c"])), dtype=np.float32))
                write_bin_f32(tdir / f"oxygen_access_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("oxygen_access", np.zeros_like(dl["sst_c"])), dtype=np.float32))
                write_bin_f32(tdir / f"npp_depth_{d}m_f32.bin", np.asarray(dl.get("npp_mmol_m3_day", np.full_like(dl["sst_c"], np.nan)), dtype=np.float32))
                write_bin_f32(tdir / f"front_gradient_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("front_gradient", depth_front[d]), dtype=np.float32))
                write_bin_f32(tdir / f"front_boa_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("front_boa", depth_front[d]), dtype=np.float32))
                write_bin_f32(tdir / f"front_cca_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("front_cca", depth_front[d]), dtype=np.float32))
                write_bin_f32(tdir / f"front_gradhist_depth_{d}m_f32.bin", np.asarray(depth_comps[d].get("front_gradhist", depth_front[d]), dtype=np.float32))
                write_bin_f32(tdir / f"front_fused_depth_{d}m_f32.bin", depth_front[d])
                write_bin_f32(tdir / f"front_depth_{d}m_f32.bin", depth_front[d])
                write_bin_f32(tdir / f"agree_depth_{d}m_f32.bin", depth_agree[d])
                write_bin_f32(tdir / f"spread_depth_{d}m_f32.bin", depth_spread[d])
                write_bin_f32(tdir / f"conf_depth_{d}m_f32.bin", layers["conf"].astype(np.float32))
                write_bin_f32(tdir / f"phab_scoring_depth_{d}m_f32.bin", depth_phab[d])
                write_bin_f32(tdir / f"pops_depth_{d}m_f32.bin", depth_pops[d])
                write_bin_f32(tdir / f"pcatch_scoring_depth_{d}m_f32.bin", np.clip(depth_phab[d] * depth_pops[d], 0, 1).astype(np.float32))
                write_bin_f32(tdir / f"pcatch_frontplus_depth_{d}m_f32.bin", np.clip((depth_phab[d] * depth_pops[d]) * np.clip(0.9 + 0.3 * depth_front[d], 0.9, 1.2), 0, 1).astype(np.float32))
                write_bin_f32(tdir / f"pcatch_ensemble_depth_{d}m_f32.bin", depth_pcatch[d])
            weighted_layers = {
                "sst": _weighted_depth_mean({d: depth_raw[d]["sst_c"] for d in DEPTH_TARGETS_M}),
                "chl": _weighted_depth_mean({d: depth_raw[d]["chl_mg_m3"] for d in DEPTH_TARGETS_M}),
                "current": _weighted_depth_mean({d: depth_raw[d]["current_m_s"] for d in DEPTH_TARGETS_M}),
                "waves": _weighted_depth_mean({d: depth_raw[d]["waves_hs_m"] for d in DEPTH_TARGETS_M}),
                "sss": _weighted_depth_mean({d: np.asarray(depth_raw[d].get("sss_psu", np.full_like(depth_raw[d]["sst_c"], np.nan)), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "o2": _weighted_depth_mean({d: np.asarray(depth_raw[d].get("o2_umol_l", np.full_like(depth_raw[d]["sst_c"], np.nan)), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "mld": _weighted_depth_mean({d: np.asarray(depth_raw[d].get("mld_m", np.full_like(depth_raw[d]["sst_c"], np.nan)), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "thermocline": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("thermocline_proxy", np.zeros_like(depth_raw[d]["sst_c"])), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "oxygen_access": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("oxygen_access", np.zeros_like(depth_raw[d]["sst_c"])), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "npp": _weighted_depth_mean({d: np.asarray(depth_raw[d].get("npp_mmol_m3_day", np.full_like(depth_raw[d]["sst_c"], np.nan)), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "front": _weighted_depth_mean(depth_front),
                "front_gradient": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("front_gradient", depth_front[d]), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "front_boa": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("front_boa", depth_front[d]), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "front_cca": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("front_cca", depth_front[d]), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "front_gradhist": _weighted_depth_mean({d: np.asarray(habitat_scoring(HabitatInputs(sst_c=depth_raw[d]["sst_c"], chl_mg_m3=depth_raw[d]["chl_mg_m3"], current_m_s=depth_raw[d]["current_m_s"], waves_hs_m=depth_raw[d]["waves_hs_m"], ssh_m=depth_raw[d]["ssh_m"], sss_psu=depth_raw[d].get("sss_psu"), o2_umol_l=depth_raw[d].get("o2_umol_l"), mld_m=depth_raw[d].get("mld_m"), npp_mmol_m3_day=depth_raw[d].get("npp_mmol_m3_day")), priors=priors, weights=weights)[1].get("front_gradhist", depth_front[d]), dtype=np.float32) for d in DEPTH_TARGETS_M}),
                "front_fused": _weighted_depth_mean(depth_front),
                "agree": _weighted_depth_mean(depth_agree),
                "spread": _weighted_depth_mean(depth_spread),
                "conf": layers["conf"].astype(np.float32),
                "phab_scoring": _weighted_depth_mean(depth_phab),
                "pops": _weighted_depth_mean(depth_pops),
                "pcatch_ensemble": _weighted_depth_mean(depth_pcatch),
            }
            weighted_layers["pcatch_scoring"] = np.clip(weighted_layers["phab_scoring"] * weighted_layers["pops"], 0, 1).astype(np.float32)
            weighted_layers["pcatch_frontplus"] = np.clip(weighted_layers["pcatch_scoring"] * np.clip(0.9 + 0.3 * weighted_layers["front"], 0.9, 1.2), 0, 1).astype(np.float32)
            for k, arr in weighted_layers.items():
                write_bin_f32(tdir / f"{k}_depth_weighted_f32.bin", arr.astype(np.float32))

        sp_meta2 = json.loads((sp_root / "meta.json").read_text(encoding="utf-8"))
        sp_meta2["provider_status"] = provider_status
        write_json(sp_root / "meta.json", sp_meta2)
        minify_json_for_web(sp_root / "meta.json")

    run_entry = {
        "run_id": run_id,
        "path": f"runs/{run_id}",
        "fast": False,
        "date": anchor.isoformat(),
        "time_count": len(time_ids),
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "models": ["scoring", "frontplus", "ensemble"],
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
    }
    _write_meta_index(out_root, run_entry)
    _write_latest_index_and_meta(out_root, run_entry, variant)


    # --- Always write logs/verify summaries for CI/QA ---
    try:
        logs_dir = out_root / "logs"
        verify_dir = out_root / "verify"
        logs_dir.mkdir(parents=True, exist_ok=True)
        verify_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
        (verify_dir / "keep.txt").write_text("keep\n", encoding="utf-8")

        # Minimal verify summary: existence checks for key outputs
        summary = {
            "run_id": run_id,
            "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
            "ok": True,
            "missing": [],
            "provider_status": provider_status,
        }

        critical = [
            out_root / "meta_index.json",
            out_root / "meta.json",
            out_root / "runs" / run_id / "meta.json",
        ]
        for pth in critical:
            if not pth.exists():
                summary["ok"] = False
                summary["missing"].append(str(pth.relative_to(out_root)))

        # Spot-check first time slice per species (if present)
        try:
            first_tid = time_ids[0] if time_ids else None
            if first_tid:
                for sp in list(species_profiles.keys())[:3]:
                    tdir = out_root / "runs" / run_id / "variants" / variant / "species" / sp / "times" / first_tid
                    for fn in ["pcatch_ensemble_f32.bin", "phab_f32.bin", "pops_f32.bin", "front_f32.bin"]:
                        fp = tdir / fn
                        if not fp.exists():
                            summary["ok"] = False
                            summary["missing"].append(str(fp.relative_to(out_root)))
        except Exception as _e:
            summary["ok"] = False
            summary["missing"].append(f"spotcheck_error:{type(_e).__name__}")

        (verify_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Never fail the pipeline because of verify/log writing
        pass

    return run_id
