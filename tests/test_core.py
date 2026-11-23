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


def _read_vectors(path: Path) -> pd.DataFrame:
    """The saved vectors, read exactly: before pandas 3 the default CSV parser can be a last
    bit off, which the fit's rho amplifies."""
    if int(pd.__version__.split(".")[0]) < 3:
        return pd.read_csv(path, float_precision="round_trip")
    return pd.read_csv(path)
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
    assert est.theta[_core.I_RATE] == pytest.approx(-2.4, abs=1e-8)
    assert est.theta[_core.I_C] == pytest.approx(120.0, abs=1e-8)
    assert est.theta[_core.I_BETA] == pytest.approx(-0.5, abs=1e-9)


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
    inputs.to_csv(VECTORS / "capacity_core_pairs.csv", index=False, float_format="%.17g")
    _vector_outputs(inputs, queries).to_csv(VECTORS / "capacity_core_expected.csv", index=False, float_format="%.17g")


def test_saved_vectors():
    inputs = _read_vectors(VECTORS / "capacity_core_pairs.csv")
    expected = _read_vectors(VECTORS / "capacity_core_expected.csv")
    got = _vector_outputs(inputs, expected[["tau_days", "t_days"]])
    assert (got["status"].to_numpy() == expected["status"].to_numpy()).all()
    for col in _OUT[1:]:
        np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-9, equal_nan=True)


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_vectors()
    print(f"wrote {VECTORS}")


# --- version 0.2: trend, voltage terms, forgetting squared, plausibility -------------------

KIND_TEXT = {_core.PAIR_FE: "F->E", _core.PAIR_EF: "E->F", _core.PAIR_FF: "F->F", _core.PAIR_EE: "E->E"}
V02 = CapacityConfig()  # the version 0.2 settings


def chain_v02(seed=21, n=400, cap=100.0, fade=-3.0, beta=-1.2, slope_f=2.0, slope_e=5.0, sigma_soc=0.02,
              sigma_u=0.002, sigma_meter=0.0, breaks=(150,), no_ee=None, wrong=(), collapse_days=None):
    """Core inputs from a random walk over full and empty rests (``t_days``, ..., ``kind``,
    ``u_start``, ``u_end``).

    Each rest misses its nominal state of charge by an error shared by its two pairs; its
    relaxed voltage moves with that error by ``slope_f`` (full) or ``slope_e`` (empty) volts
    per unit of state of charge, plus ``sigma_u`` of noise. The capacity changes by ``fade``
    Ah per year (or falls linearly to zero over ``collapse_days``). ``no_ee`` is a range of
    rests without empty-to-empty pairs; ``wrong`` lists pair indices from which the next
    full-to-empty pair gets a charge of the wrong sign.
    """
    rng = np.random.default_rng(seed)
    t = 7000.0 + np.cumsum(rng.uniform(0.2, 1.5, n + 1))
    full = rng.integers(0, 2, n + 1)
    if no_ee is not None:
        lo, hi = no_ee
        full[lo:hi] = np.resize([1, 1, 0], hi - lo)
    eps = rng.normal(0.0, sigma_soc, n + 1)
    u = np.where(full == 1, 57.4 + slope_f * eps, 44.8 + slope_e * eps) + rng.normal(0.0, sigma_u, n + 1)
    days_in = t - t[0]
    cap_t = cap + fade * days_in / 365.25 if collapse_days is None else cap * (1.0 - days_in / collapse_days)
    soc = full + eps
    x = (full[1:] - full[:-1]).astype(float)
    q = cap_t[1:] * (soc[1:] - soc[:-1]) + beta * np.diff(t) + rng.normal(0.0, 1.0, n) * sigma_meter
    kind = np.where(x < 0, _core.PAIR_FE, np.where(x > 0, _core.PAIR_EF, np.where(full[1:] == 1, _core.PAIR_FF, _core.PAIR_EE)))
    for i in wrong:
        k = i + int(np.argmax(kind[i:] == _core.PAIR_FE))
        q[k] = abs(q[k])
    linked = np.ones(n, dtype=int)
    linked[0] = 0
    for b in breaks:
        linked[b] = 0
    return pd.DataFrame(
        {
            "t_days": t[1:],
            "x": x,
            "dt_days": np.diff(t),
            "q_ah": q,
            "sigma_q_ah": 0.3,
            "sigma_x": np.sqrt(2.0) * 0.02,
            "linked": linked,
            "kind": kind,
            "u_start": u[:-1],
            "u_end": u[1:],
        }
    )


def du_of(rows):
    """The voltage terms written out from their definition (``_core`` docstring): each end
    gives its own kind's rest voltage minus that kind's first rest voltage. Returns
    ``du_f``, ``du_e``, and each end's voltage minus its reference."""
    kind = rows["kind"].to_numpy()
    fa = (kind == _core.PAIR_FE) | (kind == _core.PAIR_FF)
    fb = (kind == _core.PAIR_EF) | (kind == _core.PAIR_FF)
    ua, ub = rows["u_start"].to_numpy(float), rows["u_end"].to_numpy(float)
    seq_u, seq_f = np.column_stack([ua, ub]).ravel(), np.column_stack([fa, fb]).ravel()
    r0f, r0e = seq_u[seq_f][0], seq_u[~seq_f][0]
    da = ua - np.where(fa, r0f, r0e)
    db = ub - np.where(fb, r0f, r0e)
    du_f = np.where(fb, db, 0.0) - np.where(fa, da, 0.0)
    du_e = np.where(~fb, db, 0.0) - np.where(~fa, da, 0.0)
    return du_f, du_e, da, db


def reference_shift(rows, at, tau):
    """The forgetting-weighted mean of (u - reference) per kind at ``at``: every pair's end
    rest, and the start rest of a pair that does not continue the one before."""
    _, _, da, db = du_of(rows)
    kind = rows["kind"].to_numpy()
    fa = (kind == _core.PAIR_FE) | (kind == _core.PAIR_FF)
    fb = (kind == _core.PAIR_EF) | (kind == _core.PAIR_FF)
    t = rows["t_days"].to_numpy()
    dt = rows["dt_days"].to_numpy()
    lam_b = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    lam_a = np.exp(-(at - t + dt) / tau) if tau > 0 else np.ones_like(t)
    linked = rows["linked"].to_numpy()
    new_a = np.r_[True, linked[1:] == 0]
    out = []
    for full in (True, False):
        sa, sb = new_a & (fa == full), fb == full
        out.append(((lam_a * da)[sa].sum() + (lam_b * db)[sb].sum()) / (lam_a[sa].sum() + lam_b[sb].sum()))
    return out


def as_pairs(inputs: pd.DataFrame) -> pd.DataFrame:
    """The same pairs as a ``rests.rest_pairs`` table (what ``capacity.py`` reads)."""
    out = inputs.drop(columns=["t_days"]).copy()
    out.insert(0, "t_end", EPOCH + pd.to_timedelta(inputs["t_days"], unit="D"))
    out["kind"] = inputs["kind"].map(KIND_TEXT)
    return out


def new_state(setting, c_weight=100.0, t0=0.0):
    if setting["fit"] == "fade":
        return _core.fade_state(c_weight, t0, setting["voltage"], setting["band"], setting["plausibility"])
    return _core.capacity_state(c_weight, setting["tau_days"], setting["trend"], setting["voltage"], setting["band"],
                                setting["plausibility"])


