"""Usable capacity with a band, and its fade rate, from pairs of rests at full and empty.

Method: rest-anchored coulomb counting as in Figgener et al., Nature Energy 9, 1438
(2024), doi:10.1038/s41560-024-01620-9, with besslib's own estimator: capacity and the
offset current are fitted together over many pairs (``besslib.soh._core``), so each
estimate carries a band and a count of the pairs it rests on.

The estimate at a time uses only pairs that end before it (causal, as in a BMS). With
memory time ``tau_days`` it lags a steady fade by about ``tau_days`` (90 days at 3
points per year: about 0.7 points). It measures the capacity between the system's own
end of charge and end of discharge under its normal load. A field capacity test at full
power may read lower; besslib does not correct for that.

Two paths give the same numbers: ``run_core`` feeds the core one pair at a time (the
form a BMS or the C port uses); ``capacity_state_at`` builds the same state from sums
in one step (the fast path). Check V7 keeps them equal.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import find_rests, rest_chain, rest_pairs

_EPOCH = pd.Timestamp("2000-01-01")


def days(times) -> np.ndarray:
    """Days since 2000-01-01 (the core's time axis)."""
    return ((pd.DatetimeIndex(times) - _EPOCH).total_seconds() / 86400.0).to_numpy(dtype=float)


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def _columns(pairs: pd.DataFrame):
    return (
        days(pairs["t_end"]),
        pairs["x"].to_numpy(float),
        pairs["dt_days"].to_numpy(float),
        pairs["q_ah"].to_numpy(float),
        pairs["sigma_q_ah"].to_numpy(float),
        pairs["sigma_x"].to_numpy(float),
        pairs["linked"].to_numpy(int),
    )


def capacity_state_at(pairs: pd.DataFrame, spec: PackSpec, config: CapacityConfig, t_days: float) -> _core.Fit:
    """The core state at ``t_days``, built from sums in one step (the batch path)."""
    t, x, dt, q, sq, sx, linked = _columns(pairs)
    keep = t <= t_days
    t, x, dt, q, sq, sx, linked = t[keep], x[keep], dt[keep], q[keep], sq[keep], sx[keep], linked[keep]
    state = _core.capacity_state(spec.nominal_capacity_ah, config.tau_days)
    state.t_last = t_days
    if len(t) == 0:
        return state
    lam = np.exp(-(t_days - t) / config.tau_days) if config.tau_days > 0 else np.ones_like(t)
    w = 1.0 / (sq * sq + (spec.nominal_capacity_ah * sx) ** 2)
    rows = np.column_stack([x, dt])
    lw = lam * w
    a = (lw[:, None, None] * rows[:, :, None] * rows[:, None, :]).sum(axis=0)
    h = (lw[:, None] * rows * q[:, None]).sum(axis=0)
    lag = np.r_[False, linked[1:] == 1]
    v = np.zeros_like(w)
    v[1:] = np.sqrt(w[1:] * w[:-1])
    lv = (lam * v)[lag]
    cur, prev = rows[lag], rows[np.flatnonzero(lag) - 1]
    qc, qp = q[lag], q[np.flatnonzero(lag) - 1]
    lmat = (lv[:, None, None] * cur[:, :, None] * prev[:, None, :]).sum(axis=0)
    for r in range(2):
        for k in range(2):
            state.a[3 * r + k] = float(a[r, k])
            state.l[3 * r + k] = float(lmat[r, k])
        state.h[r] = float(h[r])
        state.ly[r] = float((lv * qc * prev[:, r]).sum())
        state.yl[r] = float((lv * cur[:, r] * qp).sum())
    state.c = float((lw * q * q).sum())
    state.yy1 = float((lv * qc * qp).sum())
    state.m = float(lam.sum())
    state.m_lag = float(lam[lag].sum())
    cross = x != 0
    state.m_cross = float(lam[cross].sum())
    state.m_same = float(lam[~cross].sum())
    state.t_last_cross = float(t[cross].max()) if cross.any() else _core._NEVER
    state.n = int(len(t))
    return state


def run_core(pairs: pd.DataFrame, spec: PackSpec, config: CapacityConfig, at) -> list:
    """The step-by-step path: one ``_core.update`` per pair, ``_core.solve`` at each time in ``at``."""
    t, x, dt, q, sq, sx, linked = _columns(pairs)
    queries = sorted(zip(days(at), range(len(at))))
    z = _z(config.level)
    state = _core.capacity_state(spec.nominal_capacity_ah, config.tau_days)
    out = [None] * len(queries)
    k = 0
    for tq, j in queries:
        while k < len(t) and t[k] <= tq:
            _core.update(state, float(t[k]), float(x[k]), float(dt[k]), float(q[k]), float(sq[k]), float(sx[k]), int(linked[k]))
            k += 1
        out[j] = _solve(state, float(tq), z, config)
    return out


def _solve(state: _core.Fit, t: float, z: float, config: CapacityConfig) -> _core.Estimate:
    return _core.solve(state, t, z, config.gain_error, config.min_cross, config.min_same, config.fade_min_span_years)


def estimate_capacity(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    rests: pd.DataFrame | None = None,
    config: CapacityConfig | None = None,
    at=None,
    mask=None,
    pairs: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Usable capacity with its band at each time in ``at``.

    Parameters
    ----------
    frame
        Operating data in the besslib frame format; may be ``None`` when ``rests`` or
        ``pairs`` are given.
    spec
        The pack. ``soh_capacity`` is the capacity divided by
        ``spec.reference_capacity_ah``, which is printed in every row.
    rests, pairs
        Precomputed outputs of ``find_rests`` or ``rest_pairs``.
    config
        Settings; the defaults are the frozen ones.
    at
        Times for the estimate. Default: the end of every calendar month in the data.
    mask
        Intervals ``(start, end)`` whose rests are left out and across which no two rests
        are paired.

    Returns
    -------
    DataFrame, one row per time: ``capacity_ah``, ``capacity_ah_low``, ``capacity_ah_high``,
    ``soh_capacity`` (with ``_low`` and ``_high``), ``reference_capacity_ah``,
    ``offset_current_a`` (negative = the battery loses charge the meter does not see),
    ``n_pairs``, ``n_full_empty``, ``n_same_kind`` (pairs in memory, decayed), ``birge``,
    ``rho`` (lag-1 correlation), ``status``. ``attrs`` hold the pairs and the
    configuration hash.
    """
    config = config or CapacityConfig()
    if pairs is None:
        if rests is None:
            if frame is None:
                raise ValueError("give a frame, rests or pairs")
            rests = find_rests(frame, spec, config)
        pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    if at is None:
        times = pairs["t_end"] if len(pairs) else (frame.index if frame is not None else [])
        if len(times) == 0:
            raise ValueError("no data to estimate from")
        at = pd.date_range(pd.Timestamp(min(times)).normalize(), pd.Timestamp(max(times)), freq="ME")
    at = pd.DatetimeIndex(at)
    z = _z(config.level)
    rows = []
    for t in days(at):
        est = _solve(capacity_state_at(pairs, spec, config, float(t)), float(t), z, config)
        rows.append(estimate_row(est, spec))
    out = pd.DataFrame(rows, index=at)
    out.index.name = "time"
    out.attrs["pairs"] = pairs
    out.attrs["config_hash"] = config_hash(config)
    return out


def estimate_row(est: _core.Estimate, spec: PackSpec) -> dict:
    """One output row from a core estimate."""
    ref = spec.reference_capacity_ah
    cap = est.theta[0]
    return {
        "capacity_ah": cap,
        "capacity_ah_low": est.low,
        "capacity_ah_high": est.high,
        "soh_capacity": cap / ref,
        "soh_capacity_low": est.low / ref,
        "soh_capacity_high": est.high / ref,
        "reference_capacity_ah": ref,
        "offset_current_a": -est.theta[1] / 24.0,
        "offset_current_sigma_a": est.sigma[1] / 24.0,
        "n_pairs": est.m,
        "n_full_empty": est.m_cross,
        "n_same_kind": est.m_same,
        "birge": est.birge,
        "rho": est.rho,
        "status": _core.STATUS_TEXT[est.status],
    }


def fade_rate(
    pairs: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    n_boot: int = 500,
    seed: int = 0,
) -> dict:
    """The fade rate over the whole record: ``q = (c0 + r y) x + beta dt`` over all pairs.

    The band is the wider of two: the core's lag-corrected t-band and a bootstrap that
    resamples whole calendar months of pairs (``n_boot`` times, fixed ``seed``), which
    keeps the correlation of pairs within a month (temperature, season, operation).

    Returns, in points of ``spec.reference_capacity_ah``: ``rate_points_per_year``
    (negative = fade) with ``_low`` and ``_high``; the same per 100 equivalent full
    cycles (charge throughput / (2 x reference capacity)); ``capacity_start_ah``;
    ``offset_current_a``; ``n_pairs``; ``span_years``; ``status``.
    """
    config = config or CapacityConfig()
    ref = spec.reference_capacity_ah
    t, x, dt, q, sq, sx, linked = _columns(pairs)
    out = {"reference_capacity_ah": ref, "config_hash": config_hash(config), "n_pairs": int(len(t))}
    t0 = float(t[0]) if len(t) else 0.0
    state = _core.fade_state(spec.nominal_capacity_ah, t0)
    for k in range(len(t)):
        _core.update(state, float(t[k]), float(x[k]), float(dt[k]), float(q[k]), float(sq[k]), float(sx[k]), int(linked[k]))
    z = _z(config.level)
    est = _solve(state, float(t[-1]) if len(t) else 0.0, z, config)
    out.update(
        status=_core.STATUS_TEXT[est.status],
        span_years=est.span_years,
        n_full_empty=int(est.m_cross),
        n_same_kind=int(est.m_same),
    )
    if est.status != _core.OK:
        out.update(rate_points_per_year=np.nan, rate_points_per_year_low=np.nan, rate_points_per_year_high=np.nan)
        return out
    pts = 100.0 / ref
    low, high = est.low, est.high
    boot = _bootstrap(pairs, spec, t, t0, n_boot, seed) if n_boot > 0 else np.array([])
    boot_low = boot_high = np.nan
    if n_boot > 0 and len(boot) >= 0.9 * n_boot:
        a = (1.0 - config.level) / 2.0
        boot_low, boot_high = np.quantile(boot, [a, 1.0 - a])
        low, high = min(low, boot_low), max(high, boot_high)
    years = (t[-1] - t[0]) / 365.25
    throughput = float(pairs["throughput_ah"].abs().sum())
    efc_per_year = throughput / (2.0 * ref) / years if years > 0 else np.nan
    out.update(
        capacity_start_ah=est.theta[0],
        rate_points_per_year=est.theta[1] * pts,
        rate_points_per_year_low=low * pts,
        rate_points_per_year_high=high * pts,
        rate_t_band_low=est.low * pts,
        rate_t_band_high=est.high * pts,
        rate_boot_low=boot_low * pts,
        rate_boot_high=boot_high * pts,
        offset_current_a=-est.theta[2] / 24.0,
        birge=est.birge,
        rho=est.rho,
        efc_per_year=efc_per_year,
    )
    k = 100.0 / efc_per_year if np.isfinite(efc_per_year) and efc_per_year > 0 else np.nan
    out.update(
        rate_points_per_100_efc=out["rate_points_per_year"] * k,
        rate_points_per_100_efc_low=out["rate_points_per_year_low"] * k,
        rate_points_per_100_efc_high=out["rate_points_per_year_high"] * k,
    )
    return out


def _bootstrap(pairs: pd.DataFrame, spec: PackSpec, t: np.ndarray, t0: float, n_boot: int, seed: int) -> np.ndarray:
    """Fade rates (Ah per year) from resampled calendar months of pairs, by weighted least squares."""
    x = pairs["x"].to_numpy(float)
    y = (t - t0) / 365.25
    rows = np.column_stack([x, x * y, pairs["dt_days"].to_numpy(float)])
    q = pairs["q_ah"].to_numpy(float)
    w = 1.0 / (pairs["sigma_q_ah"].to_numpy(float) ** 2 + (spec.nominal_capacity_ah * pairs["sigma_x"].to_numpy(float)) ** 2)
    month = np.unique(pd.DatetimeIndex(pairs["t_end"]).to_period("M").asi8, return_inverse=True)[1]
    nmonth = month.max() + 1
    am = np.zeros((nmonth, 3, 3))
    hm = np.zeros((nmonth, 3))
    np.add.at(am, month, w[:, None, None] * rows[:, :, None] * rows[:, None, :])
    np.add.at(hm, month, w[:, None] * rows * q[:, None])
    rng = np.random.default_rng(seed)
    rates = []
    for _ in range(n_boot):
        counts = np.bincount(rng.integers(0, nmonth, nmonth), minlength=nmonth).astype(float)
        a = np.tensordot(counts, am, axes=1)
        if np.linalg.cond(a) > 1e12:
            continue
        rates.append(np.linalg.solve(a, counts @ hm)[1])
    return np.asarray(rates)
