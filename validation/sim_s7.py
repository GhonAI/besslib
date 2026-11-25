"""Check S7: the SunSpec bus of besslib.sim read and written by pysunspec2, the SunSpec Alliance's own client.

For each shipped case (seed 1) the pack runs live behind a Modbus TCP server on 127.0.0.1, with time standing
still between the client's calls. Two snapshots per case: the first after the batch run-in (one month; the 396-group
string one month; failing-cell and overheating after the months of their events) plus 10 steps, the second 360 steps
later. The cold case runs twice, also from 1 May: in February its pack is empty and the BMS blocks charging, so no
current flows and the set-points have nothing to act on. At each snapshot pysunspec2 scans the device and reads every point of models 1, 802, 803 and 804. Each value
must equal ``sunspec.expected`` (written from the BMS state, not from the registers) within half of its scale step;
integers, enums, bitfields and strings exactly; a point the BMS does not know must read as "not implemented".
Then each writable point is written by the client, read back, and its effect on the pack is checked at the next step
(and not before).

Pass: 0 mismatches. It needs pymodbus and pysunspec2 (``pip install "besslib[modbus]" pysunspec2``).

Usage:
    python validation/sim_s7.py --case demo          # one case
    python validation/sim_s7.py                      # every shipped case (long: run it on the build machine)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = (1, 802, 803, 804)
HOST = "127.0.0.1"
SECOND_START = {"cold": "2020-05-01"}
FIRST_STEPS, LATER_STEPS = 10, 360
SLAVE = 1


def start_of(case: str) -> str:
    """First day of the month the live run starts at: after the case's events, one month in otherwise."""
    from besslib import sim

    data = sim.load_case(case)
    first = pd.Timestamp(data.get("case", {}).get("start", "2020-01-01"))
    months = 1 if case == "grid-string" else max([1] + [int(e.get("to_month", 1)) for e in data.get("event", [])])
    return (first + pd.DateOffset(months=months)).strftime("%Y-%m-%d")


def walk(model):
    """(path, point) of every point of a pysunspec2 model; a repeating group's path is ``name/index``."""
    def rec(group, path):
        for p in group.points.values():
            yield path, p
        for name, items in group.groups.items():
            for k, g in enumerate(items):
                yield from rec(g, f"{name}/{k}")
    yield from rec(model, "")


def same(point, got, want, step: float | None) -> str | None:
    """None when equal (``cvalue`` against the expected physical value), else the reason."""
    if want is None or got is None:
        return None if (want is None and got is None) else f"read {got!r}, expected {want!r}"
    if point.pdef["type"] == "string":
        return None if str(got).rstrip("\x00") == str(want).rstrip("\x00") else f"read {got!r}, expected {want!r}"
    tol = 0.5 * step * (1 + 1e-9) + 1e-12 if step is not None else 0.0
    return None if abs(float(got) - float(want)) <= tol else f"read {got!r}, expected {want!r} (tolerance {tol:g})"


def compare(device, live, sunspec, where: str, out: list) -> int:
    """Read every point of the four models and compare it with ``expected``; returns the points compared.

    Fewer points compared than the layout holds is itself a mismatch."""
    want = sunspec.expected(live.state(), live.scales)
    layout = [p for p in sunspec.points() if p.type != "pad"]
    seen = set()
    for model_id in sunspec.MODELS:
        model = device.models[model_id][0]
        model.read()
        for path, point in walk(model):
            if point.pdef["type"] == "pad":
                continue
            key = (model_id, path, point.pdef["name"])
            if key not in want:
                out.append(f"{where}: {key} read but not in expected()")
                continue
            seen.add(key)
            sf = point.pdef.get("sf")
            exp = want[(model_id, "", sf)] if sf else None
            step = None if exp is None else 10.0 ** exp
            why = same(point, point.cvalue, want[key], step)
            if why:
                out.append(f"{where}: model {model_id} {path + '/' if path else ''}{key[2]}: {why}")
    if len(seen) != len(layout):
        out.append(f"{where}: {len(seen)} points compared, the layout has {len(layout)}")
    return len(seen)


