"""Tests of the 1-second resistance, its growth and the power capability derived from it."""

from __future__ import annotations

import math
from dataclasses import replace
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.capacity import days
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.reduce import STEP_COLUMNS, Reduced, reduce_parts
from besslib.soh.resistance import (
    power_label,
    band_centre,
    day_band_events,
    estimate_resistance,
    qualifying_steps,
    resistance_growth,
    resistance_of_steps,
)
from synth import SynthPack, frames

SPEC = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.0, cells_in_series=14)
OK = _core.STATUS_TEXT[_core.OK]
Z90 = NormalDist().inv_cdf(0.95)
CONFIG = RecordConfig()


def step_rows(times, d_i, r, temp=25.0, steady=10.0, i0=0.0, v0=50.0):
    """Hand-made step rows: current ``i0 -> i0 + d_i``, voltage ``v0 -> v0 + r d_i``."""
    n = len(times)
    d_i = np.broadcast_to(np.asarray(d_i, dtype=float), (n,))
    r = np.broadcast_to(np.asarray(r, dtype=float), (n,))
    return pd.DataFrame(
        {
            "time": pd.DatetimeIndex(times),
            "current_before_a": np.full(n, i0),
            "current_after_a": i0 + d_i,
            "voltage_before_v": np.full(n, v0),
            "voltage_after_v": v0 + r * d_i,
            "temp_pack_c": np.broadcast_to(np.asarray(temp, dtype=float), (n,)).copy(),
            "steady_s": np.broadcast_to(np.asarray(steady, dtype=float), (n,)).copy(),
        }
    )[STEP_COLUMNS]


def reduced_of(steps, first="2021-01-01", last=None):
    last = last or pd.Timestamp(steps["time"].max()).normalize()
    daily = pd.DataFrame({"day": pd.date_range(first, last, freq="D")})
    rests = pd.DataFrame(columns=["start", "kind", "ocv_v", "ocv_sigma_v"])
    return Reduced(daily=daily, steps=steps, rests=rests, sample_s=1.0)


def daily_steps(n_days, r_of_day, start="2021-01-01", per_day=4, d_i=(10.0, -20.0, 15.0, -5.0), temp=25.0):
    """``per_day`` steps at noon on each of ``n_days`` days, each day with its own resistance."""
    frames_ = []
    for d in range(n_days):
        t0 = pd.Timestamp(start) + pd.Timedelta(days=d, hours=12)
        times = [t0 + pd.Timedelta(minutes=k) for k in range(per_day)]
        frames_.append(step_rows(times, list(d_i)[:per_day], r_of_day(d), temp=temp))
    return pd.concat(frames_, ignore_index=True)


def test_qualifying_steps_and_bands():
    t = pd.date_range("2021-01-01 12:00", periods=6, freq="min")
    steps = step_rows(t, [4.9, 5.0, -5.0, 20.0, 20.0, 30.0], 0.05, steady=[10.0, 10.0, 10.0, 1.0, 2.0, 10.0])
    got = qualifying_steps(steps, SPEC)  # C/20 = 5 A, steady 2 s
    assert list(got.index) == [1, 2, 4, 5]
    np.testing.assert_allclose(got["d_voltage_v"] / got["d_current_a"], 0.05)
    tight = qualifying_steps(steps, SPEC, CONFIG.with_(min_step_c_rate=0.1, steady_s=10.0))
    assert list(tight.index) == [5]
    masked = qualifying_steps(steps, SPEC, mask=[(t[1], t[2])])
    assert list(masked.index) == [4, 5]
    for bad in (dict(min_step_c_rate=0.04), dict(steady_s=1.0), dict(steady_s=12.0), dict(steady_tolerance=0.1)):
        with pytest.raises(ValueError):
            qualifying_steps(steps, SPEC, CONFIG.with_(**bad))
    np.testing.assert_array_equal(band_centre([22.49, 22.5, 25.0, 27.49, 27.5, -3.0]), [20.0, 25.0, 25.0, 25.0, 30.0, -5.0])
    assert math.isnan(band_centre([np.nan])[0])