def feed_v02(state, rows, rounder=None):
    for r in rows.itertuples():
        _core.update(state, r.t_days, r.x, r.dt_days, r.q_ah, r.sigma_q_ah, r.sigma_x, int(r.linked), r.u_start,
                     r.u_end, int(r.kind))
        if rounder is not None:
            rounder(state)


def direct_v02(rows, at, tau, trend, voltage, band, min_same=2.0, fade=False, min_cross_trend=0.0):
    """The 0.2 estimate and band from the residuals in two passes (plain numpy).

    Returns (C at the date and the reference voltages, or r; its sandwich standard error;
    dof; terms used; theta).
    """
    rows = rows[rows["t_days"] <= at]
    du_f, du_e, _, _ = du_of(rows)
    d_f, d_e = reference_shift(rows, at, tau)
    t = rows["t_days"].to_numpy()
    lam = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    w = 1.0 / (rows["sigma_q_ah"].to_numpy() ** 2 + (100.0 * rows["sigma_x"].to_numpy()) ** 2)
    x = rows["x"].to_numpy()
    y = (t - t[0]) / 365.25
    kind = rows["kind"].to_numpy()
    cross_count = lam[(kind == _core.PAIR_FE) | (kind == _core.PAIR_EF)].sum()
    cols, names, grad = [x], ["C"], [1.0]
    if trend and (fade or cross_count >= min_cross_trend):
        cols.append(x * y)
        names.append("r")
        grad.append(0.0 if fade else (at - t[0]) / 365.25)
    cols.append(rows["dt_days"].to_numpy())
    names.append("beta")
    grad.append(0.0)
    if voltage and lam[kind == _core.PAIR_FF].sum() >= min_same:
        cols.append(du_f)
        names.append("gamma_full")
        grad.append(d_f)
    if voltage and lam[kind == _core.PAIR_EE].sum() >= min_same:
        cols.append(du_e)
        names.append("gamma_empty")
        grad.append(-d_e)
    a = np.column_stack(cols)
    q = rows["q_ah"].to_numpy()
    amat = (lam * w)[:, None, None] * a[:, :, None] * a[:, None, :]
    amat = amat.sum(axis=0)
    theta = np.linalg.solve(amat, ((lam * w * q)[:, None] * a).sum(axis=0))
    e = q - a @ theta
    g = ((lam ** (band / 2.0)) * w * e)[:, None] * a  # scores with the band's weight
    meat_s = (g[:, :, None] * g[:, None, :]).sum(axis=0)
    linked = rows["linked"].to_numpy()
    cur = np.flatnonzero(np.r_[False, linked[1:] == 1])
    cross = g[cur][:, :, None] * g[cur - 1][:, None, :]
    meat_l = (cross + cross.transpose(0, 2, 1)).sum(axis=0)
    inv = np.linalg.inv(amat)
    m = lam.sum()
    p = len(cols)
    vmat = inv @ (meat_s + meat_l) @ inv * m / (m - p)
    grad = np.array(grad)
    if fade:
        grad = np.zeros(p)
        grad[1] = 1.0
    value = grad @ theta
    se = np.sqrt(grad @ vmat @ grad)
    dof = m - p
    if band == 2:
        c = inv @ grad
        e2, l2 = c @ meat_s @ c, c @ meat_l @ c
        m2 = (lam * lam).sum()
        link_w = lam[cur] * lam[cur - 1]
        m2_lag = link_w.sum()
        m2_ll = link_w[np.r_[False, linked[1:] == 1][cur - 1]].sum()
        rho = (0.5 * l2 / m2_lag) / (e2 / m2) if m2_lag > 0 and e2 > 0 else 0.0
        qq, q2 = m2_lag / m2, m2_ll / m2
        dof = (m - p) * (1 + 2 * rho * qq) ** 2 / (1 + 2 * qq + 8 * rho * qq + 4 * rho * rho * (qq + q2))
    return value, se, dof, "+".join(n for n in ("C", "beta", "r", "gamma_full", "gamma_empty") if n in names), theta


def _terms_text(est):
    from besslib.soh.capacity import _terms

    return _terms(est)


def test_cholesky_inverse_matches_numpy_and_the_closed_forms():
    rng = np.random.default_rng(4)
    for p in (2, 3, 4, 5):
        for _ in range(20):
            b = rng.normal(size=(p + 3, p)) * rng.uniform(0.01, 100.0, p)
            mat = b.T @ b
            flat = [0.0] * 25
            for i in range(p):
                for j in range(p):
                    flat[5 * i + j] = float(mat[i, j])
            out = [0.0] * 25
            assert _core._cholesky_inverse(p, flat, out) == 1
            got = np.array(out).reshape(5, 5)[:p, :p]
            np.testing.assert_allclose(got, np.linalg.inv(mat), rtol=1e-8, atol=1e-12 * np.abs(np.linalg.inv(mat)).max())
            closed = [0.0] * 25
            assert _core._invert(p, flat, closed) == 1
            np.testing.assert_allclose(np.array(closed).reshape(5, 5)[:p, :p], got, rtol=1e-8, atol=1e-12 * np.abs(got).max())
    b = rng.normal(size=(9, 4))
    b[:, 3] = b[:, 0] - 2.0 * b[:, 1]  # rank 3
    mat = b.T @ b
    singular = [0.0] * 25
    for i in range(4):
        for j in range(4):
            singular[5 * i + j] = float(mat[i, j])
    assert _core._cholesky_inverse(4, singular, [0.0] * 25) == 0


def test_v02_exact_recovery_without_noise():
    rows = chain_v02(sigma_soc=0.0, breaks=())
    rng = np.random.default_rng(2)
    u = rng.normal(0.0, 0.05, len(rows) + 1) + np.r_[rows["u_start"].iloc[0], rows["u_end"].to_numpy()]
    rows["u_start"], rows["u_end"] = u[:-1], u[1:]
    du_f, du_e, _, _ = du_of(rows)
    y = (rows["t_days"] - rows["t_days"].iloc[0]) / 365.25
    rows["q_ah"] = (110.0 - 4.0 * y) * rows["x"] - 0.7 * rows["dt_days"] + 14.0 * du_f + 25.0 * du_e
    at = rows["t_days"].iloc[-1] + 0.5
    # C at the date is read at the reference voltages at the date (no forgetting: their mean).
    d_f, d_e = reference_shift(rows, at, 0.0)
    for fit in ("capacity", "fade"):
        state = new_state(dict(fit=fit, tau_days=60.0, trend=1, voltage=1, band=2, plausibility=1))
        if fit == "fade":
            state = _core.fade_state(100.0, rows["t_days"].iloc[0], 1, 2, 1)
        feed_v02(state, rows)
        est = _core.solve(state, at, Z90, 0.0, 3.0, 2.0, 0.5)
        assert est.status == _core.OK
        if fit == "capacity":
            d_f, d_e = reference_shift(rows, at, 60.0)
        else:
            d_f, d_e = reference_shift(rows, at, 0.0)
        c_at = 110.0 - 4.0 * (at - rows["t_days"].iloc[0]) / 365.25 if fit == "capacity" else 110.0
        assert est.theta[_core.I_C] == pytest.approx(c_at + 14.0 * d_f - 25.0 * d_e, abs=1e-7)
        assert est.u_full == pytest.approx(rows["u_start"].iloc[0] + d_f if rows["kind"].iloc[0] in (_core.PAIR_FE, _core.PAIR_FF)
                                           else est.u_full)
        assert est.theta[_core.I_RATE] == pytest.approx(-4.0, abs=1e-6)
        assert est.theta[_core.I_BETA] == pytest.approx(-0.7, abs=1e-9)
        assert est.theta[_core.I_GF] == pytest.approx(14.0, abs=1e-6)
        assert est.theta[_core.I_GE] == pytest.approx(25.0, abs=1e-6)
        assert est.terms == _core.TERM_TREND | _core.TERM_FULL | _core.TERM_EMPTY and est.p == 5


