"""The presets of ``besslib.sim`` load, hold together, and their scripts' replay is the library's own."""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from besslib import sim
from besslib.sim.run import cell_model

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))
import sim_case_drift as drift  # noqa: E402
import sim_params_lic as lic  # noqa: E402
import sim_params_tran as tran  # noqa: E402
import sim_s10  # noqa: E402

NAMES = ("lfp", "nmc", "lmo")


def preset(name: str, **kw):
    return cell_model({"cell": {"preset": name, **kw}})


@pytest.mark.parametrize("name", NAMES)
def test_preset_curve_and_tables(name):
    c = preset(name)
    assert c.ocv_soc[0] == 0.0 and c.ocv_soc[-1] == 1.0 and len(c.ocv_soc) >= 41
    assert (np.diff(c.ocv_v) >= 0).all() and 2.0 < c.ocv_v[0] < c.ocv_v[-1] < 4.25
    assert c.tables is not None and (np.asarray(c.r0_table) > 0).all()
    cold, warm = (c.resistances(np.array([0.5]), np.array([t])) for t in (-10.0, 45.0))
    assert cold[0] > warm[0] and cold[1] > warm[1]


@pytest.mark.parametrize("name", NAMES)
def test_scaled_preset_keeps_the_voltage_at_the_same_rate(name):
    c = preset(name)
    k = 100.0 / c.capacity_ah
    big = preset(name, scale_to_ah=100.0)
    a, b = c.resistances(0.4, 5.0), big.resistances(0.4, 5.0)
    assert b[0] * k == pytest.approx(a[0]) and b[1] * k == pytest.approx(a[1]) and b[2] == pytest.approx(a[2])
    assert big.hyst_table == c.hyst_table and big.ocv_v == c.ocv_v


def test_lfp_has_hysteresis_and_the_others_have_none():
    lfp = preset("lfp")
    assert min(lfp.hyst_table) > 0 and lfp.hyst_rate > 0 and tuple(lfp.r_temp_c) == (15.0, 25.0, 35.0)
    for name in ("nmc", "lmo"):
        c = preset(name)
        assert c.hyst_v == 0.0 and c.hyst_table == () and tuple(c.r_temp_c) == (23.0,)


def test_every_preset_names_its_source_and_the_notice_credits_it():
    notice = resources.files("besslib.sim").joinpath("presets", "NOTICE.md").read_text()
    for name in NAMES:
        text = resources.files("besslib.sim").joinpath("presets", f"{name}.toml").read_text()
        assert "CC BY 4.0" in text and f"{name}.toml" in notice
    assert "10.5281/zenodo.19632939" in notice and tran.DOI in notice


def test_the_lfp_script_reads_no_file_of_20_or_30_degrees():
    assert not [f for f in lic.FILES if f.startswith(("20C", "30C"))] and set(lic.TEMPS).isdisjoint(sim_s10.TEMPS)


def test_replay_of_the_script_equals_the_library_s():
    """``sim_params_lic.Replay`` (plain floats) and ``sim_s10.replay`` (the library's blocks), on a made-up run."""
    c = preset("lfp")
    rng = np.random.default_rng(1)
    n = 400
    d = pd.DataFrame({"t": np.cumsum(rng.choice([0.01, 0.1, 1.0, 10.0, 60.0], n)), "i": rng.choice([0.0, 28.0, -28.0, 140.0], n),
                      "temp_c": 25.0 + 15.0 * np.sin(np.arange(n) / 40.0), "v": 0.0})
    d.loc[:150, "i"] = 28.0
    fast = lic.Replay(c, d, 0)
    soc, v = sim_s10.replay(c, d["t"].to_numpy(), d["i"].to_numpy(), d["temp_c"].to_numpy())
    assert np.abs(fast.error(c.hyst_rate) - v).max() <= 1e-10 and np.abs(fast.soc - soc).max() <= 1e-12


def test_presets_run_in_a_pack():
    for name, hi, lo in (("nmc", 4.15, 3.2), ("lmo", 4.15, 3.2), ("lfp", 3.6, 2.8)):
        data = sim.load_case("demo", {"cell": {"preset": name, "scale_to_ah": 100.0}, "pack.groups": 2,
                                      "bms.v_cell_max": hi, "bms.v_cell_min": lo, "case.update_s": 1800})
        m = next(iter(sim.run(data, 1, 1, measured=False)))
        kinds = set(m.truth.stops["kind"])
        assert {"full", "empty"} <= kinds, (name, kinds)


def test_monotone_and_points():
    np.testing.assert_allclose(lic.monotone(np.array([1.0, 3.0, 2.0, 4.0])), [1.0, 2.5, 2.5, 4.0])
    grid = np.round(np.arange(0, 1001) * 0.001, 6)
    y = np.sqrt(grid)
    k = lic.points(grid, [y])
    assert np.abs(np.interp(grid, grid[k], y[k]) - y).max() <= lic.TOLERANCE_V and len(k) >= 41