def test_day_band_events_by_hand():
    t = pd.to_datetime(["2021-01-01 10:00", "2021-01-01 11:00", "2021-01-01 12:00", "2021-01-02 09:00", "2021-01-02 10:00"])
    steps = step_rows(t, [10.0, -20.0, 10.0, 10.0, 10.0], [0.05, 0.05, 0.06, 0.04, 0.05], temp=[24.0, 26.0, 31.0, 25.0, np.nan])
    events, counts = day_band_events(steps, SPEC)
    assert {k: counts[k] for k in ("n_steps", "n_steps_no_temperature", "n_steps_masked", "n_days_unrefreshed")} == {
        "n_steps": 4, "n_steps_no_temperature": 1, "n_steps_masked": 0, "n_days_unrefreshed": 0}
    assert list(events["temp_band_c"]) == [25.0, 30.0, 25.0]
    np.testing.assert_allclose(events["a"], [100.0 + 400.0, 100.0, 100.0])
    np.testing.assert_allclose(events["b"], [5.0 + 20.0, 6.0, 4.0])
    np.testing.assert_allclose(events["resistance_ohm"], [0.05, 0.06, 0.04])
    assert list(events["n_steps"]) == [2, 1, 1]
    # the event counts at the end of its day
    assert events["t_days"].iloc[0] == pytest.approx(days([pd.Timestamp("2021-01-02")])[0])
    _, counts = day_band_events(steps, SPEC, mask=[(t[0], t[0])])
    assert counts["n_steps_masked"] == 1 and counts["n_steps"] == 3


def test_value_and_band_by_hand():
    rng = np.random.default_rng(1)
    r_day = 0.05 * (1.0 + 0.02 * rng.standard_normal(40))
    steps = daily_steps(40, lambda d: r_day[d])
    at = pd.Timestamp("2021-02-15")
    est = estimate_resistance(None, SPEC, reduced=reduced_of(steps), at=[at])
    row = est[est["headline"]].iloc[0]
    assert row["status"] == OK and row["temp_band_c"] == 25.0
    # by hand: day sums, exponential weights, the sandwich over unlinked days (the
    # score of a day carries its weight), t quantile on m - 1 degrees of freedom
    a_day = 100.0 + 400.0 + 225.0 + 25.0
    t_end = days(pd.date_range("2021-01-02", periods=40, freq="D"))
    lam = np.exp(-(days([at])[0] - t_end) / CONFIG.tau_days)
    r = np.sum(lam * r_day * a_day) / np.sum(lam * a_day)
    e = a_day * (r_day - r)
    m = lam.sum()
    sigma = math.sqrt(np.sum((lam * e) ** 2) / np.sum(lam * a_day) ** 2 * m / (m - 1.0))
    half = math.hypot(_core.t_quantile(Z90, m - 1.0) * sigma, Z90 * CONFIG.resistance_sigma * r)
    assert row["resistance_ohm"] == pytest.approx(r, rel=1e-12)
    assert row["resistance_ohm_low"] == pytest.approx(r - half, rel=1e-10)
    assert row["resistance_ohm_high"] == pytest.approx(r + half, rel=1e-10)
    assert row["n_days"] == pytest.approx(m, rel=1e-12)
    assert row["n_steps"] == pytest.approx(4.0 * m, rel=1e-12)
    assert est.attrs["sample_s"] == 1.0 and len(est.attrs["config_hash"]) == 12