@pytest.mark.parametrize(
    "tau, trend, voltage, band",
    [(90.0, 1, 1, 2), (30.0, 1, 1, 2), (0.0, 1, 1, 2), (90.0, 0, 1, 2), (90.0, 1, 0, 1), (90.0, 1, 1, 1)],
)
def test_v02_core_equals_direct_sandwich(tau, trend, voltage, band):
    rows = chain_v02(seed=5, no_ee=(230, 300))
    for at in rows["t_days"].iloc[[120, 260, 290, 399]].to_numpy() + 0.3:
        state = new_state(dict(fit="capacity", tau_days=tau, trend=trend, voltage=voltage, band=band, plausibility=0))
        feed_v02(state, rows[rows["t_days"] <= at])
        est = _core.solve(state, at, Z90, 0.0, 3.0, 2.0, 1.0)
        assert est.status == _core.OK
        value, se, dof, terms, _ = direct_v02(rows, at, tau, trend, voltage, band)
        assert est.theta[_core.I_C] == pytest.approx(value, rel=1e-10)
        assert est.sigma[_core.I_C] == pytest.approx(se, rel=1e-7)
        assert est.dof == pytest.approx(dof, rel=1e-7)
        assert _terms_text(est) == terms
        assert est.high - est.low == pytest.approx(2.0 * _core.t_quantile(Z90, dof) * se, rel=1e-7)


def test_v02_fade_equals_direct_sandwich():
    rows = chain_v02(seed=6, n=900, breaks=(300,))
    at = rows["t_days"].iloc[-1]
    state = _core.fade_state(100.0, rows["t_days"].iloc[0], 1, 2, 0)
    feed_v02(state, rows)
    est = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0)
    assert est.status == _core.OK
    value, se, dof, terms, theta = direct_v02(rows, at, 0.0, 1, 1, 2, fade=True)
    assert est.theta[_core.I_RATE] == pytest.approx(value, rel=1e-9)
    assert est.sigma[_core.I_RATE] == pytest.approx(se, rel=1e-7)
    d_f, d_e = reference_shift(rows, at, 0.0)  # c0 is read at the record's mean rest voltages
    assert est.theta[_core.I_C] == pytest.approx(theta[0] + theta[3] * d_f - theta[4] * d_e, rel=1e-10)
    assert est.dof == pytest.approx(dof, rel=1e-7)
    assert est.high - est.low == pytest.approx(2.0 * _core.t_quantile(Z90, dof) * se, rel=1e-7)  # no gain term


def test_v02_band_forgetting_and_dof():
    rows = chain_v02(seed=8)
    at = rows["t_days"].iloc[-1] + 0.2
    est = {}
    for tau in (0.0, 90.0):
        for band in (1, 2):
            state = new_state(dict(fit="capacity", tau_days=tau, trend=1, voltage=1, band=band, plausibility=0))
            feed_v02(state, rows)
            est[tau, band] = _core.solve(state, at, Z90, 0.0, 3.0, 2.0, 1.0)
    # Without forgetting the band's sums are the same; only the degrees of freedom differ.
    assert est[0.0, 2].sigma[_core.I_C] == pytest.approx(est[0.0, 1].sigma[_core.I_C], rel=1e-12)
    assert est[0.0, 1].dof == pytest.approx(est[0.0, 1].m - 5, rel=1e-12)
    assert est[0.0, 2].dof < est[0.0, 1].dof
    # With forgetting, the weight squared narrows the band (about sqrt(2) for a long record).
    ratio = est[90.0, 1].sigma[_core.I_C] / est[90.0, 2].sigma[_core.I_C]
    assert 1.15 < ratio < 1.7
    # Without links the share is 1: dof = m - p.
    unlinked = rows.assign(linked=0)
    state = new_state(dict(fit="capacity", tau_days=90.0, trend=1, voltage=1, band=2, plausibility=0))
    feed_v02(state, unlinked)
    e = _core.solve(state, at, Z90, 0.0, 3.0, 2.0, 1.0)
    assert e.dof == pytest.approx(e.m - 5, rel=1e-12)


def test_v02_left_out_voltage_term():
    rows = chain_v02(seed=9, no_ee=(230, 300))
    at = rows["t_days"].iloc[290] + 0.3
    state = new_state(dict(fit="capacity", tau_days=30.0, trend=1, voltage=1, band=2, plausibility=1))
    feed_v02(state, rows[rows["t_days"] <= at])
    est = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0)
    assert est.status == _core.OK
    assert state.m_ee < 2.0 <= state.m_ff
    assert est.terms == _core.TERM_TREND | _core.TERM_FULL and est.p == 4
    assert math.isnan(est.theta[_core.I_GE]) and math.isfinite(est.theta[_core.I_GF])


def test_v02_plausibility_statuses():
    rows = chain_v02(seed=10, wrong=(330,))
    k = int(np.flatnonzero((rows["kind"] == _core.PAIR_FE) & (rows["q_ah"] >= 0))[0])
    t_wrong = rows["t_days"].iloc[k]
    for tau, at, expected in [(30.0, t_wrong + 1.0, _core.WRONG_SIGN), (30.0, t_wrong + 31.0, _core.OK),
                              (0.0, t_wrong + 31.0, _core.WRONG_SIGN)]:
        for plaus in (1, 0):
            state = new_state(dict(fit="capacity", tau_days=tau, trend=1, voltage=1, band=2, plausibility=plaus))
            feed_v02(state, rows[rows["t_days"] <= at])
            status = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0).status
            assert status == (expected if plaus else _core.OK)
    # The wrong sign is read from the kind, not from x: an empty-to-full pair of zero charge counts too.
    state = new_state(dict(fit="capacity", tau_days=30.0, trend=1, voltage=1, band=2, plausibility=1))
    head = rows.iloc[:200].copy()
    j = int(np.flatnonzero(head["kind"] == _core.PAIR_EF)[-1])
    head.loc[head.index[j], "q_ah"] = 0.0
    feed_v02(state, head)
    assert _core.solve(state, head["t_days"].iloc[-1], Z90, 0.005, 3.0, 2.0, 1.0).status == _core.WRONG_SIGN
    # A capacity that falls towards zero: read 15 days after the last pair, the trend
    # puts it below zero, while every pair has the right sign.
    fall = chain_v02(seed=12, n=40, cap=10.0, sigma_soc=0.002, breaks=(), collapse_days=40.0)
    assert ((fall["kind"] == _core.PAIR_FE) & (fall["q_ah"] < 0)).sum() == (fall["kind"] == _core.PAIR_FE).sum()
    for plaus, expected in ((1, _core.OUT_OF_RANGE), (0, _core.OK)):
        state = new_state(dict(fit="capacity", tau_days=30.0, trend=1, voltage=1, band=2, plausibility=plaus))
        feed_v02(state, fall)
        est = _core.solve(state, fall["t_days"].iloc[-1] + 15.0, Z90, 0.005, 3.0, 2.0, 1.0)
        assert est.status == expected
        assert est.theta[_core.I_C] < 0.0  # filled also for OUT_OF_RANGE


