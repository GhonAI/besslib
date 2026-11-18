"""Tests of the reduced tables: daily sums, step rows and the day sums at each rest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib.frames import PackSpec
from besslib.soh.config import CapacityConfig
from besslib.soh.reduce import (
    DAILY_COLUMNS,
    MODES,
    STEP_COLUMNS,
    TEMP_BINS,
    TEMP_BIN_LOW_C,
    read_reduced,
    reduce_frame,
    reduce_parts,
    temp_hours,
    write_reduced,
)
from besslib.soh.rests import REST_COLUMNS, detect_rests_in_parts
from synth import SynthPack, frames

PACK = SynthPack(temp_mean_c=15.0, temp_season_amp_c=8.0, temp_daily_amp_c=3.0)
SPEC = PACK.spec()
CONFIG = CapacityConfig(min_duration_min=10.0)
BIN_COLUMNS = [c for c in DAILY_COLUMNS if c.startswith("s_")]


@pytest.fixture(scope="module")
def months():
    return list(frames(PACK, 3, step_s=10, seed=4))


@pytest.fixture(scope="module")
def monthly(months):
    return reduce_parts(iter(months), SPEC, CONFIG)


def close(a, b, rel=1e-9):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    np.testing.assert_allclose(a, b, rtol=rel, atol=rel * max(1.0, float(np.max(np.abs(b), initial=0.0))))


def check_identities(red):
    """The running counters of the rest detector equal the daily sums before the rest's day plus its own day's sum."""
    daily, rests = red.daily, red.rests
    assert len(rests) > 20
    first = daily["t_first"].iloc[0]
    days = daily["day"].to_numpy()
    cum = {
        c: np.concatenate([[0.0], np.cumsum(daily[c].to_numpy())])
        for c in ("ah_in", "ah_out", "wh_in", "wh_out", "measured_s")
    }
    before = np.searchsorted(days, rests["start"].dt.normalize().to_numpy())  # the days before the rest's day
    ai, ao = cum["ah_in"][before] + rests["ah_in_today"], cum["ah_out"][before] + rests["ah_out_today"]
    close(ai - ao, rests["charge_ah"])
    close(ai + ao, rests["throughput_ah"])
    close(cum["wh_in"][before] + rests["wh_in_today"], rests["energy_in_wh"])
    close(cum["wh_out"][before] + rests["wh_out_today"], rests["energy_out_wh"])
    elapsed = (rests["start"] - first).dt.total_seconds()
    close(elapsed - (cum["measured_s"][before] + rests["measured_s_today"]), rests["missing_s"])


def test_identities_at_every_rest(monthly):
    check_identities(monthly)


def test_temperature_seconds_add_up_to_measured_seconds(monthly):
    d = monthly.daily
    close(d[BIN_COLUMNS].sum(axis=1), d["measured_s"])
    assert len(d) == 91  # 1 January to 31 March 2020, one row per day
    assert (d["day"].diff().dropna() == pd.Timedelta(days=1)).all()


def test_month_by_month_equals_one_frame(months, monthly):
    whole = reduce_frame(pd.concat(months), SPEC, CONFIG)
    assert list(whole.daily.columns) == DAILY_COLUMNS
    for c in whole.daily.columns:
        if c in ("day", "t_first", "t_last"):
            assert whole.daily[c].equals(monthly.daily[c])
        else:
            close(monthly.daily[c].fillna(-1), whole.daily[c].fillna(-1))
    pd.testing.assert_frame_equal(monthly.steps, whole.steps, check_exact=True)
    pd.testing.assert_frame_equal(monthly.rests[REST_COLUMNS], whole.rests[REST_COLUMNS], check_exact=False, rtol=1e-9)
    close(monthly.rests.filter(like="_today"), whole.rests.filter(like="_today"))


def test_uneven_parts_cut_inside_a_day_give_the_same_tables(months, monthly):
    whole = pd.concat(months)
    cuts = [pd.Timestamp("2020-01-10 07:13:00"), pd.Timestamp("2020-01-10 19:00:00"), pd.Timestamp("2020-02-20 00:00:10")]
    edges = [whole.index[0], *cuts, whole.index[-1] + pd.Timedelta(seconds=1)]
    parts = [whole.loc[a : b - pd.Timedelta(seconds=1)] for a, b in zip(edges[:-1], edges[1:])]
    red = reduce_parts(parts, SPEC, CONFIG)
    check_identities(red)
    for c in red.daily.columns:
        if c not in ("day", "t_first", "t_last"):
            close(red.daily[c].fillna(-1), monthly.daily[c].fillna(-1))
    pd.testing.assert_frame_equal(red.steps, monthly.steps, check_exact=True)
    close(red.rests.filter(like="_today"), monthly.rests.filter(like="_today"))


def test_rests_keep_the_phase_two_columns(months, monthly):
    plain = detect_rests_in_parts(iter(months), SPEC, CONFIG)
    pd.testing.assert_frame_equal(monthly.rests[REST_COLUMNS], plain[REST_COLUMNS], check_exact=True)


def test_write_and_read_round_trip(tmp_path, monthly):
    write_reduced(monthly, tmp_path, "X")
    back = read_reduced(tmp_path, "X")
    pd.testing.assert_frame_equal(back.daily, monthly.daily, check_exact=True)
    pd.testing.assert_frame_equal(back.steps, monthly.steps, check_exact=True)
    pd.testing.assert_frame_equal(back.rests, monthly.rests, check_exact=True)
    assert back.sample_s == monthly.sample_s == 10.0


def test_temp_hours_long_form(monthly):
    hours = temp_hours(monthly.daily)
    assert set(hours["mode"]) == set(MODES)
    assert hours.groupby("mode").size().eq(TEMP_BINS + 1).all()
    assert hours["temp_c"].isna().sum() == len(MODES)
    close(hours["hours"].sum() * 3600.0, monthly.daily["measured_s"].sum())
    assert hours["temp_c"].min() == TEMP_BIN_LOW_C


# --- tiny hand-made frames ---------------------------------------------------------------------------------------

SPEC1 = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.0, cells_in_series=14)  # idle below 0.5 A, step at 5 A
NAN = np.nan
#: seconds from 2020-01-31 23:59:20, current, voltage, power, pack temperature, interpolated
TINY = [
    (0, 0.0, 50.0, 0.0, 25.0, False),
    (10, 0.0, 50.0, 0.0, 25.0, False),
    (20, 10.0, 51.0, 510.0, 25.0, False),  # a step of 10 A after steady 0 A: qualifies
    (30, 10.0, 51.0, 510.0, 25.0, False),
    (40, -10.0, 49.0, -490.0, 25.0, False),  # 00:00:00; sign change, 20 A: qualifies, first step of Feb 1
    (50, NAN, 49.0, -490.0, 25.0, False),  # missing current
    (60, -10.0, 49.0, NAN, NAN, False),  # missing power and temperature
    (70, -10.0, 49.0, -490.0, 26.5, False),
    (80, -2.0, 50.0, -100.0, 26.5, False),  # 8 A: qualifies (its look-back sample is finite)
    (90, -2.0, 50.0, -100.0, 26.5, True),  # interpolated end sample
    (100, 6.0, 50.0, 300.0, 26.5, False),  # 8 A but the sample before is interpolated: fails
    (110, 6.0, 51.0, 306.0, 27.0, False),
    (120, 6.0, 51.0, 306.0, 27.0, False),
    (320, 20.0, 52.0, 1040.0, 27.0, False),  # 200 s gap: not bridged, 14 A but not contiguous: fails
    (330, 20.0, 52.0, 1040.0, 27.0, False),
    (340, 4.0, 52.0, 208.0, 27.0, False),  # 16 A after steady 20 A: qualifies
    (350, 20.0, 52.0, 1040.0, 27.0, False),  # 16 A but the current before the step was not steady: fails
    (360, 20.0, 52.0, 1040.0, 27.0, False),
]


def tiny_frame(rows=TINY):
    t0 = pd.Timestamp("2020-01-31 23:59:20")
    index = pd.DatetimeIndex([t0 + pd.Timedelta(seconds=r[0]) for r in rows], name="time").as_unit("s")
    return pd.DataFrame(
        {
            "current_a": [r[1] for r in rows],
            "voltage_v": [r[2] for r in rows],
            "power_w": [r[3] for r in rows],
            "temp_pack_c": [r[4] for r in rows],
            "interpolated": [r[5] for r in rows],
        },
        index=index,
    )


def reference(frame):
    """Plain loops over the steps: the expected daily sums."""
    out = {}
    t = (frame.index - pd.Timestamp("1970-01-01")).total_seconds().to_numpy()
    i, v, p, temp = (frame[c].to_numpy(float) for c in ("current_a", "voltage_v", "power_w", "temp_pack_c"))
    interp = frame["interpolated"].to_numpy()
    for j in range(1, len(t)):
        dt = t[j] - t[j - 1]
        day = str(frame.index[j].date())
        row = out.setdefault(day, dict(measured=0.0, interp=0.0, ai=0.0, ao=0.0, wi=0.0, wo=0.0, bins={}))
        valid = np.isfinite(i[j]) and np.isfinite(i[j - 1])
        meas = (dt if dt <= 15.0 else 10.0) if valid else 0.0
        row["measured"] += meas
        row["interp"] += min(dt, 60.0) if interp[j] else 0.0
        if valid and dt <= 60.0:
            row["ai"] += 0.5 * (max(i[j], 0) + max(i[j - 1], 0)) * dt / 3600
            row["ao"] += 0.5 * (max(-i[j], 0) + max(-i[j - 1], 0)) * dt / 3600
        if np.isfinite(p[j]) and np.isfinite(p[j - 1]) and dt <= 60.0:
            row["wi"] += 0.5 * (max(p[j], 0) + max(p[j - 1], 0)) * dt / 3600
            row["wo"] += 0.5 * (max(-p[j], 0) + max(-p[j - 1], 0)) * dt / 3600
        mode = "charge" if i[j] >= 0.5 else "discharge" if i[j] <= -0.5 else "idle"
        key = (mode, "nan" if not np.isfinite(temp[j]) else f"{int(np.floor(temp[j])) - TEMP_BIN_LOW_C:02d}")
        row["bins"][key] = row["bins"].get(key, 0.0) + meas
    return out


def check_tiny(red, frame):
    expected = reference(frame)
    d = red.daily.set_index(red.daily["day"].dt.strftime("%Y-%m-%d"))
    assert list(d.index) == ["2020-01-31", "2020-02-01"]
    for day, row in expected.items():
        got = d.loc[day]
        for name, key in (("measured_s", "measured"), ("interpolated_s", "interp"), ("ah_in", "ai"), ("ah_out", "ao"),
                          ("wh_in", "wi"), ("wh_out", "wo")):
            assert got[name] == pytest.approx(row[key], abs=1e-12), (day, name)
        for mode in MODES:
            for k in [*(f"{b:02d}" for b in range(TEMP_BINS)), "nan"]:
                assert got[f"s_{mode}_{k}"] == pytest.approx(row["bins"].get((mode, k), 0.0), abs=1e-12)


@pytest.mark.parametrize("cut", [None, 4, 8])
def test_tiny_frame_daily_sums(cut):
    frame = tiny_frame()
    parts = [frame] if cut is None else [frame.iloc[:cut], frame.iloc[cut:]]
    red = reduce_parts(parts, SPEC1, CONFIG)
    check_tiny(red, frame)
    # hand-computed: three steps end on 31 January, 14 on 1 February
    d = red.daily.set_index(red.daily["day"].dt.strftime("%Y-%m-%d"))
    jan, feb = d.loc["2020-01-31"], d.loc["2020-02-01"]
    assert jan["measured_s"] == 30.0 and feb["measured_s"] == 10 * 11 + 10  # 10 s steps; the 200 s gap counts 10 s; 2 invalid steps
    assert jan["ah_in"] == pytest.approx((0.0 + 50.0 + 100.0) / 3600.0)
    assert jan["ah_out"] == 0.0
    assert jan["wh_in"] == pytest.approx((2550.0 + 5100.0) / 3600.0)
    assert jan["s_idle_45"] == 10.0 and jan["s_charge_45"] == 20.0  # 25.0 degrees = bin 45
    assert feb["interpolated_s"] == 10.0
    assert feb["voltage_min_v"] == 49.0 and feb["voltage_max_v"] == 52.0 and jan["voltage_max_v"] == 51.0
    assert feb["s_discharge_nan"] == 0.0  # the sample without temperature is a step with a missing current end
    assert red.sample_s == 10.0


def test_tiny_frame_missing_seconds_equal_the_counters():
    red = reduce_frame(tiny_frame(), SPEC1, CONFIG)
    d = red.daily
    elapsed = (tiny_frame().index[-1] - tiny_frame().index[0]).total_seconds()
    assert elapsed - d["measured_s"].sum() == pytest.approx(10.0 * 2 + 190.0)  # two invalid steps of 10 s, a gap of 190 s extra


def test_tiny_frame_step_rows():
    frame = tiny_frame()
    red = reduce_frame(frame, SPEC1, CONFIG)
    assert list(red.steps.columns) == STEP_COLUMNS
    expected = [frame.index[k] for k in (2, 4, 8, 15)]
    assert list(red.steps["time"]) == expected
    assert (red.steps["steady_s"] == 10.0).all()
    row = red.steps.iloc[1]
    assert (row["current_before_a"], row["current_after_a"], row["voltage_before_v"], row["voltage_after_v"]) == (
        10.0, -10.0, 51.0, 49.0)
    assert red.steps.iloc[2]["temp_pack_c"] == 26.5
    # the step that crosses the cut between two pieces is found as well
    parts = reduce_parts([frame.iloc[:4], frame.iloc[4:]], SPEC1, CONFIG)
    pd.testing.assert_frame_equal(parts.steps, red.steps, check_exact=True)


def test_tiny_frame_each_row_condition_is_needed():
    base = tiny_frame()
    hit = lambda f, k: base.index[k] in set(reduce_frame(f, SPEC1, CONFIG).steps["time"])  # noqa: E731
    assert hit(base, 8)
    f = base.copy()
    f.iloc[8, f.columns.get_loc("voltage_v")] = NAN  # no voltage after the step
    assert not hit(f, 8)
    f = base.copy()
    f.iloc[7, f.columns.get_loc("voltage_v")] = NAN  # no voltage before it
    assert not hit(f, 8)
    f = base.copy()
    f.iloc[8, f.columns.get_loc("interpolated")] = True
    assert not hit(f, 8)
    f = base.copy()
    f.iloc[8, f.columns.get_loc("current_a")] = -4.0  # 6 A: still a step; -6.5 -> 3.5 A is below C/20
    assert hit(f, 8)
    f.iloc[8, f.columns.get_loc("current_a")] = -6.5
    assert not hit(f, 8)
    f = base.copy()
    f.iloc[6, f.columns.get_loc("current_a")] = -3.0  # the sample before the step is not steady: steady_s = 0
    assert not hit(f, 8)
    f = base.copy()
    f.iloc[6, f.columns.get_loc("current_a")] = -9.0  # within 20 % of the 8 A step (1.6 A): still steady
    assert hit(f, 8)


def test_days_without_samples_get_a_row():
    frame = tiny_frame()
    later = frame.copy()
    later.index = later.index + pd.Timedelta(days=3)
    red = reduce_parts([frame, later], SPEC1, CONFIG)
    d = red.daily
    assert len(d) == 5
    assert list(d["day"].dt.strftime("%m-%d")) == ["01-31", "02-01", "02-02", "02-03", "02-04"]
    assert list(d.index[d["measured_s"] == 0.0]) == [2]  # the day between the two pieces has no sample
    mid = d.iloc[2]
    assert np.isnan(mid["voltage_min_v"]) and pd.isna(mid["t_first"]) and pd.isna(mid["t_last"])
    assert mid[BIN_COLUMNS].sum() == 0.0
    assert (d.iloc[2][["ah_in", "ah_out", "wh_in", "wh_out", "interpolated_s"]] == 0.0).all()
    assert d["sample_s"].nunique() == 1


def test_empty_input():
    red = reduce_parts([], SPEC1, CONFIG)
    assert len(red.daily) == len(red.steps) == len(red.rests) == 0
    assert list(red.daily.columns) == DAILY_COLUMNS and np.isnan(red.sample_s)
