"""Presence proxy builders for MaxEnt/PPP.

Three-tier strategy (preferred order):
1) AIS effort (Global Fishing Watch) if token/dataset available and permitted.
2) Weak labels from HSI (pseudo-presence from high suitability).
3) Manual CSV upload (optional).

Offline-friendly: if AIS isn't available or fails, we fall back to weak labels with a
synthetic "effort" surface (explicitly marked in audit).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .gfw_effort import GFWConfig, fetch_effort_proxy_image, load_cfg_from_env, rasterize_effort_to_grid


@dataclass
class PresenceProxyResult:
    mode: str
    points_lonlat: List[Tuple[float, float]]
    effort_surface: Optional[np.ndarray] = None
    audit: Dict[str, Any] = None


def _rng(seed: int | None) -> np.random.Generator:
    return np.random.default_rng(seed if seed is not None else 12345)


def _cell_centers(grid_lon: np.ndarray, grid_lat: np.ndarray) -> np.ndarray:
    """Return Nx2 array of lon/lat for each cell center (row-major).

    Accepts either:
      - 2D lon/lat grids (H,W) of cell centers, or
      - 1D lon and 1D lat arrays (then meshgrid is used).
    """
    if grid_lon.ndim == 2 and grid_lat.ndim == 2:
        return np.stack([grid_lon.ravel(), grid_lat.ravel()], axis=1)
    lon2d, lat2d = np.meshgrid(grid_lon, grid_lat)
    return np.stack([lon2d.ravel(), lat2d.ravel()], axis=1)


def _synthetic_effort_surface(
    *,
    habitat_like: np.ndarray,
    mask_u8: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Demo-only effort: smooth-ish random field with bias towards high habitat."""
    noise = rng.standard_normal(habitat_like.shape).astype(np.float32)
    for _ in range(3):
        noise = (
            noise
            + np.roll(noise, 1, 0)
            + np.roll(noise, -1, 0)
            + np.roll(noise, 1, 1)
            + np.roll(noise, -1, 1)
        ) / 5.0
    noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-9)

    hab = habitat_like.astype(np.float32)
    hab = (hab - hab.min()) / (hab.max() - hab.min() + 1e-9)

    eff = 0.6 * hab + 0.4 * noise
    eff = np.where(mask_u8 > 0, eff, 0.0)
    return eff.astype(np.float32)


def _sample_points_from_surface(
    *,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    prob_surface: np.ndarray,
    mask_u8: np.ndarray,
    n_points: int,
    rng: np.random.Generator,
) -> List[Tuple[float, float]]:
    pts = _cell_centers(grid_lon, grid_lat)
    p = prob_surface.ravel().astype(np.float64)
    m = mask_u8.ravel() > 0
    p = np.where(m, p, 0.0)
    s = float(p.sum())
    if s <= 0:
        idx = np.flatnonzero(m)
        choose = rng.choice(idx, size=min(n_points, idx.size), replace=False)
        return [tuple(pts[i]) for i in choose]

    p = p / s
    choose = rng.choice(np.arange(p.size), size=min(n_points, p.size), replace=False, p=p)
    return [tuple(pts[i]) for i in choose]