# --- the drifting-stop case -----------------------------------------------------------------------
def test_drift_case_holds_the_numbers_its_script_reads():
    data = sim.load_case("drifting-stop")
    target = drift.field_targets()
    assert data["duty"]["overlay"][0]["c_discharge"] == pytest.approx(target["c_rate"], abs=5e-5)
    stop = data["bms"]["v_cell_min"] - data["duty"]["overlay"][0]["v_min_offset_cell"]
    assert stop == pytest.approx(target["lowest_cell_v"], abs=5e-4)
    assert target["systems"] == ["01", "04"] and target["tests"] == 22
    start = pd.Timestamp(data["case"]["start"])
    days = [start + pd.Timedelta(days=d) for d in data["duty"]["overlay"][0]["days"]]
    assert [(d.year - start.year) * 12 + d.month for d in days] == list(drift.TEST_MONTHS) and {d.day for d in days} == {15}
    assert [start + pd.Timedelta(days=d) for d in drift.DRIFT_DAYS] == [start + pd.DateOffset(months=m) for m in (12, 72)]


@pytest.mark.parametrize("setting", list(drift.SETTINGS))
def test_drift_settings_build(setting):
    cfg = drift.SETTINGS[setting]
    data = sim.load_case(drift.CASE, cfg["overrides"])
    pack, _ = sim.build(data, 1)
    end = pack.bms.offset(pack.bms.v_min_points, drift.DRIFT_DAYS[1])
    assert end == pytest.approx(-drift.LIMIT_FALLS_MV[cfg["drift_mv"]] / 1000.0, abs=1e-12)
    assert pack.bms.offset(pack.bms.v_min_points, drift.DRIFT_DAYS[0]) == 0.0
    overlay = data["duty"]["overlay"][0]
    assert (overlay["v_min_offset_cell"] == 0.0) == setting.endswith(("-onestop", "-c10"))
    assert overlay["c_discharge"] == (0.1 if setting.endswith("-c10") else 0.8408)
    starts = drift.test_starts(data)
    assert len(starts) == 5 and all(s % 10 == 0 for s in starts)


def test_drift_line_fit_finds_a_planted_line():
    rng = np.random.default_rng(3)
    per = {d: pd.Series(2.0 + 0.15 * d / 10.0 + rng.normal(0, 0.1, 100)) for d in (0.0, 50.0, 100.0, 157.0)}
    fit = drift.line_fit(per)
    assert fit["a"] == pytest.approx(2.0, abs=0.05) and fit["b"] == pytest.approx(0.15, abs=0.01)
    assert fit["a_band"][0] <= fit["a"] <= fit["a_band"][1] and fit["b_band"][0] <= fit["b"] <= fit["b_band"][1]


def test_held_1s_holds_each_sample():
    f = pd.DataFrame({"current_a": [1.0, 2.0]}, index=pd.date_range("2020-01-01", periods=2, freq="10s", name="time"))
    h = drift.held_1s(f)
    assert len(h) == 11 and (h["current_a"].iloc[:10] == 1.0).all() and h["current_a"].iloc[10] == 2.0


def test_drift_limit_falls_lie_at_or_above_their_drifts():
    assert set(drift.LIMIT_FALLS_MV) == set(drift.DRIFTS.values()) and drift.LIMIT_FALLS_MV[0.0] == 0.0
    assert all(d <= f <= 1.2 * d for d, f in drift.LIMIT_FALLS_MV.items())


def test_true_fall_reads_the_line_of_the_operational_empty_stops():
    days = np.arange(10, 2192, 5.0)
    t0, t1 = drift.DRIFT_DAYS
    v = 3.4 - 0.1 * np.clip((days - t0) / (t1 - t0), 0, 1)
    stops = pd.DataFrame({"seconds": days * 86400.0, "kind": "empty", "soc": v, "tag": "", "first": True})
    test = stops.iloc[[300]].assign(soc=1.0, tag="capacity-test")  # a test's stop is left out
    year1, fall = drift.true_fall(pd.concat([stops, test], ignore_index=True), lambda x: x)
    assert year1 == pytest.approx(3.4) and fall == pytest.approx(-0.1, abs=1e-9)


def test_drift_rows_have_six_significant_digits():
    assert drift.rounded({"a": 1.23456789, "b": 3, "c": "x", "d": float("nan")})["a"] == 1.23457


def test_the_test_s_stop_lies_below_the_operational_one():
    """One simulated test in the first days, with and without the overlay's offset: only its empty stop moves."""
    stops = {}
    for name, offset in (("field", 0.128), ("onestop", 0.0)):
        over = {**drift._tests(days=[2], v_min_offset_cell=offset), "bms.stop_sigma_v": 0.0}
        m = next(iter(sim.run(drift.CASE, 1, 2, over, measured=False)))
        s = m.truth.stops
        stops[name] = s[(s["tag"] == "capacity-test") & (s["kind"] == "empty")].iloc[0]
        ordinary = s[(s["kind"] == "empty") & (s["tag"] != "capacity-test")]
        assert ordinary["limit_v"].to_numpy() == pytest.approx(3.38)
    assert stops["field"]["limit_v"] == pytest.approx(3.252) and stops["onestop"]["limit_v"] == pytest.approx(3.38)
    assert stops["field"]["soc"] < stops["onestop"]["soc"] - 0.1
