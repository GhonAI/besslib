"""End-to-end test of the capacity estimator on a synthetic pack with a known fade."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib.soh.capacity import estimate_capacity, fade_rate, run_core
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh import _core
from besslib.soh.rests import classify_rests, detect_rests_in_parts, levels_from, rest_chain, rest_pairs
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()


@pytest.fixture(scope="module")
def candidates():
    return detect_rests_in_parts(frames(PACK, 18, step_s=20, seed=4), SPEC, CapacityConfig())


@pytest.fixture(scope="module")
def pairs(candidates):
    # The synthetic pack has no seasons; an 18-month record would spend a year in the
    # default level warm-up, so its rests are classified with a 90-day window and no warm-up.
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    return rest_pairs(rest_chain(classify_rests(candidates, SPEC, config), SPEC, config), config)


def test_fade_rate_near_the_truth(pairs):
    out = fade_rate(pairs, SPEC, n_boot=200)
    assert out["status"] == "estimated"
    # One pack: the truth is near the estimate. Whether the 90 % band holds it in 90 % of
    # packs is check V6 (validation/synthetic_coverage.py, 200 packs).
    half = (out["rate_points_per_year_high"] - out["rate_points_per_year_low"]) / 2.0
    assert abs(out["rate_points_per_year"] + PACK.fade_points_per_year) < 2.0 * half
    assert 0.1 < half < 1.0
    assert out["offset_current_a"] == pytest.approx(PACK.hidden_current_a, abs=0.02)


def test_estimate_table(pairs):
    est = estimate_capacity(None, SPEC, pairs=pairs)
    assert est.attrs["config_hash"] == config_hash(CapacityConfig())
    ok = est[est["status"] == "estimated"]
    assert len(ok) >= len(est) - 1
    assert (ok["capacity_ah_low"] < ok["capacity_ah"]).all() and (ok["capacity_ah"] < ok["capacity_ah_high"]).all()
    # The usable capacity between the BMS stops lies a few points below the 0-100 % capacity.
    gap = (PACK.true_capacity_ah(ok.index) - ok["capacity_ah"]) / PACK.capacity_ah
    assert gap.between(0.0, 0.10).all()
    assert (ok["reference_capacity_ah"] == SPEC.reference_capacity_ah).all()


def test_batch_and_core_agree(pairs):
    config = CapacityConfig()
    est = estimate_capacity(None, SPEC, pairs=pairs, config=config)
    core = run_core(pairs, SPEC, config, est.index)
    got = np.array([c.theta[0] for c in core])
    np.testing.assert_allclose(got, est["capacity_ah"].to_numpy(), rtol=1e-6, equal_nan=True)


def test_not_possible_without_full_to_empty_pairs(pairs):
    same = pairs[pairs["x"] == 0].copy()
    est = estimate_capacity(None, SPEC, pairs=same, at=[same["t_end"].iloc[-1]])
    assert est["status"].iloc[0].startswith("not possible from this data")
    assert np.isnan(est["capacity_ah"].iloc[0])


def test_not_possible_in_the_level_warm_up(candidates):
    # With the default settings the first year of the record only sets the levels.
    rests = classify_rests(candidates, SPEC)
    ready = levels_from(rests)
    assert ready == rests.loc[rests["accepted"], "start"].min() + pd.Timedelta(days=CapacityConfig().level_warmup_days)
    at = [ready - pd.Timedelta(days=30), ready + pd.Timedelta(days=150)]
    est = estimate_capacity(None, SPEC, rests=rests, at=at)
    assert est["status"].iloc[0] == _core.STATUS_TEXT[_core.NO_LEVELS]
    assert np.isnan(est["capacity_ah"].iloc[0])
    assert est["status"].iloc[1] == "estimated"
    # With pairs alone the time must be passed; then the same reason is given.
    pairs = rest_pairs(rest_chain(rests, SPEC))
    alone = estimate_capacity(None, SPEC, pairs=pairs, at=at, levels_from=ready)
    assert list(alone["status"]) == list(est["status"])
    assert alone["capacity_ah"].iloc[1] == pytest.approx(est["capacity_ah"].iloc[1], rel=1e-12)


def test_fade_rate_needs_a_span(pairs):
    short = pairs[pairs["t_end"] < pairs["t_end"].iloc[0] + pd.Timedelta(days=200)]
    out = fade_rate(short, SPEC, n_boot=0)
    assert out["status"] == _core.STATUS_TEXT[_core.SHORT_SPAN]
    assert np.isnan(out["rate_points_per_year"])