def against_engine(live, where: str, out: list) -> None:
    """The BusState against the case data, the BMS rules (``besslib.sim.bms``) and the engine itself, never against
    ``live.py``'s own formulas: ratings, settings, limits, voltages, indexes, balancing, temperatures, count, texts."""
    import math

    import numpy as np

    s, last, engine, pack = live.state(), live.last, live.engine, live.pack
    bms, g, series = pack.bms, pack.groups, pack.series
    t = engine.temps[:g]
    soc = 100.0 * min(max(float(engine.bms_soc), 0.0), 1.0)
    limits = [None if math.isinf(x) else float(x) for x in bms.current_limits(float(t.max()), float(t.min()), pack.nominal_ah)]
    wh = float(live.spec.nominal_energy_kwh) * 1000.0
    bleed = bms.bleeding(last.cell_v)
    pairs = {
        "pack_v": (s.pack_v, last.pack_v), "current_a": (s.current_a, -last.current_a),
        "power_w": (s.power_w, -last.current_a * last.pack_v),
        "cell_v_max": (s.cell_v_max, float(last.cell_v.max())), "cell_v_min": (s.cell_v_min, float(last.cell_v.min())),
        "cell_v_avg": (s.cell_v_avg, float(last.cell_v.mean())),
        "cell_v_max_index": (s.cell_v_max_index, int(np.argmax(last.cell_v)) * series + 1),
        "cell_v_min_index": (s.cell_v_min_index, int(np.argmin(last.cell_v)) * series + 1),
        "temp_max_c": (s.temp_max_c, float(t.max())), "temp_min_c": (s.temp_min_c, float(t.min())),
        "temp_avg_c": (s.temp_avg_c, float(t.mean())), "soc_pct": (s.soc_pct, soc),
        "ah_rating": (s.ah_rating, float(live.spec.nominal_capacity_ah)), "wh_rating": (s.wh_rating, wh),
        "soh_pct": (s.soh_pct, float(bms.soh_percent)),
        "v_max": (s.v_max, pack.cells_in_series * float(bms.v_cell_max)),
        "v_min": (s.v_min, pack.cells_in_series * float(bms.v_cell_min)),
        "charge_a_max": (s.charge_a_max, limits[0]), "discharge_a_max": (s.discharge_a_max, limits[1]),
        "charge_w_max": (s.charge_w_max, max(float(p[1]) for p in bms.derate) * wh if bms.derate else None),
        "discharge_w_max": (s.discharge_w_max, max(float(p[2]) for p in bms.derate) * wh if bms.derate else None),
        "cells_balancing": (s.cells_balancing, int(bleed.sum()) * series), "cells": (s.cells, pack.cells_in_series),
        "heartbeat": (s.heartbeat, int(live.seconds) % 65535),
    }
    for name, (got, want) in pairs.items():
        if got is None or want is None:
            if got is not want:
                out.append(f"{where}: BusState.{name} {got!r}, engine {want!r}")
        elif abs(got - want) > 1e-9 * max(1.0, abs(want)):
            out.append(f"{where}: BusState.{name} {got!r}, engine {want!r}")
    if s.serial != "SIMULATED":
        out.append(f"{where}: serial {s.serial!r}")
    for text in (s.manufacturer, s.model, s.version, s.serial):
        if str(live.seed) in text.split() or (live.name and live.name.lower() in text.lower().split()):
            out.append(f"{where}: a string carries the seed or the case name: {text!r}")


def setv(point, value) -> None:
    if point.pdef.get("sf"):
        point.cvalue = value
    else:
        point.value = value


