"""Tests of the estimator core (``besslib.soh._core``) and its saved test vectors.

The vectors in ``tests/vectors/`` are the core's inputs and expected outputs; the C port
(A3) reads the same files. Rewrite them only on purpose:

    python tests/test_core.py --write-vectors
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.capacity import _solve, capacity_state_at, run_core
from besslib.soh.config import CapacityConfig

VECTORS = Path(__file__).parent / "vectors"
Z90 = 1.6448536269514722
SPEC = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.0, cells_in_series=14)
EPOCH = pd.Timestamp("2000-01-01")


def chain_pairs(seed=3, n=600, cap=100.0, beta=-1.2, sigma_soc=0.02, breaks=(150, 400), sigma_empty=None):
    """Pairs from a random walk over full and empty rests, with shared-rest errors.

    ``sigma_empty`` (default ``sigma_soc``) is the error of an empty rest; the weights
    always assume ``sigma_soc`` for both kinds.
    """
    rng = np.random.default_rng(seed)
    t = 7000.0 + np.cumsum(rng.uniform(0.2, 1.5, n + 1))
    full = rng.integers(0, 2, n + 1)
    sig = np.where(full == 1, sigma_soc, sigma_soc if sigma_empty is None else sigma_empty)
    soc_err = rng.normal(0.0, 1.0, n + 1) * sig  # error of each rest, shared by two pairs
    counter = cap * (full + soc_err) + beta * (t - t[0])
    linked = np.ones(n, dtype=int)
    linked[0] = 0
    for b in breaks:
        linked[b] = 0
    return pd.DataFrame(
        {
            "t_end": EPOCH + pd.to_timedelta(t[1:], unit="D"),
            "x": (full[1:] - full[:-1]).astype(float),
            "dt_days": np.diff(t),
            "q_ah": np.diff(counter),
            "sigma_q_ah": 0.3,
            "sigma_x": np.sqrt(2.0) * sigma_soc,
            "linked": linked,
        }
    )


def feed(pairs, fit, at_days, gain=0.005, rounder=None):
    for row in pairs.itertuples():
        td = (row.t_end - EPOCH).total_seconds() / 86400.0
        _core.update(fit, td, row.x, row.dt_days, row.q_ah, row.sigma_q_ah, row.sigma_x, row.linked)
        if rounder is not None:
            rounder(fit)
    return _core.solve(fit, at_days, Z90, gain, 3.0, 2.0, 1.0)


def end_day(pairs):
    return (pairs["t_end"].iloc[-1] - EPOCH).total_seconds() / 86400.0


def test_exact_recovery_without_noise():
    pairs = chain_pairs(sigma_soc=0.0)
    pairs["sigma_x"] = 0.02
    est = feed(pairs, _core.capacity_state(100.0, 0.0), end_day(pairs))
    assert est.status == _core.OK
    assert est.theta[0] == pytest.approx(100.0, abs=1e-8)
    assert est.theta[1] == pytest.approx(-1.2, abs=1e-9)
    assert est.chi2 == pytest.approx(0.0, abs=1e-6)


def test_shared_rests_give_negative_lag_correlation():
    pairs = chain_pairs(seed=9, n=3000, breaks=())
    est = feed(pairs, _core.capacity_state(100.0, 0.0), end_day(pairs))
    # Pair errors are differences of rest errors: their lag-1 correlation is -0.5.
    assert est.rho == pytest.approx(-0.5, abs=0.06)


def test_band_holds_the_truth_about_90_percent():
    held = 0
    runs = 200
    for seed in range(runs):
        pairs = chain_pairs(seed=seed, n=150, breaks=(70,))
        est = feed(pairs, _core.capacity_state(100.0, 0.0), end_day(pairs), gain=0.0)
        held += est.low <= 100.0 <= est.high
    assert 0.84 <= held / runs <= 0.96


def test_band_follows_the_real_scatter_of_each_kind():
    # Full rests scatter 2 % as the weights assume, empty rests only 0.2 %. The band must
    # come from the residuals: not held at the assumed size (it would hold the truth
    # nearly always) and not scaled by one common factor, which the precise
    # empty-to-empty pairs would pull down.
    held = 0
    runs = 200
    for seed in range(runs):
        pairs = chain_pairs(seed=seed, n=150, breaks=(70,), sigma_soc=0.02, sigma_empty=0.002)
        pairs["sigma_x"] = np.sqrt(2.0) * 0.02
        est = feed(pairs, _core.capacity_state(100.0, 0.0), end_day(pairs), gain=0.0)
        held += est.low <= 100.0 <= est.high
        assert est.birge < 1.0
    assert 0.84 <= held / runs <= 0.96


def test_batch_path_equals_core():
    pairs = chain_pairs()
    config = CapacityConfig(tau_days=60.0)
    at = pairs["t_end"].iloc[[50, 200, 450, 599]] + pd.Timedelta(hours=6)
    core = run_core(pairs, SPEC, config, at)
    for when, c in zip(at, core):
        td = (when - EPOCH).total_seconds() / 86400.0
        b = _solve(capacity_state_at(pairs, SPEC, config, td), td, Z90, config)
        assert b.status == c.status == _core.OK
        for k in range(2):
            assert b.theta[k] == pytest.approx(c.theta[k], rel=1e-9)
            assert b.sigma[k] == pytest.approx(c.sigma[k], rel=1e-9)
        assert b.rho == pytest.approx(c.rho, rel=1e-9, abs=1e-12)


def _to_f32(state):
    def r(v):
        return struct.unpack("f", struct.pack("f", v))[0] if math.isfinite(v) and abs(v) < 3e38 else v

    for name in state.__slots__:
        v = getattr(state, name)
        if isinstance(v, float):
            setattr(state, name, r(v))
        elif isinstance(v, list):
            setattr(state, name, [r(e) for e in v])


def test_float32_state_gives_the_same_capacity():
    pairs = chain_pairs(seed=5)
    td = end_day(pairs) + 0.1
    e64 = feed(pairs, _core.capacity_state(100.0, 90.0), td)
    e32 = feed(pairs, _core.capacity_state(100.0, 90.0), td, rounder=_to_f32)
    assert e32.theta[0] == pytest.approx(e64.theta[0], rel=1e-4)
    assert e32.high - e32.low == pytest.approx(e64.high - e64.low, rel=1e-2)


def test_forgetting_widens_the_band_but_keeps_the_value():
    pairs = chain_pairs(seed=7)
    now = feed(pairs, _core.capacity_state(100.0, 90.0), end_day(pairs))
    later = feed(pairs, _core.capacity_state(100.0, 90.0), end_day(pairs) + 30.0)
    assert later.theta[0] == pytest.approx(now.theta[0], rel=1e-12)
    assert later.high - later.low > now.high - now.low


def test_status_codes():
    pairs = chain_pairs(seed=1, n=60, breaks=())
    same = pairs[pairs["x"] == 0]
    assert feed(same, _core.capacity_state(100.0, 90.0), end_day(same)).status == _core.TOO_FEW_FULL_EMPTY
    cross = pairs[pairs["x"] != 0]
    assert feed(cross, _core.capacity_state(100.0, 90.0), end_day(cross)).status == _core.TOO_FEW_SAME_KIND
    stale = feed(pairs, _core.capacity_state(100.0, 30.0), end_day(pairs) + 31.0)
    assert stale.status == _core.STALE  # pairs in memory, but none in the last 30 days
    assert "not possible from this data" in _core.STATUS_TEXT[stale.status]
    # The fade fit needs its full-to-empty pairs to span min_span_years (1 year here);
    # these pairs span about two months.
    short = feed(pairs, _core.fade_state(100.0, end_day(pairs) - 100.0), end_day(pairs))
    assert short.status == _core.SHORT_SPAN
    assert 0.0 < short.span_years < 1.0
    # Every code has its own text; the caller sets NO_LEVELS (the core never sees rests).
    assert len(set(_core.STATUS_TEXT.values())) == len(_core.STATUS_TEXT)
    assert all(text.startswith("not possible from this data") for code, text in _core.STATUS_TEXT.items() if code != _core.OK)


@pytest.mark.parametrize("nu, expected", [(5, 2.0150), (10, 1.8125), (30, 1.6973), (120, 1.6577)])
def test_t_quantile(nu, expected):
    assert _core.t_quantile(Z90, nu) == pytest.approx(expected, abs=2e-3)


def test_fade_exact_recovery():
    pairs = chain_pairs(sigma_soc=0.0, n=1200, breaks=())
    t = (pairs["t_end"] - EPOCH).dt.total_seconds().to_numpy() / 86400.0
    t0 = t[0]
    years = (t - t0) / 365.25
    pairs["q_ah"] = (120.0 - 2.4 * years) * pairs["x"] - 0.5 * pairs["dt_days"]
    pairs["sigma_x"] = 0.02
    est = feed(pairs, _core.fade_state(100.0, t0), end_day(pairs))
    assert est.status == _core.OK
    assert est.theta[1] == pytest.approx(-2.4, abs=1e-8)
    assert est.theta[0] == pytest.approx(120.0, abs=1e-8)
    assert est.theta[2] == pytest.approx(-0.5, abs=1e-9)


# --- saved vectors ---------------------------------------------------------------------

_OUT = ("status", "theta0", "theta1", "sigma0", "sigma1", "low", "high", "chi2", "dof", "birge", "rho", "m")


def _vector_outputs(inputs: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tau in sorted(queries["tau_days"].unique()):
        state = _core.capacity_state(100.0, float(tau))
        k = 0
        for at in queries.loc[queries["tau_days"] == tau, "t_days"]:
            while k < len(inputs) and inputs["t_days"].iloc[k] <= at:
                r = inputs.iloc[k]
                _core.update(state, r["t_days"], r["x"], r["dt_days"], r["q_ah"], r["sigma_q_ah"], r["sigma_x"], int(r["linked"]))
                k += 1
            e = _core.solve(state, float(at), Z90, 0.005, 3.0, 2.0, 1.0)
            rows.append({
                "tau_days": tau, "t_days": at, "status": e.status, "theta0": e.theta[0], "theta1": e.theta[1],
                "sigma0": e.sigma[0], "sigma1": e.sigma[1], "low": e.low, "high": e.high, "chi2": e.chi2,
                "dof": e.dof, "birge": e.birge, "rho": e.rho, "m": e.m,
            })
    return pd.DataFrame(rows)


def _vector_inputs():
    pairs = chain_pairs(seed=11, n=300, breaks=(90, 200))
    inputs = pd.DataFrame(
        {
            "t_days": (pairs["t_end"] - EPOCH).dt.total_seconds() / 86400.0,
            "x": pairs["x"],
            "dt_days": pairs["dt_days"],
            "q_ah": pairs["q_ah"],
            "sigma_q_ah": pairs["sigma_q_ah"],
            "sigma_x": pairs["sigma_x"],
            "linked": pairs["linked"],
        }
    )
    at = inputs["t_days"].iloc[[5, 40, 89, 90, 150, 250, 299]].to_numpy() + 0.25
    queries = pd.DataFrame([(tau, a) for tau in (0.0, 30.0, 90.0) for a in at], columns=["tau_days", "t_days"])
    return inputs, queries


def write_vectors() -> None:
    inputs, queries = _vector_inputs()
    VECTORS.mkdir(exist_ok=True)
    for old in VECTORS.glob("capacity_core_*.csv"):
        old.unlink()
    inputs.to_csv(VECTORS / "capacity_core_pairs.csv", index=False, float_format="%.17g")
    _vector_outputs(inputs, queries).to_csv(VECTORS / "capacity_core_expected.csv", index=False, float_format="%.17g")


def test_saved_vectors():
    inputs = pd.read_csv(VECTORS / "capacity_core_pairs.csv")
    expected = pd.read_csv(VECTORS / "capacity_core_expected.csv")
    got = _vector_outputs(inputs, expected[["tau_days", "t_days"]])
    assert (got["status"].to_numpy() == expected["status"].to_numpy()).all()
    for col in _OUT[1:]:
        np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-9, equal_nan=True)


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_vectors()
    print(f"wrote {VECTORS}")


# --- the through-origin ratio fit --------------------------------------------------------

from besslib.soh import _ratio  # noqa: E402


def ratio_events(seed=3, n=300, k=0.9, p_link=0.8, s_point=0.01, s_noise=0.01, growth=0.0, bad=()):
    """Events ``(a, b)`` with ``b = k (1 + growth y) a`` plus two kinds of error.

    ``s_noise sqrt(a)``: the event's own error. ``s_point``: the error of an end point; a
    linked event starts at the end point of the event before it (as a closed loop starts
    at the rest where the previous one ended), so the two share that error. ``bad``: the
    indices of events the core must refuse (``a = 0`` or ``b`` NaN, alternately).
    """
    rng = np.random.default_rng(seed)
    t = 7000.0 + np.cumsum(rng.uniform(0.3, 1.5, n))
    a = rng.uniform(0.5, 2.0, n)
    linked = (rng.uniform(size=n) < p_link).astype(int)
    linked[0] = 0
    end = rng.normal(0.0, s_point, n)
    fresh = rng.normal(0.0, s_point, n)
    start = np.where(linked == 1, np.r_[0.0, end[:-1]], fresh)
    y = (t - t[0]) / 365.25
    b = k * (1.0 + growth * y) * a + end - start + rng.normal(0.0, 1.0, n) * s_noise * np.sqrt(a)
    for j, i in enumerate(bad):
        if j % 2 == 0:
            a[i] = 0.0
        else:
            b[i] = np.nan
    return t, a, b, linked


def feed_ratio(state, t, a, b, linked, rounder=None):
    for i in range(len(t)):
        _core.ratio_update(state, float(t[i]), float(a[i]), float(b[i]), int(linked[i]))
        if rounder is not None:
            rounder(state)
    return state


def direct_sandwich(t, a, b, linked, at, tau, p, t0=0.0):
    """The same estimate and sandwich, from the residuals in two passes (plain numpy)."""
    keep = (a > 0) & np.isfinite(a) & np.isfinite(b)
    eff = (linked != 0) & keep & np.r_[False, keep[:-1]]
    t, a, b, eff = t[keep], a[keep], b[keep], eff[keep]
    sel = t <= at
    t, a, b, eff = t[sel], a[sel], b[sel], eff[sel]
    lam = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    u = np.column_stack([np.ones_like(t), (t - t0) / 365.25])[:, :p]
    amat = (lam * a)[:, None, None] * u[:, :, None] * u[:, None, :]
    amat = amat.sum(axis=0)
    theta = np.linalg.solve(amat, ((lam * b)[:, None] * u).sum(axis=0))
    e = b - a * (u @ theta)
    g = (lam * e)[:, None] * u  # the score of a weighted fit carries its weight
    meat = (g[:, :, None] * g[:, None, :]).sum(axis=0)
    cur = np.flatnonzero(eff)
    cross = g[cur][:, :, None] * g[cur - 1][:, None, :]
    meat += (cross + cross.transpose(0, 2, 1)).sum(axis=0)
    inv = np.linalg.inv(amat)
    m = lam.sum()
    return theta, inv @ meat @ inv * m / (m - p)


def direct_dof(t, a, b, linked, at, tau, p, t0=0.0):
    """The band's degrees of freedom from the residuals (plain numpy): (m - p) times the
    moment-matched share of the lag-1 term."""
    keep = (a > 0) & np.isfinite(a) & np.isfinite(b)
    eff = (linked != 0) & keep & np.r_[False, keep[:-1]]
    t, a, b, eff = t[keep], a[keep], b[keep], eff[keep]
    sel = t <= at
    t, a, b, eff = t[sel], a[sel], b[sel], eff[sel]
    lam = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    theta, _ = direct_sandwich(t, a, b, eff.astype(int), at, tau, p, t0)
    u = np.column_stack([np.ones_like(t), (t - t0) / 365.25])[:, :p]
    e = b - a * (u @ theta)
    cur = np.flatnonzero(eff)
    w = lam[cur] * lam[cur - 1]
    m2 = np.sum(lam**2)
    rho = (np.sum(w * e[cur] * e[cur - 1]) / w.sum()) / (np.sum(lam**2 * e**2) / m2)
    q = w.sum() / m2
    q2 = w[eff[cur - 1]].sum() / m2
    share = (1 + 2 * rho * q) ** 2 / (1 + 2 * q + 8 * rho * q + 4 * rho**2 * (q + q2))
    return (lam.sum() - p) * share, rho


RATIO_ARGS = dict(level=0.90, min_events=5.0, lower=0.0, upper=10.0)


def test_ratio_batch_equals_core():
    t, a, b, linked = ratio_events(bad=(0, 17, 18, 120))
    at = np.r_[t[0] - 1.0, t[[3, 50, 51, 160, 299]] + 0.25, t[-1] + 40.0]
    for tau in (0.0, 30.0, 180.0):
        batch = _ratio.estimate_ratio(t, a, b, linked, at, tau, **RATIO_ARGS)
        core = _ratio.run_ratio_core(t, a, b, linked, at, tau, **RATIO_ARGS)
        assert (batch["status"] == core["status"]).all()
        assert (batch["status"].iloc[2:] == _core.OK).all()
        for col in ("value", "sigma", "low", "high", "n_events", "n_links", "dof", "rho"):
            np.testing.assert_allclose(batch[col], core[col], rtol=1e-9, atol=1e-15, equal_nan=True)
    state = feed_ratio(_core.growth_state(t[0]), t, a, b, linked)
    one = _core.ratio_solve(state, t[-1], Z90, 5.0, 0.1, 0.0, 10.0)
    two = _ratio.estimate_growth(t, a, b, linked, t[0], t[-1], 0.90, 5.0, 0.1, 0.0, 10.0)
    assert one.status == two["status"] == _core.OK
    np.testing.assert_allclose([two["k0"], two["k1"]], one.theta, rtol=1e-9)
    np.testing.assert_allclose(np.ravel(two["cov"]), one.cov, rtol=1e-9)
    assert state.n_skipped == 4


def test_ratio_equals_direct_sandwich():
    t, a, b, linked = ratio_events(seed=4, bad=(30, 31), growth=0.05)
    for tau, at in ((0.0, t[-1]), (60.0, t[200] + 0.5)):
        est = _ratio.estimate_ratio(t, a, b, linked, [at], tau, **RATIO_ARGS).iloc[0]
        theta, cov = direct_sandwich(t, a, b, linked, at, tau, 1)
        assert est["value"] == pytest.approx(theta[0], rel=1e-12)
        use = (t <= at) & (a > 0) & np.isfinite(b)
        lam = np.exp(-(at - t[use]) / tau) if tau > 0 else 1.0
        assert est["value"] == pytest.approx(np.sum(lam * b[use]) / np.sum(lam * a[use]), rel=1e-12)  # k = sum lambda b / sum lambda a
        assert est["sigma"] ** 2 == pytest.approx(cov[0, 0], rel=1e-9)
        dof, rho = direct_dof(t, a, b, linked, at, tau, 1)
        assert est["dof"] == pytest.approx(dof, rel=1e-9)
        assert est["rho"] == pytest.approx(rho, rel=1e-9)
        assert est["dof"] < est["n_events"] - 1.0  # the lag-1 term costs degrees of freedom
    grow = _ratio.estimate_growth(t, a, b, linked, t[0], t[-1], 0.90, 5.0, 0.1, 0.0, 10.0)
    theta, cov = direct_sandwich(t, a, b, linked, t[-1], 0.0, 2, t0=t[0])
    np.testing.assert_allclose([grow["k0"], grow["k1"]], theta, rtol=1e-10)
    np.testing.assert_allclose(np.asarray(grow["cov"]), cov, rtol=1e-9)
    assert grow["dof"] == pytest.approx(direct_dof(t, a, b, linked, t[-1], 0.0, 2, t0=t[0])[0], rel=1e-9)
    # Without links the degrees of freedom are m - p, as in the capacity fit.
    free = _ratio.estimate_ratio(t, a, b, 0 * linked, [t[-1]], 60.0, **RATIO_ARGS).iloc[0]
    assert free["dof"] == pytest.approx(free["n_events"] - 1.0, rel=1e-12)


def test_ratio_exact_recovery_without_noise():
    t, a, b, linked = ratio_events(s_point=0.0, s_noise=0.0)
    est = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t, a, b, linked), t[-1], Z90, 5.0, 0.0, 0.0, 10.0)
    assert est.status == _core.OK
    assert est.theta[0] == pytest.approx(0.9, rel=1e-13)
    assert est.high - est.low == pytest.approx(0.0, abs=1e-12)  # a variance within rounding of zero is zero
    t, a, b, linked = ratio_events(s_point=0.0, s_noise=0.0, growth=0.05)
    est = _core.ratio_solve(feed_ratio(_core.growth_state(t[0]), t, a, b, linked), t[-1], Z90, 5.0, 0.1, 0.0, 10.0)
    assert est.status == _core.OK
    assert est.theta[0] == pytest.approx(0.9, rel=1e-12)
    assert est.theta[1] == pytest.approx(0.045, rel=1e-10)


def test_ratio_forgetting_widens_the_band_but_keeps_the_value():
    t, a, b, linked = ratio_events(seed=7)
    now = _core.ratio_solve(feed_ratio(_core.ratio_state(90.0), t, a, b, linked), t[-1], Z90, 5.0, 0.0, 0.0, 10.0)
    later = _core.ratio_solve(feed_ratio(_core.ratio_state(90.0), t, a, b, linked), t[-1] + 30.0, Z90, 5.0, 0.0, 0.0, 10.0)
    assert later.theta[0] == pytest.approx(now.theta[0], rel=1e-12)
    assert later.high - later.low > now.high - now.low


def test_ratio_status_codes():
    t, a, b, linked = ratio_events(seed=1, n=60)
    args = (Z90, 5.0, 0.0, 0.0, 10.0)
    few = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t[:4], a[:4], b[:4], linked[:4]), t[3], *args)
    assert few.status == _core.TOO_FEW_EVENTS and few.m == 4.0
    # Forgetting: 60 events, but 300 days later fewer than 5 remain in memory.
    stale = _core.ratio_solve(feed_ratio(_core.ratio_state(30.0), t, a, b, linked), t[-1] + 300.0, *args)
    assert stale.status == _core.TOO_FEW_EVENTS
    # m = p: no degree of freedom left for the band.
    one = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t[:1], a[:1], b[:1], linked[:1]), t[0], Z90, 1.0, 0.0, 0.0, 10.0)
    assert one.status == _core.NO_BAND
    # Out of range: below or at ``lower``, above ``upper``; theta says what came out.
    low = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t, a, -b, linked), t[-1], *args)
    assert low.status == _core.OUT_OF_RANGE and low.theta[0] < 0.0 and math.isnan(low.low)
    high = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t, a, b, linked), t[-1], Z90, 5.0, 0.0, 0.0, 0.5)
    assert high.status == _core.OUT_OF_RANGE
    # Residuals that alternate in sign along a linked chain: the lag-1 term exceeds the
    # diagonal, the variance is negative, and no floor replaces it.
    n = 40
    alt = 0.9 + 0.1 * (-1.0) ** np.arange(n)
    ones = np.ones(n)
    chain = np.r_[0, np.ones(n - 1, dtype=int)]
    neg = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), 7000.0 + np.arange(n), ones, alt, chain), 7000.0 + n, *args)
    assert neg.status == _core.NO_BAND and neg.rho < -0.9
    # The growth fit: span, singular, a fitted value out of range at the query time.
    gargs = (Z90, 5.0, 1.0, 0.0, 10.0)
    short = _core.ratio_solve(feed_ratio(_core.growth_state(t[0]), t, a, b, linked), t[-1], *gargs)
    assert short.status == _core.SHORT_SPAN and 0.0 < short.span_years < 1.0
    same = np.full(n, 7000.0)
    sing = _core.ratio_solve(feed_ratio(_core.growth_state(7000.0), same, ones, alt, chain * 0), 7000.0, Z90, 5.0, 0.0, 0.0, 10.0)
    assert sing.status == _core.SINGULAR
    tt = 7000.0 + 20.0 * np.arange(60)
    y = (tt - tt[0]) / 365.25
    falling = 1.0 - 0.4 * y
    fall = _core.ratio_solve(feed_ratio(_core.growth_state(tt[0]), tt, np.ones(60), falling, np.zeros(60)), tt[-1] + 400.0, *gargs)
    assert fall.status == _core.OUT_OF_RANGE and fall.theta[0] > 0.0
    for code in (_core.TOO_FEW_EVENTS, _core.OUT_OF_RANGE):
        assert _core.STATUS_TEXT[code].startswith("not possible from this data")
    assert len(set(_core.STATUS_TEXT.values())) == len(_core.STATUS_TEXT)


def test_ratio_band_holds_the_truth_about_90_percent():
    # Events that share end-point errors (as closed loops that share a rest; end-point
    # error as large as the event's own; 200 events) and events without links (as days;
    # 80 events). A chain without a band counts as not held.
    shares = {}
    runs = 300
    cases = (
        ("linked", 200, dict(p_link=1.0, s_point=0.01)),
        ("days", 80, dict(p_link=0.0, s_point=0.0, s_noise=0.02)),
        ("linked, end points twice the event error", 80, dict(p_link=1.0, s_point=0.02)),
    )
    for name, n, kw in cases:
        held = no_band = 0
        for seed in range(runs):
            t, a, b, linked = ratio_events(seed=seed, n=n, **kw)
            est = _core.ratio_solve(feed_ratio(_core.ratio_state(0.0), t, a, b, linked), t[-1], Z90, 5.0, 0.0, 0.0, 10.0)
            held += est.status == _core.OK and est.low <= 0.9 <= est.high
            no_band += est.status == _core.NO_BAND
        shares[name] = (held / runs, no_band / runs, held / (runs - no_band))
    print(f"ratio band coverage (held, no band, held among those with a band): {shares}")
    assert 0.85 <= shares["linked"][0] <= 0.95
    assert 0.85 <= shares["days"][0] <= 0.95
    # When the shared end-point errors dominate (lag-1 correlation near -0.43), the lag-1
    # term nearly cancels the diagonal: its estimate often comes out negative (no band,
    # never a floor), and where it is positive it rests on very few degrees of freedom,
    # so the bands that exist are wide and hold the truth more often than 90 %.
    held, no_band, banded = shares["linked, end points twice the event error"]
    assert no_band > 0.05
    assert banded >= 0.85


def forgetting_chain(seed, kind, years=3.0):
    """Daily events without links (as resistance days) or loops every 2-5 days that share
    their end points (as round trips between rests), with a constant true k = 0.9."""
    rng = np.random.default_rng(seed)
    if kind == "days":
        t = np.arange(0.0, 365.25 * years, 1.0)
        a = rng.uniform(5.0, 50.0, len(t))
        return t, a, 0.9 * a + 0.02 * np.sqrt(a) * rng.standard_normal(len(t)), np.zeros(len(t), dtype=int)
    t = np.cumsum(rng.uniform(2.0, 5.0, int(365.25 * years / 3.5)))
    a = rng.uniform(5.0, 15.0, len(t))
    point = 0.1 * rng.standard_normal(len(t) + 1)
    b = 0.9 * a + 0.05 * np.sqrt(a) * rng.standard_normal(len(t)) + point[1:] - point[:-1]
    linked = np.ones(len(t), dtype=int)
    linked[0] = 0
    return t, a, b, linked


def test_ratio_band_with_forgetting_holds_the_truth_about_90_percent():
    # 180 days of memory: about 180 days in memory, or about 51 loops whose shared end
    # points give a lag-1 correlation near -0.22. The band's standard error must match the
    # scatter of the estimates (the weight enters the band's sums twice), and the
    # degrees of freedom must carry the noise of the lag-1 term. A chain without a band
    # counts as not held.
    runs = 300
    shares = {}
    for kind in ("days", "loops"):
        held = no_band = 0
        errors, sigmas = [], []
        for seed in range(runs):
            t, a, b, linked = forgetting_chain(seed, kind)
            est = _ratio.estimate_ratio(t, a, b, linked, [t[-1]], 180.0, 0.90, 5.0, 0.0, 10.0).iloc[0]
            if est["status"] != _core.OK:
                no_band += 1
                continue
            held += est["low"] <= 0.9 <= est["high"]
            errors.append(est["value"] - 0.9)
            sigmas.append(est["sigma"])
        shares[kind] = (held / runs, no_band / runs, float(np.median(sigmas) / np.std(errors)))
    print(f"ratio band coverage with 180 days of memory (held, no band, median sigma / scatter): {shares}")
    for kind in ("days", "loops"):
        held, no_band, ratio = shares[kind]
        assert 0.85 <= held <= 0.95
        assert 0.9 <= ratio <= 1.1


def test_ratio_float32_state():
    t, a, b, linked = ratio_events(seed=5, n=600, k=0.02, s_noise=0.0005, s_point=0.0005)
    a = a * 2.0e4  # sums of squared current steps, A^2
    b = b * 2.0e4
    td = t[-1] + 0.1
    agree = {}
    for name, make, args in (
        ("ratio", lambda: _core.ratio_state(90.0), (Z90, 5.0, 0.0, 0.0, 10.0)),
        ("growth", lambda: _core.growth_state(t[0]), (Z90, 5.0, 0.1, 0.0, 10.0)),
    ):
        e64 = _core.ratio_solve(feed_ratio(make(), t, a, b, linked), td, *args)
        e32 = _core.ratio_solve(feed_ratio(make(), t, a, b, linked, rounder=_to_f32), td, *args)
        assert e64.status == e32.status == _core.OK
        main = 0 if name == "ratio" else 1
        agree[name] = (
            abs(e32.theta[0] / e64.theta[0] - 1.0),
            abs((e32.high - e32.low) / (e64.high - e64.low) - 1.0),
            abs(e32.theta[main] / e64.theta[main] - 1.0),
        )
    print(f"float32 relative differences (value, band width, main parameter): {agree}")
    for value, width, main in agree.values():
        assert value < 1e-5
        assert width < 1e-3
        assert main < 1e-4


# --- saved vectors of the ratio fit -------------------------------------------------------

_RATIO_OUT = ("status", "theta0", "theta1", "sigma0", "sigma1", "cov0", "cov1", "cov2", "cov3", "low", "high", "dof", "m", "m_lag", "rho", "span_years")


def _ratio_vector_inputs():
    t, a, b, linked = ratio_events(seed=11, n=300, k=0.02, s_noise=0.002, s_point=0.002, growth=0.05, bad=(7, 150))
    events = pd.DataFrame({"t_days": t, "a": a * 1.0e3, "b": b * 1.0e3, "linked": linked})
    at = t[[2, 40, 89, 90, 150, 250, 299]] + 0.25
    rows = [("ratio", tau, 0.0, q) for tau in (0.0, 30.0, 90.0) for q in at]
    rows += [("growth", 0.0, float(t[0]), q) for q in at]
    queries = pd.DataFrame(rows, columns=["fit", "tau_days", "t0_days", "t_days"])
    queries["z"] = Z90
    queries["min_events"] = 5.0
    queries["min_span_years"] = np.where(queries["fit"] == "growth", 0.25, 0.0)
    queries["lower"] = 0.0
    queries["upper"] = 1.0
    return events, queries


def _ratio_vector_outputs(events: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (fit, tau, t0), group in queries.groupby(["fit", "tau_days", "t0_days"], sort=False):
        state = _core.ratio_state(float(tau)) if fit == "ratio" else _core.growth_state(float(t0))
        k = 0
        for q in group.itertuples():
            while k < len(events) and events["t_days"].iloc[k] <= q.t_days:
                r = events.iloc[k]
                _core.ratio_update(state, float(r["t_days"]), float(r["a"]), float(r["b"]), int(r["linked"]))
                k += 1
            e = _core.ratio_solve(state, float(q.t_days), q.z, q.min_events, q.min_span_years, q.lower, q.upper)
            row = {"fit": fit, "tau_days": tau, "t0_days": t0, "t_days": q.t_days, "z": q.z, "min_events": q.min_events,
                   "min_span_years": q.min_span_years, "lower": q.lower, "upper": q.upper, "status": e.status}
            row.update({f"theta{i}": e.theta[i] for i in range(2)})
            row.update({f"sigma{i}": e.sigma[i] for i in range(2)})
            row.update({f"cov{i}": e.cov[i] for i in range(4)})
            row.update(low=e.low, high=e.high, dof=e.dof, m=e.m, m_lag=e.m_lag, rho=e.rho, span_years=e.span_years)
            rows.append(row)
    return pd.DataFrame(rows)


def write_ratio_vectors() -> None:
    events, queries = _ratio_vector_inputs()
    VECTORS.mkdir(exist_ok=True)
    events.to_csv(VECTORS / "ratio_core_events.csv", index=False, float_format="%.17g")
    _ratio_vector_outputs(events, queries).to_csv(VECTORS / "ratio_core_expected.csv", index=False, float_format="%.17g")


def test_saved_ratio_vectors():
    events = pd.read_csv(VECTORS / "ratio_core_events.csv")
    expected = pd.read_csv(VECTORS / "ratio_core_expected.csv")
    got = _ratio_vector_outputs(events, expected[["fit", "tau_days", "t0_days", "t_days", "z", "min_events", "min_span_years", "lower", "upper"]])
    assert (got["status"].to_numpy() == expected["status"].to_numpy()).all()
    assert set(expected["status"]) >= {_core.OK, _core.TOO_FEW_EVENTS, _core.SHORT_SPAN}
    for col in _RATIO_OUT[1:]:
        np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-15, equal_nan=True)


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_ratio_vectors()
    print(f"wrote the ratio vectors to {VECTORS}")