@pytest.mark.parametrize("tau", [0.0, 30.0, 180.0])
def test_v02_batch_path_equals_core(tau):
    rows = chain_v02(seed=13, no_ee=(230, 300), wrong=(330,))
    pairs = as_pairs(rows)
    config = V02.with_(tau_days=tau)
    at = pairs["t_end"].iloc[[30, 149, 150, 260, 290, 335, 380, 399]] + pd.Timedelta(hours=6)
    core = run_core(pairs, SPEC, config, at)
    statuses = set()
    for when, c in zip(at, core):
        td = (when - EPOCH).total_seconds() / 86400.0
        b = _solve(capacity_state_at(pairs, SPEC, config, td), td, Z90, config)
        assert b.status == c.status
        assert b.terms == c.terms and b.p == c.p
        statuses.add(c.status)
        for k in range(_core.P_MAX):
            assert b.theta[k] == pytest.approx(c.theta[k], rel=1e-9, nan_ok=True)
            assert b.sigma[k] == pytest.approx(c.sigma[k], rel=1e-9, nan_ok=True)
        assert b.dof == pytest.approx(c.dof, rel=1e-9)
        assert b.low == pytest.approx(c.low, rel=1e-9, nan_ok=True)
        # rho (a diagnostic) cancels more than the rest: it agrees to rounding at 1e-7
        assert b.rho == pytest.approx(c.rho, rel=1e-7, abs=1e-12, nan_ok=True)
        assert b.u_full == pytest.approx(c.u_full, rel=1e-12, nan_ok=True)
        assert b.u_empty == pytest.approx(c.u_empty, rel=1e-12, nan_ok=True)
        assert b.dof_beta == pytest.approx(c.dof_beta, rel=1e-9)
    assert _core.OK in statuses and _core.WRONG_SIGN in statuses


def test_v02_batch_state_continues_like_the_core():
    rows = chain_v02(seed=14)
    pairs = as_pairs(rows)
    mid = 0.5 * (rows["t_days"].iloc[250] + rows["t_days"].iloc[251])  # between two pairs
    state = capacity_state_at(pairs, SPEC, V02, float(mid))
    feed_v02(state, rows[rows["t_days"] > mid])
    full = capacity_state_at(pairs, SPEC, V02, float(rows["t_days"].iloc[-1]) + 0.01)
    at = float(rows["t_days"].iloc[-1]) + 1.0
    a, b = _solve(state, at, Z90, V02), _solve(full, at, Z90, V02)
    assert a.status == b.status == _core.OK
    assert a.theta[_core.I_C] == pytest.approx(b.theta[_core.I_C], rel=1e-10)
    assert a.high == pytest.approx(b.high, rel=1e-10)


def test_v02_band_at_the_date_holds_a_fading_capacity():
    # A steady fade, 90 days of memory: with the trend the band holds the capacity at the
    # date, without it the estimate lags. The truth is the capacity between the two
    # reference voltages at the date, on the chain's own voltage map (u = 57.4 + 2 eps at
    # full, 44.8 + 5 eps at empty): the quantity the estimate reads.
    held = 0
    lag_trend, lag_flat = [], []
    runs = 200
    config = V02.with_(tau_days=90.0, gain_error=0.0, plausibility=False)
    for seed in range(runs):
        rows = chain_v02(seed=100 + seed, n=600, fade=-8.0, breaks=(300,))
        pairs = as_pairs(rows)
        at = float(rows["t_days"].iloc[-1]) + 0.5
        cap = 100.0 - 8.0 * (at - rows["t_days"].iloc[0] + rows["dt_days"].iloc[0]) / 365.25
        est = _solve(capacity_state_at(pairs, SPEC, config, at), at, Z90, config)
        flat = _solve(capacity_state_at(pairs, SPEC, config.with_(local_trend=False), at), at, Z90, config)
        assert est.terms == _core.TERM_TREND | _core.TERM_FULL | _core.TERM_EMPTY
        truth = cap * (1.0 + (est.u_full - 57.4) / 2.0 - (est.u_empty - 44.8) / 5.0)
        held += est.low <= truth <= est.high
        lag_trend.append(est.theta[_core.I_C] - truth)
        lag_flat.append(flat.theta[_core.I_C] - truth)
    assert 0.84 <= held / runs <= 0.96
    assert abs(np.mean(lag_trend)) < 2.0 * np.std(lag_trend) / np.sqrt(runs)
    assert np.mean(lag_flat) > 1.5  # about tau_days of fade: 8 Ah/yr x 90/365 = 2 Ah


def test_v02_reference_moves_only_the_capacity():
    # Moving every rest voltage of one kind by the same amount moves the references and
    # nothing else: the same fit, the same estimate.
    rows = chain_v02(seed=15)
    at = rows["t_days"].iloc[-1] + 0.2
    base, moved = [], []
    for shift, out in ((0.0, base), (0.3, moved)):
        r = rows.copy()
        kind = r["kind"].to_numpy()
        fa = (kind == _core.PAIR_FE) | (kind == _core.PAIR_FF)
        fb = (kind == _core.PAIR_EF) | (kind == _core.PAIR_FF)
        r["u_start"] += np.where(fa, shift, 0.0)
        r["u_end"] += np.where(fb, shift, 0.0)
        state = new_state(dict(fit="capacity", tau_days=90.0, trend=1, voltage=1, band=2, plausibility=0))
        feed_v02(state, r)
        out.append(_core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0))
    a, b = base[0], moved[0]
    for k in range(_core.P_MAX):
        assert b.theta[k] == pytest.approx(a.theta[k], rel=1e-9)
        assert b.sigma[k] == pytest.approx(a.sigma[k], rel=1e-7)
    assert b.u_full == pytest.approx(a.u_full + 0.3, rel=1e-12) and b.u_empty == pytest.approx(a.u_empty, rel=1e-12)
    # The capacity at another reference: C(rf) = C(r0f) + gamma_f (rf - r0f), the same band.
    state = new_state(dict(fit="capacity", tau_days=90.0, trend=1, voltage=1, band=2, plausibility=0))
    feed_v02(state, rows)
    est = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0)
    d_f, d_e = reference_shift(rows, at, 90.0)
    assert est.u_full - d_f == pytest.approx(rows["u_start"].iloc[0] if rows["kind"].iloc[0] in (_core.PAIR_FE, _core.PAIR_FF)
                                             else est.u_full - d_f)


