"""Tests of the round-trip efficiency over closed loops."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from besslib.soh import _core
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.efficiency import (
    NO_ENERGY_OUT,
    NO_REST_ERROR,
    NO_SHARED_REST,
    OUT_EXCEEDS_IN,
    TOO_FEW_LOOPS,
    efficiency_loops,
    estimate_efficiency,
)
from besslib.soh.rests import detect_rests_in_parts, rest_chain
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()
OK = _core.STATUS_TEXT[_core.OK]
# Four months of a pack without seasons: a 60-day level window and no warm-up.
SHORT = CapacityConfig(level_window_days=60.0, level_warmup_days=0.0)


def labelled(kinds, eta=0.95, hours=12.0, start="2021-03-01", gaps=()):
    """Hand-made labelled rests: alternating discharges and charges of 50 Ah at 50 V.

    Each discharge delivers ``eta`` times the energy the next charge puts in. A rest
    index in ``gaps`` is preceded by 2000 s of missing data (a break of the chain).
    """
    rows = []
    t = pd.Timestamp(start)
    q = thr = e_in = e_out = missing = 0.0
    for k, kind in enumerate(kinds):
        if k:
            charge = kind == "full"
            thr += 50.0
            q += 50.0 if charge else -50.0
            if charge:
                e_in += 2500.0
            else:
                e_out += 2500.0 * eta
        if k in gaps:
            missing += 2000.0
        rows.append(
            dict(start=t, kind=kind, charge_ah=q, throughput_ah=thr, missing_s=missing, edge_v=50.0,
                 temp_pack_c=25.0, energy_in_wh=e_in, energy_out_wh=e_out, accepted=True)
        )
        t += pd.Timedelta(hours=hours)
    return pd.DataFrame(rows)


def test_loops_between_full_rests():
    rests = labelled(["full", "empty", "full", "empty", "full", "other", "full"])
    chain = rest_chain(rests, SPEC, CapacityConfig())
    loops = efficiency_loops(rests, chain)
    assert len(loops) == 3
    assert list(loops["t_start"]) == list(rests.loc[[0, 2, 4], "start"])
    assert list(loops["t_end"]) == list(rests.loc[[2, 4, 6], "start"])
    np.testing.assert_allclose(loops["energy_in_wh"], [2500.0, 2500.0, 2500.0])
    np.testing.assert_allclose(loops["energy_out_wh"], [2375.0, 2375.0, 2375.0])
    # The last loop passes an "other" rest, which is not in the chain; it holds one discharge
    # and one charge, as the others.
    np.testing.assert_allclose(loops["ah_in"], [50.0, 50.0, 50.0])
    np.testing.assert_allclose(loops["ah_out"], [50.0, 50.0, 50.0])
    assert list(loops["linked"]) == [0, 1, 1]


def test_a_loop_never_crosses_a_break():
    rests = labelled(["full", "empty", "full", "empty", "full", "empty", "full"], gaps=(3,))
    chain = rest_chain(rests, SPEC, CapacityConfig())
    assert chain["reason"].iloc[3] == "gap"
    loops = efficiency_loops(rests, chain)
    assert list(loops["t_start"]) == list(rests.loc[[0, 4], "start"])
    assert list(loops["linked"]) == [0, 0]
    empty = efficiency_loops(rests, chain, kind="empty")
    assert list(empty["t_start"]) == list(rests.loc[[3], "start"])  # 1 -> 3 crosses the break; 3 -> 5 does not


def test_mask_breaks_the_loops():
    rests = labelled(["full", "empty"] * 6 + ["full"])
    mask = [(rests["start"].iloc[5] - pd.Timedelta(hours=1), rests["start"].iloc[5] + pd.Timedelta(hours=1))]
    chain = rest_chain(rests, SPEC, CapacityConfig(), mask=mask)
    loops = efficiency_loops(rests, chain)
    assert not ((loops["t_start"] < mask[0][0]) & (loops["t_end"] > mask[0][1])).any()
    assert len(loops) == 5  # six loops, one of them spans the masked rest


def test_rests_need_energy_counters():
    rests = labelled(["full", "empty", "full"]).drop(columns=["energy_in_wh"])
    with pytest.raises(ValueError):
        efficiency_loops(rests, rest_chain(rests, SPEC, CapacityConfig()))


def test_statuses_on_hand_made_loops():
    short = CapacityConfig(level_warmup_days=0.0)
    kinds = ["full", "empty"] * 12 + ["full"]
    rests = labelled(kinds)
    at = [rests["start"].iloc[6], rests["start"].iloc[-1]]
    est = estimate_efficiency(None, SPEC, rests=rests, capacity_config=short, at=at)
    assert est["status"].iloc[0] == TOO_FEW_LOOPS and math.isnan(est["round_trip_efficiency"].iloc[0])
    last = est.iloc[1]
    assert last["status"] == OK
    assert last["round_trip_efficiency"] == pytest.approx(0.95)
    assert last["coulombic_efficiency"] == pytest.approx(1.0)
    assert last["voltage_efficiency"] == pytest.approx(0.95)
    # Energy out above energy in is never an estimate.
    over = estimate_efficiency(None, SPEC, rests=labelled(kinds, eta=1.02), capacity_config=short, at=at[1:])
    assert over["status"].iloc[0] == OUT_EXCEEDS_IN and math.isnan(over["round_trip_efficiency"].iloc[0])
    zero = estimate_efficiency(None, SPEC, rests=labelled(kinds, eta=0.0), capacity_config=short, at=at[1:])
    assert zero["status"].iloc[0] == NO_ENERGY_OUT
    # With the default one-year warm-up no level exists yet: the warm-up reason.
    warm = estimate_efficiency(None, SPEC, rests=rests, at=at[1:])
    assert warm["status"].iloc[0] == _core.STATUS_TEXT[_core.NO_LEVELS]
    # Loops that close exactly: no rest error, a band of zero width.
    assert last["rest_error_wh"] == pytest.approx(0.0, abs=1e-6)
    assert last["round_trip_efficiency_high"] - last["round_trip_efficiency_low"] == pytest.approx(0.0, abs=1e-12)


def test_band_statuses_on_hand_made_loops():
    short = CapacityConfig(level_warmup_days=0.0)
    kinds = ["full", "empty"] * 12 + ["full"]
    at = [labelled(kinds)["start"].iloc[-1]]
    # Breaks between the loops (a gap before each empty rest): every loop closes, none
    # shares its start rest with the loop before it.
    rests = labelled(kinds, gaps=tuple(range(1, len(kinds), 4)))
    loops = efficiency_loops(rests, rest_chain(rests, SPEC, short))
    assert len(loops) >= 5 and loops["linked"].sum() == 0
    est = estimate_efficiency(None, SPEC, rests=rests, capacity_config=short, at=at).iloc[0]
    assert est["status"] == NO_SHARED_REST and math.isnan(est["round_trip_efficiency"])
    # Linked loops whose own efficiency drifts slowly, with no rest error: the lag-1
    # products of the scores are positive, so no rest error is seen and there is no band.
    drift = labelled(kinds)
    out = drift["energy_out_wh"].to_numpy(float)
    gain = 1.0 + 0.02 * np.sin(np.arange(len(drift)) / 4.0)
    drift["energy_out_wh"] = np.r_[0.0, np.cumsum(np.diff(out) * gain[1:])]
    est = estimate_efficiency(None, SPEC, rests=drift, capacity_config=short, at=at).iloc[0]
    assert est["status"] == NO_REST_ERROR and est["rho"] > 0.0


@pytest.fixture(scope="module")
def candidates():
    return detect_rests_in_parts(frames(PACK, 4, step_s=10, seed=3), SPEC, SHORT)


def test_synthetic_pack(candidates):
    from besslib.soh import _ratio
    from besslib.soh.capacity import days

    config = RecordConfig()
    full = estimate_efficiency(None, SPEC, rests=candidates, capacity_config=SHORT)
    empty = estimate_efficiency(None, SPEC, rests=candidates, capacity_config=SHORT, kind="empty")
    for est in (full, empty):
        ok = est[est["status"] == OK]
        assert len(ok) >= 2
        assert ((ok["round_trip_efficiency_low"] < ok["round_trip_efficiency"])
                & (ok["round_trip_efficiency"] < ok["round_trip_efficiency_high"])).all()
        assert ok["round_trip_efficiency"].between(0.90, 0.99).all()
        np.testing.assert_allclose(ok["voltage_efficiency"] * ok["coulombic_efficiency"], ok["round_trip_efficiency"])
        # The value is the decayed ratio of the loops' sums.
        loops = est.attrs["loops"]
        t_end = days(loops["t_end"])
        at = days(ok.index)[-1]
        lam = np.exp(-(at - t_end[t_end <= at]) / config.tau_days)
        sel = t_end <= at
        ratio = (lam * loops["energy_out_wh"][sel]).sum() / (lam * loops["energy_in_wh"][sel]).sum()
        assert ok["round_trip_efficiency"].iloc[-1] == pytest.approx(ratio, rel=1e-12)
        # The step-by-step core gives the same.
        core = _ratio.run_loop_core(t_end, loops["energy_in_wh"], loops["energy_out_wh"], loops["linked"],
                                     days(ok.index), config.tau_days, 0.90, config.efficiency_min_loops, 0.0, 1.0)
        np.testing.assert_allclose(core["value"], ok["round_trip_efficiency"], rtol=1e-9)
        np.testing.assert_allclose(core["high"], ok["round_trip_efficiency_high"], rtol=1e-9)
    # Full and empty loops of the same record agree within their bands.
    a, b = full.iloc[-1], empty.iloc[-1]
    assert abs(a["round_trip_efficiency"] - b["round_trip_efficiency"]) < (
        a["round_trip_efficiency_high"] - a["round_trip_efficiency_low"] + b["round_trip_efficiency_high"]
        - b["round_trip_efficiency_low"]
    )


def test_mask_leaves_its_loops_out(candidates):
    full = estimate_efficiency(None, SPEC, rests=candidates, capacity_config=SHORT)
    loops = full.attrs["loops"]
    mid = loops["t_end"].iloc[len(loops) // 2]
    mask = [(mid - pd.Timedelta(days=2), mid + pd.Timedelta(days=2))]
    masked = estimate_efficiency(None, SPEC, rests=candidates, capacity_config=SHORT, mask=mask).attrs["loops"]
    assert len(masked) < len(loops)
    assert not ((masked["t_end"] >= mask[0][0]) & (masked["t_start"] <= mask[0][1])).any()
