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
    assert len(sim_cases.parse_seeds("development")) == 200 and sim_cases.parse_seeds("held-out")[0] == 2000
    assert sim_cases.seed_set([0, 5]) == "development" and sim_cases.seed_set([2000]) == "held-out"
    assert sim_cases.seed_set([5, 2000]) == "mixed"
    assert sim_cases.seed_set([1000]) == "mixed"  # the seeds of version 0.2's run are no longer a set of their own
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


# --- the pass rules of version 0.3's case checks, on hand-made tables at their edges ------------------------

def _rows(by_check: list[dict], check: str) -> list[dict]:
    return [r for r in by_check if r["check"] == check]


def test_held_out_rows_go_to_version_0_3s_files_only(tmp_path):
    _validation()
    import pandas as pd
    import sim_case_heat
    import sim_cases
    from pathlib import Path

    results = Path(sim_cases.ROOT) / "validation" / "results"
    assert sim_cases.RESULTS == results / "sim_v03" and sim_cases.CHECKS == results / "checks_sim_v03.csv"
    assert sim_cases.RESULTS_02 == results / "sim"
    rows = [{"setting": next(iter(sim_case_heat.SETTINGS)), "seed": 2000}]
    for folder in (sim_cases.RESULTS_02, results, tmp_path):
        with pytest.raises(SystemExit):
            sim_cases.write_rows(sim_case_heat, rows, "held-out", folder)
    with pytest.raises(SystemExit):
        sim_cases.write_rows(sim_case_heat, rows, "development", sim_cases.RESULTS_02)
    path = sim_cases.write_rows(sim_case_heat, [{**rows[0], "seed": 0}], "development", tmp_path)
    assert pd.read_csv(path)["seed_set"].tolist() == ["development"]


def _grid_row(setting, seed, estimated=0, refusals_ok=True, estimated_l=0, refusals_ok_l=True):
    return {"setting": setting, "seed": seed, "month_ends": 36, "estimated_months": estimated,
            "all_refusals_say_not_possible": refusals_ok, "reasons": "not possible from this data: x x36",
            "estimated_months_limits": estimated_l, "all_refusals_say_not_possible_limits": refusals_ok_l,
            "reasons_limits": "not possible from this data: y x36", "with_band": 0, "n_no_reference": 0, "held_rest": 0,
            "mean_error_rest_ah": math.nan, "estimate_over_true_capacity": 0.5}


def test_t5a_l_and_t5b_prime_count_the_right_table():
    _validation()
    import pandas as pd
    import sim_case_grid

    def passed(rows, check):
        (r,) = _rows(sim_case_grid.summary(pd.DataFrame(rows)), check)
        return r["passed"]

    never = [_grid_row("nmc-never", k, estimated=3) for k in range(3)]  # T5a, no limits: a published fail
    assert passed(never, "T5a") is False and passed(never, "T5a-L") is True
    assert passed(never[:2] + [_grid_row("nmc-never", 2, estimated=3, estimated_l=1)], "T5a-L") is False
    assert passed(never[:2] + [_grid_row("nmc-never", 2, estimated=3, refusals_ok_l=False)], "T5a-L") is False
    weekly = [_grid_row("nmc-7", k, estimated_l=30) for k in range(3)]  # T5b': no limits given, rule A alone
    assert passed(weekly, "T5b'") is True
    assert passed(weekly[:2] + [_grid_row("nmc-7", 2, estimated=1)], "T5b'") is False


def test_t5a_without_limits_is_the_field_case(monkeypatch):
    _validation()
    import sim_case_common
    import sim_case_grid
    from besslib.frames import PackSpec

    monkeypatch.setattr(sim_case_common, "capacity_table", lambda reduced, spec, start, months: spec)
    spec = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.0, cells_in_series=14, chemistry="LMO/NMC",
                    voltage_max_v=58.0, voltage_min_v=42.0)
    plain, limited = sim_case_grid.capacity_tables(None, spec, None, 1)
    assert (plain.voltage_max_v, plain.voltage_min_v) == (None, None)
    assert (limited.voltage_max_v, limited.voltage_min_v) == (58.0, 42.0)


def test_r_needs_not_fewer_estimates_than_0_2():
    _validation()
    import pandas as pd
    import sim_case_common

    def row(new, old, judged=True):
        return sim_case_common.r_row("x", pd.DataFrame({"estimated_months": new, "estimated_months_02": old}), judged)

    assert row([10, 10], [10, 10])["passed"] is True
    assert row([10, 9], [10, 10])["passed"] is False  # one month end fewer fails
    assert row([11, 9], [10, 10])["passed"] is True  # the rule is the sum
    assert row([0, 0], [10, 10], judged=False)["passed"] == ""


def _drift_table(width_d0: float, rest_held: list[int], rest_nan: int = 0):
    """Packs at D0 with a median band width; tests: ``rest_held`` (1/0 per test, with a band) plus ``rest_nan`` tests
    with a band but without a reading voltage."""
    import pandas as pd

    packs = [{"row": "pack", "setting": "D0", "seed": k, "nominal_ah": 15.0, "empty_drift_mv": 0.0, "true_fall_mv": 0.0,
              "month_ends_held": 9, "month_ends": 10, "mean_error_ah": 0.001 * (k - 1), "month_ends_without_band": 0,
              "month_ends_without_truth": 0, "median_band_points": width_d0, "estimated_months": 60,
              "estimated_months_02": 60} for k in range(3)]
    tests = [{"row": "test", "setting": "D0", "seed": k % 3, "estimate_ah": 14.0, "test_ah": 14.0, "t_rest": 14.0,
              "t_ops": 14.0, "t_test": 14.0, "t_fixed": 14.0, "held": h, "held_rest": h, "has_band": 1, "test_status": "ok"}
             for k, h in enumerate(rest_held)]
    tests += [{**tests[0], "t_rest": math.nan, "held_rest": 0, "held": 0} for _ in range(rest_nan)]
    return pd.DataFrame(packs + tests)


def test_w_is_the_width_against_1_2_times_version_0_2s():
    _validation()
    import sim_case_drift

    old = sim_case_drift.width_02()
    assert sim_case_drift.WIDTH_FACTOR == 1.2
    for width, ok in ((1.19 * old, True), (1.21 * old, False)):
        (w,) = _rows(sim_case_drift.summary(_drift_table(width, [1] * 9 + [0])), "W")
        assert w["passed"] is ok


def test_t6b_prime_counts_a_test_without_a_reading_voltage_as_not_held():
    _validation()
    import pandas as pd
    import sim_case_drift

    old = sim_case_drift.width_02()
    # 17 held of 20 tests with a reading voltage (85 %); 2 more without one: 17 of 22 = 77 %, a fail (D93)
    (r,) = _rows(sim_case_drift.summary(_drift_table(old, [1] * 17 + [0] * 3, rest_nan=2)), "T6b'")
    assert r["passed"] is False and r["result"].startswith("17 of 22 tests")
    assert "2 with a band but without a reading voltage" in r["result"] and "17 of 20 without them, 85.0%" in r["result"]
    (r,) = _rows(sim_case_drift.summary(_drift_table(old, [1] * 17 + [0] * 3)), "T6b'")
    assert r["passed"] is True
    # a test without a band counts as not held too: 17 of 21
    table = _drift_table(old, [1] * 17 + [0] * 3)
    table = pd.concat([table, table.iloc[[-1]].assign(has_band=0)], ignore_index=True)
    (r,) = _rows(sim_case_drift.summary(table), "T6b'")
    assert r["passed"] is False and r["result"].startswith("17 of 21 tests")