def test_v02_trend_needs_enough_full_to_empty_pairs():
    rows = chain_v02(seed=16)
    at = rows["t_days"].iloc[-1] + 0.2
    state = new_state(dict(fit="capacity", tau_days=30.0, trend=1, voltage=1, band=2, plausibility=0))
    feed_v02(state, rows)
    m_cross = state.m_cross
    on = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0, m_cross - 0.5)
    off = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0, m_cross + 0.5)
    assert on.terms & _core.TERM_TREND and on.p == 5 and math.isfinite(on.theta[_core.I_RATE])
    assert not off.terms & _core.TERM_TREND and off.p == 4 and math.isnan(off.theta[_core.I_RATE])
    for est, gate in ((on, m_cross - 0.5), (off, m_cross + 0.5)):
        value, se, dof, terms, _ = direct_v02(rows, at, 30.0, 1, 1, 2, min_cross_trend=gate)
        assert est.theta[_core.I_C] == pytest.approx(value, rel=1e-10)
        assert est.sigma[_core.I_C] == pytest.approx(math.hypot(se, 0.005 * value), rel=1e-7)
        assert _terms_text(est) == terms
    # The fade fit always keeps its trend: it is its main quantity.
    fade = _core.fade_state(100.0, rows["t_days"].iloc[0], 1, 2, 0)
    feed_v02(fade, rows)
    kept = _core.solve(fade, at, Z90, 0.0, 3.0, 2.0, 0.5, 1e9)
    assert kept.status == _core.OK and kept.terms & _core.TERM_TREND


def test_v02_rest_without_voltage():
    # A rest whose relaxed voltage is not finite gives no voltage term and does not move
    # the references; the estimate stays finite.
    rows = chain_v02(seed=17)
    rows.loc[rows.index[50], "u_end"] = np.nan
    rows.loc[rows.index[51], "u_start"] = np.nan
    at = rows["t_days"].iloc[-1] + 0.2
    state = new_state(dict(fit="capacity", tau_days=0.0, trend=1, voltage=1, band=2, plausibility=0))
    feed_v02(state, rows)
    est = _core.solve(state, at, Z90, 0.005, 3.0, 2.0, 1.0)
    assert est.status == _core.OK and math.isfinite(est.theta[_core.I_C]) and math.isfinite(est.u_full)
    pairs = as_pairs(rows)
    b = _solve(capacity_state_at(pairs, SPEC, V02.with_(tau_days=0.0), float(at)), float(at), Z90, V02.with_(tau_days=0.0))
    assert b.theta[_core.I_C] == pytest.approx(est.theta[_core.I_C], rel=1e-9)
    assert b.u_full == pytest.approx(est.u_full, rel=1e-12) and b.u_empty == pytest.approx(est.u_empty, rel=1e-12)


_OUT_V02 = (
    "status", "theta0", "theta1", "theta2", "theta3", "theta4", "sigma0", "sigma1", "sigma2", "sigma3", "sigma4",
    "low", "high", "chi2", "dof", "birge", "rho", "m", "m_cross", "m_same", "terms", "p", "u_full", "u_empty",
    "dof_beta",
)
_SETTINGS_V02 = ("fit", "tau_days", "trend", "voltage", "band", "plausibility", "min_cross_trend")


def _v02_vector_inputs():
    main = chain_v02(seed=31, no_ee=(230, 300), wrong=(330,)).assign(chain=0)
    fall = chain_v02(seed=12, n=40, cap=10.0, sigma_soc=0.01, breaks=(), collapse_days=40.0).assign(chain=1)
    fall["sigma_q_ah"] = 0.03  # a 10 Ah pack: its weights and centring use its own nominal 10 Ah
    inputs = pd.concat([main, fall], ignore_index=True)
    at_main = main["t_days"].iloc[[20, 60, 149, 150, 220, 290, 300, 335, 380, 399]].to_numpy() + 0.25
    at_fall = np.r_[fall["t_days"].iloc[[25, 39]].to_numpy() + 0.25, fall["t_days"].iloc[-1] + 15.0]
    settings = [
        ("capacity", 0.0, 1, 1, 2, 1, 0.0), ("capacity", 30.0, 1, 1, 2, 1, 0.0), ("capacity", 90.0, 1, 1, 2, 1, 0.0),
        ("capacity", 90.0, 1, 0, 1, 0, 0.0), ("capacity", 90.0, 0, 1, 2, 1, 0.0), ("fade", 0.0, 1, 1, 2, 0, 0.0),
        ("capacity", 30.0, 1, 1, 2, 1, 12.0),  # the trend left out while fewer than 12 weighted cross pairs
    ]
    queries = [(0, 100.0) + s + (a,) for s in settings for a in at_main]
    queries += [(1, 10.0, "capacity", 30.0, 1, 1, 2, 1, 0.0, a) for a in at_fall]
    return inputs, pd.DataFrame(queries, columns=("chain", "c_weight") + _SETTINGS_V02 + ("t_days",))


def _v02_vector_outputs(inputs: pd.DataFrame, queries: pd.DataFrame, rounder=None) -> pd.DataFrame:
    rows = []
    for key, group in queries.groupby(["chain", "c_weight", *_SETTINGS_V02], sort=False):
        setting = dict(zip(("chain", "c_weight") + _SETTINGS_V02, key))
        chain = inputs[inputs["chain"] == setting["chain"]]
        state = new_state(setting, c_weight=setting["c_weight"], t0=float(chain["t_days"].iloc[0]))
        k = 0
        for at in group["t_days"]:
            while k < len(chain) and chain["t_days"].iloc[k] <= at:
                feed_v02(state, chain.iloc[k:k + 1], rounder)
                k += 1
            e = _core.solve(state, float(at), Z90, 0.005, 3.0, 2.0, 1.0, float(setting["min_cross_trend"]))
            row = dict(setting, t_days=at, status=e.status, low=e.low, high=e.high, chi2=e.chi2, dof=e.dof,
                       birge=e.birge, rho=e.rho, m=e.m, m_cross=e.m_cross, m_same=e.m_same, terms=e.terms, p=e.p,
                       u_full=e.u_full, u_empty=e.u_empty, dof_beta=e.dof_beta)
            for i in range(_core.P_MAX):
                row[f"theta{i}"] = e.theta[i]
                row[f"sigma{i}"] = e.sigma[i]
            rows.append(row)
    return pd.DataFrame(rows)[["chain", "c_weight", *_SETTINGS_V02, "t_days", *_OUT_V02]]


def write_v02_vectors() -> None:
    inputs, queries = _v02_vector_inputs()
    VECTORS.mkdir(exist_ok=True)
    inputs.to_csv(VECTORS / "capacity_core_v02_pairs.csv", index=False, float_format="%.17g")
    _v02_vector_outputs(inputs, queries).to_csv(VECTORS / "capacity_core_v02_expected.csv", index=False, float_format="%.17g")


