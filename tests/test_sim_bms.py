"""The BMS in the engine (``besslib.sim.bms``, ``besslib.sim.pack``): stops, the latch,
power pieces, ``until_soc``, a moving stop limit, a piece across a month, derating and the
charge block, each with checks S1, S2 and S4 of ``validation/sim_plant_checks.py``.

The pieces are built here and run through ``Engine.run``. Every value is an example value,
not from a source; the curve is the ``synth`` preset's (example values, not from a source).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from besslib.sim.bms import BmsSettings
from besslib.sim.cell import CellModel, GroupState
from besslib.sim.duty import Piece
from besslib.sim.pack import Engine, PackModel
from besslib.sim.thermal import Profile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))
import sim_plant_checks as checks  # noqa: E402

DAY, H = 86400.0, 3600.0
START = pd.Timestamp("2020-01-01")
CELL = CellModel(ocv_soc=(0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0, 1.1),
                 ocv_v=(3.0, 3.40, 3.50, 3.60, 3.75, 3.95, 4.05, 4.15, 4.30),
                 capacity_ah=100.0, r0_ohm=0.0014, r1_ohm=0.0007, tau1_s=600.0, r2_ohm=0.0003, tau2_s=60.0,
                 hyst_v=0.01, hyst_rate=20.0, ea_r0_j_mol=20000.0, ea_r1_j_mol=30000.0)


def pack(**kw) -> PackModel:
    bms = kw.pop("bms", {})
    base = dict(cell=CELL, groups=3, series=4, capacity_factor=(1.0, 0.97, 1.02), soc0=(0.5, 0.52, 0.49),
                bms=BmsSettings(**{"v_cell_max": 4.10, "v_cell_min": 3.45, "supply_a": 0.05, **bms}))
    base.update(kw)
    return PackModel(**base)


def filled(pieces, months: int = 1):
    """The pieces with rests in every gap, to the end of the run."""
    end = (START + pd.DateOffset(months=months) - START).total_seconds()
    out, t = [], 0.0
    for p in sorted(pieces, key=lambda p: p.start_s):
        if p.start_s > t:
            out.append(Piece(t, p.start_s))
        out.append(p)
        t = p.end_s
    if t < end:
        out.append(Piece(t, end))
    return out


def run(pk: PackModel, pieces, months: int = 1, update_s=None, limits=None):
    eng = Engine(pk, step_s=10.0, update_s=update_s, cell_every_s=600.0)
    days = int((START + pd.DateOffset(months=months) - START).days)
    lim = np.zeros((days, 2)) if limits is None else limits
    return eng, list(eng.run(filled(pieces, months), lim, START, months))


def assert_checks(pk, months, update_s=None):
    s1, s2, s4 = checks.s1_charge(months), checks.s2_energy(months, pk), checks.s4_bms(months, pk, update_s)
    assert s1["worst"] <= 1e-9, s1
    assert s2["worst"] <= 1e-6, s2
    assert s4["worst"] == 0 and s4["passed"], s4
    return s1, s2, s4


def cycles(days, charge_a=40.0, discharge_a=-35.0, **kw):
    out = []
    for d in days:
        out.append(Piece(d * DAY + 1 * H, d * DAY + 7 * H, "current", charge_a, **kw))
        out.append(Piece(d * DAY + 8 * H, d * DAY + 15 * H, "current", discharge_a, **kw))
    return out


def test_stops_and_the_latch():
    pk = pack()
    _, months = run(pk, cycles(range(5)))
    st = months[0].stops
    full, empty = st[st["kind"] == "full"], st[st["kind"] == "empty"]
    assert len(full) == 5 and len(empty) == 5
    assert (full["v_before"] < full["limit_v"]).all() and (full["v_after"] >= full["limit_v"]).all()
    # After a stop the piece rests until its end.
    t = months[0]
    for s in full.itertuples():
        k = int(round(s.seconds / 10.0))
        end = int(round((math.floor(s.seconds / DAY) * DAY + 7 * H) / 10.0))
        assert t.current_a[k - 1] == 40.0 and (t.current_a[k:end] == 0.0).all()
    assert_checks(pk, months)


def test_stop_at_a_block_start_when_the_current_is_applied():
    # Two charging pieces back to back: the second's larger R0 step crosses the limit at once.
    pk = pack()
    pieces = [Piece(1 * H, 7 * H, "current", 40.0), Piece(7 * H, 8 * H, "current", 200.0)]
    _, months = run(pk, pieces)
    st = months[0].stops
    assert len(st) == 2 and math.isnan(st["v_before"].iloc[1]) and st["seconds"].iloc[1] == 7 * H
    assert st["current_a"].iloc[1] == 200.0
    s4 = assert_checks(pk, months)[2]
    assert s4["n_stops_at_block_start"] == 1


@pytest.mark.parametrize("rule", ["cells", "pack"])
def test_stop_rules(rule):
    pk = pack(bms={"stop_rule": rule})
    _, months = run(pk, cycles(range(3)))
    assert (months[0].stops["rule"] == rule).all()
    if rule == "pack":
        assert (months[0].stops["group"] == -1).all()
    assert_checks(pk, months)


def test_power_piece():
    pk = pack()
    pieces = [Piece(1 * H, 6 * H, "power", -1500.0, hold_s=60.0), Piece(7 * H, 12 * H, "power", 1800.0, hold_s=120.0)]
    _, months = run(pk, pieces)
    t = months[0]
    for b in t.blocks.itertuples():
        if b.current_a == 0.0:
            continue
        k = int(round(b.seconds / 10.0))
        want = -1500.0 if b.seconds < 6 * H else 1800.0
        assert abs(t.current_a[k] * t.voltage_v[k] - want) <= 1e-10 * abs(want)
        assert b.duration_s <= (60.0 if want < 0 else 120.0)
    assert_checks(pk, months)


def test_power_beyond_the_largest():
    pk = pack()
    eng = Engine(pk)
    eng.state = GroupState(np.full(3, 0.5), np.zeros(3), np.zeros(3), np.zeros(3))
    prm = eng.params(0.0, np.full(3, 25.0))
    e = float((eng.ocv(eng.state.soc) * prm.series).sum())
    r = float(prm.r0.sum())
    i = eng._ask(Piece(0, 10, "power", -1e9), eng.state, prm)
    assert i == pytest.approx(-e / (2 * r), rel=1e-14)


def test_until_soc():
    pk = pack()
    pieces = [Piece(1 * H, 8 * H, "current", 30.0, until_soc=0.75),
              Piece(9 * H, 16 * H, "current", -30.0, until_soc=0.3)]
    _, months = run(pk, pieces)
    st = months[0].stops
    assert list(st["kind"]) == ["until", "until"]
    for s, target, sign in zip(st.itertuples(), (0.75, 0.3), (1, -1)):
        assert sign * (s.bms_soc - target) >= 0 and abs(s.bms_soc - target) <= 30.0 * 10.0 / (3600.0 * 100.0)
    t = months[0]
    k = int(round(st["seconds"].iloc[0] / 10.0))
    assert t.current_a[k - 1] == 30.0 and (t.current_a[k:int(8 * H / 10)] == 0.0).all()
    assert_checks(pk, months)


def test_moving_stop_limit():
    points = ((0.0, 0.0), (8.0, 0.08))
    pk = pack(bms={"v_min_points": points, "v_cell_min": 3.40})
    _, months = run(pk, cycles(range(10), charge_a=50.0, discharge_a=-45.0))
    empty = months[0].stops.query("kind == 'empty'")
    assert len(empty) == 10
    for s in empty.itertuples():
        day = math.floor(s.seconds / DAY)
        want = 3.40 + float(np.interp((day * DAY + 8 * H) / DAY, [0.0, 8.0], [0.0, 0.08]))
        assert abs(s.limit_v - want) <= 1e-12
        assert s.v_after <= s.limit_v < s.v_before
    assert empty["limit_v"].iloc[-1] > empty["limit_v"].iloc[0] + 0.07
    assert_checks(pk, months)


def test_piece_across_a_month():
    pk = pack(soc0=(0.9, 0.9, 0.9), bms={"supply_a": 0.0})
    jan31 = 30 * DAY
    pieces = [Piece(jan31 + 22 * H, jan31 + 26 * H, "current", -10.0),
              Piece(jan31 + 26 * H, jan31 + 27 * H),
              Piece(jan31 + 27 * H, jan31 + 40 * H, "current", -60.0)]
    _, months = run(pk, pieces, months=2)
    jan, feb = months
    at2 = int(2 * H / 10)
    assert (jan.current_a[-at2:] == -10.0).all() and (feb.current_a[:at2] == -10.0).all()
    assert feb.seconds[0] == 31 * DAY and feb.current_a[at2] == 0.0
    assert np.array_equal(jan.soc_end, feb.soc_start)
    assert (feb.stops["kind"] == "empty").sum() == 1  # the -60 A piece reaches the stop, then rests
    assert_checks(pk, months)


def test_latch_survives_a_month_end():
    # A drain-only month follows: S2 is judged against the drains there.
    pk = pack(soc0=(0.6, 0.6, 0.6), bms={"supply_a": 0.01})
    jan31 = 30 * DAY
    pieces = [Piece(jan31 + 20 * H, jan31 + 30 * H, "current", -30.0)]
    _, months = run(pk, pieces, months=2)
    jan, feb = months
    assert len(jan.stops) == 1 and jan.stops["kind"].iloc[0] == "empty"
    assert (feb.current_a[: 6 * 360] == 0.0).all() and (feb.current_a == 0.0).all()
    assert_checks(pk, months)


def test_derating_and_charge_block():
    derate = ((-10.0, 0.0, 0.2), (5.0, 0.15, 0.4), (30.0, 0.3, 0.4), (45.0, 0.1, 0.2))
    planted = tuple((d, 9.0, 3.0, -4.0) for d in range(0, 6, 2))  # cold from 09:00 to 12:00 on days 0, 2, 4
    prof = Profile(mean_c=12.0, daily_amp_c=8.0, planted=planted)
    pk = pack(pack_profile=prof, bms={"derate": derate, "charge_block_below_c": 0.0})
    pieces = []
    for d in range(6):
        pieces.append(Piece(d * DAY + 8 * H, d * DAY + 14 * H, "current", 60.0))
        pieces.append(Piece(d * DAY + 15 * H, d * DAY + 22 * H, "current", -60.0))
    _, months = run(pk, pieces, update_s=600.0)
    t = months[0]
    temp_at_start = prof(t.blocks["seconds"].to_numpy())
    for b, temp in zip(t.blocks.itertuples(), temp_at_start):
        ch, dis = pk.bms.current_limits(float(temp), float(temp), pk.nominal_ah)
        assert -dis - 1e-12 <= b.current_a <= ch + 1e-12
    cold = np.zeros(len(t.seconds), dtype=bool)
    for d, h0, hours, _ in planted:
        cold |= (t.seconds >= d * DAY + h0 * H) & (t.seconds < d * DAY + (h0 + hours) * H)
    assert (t.current_a[cold] <= 0.0).all()
    assert (t.current_a[~cold] > 0.0).any()
    assert t.current_a.max() < 60.0 and t.current_a.min() > -60.0  # derated below what was asked
    assert_checks(pk, months, update_s=600.0)


def test_balancing_bleeds_only_the_cells_above_the_lowest_by_the_spread():
    bms = BmsSettings(balance=True, balance_v=4.0, balance_spread_v=0.010)
    # 4.02 V is 20 mV above the lowest; 4.005 V is within the spread; 3.99 V is below balance_v.
    assert list(bms.bleeding(np.array([4.02, 4.005, 4.00]))) == [True, False, False]
    assert list(bms.bleeding(np.array([3.99, 3.95]))) == [False, False]
    # A permanent spread at the top: only the high cells come down, the lowest never bleeds.
    assert list(bms.bleeding(np.array([4.10, 4.05, 4.02]))) == [True, True, False]
