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
# The synthetic pack has no seasons and its record is three months long, shorter than the
# default one-year level window and warm-up; these tests use a 90-day window and no warm-up.
SHORT = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)


@pytest.fixture(scope="module")
def months():
    return list(frames(PACK, 3, step_s=10, seed=2))


def test_rests_are_found_and_labelled(months):
    rests = find_rests(pd.concat(months), SPEC, SHORT)
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
    _, _, _, missing, *_ = counters_at(cut, cut.index[-1])
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
    rests = find_rests(pd.concat(months), SPEC, SHORT)
    mid = rests["start"].iloc[len(rests) // 2]
    mask = [(mid - pd.Timedelta(days=1), mid + pd.Timedelta(days=1))]
    chain = rest_chain(rests, SPEC, SHORT, mask=mask)
    assert not ((chain["time"] >= mask[0][0]) & (chain["time"] <= mask[0][1])).any()
    first_after = chain[chain["time"] > mask[0][1]].iloc[0]
    assert first_after["new_segment"] == 1 and first_after["reason"] == "mask"
    pairs = rest_pairs(chain, SHORT)
    assert (pairs["linked"].iloc[1:] == 1).mean() > 0.9
    assert not ((pairs["t_start"] < mask[0][0]) & (pairs["t_end"] > mask[0][1])).any()


# A hand-built home storage year: a true end of charge at 56 V and a true end of
# discharge at 46 V. In summer (May to September) the battery is rarely emptied: every
# discharge stops shallow, at 50 V. In winter (November to February) it is rarely filled:
# every charge stops part way, at 53 V. In spring and October both limits are reached.
FULL_V, EMPTY_V, SHALLOW_V, PARTIAL_V = 56.0, 46.0, 50.0, 53.0


def seasonal_candidates(years: int = 3) -> pd.DataFrame:
    rows = []
    throughput = 0.0
    for day in pd.date_range("2020-01-01", periods=int(365.25 * years), freq="D"):
        summer, winter = day.month in (5, 6, 7, 8, 9), day.month in (11, 12, 1, 2)
        for hour, current, edge in ((6, -20.0, SHALLOW_V if summer else EMPTY_V), (15, 20.0, PARTIAL_V if winter else FULL_V)):
            throughput += 50.0  # half the 100 Ah pack between rests: every rest passes the throughput filter
            start = day + pd.Timedelta(hours=hour)
            rows.append(
                dict(start=start, end=start + pd.Timedelta(hours=1), duration_min=60.0, truncated=False, edge_v=edge,
                     edge_current_a=current, v_end=edge, ocv_v=edge, ocv_sigma_v=0.01, temp_pack_c=20.0, charge_ah=0.0,
                     throughput_ah=throughput, throughput_end_ah=throughput, missing_s=0.0, interpolated_s=0.0)
            )
    return pd.DataFrame(rows)


def test_no_level_and_no_full_or_empty_rest_in_the_warm_up():
    config = CapacityConfig()
    rests = classify_rests(seasonal_candidates(), SPEC, config)
    ready = rests["start"].iloc[0] + pd.Timedelta(days=config.level_warmup_days)
    early, late = rests[rests["start"] < ready], rests[rests["start"] >= ready]
    assert len(early) > 600 and early["accepted"].all()
    assert early["level_full_v"].isna().all() and early["level_empty_v"].isna().all()
    assert (early["kind"] == "other").all()
    assert late["level_full_v"].notna().all() and late["level_empty_v"].notna().all()
    assert set(late["kind"]) == {"full", "empty", "other"}
    # A shorter warm-up starts the labels sooner, counted from the first accepted rest.
    short = classify_rests(seasonal_candidates(), SPEC, config.with_(level_warmup_days=100.0))
    first = short.loc[short["kind"] != "other", "start"].min()
    assert first >= rests["start"].iloc[0] + pd.Timedelta(days=100) and first < ready


def test_a_year_long_level_window_ignores_the_seasons():
    candidates = seasonal_candidates()
    after = pd.Timestamp("2021-01-01")  # past the warm-up of both settings
    labels = {}
    for days in (90.0, 365.0):
        config = CapacityConfig(level_window_days=days, level_warmup_days=days)
        rests = classify_rests(candidates, SPEC, config)
        labels[days] = rests[rests["start"] >= after]
    short, year = labels[90.0], labels[365.0]
    shallow_short = short[short["edge_v"] == SHALLOW_V]
    partial_short = short[short["edge_v"] == PARTIAL_V]
    # A 90-day window follows the season: late in summer its end-of-discharge level is the
    # shallow stop, late in winter its end-of-charge level is the partial charge.
    assert (shallow_short["kind"] == "empty").mean() > 0.3
    assert (partial_short["kind"] == "full").mean() > 0.3
    # A one-year window keeps the system's limits: shallow and partial stops are other,
    # and every true stop keeps its label.
    assert (year.loc[year["edge_v"].isin([SHALLOW_V, PARTIAL_V]), "kind"] == "other").all()
    assert (year.loc[year["edge_v"] == EMPTY_V, "kind"] == "empty").all()
    assert (year.loc[year["edge_v"] == FULL_V, "kind"] == "full").all()
    np.testing.assert_allclose(year["level_full_v"], FULL_V)
    np.testing.assert_allclose(year["level_empty_v"], EMPTY_V)


def test_no_warning_when_a_slice_has_no_reading(months):
    import warnings

    frame = pd.concat(months).copy()
    frame["temp_pack_c"] = np.nan  # no temperature anywhere: the rests' temperature is NaN, silently
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        rests = detect_rests(frame, SPEC, SHORT)
    assert len(rests) > 0 and rests["temp_pack_c"].isna().all()
    assert rests["edge_v"].notna().all()