def test_saved_v02_vectors():
    inputs = _read_vectors(VECTORS / "capacity_core_v02_pairs.csv")
    expected = _read_vectors(VECTORS / "capacity_core_v02_expected.csv")
    got = _v02_vector_outputs(inputs, expected[["chain", "c_weight", *_SETTINGS_V02, "t_days"]])
    for col in ("status", "terms", "p"):
        assert (got[col].to_numpy() == expected[col].to_numpy()).all(), col
    for col in _OUT_V02:
        if col not in ("status", "terms", "p"):
            np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-9, equal_nan=True)
    # What the vectors cover: the trend, both voltage terms and one left out, forgetting
    # squared, and the statuses of plausibility rules 2 and 3.
    assert {_core.OK, _core.WRONG_SIGN, _core.OUT_OF_RANGE} <= set(expected["status"])
    ok = expected[(expected["status"] == _core.OK) & (expected["voltage"] == 1)]
    assert (ok["terms"] == _core.TERM_TREND | _core.TERM_FULL | _core.TERM_EMPTY).any()
    assert (ok["terms"] == _core.TERM_TREND | _core.TERM_FULL).any()
    assert (ok["terms"] == _core.TERM_FULL | _core.TERM_EMPTY).any()  # the trend left out by the gate
    assert set(expected["band"]) == {1, 2}


def _v02_float32_errors():
    """Largest relative error of C (or r) and of the band's width, 32-bit state against 64-bit,
    over the estimated rows of the 0.2 vectors."""
    inputs, queries = _v02_vector_inputs()
    e64 = _v02_vector_outputs(inputs, queries)
    e32 = _v02_vector_outputs(inputs, queries, rounder=_to_f32)
    ok = (e64["status"] == _core.OK) & (e32["status"] == _core.OK)
    main = np.where(e64["fit"] == "fade", "theta2", "theta0")
    v64 = np.array([e64[c].iloc[i] for i, c in enumerate(main)])[ok]
    v32 = np.array([e32[c].iloc[i] for i, c in enumerate(main)])[ok]
    w64 = (e64["high"] - e64["low"])[ok].to_numpy()
    w32 = (e32["high"] - e32["low"])[ok].to_numpy()
    return float(np.max(np.abs(v32 - v64) / np.abs(v64))), float(np.max(np.abs(w32 - w64) / np.abs(w64))), \
        int((e64["status"] != e32["status"]).sum())


def test_float32_state_v02():
    value_err, width_err, status_diff = _v02_float32_errors()
    assert status_diff == 0
    assert value_err < 1e-4
    assert width_err < 1e-2


if __name__ == "__main__" and "--write-v02-vectors" in sys.argv:
    write_v02_vectors()
    print(f"wrote the 0.2 capacity vectors to {VECTORS}")


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
    events = _read_vectors(VECTORS / "ratio_core_events.csv")
    expected = _read_vectors(VECTORS / "ratio_core_expected.csv")
    got = _ratio_vector_outputs(events, expected[["fit", "tau_days", "t0_days", "t_days", "z", "min_events", "min_span_years", "lower", "upper"]])
    assert (got["status"].to_numpy() == expected["status"].to_numpy()).all()
    assert set(expected["status"]) >= {_core.OK, _core.TOO_FEW_EVENTS, _core.SHORT_SPAN}
    for col in _RATIO_OUT[1:]:
        np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-15, equal_nan=True)


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_ratio_vectors()
    print(f"wrote the ratio vectors to {VECTORS}")


# --- the loop band: the ratio over the loops in memory, from the rest error -------------------


def loop_chain(seed, years=3.0, s_rest=0.1, s_eta=0.01, p_break=0.02, season=0.0):
    """Closed loops every 1-4 days, ``b = eta a + e_start - e_end``.

    ``e``: the error of the energy stored at a rest (standard deviation ``s_rest``); a
    linked loop starts at the rest where the loop before it ended. ``eta``: the loop's
    own efficiency, 0.9 plus an independent part (``s_eta``) and a seasonal swing of
    amplitude ``season``. A share ``p_break`` of the loops starts a new run (a break of
    the chain, a fresh start rest). Returns ``t, a, b, linked, eta``.
    """
    rng = np.random.default_rng(seed)
    n = int(365.25 * years / 2.5)
    t = 7000.0 + np.cumsum(rng.uniform(1.0, 4.0, n))
    a = rng.uniform(5.0, 15.0, n)
    eta = 0.9 + s_eta * rng.standard_normal(n) + season * np.sin(2.0 * np.pi * t / 365.25)
    linked = (rng.uniform(size=n) >= p_break).astype(int)
    linked[0] = 0
    end = rng.normal(0.0, s_rest, n)
    fresh = rng.normal(0.0, s_rest, n)
    start = np.where(linked == 1, np.r_[0.0, end[:-1]], fresh)
    return t, a, eta * a + start - end, linked, eta


def direct_loop_band(t, a, b, linked, at, tau):
    """The loop band from the loops in two passes (plain numpy). ``C2`` is summed over the
    rests themselves (each loop gets a start and an end rest; a linked loop's start rest is
    the end rest of the loop before it). The variance of ``s2`` is the full double sum
    over every pair of lag products, with the normal fourth moments and the autocovariances
    ``gamma0``, ``M1``, ``M2`` within a linked run (none across a break or beyond lag 2)."""
    keep = (a > 0) & np.isfinite(a) & np.isfinite(b)
    eff = (linked != 0) & keep & np.r_[False, keep[:-1]]
    t, a, b, eff = t[keep], a[keep], b[keep], eff[keep]
    sel = t <= at
    t, a, b, eff = t[sel], a[sel], b[sel], eff[sel]
    lam = np.exp(-(at - t) / tau) if tau > 0 else np.ones_like(t)
    coef = {}
    rest = 0
    for k in range(len(t)):
        if not eff[k]:
            rest += 1
            coef[rest] = coef.get(rest, 0.0) + lam[k]  # start rest
        else:
            coef[rest] += lam[k]  # the shared rest: the previous loop's end
        rest += 1
        coef[rest] = -lam[k]  # end rest
    c2 = sum(c * c for c in coef.values())
    big_a = np.sum(lam * a)
    value = np.sum(lam * b) / big_a
    g = b - value * a
    run = np.cumsum(~eff)
    one = np.flatnonzero(eff)  # loop k linked to k - 1
    two = one[(one >= 2)]
    two = two[eff[two - 1]]  # and k - 1 linked to k - 2
    w1 = lam[one] * lam[one - 1]
    w2 = lam[two] * lam[two - 2]
    m1 = np.sum(w1 * g[one] * g[one - 1]) / w1.sum()
    m2 = np.sum(w2 * g[two] * g[two - 2]) / w2.sum()
    g0 = np.sum(lam**2 * g**2) / np.sum(lam**2)
    s2 = m2 - m1
    # s2 = sum coef_p g_i g_j over the products p = (i, j).
    pi = np.r_[one, two]
    pj = np.r_[one - 1, two - 2]
    pc = np.r_[-w1 / w1.sum(), w2 / w2.sum()]

    def gam(x, y):
        d = np.abs(x[:, None] - y[None, :])
        same = run[x][:, None] == run[y][None, :]
        return np.where(same & (d == 0), g0, 0.0) + np.where(same & (d == 1), m1, 0.0) + np.where(same & (d == 2), m2, 0.0)

    cov = gam(pi, pi) * gam(pj, pj) + gam(pi, pj) * gam(pj, pi)
    var_s2 = pc @ cov @ pc
    dof = 2.0 * s2**2 / var_s2
    sigma = math.sqrt(s2 * c2) / big_a
    half = _core.t_quantile(Z90, dof) * sigma
    return dict(value=value, c2=c2, s2=s2, sigma=sigma, dof=dof, rho=m1 / g0, low=value - half, high=value + half,
                m1=m1, m2=m2, g0=g0)