def test_statuses_and_every_band_reported():
    good = daily_steps(30, lambda d: 0.05)
    few = daily_steps(5, lambda d: 0.08, temp=31.0)
    wrong = daily_steps(30, lambda d: -0.01, temp=19.0)
    steps = pd.concat([good, few, wrong], ignore_index=True).sort_values("time", ignore_index=True)
    est = estimate_resistance(None, SPEC, reduced=reduced_of(steps), at=[pd.Timestamp("2021-02-15")])
    by = est.set_index("temp_band_c")
    assert list(by.index) == [20.0, 25.0, 30.0]
    assert list(by["headline"]) == [False, True, False]
    assert by.loc[25.0, "status"] == OK
    assert by.loc[25.0, "resistance_ohm"] == pytest.approx(0.05, rel=1e-12)
    assert by.loc[30.0, "status"] == "not possible from this data: too few current steps near 30 °C"
    assert by.loc[20.0, "status"] == "not possible from this data: the current steps near 20 °C give a resistance at or below zero"
    for band in (20.0, 30.0):
        assert math.isnan(by.loc[band, "resistance_ohm"]) and math.isnan(by.loc[band, "resistance_ohm_low"])
    # No step at all near the reference temperature: the headline band is still reported.
    cold = estimate_resistance(None, SPEC, reduced=reduced_of(wrong), temp_ref_c=10.0, at=[pd.Timestamp("2021-02-15")])
    head = cold[cold["headline"]].iloc[0]
    assert head["temp_band_c"] == 10.0 and head["n_days"] == 0.0
    assert head["status"] == "not possible from this data: too few current steps near 10 °C"
    # Default times: every month end; a mask drops its steps.
    monthly = estimate_resistance(None, SPEC, reduced=reduced_of(good))
    assert list(monthly["time"]) == [pd.Timestamp("2021-01-31")]
    masked = estimate_resistance(None, SPEC, reduced=reduced_of(good), at=[pd.Timestamp("2021-02-15")],
                                 mask=[(pd.Timestamp("2021-01-01"), pd.Timestamp("2021-01-25"))])
    assert masked["status"].iloc[0] == "not possible from this data: too few current steps near 25 °C"
    assert masked.attrs["n_steps_masked"] == 4 * 24  # the mask ends at midnight before the 25th's steps


def test_growth_and_power_share_by_hand():
    r0, g = 0.05, 0.10  # 10 % of the start value per year
    rng = np.random.default_rng(2)
    noise = 0.01 * rng.standard_normal(800)
    steps = daily_steps(800, lambda d: r0 * (1.0 + g * d / 365.25) * (1.0 + noise[d]), per_day=2)
    out = resistance_growth(None, SPEC, reduced=reduced_of(steps))
    assert out["status"] == OK
    assert out["at"] == pd.Timestamp("2021-01-01") + pd.Timedelta(days=800)
    assert out["first_day"] == pd.Timestamp("2021-01-01")
    assert out["n_days"] == 800.0
    assert out["power_capability_label"] == power_label(1.0) == "derived from the 1-second resistance, not measured"
    assert out["growth_percent_per_year"] == pytest.approx(100.0 * g, rel=0.05)
    assert out["growth_percent_per_year_low"] < 100.0 * g < out["growth_percent_per_year_high"]
    # by hand: the weighted through-origin fit b = (k0 + k1 y) a and its sandwich
    events, _ = day_band_events(steps, SPEC)
    t, a, b = events["t_days"].to_numpy(), events["a"].to_numpy(), events["b"].to_numpy()
    y = (t - t[0]) / 365.25
    u = np.column_stack([np.ones_like(y), y])
    amat = (a[:, None, None] * u[:, :, None] * u[:, None, :]).sum(axis=0)
    k = np.linalg.solve(amat, (b[:, None] * u).sum(axis=0))
    e = b - a * (u @ k)
    meat = ((e * e)[:, None, None] * u[:, :, None] * u[:, None, :]).sum(axis=0)
    inv = np.linalg.inv(amat)
    cov = inv @ meat @ inv * len(t) / (len(t) - 2.0)
    kt = _core.t_quantile(Z90, len(t) - 2.0)
    assert out["resistance_start_ohm"] == pytest.approx(k[0], rel=1e-10)
    grad = np.array([-k[1] / k[0] ** 2, 1.0 / k[0]])
    half = kt * math.sqrt(grad @ cov @ grad)
    assert out["growth_percent_per_year"] == pytest.approx(100.0 * k[1] / k[0], rel=1e-10)
    assert out["growth_percent_per_year_high"] - out["growth_percent_per_year"] == pytest.approx(100.0 * half, rel=1e-8)
    y_at = (days([out["at"]])[0] - t[0]) / 365.25
    r_at = k[0] + k[1] * y_at
    assert out["resistance_at_ohm"] == pytest.approx(r_at, rel=1e-10)
    grad = np.array([k[1] * y_at / r_at**2, -k[0] * y_at / r_at**2])
    assert out["power_capability_share"] == pytest.approx(k[0] / r_at, rel=1e-10)
    assert out["power_capability_share_high"] - out["power_capability_share"] == pytest.approx(
        kt * math.sqrt(grad @ cov @ grad), rel=1e-8
    )
    assert out["power_capability_w_status"] == "not possible from this data: voltage limits not given"
    assert math.isnan(out["power_capability_w"])


