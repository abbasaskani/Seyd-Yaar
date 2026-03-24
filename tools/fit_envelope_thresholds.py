#!/usr/bin/env python
"""
Fit ENM/envelope thresholds from presence points on a grid using one representative day's environmental layers.

Usage:
  python tools/fit_envelope_thresholds.py \
    --presence backend/data/presence/skipjack_presence.csv \
    --envdir docs/latest/variants/default/species/skipjack/times/20260223_1200Z \
    --out backend/config/envelope_fit_skipjack.json

Expected envdir files:
  sst_f32.bin, chl_f32.bin, front_f32.bin (optional), sss_f32.bin (optional), mld_f32.bin (optional), o2_f32.bin (optional)

The script computes robust percentiles (5..95) for each variable on the presence set, and writes a JSON block
that can be copied into species_profiles.json -> priors -> envelope.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np

def load_bin(path: Path, shape: tuple[int,int]) -> np.ndarray:
    a = np.fromfile(path, dtype=np.float32)
    return a.reshape(shape)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presence", required=True, help="CSV with columns lon,lat (header required)")
    ap.add_argument("--envdir", required=True, help="Directory with *_f32.bin layers for a single day")
    ap.add_argument("--shape", default="220,220", help="H,W (default 220,220)")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    H,W = [int(x) for x in args.shape.split(",")]
    envdir = Path(args.envdir)
    pres = np.genfromtxt(args.presence, delimiter=",", names=True, dtype=None, encoding="utf-8")
    lonp = np.array(pres["lon"], dtype=np.float32)
    latp = np.array(pres["lat"], dtype=np.float32)

    # infer grid bounds from meta.json if present
    meta_path = envdir.parent.parent/"meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        g = meta["grid"]
        lon_min, lon_max = float(g["lon_min"]), float(g["lon_max"])
        lat_min, lat_max = float(g["lat_min"]), float(g["lat_max"])
    else:
        raise SystemExit("meta.json not found near envdir; please provide bounds manually (edit script).")

    ix = np.clip(np.rint((lonp - lon_min) / (lon_max - lon_min + 1e-9) * (W - 1)).astype(int), 0, W - 1)
    iy = np.clip(np.rint((lat_max - latp) / (lat_max - lat_min + 1e-9) * (H - 1)).astype(int), 0, H - 1)
    idx = (iy * W + ix).astype(np.int64)

    def q(a, lo=5, hi=95):
        a = a.reshape(-1)[idx]
        a = a[np.isfinite(a)]
        if a.size == 0:
            return None
        return float(np.percentile(a, lo)), float(np.percentile(a, hi))

    out = {}

    for name, fname in [
        ("sst", "sst_f32.bin"),
        ("chl", "chl_f32.bin"),
        ("front", "front_f32.bin"),
        ("sss", "sss_f32.bin"),
        ("mld", "mld_f32.bin"),
        ("o2", "o2_f32.bin"),
    ]:
        p = envdir/fname
        if not p.exists():
            continue
        arr = load_bin(p, (H,W))
        qq = q(arr)
        if qq is None:
            continue
        out[name] = {"p05": qq[0], "p95": qq[1]}

    # Translate into envelope keys (you will still choose lo/hi strategy)
    env = {}
    if "chl" in out:
        env["chl_lo"] = out["chl"]["p05"]
        env["chl_hi"] = out["chl"]["p95"]
    if "front" in out:
        env["front_lo"] = out["front"]["p05"]
        env["front_hi"] = out["front"]["p95"]
    if "sst" in out:
        env["sst_lo"] = out["sst"]["p05"]
        env["sst_hi"] = out["sst"]["p95"]
    if "sss" in out:
        env["sss_lo"] = out["sss"]["p05"]
        env["sss_hi"] = out["sss"]["p95"]
    if "mld" in out:
        env["mld_lo"] = out["mld"]["p05"]
        env["mld_hi"] = out["mld"]["p95"]
    if "o2" in out:
        env["o2_min"] = out["o2"]["p05"]

    Path(args.out).write_text(json.dumps({"raw": out, "envelope": env}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", args.out)

if __name__ == "__main__":
    main()