def writes(device, live, bus, sunspec, where: str, out: list) -> tuple[int, int, int]:
    """Write every writable point; returns (writes, points compared, effects not exercised).

    After the write the whole map is compared with the state before the step (the register echoes the accepted value
    at once, nothing else moves), then again after one step (the effect), and the effect on the current is checked
    where current flows in the direction the point acts on. Where none does within 24 simulated hours the effect is
    counted as not exercised, never as a pass."""
    done = compared = missed = 0

    def flow(direction):
        """Steps until current flows ('charge', 'discharge' or 'any'); True when it does."""
        ok = {"charge": lambda c: c < -1.0, "discharge": lambda c: c > 1.0, "any": lambda c: abs(c) > 1.0}[direction]
        for _ in range(24):
            if ok(live.state().current_a):
                return True
            bus.step(360)
        return ok(live.state().current_a)

    def put(model_id, name, value, group=None, direction=None, effect=None, restore=None):
        nonlocal done, compared, missed
        flows = flow(direction) if direction else None
        if callable(value):  # a value that depends on the state: read once the current flows
            value = value()
        label = f"{where}: write {model_id}/{name}={value}"
        model = device.models[model_id][0]
        target = model.groups[group][0] if group else model
        point = target.points[name]
        setv(point, value)
        (target if group else point).write()
        model.read()
        done += 1
        before = live.state()
        compared += compare(device, live, sunspec, f"{label} before the step", out)
        if direction and before.current_a == 0.0 and flows:
            out.append(f"{label}: the current stopped before the step")
        bus.step(1)
        after = live.state()
        compared += compare(device, live, sunspec, f"{label} after the step", out)
        if effect is not None:
            bad = effect(after)
            if bad:
                out.append(f"{label}: {bad}")
        if direction:
            if flows:
                if after.current_a != 0.0:
                    out.append(f"{label}: the current still flows after the step ({after.current_a} A)")
            else:
                missed += 1
        if restore is not None:
            setv(point, restore)
            (target if group else point).write()
            model.read()
            bus.step(1)

    state = live.state()
    lo, hi = state.soc_reserve_min_pct, state.soc_reserve_max_pct
    put(1, "DA", state.device_address, effect=lambda s: None if s.device_address == state.device_address else "DA moved")
    put(802, "CtrlHb", 7, effect=lambda s: None if s.control_heartbeat == 7 else f"control_heartbeat {s.control_heartbeat}",
        restore=0)
    put(802, "AlmRst", 1, effect=lambda s: None if s.alarm_reset == 0 else f"alarm_reset {s.alarm_reset}")
    # Reserves: just beyond the state of charge, so the flow in that direction must stop. The value is computed once
    # that flow runs, since the state of charge moves while the check waits for it.
    for direction, name, sign, field in (("discharge", "SoCRsvMin", 0.5, "soc_reserve_min_pct"),
                                         ("charge", "SocRsvMax", -0.5, "soc_reserve_max_pct")):
        written = []

        def beyond(sign=sign, written=written):
            written.append(min(max(round(live.state().soc_pct + sign, 2), 0.01), 99.99))
            return written[-1]

        put(802, name, beyond, direction=direction, restore=lo if name == "SoCRsvMin" else hi,
            effect=lambda s, name=name, field=field, written=written: None
            if abs(getattr(s, field) - written[-1]) < 0.005 else f"{name} not held")
    put(802, "SetOp", 2, direction="any", effect=lambda s: None if (not s.connected and s.set_op == 2) else "still connected",
        restore=1)
    put(802, "SetInvState", 1, direction="any", effect=lambda s: None if s.set_inv_state == 1 else "set_inv_state moved",
        restore=3)
    put(803, "StrSetCon", 2, group="string", direction="any",
        effect=lambda s: None if not s.connected else "still connected", restore=1)
    put(803, "StrSetEna", 2, group="string", direction="any",
        effect=lambda s: None if not s.enabled else "still enabled", restore=1)
    put(804, "SetCon", 2, direction="any", effect=lambda s: None if (not s.connected and s.con_request == 0) else
        "still connected or request not reset", restore=1)
    put(804, "SetEna", 2, direction="any", effect=lambda s: None if (not s.enabled and s.ena_request == 0) else
        "still enabled or request not reset", restore=1)
    after = live.state()
    if not (after.connected and after.enabled and after.set_inv_state == 3
            and (after.soc_reserve_min_pct, after.soc_reserve_max_pct) == (lo, hi)):
        out.append(f"{where}: the writes were not restored")
    return done, compared, missed


def one_case(case: str, seed: int = 1, start: str | None = None) -> dict:
    from sunspec2.modbus.client import SunSpecModbusClientDeviceTCP

    from besslib.sim.bus import server, sunspec
    from besslib.sim.bus.live import LivePack

    start = start or start_of(case)
    live = LivePack(case, seed=seed, start=start)
    mismatches: list[str] = []
    points = writes_done = missed = 0
    with server.running(live, HOST, 0, speed=None) as bus:
        bus.step(FIRST_STEPS)
        for k, label in enumerate(("snapshot 1", "snapshot 2")):
            if k:
                bus.step(LATER_STEPS)
            device = SunSpecModbusClientDeviceTCP(slave_id=SLAVE, ipaddr=HOST, ipport=bus.port, timeout=10)
            device.base_addr_list = [sunspec.BASE]
            try:
                device.scan()
                against_engine(live, f"{case} {label}", mismatches)
                points += compare(device, live, sunspec, f"{case} {label}", mismatches)
                if k == 1:
                    n, c, m = writes(device, live, bus, sunspec, f"{case} {label}", mismatches)
                    writes_done, points, missed = writes_done + n, points + c, missed + m
                    against_engine(live, f"{case} after the writes", mismatches)
            finally:
                device.close()
    return {"case": case, "start": start, "points": points, "writes": writes_done, "not_exercised": missed,
            "mismatches": len(mismatches), "text": mismatches}


def check_s7(cases=None, seeds=(1,)) -> list[dict]:
    """One row per case, start and seed: points read, writes made, mismatches and the text of each."""
    from besslib import sim

    rows = []
    for case in cases or sim.case_names():
        for seed in seeds:
            for start in [None] + ([SECOND_START[case]] if case in SECOND_START else []):
                rows.append({**one_case(case, seed, start), "seed": seed})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", action="append", help="a case name (repeatable); default: every shipped case")
    args = parser.parse_args(argv)
    rows = check_s7(args.case)
    for r in rows:
        print(f"{r['case']} from {r['start']} seed {r['seed']}: {r['points']} points read, {r['writes']} writes, "
              f"{r['mismatches']} mismatches, effect not exercised {r['not_exercised']}")
        for line in r["text"]:
            print("   ", line)
    return 1 if any(r["mismatches"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