def test_growth_needs_a_start_and_end_resistance_above_zero():
    # The line starts barely above zero and the days scatter by 10 mOhm: the start value's
    # band reaches below zero, so neither ratio has a usable band.
    steps = daily_steps(800, lambda d: 0.0005 + 0.05 * d / 365.25 + 0.01 * (-1.0) ** d, per_day=2)
    out = resistance_growth(None, SPEC, reduced=reduced_of(steps))
    events, _ = day_band_events(steps, SPEC)
    assert out["n_days"] == 800.0 and len(events) == 800
    assert out["status"] == ("not possible from this data: the growth fit near 25 °C does not separate the resistance "
                             "at the start or at the date from zero")
    for key in ("growth_percent_per_year", "power_capability_share", "resistance_start_ohm"):
        assert math.isnan(out[key])


def test_growth_needs_a_span():
    steps = daily_steps(200, lambda d: 0.05)
    out = resistance_growth(None, SPEC, reduced=reduced_of(steps))
    assert out["status"] == (
        "not possible from this data: the days with current steps near 25 °C span too short a time for a growth rate"
    )
    assert math.isnan(out["growth_percent_per_year"])
    none = resistance_growth(None, SPEC, reduced=reduced_of(steps), temp_ref_c=40.0)
    assert none["status"] == "not possible from this data: too few current steps near 40 °C" and none["n_days"] == 0.0


def test_resistance_of_steps():
    t = pd.date_range("2021-01-01 12:00", periods=3, freq="s")
    steps = step_rows(t, [10.0, -20.0, 30.0], [0.05, 0.06, 0.04])
    out = resistance_of_steps(steps, SPEC)
    a = np.array([100.0, 400.0, 900.0])
    b = np.array([5.0, 24.0, 36.0])
    r = b.sum() / a.sum()
    e = b - r * a
    sigma = math.sqrt((e * e).sum() / a.sum() ** 2 * 3.0 / 2.0)
    half = math.hypot(_core.t_quantile(Z90, 2.0) * sigma, Z90 * CONFIG.resistance_sigma * r)
    assert out["status"] == OK and out["n_steps"] == 3 and out["band"] == "statistical and systematic"
    assert out["resistance_ohm"] == pytest.approx(r, rel=1e-12)
    assert out["resistance_ohm_high"] - r == pytest.approx(half, rel=1e-10)
    one = resistance_of_steps(steps.iloc[:1], SPEC)
    assert one["status"] == OK and one["band"] == "systematic only: one step"
    assert one["resistance_ohm_high"] - 0.05 == pytest.approx(Z90 * CONFIG.resistance_sigma * 0.05, rel=1e-12)
    small = resistance_of_steps(step_rows(t, 1.0, 0.05), SPEC)
    assert small["n_steps"] == 0 and small["status"] == "not possible from this data: no qualifying current step"
    neg = resistance_of_steps(step_rows(t, 10.0, -0.05), SPEC)
    assert neg["status"] == "not possible from this data: the current steps give a resistance at or below zero"
    assert math.isnan(neg["resistance_ohm"])


# --- a short synthetic pack ----------------------------------------------------------------

# Three months at 25 °C; the resistance grows by 40 % of its start value per year, so the
# slope stands out of the scatter of a short record. A 60-day level window and no
# warm-up give full rests within the record.
GROW = SynthPack(r_growth_per_year=0.40, voltage_gain_error_max=0.0025)
SHORT = CapacityConfig(level_window_days=60.0, level_warmup_days=0.0)
RECORD = RecordConfig(growth_min_span_years=0.2)


@pytest.fixture(scope="module")
def pack_reduced():
    return reduce_parts(frames(GROW, 3, step_s=10, seed=4), GROW.spec(), SHORT)


@pytest.fixture(scope="module")
def pack_true():
    return reduce_parts(frames(GROW, 3, step_s=10, seed=4, true_signals=True), GROW.spec(), SHORT)


