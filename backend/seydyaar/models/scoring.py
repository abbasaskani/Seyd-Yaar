from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple
import numpy as np


def _gauss(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    sigma = max(float(sigma), 1e-6)
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def _logistic(x: np.ndarray, x0: float, k: float) -> np.ndarray:
    k = max(float(k), 1e-6)
    return 1.0 / (1.0 + np.exp(-(x - x0) / k))


def _robust01(arr: np.ndarray, p_lo: float = 5.0, p_hi: float = 95.0) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    lo, hi = np.nanpercentile(a, [p_lo, p_hi])
    if not np.isfinite(lo):
        lo = np.nanmin(a)
    if not np.isfinite(hi):
        hi = np.nanmax(a)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.zeros_like(a, dtype=np.float32)
    out = (a - lo) / (hi - lo + 1e-9)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _mean_filter3(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    p = np.pad(a, 1, mode='edge')
    out = np.zeros_like(a, dtype=np.float32)
    for dy in range(3):
        for dx in range(3):
            out += p[dy:dy+a.shape[0], dx:dx+a.shape[1]]
    return out / 9.0


def _median_filter3(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    p = np.pad(a, 1, mode='edge')
    stack = []
    for dy in range(3):
        for dx in range(3):
            stack.append(p[dy:dy+a.shape[0], dx:dx+a.shape[1]])
    return np.median(np.stack(stack, axis=0), axis=0).astype(np.float32)


def score_temp_c(sst_c: np.ndarray, opt_c: float, sigma_c: float) -> np.ndarray:
    return _gauss(sst_c, opt_c, sigma_c)


def score_chl_mg_m3(chl: np.ndarray, opt_mg_m3: float, sigma_log10: float) -> np.ndarray:
    chl = np.clip(chl, 1e-6, None)
    return _gauss(np.log10(chl), np.log10(opt_mg_m3), sigma_log10)


def score_current_m_s(spd: np.ndarray, opt_m_s: float, sigma_m_s: float) -> np.ndarray:
    return _gauss(spd, opt_m_s, sigma_m_s)


def score_salinity_psu(sss: np.ndarray, opt_psu: float, sigma_psu: float) -> np.ndarray:
    return _gauss(sss, opt_psu, sigma_psu)


def score_o2_umol_l(o2: np.ndarray, opt_umol_l: float, sigma_umol_l: float) -> np.ndarray:
    return _gauss(o2, opt_umol_l, sigma_umol_l)


def score_waves_hs(hs_m: np.ndarray, soft_max_m: float = 1.5, softness: float = 0.35) -> np.ndarray:
    return 1.0 / (1.0 + np.exp((hs_m - soft_max_m) / max(softness, 1e-6)))


def gradient_magnitude(arr: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(arr.astype(np.float32))
    return np.sqrt(gx * gx + gy * gy).astype(np.float32)


def _front_from_field(arr: np.ndarray, method: str = 'boa') -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    method = (method or 'boa').lower()
    if method == 'gradient':
        return _robust01(gradient_magnitude(a))
    if method == 'boa':
        med = _median_filter3(a)
        hi = a - med
        return _robust01(gradient_magnitude(hi))
    if method == 'cca':
        local_mean = _mean_filter3(a)
        local_dev = np.abs(a - local_mean)
        local_grad = gradient_magnitude(local_mean)
        return _robust01(0.65 * local_dev + 0.35 * local_grad)
    if method == 'gradhist':
        g1 = gradient_magnitude(a)
        g2 = gradient_magnitude(_mean_filter3(a))
        return _robust01(np.maximum(g1, g2))
    return _robust01(gradient_magnitude(a))


def front_score(temp_front: np.ndarray, chl_front: np.ndarray, ssh_front: np.ndarray,
                w_temp: float = 0.5, w_chl: float = 0.25, w_ssh: float = 0.25) -> np.ndarray:
    s = w_temp * temp_front + w_chl * chl_front + w_ssh * ssh_front
    return _robust01(s)


def front_feature_stack(sst_c: np.ndarray, chl_mg_m3: np.ndarray, ssh_m: np.ndarray, priors: Dict) -> Dict[str, np.ndarray]:
    """Fast front stack.

    Runtime is intentionally limited to the two most useful lightweight detectors:
    gradient + BOA-like contextual median filtering. Heavier alternatives remain
    represented through aliases so the UI and metadata schema do not break.
    """
    fw = priors.get('front_weights', {'temp':0.5,'chl':0.25,'ssh':0.25})

    tf_grad = _front_from_field(sst_c, "gradient")
    cf_grad = _front_from_field(np.log10(np.clip(chl_mg_m3, 1e-6, None)), "gradient")
    sf_grad = _front_from_field(ssh_m, "gradient")
    front_gradient = front_score(tf_grad, cf_grad, sf_grad, fw.get('temp',0.5), fw.get('chl',0.25), fw.get('ssh',0.25))

    tf_boa = _front_from_field(sst_c, "boa")
    cf_boa = _front_from_field(np.log10(np.clip(chl_mg_m3, 1e-6, None)), "boa")
    sf_boa = _front_from_field(ssh_m, "boa")
    front_boa = front_score(tf_boa, cf_boa, sf_boa, fw.get('temp',0.5), fw.get('chl',0.25), fw.get('ssh',0.25))

    front_fused = _robust01(0.65 * front_boa + 0.35 * front_gradient)

    return {
        "temp_front_gradient": tf_grad.astype(np.float32),
        "chl_front_gradient": cf_grad.astype(np.float32),
        "ssh_front_gradient": sf_grad.astype(np.float32),
        "front_gradient": front_gradient.astype(np.float32),
        "temp_front_boa": tf_boa.astype(np.float32),
        "chl_front_boa": cf_boa.astype(np.float32),
        "ssh_front_boa": sf_boa.astype(np.float32),
        "front_boa": front_boa.astype(np.float32),
        # Lightweight aliases: keep schema/UI stable without running extra detectors.
        "front_cca": front_boa.astype(np.float32),
        "front_gradhist": front_gradient.astype(np.float32),
        "front_fused": front_fused.astype(np.float32),
    }


def thermocline_proxy(mld_m: np.ndarray | None, sst_c: np.ndarray) -> np.ndarray | None:
    if mld_m is None:
        return None
    mld_term = 1.0 - _robust01(np.clip(mld_m, 0.0, 200.0), 5.0, 95.0)
    temp_term = _robust01(np.abs(sst_c - np.nanmedian(sst_c)))
    return np.clip(0.7 * mld_term + 0.3 * temp_term, 0.0, 1.0).astype(np.float32)


def oxygen_access_score(o2_umol_l: np.ndarray | None, mld_m: np.ndarray | None) -> np.ndarray | None:
    if o2_umol_l is None:
        return None
    s_o2 = _logistic(np.asarray(o2_umol_l, dtype=np.float32), 170.0, 18.0)
    if mld_m is None:
        return np.clip(s_o2, 0.0, 1.0).astype(np.float32)
    s_mld = 1.0 - _robust01(np.clip(mld_m, 0.0, 200.0), 5.0, 95.0)
    return np.clip(0.7 * s_o2 + 0.3 * s_mld, 0.0, 1.0).astype(np.float32)


def npp_score(npp: np.ndarray | None) -> np.ndarray | None:
    if npp is None:
        return None
    return _robust01(np.asarray(npp, dtype=np.float32), 10.0, 95.0)


@dataclass
class HabitatInputs:
    sst_c: np.ndarray
    chl_mg_m3: np.ndarray
    current_m_s: np.ndarray
    waves_hs_m: np.ndarray
    ssh_m: np.ndarray
    sss_psu: np.ndarray | None = None
    o2_umol_l: np.ndarray | None = None
    mld_m: np.ndarray | None = None
    npp_mmol_m3_day: np.ndarray | None = None


def habitat_scoring(inputs: HabitatInputs, priors: Dict, weights: Dict) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    s_temp = score_temp_c(inputs.sst_c, priors['sst_opt_c'], priors['sst_sigma_c'])
    s_chl  = score_chl_mg_m3(inputs.chl_mg_m3, priors['chl_opt_mg_m3'], priors['chl_sigma_log10'])
    s_cur  = score_current_m_s(inputs.current_m_s, priors['current_opt_m_s'], priors['current_sigma_m_s'])
    s_sss = score_salinity_psu(inputs.sss_psu, priors.get('sss_opt_psu', 35.5), priors.get('sss_sigma_psu', 0.6)) if inputs.sss_psu is not None else None
    s_o2 = score_o2_umol_l(inputs.o2_umol_l, priors.get('o2_opt_umol_l', 180.0), priors.get('o2_sigma_umol_l', 50.0)) if inputs.o2_umol_l is not None else None
    s_waves = score_waves_hs(inputs.waves_hs_m, priors.get('waves_hs_soft_max_m', 1.5))

    fronts = front_feature_stack(inputs.sst_c, inputs.chl_mg_m3, inputs.ssh_m, priors)
    s_front = fronts['front_fused']
    s_thermo = thermocline_proxy(inputs.mld_m, inputs.sst_c)
    s_oxy_access = oxygen_access_score(inputs.o2_umol_l, inputs.mld_m)
    s_npp = npp_score(inputs.npp_mmol_m3_day)

    w = dict(weights)
    total = sum(max(float(v), 0.0) for v in w.values())
    if total <= 0:
        w = {'temp':1.0}
        total = 1.0
    for k in list(w.keys()):
        w[k] = max(float(w[k]), 0.0) / total

    phab = (
        w.get('temp',0.0)*s_temp +
        w.get('chl',0.0)*s_chl +
        w.get('front',0.0)*s_front +
        w.get('current',0.0)*s_cur +
        (w.get('sss',0.0)*(s_sss if s_sss is not None else 0.0)) +
        (w.get('o2',0.0)*(s_o2 if s_o2 is not None else 0.0)) +
        (w.get('thermo',0.0)*(s_thermo if s_thermo is not None else 0.0)) +
        (w.get('oxy_access',0.0)*(s_oxy_access if s_oxy_access is not None else 0.0)) +
        (w.get('npp',0.0)*(s_npp if s_npp is not None else 0.0))
    )
    phab = np.clip(phab, 0.0, 1.0).astype(np.float32)

    comps = {
        'score_temp': s_temp.astype(np.float32),
        'score_chl': s_chl.astype(np.float32),
        'score_front': s_front.astype(np.float32),
        'score_current': s_cur.astype(np.float32),
        'score_waves': s_waves.astype(np.float32),
        'front_gradient': fronts['front_gradient'].astype(np.float32),
        'front_boa': fronts['front_boa'].astype(np.float32),
        'front_cca': fronts['front_cca'].astype(np.float32),
        'front_gradhist': fronts['front_gradhist'].astype(np.float32),
        'front_fused': fronts['front_fused'].astype(np.float32),
    }
    if s_sss is not None:
        comps['score_sss'] = s_sss.astype(np.float32)
    if s_o2 is not None:
        comps['score_o2'] = s_o2.astype(np.float32)
    if inputs.mld_m is not None:
        comps['mld_m'] = np.asarray(inputs.mld_m, dtype=np.float32)
    if s_thermo is not None:
        comps['thermocline_proxy'] = s_thermo.astype(np.float32)
    if s_oxy_access is not None:
        comps['oxygen_access'] = s_oxy_access.astype(np.float32)
    if inputs.npp_mmol_m3_day is not None:
        comps['npp_mmol_m3_day'] = np.asarray(inputs.npp_mmol_m3_day, dtype=np.float32)
    if s_npp is not None:
        comps['score_npp'] = s_npp.astype(np.float32)
    return phab, comps
