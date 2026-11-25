"""Check S10: the LFP preset against measured runs it was not built from.

The measured current of ``20C_hppc.csv`` and ``30C_hppc.csv`` (Gray and Vagg, Zenodo 19632939,
CC BY 4.0; ``sim_params_lic.py`` reads neither) goes through one preset cell, block by block with
``besslib.sim.cell``'s own functions: each sample's current is held to the next sample, at the
measured surface temperature. The run starts at the empty rest before the first full charge, at
state of charge 0, with the hysteresis voltage at its bound of the discharge side (the cell comes
from a discharge) and the RC element at rest. The voltages are compared sample by sample.

Pass: RMS error at most 20 mV between 10 and 90 % model state of charge, at 20 and at 30 degC.
Also reported: the full range, the largest error, the time-weighted RMS (the samples are 0.01 to
10 s apart and dense in and after the pulses), and where the error sits (pulse, the 20 minutes
after a pulse, C/10 run, other rest; and per 10 % of state of charge).

It is a check at temperatures not used to build the values, not on another cell.

Usage: python validation/sim_s10.py    # writes validation/results/sim/s10_lfp.csv
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

import sim_params_lic as lic
from besslib.sim.cell import CellModel, GroupParams, GroupState, OcvCurve, advance, trajectory
from besslib.sim.run import cell_model

OUT = Path(__file__).resolve().parent / "results" / "sim" / "s10_lfp.csv"
TEMPS = (20, 30)
PASS_MV = 20.0
INSIDE = (0.1, 0.9)
PULSE_A = 100.0  # the HPPC pulses are at 140 A, everything else at or below 28 A


def replay(cell: CellModel, t, i, temp_c, soc0: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """State of charge and voltage of one cell at every sample, each current held to the next sample."""
    ocv = OcvCurve(cell.ocv_soc, cell.ocv_v)
    one = np.ones(1)
    state = GroupState(soc=soc0 * one, p1=0.0 * one, p2=0.0 * one, h=-cell.hyst_bound(soc0 * one))
    soc, volts = np.zeros(len(t)), np.zeros(len(t))
    for k in range(len(t)):
        r0, r1, tau1 = cell.resistances(state.soc, float(temp_c[k]))
        prm = GroupParams(series=one, capacity_ah=cell.capacity_ah * one, r0=r0, r1=r1, tau1=tau1, r2=0.0 * one,
                          tau2=one, h_max=cell.hyst_bound(state.soc), kappa=cell.hyst_rate * one)
        cur = float(i[k]) * one
        _, v = trajectory(state, prm, ocv, cur, cur, [0.0])
        soc[k], volts[k] = state.soc[0], v[0, 0]
        if k + 1 < len(t):
            state, _ = advance(state, prm, ocv, cur, cur, float(t[k + 1] - t[k]))
    return soc, volts


def phases(t: np.ndarray, i: np.ndarray) -> np.ndarray:
    """What each sample belongs to."""
    pulse = np.abs(i) > PULSE_A
    last = np.full(len(t), -np.inf)
    last[pulse] = t[pulse]
    since = t - np.maximum.accumulate(last)
    out = np.where(np.abs(i) > 1.0, "C/10 run", "other rest").astype(object)
    out[(np.abs(i) <= 1.0) & (since <= 1250.0)] = "after a pulse"
    out[pulse] = "pulse"
    return out


def rms(e, w=None) -> float:
    if not len(e):
        return math.nan
    return math.sqrt(float(np.average(np.square(e), weights=w)))


def rows_of(temp: int, cell: CellModel) -> list[dict]:
    d = lic.read_run(lic.fetch(f"{temp}C_hppc.csv"))
    d = d.iloc[lic.first_charge_start(d):].reset_index(drop=True)
    t, i, v = (d[c].to_numpy(float) for c in ("t", "i", "v"))
    soc, model = replay(cell, t, i, d["temp_c"].to_numpy(float))
    e = 1000.0 * (model - v)
    w = np.r_[np.diff(t), 0.0]
    inside = (soc >= INSIDE[0]) & (soc <= INSIDE[1])
    kind = phases(t, i)

    def row(part: str, use) -> dict:
        k = np.flatnonzero(use)
        worst = k[np.argmax(np.abs(e[k]))] if len(k) else None
        return {"temp_c": temp, "part": part, "samples": len(k), "hours": float(w[k].sum() / 3600.0),
                "rms_mv": rms(e[k]), "rms_time_weighted_mv": rms(e[k], w[k]) if w[k].sum() > 0 else math.nan,
                "mean_mv": float(e[k].mean()) if len(k) else math.nan,
                "largest_mv": float(e[worst]) if worst is not None else math.nan,
                "largest_at_soc": float(soc[worst]) if worst is not None else math.nan,
                "largest_in": str(kind[worst]) if worst is not None else ""}

    out = [row("10-90 %", inside), row("full range", np.ones(len(e), bool)), row("below 10 %", soc < INSIDE[0]),
           row("above 90 %", soc > INSIDE[1])]
    for name in ("pulse", "after a pulse", "C/10 run", "other rest"):
        out.append(row(f"10-90 %, {name}", inside & (kind == name)))
    out += [row(f"10-90 %, C/10 run, {name}", inside & (kind == "C/10 run") & (np.sign(i) == s))
            for name, s in (("charge", 1), ("discharge", -1))]
    edges = np.linspace(0, 1, 11)
    for lo, hi in zip(edges[:-1], edges[1:]):
        out.append(row(f"{lo * 100:.0f}-{hi * 100:.0f} %", (soc >= lo) & (soc < hi)))
    return out


def check_s10(table: pd.DataFrame | None = None) -> list[dict]:
    """The check row of S10, from ``s10_lfp.csv`` (or the given table); information rows with it."""
    table = pd.read_csv(OUT) if table is None else table
    out = []
    part = table[table["part"] == "10-90 %"].set_index("temp_c")
    ok = all(float(part.loc[t, "rms_mv"]) <= PASS_MV for t in TEMPS)
    text = "; ".join(f"{t} degC: RMS {part.loc[t, 'rms_mv']:.1f} mV ({part.loc[t, 'samples']:.0f} samples; "
                     f"time-weighted {part.loc[t, 'rms_time_weighted_mv']:.1f} mV, largest {part.loc[t, 'largest_mv']:+.0f} mV "
                     f"at {100 * part.loc[t, 'largest_at_soc']:.0f} %, {part.loc[t, 'largest_in']})" for t in TEMPS)
    out.append({"check": "S10", "what": "LFP preset (built from the 15, 25 and 35 degC data): the measured current of the 20 and "
                "30 degC HPPC runs through one preset cell, voltage sample by sample, 10-90 % state of charge",
                "result": text, "pass_if": f"RMS <= {PASS_MV:.0f} mV at 20 and at 30 degC", "passed": bool(ok)})
    full = table[table["part"] == "full range"].set_index("temp_c")
    out.append({"check": "S10", "what": "information: the same, full range", "result": "; ".join(
        f"{t} degC: RMS {full.loc[t, 'rms_mv']:.1f} mV, time-weighted {full.loc[t, 'rms_time_weighted_mv']:.1f} mV, largest "
        f"{full.loc[t, 'largest_mv']:+.0f} mV at {100 * full.loc[t, 'largest_at_soc']:.1f} %, {full.loc[t, 'largest_in']}"
        for t in TEMPS), "pass_if": "information", "passed": ""})
    by = table.set_index(["temp_c", "part"])["rms_mv"]
    out.append({"check": "S10", "what": "information: where the error sits, 10-90 %", "result": "; ".join(
        f"{t} degC: " + ", ".join(f"{n} {by[(t, '10-90 %, ' + n)]:.1f} mV" for n in ("pulse", "after a pulse", "C/10 run", "other rest"))
        for t in TEMPS), "pass_if": "information", "passed": ""})
    return out


def main() -> None:
    cell = cell_model({"cell": {"preset": "lfp"}})
    table = pd.DataFrame([r for t in TEMPS for r in rows_of(t, cell)])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False, float_format="%.6g")
    print(table.to_string())
    for r in check_s10(table):
        print(r)


if __name__ == "__main__":
    main()
