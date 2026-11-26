"""End-to-end test of the capacity estimator on a synthetic pack with a known fade."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from besslib.soh.capacity import estimate_capacity, fade_rate, run_core, voltage_terms
from besslib.frames import PackSpec
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
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


def window_fade(pairs) -> float:
    """The fade the voltage terms measure: the pack's fade times the state-of-charge window
    between the record's mean full and mean empty rest voltage, on the simulator's own
    open-circuit voltage curve (points per year)."""
    import synth

    du_f, du_e, r0f, r0e = voltage_terms(pairs)
    kind = pairs["kind"].to_numpy()
    full_u = np.r_[pairs["u_start"].to_numpy()[[k[0] == "F" for k in kind]], pairs["u_end"].to_numpy()[[k[-1] == "F" for k in kind]]]
    empty_u = np.r_[pairs["u_start"].to_numpy()[[k[0] == "E" for k in kind]], pairs["u_end"].to_numpy()[[k[-1] == "E" for k in kind]]]
    soc = lambda v: np.interp(v / PACK.cells_in_series, synth._OCV_V, synth._OCV_SOC)  # noqa: E731
    return -PACK.fade_points_per_year * float(soc(np.mean(full_u)) - soc(np.mean(empty_u)))


def test_fade_rate_near_the_truth(pairs):
    out = fade_rate(pairs, SPEC, n_boot=200)
    assert out["status"] == "estimated"
    # One pack: the truth is near the estimate. Whether the 90 % band holds it in 90 % of
    # packs is check V6 (validation/synthetic_coverage.py, 200 packs). With the voltage
    # terms the rate is that of the capacity between two fixed rest voltages.
    half = (out["rate_points_per_year_high"] - out["rate_points_per_year_low"]) / 2.0
    assert abs(out["rate_points_per_year"] - window_fade(pairs)) < 2.0 * half
    assert 0.01 < half < 1.0
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


def test_pairs_carry_the_relaxed_voltage_of_both_rests(candidates):
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    chain = rest_chain(classify_rests(candidates, SPEC, config), SPEC, config)
    pairs = rest_pairs(chain, config)
    assert np.isfinite(chain["ocv_v"]).all()
    keep = (chain["new_segment"] == 0).to_numpy()[1:]
    np.testing.assert_array_equal(pairs["u_start"].to_numpy(), chain["ocv_v"].to_numpy()[:-1][keep])
    np.testing.assert_array_equal(pairs["u_end"].to_numpy(), chain["ocv_v"].to_numpy()[1:][keep])
    # The voltage terms on every pair: each end its own kind's voltage minus that kind's first rest.
    du_f, du_e, r0f, r0e = voltage_terms(pairs)
    kind = pairs["kind"].to_numpy()
    first = pairs.iloc[0]
    assert r0f == (first["u_start"] if kind[0][0] == "F" else (first["u_end"] if kind[0][-1] == "F" else r0f))
    same_f, same_e = kind == "F->F", kind == "E->E"
    np.testing.assert_allclose(du_f[same_f], (pairs["u_end"] - pairs["u_start"]).to_numpy()[same_f], atol=1e-12)
    np.testing.assert_allclose(du_e[same_e], (pairs["u_end"] - pairs["u_start"]).to_numpy()[same_e], atol=1e-12)
    fe = kind == "F->E"
    np.testing.assert_allclose(du_f[fe], -(pairs["u_start"].to_numpy()[fe] - r0f), atol=1e-12)
    np.testing.assert_allclose(du_e[fe], pairs["u_end"].to_numpy()[fe] - r0e, atol=1e-12)
    assert (du_e[same_f] == 0).all() and (du_f[same_e] == 0).all()
    assert same_f.any() and same_e.any() and fe.any()


def test_settings_of_release_0_1_give_its_fit(pairs):
    old = estimate_capacity(None, SPEC, pairs=pairs, config=CapacityConfig().v0_1())
    new = estimate_capacity(None, SPEC, pairs=pairs)
    ok = old[old["status"] == "estimated"]
    assert (ok["terms"] == "C+beta").all()
    np.testing.assert_allclose(ok["dof"], ok["n_pairs"] - 2, rtol=1e-12)
    assert ok[["trend_ah_per_year", "gamma_full_ah_per_v", "gamma_empty_ah_per_v"]].isna().all().all()
    used = new.loc[new["status"] == "estimated", "terms"]
    assert used.str.startswith("C+beta+r").all() and used.str.contains("gamma_full").any()
    fade_old = fade_rate(pairs, SPEC, CapacityConfig().v0_1(), n_boot=0)
    fade_new = fade_rate(pairs, SPEC, n_boot=50)
    assert fade_old["terms"] == "C+beta+r" and fade_old["dof"] == pytest.approx(fade_old["n_pairs"] - 3, rel=1e-12)
    assert "gamma_full" in fade_new["terms"] and np.isfinite(fade_new["rate_boot_low"])
    # 0.1 against the cell fade (its band is wide); 0.2 against window_fade (test above).
    half_old = (fade_old["rate_points_per_year_high"] - fade_old["rate_points_per_year_low"]) / 2.0
    assert abs(fade_old["rate_points_per_year"] + PACK.fade_points_per_year) < 2.0 * half_old


# --- version 0.2: plausibility rule 1 (levels crossed) and rule 4 (fade rate) ---------------------------------


def _crossed_rests(candidates):
    """Rests whose empty level has reached the full level from the middle of the record on."""
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(candidates, SPEC, config)
    middle = rests["start"].iloc[len(rests) // 2]
    late = rests["start"] >= middle
    rests.loc[late, "level_empty_v"] = rests.loc[late, "level_full_v"] + 0.1
    return config, rests, middle


def test_crossed_levels_give_status_12(candidates):
    config, rests, middle = _crossed_rests(candidates)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)
    at = [middle - pd.Timedelta(days=20), rests["start"].iloc[-1]]
    ready = levels_from(rests, config)
    crossed = estimate_capacity(None, SPEC, rests=rests, pairs=pairs, config=config, at=at, levels_from=ready)
    assert crossed["status"].iloc[0] != _core.STATUS_TEXT[_core.LEVELS_CROSSED]
    assert crossed["status"].iloc[1] == _core.STATUS_TEXT[_core.LEVELS_CROSSED]
    assert np.isnan(crossed["capacity_ah"].iloc[1])
    # release 0.1.0 settings: no rule; with pairs alone the levels are not known, the rule is not checked
    old = estimate_capacity(None, SPEC, rests=rests, pairs=pairs, config=config.v0_1(), at=at, levels_from=ready)
    assert old["status"].iloc[1] != _core.STATUS_TEXT[_core.LEVELS_CROSSED]
    alone = estimate_capacity(None, SPEC, pairs=pairs, config=config, at=at, levels_from=ready)
    assert alone["status"].iloc[1] != _core.STATUS_TEXT[_core.LEVELS_CROSSED]


def test_fade_rate_checked_follows_the_rules(candidates):
    from besslib.soh.capacity import fade_rate_checked

    config, rests, middle = _crossed_rests(candidates)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)
    table = estimate_capacity(None, SPEC, rests=rests, pairs=pairs, config=config, levels_from=levels_from(rests, config))
    out = fade_rate_checked(table, pairs, SPEC, config, n_boot=0)
    assert out["status"].startswith("not possible from this data: the capacity is implausible at ")
    assert np.isnan(out["rate_points_per_year"])
    first = table.index[table["status"] == _core.STATUS_TEXT[_core.LEVELS_CROSSED]][0]
    assert out["first_implausible_date"] == first and out["first_implausible_rule"] == 1
    # no rule held (the real levels), or the rules off: fade_rate's own result
    clean = estimate_capacity(None, SPEC, rests=classify_rests(candidates, SPEC, config), pairs=pairs, config=config,
                              levels_from=levels_from(rests, config))
    assert fade_rate_checked(clean, pairs, SPEC, config, n_boot=0)["status"] == fade_rate(pairs, SPEC, config, n_boot=0)["status"]
    off = fade_rate_checked(table, pairs, SPEC, config.v0_1(), n_boot=0)
    assert "first_implausible_date" not in off


def test_gain_term_is_a_rectangular_half_width(pairs):
    # Version 0.2: the 0.5 % gain accuracy is a half-width a; the band adds a / sqrt(3) (JCGM 100:2008, 4.3.7).
    at = [pairs["t_end"].iloc[-1]]
    new = estimate_capacity(None, SPEC, pairs=pairs, at=at).iloc[0]
    same = estimate_capacity(None, SPEC, pairs=pairs, at=at,
                             config=CapacityConfig(gain_rectangular=False, gain_error=0.005 / np.sqrt(3.0))).iloc[0]
    old = estimate_capacity(None, SPEC, pairs=pairs, at=at, config=CapacityConfig(gain_rectangular=False)).iloc[0]
    assert new["capacity_ah_high"] == pytest.approx(same["capacity_ah_high"], rel=1e-12)
    assert new["capacity_ah_high"] - new["capacity_ah"] < old["capacity_ah_high"] - old["capacity_ah"]


# --- version 0.3: the range rules (full or empty is not the pack's) and the time between the readings ----------


def _limits(v_max, v_min):
    return replace(SPEC, voltage_max_v=v_max, voltage_min_v=v_min)


def _last_levels(rests):
    last = rests.dropna(subset=["level_full_v", "level_empty_v"]).iloc[-1]
    return float(last["level_full_v"]), float(last["level_empty_v"])


def test_levels_inside_the_documented_limits_are_refused(candidates):
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(candidates, SPEC, config)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)
    full, empty = _last_levels(rests)
    span, at = full - empty, [rests["start"].iloc[-1]]

    def status(spec, config=config, rests=rests):
        return estimate_capacity(None, spec, rests=rests, pairs=pairs, config=config, at=at)["status"].iloc[0]

    refused = _core.STATUS_TEXT[_core.INSIDE_LIMITS]
    assert status(SPEC) == "estimated"  # no limits given: not checked
    assert status(replace(SPEC, voltage_max_v=full + 10 * span)) == "estimated"  # one limit alone is no window
    assert status(_limits(full, empty)) == "estimated"
    # 25 % of the window: the window is span / (1 - g) when one end lies g inside it
    for g, expected in ((0.24, "estimated"), (0.26, refused)):
        assert status(_limits(empty + span / (1.0 - g), empty)) == expected
        assert status(_limits(full, full - span / (1.0 - g))) == expected
    far = _limits(empty + span / 0.5, empty)
    assert status(far, config=config.v0_2()) == "estimated"  # release 0.2.0 has no such rule
    assert status(far, config=config.with_(limits_share=0.6)) == "estimated"
    alone = estimate_capacity(None, far, pairs=pairs, config=config, at=at)
    assert alone["status"].iloc[0] == "estimated"  # with pairs alone the levels are not known
    with pytest.raises(ValueError, match="voltage_max_v"):
        status(_limits(empty, full))


def test_charges_above_the_full_level_are_refused(candidates):
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(candidates, SPEC, config)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)
    full, empty = _last_levels(rests)
    at = rests["start"].iloc[-1]
    charges = rests.index[rests["accepted"] & (rests["direction"] == "charge") & (rests["start"] > at - pd.Timedelta(days=60))]

    def status(n, share=0.12, config=config, direction=None, when=at, accepted=True):
        changed = rests.copy()
        changed.loc[charges[-n:] if n else [], "edge_v"] = full + share * (full - empty)
        if direction:
            changed.loc[charges[-n:], "direction"] = direction
        if not accepted:
            changed.loc[charges[-n:], "accepted"] = False
        return estimate_capacity(None, SPEC, rests=changed, pairs=pairs, config=config, at=[when])["status"].iloc[0]

    refused = _core.STATUS_TEXT[_core.ABOVE_FULL]
    assert status(0) == "estimated" and status(4) == "estimated"
    assert status(5) == refused
    assert status(5, share=0.08) == "estimated"
    assert status(5, direction="discharge") == "estimated"  # only rests after a charge count
    assert status(5, accepted=False) == "estimated"  # and only accepted ones
    assert status(5, config=config.v0_2()) == "estimated"
    # causal: before those rests the rule does not hold
    assert status(5, when=rests["start"].loc[charges[-5]] - pd.Timedelta(hours=1)) == "estimated"
    # the trailing level window, not the whole record
    assert status(5, config=config.with_(level_window_days=0.001)) == "estimated"


def test_fade_rate_is_refused_with_the_range_rules(candidates):
    from besslib.soh.capacity import fade_rate_checked

    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(candidates, SPEC, config)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)
    full, empty = _last_levels(rests)
    spec = _limits(empty + (full - empty) / 0.5, empty)
    table = estimate_capacity(None, spec, rests=rests, pairs=pairs, config=config)
    out = fade_rate_checked(table, pairs, spec, config, n_boot=0)
    assert out["status"].startswith("not possible from this data: the capacity is implausible at ")
    assert out["first_implausible_rule"] == 5 and np.isnan(out["rate_points_per_year"])
    # rule 6: charges end above the full level (rule A), no limits given
    at = rests["start"].iloc[-1]
    charges = rests.index[rests["accepted"] & (rests["direction"] == "charge") & (rests["start"] > at - pd.Timedelta(days=60))]
    high = rests.copy()
    high.loc[charges[-5:], "edge_v"] = full + 0.12 * (full - empty)
    table = estimate_capacity(None, SPEC, rests=high, pairs=pairs, config=config, at=[at])
    assert table["status"].iloc[0] == _core.STATUS_TEXT[_core.ABOVE_FULL]
    out = fade_rate_checked(table, pairs, SPEC, config, n_boot=0)
    assert out["first_implausible_rule"] == 6 and np.isnan(out["rate_points_per_year"])
    assert out["status"].endswith("(charges end above the full level)")


def test_the_time_of_a_pair_is_between_the_readings(candidates):
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    chain = rest_chain(classify_rests(candidates, SPEC, config), SPEC, config)
    new, old = rest_pairs(chain, config), rest_pairs(chain, config.v0_2())
    wait = chain["duration_min"].clip(upper=config.ocv_fit_max_min).to_numpy() / 1440.0
    keep = (chain["new_segment"] == 0).to_numpy()[1:]
    np.testing.assert_allclose(new["dt_days"] - old["dt_days"], np.diff(wait)[keep], atol=1e-12)
    start = (chain["time"].diff().dt.total_seconds() / 86400.0).to_numpy()[1:][keep]
    np.testing.assert_allclose(old["dt_days"], start, atol=1e-12)
    assert (new["dt_days"] > 0).all() and (new["dt_days"] != old["dt_days"]).any()
    same = [c for c in new.columns if c != "dt_days"]
    pd.testing.assert_frame_equal(new[same], old[same])


def test_settings_keep_their_release_hashes():
    assert config_hash(CapacityConfig()) == "b30137b3f868"  # version 0.3, frozen before its held-out run
    assert CapacityConfig().quad_min_rests == 2.0  # the gate as tuned (tune_quadratic.py)
    assert config_hash(CapacityConfig().v0_2()) == "b3959941c19b"
    assert config_hash(CapacityConfig().v0_1()) == "109e9eb1efa5"
    # the release settings do not depend on the order they are asked in
    assert config_hash(CapacityConfig().v0_1().v0_2()) == "b3959941c19b"
    assert config_hash(RecordConfig().v0_1().v0_2()) == config_hash(RecordConfig()) == "d49cbfdcd5b6"
    assert len({config_hash(c) for c in (CapacityConfig(), CapacityConfig().v0_2(), CapacityConfig().v0_1())}) == 3
    # every 0.3 setting moves the hash
    base = CapacityConfig().v0_2()
    for change in (dict(voltage_terms="every_pair_quadratic"), dict(rest_time="reading"), dict(quad_min_rests=5.0),
                   dict(above_full_min_rests=5), dict(above_full_share=0.1), dict(limits_share=0.25)):
        assert config_hash(base.with_(**change)) != config_hash(base), change
    with pytest.raises(ValueError, match="rest_time"):
        CapacityConfig(rest_time="end")


def test_the_gate_of_the_quadratic_terms_reaches_the_estimate(candidates):
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(candidates, SPEC, config)
    pairs = rest_pairs(rest_chain(rests, SPEC, config), config)

    def terms(q):
        return estimate_capacity(None, SPEC, rests=rests, pairs=pairs, config=config.with_(quad_min_rests=q))["terms"]

    open_, some, closed = terms(0.0), terms(16.0), terms(1e9)
    assert open_.str.contains("gamma_full2").all() and open_.str.contains("gamma_empty2").all()
    assert not closed.str.contains("2").any()  # linear terms only
    assert closed.str.contains("gamma_full").all() and closed.str.contains("gamma_empty").all()
    assert (some != open_).any() and (some == open_).any()  # a month end with too few rests drops them


@pytest.mark.parametrize("limits, message", [
    (dict(voltage_max_v=float("nan"), voltage_min_v=40.0), "positive number"),
    (dict(voltage_max_v=58.4, voltage_min_v=-1.0), "positive number"),
    (dict(voltage_max_v=40.0, voltage_min_v=58.4), "must lie above"),
    (dict(voltage_max_v=3.65, voltage_min_v=2.5), "not of one cell"),  # one cell's limits given for the pack
    (dict(voltage_max_v=3.65), "not of one cell"),
    (dict(voltage_min_v=52.0), "at or above the pack's nominal voltage"),
])
def test_pack_spec_refuses_limits_that_are_not_the_packs(limits, message):
    with pytest.raises(ValueError, match=message):
        PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.12, cells_in_series=16, **limits)
    PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.12, cells_in_series=16, voltage_max_v=58.4, voltage_min_v=40.0)