def _presence_from_csv(*, csv_path: Path, species: str, time_id: str) -> List[Tuple[float, float]]:
    """Read user-provided presences.

    CSV columns supported:
      - lat, lon (required)
      - species (optional)
      - time (optional; matched by prefix against time_id)
    """
    out: List[Tuple[float, float]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    has_species = any(r.get("species") for r in rows)
    has_time = any(r.get("time") for r in rows)

    for r in rows:
        if has_species and r.get("species") and r.get("species").strip().lower() != species.lower():
            continue
        if has_time and r.get("time") and time_id:
            t = r.get("time").strip()
            if not time_id.startswith(t) and not t.startswith(time_id):
                continue
        try:
            lon = float(r["lon"])
            lat = float(r["lat"])
        except Exception:
            continue
        out.append((lon, lat))
    return out


def build_presence_proxy(
    *,
    mode: str,
    date_ymd: str,
    time_id: str,
    bbox: Tuple[float, float, float, float],
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    mask_u8: np.ndarray,
    habitat_like: np.ndarray,
    n_points: int = 800,
    seed: int | None = None,
    csv_path: Optional[Path] = None,
    gfw_cfg: Optional[GFWConfig] = None,
) -> PresenceProxyResult:
    """Build presence proxy points + optional effort surface.

    - mode: 'auto'|'ais'|'weak'|'csv'
    """
    rng = _rng(seed)
    audit: Dict[str, Any] = {"requested_mode": mode}

    # 1) CSV
    if mode == "csv":
        if not csv_path or not csv_path.exists():
            raise FileNotFoundError("CSV mode requested but csv_path missing")
        pts = _presence_from_csv(csv_path=csv_path, species="", time_id=time_id)
        audit["presence_proxy"] = {"mode": "csv", "path": str(csv_path), "n": len(pts)}
        return PresenceProxyResult(mode="csv", points_lonlat=pts, effort_surface=None, audit=audit)

    # 2) AIS effort
    if mode in ("auto", "ais"):
        if gfw_cfg is None:
            gfw_cfg = load_cfg_from_env()
        if gfw_cfg is not None:
            try:
                proxy_img, meta = fetch_effort_proxy_image(cfg=gfw_cfg, bbox=bbox, date_ymd=date_ymd)
                effort = rasterize_effort_to_grid(proxy_img, img_meta=meta, grid_lon=grid_lon, grid_lat=grid_lat, bbox=bbox)
                pts = _sample_points_from_surface(
                    grid_lon=grid_lon,
                    grid_lat=grid_lat,
                    prob_surface=effort,
                    mask_u8=mask_u8,
                    n_points=n_points,
                    rng=rng,
                )
                audit["presence_proxy"] = {"mode": "ais", "provider": "gfw", "zoom": gfw_cfg.zoom, "date": date_ymd}
                return PresenceProxyResult(mode="ais", points_lonlat=pts, effort_surface=effort.astype(np.float32), audit=audit)
            except Exception as e:
                audit["presence_proxy_ais_error"] = str(e)
                if mode == "ais":
                    raise

    # 3) Weak labels (fallback)
    eff = _synthetic_effort_surface(habitat_like=habitat_like, mask_u8=mask_u8, rng=rng)
    pts = _sample_points_from_surface(
        grid_lon=grid_lon,
        grid_lat=grid_lat,
        prob_surface=np.clip(habitat_like, 0, 1) * (0.3 + 0.7 * eff),
        mask_u8=mask_u8,
        n_points=n_points,
        rng=rng,
    )
    audit["presence_proxy"] = {"mode": "weak", "method": "HSI-high-quantile", "synthetic_effort_surface": True}
    return PresenceProxyResult(mode="weak", points_lonlat=pts, effort_surface=eff, audit=audit)


def build_presence_proxy_details(
    *,
    mode: str,
    grid_lon: np.ndarray,
    grid_lat: np.ndarray,
    bbox: Tuple[float, float, float, float],
    species: str,
    presence_csv_path: Optional[str] = None,
    n_presence: int = 1500,
    seed: int = 7,
) -> Tuple[np.ndarray, dict, Optional[np.ndarray]]:
    """Return (presence_idx, audit, bias_surface).

    - presence_idx: flat indices into the (H*W) grid
    - audit: describes the chosen proxy and any fallbacks/errors
    - bias_surface: optional (H,W) surface in 0..1 for bias correction (or None)
    """
    audit: dict = {"requested_mode": mode, "species": species}
    rng = np.random.default_rng(seed)

    # Normalize to 2D lon/lat grids
    if grid_lon.ndim == 2 and grid_lat.ndim == 2:
        H, W = grid_lon.shape
    else:
        H, W = int(grid_lat.size), int(grid_lon.size)
        grid_lon, grid_lat = np.meshgrid(grid_lon, grid_lat)

    # 1) CSV
    if mode == "csv":
        if not presence_csv_path:
            raise ValueError("presence_csv_path is required when mode='csv'")
        pts = _presence_from_csv(csv_path=Path(presence_csv_path), species=species, time_id="")
        lon_min, lat_min, lon_max, lat_max = bbox
        idx_list: list[int] = []
        for lon, lat in pts:
            x = int(round((lon - lon_min) / max(lon_max - lon_min, 1e-9) * (W - 1)))
            y = int(round((lat_max - lat) / max(lat_max - lat_min, 1e-9) * (H - 1)))
            x = max(0, min(W - 1, x))
            y = max(0, min(H - 1, y))
            idx_list.append(y * W + x)
        idx = np.array(idx_list, dtype=np.int64)
        audit.update({"mode_used": "csv", "presence_points": int(idx.size), "csv": {"path": str(presence_csv_path)}})
        return idx, audit, None

    # 2) AIS effort
    if mode in ("auto", "ais"):
        cfg = load_cfg_from_env(zoom=4)
        if cfg is not None:
            try:
                proxy_img, meta = fetch_effort_proxy_image(cfg=cfg, bbox=bbox, date_ymd=None)
                effort = rasterize_effort_to_grid(proxy_img, img_meta=meta, grid_lon=grid_lon, grid_lat=grid_lat, bbox=bbox)
                w = np.clip(effort, 0.0, 1.0)
                flat = w.ravel().astype(np.float64)
                s = float(flat.sum())
                if s > 0:
                    p = flat / s
                    idx = rng.choice(np.arange(flat.size), size=min(n_presence, flat.size), replace=True, p=p).astype(np.int64)
                    audit.update({"mode_used": "ais", "presence_points": int(idx.size), "ais": {"zoom": cfg.zoom, "date_range": cfg.date_range}})
                    return idx, audit, w.astype(np.float32)
                audit.update({"ais": "empty_surface"})
            except Exception as e:
                audit.update({"ais_error": str(e)})
                if mode == "ais":
                    raise
        else:
            audit.update({"ais": "no_token"})
        if mode == "ais":
            audit.update({"fallback": "weak"})

    # 3) Weak fallback (simple habitat-like gaussian + synthetic effort)
    lon_min, lat_min, lon_max, lat_max = bbox
    lon0 = 0.5 * (lon_min + lon_max)
    lat0 = 0.5 * (lat_min + lat_max)
    sx = max((lon_max - lon_min) * 0.25, 1e-6)
    sy = max((lat_max - lat_min) * 0.25, 1e-6)
    hab = np.exp(-(((grid_lon - lon0) / sx) ** 2 + ((grid_lat - lat0) / sy) ** 2)).astype(np.float32)

    mask_u8 = np.ones((H, W), dtype=np.uint8)
    eff = _synthetic_effort_surface(habitat_like=hab, mask_u8=mask_u8, rng=rng)
    prob = np.clip(hab, 0, 1) * (0.3 + 0.7 * eff)
    flat = prob.ravel().astype(np.float64)
    s = float(flat.sum())
    if s <= 0:
        idx = rng.choice(np.arange(flat.size), size=min(n_presence, flat.size), replace=False).astype(np.int64)
    else:
        p = flat / s
        idx = rng.choice(np.arange(flat.size), size=min(n_presence, flat.size), replace=True, p=p).astype(np.int64)

    audit.update({"mode_used": "weak", "presence_points": int(idx.size), "synthetic_effort_surface": True})
    return idx, audit, eff.astype(np.float32)
