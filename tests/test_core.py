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
