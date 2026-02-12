from __future__ import annotations

"""
Scheduled "real" run generator for GitHub Pages hosting.

Key idea:
- GitHub Pages is static, so the web app *reads files* under docs/latest/.
- GitHub Actions runs this generator daily and commits new outputs.

This pipeline writes the SAME file layout that docs/app.js expects:
  docs/latest/meta_index.json
  docs/latest/runs/<run_id>/meta.json
  docs/latest/runs/<run_id>/variants/<variant>/species/<species>/meta.json
  docs/latest/runs/<run_id>/variants/<variant>/species/<species>/mask_u8.bin
  docs/latest/runs/<run_id>/variants/<variant>/species/<species>/times/<timeId>/<layers>.bin

Layers written per time (all float32 bins unless noted):
  pcatch_f32.bin, phab_f32.bin, pops_f32.bin, agree_f32.bin, spread_f32.bin
  sst_f32.bin, chl_f32.bin, current_f32.bin, waves_f32.bin, front_f32.bin
  qc_chl_u8.bin (quality mask, uint8 0/1)
  conf_f32.bin (confidence 0..1, float32)

Real data:
- If Copernicus Marine is configured (env user/pass + datasets.json filled), the code *tries* to use it.
- Otherwise, it falls back to deterministic synthetic layers (keeps UI working end-to-end).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import json
import os
import math
import numpy as np
import requests
from dateutil import parser as dtparser
from dateutil import tz

from ..utils_geo import bbox_from_geojson, GridSpec, mask_from_geojson
from ..utils_time import trusted_utc_now, timestamps_for_range
from ..utils_time import time_id_from_iso
from ..models.scoring import HabitatInputs, habitat_scoring, gradient_magnitude, front_score
from ..models.ops import ops_feasibility
from ..models.ensemble import ensemble_stats
from .io import write_bin_f32, write_bin_u8, write_json, minify_json_for_web


def _seed_from_ts(ts_iso: str) -> int:
    h = 2166136261
    for ch in ts_iso.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _dt_from_time_id(time_id: str) -> datetime:
    """Parse YYYYMMDD_HHMMZ into aware UTC datetime."""
    # Example: 20260211_0600Z
    return datetime.strptime(time_id, "%Y%m%d_%H%MZ").replace(tzinfo=timezone.utc)


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

    waves = 1.1 + 0.4 * np.cos((lat2d - lat2d.mean()) * math.pi / 14.0)
    waves = np.clip(waves + rng.normal(0, 0.05, size=waves.shape), 0.0, 4.0)

    qc_chl = (rng.random(size=chl.shape) > 0.07).astype(np.uint8)  # ~7% "bad" pixels
    conf = qc_chl.astype(np.float32)

    return {
        "sst_c": sst.astype(np.float32),
        "chl_mg_m3": chl.astype(np.float32),
        "ssh_m": ssh.astype(np.float32),
        "current_m_s": cur.astype(np.float32),
        "waves_hs_m": waves.astype(np.float32),
        "qc_chl": qc_chl,
        "conf": conf,
    }


def _try_copernicus_layers(
    grid: GridSpec,
    bbox: Tuple[float, float, float, float],
    ts_iso: str,
    datasets_cfg: Dict[str, Any],
) -> Tuple[Optional[Dict[str, np.ndarray]], Dict[str, Any]]:
    user = os.getenv("COPERNICUS_MARINE_USERNAME", "")
    pwd = os.getenv("COPERNICUS_MARINE_PASSWORD", "")
    status: Dict[str, Any] = {"provider": "copernicusmarine", "ok": False, "errors": []}

    if not (user and pwd):
        status["errors"].append("missing COPERNICUS_MARINE_USERNAME/PASSWORD")
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

    tmpdir = Path(".seydyaar_tmp")
    tmpdir.mkdir(exist_ok=True)

    lon_min, lat_min, lon_max, lat_max = bbox
    t0 = dtparser.isoparse(ts_iso).astimezone(tz.UTC)
    t1 = t0

    def _subset_one(key: str) -> Path:
        cfg = datasets_cfg[key]
        dsid = cfg["dataset_id"]
        # Support both "variable" (single) and "variables" (list)
        vars_ = cfg.get("variables", None)
        if not vars_:
            v = cfg.get("variable", None)
            vars_ = [v] if v else []
        if not vars_:
            raise RuntimeError(f"{key}: variables list is empty in datasets.json")
        # Best-effort "latest available" fallback: try nearest times (handles daily / cadence gaps)
        offsets_h = [0, -6, -12, -18, -24, 6, 12, 18, 24]
        last_err: Optional[Exception] = None
        for off in offsets_h:
            tt0 = t0 + dt.timedelta(hours=off)
            tt1 = tt0
            p = tmpdir / f"{key}_{tt0.strftime('%Y%m%dT%H%M%S')}.nc"
            try:
                copernicusmarine.subset(
                    dataset_id=dsid,
                    variables=vars_,
                    minimum_longitude=lon_min,
                    maximum_longitude=lon_max,
                    minimum_latitude=lat_min,
                    maximum_latitude=lat_max,
                    start_datetime=tt0.isoformat(),
                    end_datetime=tt1.isoformat(),
                    username=user,
                    password=pwd,
                    output_filename=str(p),
                )
                status.setdefault("resolved_times", {})[key] = tt0.isoformat()
                return p
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"{key}: subset failed for {t0.isoformat()} (tried ±24h). Last error: {last_err}")

    def _read_nc_var(path: Path, var: str) -> np.ndarray:
        import rasterio
        with rasterio.open(f'NETCDF:"{path}":{var}') as ds:
            arr = ds.read(1).astype(np.float32)
        return arr

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
        out["sst_c"] = _read_nc_var(p, _v0("sst"))

        p = _subset_one("chl")
        out["chl_mg_m3"] = _read_nc_var(p, _v0("chl"))

        p = _subset_one("ssh")
        out["ssh_m"] = _read_nc_var(p, _v0("ssh"))

        p = _subset_one("currents")
        vars_uv = datasets_cfg["currents"]["variables"]
        if len(vars_uv) >= 2:
            u = _read_nc_var(p, vars_uv[0])
            v = _read_nc_var(p, vars_uv[1])
            out["current_m_s"] = np.sqrt(u*u + v*v).astype(np.float32)
        else:
            out["current_m_s"] = _read_nc_var(p, vars_uv[0])

        p = _subset_one("waves")
        out["waves_hs_m"] = _read_nc_var(p, _v0("waves"))

        # placeholders for QC/conf (real QC needs dataset QA flags; keep 1 for now)
        qc = np.ones_like(out["chl_mg_m3"], dtype=np.uint8)
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
    """
    Returns run_id written under out_root/runs/<run_id>.
    """
    # NOTE: We intentionally keep a *single* run folder ("main") and only append
    # per-time outputs under times/<timeId>/... to avoid overlapping/duplicate
    # files across daily runs (e.g., 13/14/15 repeated when anchoring on 11 then 12).
    now_utc, time_source = trusted_utc_now()
    anchor = now_utc.date() if date.lower() == "today" else datetime.fromisoformat(date).date()

    # Enforce product requirement: temporal resolution is 6 hours or coarser.
    step_hours = max(int(step_hours), 6)

    run_id = "main"

    W, H = [int(x) for x in grid_wh.lower().split("x")]

    bbox = bbox_from_geojson(aoi_geojson)
    grid = GridSpec(lon_min=bbox[0], lat_min=bbox[1], lon_max=bbox[2], lat_max=bbox[3], width=W, height=H)
    mask = mask_from_geojson(aoi_geojson, grid)

    # Candidate time list (ISO strings). We will de-duplicate by their time_id
    # and will also keep a compact retention window for past data.
    ts_list = timestamps_for_range(anchor_date=date, past_days=past_days, future_days=future_days, step_hours=step_hours)
    time_ids = [time_id_from_iso(iso) for iso in ts_list]
    id_by_iso = {iso: tid for iso, tid in zip(ts_list, time_ids)}

    run_root = out_root / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    # If we have an existing run, keep its time catalog and only append new times.
    prev_meta_path = run_root / "meta.json"
    prev_time_ids: List[str] = []
    if prev_meta_path.exists():
        try:
            prev = json.loads(prev_meta_path.read_text(encoding="utf-8"))
            prev_time_ids = list(prev.get("time_ids", []) or [])
        except Exception:
            prev_time_ids = []

    # run-level meta (will be rewritten at the end with a *deduped* time list)
    run_meta = {
        "run_id": run_id,
        "date": anchor.isoformat(),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "time_source": time_source,
        "times": ts_list,  # provisional; will be updated after generation
        "time_ids": time_ids,  # provisional
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "bbox": list(bbox),
        "step_hours": step_hours,
        "grid": {"width": W, "height": H, "lon_min": grid.lon_min, "lon_max": grid.lon_max, "lat_min": grid.lat_min, "lat_max": grid.lat_max},
    }
    write_json(run_root / "meta.json", run_meta)
    minify_json_for_web(run_root / "meta.json")

    # datasets config (optional)
    datasets_cfg_path = Path("backend/config/datasets.json")
    datasets_cfg = json.loads(datasets_cfg_path.read_text(encoding="utf-8")) if datasets_cfg_path.exists() else {}

    # per species outputs
    for sp, prof in species_profiles.items():
        priors = prof.get("priors", {})
        weights = prof.get("layer_weights", {})
        ops_priors = prof.get("ops_constraints", {})
        # embed current/waves priors into ops layer
        ops_priors = {**priors, **ops_priors}

        sp_root = run_root / "variants" / variant / "species" / sp
        times_root = sp_root / "times"
        times_root.mkdir(parents=True, exist_ok=True)

        # write mask (shared)
        write_bin_u8(sp_root / "mask_u8.bin", mask)

        # species meta for the web UI
        sp_meta = {
            "species": sp,
            "label": prof.get("label", {}),
            "grid": run_meta["grid"],
            "times": ts_list,  # ISO list
            "time_ids": time_ids,
            "paths": {
                "mask": f"variants/{variant}/species/{sp}/mask_u8.bin",
                "per_time": {
                    # Main outputs (UI expects these keys)
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
                    "conf": f"variants/{variant}/species/{sp}/times/{{time}}/conf_f32.bin",
                    "qc_chl": f"variants/{variant}/species/{sp}/times/{{time}}/qc_chl_u8.bin",
                  },
            },
            "model_info": {
                "habitat": {"priors": priors, "weights": weights},
                "ops": {"priors": ops_priors, "gear_depths_m": gear_depths_m},
            },
        }
        write_json(sp_root / "meta.json", sp_meta)
        minify_json_for_web(sp_root / "meta.json")

        # compute each time
        provider_status: List[Dict[str, Any]] = []
        for ts_iso in ts_list:
            tid = id_by_iso[ts_iso]

            # De-duplicate across days: if this timestamp was already generated
            # in a previous run, don't regenerate it.
            if (times_root / tid / "pcatch_f32.bin").exists():
                provider_status.append({"timestamp": ts_iso, "skipped": True, "reason": "already_exists"})
                continue

            layers, status = _try_copernicus_layers(grid, bbox, ts_iso, datasets_cfg) if datasets_cfg else (None, {"provider":"none","ok":False,"errors":["no datasets.json"]})
            if layers is None:
                layers = _synthetic_env_layers(grid, ts_iso)
                status = {**status, "fallback": "synthetic"}
            provider_status.append({"timestamp": ts_iso, **status})

            # fronts from sst/chl/ssh
            t_front = gradient_magnitude(layers["sst_c"])
            c_front = gradient_magnitude(layers["chl_mg_m3"])
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
            )
            phab, _comps = habitat_scoring(inputs, priors=priors, weights=weights)
            pops = ops_feasibility(inputs.current_m_s, inputs.waves_hs_m, ops_priors, gear_depth_m=10.0)
            pcatch = np.clip(phab * pops, 0, 1).astype(np.float32)

            m2 = np.clip(pcatch * (0.7 + 0.3 * f), 0, 1).astype(np.float32)
            ens = np.nanmean(np.stack([pcatch, m2], axis=0), axis=0).astype(np.float32)
            agree, spread = ensemble_stats([pcatch, m2])

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

            # covariates (for explainability table)
            write_bin_f32(tdir / "sst_f32.bin", inputs.sst_c.astype(np.float32))
            write_bin_f32(tdir / "chl_f32.bin", inputs.chl_mg_m3.astype(np.float32))
            write_bin_f32(tdir / "current_f32.bin", inputs.current_m_s.astype(np.float32))
            write_bin_f32(tdir / "waves_f32.bin", inputs.waves_hs_m.astype(np.float32))

            # QC + confidence
            write_bin_u8(tdir / "qc_chl_u8.bin", layers["qc_chl"])
            write_bin_f32(tdir / "conf_f32.bin", layers["conf"])

        # write provider status into species meta (append field)
        sp_meta2 = json.loads((sp_root / "meta.json").read_text(encoding="utf-8"))
        sp_meta2["provider_status"] = provider_status
        write_json(sp_root / "meta.json", sp_meta2)
        minify_json_for_web(sp_root / "meta.json")

    # -----------------------------
    # Retention + time catalog
    # -----------------------------
    # Keep only a compact window of past data (default: 2 days) to prevent
    # uncontrolled growth of docs/latest and avoid duplicated overlaps.
    anchor_dt = datetime(anchor.year, anchor.month, anchor.day, 0, 0, 0, tzinfo=timezone.utc)
    cutoff_dt = anchor_dt - timedelta(days=max(int(past_days), 0))

    sp0 = next(iter(species_profiles.keys())) if species_profiles else None
    existing_time_ids: List[str] = []
    if sp0:
        times_dir = run_root / "variants" / variant / "species" / sp0 / "times"
        if times_dir.exists():
            existing_time_ids = sorted([p.name for p in times_dir.iterdir() if p.is_dir()])

    # Delete old times across all species
    import shutil
    for tid in list(existing_time_ids):
        try:
            tdt = _dt_from_time_id(tid)
        except Exception:
            continue
        if tdt < cutoff_dt:
            for sp in species_profiles.keys():
                tpath = run_root / "variants" / variant / "species" / sp / "times" / tid
                if tpath.exists():
                    shutil.rmtree(tpath, ignore_errors=True)

    # Re-scan after deletion
    existing_time_ids = []
    if sp0:
        times_dir = run_root / "variants" / variant / "species" / sp0 / "times"
        if times_dir.exists():
            existing_time_ids = sorted([p.name for p in times_dir.iterdir() if p.is_dir()])

    # Update run-level meta.json to reflect *deduped* available times
    run_meta2 = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    run_meta2["past_days_kept"] = int(past_days)
    run_meta2["future_days_target"] = int(future_days)
    run_meta2["available_time_ids"] = existing_time_ids
    run_meta2["latest_available_time_id"] = existing_time_ids[-1] if existing_time_ids else None
    write_json(run_root / "meta.json", run_meta2)
    minify_json_for_web(run_root / "meta.json")

    # update meta_index.json
    run_entry = {
        "run_id": run_id,
        "path": f"runs/{run_id}",
        "fast": False,
        "date": anchor.isoformat(),
        "time_count": len(existing_time_ids),
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
    }
    _write_meta_index(out_root, run_entry)
    return run_id