LOOP_ARGS = dict(level=0.90, min_events=5.0, lower=0.0, upper=1.0)


def test_loop_batch_equals_core():
    t, a, b, linked, _ = loop_chain(seed=2)
    a[[0, 17, 120]] = 0.0  # refused events
    b[[18, 300]] = np.nan
    at = np.r_[t[0] - 1.0, t[[3, 50, 51, 160, 299, 301]] + 0.25, t[-1], t[-1] + 40.0]
    for tau in (0.0, 30.0, 180.0, 400.0):
        batch = _ratio.estimate_loops(t, a, b, linked, at, tau, **LOOP_ARGS)
        core = _ratio.run_loop_core(t, a, b, linked, at, tau, **LOOP_ARGS)
        assert (batch["status"] == core["status"]).all()
        assert (batch["status"].iloc[3:-1] == _core.OK).all() or tau == 30.0  # 30 days: some below one dof
        for col in ("value", "sigma", "low", "high", "n_events", "n_links", "dof", "rho", "rest_error"):
            np.testing.assert_allclose(batch[col], core[col], rtol=1e-9, atol=1e-15, equal_nan=True)
        # The value is the ratio fit's value.
        ratio = _ratio.estimate_ratio(t, a, b, linked, at, tau, 0.90, 5.0, 0.0, 1.0)
        ok = batch["status"] == _core.OK
        np.testing.assert_allclose(batch["value"][ok], ratio["value"][ok], rtol=1e-12)


def test_loop_band_equals_direct_computation():
    t, a, b, linked, _ = loop_chain(seed=4)
    a[[30, 31]] = 0.0
    cases = ((0.0, t[-1], _core.OK), (180.0, t[250] + 0.5, _core.OK), (60.0, t[200] + 0.5, _core.OK),
             (180.0, t[-1], _core.LOW_DOF))
    for tau, at, status in cases:
        state = feed_ratio(_core.ratio_state(tau), t[t <= at], a[t <= at], b[t <= at], linked[t <= at])
        est = _core.loop_solve(state, at, Z90, 5.0, 0.0, 1.0)
        want = direct_loop_band(t, a, b, linked, at, tau)
        assert est.status == status
        assert state.c2 == pytest.approx(want["c2"], rel=1e-10)  # the recursion equals the sum over rests
        assert est.rest_error ** 2 == pytest.approx(want["s2"], rel=1e-9)
        assert est.dof == pytest.approx(want["dof"], rel=1e-9)  # the fixed-size sums equal the full double sum
        assert est.rho == pytest.approx(want["rho"], rel=1e-9)
        if status != _core.OK:
            assert est.dof < 1.0 and math.isnan(est.low)
            continue
        assert est.theta[0] == pytest.approx(want["value"], rel=1e-12)
        assert est.sigma[0] == pytest.approx(want["sigma"], rel=1e-9)
        assert est.low == pytest.approx(want["low"], rel=1e-12)
        assert est.high == pytest.approx(want["high"], rel=1e-12)
    # Without forgetting C2 is 2 per linked run: the first and the last rest of each.
    keep = a > 0  # a refused event breaks the chain
    runs = int(np.sum(keep & ~((linked != 0) & np.r_[False, keep[:-1]])))
    state = feed_ratio(_core.ratio_state(0.0), t, a, b, linked)
    assert state.c2 == pytest.approx(2.0 * runs, rel=1e-12)
    # Uniform chains of pure rest errors: rho near -1/2 and about 2 N / 21 degrees of
    # freedom (2 N / 7 for the lag-1 products alone). One chain's dof is itself an
    # estimate (it scales with the estimated s2 squared), so the median of 21 chains.
    n = 400
    chain = np.r_[0, np.ones(n - 1, dtype=int)]
    rows = []
    for seed in range(21):
        pt = np.random.default_rng(seed).normal(0.0, 0.1, n + 1)
        est = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), 7000.0 + np.arange(n), np.full(n, 10.0),
                                          9.0 + pt[:-1] - pt[1:], chain), 7000.0 + n, Z90, 5.0, 0.0, 1.0)
        rows.append((est.rho, est.dof, est.rest_error))
    rho, dof, rest = np.median(np.asarray(rows), axis=0)
    assert rho == pytest.approx(-0.5, abs=0.05)
    assert dof == pytest.approx(2.0 * (n - 1) / 21.0, rel=0.15)
    assert rest == pytest.approx(0.1, rel=0.1)


def test_loop_dof_matches_the_scatter_of_the_rest_error():
    # The degrees of freedom against the scatter of s2 over many chains (a chi-square with
    # dof degrees of freedom has variance 2 mean^2 / dof), for independent own variation
    # and for a slow seasonal swing of the loops' own efficiency.
    out = {}
    for name, kw in (("independent", {}), ("seasonal swing", dict(season=0.01))):
        s2, dof = [], []
        for seed in range(300):
            t, a, b, linked, _ = loop_chain(seed, **kw)
            est = _ratio.estimate_loops(t, a, b, linked, [t[-1]], 0.0, **LOOP_ARGS).iloc[0]
            s2.append(est["rest_error"] ** 2)
            dof.append(est["dof"])
        s2 = np.asarray(s2)
        out[name] = (float(s2.mean()), float(2.0 * s2.mean() ** 2 / s2.var()), float(np.median(dof)))
    print(f"rest error variance (mean, dof from scatter, median dof from the formula; true 0.01): {out}")
    for mean, scatter, formula in out.values():
        assert mean == pytest.approx(0.01, rel=0.05)
        assert scatter == pytest.approx(formula, rel=0.25)


def test_loop_status_codes():
    t, a, b, linked, _ = loop_chain(seed=1, years=0.5)
    args = (Z90, 5.0, 0.0, 1.0)
    few = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t[:4], a[:4], b[:4], linked[:4]), t[3], *args)
    assert few.status == _core.TOO_FEW_EVENTS and few.m == 4.0
    growth = _core.loop_solve(feed_ratio(_core.growth_state(t[0]), t, a, b, linked), t[-1], *args)
    assert growth.status == _core.SINGULAR
    over = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t, a, 1.2 * b, linked), t[-1], *args)
    assert over.status == _core.OUT_OF_RANGE and over.theta[0] > 1.0 and math.isnan(over.low)
    free = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t, a, b, 0 * linked), t[-1], *args)
    assert free.status == _core.NO_LINKS and math.isnan(free.low)
    # Links, but never three loops in a row: the rest error cannot be told from a drift.
    pairs = np.arange(len(t)) % 2
    two = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t, a, b, pairs), t[-1], *args)
    assert two.status == _core.NO_LINKS and two.m_lag > 0.0
    # The loops' own efficiency changes within two loops and no rest error is seen: the
    # lag-1 products exceed the lag-2 ones, the rest error's variance is negative, and no
    # floor replaces it.
    drift = 0.9 + 0.02 * np.sin(2.0 * np.pi * (t - t[0]) / 20.0)
    up = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t, a, drift * a, linked), t[-1], *args)
    assert up.status == _core.NO_BAND and up.rho > 0.0 and math.isnan(up.low)
    # Few loops: the rest error is known on less than one degree of freedom.
    low = _core.loop_solve(feed_ratio(_core.ratio_state(0.0), t[:8], a[:8], b[:8], linked[:8] * 0 + 1), t[7], *args)
    assert low.status == _core.LOW_DOF and 0.0 <= low.dof < 1.0 and math.isnan(low.low)
    for code in (_core.NO_LINKS, _core.NO_BAND, _core.LOW_DOF):
        assert _core.STATUS_TEXT[code].startswith("not possible from this data")
    assert len(set(_core.STATUS_TEXT.values())) == len(_core.STATUS_TEXT)


