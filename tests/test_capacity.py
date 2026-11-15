"""End-to-end test of the capacity estimator on a synthetic pack with a known fade."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib.soh.capacity import estimate_capacity, fade_rate, run_core
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import classify_rests, detect_rests_in_parts, rest_chain, rest_pairs
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()


@pytest.fixture(scope="module")
def pairs():
    config = CapacityConfig()
    candidates = detect_rests_in_parts(frames(PACK, 18, step_s=20, seed=4), SPEC, config)
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
