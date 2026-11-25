"""Duties (grid, overlays) and sensor gaps of besslib.sim."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from besslib import frames, sim
from besslib.sim.duty import DAY_S, check_pieces, make_duty, replace_window, Piece
from besslib.sim.sensors import SensorSettings, apply_gaps


def _days(duty, n, seed=0):
    rng = np.random.default_rng(seed)
    return [duty.day(rng, d) for d in range(n)]


def test_grid_day_is_gapless_on_the_grid_and_has_its_pieces():
    duty = make_duty({"kind": "grid", "calibration_days": 3, "nominal_energy_kwh": 5.0})
    days = _days(duty, 6)
    check_pieces([p for day in days for p in day], 10.0)
    assert [any(p.tag == "calibration" for p in day) for day in days] == [False, False, True, False, False, True]
    assert all(sum(p.tag == "cycle" for p in day) == 2 for day in days if day is not days[2] and day is not days[5])
    for p in days[0]:
        if p.until_soc is not None:
            assert p.until_soc in (0.70, 0.30, 0.75, 0.25)
            assert (p.value > 0) == (p.until_soc in (0.70, 0.75))


def test_grid_asks_for_power_in_shares_of_the_nominal_energy_and_holds_it():
    day = _days(make_duty({"kind": "grid", "calibration_days": 1, "nominal_energy_kwh": 5.0}), 1)[0]
    assert {p.mode for p in day} <= {"power", "rest"}
    cal = [p for p in day if p.tag == "calibration"]
    assert len(cal) == 1 and cal[0].value == pytest.approx(0.20 * 5000.0) and cal[0].until_soc is None
    assert all(p.hold_s == 300.0 for p in day if p.mode == "power")
    assert all(abs(p.value) <= 0.25 * 5000.0 + 1e-9 for p in day)
    with pytest.raises(ValueError, match="nominal_energy_kwh"):
        _days(make_duty({"kind": "grid"}), 1)


def test_grid_draws_one_uniform_per_slot_whatever_the_calibration():
    a, b = np.random.default_rng(3), np.random.default_rng(3)
    make_duty({"kind": "grid", "nominal_energy_kwh": 5.0}).day(a, 0)
    make_duty({"kind": "grid", "calibration_days": 1, "nominal_energy_kwh": 5.0}).day(b, 0)
    assert a.bit_generator.state == b.bit_generator.state
    c = np.random.default_rng(3)
    c.uniform(-1, 1, 24)
    assert a.bit_generator.state == c.bit_generator.state


def test_grid_without_cycle_or_calibration_is_slots_only():
    day = _days(make_duty({"kind": "grid", "cycle": False, "nominal_energy_kwh": 5.0}), 1)[0]
    assert len(day) == 24 and all(p.tag == "" for p in day)


def test_overlay_day_draws_the_ordinary_numbers():
    plain = make_duty({"kind": "home"})
    planted = make_duty({"kind": "home", "overlay": [{"kind": "capacity_test", "days": [1]}]})
    a, b = np.random.default_rng(4), np.random.default_rng(4)
    for d in range(4):
        plain.day(a, d)
        planted.day(b, d)
    assert a.bit_generator.state == b.bit_generator.state


def test_capacity_test_pieces():
    duty = make_duty({"kind": "home", "capacity_ah": 50.0,
                      "overlay": [{"kind": "capacity_test", "days": [2], "c_charge": 0.5, "c_discharge": 0.25}]})
    days = _days(duty, 4)
    check_pieces([p for day in days for p in day], 10.0)
    tagged = [p for p in days[2] if p.tag == "capacity-test"]
    assert [p.value for p in tagged] == [25.0, -12.5]
    assert tagged[0].until_soc is None and tagged[1].until_soc is None
    assert all(p.tag == "" for p in days[1] + days[3])
    assert tagged[0].end_s - tagged[0].start_s == pytest.approx(1.15 * 3600 / 0.5, abs=10)


def test_monthly_overlay_lands_on_one_day_a_month():
    duty = make_duty({"kind": "home", "overlay": [{"kind": "capacity_test", "day_of_month": 15}]}, start="2020-01-01")
    planted = [d for d in range(60) if any(p.tag == "capacity-test" for p in duty.day(np.random.default_rng(d), d))]
    assert planted == [14, 45]


def test_cp_discharge_is_a_power_piece_held_for_one_step():
    duty = make_duty({"kind": "home", "overlay": [{"kind": "cp_discharge", "days": [0], "power_w": 1500.0}]}, step_s=10.0)
    piece = [p for p in duty.day(np.random.default_rng(0), 0) if p.mode == "power"][0]
    assert piece.value == -1500.0 and piece.hold_s == 10.0 and piece.tag == "cp-discharge"


def test_overlay_that_does_not_fit_a_day_is_refused():
    duty = make_duty({"kind": "home", "overlay": [{"kind": "capacity_test", "days": [0], "c_charge": 0.1,
                                                   "c_discharge": 0.1}]})
    with pytest.raises(ValueError, match="does not fit"):
        duty.day(np.random.default_rng(0), 0)


def test_unknown_setting_and_kind_are_refused():
    with pytest.raises(ValueError, match="does not know"):
        make_duty({"kind": "grid", "soc_lo": 0.2})
    with pytest.raises(ValueError, match="unknown overlay"):
        make_duty({"overlay": [{"kind": "nothing"}]})
    with pytest.raises(ValueError, match="power_w"):
        make_duty({"overlay": [{"kind": "cp_discharge"}]})


def test_replace_window_keeps_the_rest_gapless():
    pieces = [Piece(0, 100), Piece(100, 200, "current", 1.0), Piece(200, 300)]
    out = replace_window(pieces, 50, 250, [Piece(50, 250, "current", -1.0)])
    check_pieces(out, 10.0)
    assert [(p.start_s, p.end_s) for p in out] == [(0, 50), (50, 250), (250, 300)]


def test_check_pieces_finds_a_gap_and_a_midnight_crossing():
    with pytest.raises(ValueError, match="does not start"):
        check_pieces([Piece(0, 100), Piece(110, 200)], 10.0)
    with pytest.raises(ValueError, match="midnight"):
        check_pieces([Piece(DAY_S - 100, DAY_S + 100)], 10.0)


def test_grid_run_keeps_until_stops_inside_the_window_and_tags_the_test():
    over = {"duty.kind": "grid", "duty.overlay": [{"kind": "capacity_test", "days": [3]}]}
    out = next(iter(sim.run("synth", 1, 1, {**over, "duty.capacity_ah": 100.0})))
    stops = out.truth.stops
    until = stops[(stops.kind == "until") & (stops.seconds < 3 * DAY_S)]  # the test empties the pack on day 3
    assert len(until) > 10
    assert until.bms_soc.between(0.22, 0.78).all()
    test = stops[stops.tag == "capacity-test"]
    assert list(test.kind) == ["full", "empty"]
    frames.check_frame(out.frame)


def _frame(n=600):
    index = pd.date_range("2020-01-01", periods=n, freq="10s", name="time")
    t = np.arange(n, dtype=float)
    return pd.DataFrame({"voltage_v": 50 + t * 0.01, "current_a": 5.0 + t * 0.0, "power_w": t, "temp_pack_c": 25.0,
                         "temp_ambient_c": 20.0, "interpolated": np.zeros(n, dtype=bool)}, index=index)


def test_gaps_off_by_default_changes_nothing():
    f = _frame()
    assert apply_gaps(f, SensorSettings(), np.random.default_rng(0), 10.0) is f


def test_short_gaps_are_interpolated_and_flagged_long_ones_leave_rows_out():
    f = _frame(3000)
    short = SensorSettings(gaps_per_month=8, gap_median_s=60, gap_sigma=0.1, interpolate_max_s=300)
    out = apply_gaps(f, short, np.random.default_rng(1), 10.0)
    assert len(out) == len(f) and out.interpolated.any()
    filled = out[out.interpolated]
    assert np.allclose(filled.voltage_v, f.loc[filled.index, "voltage_v"])  # a straight line stays itself
    frames.check_frame(out)
    long = SensorSettings(gaps_per_month=8, gap_median_s=900, gap_sigma=0.1, gap_max_s=3600, interpolate_max_s=300)
    out = apply_gaps(f, long, np.random.default_rng(1), 10.0)
    assert len(out) < len(f) and not out.interpolated.any()
    assert out.index.to_series().diff().max() > pd.Timedelta("300s")
    frames.check_frame(out)


def test_gaps_use_their_own_stream_so_the_true_signals_do_not_change():
    a = next(iter(sim.run("synth", 1, 2)))
    b = next(iter(sim.run("synth", 1, 2, {"sensors.gaps_per_month": 5.0})))
    pd.testing.assert_frame_equal(a.true_frame, b.true_frame)
    assert len(b.true_frame) >= len(b.frame)
