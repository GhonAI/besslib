"""Usable capacity with a band, and its fade rate, from pairs of rests at full and empty.

Method: rest-anchored coulomb counting as in Figgener et al., Nature Energy 9, 1438
(2024), doi:10.1038/s41560-024-01620-9, with besslib's own estimator: capacity and the
offset current are fitted together over many pairs (``besslib.soh._core``), so each
estimate carries a band and a count of the pairs it rests on.

The estimate at a time uses only pairs that end before it (causal, as in a BMS). Without
voltage terms (the release 0.1.0 settings) it measures the capacity between the system's
own end of charge and end of discharge under its normal load; with them (version 0.2) it
measures the capacity between a full and an empty rest at two stated relaxed voltages,
not the charge between the BMS stops, which move as the cells age. A field capacity test
at full power may read lower; besslib does not correct for that.

The fit (version 0.2; each part has its setting in ``CapacityConfig``): with
``local_trend`` the fit with memory time ``tau_days`` carries a linear trend in the
capacity and reads the value at the date, so a steady fade does not lag; without it the
estimate lags a steady fade by about ``tau_days``. With ``voltage_terms = "every_pair"``
every pair also carries the relaxed open-circuit voltages of its two rests
(``rests.rest_pairs``: ``u_start``, ``u_end``), each end its own kind's voltage minus a
reference, because two stops of one kind are not at the same state of charge
(``voltage_terms`` gives the columns as the core forms them);
``"every_pair_quadratic"`` (version 0.3) adds the square of that difference at each end,
because the charge is a curved function of the voltage. The capacity is then the
capacity between a full rest at ``ref_full_v`` and an empty rest at ``ref_empty_v``, the
forgetting-weighted mean rest voltages of each kind at the date. The columns
``gamma_full_ah_per_v`` and ``gamma_empty_ah_per_v`` (and ``gamma_full2_ah_per_v2``,
``gamma_empty2_ah_per_v2``) report the terms, and
``terms`` the parameters each solve used. ``band_forgetting``
sets how often the forgetting weight enters the band's sums, and with it the band's
degrees of freedom (``dof``). With ``plausibility`` a capacity or band at or below zero,
or a full-to-empty or empty-to-full pair of the wrong sign in memory, is ``not
possible``. ``CapacityConfig().v0_1()`` gives the fit of release 0.1.0, number for
number. The method is in ``besslib.soh._core``.

Two paths give the same numbers: ``run_core`` feeds the core one pair at a time (the
form a BMS or the C port uses); ``capacity_state_at`` builds the same state from sums
in one step (the fast path). The tests keep them equal.

The band is a band on the capacity at the date as the weighted fit measures it. With
the trend it allows for a straight-line fade, not for a fade that bends within the
memory time. It does not include a systematic offset of the usable capacity (for
example a field test at a different current).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import find_rests, rest_chain, rest_pairs
from besslib.soh.rests import levels_from as rest_levels_from

_EPOCH = pd.Timestamp("2000-01-01")


def days(times) -> np.ndarray:
    """Days since 2000-01-01 (the core's time axis)."""
    return ((pd.DatetimeIndex(times) - _EPOCH).total_seconds() / 86400.0).to_numpy(dtype=float)


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


#: The core's codes of the pair kinds in ``rests.rest_pairs``.
_KIND_CODES = {"F->E": _core.PAIR_FE, "E->F": _core.PAIR_EF, "F->F": _core.PAIR_FF, "E->E": _core.PAIR_EE}

#: The core's codes of ``CapacityConfig.voltage_terms``.
_VOLTAGE_CODES = {"none": 0, "every_pair": 1, "every_pair_quadratic": 2}


def _columns(pairs: pd.DataFrame):
    """The core's inputs from a pair table. Without ``kind`` the kind is taken from the
    sign of ``x``; without ``u_start`` or ``u_end`` the rest voltages are 0 (no voltage
    information)."""
    n = len(pairs)
    if "kind" in pairs:
        kind = pairs["kind"].map(_KIND_CODES).fillna(_core.PAIR_FROM_X).to_numpy(int)
    else:
        kind = np.full(n, _core.PAIR_FROM_X, dtype=int)
    u_a = pairs["u_start"].to_numpy(float) if "u_start" in pairs else np.zeros(n)
    u_b = pairs["u_end"].to_numpy(float) if "u_end" in pairs else np.zeros(n)
    return (
        days(pairs["t_end"]),
        pairs["x"].to_numpy(float),
        pairs["dt_days"].to_numpy(float),
        pairs["q_ah"].to_numpy(float),
        pairs["sigma_q_ah"].to_numpy(float),
        pairs["sigma_x"].to_numpy(float),
        pairs["linked"].to_numpy(int),
        kind,
        u_a,
        u_b,
    )


def _new_state(spec: PackSpec, config: CapacityConfig) -> _core.Fit:
    return _core.capacity_state(
        spec.nominal_capacity_ah,
        config.tau_days,
        int(config.local_trend),
        _VOLTAGE_CODES[config.voltage_terms],
        int(config.band_forgetting),
        int(config.plausibility),
    )


def _kinds(kind: np.ndarray, x: np.ndarray) -> np.ndarray:
    """``kind`` with ``PAIR_FROM_X`` resolved from the sign of ``x``, as ``_core.update`` does."""
    out = kind.copy()
    out[(kind == _core.PAIR_FROM_X) & (x > 0)] = _core.PAIR_EF
    out[(kind == _core.PAIR_FROM_X) & (x < 0)] = _core.PAIR_FE
    return out


def _ends(kind: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pair: whether its start and its end rest are full, and whether the kinds are known."""
    known = kind != _core.PAIR_FROM_X
    fa = (kind == _core.PAIR_FE) | (kind == _core.PAIR_FF)
    fb = (kind == _core.PAIR_EF) | (kind == _core.PAIR_FF)
    return fa, fb, known


def voltage_terms(pairs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, float, float]:
    """The voltage terms ``du_f``, ``du_e`` of every pair, as the core forms them, and the
    two reference voltages (the first full and the first empty rest; NaN if none).

    Each end gives its own kind's rest voltage minus that kind's reference
    (``besslib.soh._core``); an end whose voltage is not finite gives 0, and a pair whose
    kinds are not known gives 0 for both.
    """
    _, x, _, _, _, _, _, kind, u_a, u_b = _columns(pairs)
    kind = _kinds(kind, x)
    du_f, du_e, r0f, r0e, _, _ = _voltage_parts(kind, u_a, u_b)
    return du_f, du_e, r0f, r0e


def quadratic_terms(pairs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """The quadratic voltage terms ``du_f2``, ``du_e2`` of every pair, as the core forms them
    (``besslib.soh._core``): as ``voltage_terms``, with the square of each end's difference."""
    _, x, _, _, _, _, _, kind, u_a, u_b = _columns(pairs)
    kind = _kinds(kind, x)
    _, _, _, _, da, db = _voltage_parts(kind, u_a, u_b)
    return _end_terms(kind, da * da, db * db)


def _end_terms(kind, da, db) -> tuple[np.ndarray, np.ndarray]:
    """What each end gives its own kind: the end rest's value less the start rest's, full and empty."""
    fa, fb, known = _ends(kind)
    du_f = np.where(fb, db, 0.0) - np.where(fa, da, 0.0)
    du_e = np.where(~fb, db, 0.0) - np.where(~fa, da, 0.0)
    return np.where(known, du_f, 0.0), np.where(known, du_e, 0.0)


def _voltage_parts(kind, u_a, u_b):
    """``du_f``, ``du_e``, the references, and each end's voltage minus its reference."""
    fa, fb, known = _ends(kind)
    # The references: the first finite rest voltage of each kind, in the order the core
    # sees the rests (a pair's start before its end).
    order_u = np.column_stack([u_a, u_b]).ravel()
    order_full = np.column_stack([fa, fb]).ravel()
    order_ok = np.repeat(known, 2) & np.isfinite(order_u)
    r0 = {}
    for full in (True, False):
        hit = np.flatnonzero(order_ok & (order_full == full))
        r0[full] = float(order_u[hit[0]]) if len(hit) else np.nan
    with np.errstate(invalid="ignore"):
        da = np.where(fa, u_a - r0[True], u_a - r0[False])
        db = np.where(fb, u_b - r0[True], u_b - r0[False])
    da = np.where(known & np.isfinite(da), da, 0.0)
    db = np.where(known & np.isfinite(db), db, 0.0)
    du_f, du_e = _end_terms(kind, da, db)
    return du_f, du_e, r0[True], r0[False], da, db


def _regressors(state: _core.Fit, x, dt, du_f, du_e, du_f2, du_e2, y) -> list:
    """The regressor columns in the core's order: x, (x y), dt, (du_f, du_e), (du_f2, du_e2)."""
    cols = [x]
    if state.trend:
        cols.append(x * y)
    cols.append(dt)
    if state.voltage:
        cols += [du_f, du_e]
    if state.voltage == 2:
        cols += [du_f2, du_e2]
    return cols


def capacity_state_at(pairs: pd.DataFrame, spec: PackSpec, config: CapacityConfig, t_days: float) -> _core.Fit:
    """The core state at ``t_days``, built from sums in one step (the batch path)."""
    t, x, dt, q, sq, sx, linked, kind, u_a, u_b = _columns(pairs)
    keep = t <= t_days
    t, x, dt, q, sq, sx, linked = t[keep], x[keep], dt[keep], q[keep], sq[keep], sx[keep], linked[keep]
    kind, u_a, u_b = _kinds(kind[keep], x), u_a[keep], u_b[keep]
    state = _new_state(spec, config)
    state.t_last = t_days
    if len(t) == 0:
        return state
    du_f, du_e, du_f2, du_e2 = (np.zeros(len(t)) for _ in range(4))
    if state.voltage:
        du_f, du_e, r0f, r0e, da, db = _voltage_parts(kind, u_a, u_b)
        du_f2, du_e2 = _end_terms(kind, da * da, db * db)
    state.t0_days = float(t[0])  # the time of the first pair, as the core takes it
    state.has_t0 = 1
    tau = config.tau_days
    lam = np.exp(-(t_days - t) / tau) if tau > 0 else np.ones_like(t)
    w = 1.0 / (sq * sq + (spec.nominal_capacity_ah * sx) ** 2)
    y = (t - state.t0_days) / 365.25
    cols = _regressors(state, x, dt, du_f, du_e, du_f2, du_e2, y)
    p = state.p
    rows = np.column_stack(cols)
    zs = np.column_stack(cols + [q - spec.nominal_capacity_ah * x])
    lw = lam * w
    a = (lw[:, None, None] * rows[:, :, None] * rows[:, None, :]).sum(axis=0)
    h = (lw[:, None] * rows * q[:, None]).sum(axis=0)
    band = state.band
    s4 = np.einsum("n,nr,ns,ni,nj->rsij", lam * w * w if band == 1 else lam * lam * w * w, rows, rows, zs, zs)
    lag = np.r_[False, linked[1:] == 1]
    if state.voltage:
        # The reference sums: each rest once, as a pair's end, or as the start of a pair
        # that does not continue the one before, with its decayed weight.
        fa, fb, known = _ends(kind)
        lam_a = lam * (np.exp(-dt / tau) if tau > 0 else 1.0)
        new_a = known & ~lag & np.isfinite(u_a)
        end_b = known & np.isfinite(u_b)
        for full, r0 in ((True, r0f), (False, r0e)):
            if not np.isfinite(r0):
                continue
            sa = new_a & (fa == full)
            sb = end_b & (fb == full)
            w_sum = float(lam_a[sa].sum() + lam[sb].sum())
            u_sum = float((lam_a[sa] * da[sa]).sum() + (lam[sb] * db[sb]).sum())
            if full:
                state.r0_f, state.has_r0_f, state.wu_f, state.su_f = r0, 1, w_sum, u_sum
            else:
                state.r0_e, state.has_r0_e, state.wu_e, state.su_e = r0, 1, w_sum, u_sum
    cur_i = np.flatnonzero(lag)
    prev_i = cur_i - 1
    g = np.exp(-(t[cur_i] - t[prev_i]) / (2.0 * tau)) if tau > 0 else np.ones(len(cur_i))
    lg = lam[cur_i] * g
    lv = lg * np.sqrt(w[cur_i] * w[prev_i])
    cur, prev = rows[cur_i], rows[prev_i]
    qc, qp = q[cur_i], q[prev_i]
    lmat = (lv[:, None, None] * cur[:, :, None] * prev[:, None, :]).sum(axis=0)
    lg2 = lam[cur_i] * lam[prev_i]  # lambda_k lambda_(k-1)
    u = (lg if band == 1 else lg2) * w[cur_i] * w[prev_i]
    aa = cur[:, :, None] * prev[:, None, :] + prev[:, :, None] * cur[:, None, :]
    s4l = np.einsum("n,nrs,ni,nj->rsij", u, aa, zs[cur_i], zs[prev_i])
    for r in range(p):
        for k in range(p):
            state.a[_core.P_MAX * r + k] = float(a[r, k])
            state.l[_core.P_MAX * r + k] = float(lmat[r, k])
            for i in range(p + 1):
                for j in range(p + 1):
                    idx = ((r * _core.P_MAX + k) * _core._NZ + i) * _core._NZ + j
                    state.s4[idx] = float(s4[r, k, i, j])
                    state.s4l[idx] = float(s4l[r, k, i, j])
        state.h[r] = float(h[r])
        state.ly[r] = float((lv * qc * prev[:, r]).sum())
        state.yl[r] = float((lv * cur[:, r] * qp).sum())
    state.c = float((lw * q * q).sum())
    state.yy1 = float((lv * qc * qp).sum())
    state.m = float(lam.sum())
    state.m_lag = float(lg.sum())
    state.m2 = float((lam * lam).sum())
    state.m2_lag = float(lg2.sum())
    state.m2_ll = float(lg2[lag[prev_i]].sum())
    cross = (kind == _core.PAIR_FE) | (kind == _core.PAIR_EF)
    state.m_cross = float(lam[cross].sum())
    state.m_same = float(lam[~cross].sum())
    state.m_ff = float(lam[kind == _core.PAIR_FF].sum())
    state.m_ee = float(lam[kind == _core.PAIR_EE].sum())
    state.t_last_cross = float(t[cross].max()) if cross.any() else _core._NEVER
    wrong = ((kind == _core.PAIR_FE) & (q >= 0)) | ((kind == _core.PAIR_EF) & (q <= 0))
    state.t_wrong = float(t[wrong].max()) if wrong.any() else _core._NEVER
    if cross.any():
        state.y_min = float(y[cross].min())
        state.y_max = float(y[cross].max())
    state.n = int(len(t))
    # The last pair, so the state can take further pairs with ``_core.update``.
    state.prev_ok = 1
    state.prev_linked = int(lag[-1])
    for r in range(p):
        state.prev_a[r] = float(rows[-1, r])
    for r in range(p + 1):
        state.prev_z[r] = float(zs[-1, r])
    state.prev_q = float(q[-1])
    state.prev_w = float(w[-1])
    state.prev_t = float(t[-1])
    return state


def run_core(pairs: pd.DataFrame, spec: PackSpec, config: CapacityConfig, at) -> list:
    """The step-by-step path: one ``_core.update`` per pair, ``_core.solve`` at each time in ``at``."""
    t, x, dt, q, sq, sx, linked, kind, u_a, u_b = _columns(pairs)
    queries = sorted(zip(days(at), range(len(at))))
    z = _z(config.level)
    state = _new_state(spec, config)
    out = [None] * len(queries)
    k = 0
    for tq, j in queries:
        while k < len(t) and t[k] <= tq:
            _core.update(
                state, float(t[k]), float(x[k]), float(dt[k]), float(q[k]), float(sq[k]), float(sx[k]), int(linked[k]),
                float(u_a[k]), float(u_b[k]), int(kind[k]),
            )
            k += 1
        out[j] = _solve(state, float(tq), z, config)
    return out


def _solve(state: _core.Fit, t: float, z: float, config: CapacityConfig) -> _core.Estimate:
    gain = config.gain_error / math.sqrt(3.0) if config.gain_rectangular else config.gain_error
    return _core.solve(
        state, t, z, gain, config.min_cross, config.min_same, config.fade_min_span_years, config.trend_min_cross,
        config.quad_min_rests,
    )


def estimate_capacity(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    rests: pd.DataFrame | None = None,
    config: CapacityConfig | None = None,
    at=None,
    mask=None,
    pairs: pd.DataFrame | None = None,
    levels_from: pd.Timestamp | None = None,
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
    levels_from
        The first time at which the full and empty levels exist (``rests.levels_from``).
        Taken from ``rests`` (or ``frame``) when they are given; with ``pairs`` alone it
        must be passed to be known. Before it, the status says that the record has not
        enough history yet to set the levels.

    Returns
    -------
    DataFrame, one row per time: ``capacity_ah``, ``capacity_ah_low``, ``capacity_ah_high``,
    ``soh_capacity`` (with ``_low`` and ``_high``), ``reference_capacity_ah``,
    ``offset_current_a`` (negative = the battery loses charge the meter does not see),
    ``n_pairs``, ``n_full_empty``, ``n_same_kind`` (pairs in memory, decayed), ``birge``
    (reduced chi-square: below 1 when the pairs scatter less than the settings'
    ``sigma_q_rel`` and ``sigma_soc_edge`` assume), ``rho`` (lag-1 correlation of the
    weighted residuals), ``status``; version 0.2 adds ``dof`` (the band's degrees of
    freedom), ``offset_dof`` (those of the offset's band, for ``charge_loss``),
    ``trend_ah_per_year``, the voltage terms with their standard errors, ``ref_full_v``
    and ``ref_empty_v`` (the rest voltages the capacity is read at; NaN without that
    voltage term) and ``terms``. Both diagnostics are reported only; the band comes
    from the residuals themselves (``besslib.soh._core``). ``attrs`` hold the pairs and
    the configuration hash.

    With ``config.plausibility`` (version 0.2, rule 1), a date at which the empty level
    lies at or above the full level has the status ``LEVELS_CROSSED``: full and empty no
    longer mark the two ends of the charge window. The levels at a date are those of the
    last rest of ``rests`` at or before it, so the rule can only be checked when ``rests``
    is given (also together with ``pairs``). With ``pairs`` alone the levels are not known,
    and the rule is not checked.

    The two range rules of version 0.3 (``CapacityConfig``) need ``rests`` in the same way:
    ``INSIDE_LIMITS`` when ``spec`` gives ``voltage_max_v`` and ``voltage_min_v`` and a level
    lies too far inside them, ``ABOVE_FULL`` when charges end above the full level. Without
    documented limits nothing tells a pack that never reaches full from one that does:
    full and empty are then the ends of the observed window, and the Annex VII record says
    so (``besslib.soh.record``).
    """
    config = config or CapacityConfig()
    if pairs is None:
        if rests is None:
            if frame is None:
                raise ValueError("give a frame, rests or pairs")
            rests = find_rests(frame, spec, config)
        pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    if levels_from is None and rests is not None:
        levels_from = rest_levels_from(rests, config)
    ready = float(days([levels_from])[0]) if levels_from is not None and pd.notna(levels_from) else -np.inf
    if at is None:
        times = pairs["t_end"] if len(pairs) else (frame.index if frame is not None else [])
        if len(times) == 0:
            raise ValueError("no data to estimate from")
        at = pd.date_range(pd.Timestamp(min(times)).normalize(), pd.Timestamp(max(times)), freq="ME")
    at = pd.DatetimeIndex(at)
    z = _z(config.level)
    crossed = _levels_crossed(rests, days(at)) if config.plausibility and rests is not None else None
    inside = above = None
    if rests is not None:
        inside = _inside_limits(rests, spec, config, days(at))
        above = _above_full(rests, config, days(at))
    rows = []
    for k, t in enumerate(days(at)):
        if t < ready:
            est = _core.Estimate()
            est.status = _core.NO_LEVELS
        elif crossed is not None and crossed[k]:
            est = _core.Estimate()
            est.status = _core.LEVELS_CROSSED
        elif inside is not None and inside[k]:
            est = _core.Estimate()
            est.status = _core.INSIDE_LIMITS
        elif above is not None and above[k]:
            est = _core.Estimate()
            est.status = _core.ABOVE_FULL
        else:
            est = _solve(capacity_state_at(pairs, spec, config, float(t)), float(t), z, config)
        rows.append(estimate_row(est, spec))
    out = pd.DataFrame(rows, index=at)
    out.index.name = "time"
    out.attrs["pairs"] = pairs
    out.attrs["config_hash"] = config_hash(config)
    return out


def _levels_crossed(rests: pd.DataFrame, t_days: np.ndarray) -> np.ndarray:
    """Per date: the empty level lies at or above the full level, in the last rest at or before it."""
    full, empty = levels_at(rests, t_days)
    return np.isfinite(full) & np.isfinite(empty) & (empty >= full)


def levels_at(rests: pd.DataFrame, t_days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The full and the empty level (V) at each date: those of the last rest at or before it (NaN if none)."""
    nan = np.full(len(t_days), np.nan)
    if "level_full_v" not in rests.columns or len(rests) == 0:
        return nan, nan.copy()
    table = rests.sort_values("start")
    last = np.searchsorted(days(table["start"]), t_days, side="right") - 1
    pick = np.maximum(last, 0)
    full = np.where(last >= 0, table["level_full_v"].to_numpy(float)[pick], np.nan)
    empty = np.where(last >= 0, table["level_empty_v"].to_numpy(float)[pick], np.nan)
    return full, empty


def limit_gaps(rests: pd.DataFrame, spec: PackSpec, t_days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far the full level lies below ``spec.voltage_max_v`` and the empty level above
    ``spec.voltage_min_v`` at each date, as shares of the documented window (NaN without
    both limits or without a level)."""
    nan = np.full(len(t_days), np.nan)
    if spec.voltage_max_v is None or spec.voltage_min_v is None:
        return nan, nan.copy()
    window = float(spec.voltage_max_v) - float(spec.voltage_min_v)
    if not window > 0.0:
        raise ValueError("voltage_max_v must lie above voltage_min_v")
    full, empty = levels_at(rests, t_days)
    return (float(spec.voltage_max_v) - full) / window, (empty - float(spec.voltage_min_v)) / window


def _inside_limits(rests: pd.DataFrame, spec: PackSpec, config: CapacityConfig, t_days: np.ndarray) -> np.ndarray | None:
    """Per date: a level lies more than ``limits_share`` of the documented window inside it."""
    if config.limits_share <= 0.0:
        return None
    full, empty = limit_gaps(rests, spec, t_days)
    with np.errstate(invalid="ignore"):
        return (full > config.limits_share) | (empty > config.limits_share)


def _above_full(rests: pd.DataFrame, config: CapacityConfig, t_days: np.ndarray) -> np.ndarray | None:
    """Per date: at least ``above_full_min_rests`` accepted rests after a charge, in the trailing
    ``level_window_days``, end more than ``above_full_share`` of the span above the full level."""
    if config.above_full_min_rests <= 0 or "level_full_v" not in rests.columns:
        return None
    full, empty = levels_at(rests, t_days)
    bar = full + config.above_full_share * (full - empty)
    ends = rests[rests["accepted"].to_numpy(dtype=bool) & (rests["direction"] == "charge").to_numpy()]
    t = days(ends["start"])
    v = ends["edge_v"].to_numpy(float)
    out = np.zeros(len(t_days), dtype=bool)
    for k in range(len(t_days)):
        if not (np.isfinite(bar[k]) and full[k] > empty[k]):
            continue
        near = (t <= t_days[k]) & (t > t_days[k] - config.level_window_days)
        out[k] = int((v[near] > bar[k]).sum()) >= config.above_full_min_rests
    return out


def _terms(est: _core.Estimate) -> str:
    """The parameters a solve used, e.g. ``C+beta+r+gamma_full+gamma_empty``; empty when it stopped before."""
    if est.p == 0:
        return ""
    names = ["C", "beta"]
    if est.terms & _core.TERM_TREND:
        names.append("r")
    if est.terms & _core.TERM_FULL:
        names.append("gamma_full")
    if est.terms & _core.TERM_EMPTY:
        names.append("gamma_empty")
    if est.terms & _core.TERM_FULL2:
        names.append("gamma_full2")
    if est.terms & _core.TERM_EMPTY2:
        names.append("gamma_empty2")
    return "+".join(names)


def estimate_row(est: _core.Estimate, spec: PackSpec) -> dict:
    """One output row from a core estimate. The values are NaN unless the status is ``estimated``."""
    ref = spec.reference_capacity_ah
    ok = est.status == _core.OK
    nan = float("nan")
    cap = est.theta[_core.I_C] if ok else nan
    low = est.low if ok else nan
    high = est.high if ok else nan
    return {
        "capacity_ah": cap,
        "capacity_ah_low": low,
        "capacity_ah_high": high,
        "soh_capacity": cap / ref,
        "soh_capacity_low": low / ref,
        "soh_capacity_high": high / ref,
        "reference_capacity_ah": ref,
        "offset_current_a": -est.theta[_core.I_BETA] / 24.0 if ok else nan,
        "offset_current_sigma_a": est.sigma[_core.I_BETA] / 24.0 if ok else nan,
        "n_pairs": est.m,
        "n_full_empty": est.m_cross,
        "n_same_kind": est.m_same,
        "birge": est.birge,
        "rho": est.rho,
        "status": _core.STATUS_TEXT[est.status],
        "dof": est.dof if ok else nan,
        "offset_dof": est.dof_beta if ok else nan,
        "ref_full_v": est.u_full if ok else nan,
        "ref_empty_v": est.u_empty if ok else nan,
        "trend_ah_per_year": est.theta[_core.I_RATE] if ok else nan,
        "gamma_full_ah_per_v": est.theta[_core.I_GF] if ok else nan,
        "gamma_full_sigma_ah_per_v": est.sigma[_core.I_GF] if ok else nan,
        "gamma_empty_ah_per_v": est.theta[_core.I_GE] if ok else nan,
        "gamma_empty_sigma_ah_per_v": est.sigma[_core.I_GE] if ok else nan,
        "gamma_full2_ah_per_v2": est.theta[_core.I_GF2] if ok else nan,
        "gamma_full2_sigma_ah_per_v2": est.sigma[_core.I_GF2] if ok else nan,
        "gamma_empty2_ah_per_v2": est.theta[_core.I_GE2] if ok else nan,
        "gamma_empty2_sigma_ah_per_v2": est.sigma[_core.I_GE2] if ok else nan,
        "terms": _terms(est),
    }


def fade_rate(
    pairs: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    n_boot: int = 500,
    seed: int = 0,
) -> dict:
    """The fade rate over the whole record: the capacity fit without forgetting.

    ``q = (c0 + r y) x + beta dt`` over all pairs, ``y`` in years since the first pair;
    with ``config.voltage_terms`` also the voltage terms, each left out
    when its kind has fewer than ``min_same`` pairs (``besslib.soh._core``). The rate is
    ``r``. With ``config.plausibility`` a full-to-empty or empty-to-full pair of the
    wrong sign anywhere in the record makes the rate ``not possible`` (the fit has no
    forgetting, so its memory is the whole record).

    The band is the core's: a Student-t interval on the lag-1 sandwich standard error,
    which allows for scatter that differs between kinds of pair and for the correlation
    of pairs that share a rest (``besslib.soh._core``). A bootstrap that resamples whole
    calendar months of pairs (``n_boot`` times, fixed ``seed``; the same terms as the
    core's solve) is reported next to it as a check (``rate_boot_low``,
    ``rate_boot_high``): it also keeps any correlation within a month (temperature,
    season, operation) that the core does not model. A band much narrower than the
    bootstrap's points to such correlation. The two are not merged: the union of two
    90 % intervals holds the truth more often than 90 %.

    Returns, in points of ``spec.reference_capacity_ah``: ``rate_points_per_year``
    (negative = fade) with ``_low`` and ``_high``; the same per 100 equivalent full
    cycles (charge throughput / (2 x reference capacity)); ``capacity_start_ah``;
    ``offset_current_a``; ``n_pairs``; ``span_years``; ``status``; the diagnostics
    ``birge`` (reduced chi-square of the weighted fit) and ``rho`` (lag-1 correlation
    of its weighted residuals); ``dof`` (the band's degrees of freedom), ``terms`` (the
    parameters solved for) and the voltage terms ``gamma_full_ah_per_v`` and
    ``gamma_empty_ah_per_v`` (NaN when not used).
    """
    config = config or CapacityConfig()
    ref = spec.reference_capacity_ah
    t, x, dt, q, sq, sx, linked, kind, u_a, u_b = _columns(pairs)
    out = {"reference_capacity_ah": ref, "config_hash": config_hash(config), "n_pairs": int(len(t))}
    t0 = float(t[0]) if len(t) else 0.0
    state = _core.fade_state(
        spec.nominal_capacity_ah, t0, _VOLTAGE_CODES[config.voltage_terms], int(config.band_forgetting), int(config.plausibility)
    )
    for k in range(len(t)):
        _core.update(
            state, float(t[k]), float(x[k]), float(dt[k]), float(q[k]), float(sq[k]), float(sx[k]), int(linked[k]),
            float(u_a[k]), float(u_b[k]), int(kind[k]),
        )
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
    boot = _bootstrap(pairs, spec, t, t0, n_boot, seed, est.terms) if n_boot > 0 else np.array([])
    boot_low = boot_high = np.nan
    if n_boot > 0 and len(boot) >= 0.9 * n_boot:
        a = (1.0 - config.level) / 2.0
        boot_low, boot_high = np.quantile(boot, [a, 1.0 - a])
    years = (t[-1] - t[0]) / 365.25
    throughput = float(pairs["throughput_ah"].abs().sum())
    efc_per_year = throughput / (2.0 * ref) / years if years > 0 else np.nan
    out.update(
        capacity_start_ah=est.theta[_core.I_C],
        rate_points_per_year=est.theta[_core.I_RATE] * pts,
        rate_points_per_year_low=low * pts,
        rate_points_per_year_high=high * pts,
        rate_t_band_low=est.low * pts,
        rate_t_band_high=est.high * pts,
        rate_boot_low=boot_low * pts,
        rate_boot_high=boot_high * pts,
        offset_current_a=-est.theta[_core.I_BETA] / 24.0,
        birge=est.birge,
        rho=est.rho,
        efc_per_year=efc_per_year,
    )
    k = 100.0 / efc_per_year if np.isfinite(efc_per_year) and efc_per_year > 0 else np.nan
    out.update(
        rate_points_per_100_efc=out["rate_points_per_year"] * k,
        rate_points_per_100_efc_low=out["rate_points_per_year_low"] * k,
        rate_points_per_100_efc_high=out["rate_points_per_year_high"] * k,
        dof=est.dof,
        terms=_terms(est),
        gamma_full_ah_per_v=est.theta[_core.I_GF],
        gamma_empty_ah_per_v=est.theta[_core.I_GE],
        gamma_full2_ah_per_v2=est.theta[_core.I_GF2],
        gamma_empty2_ah_per_v2=est.theta[_core.I_GE2],
    )
    return out


def _bootstrap(
    pairs: pd.DataFrame, spec: PackSpec, t: np.ndarray, t0: float, n_boot: int, seed: int, terms: int = 0
) -> np.ndarray:
    """Fade rates (Ah per year) from resampled calendar months of pairs, by weighted least squares.

    ``terms`` (``_core.TERM_`` bits) adds the voltage columns the core's solve used.
    """
    x = pairs["x"].to_numpy(float)
    y = (t - t0) / 365.25
    cols = [x, x * y, pairs["dt_days"].to_numpy(float)]
    if terms & (_core.TERM_FULL | _core.TERM_EMPTY):
        du_f, du_e, _, _ = voltage_terms(pairs)
    if terms & _core.TERM_FULL:
        cols.append(du_f)
    if terms & _core.TERM_EMPTY:
        cols.append(du_e)
    if terms & (_core.TERM_FULL2 | _core.TERM_EMPTY2):
        du_f2, du_e2 = quadratic_terms(pairs)
    if terms & _core.TERM_FULL2:
        cols.append(du_f2)
    if terms & _core.TERM_EMPTY2:
        cols.append(du_e2)
    rows = np.column_stack(cols)
    p = rows.shape[1]
    q = pairs["q_ah"].to_numpy(float)
    w = 1.0 / (pairs["sigma_q_ah"].to_numpy(float) ** 2 + (spec.nominal_capacity_ah * pairs["sigma_x"].to_numpy(float)) ** 2)
    month = np.unique(pd.DatetimeIndex(pairs["t_end"]).to_period("M").asi8, return_inverse=True)[1]
    nmonth = month.max() + 1
    am = np.zeros((nmonth, p, p))
    hm = np.zeros((nmonth, p))
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


#: The statuses of the plausibility rules 1 to 3 (version 0.2).
PLAUSIBILITY_STATUSES = (_core.LEVELS_CROSSED, _core.OUT_OF_RANGE, _core.WRONG_SIGN)

#: The statuses of the range rules (version 0.3): full or empty is not the pack's.
RANGE_STATUSES = (_core.INSIDE_LIMITS, _core.ABOVE_FULL)


def implausible_dates(capacity: pd.DataFrame) -> pd.DataFrame:
    """The rows of a capacity table that hold a plausibility rule (version 0.2, rules 1 to 3).

    Rule 1: levels crossed. Rule 2: out of range (status 9), or an ``estimated`` row whose
    value or low end is at or below zero. Rule 3: a pair of the wrong sign in memory. The
    range rules of version 0.3 are numbered 5 (a level too far inside the documented limits)
    and 6 (charges end above the full level); rule 4 is ``fade_rate_checked``'s own.
    Returns the index (``time``) and ``rule`` (1, 2 or 3) of each such row.
    """
    status = capacity["status"]
    rule = pd.Series(0, index=capacity.index)
    rule[status == _core.STATUS_TEXT[_core.LEVELS_CROSSED]] = 1
    nonpositive = (status == _core.STATUS_TEXT[_core.OK]) & (
        (capacity["capacity_ah"] <= 0) | (capacity["capacity_ah_low"] <= 0)
    )
    rule[(status == _core.STATUS_TEXT[_core.OUT_OF_RANGE]) | nonpositive] = 2
    rule[status == _core.STATUS_TEXT[_core.WRONG_SIGN]] = 3
    rule[status == _core.STATUS_TEXT[_core.INSIDE_LIMITS]] = 5
    rule[status == _core.STATUS_TEXT[_core.ABOVE_FULL]] = 6
    return rule[rule > 0].rename("rule").to_frame()


def fade_rate_checked(
    capacity: pd.DataFrame,
    pairs: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    n_boot: int = 500,
    seed: int = 0,
) -> dict:
    """``fade_rate``, but ``not possible`` for a record that holds a plausibility rule (rule 4).

    ``capacity`` is the record's capacity table (``estimate_capacity``, one row per month
    end). If any row holds rule 1, 2, 3, 5 or 6 (``implausible_dates``) the rate is NaN and the
    status names the first such date and its rule (``first_implausible_date``,
    ``first_implausible_rule``); with ``config.plausibility`` off, or no such row, the
    result is ``fade_rate``'s, unchanged.
    """
    config = config or CapacityConfig()
    out = fade_rate(pairs, spec, config, n_boot=n_boot, seed=seed)
    if not config.plausibility:
        return out
    bad = implausible_dates(capacity)
    if bad.empty:
        return out
    first, rule = bad.index[0], int(bad["rule"].iloc[0])
    names = {1: "the levels have crossed", 2: "the estimate is out of range", 3: "a pair has the wrong sign",
             5: "a level lies too far inside the documented voltage limits", 6: "charges end above the full level"}
    for key in list(out):
        if key.startswith("rate_"):
            out[key] = np.nan
    out.update(
        status=f"not possible from this data: the capacity is implausible at {pd.Timestamp(first):%Y-%m-%d} "
        f"({names[rule]})",
        first_implausible_date=pd.Timestamp(first),
        first_implausible_rule=rule,
    )
    return out
