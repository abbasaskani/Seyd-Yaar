from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, Optional
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

def _centered_grad(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    a = np.asarray(arr, dtype=np.float32)
    gy, gx = np.gradient(a)
    return gx.astype(np.float32), gy.astype(np.float32)


def _binary_erode8(mask: np.ndarray) -> np.ndarray:
    m = np.asarray(mask, dtype=bool)
    p = np.pad(m, 1, mode='constant', constant_values=False)
    out = np.ones_like(m, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            out &= p[dy:dy+m.shape[0], dx:dx+m.shape[1]]
    return out


def _distance_to_mask(mask: np.ndarray) -> np.ndarray:
    m = np.asarray(mask, dtype=bool)
    inf = np.float32(1e6)
    d = np.where(m, 0.0, inf).astype(np.float32)
    h, w = d.shape
    for y in range(h):
        for x in range(w):
            best = d[y, x]
            if y > 0: best = min(best, d[y-1, x] + 1.0)
            if x > 0: best = min(best, d[y, x-1] + 1.0)
            if y > 0 and x > 0: best = min(best, d[y-1, x-1] + 1.4142)
            if y > 0 and x+1 < w: best = min(best, d[y-1, x+1] + 1.4142)
            d[y, x] = best
    for y in range(h-1, -1, -1):
        for x in range(w-1, -1, -1):
            best = d[y, x]
            if y+1 < h: best = min(best, d[y+1, x] + 1.0)
            if x+1 < w: best = min(best, d[y, x+1] + 1.0)
            if y+1 < h and x+1 < w: best = min(best, d[y+1, x+1] + 1.4142)
            if y+1 < h and x > 0: best = min(best, d[y+1, x-1] + 1.4142)
            d[y, x] = best
    return d.astype(np.float32)


def _local_extrema(arr: np.ndarray, mode: str = 'max') -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    p = np.pad(a, 1, mode='edge')
    if mode == 'max':
        ref = a
        ok = np.ones_like(a, dtype=bool)
        for dy in range(3):
            for dx in range(3):
                if dy == 1 and dx == 1:
                    continue
                ok &= ref >= p[dy:dy+a.shape[0], dx:dx+a.shape[1]]
        return ok
    ref = a
    ok = np.ones_like(a, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            if dy == 1 and dx == 1:
                continue
            ok &= ref <= p[dy:dy+a.shape[0], dx:dx+a.shape[1]]
    return ok


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
    fw = priors.get('front_weights', {'temp':0.5,'chl':0.25,'ssh':0.25})
    methods = ['gradient', 'boa', 'cca', 'gradhist']
    out: Dict[str, np.ndarray] = {}
    for method in methods:
        tf = _front_from_field(sst_c, method)
        cf = _front_from_field(np.log10(np.clip(chl_mg_m3, 1e-6, None)), method)
        sf = _front_from_field(ssh_m, method)
        out[f'temp_front_{method}'] = tf
        out[f'chl_front_{method}'] = cf
        out[f'ssh_front_{method}'] = sf
        out[f'front_{method}'] = front_score(tf, cf, sf, fw.get('temp',0.5), fw.get('chl',0.25), fw.get('ssh',0.25))
    out['front_fused'] = _robust01(
        0.40 * out['front_boa'] +
        0.25 * out['front_gradhist'] +
        0.20 * out['front_cca'] +
        0.15 * out['front_gradient']
    )
    return out


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


def eddy_feature_stack(ssh_m: np.ndarray, u_current_m_s: Optional[np.ndarray], v_current_m_s: Optional[np.ndarray]) -> Dict[str, np.ndarray]:
    ssh = np.asarray(ssh_m, dtype=np.float32)
    if u_current_m_s is None or v_current_m_s is None:
        z = np.zeros_like(ssh, dtype=np.float32)
        return {
            'eddy_eke': z, 'eddy_vorticity': z, 'eddy_strain': z, 'eddy_okubo_weiss': z,
            'eddy_core': z, 'eddy_edge_distance': z, 'eddy_polarity': z, 'eddy_amplitude': z,
            'eddy_radius': z, 'eddy_opportunity': z,
        }
    u = np.asarray(u_current_m_s, dtype=np.float32)
    v = np.asarray(v_current_m_s, dtype=np.float32)
    du_dx, du_dy = _centered_grad(u)
    dv_dx, dv_dy = _centered_grad(v)
    vort = (dv_dx - du_dy).astype(np.float32)
    strain = np.sqrt((du_dx - dv_dy) ** 2 + (dv_dx + du_dy) ** 2).astype(np.float32)
    ow = (strain ** 2 - vort ** 2).astype(np.float32)
    u_an = u - np.nanmean(u)
    v_an = v - np.nanmean(v)
    eke = (0.5 * (u_an ** 2 + v_an ** 2)).astype(np.float32)

    high_eke = eke >= np.nanpercentile(eke, 75.0)
    closed_like = ow < np.nanpercentile(ow, 30.0)
    peaks = _local_extrema(ssh, 'max')
    pits = _local_extrema(ssh, 'min')
    cyc = pits & high_eke & closed_like
    anti = peaks & high_eke & closed_like
    core_mask = cyc | anti
    core = np.zeros_like(ssh, dtype=np.float32)
    core[anti] = 1.0
    core[cyc] = -1.0

    amp = np.abs(ssh - _mean_filter3(ssh)).astype(np.float32) * core_mask.astype(np.float32)
    radius = _distance_to_mask(core_mask)
    edge_distance = _distance_to_mask(~core_mask)
    edge_distance = np.where(core_mask, radius, 0.0).astype(np.float32)
    polarity = np.sign(core).astype(np.float32)
    opp = _robust01(0.40 * _robust01(eke) + 0.25 * _robust01(-ow) + 0.20 * _robust01(amp) + 0.15 * _robust01(edge_distance))
    return {
        'eddy_eke': _robust01(eke),
        'eddy_vorticity': _robust01(np.abs(vort)),
        'eddy_strain': _robust01(strain),
        'eddy_okubo_weiss': _robust01(-ow),
        'eddy_core': polarity,
        'eddy_edge_distance': _robust01(edge_distance),
        'eddy_polarity': polarity,
        'eddy_amplitude': _robust01(amp),
        'eddy_radius': _robust01(radius),
        'eddy_opportunity': opp.astype(np.float32),
    }


def lagrangian_feature_stack(u_current_m_s: Optional[np.ndarray], v_current_m_s: Optional[np.ndarray], horizon_hours: float = 72.0) -> Dict[str, np.ndarray]:
    if u_current_m_s is None or v_current_m_s is None:
        z = np.zeros((1, 1), dtype=np.float32)
        return {
            'ftle': z, 'fsle': z, 'lavd': z, 'lcs_ridge': z, 'lagrangian_opportunity': z,
        }
    u = np.asarray(u_current_m_s, dtype=np.float32)
    v = np.asarray(v_current_m_s, dtype=np.float32)
    T = max(float(horizon_hours), 1.0) * 3600.0
    du_dx, du_dy = _centered_grad(u)
    dv_dx, dv_dy = _centered_grad(v)
    # Local frozen-flow deformation tensor approximation
    a11 = 1.0 + T * du_dx * 1e-4
    a12 = T * du_dy * 1e-4
    a21 = T * dv_dx * 1e-4
    a22 = 1.0 + T * dv_dy * 1e-4
    c11 = a11 * a11 + a21 * a21
    c12 = a11 * a12 + a21 * a22
    c22 = a12 * a12 + a22 * a22
    tr = c11 + c22
    det = c11 * c22 - c12 * c12
    disc = np.clip(tr * tr - 4.0 * det, 0.0, None)
    lam_max = 0.5 * (tr + np.sqrt(disc))
    lam_max = np.clip(lam_max, 1.0 + 1e-6, None)
    ftle = (0.5 / max(abs(T), 1e-6) * np.log(lam_max)).astype(np.float32)
    strain_rate = np.clip(np.sqrt(lam_max) - 1.0, 1e-6, None)
    fsle = (np.log(2.0) / np.maximum(strain_rate * 3600.0, 1e-6)).astype(np.float32)
    vort = (dv_dx - du_dy).astype(np.float32)
    vort_an = np.abs(vort - np.nanmean(vort))
    lavd = (vort_an * (T / 3600.0)).astype(np.float32)
    ridge = (_robust01(ftle) > 0.85).astype(np.float32) * _robust01(gradient_magnitude(ftle))
    opp = _robust01(0.45 * _robust01(ftle) + 0.30 * _robust01(lavd) + 0.25 * _robust01(gradient_magnitude(ftle)))
    return {
        'ftle': _robust01(ftle),
        'fsle': _robust01(-fsle),
        'lavd': _robust01(lavd),
        'lcs_ridge': _robust01(ridge),
        'lagrangian_opportunity': opp.astype(np.float32),
    }




def ameda_like_feature_stack(ssh_m: np.ndarray, u_current_m_s: Optional[np.ndarray], v_current_m_s: Optional[np.ndarray]) -> Dict[str, np.ndarray]:
    ssh = np.asarray(ssh_m, dtype=np.float32)
    if u_current_m_s is None or v_current_m_s is None:
        z = np.zeros_like(ssh, dtype=np.float32)
        return {
            'ameda_proxy': z, 'ameda_core': z, 'ameda_polarity': z,
            'ameda_confidence': z,
        }
    u = np.asarray(u_current_m_s, dtype=np.float32)
    v = np.asarray(v_current_m_s, dtype=np.float32)
    h, w = ssh.shape
    yy, xx = np.meshgrid(np.linspace(-1.0, 1.0, h, dtype=np.float32), np.linspace(-1.0, 1.0, w, dtype=np.float32), indexing='ij')
    u_an = u - np.nanmean(u)
    v_an = v - np.nanmean(v)
    ang_mom = (xx * v_an - yy * u_an).astype(np.float32)
    vort = (_centered_grad(v)[0] - _centered_grad(u)[1]).astype(np.float32)
    ssh_an = (ssh - _mean_filter3(ssh)).astype(np.float32)
    proxy = _robust01(np.abs(ang_mom))
    conf = _robust01(0.50 * np.abs(ang_mom) + 0.30 * np.abs(vort) + 0.20 * np.abs(ssh_an))
    core_mask = _local_extrema(np.abs(ang_mom), 'max') & (conf >= np.nanpercentile(conf, 85.0))
    polarity = np.sign(ang_mom).astype(np.float32) * core_mask.astype(np.float32)
    return {
        'ameda_proxy': proxy.astype(np.float32),
        'ameda_core': polarity.astype(np.float32),
        'ameda_polarity': np.sign(ang_mom).astype(np.float32),
        'ameda_confidence': conf.astype(np.float32),
    }


def ameda_track_score(prev_core: Optional[np.ndarray], curr_core: np.ndarray, next_core: Optional[np.ndarray], curr_conf: np.ndarray) -> np.ndarray:
    curr = np.asarray(curr_core, dtype=np.float32)
    conf = np.asarray(curr_conf, dtype=np.float32)
    support = np.zeros_like(curr, dtype=np.float32)
    if prev_core is not None:
        support += _mean_filter3(np.abs(np.asarray(prev_core, dtype=np.float32)))
    if next_core is not None:
        support += _mean_filter3(np.abs(np.asarray(next_core, dtype=np.float32)))
    support = np.clip(support, 0.0, 1.0)
    return _robust01(conf * (0.35 + 0.65 * support))


def time_dependent_lagrangian_feature_stack(u_seq: list[np.ndarray], v_seq: list[np.ndarray], dt_hours: float = 24.0) -> Dict[str, np.ndarray]:
    if len(u_seq) < 2 or len(v_seq) < 2:
        z = np.zeros_like(np.asarray(u_seq[0] if u_seq else [[0.0]], dtype=np.float32), dtype=np.float32)
        return {
            'ftle_td': z, 'fsle_td': z, 'lavd_td': z,
            'lcs_ridge_td': z, 'lagrangian_td_opportunity': z,
        }
    dt = max(float(dt_hours), 1.0) * 3600.0
    a11 = np.ones_like(np.asarray(u_seq[0], dtype=np.float32))
    a12 = np.zeros_like(a11)
    a21 = np.zeros_like(a11)
    a22 = np.ones_like(a11)
    lavd_acc = np.zeros_like(a11)
    for u, v in zip(u_seq, v_seq):
        u = np.asarray(u, dtype=np.float32)
        v = np.asarray(v, dtype=np.float32)
        du_dx, du_dy = _centered_grad(u)
        dv_dx, dv_dy = _centered_grad(v)
        b11 = 1.0 + dt * du_dx * 1e-4
        b12 = dt * du_dy * 1e-4
        b21 = dt * dv_dx * 1e-4
        b22 = 1.0 + dt * dv_dy * 1e-4
        na11 = b11 * a11 + b12 * a21
        na12 = b11 * a12 + b12 * a22
        na21 = b21 * a11 + b22 * a21
        na22 = b21 * a12 + b22 * a22
        a11, a12, a21, a22 = na11.astype(np.float32), na12.astype(np.float32), na21.astype(np.float32), na22.astype(np.float32)
        vort = (dv_dx - du_dy).astype(np.float32)
        lavd_acc += np.abs(vort - np.nanmean(vort)).astype(np.float32) * (dt / 3600.0)
    c11 = a11 * a11 + a21 * a21
    c12 = a11 * a12 + a21 * a22
    c22 = a12 * a12 + a22 * a22
    tr = c11 + c22
    det = c11 * c22 - c12 * c12
    disc = np.clip(tr * tr - 4.0 * det, 0.0, None)
    lam_max = 0.5 * (tr + np.sqrt(disc))
    lam_max = np.clip(lam_max, 1.0 + 1e-6, None)
    total_T = dt * float(len(u_seq))
    ftle = (0.5 / max(abs(total_T), 1e-6) * np.log(lam_max)).astype(np.float32)
    strain_rate = np.clip(np.sqrt(lam_max) - 1.0, 1e-6, None)
    fsle = (np.log(2.0) / np.maximum(strain_rate * 3600.0, 1e-6)).astype(np.float32)
    ridge = (_robust01(ftle) > 0.85).astype(np.float32) * _robust01(gradient_magnitude(ftle))
    opp = _robust01(0.45 * _robust01(ftle) + 0.30 * _robust01(lavd_acc) + 0.25 * _robust01(gradient_magnitude(ftle)))
    return {
        'ftle_td': _robust01(ftle),
        'fsle_td': _robust01(-fsle),
        'lavd_td': _robust01(lavd_acc),
        'lcs_ridge_td': _robust01(ridge),
        'lagrangian_td_opportunity': opp.astype(np.float32),
    }

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
    u_current_m_s: np.ndarray | None = None
    v_current_m_s: np.ndarray | None = None
    sss_psu: np.ndarray | None = None
    o2_umol_l: np.ndarray | None = None
    mld_m: np.ndarray | None = None
    npp_mmol_m3_day: np.ndarray | None = None
    prev_u_current_m_s: np.ndarray | None = None
    prev_v_current_m_s: np.ndarray | None = None
    next_u_current_m_s: np.ndarray | None = None
    next_v_current_m_s: np.ndarray | None = None


def habitat_scoring(inputs: HabitatInputs, priors: Dict, weights: Dict) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    s_temp = score_temp_c(inputs.sst_c, priors['sst_opt_c'], priors['sst_sigma_c'])
    s_chl  = score_chl_mg_m3(inputs.chl_mg_m3, priors['chl_opt_mg_m3'], priors['chl_sigma_log10'])
    s_cur  = score_current_m_s(inputs.current_m_s, priors['current_opt_m_s'], priors['current_sigma_m_s'])
    s_sss = score_salinity_psu(inputs.sss_psu, priors.get('sss_opt_psu', 35.5), priors.get('sss_sigma_psu', 0.6)) if inputs.sss_psu is not None else None
    s_o2 = score_o2_umol_l(inputs.o2_umol_l, priors.get('o2_opt_umol_l', 180.0), priors.get('o2_sigma_umol_l', 50.0)) if inputs.o2_umol_l is not None else None
    s_waves = score_waves_hs(inputs.waves_hs_m, priors.get('waves_hs_soft_max_m', 1.5))

    fronts = front_feature_stack(inputs.sst_c, inputs.chl_mg_m3, inputs.ssh_m, priors)
    eddies = eddy_feature_stack(inputs.ssh_m, inputs.u_current_m_s, inputs.v_current_m_s)
    ameda = ameda_like_feature_stack(inputs.ssh_m, inputs.u_current_m_s, inputs.v_current_m_s)
    lagrangian = lagrangian_feature_stack(inputs.u_current_m_s, inputs.v_current_m_s, horizon_hours=float(priors.get("lagrangian_horizon_hours", 72.0)))
    seq_u = [a for a in [inputs.prev_u_current_m_s, inputs.u_current_m_s, inputs.next_u_current_m_s] if a is not None]
    seq_v = [a for a in [inputs.prev_v_current_m_s, inputs.v_current_m_s, inputs.next_v_current_m_s] if a is not None]
    lagrangian_td = time_dependent_lagrangian_feature_stack(seq_u, seq_v, dt_hours=float(priors.get("lagrangian_step_hours", 24.0))) if len(seq_u) >= 2 and len(seq_v) >= 2 else None
    s_front = fronts['front_fused']
    s_eddy = _robust01(0.70 * eddies['eddy_opportunity'] + 0.30 * ameda['ameda_confidence'])
    if lagrangian_td is not None:
        s_lagrangian = _robust01(0.45 * lagrangian['lagrangian_opportunity'] + 0.55 * lagrangian_td['lagrangian_td_opportunity'])
    else:
        s_lagrangian = lagrangian['lagrangian_opportunity']
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
        w.get('eddy',0.0)*s_eddy +
        w.get('lagrangian',0.0)*s_lagrangian +
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
        'eddy_eke': eddies['eddy_eke'].astype(np.float32),
        'eddy_vorticity': eddies['eddy_vorticity'].astype(np.float32),
        'eddy_strain': eddies['eddy_strain'].astype(np.float32),
        'eddy_okubo_weiss': eddies['eddy_okubo_weiss'].astype(np.float32),
        'eddy_core': eddies['eddy_core'].astype(np.float32),
        'eddy_edge_distance': eddies['eddy_edge_distance'].astype(np.float32),
        'eddy_polarity': eddies['eddy_polarity'].astype(np.float32),
        'eddy_amplitude': eddies['eddy_amplitude'].astype(np.float32),
        'eddy_radius': eddies['eddy_radius'].astype(np.float32),
        'eddy_opportunity': s_eddy.astype(np.float32),
        'ameda_proxy': ameda['ameda_proxy'].astype(np.float32),
        'ameda_core': ameda['ameda_core'].astype(np.float32),
        'ameda_polarity': ameda['ameda_polarity'].astype(np.float32),
        'ameda_confidence': ameda['ameda_confidence'].astype(np.float32),
        'ftle': lagrangian['ftle'].astype(np.float32),
        'fsle': lagrangian['fsle'].astype(np.float32),
        'lavd': lagrangian['lavd'].astype(np.float32),
        'lcs_ridge': lagrangian['lcs_ridge'].astype(np.float32),
        'lagrangian_opportunity': s_lagrangian.astype(np.float32),
    }
    if lagrangian_td is not None:
        comps['ftle_td'] = lagrangian_td['ftle_td'].astype(np.float32)
        comps['fsle_td'] = lagrangian_td['fsle_td'].astype(np.float32)
        comps['lavd_td'] = lagrangian_td['lavd_td'].astype(np.float32)
        comps['lcs_ridge_td'] = lagrangian_td['lcs_ridge_td'].astype(np.float32)
        comps['lagrangian_td_opportunity'] = lagrangian_td['lagrangian_td_opportunity'].astype(np.float32)
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
