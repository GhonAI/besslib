"""Tests of rest detection, classification and the rest chain, on the synthetic pack."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib.soh.config import CapacityConfig
from besslib.soh.rests import (
    classify_rests,
    counters_at,
    detect_rests,
    detect_rests_in_parts,
    find_rests,
    rest_chain,
    rest_pairs,
)
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()


@pytest.fixture(scope="module")
def months():
    return list(frames(PACK, 3, step_s=10, seed=2))


def test_rests_are_found_and_labelled(months):
    rests = find_rests(pd.concat(months), SPEC)
    kinds = rests["kind"].value_counts()
    assert kinds.get("full", 0) > 20 and kinds.get("empty", 0) > 20
    full = rests[rests["kind"] == "full"]
    empty = rests[rests["kind"] == "empty"]
    assert (full["direction"] == "charge").all() and (empty["direction"] == "discharge").all()
    assert full["edge_v"].min() > empty["edge_v"].max()
    # The relaxation fit moves the voltage towards rest: down after a charge, up after a discharge.
    assert (full["ocv_v"] <= full["edge_v"]).mean() > 0.9
    assert (empty["ocv_v"] >= empty["edge_v"]).mean() > 0.9


def test_parts_give_the_same_rests_as_one_frame(months):
    config = CapacityConfig()
    whole = pd.concat(months)
    one = detect_rests(whole, SPEC, config)
    parts = detect_rests_in_parts(iter(months), SPEC, config)
    assert list(one["start"]) == list(parts["start"])
    for col in ("charge_ah", "throughput_ah", "missing_s", "edge_v"):
        np.testing.assert_allclose(parts[col].to_numpy(float), one[col].to_numpy(float), atol=1e-8)


def test_counters_count_a_gap_as_missing(months):
    frame = pd.concat(months[:1])
    cut = frame.drop(frame.index[1000:1090])  # 90 samples of 10 s = 900 s
    _, _, _, missing, _ = counters_at(cut, cut.index[-1])
    assert missing == pytest.approx(900.0)


def test_throughput_filter():
    config = CapacityConfig(min_throughput=0.2)
    base = dict(end=pd.Timestamp("2020-01-01"), duration_min=30.0, truncated=False, v_end=50.0, ocv_v=50.0,
                ocv_sigma_v=0.0, temp_pack_c=25.0, charge_ah=0.0, missing_s=0.0, interpolated_s=0.0)
    rows = [
        dict(base, start=pd.Timestamp("2020-01-01 01:00"), edge_v=58.0, edge_current_a=5.0, throughput_ah=0.0, throughput_end_ah=0.0),
        dict(base, start=pd.Timestamp("2020-01-01 02:00"), edge_v=58.0, edge_current_a=5.0, throughput_ah=10.0, throughput_end_ah=10.0),
        dict(base, start=pd.Timestamp("2020-01-01 03:00"), edge_v=58.0, edge_current_a=5.0, throughput_ah=25.0, throughput_end_ah=25.0),
    ]
    out = classify_rests(pd.DataFrame(rows), SPEC, config)
    assert list(out["accepted"]) == [True, False, True]  # 10 Ah < 20 % of 100 Ah; 25 Ah is enough


def test_mask_breaks_the_chain(months):
    rests = find_rests(pd.concat(months), SPEC)
    mid = rests["start"].iloc[len(rests) // 2]
    mask = [(mid - pd.Timedelta(days=1), mid + pd.Timedelta(days=1))]
    chain = rest_chain(rests, SPEC, mask=mask)
    assert not ((chain["time"] >= mask[0][0]) & (chain["time"] <= mask[0][1])).any()
    first_after = chain[chain["time"] > mask[0][1]].iloc[0]
    assert first_after["new_segment"] == 1 and first_after["reason"] == "mask"
    pairs = rest_pairs(chain)
    assert (pairs["linked"].iloc[1:] == 1).mean() > 0.9
    assert not ((pairs["t_start"] < mask[0][0]) & (pairs["t_end"] > mask[0][1])).any()