def test_synthetic_pack(pack_reduced, pack_true):
    spec = replace(GROW.spec(), voltage_min_v=GROW.cells_in_series * GROW.v_min_cell)
    end = pd.Timestamp(pack_reduced.daily["day"].max()) + pd.Timedelta(days=1)
    est = estimate_resistance(None, spec, reduced=pack_reduced, at=[end], config=RECORD, capacity_config=SHORT)
    row = est[est["headline"]].iloc[0]
    assert list(est["temp_band_c"]) == [25.0]
    assert row["status"] == OK and est.attrs["sample_s"] == 10.0
    events = est.attrs["events"]
    lam = np.exp(-(days([end])[0] - events["t_days"].to_numpy()) / RECORD.tau_days)
    age = (events["t_days"].to_numpy() - 1.0 - days([pd.Timestamp(GROW.start)])[0])
    ohmic = GROW.r_ohm * np.sum(lam * events["a"] * GROW.resistance_scale(age)) / np.sum(lam * events["a"])
    # The 1-sample response is the ohmic step plus the drift of the open-circuit and RC
    # voltages over the sample before the step: when a load ends, that drift opposes the
    # step, so on this pack the response lies about 2 % below the ohmic resistance.
    own = estimate_resistance(None, spec, reduced=pack_true, at=[end], config=RECORD, capacity_config=SHORT)
    truth = float(own["resistance_ohm"].iloc[0])
    assert -0.04 < truth / ohmic - 1.0 < 0.0
    # The measured signals differ from the pack's own by the sensors' gain errors (at most
    # 0.5 % and 0.25 % on this pack) and noise.
    assert abs(row["resistance_ohm"] / truth - 1.0) < 0.0075 + 0.001
    out = resistance_growth(None, spec, reduced=pack_reduced, config=RECORD, capacity_config=SHORT)
    assert out["status"] == OK
    start_age = (out["first_day"] - pd.Timestamp(GROW.start)).days / 365.25
    assert out["growth_percent_per_year"] == pytest.approx(40.0 / (1.0 + 0.40 * start_age), rel=0.15)
    assert 0.85 < out["power_capability_share"] < 1.0
    assert out["power_capability_w_status"] == OK
    # V_min (OCV - V_min) / R at full charge
    assert 1.0e4 < out["power_capability_w"] < 1.0e5
    assert out["power_capability_w_low"] < out["power_capability_w"] < out["power_capability_w_high"]


# --- the voltage-refresh rule ---------------------------------------------------------------


def refresh_steps(n_days, zeros_per_day, start="2021-01-01", per_day=4, temp=25.0):
    """``per_day`` steps a day at 50 mOhm; the first ``zeros_per_day(d)`` of them show no voltage change."""
    parts = []
    for d in range(n_days):
        t0 = pd.Timestamp(start) + pd.Timedelta(days=d, hours=12)
        times = [t0 + pd.Timedelta(minutes=k) for k in range(per_day)]
        r = np.where(np.arange(per_day) < zeros_per_day(d), 0.0, 0.05)
        parts.append(step_rows(times, 10.0, r, temp=temp))
    return pd.concat(parts, ignore_index=True)


def test_band_days_without_voltage_refresh_are_left_out():
    # 100 steps a day: 1 without voltage change is within the 1 % limit, 2 are not.
    steps = refresh_steps(3, lambda d: (1, 2, 0)[d], per_day=100)
    events, counts = day_band_events(steps, SPEC)
    assert list(events["day"]) == [pd.Timestamp("2021-01-01"), pd.Timestamp("2021-01-03")]
    assert list(events["n_unrefreshed"]) == [1, 0]
    assert counts["n_days_unrefreshed"] == 1 and counts["n_steps_unrefreshed_days"] == 100
    assert list(counts["dropped"]["day"]) == [pd.Timestamp("2021-01-02")]
    assert counts["unrefreshed_share_by_band"] == {25.0: pytest.approx(3.0 / 300.0)}
    loose = day_band_events(steps, SPEC, CONFIG.with_(unrefreshed_max_share=0.02))[0]
    assert len(loose) == 3


