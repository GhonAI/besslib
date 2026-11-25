"""Packs run end to end through ``besslib.sim.run``, built as case dictionaries here (not
shipped case files), with checks S1 (charge), S2 (energy) and S4 (BMS) of
``validation/sim_plant_checks.py``.

Every value in these cases is an example value, not from a source; the cell starts from the
``synth`` preset (example values, not from a source).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from besslib import sim
from besslib.sim.pack import Engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))
import sim_plant_checks as checks  # noqa: E402

DAY = 86400.0
LEAK_GROUP, LEAK_FROM = 5, 10 * DAY + 5 * 3600.0 + 3.0  # off the 10 s grid on purpose
FAN_FAIL = 20 * DAY


def big_case() -> dict:
    """14 groups with a capacity spread, a thermal network with a fan failure, balancing, a
    leak in one group, 2RC, hysteresis, Arrhenius resistances, derating; two months."""
    g = 14
    leak = [math.inf] * g
    leak[LEAK_GROUP] = 40.0
    leak_from = [0.0] * g
    leak_from[LEAK_GROUP] = LEAK_FROM
    return {
        "case": {"name": "test-big", "start": "2021-01-01", "step_s": 10, "update_s": 3600, "cell_every_s": 600},
        "cell": {"preset": "synth", "r2_ohm": 3e-4, "tau2_s": 60.0, "hyst_v": 0.01, "hyst_rate": 20.0,
                 "ea_r0_j_mol": 20000.0, "ea_r1_j_mol": 30000.0, "rc_hold": "capacitance"},
        "pack": {"groups": g, "series": 1, "capacity_spread_sd": 0.03, "resistance_spread_sd": 0.05,
                 "soc0": [0.45 + 0.01 * (k % 3) for k in range(g)],
                 "self_discharge_a": [0.002] * g, "leak_ohm": leak, "leak_from_s": leak_from,
                 "fan_points": [[FAN_FAIL, 0.2]]},
        "thermal": {"mode": "network", "c_group_j_k": 2000.0, "c_housing_j_k": 20000.0, "g_group_housing_w_k": 0.5,
                    "g_neighbour_w_k": 1.0, "g_group_air_w_k": 0.3, "g_housing_air_w_k": 3.0, "sensor": "housing"},
        "temperature": {"ambient": {"mean_c": 24.0, "daily_amp_c": 5.0}},
        "bms": {"stop_rule": "cells", "v_cell_max": 4.12, "v_cell_min": 3.40, "stop_sigma_v": 0.003, "supply_a": 0.05,
                "balance": True, "balance_v": 3.85, "balance_spread_v": 0.005, "bleed_ohm": 30.0,
                "derate": [[-10.0, 0.0, 0.2], [5.0, 0.2, 0.5], [40.0, 0.5, 0.5], [55.0, 0.1, 0.2]],
                "stop_temp_c": 65.0},
        "duty": {"kind": "home", "capacity_ah": 100.0, "peak_c": [0.2, 0.45]},
    }


@pytest.fixture(scope="module")
def big():
    data = big_case()
    pack, _ = sim.build(data, 3)
    months = list(sim.run(data, months=2, seed=3, measured=False))
    return pack, months


def test_big_pack_checks(big):
    pack, months = big
    s1, s2, s4 = checks.s1_charge(months), checks.s2_energy(months, pack), checks.s4_bms(months, pack, update_s=3600)
    assert s1["passed"] and s1["worst"] <= 1e-9, s1
    assert s2["passed"] and s2["worst"] <= 1e-6, s2
    assert "month-end states of charge" in s2["path"]
    assert s4["passed"] and s4["worst"] == 0 and s4["n_stops"] > 10, s4


def test_big_pack_features(big):
    pack, months = big
    stops = [m.truth.stops for m in months]
    kinds = set().union(*(set(s["kind"]) for s in stops))
    assert {"full", "empty"} <= kinds
    assert len({int(g) for s in stops for g in s["group"]}) >= 2  # the first cell to stop is not always the same
    bal = sum(m.truth.balance[["bleed_ah", "leak_ah"]].to_numpy() for m in months)
    assert (bal[:, 0] > 0).sum() >= 1  # balancing ran
    assert bal[LEAK_GROUP, 1] > 0 and np.delete(bal[:, 1], LEAK_GROUP).max() == 0.0
    # The leak starts at the first step at or after its time, and that step starts a block.
    on = math.ceil(LEAK_FROM / 10.0) * 10.0
    cells = months[0].truth.cells
    lk = cells[cells["group"] == LEAK_GROUP]
    assert (lk.loc[lk["seconds"] < on, "leak_a"] == 0).all() and (lk.loc[lk["seconds"] >= on, "leak_a"] > 0).all()
    assert on in set(months[0].truth.blocks["seconds"])
    # The fan failure: the groups run warmer above the air afterwards.
    excess = []
    for m in months:
        c = m.truth.cells
        amb = np.interp(c["seconds"], m.truth.seconds, m.truth.temp_ambient_c)
        excess.append((c["seconds"].to_numpy(), c["temp_c"].to_numpy() - amb))
    sec = np.concatenate([e[0] for e in excess])
    ex = np.concatenate([e[1] for e in excess])
    assert ex[sec >= FAN_FAIL + 2 * DAY].mean() > ex[(sec < FAN_FAIL) & (sec > 2 * DAY)].mean() + 0.1


def test_cell_table_matches_the_terminal(big):
    pack, months = big
    for m in months:
        t = m.truth
        c = t.cells
        total = c.groupby("seconds")["voltage_v"].sum()
        k = np.round((total.index.to_numpy() - t.seconds[0]) / 10.0).astype(int)
        assert np.abs(total.to_numpy() - t.voltage_v[k]).max() <= 1e-9
        first = c[c["seconds"] == t.seconds[0]].sort_values("group")
        assert np.array_equal(first["soc"].to_numpy(), t.soc_start)
    assert np.array_equal(months[0].truth.soc_end, months[1].truth.soc_start)


def test_capacitance_hold_scales_tau():
    data = big_case()
    pack, _ = sim.build(data, 3)
    e = Engine(pack)
    cold, warm = e.params(0.0, np.full(14, 0.0)), e.params(0.0, np.full(14, 25.0))
    np.testing.assert_allclose(cold.tau1 / warm.tau1, cold.r1 / warm.r1, rtol=1e-14)
    np.testing.assert_allclose(cold.tau2 / warm.tau2, cold.r2 / warm.r2, rtol=1e-14)
    assert (cold.r0 > warm.r0).all()
    np.testing.assert_allclose(warm.tau1, 600.0, rtol=1e-14)


def test_fading_pack_uses_block_sums():
    data = big_case()
    data["pack"]["fade_points_per_year"] = 5.0
    data["thermal"] = {"mode": "prescribed"}
    data["temperature"] = {"pack": {"mean_c": 25.0}}
    data["pack"].pop("fan_points")
    pack, _ = sim.build(data, 4)
    months = list(sim.run(data, months=1, seed=4, measured=False))
    s1, s2 = checks.s1_charge(months), checks.s2_energy(months, pack)
    assert s1["worst"] <= 1e-9 and s2["worst"] <= 1e-6 and "block sums" in s2["path"]


def test_synth_case_checks():
    data = sim.load_case("synth", {"pack.fade_points_per_year": 0.0})
    pack, _ = sim.build(data, 1)
    months = list(sim.run(data, months=2, seed=1, measured=False))
    s1, s2, s4 = checks.s1_charge(months), checks.s2_energy(months, pack), checks.s4_bms(months, pack)
    assert s1["worst"] <= 1e-9 and s2["worst"] <= 1e-6
    # The synth rule checks the BMS's own copy of the pack: every stop lies in its step.
    assert s4["rule"] == "synth" and s4["bad_stops"] == 0 and s4["n_stops"] > 100


def table_case() -> dict:
    """The big pack with resistance and hysteresis tables (example values, not from a source)."""
    data = big_case()
    data["cell"].update(r_soc=[0.1, 0.5, 0.9], r_temp_c=[15.0, 30.0], r0_table=[[2e-3, 1.6e-3, 1.5e-3], [1.5e-3, 1.2e-3, 1.1e-3]],
                        r1_table=[[1e-3, 8e-4, 7e-4], [7e-4, 5e-4, 4e-4]], tau1_table=[[500.0, 600.0, 700.0], [400.0, 500.0, 600.0]],
                        hyst_soc=[0.0, 0.5, 1.0], hyst_table=[0.02, 0.0, 0.03])
    data["pack"]["resistance_growth_per_year"] = 0.1
    return data


def test_table_pack_checks():
    data = table_case()
    pack, _ = sim.build(data, 3)
    months = list(sim.run(data, months=1, seed=3, measured=False))
    s1, s2, s4 = checks.s1_charge(months), checks.s2_energy(months, pack), checks.s4_bms(months, pack, update_s=3600)
    assert s1["passed"] and s2["passed"] and s4["passed"] and s4["n_stops"] > 10, (s1, s2, s4)


def test_engine_parameters_follow_each_group_s_state_and_temperature():
    data = table_case()
    pack, _ = sim.build(data, 3)
    e = Engine(pack)
    soc, temps = np.linspace(0.05, 0.95, 14), np.linspace(-5.0, 45.0, 14)
    t = 2.0 * 365.25 * DAY
    prm = e.params(t, temps, soc)
    r0, r1, tau = pack.cell.resistances(soc, temps)
    rf = np.asarray(pack.resistance_factor)
    np.testing.assert_allclose(prm.r0, r0 * rf * 1.2, rtol=1e-13)
    np.testing.assert_allclose(prm.r1, r1 * rf * 1.2, rtol=1e-13)
    np.testing.assert_allclose(prm.tau1, tau * 1.2, rtol=1e-13)  # the capacitance is held: tau grows with r1
    np.testing.assert_allclose(prm.h_max, np.interp(soc, [0.0, 0.5, 1.0], [0.02, 0.0, 0.03]), rtol=1e-13)


def test_constant_tables_give_the_constant_cell():
    """Tables with one value everywhere at the reference temperature: the same run as the constants."""
    a = big_case()
    a["thermal"], a["temperature"] = {"mode": "prescribed"}, {"pack": {"mean_c": 25.0}}
    a["pack"].pop("fan_points")
    b = {**a, "cell": dict(a["cell"])}
    b["cell"].update(r_soc=[0.5], r_temp_c=[25.0], r0_table=[[0.02 / 14]], r1_table=[[0.01 / 14]], tau1_table=[[600.0]],
                     hyst_soc=[0.0, 1.0], hyst_table=[0.01, 0.01])
    (ma,), (mb,) = (list(sim.run(x, months=1, seed=3, measured=False)) for x in (a, b))
    assert np.abs(ma.truth.voltage_v - mb.truth.voltage_v).max() <= 1e-9
    assert np.array_equal(ma.truth.current_a, mb.truth.current_a)


def test_synth_rules_refuse_tables():
    data = sim.load_case("synth")
    data["cell"].update({k: v for k, v in table_case()["cell"].items() if k.startswith(("r_", "r0_t", "r1_t", "tau1_t"))})
    with pytest.raises(ValueError, match="synth"):
        sim.build(data, 1)
