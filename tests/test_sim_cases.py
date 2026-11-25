"""Run settings of besslib.sim cases: events, cell scaling, self-discharge spread, the pack's drawn errors."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from besslib import sim
from besslib.sim.run import cell_model

DEMO = {"pack.groups": 4, "pack.series": 1}


def _event_case(*events, **extra):
    data = sim.load_case("demo", {**DEMO, **extra})
    data["event"] = list(events)
    return data


def test_scale_to_ah_is_a_parallel_pack():
    base = cell_model(sim.load_case("synth"))
    big = cell_model(sim.load_case("synth", {"cell.scale_to_ah": 280.0}))
    k = 2.8
    assert big.capacity_ah == 280.0
    assert big.r0_ohm == pytest.approx(base.r0_ohm / k) and big.r1_ohm == pytest.approx(base.r1_ohm / k)
    assert big.tau1_s == base.tau1_s and big.ocv_v == base.ocv_v


def test_scale_to_ah_divides_the_tables_and_keeps_time_constants():
    data = {"cell": {"ocv_soc": [0, 1], "ocv_v": [3.0, 4.0], "capacity_ah": 2.0, "r0_ohm": 0.04, "r1_ohm": 0.02,
                     "tau1_s": 30.0, "source": "x", "notes": "y", "credit": "z"}}
    data["cell"]["scale_to_ah"] = 10.0
    cell = cell_model(data)
    assert cell.capacity_ah == 10.0 and cell.r0_ohm == pytest.approx(0.008)
    # a nested list in a preset becomes nested tuples (the tables of the cell, when it has them)
    import importlib

    run_module = importlib.import_module("besslib.sim.run")

    assert run_module._nested([[1, 2], [3, [4]]]) == ((1, 2), (3, (4,)))
    assert run_module._scaled([[2.0, 4.0], [6.0]], 2.0) == ((1.0, 2.0), (3.0,))


def test_nothing_new_set_changes_nothing():
    a = next(iter(sim.run("synth", 1, 3)))
    assert a.events == [] and a.current_gain != 0.0 and a.voltage_gain == 0.0
    b = next(iter(sim.run("synth", 1, 3, measured=False)))
    assert b.current_gain == 0.0 and b.voltage_gain == 0.0
    pack, _ = sim.build(sim.load_case("synth"), 3)
    assert pack.self_discharge_a == () and pack.leak_ohm == () and pack.fan_points == ()


def test_self_discharge_spread_is_clipped_and_drawn_after_the_other_spreads():
    over = {**DEMO, "pack.self_discharge_mean_a": 0.002, "pack.self_discharge_sd_a": 0.005}
    pack, _ = sim.build(sim.load_case("demo", over), 1)
    assert len(pack.self_discharge_a) == 4 and min(pack.self_discharge_a) >= 0.0
    assert 0.0 in pack.self_discharge_a or len(set(pack.self_discharge_a)) == 4
    plain, _ = sim.build(sim.load_case("demo", DEMO), 1)
    assert pack.capacity_factor == plain.capacity_factor  # the capacity draws come first and are untouched
    flat, _ = sim.build(sim.load_case("demo", {**DEMO, "pack.self_discharge_mean_a": 0.002}), 1)
    assert flat.self_discharge_a == (0.002,) * 4  # no sd: no draw


def test_fan_failure_draws_one_day_in_its_months():
    case = _event_case({"kind": "fan_failure", "from_month": 2, "to_month": 3, "factor": 0.25})
    days = {sim.events(case, s)[0]["day"] for s in range(30)}
    assert min(days) >= 31 and max(days) < 31 + 29 + 31 and len(days) > 10
    pack, _ = sim.build(case, 4)
    (t, f), = pack.fan_points
    assert f == 0.25 and t == sim.events(case, 4)[0]["day"] * 86400.0


def test_leak_event_sets_one_group_and_the_control_draws_the_same():
    on = _event_case({"kind": "leak", "from_month": 2, "to_month": 4, "leak_a": 0.015})
    off = _event_case({"kind": "leak", "from_month": 2, "to_month": 4, "leak_a": 0.0})
    a, b = sim.events(on, 7)[0], sim.events(off, 7)[0]
    assert (a["day"], a["group"]) == (b["day"], b["group"]) and b["leak_ohm"] is None
    pack, _ = sim.build(on, 7)
    assert pack.leak_ohm[a["group"]] == pytest.approx(3.7 * 1 / 0.015)
    assert sum(math.isfinite(x) for x in pack.leak_ohm) == 1
    assert pack.leak_from_s[a["group"]] == a["day"] * 86400.0
    assert sim.build(off, 7)[0].leak_ohm == ()


def test_events_use_their_own_stream_in_file_order():
    e1 = {"kind": "fan_failure", "from_month": 1, "to_month": 6, "factor": 0.5}
    e2 = {"kind": "leak", "from_month": 1, "to_month": 6, "leak_a": 0.01}
    both = sim.events(_event_case(e1, e2), 5)
    alone = sim.events(_event_case(e1), 5)
    assert both[0] == alone[0]
    plain = sim.build(sim.load_case("demo", DEMO), 5)[0]
    assert sim.build(_event_case(e1, e2), 5)[0].capacity_factor == plain.capacity_factor


def test_stop_steps_fall_in_equal_vertical_steps():
    case = _event_case({"kind": "stop_steps", "from_month": 2, "to_month": 5, "steps": 3, "total_v": 0.09})
    ev = sim.events(case, 2)[0]
    assert len(ev["days"]) == 3 and ev["days"] == sorted(ev["days"])
    pack, _ = sim.build(case, 2)
    pts = pack.bms.v_min_points
    assert len(pts) == 7
    assert [round(p[1], 12) for p in pts] == [0.0, 0.0, -0.03, -0.03, -0.06, -0.06, -0.09]
    assert all(b[0] > a[0] for a, b in zip(pts, pts[1:]))
    assert pts[2][0] - pts[1][0] == pytest.approx(1.0 / 86400.0)
    assert pack.bms.offset(pts, 400.0) == pytest.approx(-0.09)


def test_event_conflicts_and_unknown_kinds_are_refused():
    with pytest.raises(ValueError, match="unknown event"):
        sim.events(_event_case({"kind": "fire", "from_month": 1, "to_month": 1}), 1)
    clash = _event_case({"kind": "fan_failure", "from_month": 1, "to_month": 2, "factor": 0.5},
                        **{"pack.fan_points": [[10.0, 0.5]]})
    with pytest.raises(ValueError, match="fan_points"):
        sim.build(clash, 1)


def test_monthout_carries_the_drawn_errors_and_the_events():
    case = _event_case({"kind": "fan_failure", "from_month": 1, "to_month": 1, "factor": 0.5},
                       **{"sensors.voltage_gain_max": 0.0025, "sensors.temp_offset_max_c": 2.0})
    out = next(iter(sim.run(case, 1, 3)))
    assert 0 < abs(out.voltage_gain) <= 0.0025 and 0 < abs(out.temp_offset_c) <= 2.0
    assert out.events == sim.events(case, 3)
    assert (out.frame.temp_pack_c - out.true_frame.temp_pack_c).mean() == pytest.approx(out.temp_offset_c)
    other = next(iter(sim.run(case, 1, 4)))
    assert other.temp_offset_c != out.temp_offset_c


def test_events_are_written_in_the_manifest(tmp_path):
    from besslib.sim.truth import run_to_dir

    case = _event_case({"kind": "leak", "from_month": 1, "to_month": 1, "leak_a": 0.01})
    manifest = run_to_dir(case, 1, 2, tmp_path)
    assert manifest["events"] == json.loads((tmp_path / "manifest.json").read_text())["events"]
    assert manifest["events"][0]["kind"] == "leak"


def test_grid_power_pieces_stop_inside_their_step():
    over = {**DEMO, "duty.kind": "grid", "duty.calibration_days": 1, "duty.mid_p": 0.4,
            "duty.cycle_p": 0.5, "duty.calibration_p": 0.5, "duty.capacity_ah": 100.0}
    data = sim.load_case("demo", over)
    data["duty"] = {k: v for k, v in data["duty"].items() if k != "overlay"}
    out = [m.truth for m in sim.run(data, 1, 1, measured=False)]
    t = out[0]
    stops = t.stops[t.stops.kind.isin(["full", "empty"])]
    assert len(stops) > 5
    charging, discharging = t.current_a > 0, t.current_a < 0
    assert (t.v_cell_max[charging] < t.limit_max_v[charging]).all()
    assert (t.v_cell_min[discharging] > t.limit_min_v[discharging]).all()
    assert (t.stops.kind == "full").any() and (t.stops.tag == "calibration").any()


def test_per_cell_truth_adds_voltage_per_cell_and_a_c_rate_to_the_steps():
    from besslib.sim import truth

    out = next(iter(sim.run("demo", 1, 1, {**DEMO}, measured=False)))
    plain = truth.tables(out)["steps"]
    extra = truth.tables(out, (4, 100.0))["steps"]
    assert "voltage_cell_v" not in plain.columns
    assert np.allclose(extra["voltage_cell_v"], plain["voltage_v"] / 4.0)
    assert np.allclose(extra["current_c"], plain["current_a"] / 100.0)


def test_every_shipped_case_loads_and_builds():
    for name in sim.case_names():
        data = sim.load_case(name)
        pack, settings = sim.build(data, 1)
        assert pack.groups >= 1 and settings.get("months", 1) >= 1
        sim.events(data, 1)


def test_case_files_hold_their_settings():
    heat = sim.load_case("overheating")
    assert heat["event"][0]["kind"] == "fan_failure" and heat["bms"]["derate"][0][0] == 45.0
    assert sim.load_case("failing-cell")["event"][0]["kind"] == "leak"
    cold = sim.load_case("cold")
    assert cold["case"]["months"] == 12 and cold["bms"]["charge_block_below_c"] == 0.0
    string = sim.load_case("grid-string")
    assert string["pack"]["groups"] == 396 and string["case"]["per_cell_truth"] is True
    assert string["bms"]["supply_a"] == 0.0 and "self_discharge_mean_a" not in string["pack"]


def _validation():
    import sys
    from pathlib import Path

    path = str(Path(__file__).resolve().parents[1] / "validation")
    if path not in sys.path:
        sys.path.insert(0, path)


def test_seeds_and_the_one_seed_set_rule():
    _validation()
    import sim_cases

    assert sim_cases.parse_seeds("0-9") == list(range(10))
    assert len(sim_cases.parse_seeds("development")) == 200 and sim_cases.parse_seeds("held-out")[0] == 1000
    assert sim_cases.seed_set([0, 5]) == "development" and sim_cases.seed_set([1000]) == "held-out"
    assert sim_cases.seed_set([5, 1000]) == "mixed"
    with pytest.raises(SystemExit):
        sim_cases.parse_seeds("nine")


def test_case_modules_follow_the_protocol():
    _validation()
    import importlib

    import sim_cases

    for name, module_name in sim_cases.CASES.items():
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue  # a module not written yet
        for attr in ("CASE", "SETTINGS", "RESULT", "run_pack", "summary"):
            assert hasattr(module, attr), (name, attr)
        assert module.RESULT.endswith(".csv")
        for setting, cfg in module.SETTINGS.items():
            assert isinstance(cfg["overrides"], dict) and cfg["months"] >= 1, (name, setting)
        if name != "drift":
            assert module.CASE in sim.case_names()