def test_refresh_status_of_the_estimate():
    at = [pd.Timestamp("2021-02-15")]
    # 30 days, the first 25 with one of four steps unrefreshed: 5 days kept, fewer than 10,
    # and the 25 left out would have made enough.
    steps = refresh_steps(30, lambda d: 1 if d < 25 else 0)
    est = estimate_resistance(None, SPEC, reduced=reduced_of(steps), at=at)
    row = est.iloc[0]
    t_end = days(pd.date_range("2021-01-02", periods=30, freq="D"))
    lam = np.exp(-(days(at)[0] - t_end) / CONFIG.tau_days)
    share = np.sum(lam[:25] * 1.0) / np.sum(lam * 4.0)
    assert row["status"] == f"not possible from this data: the voltage is not refreshed with the current ({100 * share:.1f} % of steps show no voltage change)"
    assert row["n_days"] == pytest.approx(np.sum(lam[25:]), rel=1e-12)
    assert est.attrs["n_days_unrefreshed"] == 25 and len(est.attrs["dropped"]) == 25
    # 7 days, 2 left out: too few days even with them, so the reason is the step count.
    few = estimate_resistance(None, SPEC, reduced=reduced_of(refresh_steps(7, lambda d: 1 if d < 2 else 0)), at=at)
    assert few["status"].iloc[0] == "not possible from this data: too few current steps near 25 °C"
    # Every day of a band left out: the band is still reported, with the refresh status.
    cold = refresh_steps(30, lambda d: 4, temp=10.0)
    both = estimate_resistance(None, SPEC, reduced=reduced_of(pd.concat([steps.iloc[100:], cold], ignore_index=True)), at=at)
    assert list(both["temp_band_c"]) == [10.0, 25.0]
    assert both["status"].iloc[0] == "not possible from this data: the voltage is not refreshed with the current (100.0 % of steps show no voltage change)"


def test_refresh_status_of_the_growth_fit():
    steps = refresh_steps(400, lambda d: 1 if d >= 5 else 0, per_day=4)
    out = resistance_growth(None, SPEC, reduced=reduced_of(steps))
    assert out["n_days_unrefreshed"] == 395
    share = 395.0 / 1600.0  # no forgetting: over the record up to the date
    assert out["status"] == f"not possible from this data: the voltage is not refreshed with the current ({100 * share:.1f} % of steps show no voltage change)"


def test_refresh_rule_on_a_short_window():
    t = pd.date_range("2021-01-01 12:00", periods=3, freq="s")
    one = resistance_of_steps(step_rows(t[:1], 10.0, 0.0), SPEC)
    assert one["status"] == "not possible from this data: the voltage is not refreshed with the current (100.0 % of steps show no voltage change)"
    assert one["n_steps"] == 1 and one["n_unrefreshed"] == 1 and math.isnan(one["resistance_ohm"])
    three = resistance_of_steps(step_rows(t, [10.0, -20.0, 30.0], [0.05, 0.0, 0.04]), SPEC)
    assert three["status"].endswith("(33.3 % of steps show no voltage change)")
    fine = resistance_of_steps(step_rows(t[:1], 10.0, 0.05), SPEC)
    assert fine["status"] == OK and fine["n_steps"] == 1 and fine["band"] == "systematic only: one step"


# --- the onset reference of the validation script -------------------------------------------


def test_onset_value_of_a_capacity_test_window():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))
    from onset_resistance import onset_value

    index = pd.date_range("2021-05-01 12:00:00", periods=200, freq="s")
    onset = index[100]
    current = np.where(index >= onset, -20.0, 0.0)
    noise = 0.001 * np.sin(np.arange(200))  # no exact zeros of the voltage change
    window = pd.DataFrame(
        {"voltage_v": 50.0 + 0.05 * current + noise, "current_a": current, "power_w": 0.0,
         "temp_pack_c": 21.0, "interpolated": False},
        index=index,
    )
    window["power_w"] = window["voltage_v"] * window["current_a"]
    ref, temp = onset_value(window, onset, SPEC, CONFIG)
    assert temp == 21.0
    assert ref["status"] == OK and ref["n_steps"] == 1 and ref["band"] == "systematic only: one step"
    assert ref["resistance_ohm"] == pytest.approx(0.05 + (noise[100] - noise[99]) / -20.0, rel=1e-9)
    # The voltage sample at the step was not refreshed: no reference.
    stale = window.copy()
    stale.iloc[100, stale.columns.get_loc("voltage_v")] = stale["voltage_v"].iloc[99]
    ref, _ = onset_value(stale, onset, SPEC, CONFIG)
    assert ref["status"].startswith("not possible from this data: the voltage is not refreshed with the current")
