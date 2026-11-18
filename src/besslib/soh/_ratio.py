"""The batch twin of the through-origin ratio fit in ``besslib.soh._core``.

``estimate_ratio`` and ``estimate_growth`` build the core's state from sums in one step
(numpy) and solve it with ``_core.ratio_solve``; ``run_ratio_core`` feeds the core one
event at a time (the form a BMS or the C port uses). The tests keep the two paths equal.

An event is two sums ``(a, b)`` with ``a > 0``; the estimate is ``k = sum lambda b /
sum lambda a`` with ``lambda = exp(-age / tau_days)``. The band is the lag-1 sandwich on
the scores ``lambda (b - k a)`` (Newey and West, Econometrica 55, 703 (1987),
doi:10.2307/1913610); see ``_core`` for the conventions, and for where they depart
from the capacity fit. Events
with ``a <= 0`` or a non-finite sum are refused by both paths in the same way.

Time axis: days since 2000-01-01 (``besslib.soh.capacity.days``). An event counts at a
query time if its ``t_days`` is at or before it. Events must be in time order.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.soh import _core


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def _events(t_days, a, b, linked):
    """The accepted events and their effective links, as the core sees them."""
    t = np.asarray(t_days, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    linked = np.asarray(linked, dtype=int)
    if not (len(t) == len(a) == len(b) == len(linked)):
        raise ValueError("t_days, a, b and linked must have the same length")
    if len(t) and (not np.isfinite(t).all() or (np.diff(t) < 0).any()):
        raise ValueError("t_days must be finite and in time order")
    kept = (a > 0) & np.isfinite(a) & np.isfinite(b)
    eff = (linked != 0) & kept & np.r_[False, kept[:-1]]
    return t[kept], a[kept], b[kept], eff[kept]


def _state_at(p: int, tau_days: float, t0_days: float, events, at: float) -> _core.Ratio:
    """The core state at ``at``, built from sums in one step (the batch path)."""
    t, a, b, eff = events
    state = _core.Ratio(p, tau_days if p == 1 else 0.0, t0_days)
    state.t_last = at
    if len(t) == 0:
        return state
    state.k_ref = float(b[0] / a[0])
    state.has_ref = 1
    sel = t <= at
    t, a, b, eff = t[sel], a[sel], b[sel], eff[sel]
    if len(t) == 0:
        return state
    tau = state.tau_days
    lam = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    y = (t - t0_days) / 365.25
    u = np.column_stack([np.ones_like(t), y])[:, :p]
    z = np.column_stack([u * a[:, None], b - state.k_ref * a])
    amat = np.einsum("n,nr,ns->rs", lam * a, u, u)
    h = np.einsum("n,nr->r", lam * z[:, p], u)
    s4 = np.einsum("n,nr,ns,ni,nj->rsij", lam * lam, u, u, z, z)
    cur = np.flatnonzero(eff)
    prev = cur - 1
    c = lam[cur] * lam[prev]
    uu = u[cur][:, :, None] * u[prev][:, None, :] + u[prev][:, :, None] * u[cur][:, None, :]
    s4l = np.einsum("n,nrs,ni,nj->rsij", c, uu, z[cur], z[prev])
    for r in range(p):
        state.h[r] = float(h[r])
        for s in range(p):
            state.a[2 * r + s] = float(amat[r, s])
            for i in range(p + 1):
                for j in range(p + 1):
                    state.s4[((r * 2 + s) * 3 + i) * 3 + j] = float(s4[r, s, i, j])
                    state.s4l[((r * 2 + s) * 3 + i) * 3 + j] = float(s4l[r, s, i, j])
    state.m = float(lam.sum())
    state.m_lag = float(np.sqrt(c).sum())
    state.m2 = float((lam * lam).sum())
    state.m2_lag = float(c.sum())
    follows = eff[cur - 1] if len(cur) else np.zeros(0, dtype=bool)  # the link before this one exists too
    state.m2_ll = float(c[follows].sum())
    state.n = int(len(t))
    state.y_min = float(y.min())
    state.y_max = float(y.max())
    return state


def _row(est: _core.RatioEstimate) -> dict:
    return {
        "value": est.theta[0] if est.status == _core.OK else np.nan,
        "sigma": est.sigma[0] if est.status == _core.OK else np.nan,
        "low": est.low,
        "high": est.high,
        "n_events": est.m,
        "n_links": est.m_lag,
        "dof": est.dof,
        "rho": est.rho,
        "status": int(est.status),
    }


def estimate_ratio(t_days, a, b, linked, at_days, tau_days, level, min_events, lower, upper) -> pd.DataFrame:
    """``k = sum lambda b / sum lambda a`` with its statistical band at each time in ``at_days``.

    One row per query, in the order given: ``value``, ``sigma``, ``low``, ``high``
    (statistical band only), ``n_events`` and ``n_links`` (decayed), ``dof``, ``rho``,
    ``status`` (a ``_core`` code). ``value`` and ``sigma`` are NaN unless the status is OK.
    """
    events = _events(t_days, a, b, linked)
    z = _z(level)
    rows = []
    for at in np.asarray(at_days, dtype=float):
        state = _state_at(1, tau_days, 0.0, events, float(at))
        rows.append(_row(_core.ratio_solve(state, float(at), z, min_events, 0.0, lower, upper)))
    return pd.DataFrame(rows)


def run_ratio_core(t_days, a, b, linked, at_days, tau_days, level, min_events, lower, upper) -> pd.DataFrame:
    """The same as ``estimate_ratio``, by one ``_core.ratio_update`` per event (the step-by-step path)."""
    t = np.asarray(t_days, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    linked = np.asarray(linked, dtype=int)
    _events(t, a, b, linked)  # the same input checks as the batch path
    at = np.asarray(at_days, dtype=float)
    z = _z(level)
    state = _core.ratio_state(tau_days)
    rows = [None] * len(at)
    k = 0
    for j in np.argsort(at, kind="stable"):
        tq = float(at[j])
        while k < len(t) and t[k] <= tq:
            _core.ratio_update(state, float(t[k]), float(a[k]), float(b[k]), int(linked[k]))
            k += 1
        rows[j] = _row(_core.ratio_solve(state, tq, z, min_events, 0.0, lower, upper))
    return pd.DataFrame(rows)


def estimate_growth(t_days, a, b, linked, t0_days, at_day, level, min_events, min_span_years, lower, upper) -> dict:
    """The growth fit ``b = (k0 + k1 y) a`` over every event up to ``at_day``, no forgetting.

    ``y`` is in years since ``t0_days``. Returns ``k0``, ``k1``, ``cov`` (2 x 2 list),
    ``sigma0``, ``sigma1``, ``dof``, ``n_events``, ``n_links``, ``span_years``, ``rho``,
    ``status`` (a ``_core`` code). The parameters are NaN unless the status is OK.
    """
    events = _events(t_days, a, b, linked)
    state = _state_at(2, 0.0, float(t0_days), events, float(at_day))
    est = _core.ratio_solve(state, float(at_day), _z(level), min_events, min_span_years, lower, upper)
    return growth_dict(est)


def growth_dict(est: _core.RatioEstimate) -> dict:
    """The output dict of ``estimate_growth`` from a core estimate."""
    ok = est.status == _core.OK
    nan = float("nan")
    return {
        "k0": est.theta[0] if ok else nan,
        "k1": est.theta[1] if ok else nan,
        "cov": [[est.cov[0], est.cov[1]], [est.cov[2], est.cov[3]]] if ok else [[nan, nan], [nan, nan]],
        "sigma0": est.sigma[0] if ok else nan,
        "sigma1": est.sigma[1] if ok else nan,
        "dof": est.dof,
        "n_events": est.m,
        "n_links": est.m_lag,
        "span_years": est.span_years,
        "rho": est.rho,
        "status": int(est.status),
    }