def test_loop_float32_state():
    t, a, b, linked, _ = loop_chain(seed=5)
    a = a * 1.0e3  # Wh
    b = b * 1.0e3
    td = t[-1] + 0.1
    e64 = _core.loop_solve(feed_ratio(_core.ratio_state(180.0), t, a, b, linked), td, Z90, 5.0, 0.0, 1.0)
    e32 = _core.loop_solve(feed_ratio(_core.ratio_state(180.0), t, a, b, linked, rounder=_to_f32), td, Z90, 5.0, 0.0, 1.0)
    assert e64.status == e32.status == _core.OK
    agree = (abs(e32.theta[0] / e64.theta[0] - 1.0), abs((e32.high - e32.low) / (e64.high - e64.low) - 1.0),
             abs(e32.dof / e64.dof - 1.0))
    print(f"loop band, float32 relative differences (value, band width, dof): {agree}")
    assert agree[0] < 1e-5
    assert agree[1] < 1e-3
    assert agree[2] < 1e-3


def test_loop_band_holds_the_closed_loop_ratio():
    # Linked loops every 1-4 days over 3 years with occasional breaks: rest errors (0.1)
    # and the loops' own efficiency varying independently (0.01 of about 10 per loop), or
    # with a seasonal swing on top. The target is the ratio over the loops in memory had
    # each closed, with the same weights. A chain without a band counts as not held.
    runs = 300
    shares = {}
    cases = (
        ("no forgetting", 0.0, {}),
        ("no forgetting, seasonal swing", 0.0, dict(season=0.01)),
        ("180 days", 180.0, {}),
        ("180 days, seasonal swing", 180.0, dict(season=0.01)),
    )
    for name, tau, kw in cases:
        held = no_band = 0
        errors, sigmas, dofs = [], [], []
        for seed in range(runs):
            t, a, b, linked, eta = loop_chain(seed, **kw)
            lam = np.exp(-(t[-1] - t) / tau) if tau > 0 else np.ones_like(t)
            target = np.sum(lam * eta * a) / np.sum(lam * a)
            est = _ratio.estimate_loops(t, a, b, linked, [t[-1]], tau, **LOOP_ARGS).iloc[0]
            if est["status"] != _core.OK:
                no_band += 1
                continue
            held += est["low"] <= target <= est["high"]
            errors.append(est["value"] - target)
            sigmas.append(est["sigma"])
            dofs.append(est["dof"])
        shares[name] = (held / runs, no_band / runs, held / (runs - no_band), float(np.median(sigmas) / np.std(errors)),
                        float(np.median(dofs)))
    print("loop band coverage (held, no band, held among those with a band, median sigma / scatter of R - T, "
          f"median dof): {shares}")
    # No forgetting: about 90 %, with or without the seasonal swing (the lag-2 products
    # carry the swing as the lag-1 products do, and their difference does not).
    for name in ("no forgetting", "no forgetting, seasonal swing"):
        held, no_band, banded, ratio, dof = shares[name]
        assert 0.85 <= held <= 0.95
        assert 0.9 <= ratio <= 1.1
    # 180 days of memory: the rest error rests on a few degrees of freedom, and a share of
    # the chains has a negative estimate or less than one degree of freedom (no band, never
    # a floor); the bands that exist hold the target more often than 90 %.
    for name in ("180 days", "180 days, seasonal swing"):
        held, no_band, banded, ratio, dof = shares[name]
        assert no_band > 0.05
        assert banded >= 0.9
        assert 0.85 <= ratio <= 1.15


# --- saved vectors of the loop band -----------------------------------------------------------

_LOOP_OUT = ("status", "theta0", "sigma0", "cov0", "low", "high", "dof", "m", "m_lag", "rho", "rest_error")


def _loop_vector_inputs():
    t, a, b, linked, _ = loop_chain(seed=11, years=2.0)
    a[[7, 150]] = 0.0
    events = pd.DataFrame({"t_days": t, "a": a * 1.0e3, "b": b * 1.0e3, "linked": linked})
    at = t[[2, 40, 89, 90, 150, 250, len(t) - 1]] + 0.25
    queries = pd.DataFrame([(tau, q) for tau in (0.0, 30.0, 180.0) for q in at], columns=["tau_days", "t_days"])
    queries["z"] = Z90
    queries["min_events"] = 5.0
    queries["lower"] = 0.0
    queries["upper"] = 1.0
    return events, queries


def _loop_vector_outputs(events: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tau, group in queries.groupby("tau_days", sort=False):
        state = _core.ratio_state(float(tau))
        k = 0
        for q in group.itertuples():
            while k < len(events) and events["t_days"].iloc[k] <= q.t_days:
                r = events.iloc[k]
                _core.ratio_update(state, float(r["t_days"]), float(r["a"]), float(r["b"]), int(r["linked"]))
                k += 1
            e = _core.loop_solve(state, float(q.t_days), q.z, q.min_events, q.lower, q.upper)
            rows.append({"tau_days": tau, "t_days": q.t_days, "z": q.z, "min_events": q.min_events, "lower": q.lower,
                         "upper": q.upper, "status": e.status, "theta0": e.theta[0], "sigma0": e.sigma[0],
                         "cov0": e.cov[0], "low": e.low, "high": e.high, "dof": e.dof, "m": e.m, "m_lag": e.m_lag,
                         "rho": e.rho, "rest_error": e.rest_error})
    return pd.DataFrame(rows)


def write_loop_vectors() -> None:
    events, queries = _loop_vector_inputs()
    VECTORS.mkdir(exist_ok=True)
    events.to_csv(VECTORS / "loop_core_events.csv", index=False, float_format="%.17g")
    _loop_vector_outputs(events, queries).to_csv(VECTORS / "loop_core_expected.csv", index=False, float_format="%.17g")


def test_saved_loop_vectors():
    events = _read_vectors(VECTORS / "loop_core_events.csv")
    expected = _read_vectors(VECTORS / "loop_core_expected.csv")
    got = _loop_vector_outputs(events, expected[["tau_days", "t_days", "z", "min_events", "lower", "upper"]])
    assert (got["status"].to_numpy() == expected["status"].to_numpy()).all()
    assert set(expected["status"]) >= {_core.OK, _core.TOO_FEW_EVENTS}
    for col in _LOOP_OUT[1:]:
        np.testing.assert_allclose(got[col].to_numpy(float), expected[col].to_numpy(float), rtol=1e-12, atol=1e-15, equal_nan=True)


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_loop_vectors()
    print(f"wrote the loop vectors to {VECTORS}")
