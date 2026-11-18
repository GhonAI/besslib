"""Tests of the usage counts: hand-made reduced tables with known sums, and a synthetic pack with planted events."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib.frames import PackSpec
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.reduce import DAILY_COLUMNS, Reduced, reduce_parts
from besslib.soh.rests import REST_COLUMNS
from besslib.soh.usage import Limits, count_usage, usage_by_year
from synth import SynthPack, frames, true_segments

SPEC = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=3.6, cells_in_series=10, cells_in_parallel=1, reference_capacity_ah=50.0)
Z90 = 1.6448536269514722


def hand_reduced() -> Reduced:
    """Three days over New Year (30, 31 December 2019 and 1 January 2020), with a missing hour on the 31st."""
    days = pd.to_datetime(["2019-12-30", "2019-12-31", "2020-01-01"])
    daily = pd.DataFrame(0.0, index=range(3), columns=DAILY_COLUMNS)
    daily["day"] = days
    daily["t_first"] = pd.to_datetime(["2019-12-30 12:00", "2019-12-31 00:00", "2020-01-01 00:00"])
    daily["t_last"] = pd.to_datetime(["2019-12-30 23:59:59", "2019-12-31 22:59:59", "2020-01-01 06:00:00"])
    daily["sample_s"] = 10.0
    daily["measured_s"] = [12 * 3600.0 - 1, 23 * 3600.0 - 1, 6 * 3600.0]
    daily["interpolated_s"] = [0.0, 3600.0, 0.0]
    daily["ah_in"] = [10.0, 20.0, 5.0]
    daily["ah_out"] = [8.0, 15.0, 2.0]
    daily["wh_in"] = [1000.0, 2000.0, 500.0]
    daily["wh_out"] = [800.0, 1500.0, 200.0]
    daily["voltage_min_v"] = [31.0, np.nan, 30.0]
    daily["voltage_max_v"] = [41.0, 42.0, np.nan]
    # charging 3 h at 10-14 degrees (bin 30-34), discharging 2 h at 50 degrees and 1 h at 5, the rest of the
    # measured time idle at 20 degrees or without a reading
    daily.loc[0, "s_charge_30"] = 3 * 3600.0
    daily.loc[0, "s_discharge_70"] = 2 * 3600.0
    daily.loc[1, "s_discharge_25"] = 3600.0
    daily.loc[0, "s_idle_40"] = daily.loc[0, "measured_s"] - 5 * 3600.0
    daily.loc[1, "s_idle_nan"] = daily.loc[1, "measured_s"] - 3600.0
    daily.loc[2, "s_idle_40"] = daily.loc[2, "measured_s"]
    rests = pd.DataFrame(columns=REST_COLUMNS)
    return Reduced(daily=daily, steps=pd.DataFrame(), rests=rests, sample_s=10.0)


def test_hand_made_totals():
    u = count_usage(None, SPEC, reduced=hand_reduced())
    assert u.first_day == pd.Timestamp("2019-12-30") and u.last_day == pd.Timestamp("2020-01-01")
    measured = 12 * 3600.0 - 1 + 23 * 3600.0 - 1 + 6 * 3600.0
    assert u.hours_measured == pytest.approx(measured / 3600.0)
    elapsed = (pd.Timestamp("2020-01-01 06:00") - pd.Timestamp("2019-12-30 12:00")).total_seconds()
    assert u.hours_missing == pytest.approx((elapsed - measured) / 3600.0)
    assert u.share_interpolated == pytest.approx(3600.0 / elapsed)
    assert u.energy_in_kwh == pytest.approx(3.5) and u.energy_out_kwh == pytest.approx(2.5)
    assert u.energy_throughput_kwh == pytest.approx(6.0)
    assert u.charge_in_ah == pytest.approx(35.0) and u.charge_out_ah == pytest.approx(25.0)
    assert u.capacity_throughput_ah == pytest.approx(60.0)
    assert u.full_equivalent_cycles == pytest.approx(60.0 / 100.0)  # 60 Ah / (2 x 50 Ah)
    assert u.voltage_min_v == 30.0 and u.voltage_max_v == 42.0
    assert u.voltage_min_cell_v == pytest.approx(3.0) and u.voltage_max_cell_v == pytest.approx(4.2)
    assert u.n_empty_rests == 0
    assert "since the first day of data" in u.status


def test_bands_are_the_sensor_term_only():
    u = count_usage(None, SPEC, reduced=hand_reduced())
    cfg = RecordConfig()
    assert u.capacity_throughput_ah_high - u.capacity_throughput_ah == pytest.approx(
        Z90 * cfg.charge_sigma * 60.0, rel=1e-6
    )
    assert u.capacity_throughput_ah - u.capacity_throughput_ah_low == pytest.approx(
        Z90 * cfg.charge_sigma * 60.0, rel=1e-6
    )
    assert u.energy_throughput_kwh_high - u.energy_throughput_kwh == pytest.approx(Z90 * cfg.energy_sigma * 6.0, rel=1e-6)
    assert u.full_equivalent_cycles_high == pytest.approx(u.capacity_throughput_ah_high / 100.0)
    wide = count_usage(None, SPEC, reduced=hand_reduced(), level=0.99)
    assert wide.capacity_throughput_ah_high > u.capacity_throughput_ah_high


def test_temperature_hours_and_limits():
    red = hand_reduced()
    u = count_usage(None, SPEC, Limits(temp_low_c=5.0, temp_high_c=50.0, charge_temp_high_c=10.0,
                                       charge_temp_low_c=10.0, voltage_min_v=30.0), reduced=red)
    t = u.temp_hours
    assert len(t) == 18 and t["temp_band_c"].iloc[0] == -20 and t["temp_band_c"].iloc[-1] == 65
    assert t["hours_charge"].sum() == pytest.approx(3.0) and t["hours_discharge"].sum() == pytest.approx(3.0)
    band = t.set_index("temp_band_c")
    assert band.loc[10, "hours_charge"] == pytest.approx(3.0)  # bin 30 is [10, 11)
    assert band.loc[5, "hours_discharge"] == pytest.approx(1.0)  # bin 25 is [5, 6)
    assert u.hours_no_temperature == pytest.approx((23 * 3600.0 - 1 - 3600.0) / 3600.0)
    # a low limit of 5: bins whose upper edge is at or below 5 (none holds hours); a high limit of 50: bins [50, ..)
    assert u.hours_below_temp_low == 0.0
    assert u.hours_above_temp_high == pytest.approx(2.0)  # the 2 h discharging at bin 70 = [50, 51)
    assert u.hours_charging_above_temp_high == pytest.approx(3.0)  # limit 10: the charging bin [10, 11) counts
    # a low charging limit at 11 counts the bin [10, 11) (its upper edge is at or below 11)
    u2 = count_usage(None, SPEC, Limits(charge_temp_high_c=10.0, charge_temp_low_c=11.0), reduced=red)
    assert u2.hours_charging_above_temp_high == pytest.approx(3.0)
    assert u2.hours_charging_below_temp_low == pytest.approx(3.0)  # [10, 11) ends at 11
    assert u2.hours_below_temp_low is None and u2.hours_above_temp_high is None
    assert u2.n_deep_discharge is None and u2.limits_status.startswith("not possible from this data")


def test_no_limits_gives_none_and_a_reason():
    u = count_usage(None, SPEC, reduced=hand_reduced())
    for name in ("hours_below_temp_low", "hours_above_temp_high", "hours_charging_below_temp_low",
                 "hours_charging_above_temp_high", "n_deep_discharge"):
        assert getattr(u, name) is None
    assert u.limits_status == "not possible from this data: no limits given"
    d = u.to_dict()
    assert d["first_day"] == "2019-12-30T00:00:00" and isinstance(d["temp_hours"], list)


def test_usage_by_year_splits_at_new_year():
    y = usage_by_year(hand_reduced(), SPEC)
    assert list(y["year"]) == [2019, 2020]
    assert y.loc[0, "energy_in_kwh"] == pytest.approx(3.0) and y.loc[1, "energy_in_kwh"] == pytest.approx(0.5)
    assert y.loc[0, "charge_in_ah"] == pytest.approx(30.0) and y.loc[1, "charge_out_ah"] == pytest.approx(2.0)
    assert y.loc[0, "full_equivalent_cycles"] == pytest.approx(53.0 / 100.0)
    assert y["hours_measured"].sum() == pytest.approx(count_usage(None, SPEC, reduced=hand_reduced()).hours_measured)
    assert y["hours_missing"].sum() == pytest.approx(count_usage(None, SPEC, reduced=hand_reduced()).hours_missing)
    assert y.loc[1, "hours_missing"] == pytest.approx(0.0, abs=1e-3)  # 2020: 00:00 to 06:00 fully measured


def test_empty_record_is_not_possible():
    empty = Reduced(daily=pd.DataFrame(columns=DAILY_COLUMNS), steps=pd.DataFrame(),
                    rests=pd.DataFrame(columns=REST_COLUMNS), sample_s=float("nan"))
    u = count_usage(None, SPEC, reduced=empty)
    assert u.status.startswith("not possible from this data") and np.isnan(u.energy_in_kwh)
    assert len(usage_by_year(empty, SPEC)) == 0
    with pytest.raises(ValueError):
        count_usage(None, SPEC)


def test_frame_is_reduced_when_no_table_is_given():
    pack = SynthPack()
    part = next(frames(pack, 1, step_s=10, seed=2))
    a = count_usage(part, pack.spec())
    b = count_usage(None, pack.spec(), reduced=reduce_parts([part], pack.spec(), CapacityConfig()))
    assert a.capacity_throughput_ah == b.capacity_throughput_ah and a.energy_in_kwh == b.energy_in_kwh


def test_synthetic_pack_with_planted_events():
    pack = SynthPack(
        temp_mean_c=20.0,
        planted_temps=((10, 10.0, 4.0, 47.0), (30, 1.0, 3.0, -3.0)),
        planted_deep_days=(15, 40, 61),
    )
    spec = pack.spec()
    red = reduce_parts(frames(pack, 3, step_s=10, seed=3), spec, CapacityConfig(min_duration_min=10.0))
    segs = true_segments(pack, 3, 3)
    truth_in = float((np.maximum(segs["current_a"], 0.0) * segs["duration_s"]).sum()) / 3600.0
    truth_out = float((np.maximum(-segs["current_a"], 0.0) * segs["duration_s"]).sum()) / 3600.0
    limits = Limits(temp_low_c=0.0, temp_high_c=45.0, voltage_min_v=3.2 * spec.cells_in_series)
    u = count_usage(None, spec, limits, reduced=red)
    assert u.charge_in_ah == pytest.approx(truth_in, rel=0.007)
    assert u.charge_out_ah == pytest.approx(truth_out, rel=0.007)
    assert u.hours_above_temp_high == pytest.approx(4.0, abs=0.1)  # all measured: the pack runs through the interval
    assert u.hours_below_temp_low == pytest.approx(3.0, abs=0.1)
    assert u.n_deep_discharge == 3
    assert u.hours_missing == pytest.approx(0.0, abs=0.01)
    assert (u.temp_hours[["hours_charge", "hours_discharge", "hours_idle"]].sum().sum() + u.hours_no_temperature
            == pytest.approx(u.hours_measured))
