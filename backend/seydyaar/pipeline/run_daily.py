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
from typing import Dict, Any, List, Tuple, Optional
import json
import os
import math
import hashlib
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


def _try_copernicus_layers(
    grid: GridSpec,
    bbox: Tuple[float, float, float, float],
    ts_iso: str,
    datasets_cfg: Dict[str, Any],
) -> Tuple[Optional[Dict[str, np.ndarray]], Dict[str, Any]]:
    """
    Try to download required layers from Copernicus Marine using the toolbox.

    Also writes an auditable JSONL manifest to SEYDYAAR_LOG_DIR/download_manifest.jsonl.
    Each successful/failed subset attempt is written as one JSON line with:
      - key, dataset_id, variables
      - requested_time_utc, resolved_time_utc
      - output_nc, bytes, sha256
      - ok, error (if any)
    """
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

    # Manifest (auditable trail)
    log_dir = Path(os.getenv("SEYDYAAR_LOG_DIR", "docs/latest/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = log_dir / "download_manifest.jsonl"

    # Optional verification snapshot (keep a single timestamp's NetCDFs under docs/latest/verify/<time_id>/)
    verify_tid = os.getenv("SEYDYAAR_VERIFY_TIME_ID", "").strip()
    verify_dir = Path(os.getenv("SEYDYAAR_VERIFY_DIR", "docs/latest/verify"))
    if verify_tid:
        (verify_dir / verify_tid).mkdir(parents=True, exist_ok=True)

    lon_min, lat_min, lon_max, lat_max = bbox
    t0 = dtparser.isoparse(ts_iso).astimezone(tz.UTC)
    tid = time_id_from_iso(ts_iso)

    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _append_manifest(rec: Dict[str, Any]) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _subset_one(key: str) -> Path:
        cfg = datasets_cfg[key]
        dsid = cfg["dataset_id"]

        vars_ = cfg.get("variables", None)
        if not vars_:
            v = cfg.get("variable", None)
            vars_ = [v] if v else []
        if not vars_:
            raise RuntimeError(f"{key}: variables list is empty in datasets.json")

        # Time handling:
        # - For daily datasets (P1D), force request at 00:00Z to avoid out-of-range intra-day timestamps.
        tt_base = t0
        if key == "chl":
            tt_base = datetime(tt_base.year, tt_base.month, tt_base.day, 0, 0, 0, tzinfo=timezone.utc)
            offsets = [0, -24, -48, -72]  # don't try future for daily chl
        else:
            offsets = [0, -6, -12, -18, -24, 6, 12, 18, 24]

        last_err: Optional[Exception] = None

        for off in offsets:
            tt0 = tt_base + timedelta(hours=off)
            tt1 = tt0

            p = tmpdir / f"{key}_{tt0.strftime('%Y%m%dT%H%M%S')}.nc"
            rec: Dict[str, Any] = {
                "time_id": tid,
                "key": key,
                "dataset_id": dsid,
                "variables": vars_,
                "requested_time_utc": t0.isoformat(),
                "attempt_time_utc": tt0.isoformat(),
                "bbox": [lon_min, lat_min, lon_max, lat_max],
                "min_depth": cfg.get("depth_m", None),
                "max_depth": cfg.get("depth_m", None),
            }

            try:
                copernicusmarine.subset(
                    dataset_id=dsid,
                    variables=vars_,
                    minimum_longitude=lon_min,
                    maximum_longitude=lon_max,
                    minimum_latitude=lat_min,
                    maximum_latitude=lat_max,
                    minimum_depth=cfg.get("depth_m", None),
                    maximum_depth=cfg.get("depth_m", None),
                    coordinates_selection_method="nearest",
                    start_datetime=tt0.isoformat(),
                    end_datetime=tt1.isoformat(),
                    username=user,
                    password=pwd,
                    output_filename=str(p),
                )

                # Record + copy for verification snapshot
                size = p.stat().st_size if p.exists() else 0
                rec.update({
                    "ok": True,
                    "resolved_time_utc": tt0.isoformat(),
                    "output_nc": str(p),
                    "bytes": int(size),
                    "sha256": _sha256(p) if p.exists() else None,
                })
                _append_manifest(rec)

                status.setdefault("resolved_times", {})[key] = tt0.isoformat()

                if verify_tid and tid == verify_tid and p.exists():
                    dst = (verify_dir / verify_tid / f"{key}.nc")
                    try:
                        dst.write_bytes(p.read_bytes())
                    except Exception:
                        # best-effort; manifest still records output_nc
                        pass

                return p

            except Exception as e:
                last_err = e
                rec.update({"ok": False, "error": str(e)})
                _append_manifest(rec)
                continue

        raise RuntimeError(f"{key}: subset failed for {t0.isoformat()} (tried offsets). Last error: {last_err}")

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

    run_meta = {
        "run_id": run_id,
        "date": anchor.isoformat(),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "time_source": time_source,
        "times": ts_list,
        "time_ids": time_ids,
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

    # Verification snapshot: keep NetCDFs for anchor date 00:00Z under docs/latest/verify/<time_id>/
    try:
        verify_iso = f"{anchor.isoformat()}T00:00:00+00:00"
        os.environ.setdefault("SEYDYAAR_VERIFY_TIME_ID", time_id_from_iso(verify_iso))
    except Exception:
        pass

    strict = os.getenv("SEYDYAAR_STRICT_COPERNICUS", "0") == "1"

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
                    if strict:
                        raise RuntimeError("Copernicus download failed (strict mode). Errors: " + "; ".join(status.get("errors", [])))
                    layers = _synthetic_env_layers(grid, ts_iso)
                    status = {**status, "fallback": "synthetic"}
                layers_cache[tid] = (layers, status)

            provider_status.append({"timestamp": ts_iso, **status})

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

    run_entry = {
        "run_id": run_id,
        "path": f"runs/{run_id}",
        "fast": False,
        "date": anchor.isoformat(),
        "time_count": len(time_ids),
        "variants": [variant],
        "species": list(species_profiles.keys()),
        "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
    }
    _write_meta_index(out_root, run_entry)
    _write_latest_index_and_meta(out_root, run_entry, variant)
    return run_id
