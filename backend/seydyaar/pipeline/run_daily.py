from __future__ import annotations

"""
Seyd-Yaar daily pipeline (STRICT + transparent download logging)

What this version guarantees:
1) STRICT mode: if Copernicus fails for ANY layer -> raise error (NO synthetic fallback).
2) Full download transparency: logs dataset_id, variables, requested/resolved time,
   bbox, output .nc path, bytes + sha256, units, and unit conversion applied.
3) Keeps the same docs/latest output layout expected by your web UI.

Env vars:
- SEYDYAAR_STRICT_COPERNICUS=1   -> fail hard if any Copernicus layer fails
- SEYDYAAR_TMPDIR=.seydyaar_tmp  -> where to store downloaded .nc files (relative to cwd)
- SEYDYAAR_LOG_DIR=docs/latest/logs -> where to store manifest logs (relative to cwd)
- SEYDYAAR_FORCE_REGEN=1         -> rebuild even if outputs exist
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import json
import os
import math
import hashlib

import numpy as np
from dateutil import parser as dtparser
from dateutil import tz

from ..utils_geo import bbox_from_geojson, GridSpec, mask_from_geojson
from ..utils_time import trusted_utc_now, timestamps_for_range, time_id_from_iso
from ..models.scoring import HabitatInputs, habitat_scoring, gradient_magnitude, front_score
from ..models.ops import ops_feasibility
from ..models.ensemble import ensemble_stats
from .io import write_bin_f32, write_bin_u8, write_json, minify_json_for_web


STRICT_COPERNICUS = os.getenv("SEYDYAAR_STRICT_COPERNICUS", "0") == "1"
SEYDYAAR_TMPDIR = os.getenv("SEYDYAAR_TMPDIR", ".seydyaar_tmp")
SEYDYAAR_LOG_DIR = os.getenv("SEYDYAAR_LOG_DIR", "docs/latest/logs")
FORCE_REGEN = os.getenv("SEYDYAAR_FORCE_REGEN", "0") == "1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_from_ts(ts_iso: str) -> int:
    h = 2166136261
    for ch in ts_iso.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _get_copernicus_creds() -> Tuple[str, str]:
    user = (os.getenv("COPERNICUS_MARINE_USERNAME", "") or os.getenv("COPERNICUSMARINE_SERVICE_USERNAME", "")).strip()
    pwd = (os.getenv("COPERNICUS_MARINE_PASSWORD", "") or os.getenv("COPERNICUSMARINE_SERVICE_PASSWORD", "")).strip()
    return user, pwd


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _synthetic_env_layers(grid: GridSpec, ts_iso: str) -> Dict[str, np.ndarray]:
    """Development-only. STRICT mode must never allow reaching this."""
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

    qc_chl = (rng.random(size=chl.shape) > 0.07).astype(np.uint8)
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


def _apply_conversion(arr: np.ndarray, convert: Optional[str]) -> Tuple[np.ndarray, Optional[str]]:
    if not convert:
        return arr, None
    if convert == "K_TO_C":
        return (arr - 273.15).astype(np.float32), "K_TO_C (value - 273.15)"
    return arr, f"unknown_convert:{convert}"


def _try_copernicus_layers(
    grid: GridSpec,
    bbox: Tuple[float, float, float, float],
    ts_iso: str,
    datasets_cfg: Dict[str, Any],
) -> Tuple[Dict[str, np.ndarray], Dict[str, Any]]:
    """
    Returns:
      layers dict (sst_c, chl_mg_m3, ssh_m, current_m_s, waves_hs_m, qc_chl, conf)
      status dict: provider/ok/errors/downloads/resolved_times
    """
    # Allow config shape: {"cmems": {...}} or direct
    if isinstance(datasets_cfg, dict) and "cmems" in datasets_cfg and isinstance(datasets_cfg["cmems"], dict):
        datasets_cfg = datasets_cfg["cmems"]

    user, pwd = _get_copernicus_creds()
    status: Dict[str, Any] = {"provider": "copernicusmarine", "ok": False, "errors": [], "downloads": []}

    if not (user and pwd):
        raise RuntimeError("Missing Copernicus credentials: set COPERNICUSMARINE_SERVICE_* (or COPERNICUS_MARINE_*)")

    try:
        import copernicusmarine  # type: ignore
    except Exception as e:
        raise RuntimeError(f"copernicusmarine import failed: {e}")

    for k in ["sst", "chl", "ssh", "currents", "waves"]:
        if not str(datasets_cfg.get(k, {}).get("dataset_id", "")).strip():
            raise RuntimeError(f"datasets.json missing dataset_id for '{k}'")

    tmpdir = Path(SEYDYAAR_TMPDIR)
    tmpdir.mkdir(exist_ok=True)

    lon_min, lat_min, lon_max, lat_max = bbox
    t0 = dtparser.isoparse(ts_iso).astimezone(tz.UTC)

    def _subset_one(key: str) -> Path:
        cfg = datasets_cfg[key]
        dsid = cfg["dataset_id"]

        vars_ = cfg.get("variables") or ([cfg.get("variable")] if cfg.get("variable") else [])
        if not vars_:
            raise RuntimeError(f"{key}: empty variables list in datasets.json")

        offsets_h = [0, -6, -12, -18, -24, 6, 12, 18, 24]
        last_err: Optional[Exception] = None

        for off in offsets_h:
            tt0 = t0 + timedelta(hours=off)
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
                    end_datetime=tt0.isoformat(),
                    username=user,
                    password=pwd,
                    output_filename=str(p),
                    overwrite=True,
                )

                # record file facts
                size = p.stat().st_size
                sha = _sha256_file(p)
                status["downloads"].append({
                    "layer_key": key,
                    "dataset_id": dsid,
                    "variables": vars_,
                    "requested_time": t0.isoformat(),
                    "resolved_time": tt0.isoformat(),
                    "bbox": [lon_min, lat_min, lon_max, lat_max],
                    "output_nc": str(p),
                    "bytes": size,
                    "sha256": sha,
                    "units": cfg.get("units"),
                    "convert": cfg.get("convert"),
                })
                status.setdefault("resolved_times", {})[key] = tt0.isoformat()
                return p
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(f"{key}: subset failed for {t0.isoformat()} (tried ±24h). Last error: {last_err}")

    def _read_nc_var(path: Path, var: str) -> np.ndarray:
        import rasterio
        with rasterio.open(f'NETCDF:"{path}":{var}') as ds:
            return ds.read(1).astype(np.float32)

    out: Dict[str, np.ndarray] = {}

    # SST
    p = _subset_one("sst")
    sst = _read_nc_var(p, datasets_cfg["sst"]["variable"])
    sst, note = _apply_conversion(sst, datasets_cfg["sst"].get("convert"))
    if note:
        status["downloads"][-1]["convert_applied"] = note
    out["sst_c"] = sst

    # CHL
    p = _subset_one("chl")
    chl = _read_nc_var(p, datasets_cfg["chl"]["variable"])
    chl, note = _apply_conversion(chl, datasets_cfg["chl"].get("convert"))
    if note:
        status["downloads"][-1]["convert_applied"] = note
    out["chl_mg_m3"] = chl

    # SSH (SLA proxy allowed)
    p = _subset_one("ssh")
    ssh = _read_nc_var(p, datasets_cfg["ssh"]["variable"])
    ssh, note = _apply_conversion(ssh, datasets_cfg["ssh"].get("convert"))
    if note:
        status["downloads"][-1]["convert_applied"] = note
    out["ssh_m"] = ssh

    # currents (uo/vo)
    p = _subset_one("currents")
    u = _read_nc_var(p, datasets_cfg["currents"]["variables"][0])
    v = _read_nc_var(p, datasets_cfg["currents"]["variables"][1])
    out["current_m_s"] = np.sqrt(u*u + v*v).astype(np.float32)

    # waves
    p = _subset_one("waves")
    waves = _read_nc_var(p, datasets_cfg["waves"]["variable"])
    waves, note = _apply_conversion(waves, datasets_cfg["waves"].get("convert"))
    if note:
        status["downloads"][-1]["convert_applied"] = note
    out["waves_hs_m"] = waves

    qc = np.ones_like(out["chl_mg_m3"], dtype=np.uint8)
    conf = qc.astype(np.float32)
    out["qc_chl"] = qc
    out["conf"] = conf

    status["ok"] = True
    return out, status


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
        "time_count": len(time_ids),
        "available_time_ids": time_ids,
        "latest_available_time_id": latest_tid,
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

    datasets_cfg_path = Path("backend/config/datasets.json")
    datasets_cfg = json.loads(datasets_cfg_path.read_text(encoding="utf-8"))

    # Manifest file committed under docs/latest/logs
    log_dir = Path(SEYDYAAR_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    manifest_jsonl = log_dir / "download_manifest.jsonl"

    # Cache per time_id (shared between species)
    layers_cache: Dict[str, Tuple[Dict[str, np.ndarray], Dict[str, Any]]] = {}

    for sp, prof in species_profiles.items():
        priors = prof.get("priors", {})
        weights = prof.get("layer_weights", {})
        ops_priors = {**priors, **prof.get("ops_constraints", {})}

        sp_root = run_root / "variants" / variant / "species" / sp
        times_root = sp_root / "times"
        times_root.mkdir(parents=True, exist_ok=True)

        write_bin_u8(sp_root / "mask_u8.bin", mask)

        sp_meta = {
            "species": sp,
            "label": prof.get("label", {}),
            "grid": {"width": W, "height": H, "lon_min": grid.lon_min, "lon_max": grid.lon_max, "lat_min": grid.lat_min, "lat_max": grid.lat_max},
            "times": ts_list,
            "time_ids": time_ids,
            "download_manifest": str((Path(SEYDYAAR_LOG_DIR) / "download_manifest.jsonl").as_posix()),
        }
        write_json(sp_root / "meta.json", sp_meta)
        minify_json_for_web(sp_root / "meta.json")

        provider_status: List[Dict[str, Any]] = []

        for ts_iso in ts_list:
            tid = id_by_iso[ts_iso]

            if (not FORCE_REGEN) and (times_root / tid / "pcatch_ensemble_f32.bin").exists():
                provider_status.append({"timestamp": ts_iso, "skipped": True, "reason": "already_exists"})
                continue

            if tid in layers_cache:
                layers, status = layers_cache[tid]
            else:
                # STRICT download: if fails -> raises (NO synthetic)
                layers, status = _try_copernicus_layers(grid, bbox, ts_iso, datasets_cfg)
                layers_cache[tid] = (layers, status)

                # Write one manifest record per time_id (shared by both species)
                _append_jsonl(
                    manifest_jsonl,
                    {
                        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
                        "time_id": tid,
                        "timestamp": ts_iso,
                        "bbox": list(bbox),
                        "grid": {"width": W, "height": H},
                        "tmpdir": str(Path(SEYDYAAR_TMPDIR).resolve()),
                        "status": status,
                    },
                )

            provider_status.append({"timestamp": ts_iso, **status})

            # Fronts
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
            phab, _ = habitat_scoring(inputs, priors=priors, weights=weights)
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

            # covariates
            write_bin_f32(tdir / "sst_f32.bin", inputs.sst_c.astype(np.float32))
            write_bin_f32(tdir / "chl_f32.bin", inputs.chl_mg_m3.astype(np.float32))
            write_bin_f32(tdir / "current_f32.bin", inputs.current_m_s.astype(np.float32))
            write_bin_f32(tdir / "waves_f32.bin", inputs.waves_hs_m.astype(np.float32))

            write_bin_u8(tdir / "qc_chl_u8.bin", layers["qc_chl"])
            write_bin_f32(tdir / "conf_f32.bin", layers["conf"])

        sp_meta2 = json.loads((sp_root / "meta.json").read_text(encoding="utf-8"))
        sp_meta2["provider_status"] = provider_status
        write_json(sp_root / "meta.json", sp_meta2)
        minify_json_for_web(sp_root / "meta.json")

    run_meta = {
        "run_id": run_id,
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "time_source": time_source,
        "times": ts_list,
        "time_ids": time_ids,
        "available_time_ids": time_ids,
        "latest_available_time_id": time_ids[-1] if time_ids else None,
        "bbox": list(bbox),
        "grid": {"width": W, "height": H, "lon_min": grid.lon_min, "lon_max": grid.lon_max, "lat_min": grid.lat_min, "lat_max": grid.lat_max},
        "strict_copernicus": True,
        "tmpdir": str(Path(SEYDYAAR_TMPDIR).resolve()),
        "download_manifest": str((Path(SEYDYAAR_LOG_DIR) / "download_manifest.jsonl").as_posix()),
    }
    write_json(run_root / "meta.json", run_meta)
    minify_json_for_web(run_root / "meta.json")

    run_entry = {
        "run_id": run_id,
        "path": f"runs/{run_id}",
        "date": anchor.isoformat(),
        "time_count": len(time_ids),
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
    }
    _write_meta_index(out_root, run_entry)
    _write_latest_index_and_meta(out_root, run_entry, variant)
    return run_id
