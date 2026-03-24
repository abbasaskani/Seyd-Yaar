from __future__ import annotations
import numpy as np


def ops_feasibility(current_m_s: np.ndarray, waves_hs_m: np.ndarray, priors: dict, gear_depth_m: float = 10.0) -> np.ndarray:
    current = np.asarray(current_m_s, dtype=np.float32)
    waves = np.asarray(waves_hs_m, dtype=np.float32)
    c_opt = float(priors.get("current_opt_m_s", 0.25))
    c_sig = max(float(priors.get("current_sigma_m_s", 0.20)), 1e-6)
    hs_soft = float(priors.get("waves_hs_soft_max_m", 1.5))
    hs_k = max(float(priors.get("waves_softness", 0.35)), 1e-6)
    s_current = np.exp(-0.5 * ((current - c_opt) / c_sig) ** 2)
    z = np.clip((waves - hs_soft) / hs_k, -60.0, 60.0)
    s_waves = 1.0 / (1.0 + np.exp(z))
    depth_factor = np.clip(1.0 - float(gear_depth_m) / 30.0, 0.4, 1.0)
    out = (0.55 * s_waves + 0.45 * s_current) * depth_factor
    return np.clip(out, 0.0, 1.0).astype(np.float32)
