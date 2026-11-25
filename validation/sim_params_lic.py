"""The LFP cell of ``besslib.sim``: ``presets/lfp.toml`` from measured open data of one 280 Ah cell.

Source: Gray and Vagg, "LIC cell characterisation" (EU Horizon project TEMPEST), Zenodo 19632939,
CC BY 4.0, https://zenodo.org/records/19632939. A missing file is downloaded into
``data/lic_19632939/`` (git-ignored) and its md5 compared with the one in Zenodo's record.

The files of 20 and 30 degC are not read here: check S10 (``sim_s10.py``) judges the preset on
them. From the two analysis tables only the rows of 15, 25 and 35 degC are used.

How each value is read:

* capacity: ``LIC_capacity_analysis.csv``, 25 degC;
* R0, R1, tau1 = R1 C1: ``LIC_hppc_analysis.csv``, 15, 25 and 35 degC, 10 states of charge;
* open-circuit voltage: ``25C_capacity.csv``, the C/10 charge and the C/10 discharge, each against
  its own charge from its own start divided by the measured capacity (the charge counted from
  empty, the discharge from full), both averaged in bins of 0.1 % state of charge; the curve is their
  mean at equal charge, made non-decreasing;
* largest hysteresis voltage: half of their difference less R0 times the mean current (not below 0).
  The RC element's share of the difference (R1 times the current) stays in it;
* both curves' points: 41 equal steps of 2.5 %, then more points where the straight line between two
  points is off by more than 1 mV, worst first;
* activation energies: ln(R/T) = c(state of charge) + Ea / (R_gas T), least squares over the three
  temperatures with a constant of its own per state of charge; used only beyond 15-35 degC, where
  they are an extrapolation;
* ``rc_hold``: the tables' own choice between a constant capacitance and a constant time constant,
  whichever varies less over the three temperatures;
* hysteresis rate: the value on a grid that gives the lowest RMS voltage error (sample by sample, 10
  to 90 % state of charge) over the HPPC runs of 15, 25 and 35 degC, each from the empty rest before
  its first full charge.

Writes ``src/besslib/sim/presets/lfp.toml`` and ``validation/results/sim/lfp_preset.csv``.

Usage: python validation/sim_params_lic.py [--verify]    # --verify: compare every file's md5 again
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tomllib
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.sim.cell import GAS_CONSTANT, KELVIN, CellModel

ROOT = Path(__file__).resolve().parents[1]
RECORD = "19632939"
FOLDER = ROOT / "data" / f"lic_{RECORD}"
PRESET = ROOT / "src" / "besslib" / "sim" / "presets" / "lfp.toml"
OUT = ROOT / "validation" / "results" / "sim" / "lfp_preset.csv"
TEMPS = (15, 25, 35)  # the only temperatures read
FILES = ["LIC_capacity_analysis.csv", "LIC_hppc_analysis.csv", "25C_capacity.csv"] + [f"{t}C_hppc.csv" for t in TEMPS]
BIN = 0.001  # state of charge
TOLERANCE_V = 0.001
KAPPA_GRID = tuple(round(10 ** (k / 24), 3 - min(2, k // 24)) for k in range(0, 24 * 3 + 12))  # 1 to 2900 in steps of 10 %


def fetch(name: str, verify: bool = False) -> Path:
    """The file, downloaded when missing; its md5 is compared with Zenodo's on download (or ``verify``)."""
    path = FOLDER / name
    if path.exists() and not verify:
        return path
    with urllib.request.urlopen(f"https://zenodo.org/api/records/{RECORD}") as r:
        listed = {f["key"]: f["checksum"] for f in json.load(r)["files"]}
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"https://zenodo.org/api/records/{RECORD}/files/{name}/content") as r:
            path.write_bytes(r.read())
    md5 = "md5:" + hashlib.md5(path.read_bytes()).hexdigest()
    if md5 != listed[name]:
        raise ValueError(f"{name}: {md5}, Zenodo's record says {listed[name]}")
    return path


def read_run(path: Path) -> pd.DataFrame:
    """A raw file as ``t`` (s), ``i`` (A, positive = charging), ``v`` (V), ``temp_c``; empty rows left out.

    The column names differ between files; their order does not."""
    d = pd.read_csv(path)
    d.columns = ["t", "i", "v", "temp_k"]
    d = d.dropna().reset_index(drop=True)
    if not (np.diff(d["t"]) > 0).all():
        raise ValueError(f"{path.name}: the time does not increase")
    d["temp_c"] = d.pop("temp_k") - KELVIN
    return d


def first_charge_start(d: pd.DataFrame, rate_a: float = 20.0) -> int:
    """The sample at rest just before the first charge at more than ``rate_a``."""
    return int(np.argmax(d["i"].to_numpy() > rate_a)) - 1


# --- the tables -----------------------------------------------------------------------------
def tables() -> dict:
    a = pd.read_csv(fetch("LIC_hppc_analysis.csv"))
    a = a[a["Temperature_C"].isin(TEMPS)]
    grid = {c: a.pivot(index="Temperature_C", columns="SoC", values=c).loc[list(TEMPS)]
            for c in ("R0_Ohm", "R1_Ohm", "C1_F")}
    r0, r1, c1 = (grid[c].to_numpy(float) for c in ("R0_Ohm", "R1_Ohm", "C1_F"))
    return {"soc": [float(s) for s in grid["R0_Ohm"].columns], "r0": r0, "r1": r1, "c1": c1, "tau1": r1 * c1}


def activation(r: np.ndarray) -> tuple[float, float]:
    """Ea (J/mol) and the RMS relative misfit of ln(r/T) = c_k + Ea / (R T), one c_k per state of charge."""
    t = np.array(TEMPS, dtype=float) + KELVIN
    y = np.log(r / t[:, None])
    x = 1.0 / (GAS_CONSTANT * t)
    xc, yc = x - x.mean(), y - y.mean(axis=0)
    ea = float((xc[:, None] * yc).sum() / ((xc**2).sum() * r.shape[1]))
    return ea, float(np.sqrt(np.mean((yc - ea * xc[:, None]) ** 2)))


# --- the open-circuit curve --------------------------------------------------------------------
def branch(d: pd.DataFrame, capacity: float, sign: int) -> pd.DataFrame:
    """One C/10 run (its constant-current part and, on charge, the tail at the voltage limit) against
    state of charge: the charge from the run's own start over the measured capacity."""
    i = d["i"].to_numpy()
    on = np.flatnonzero(sign * i > 1.0)
    d = d.iloc[on[0] : on[-1] + 1]
    t, i = d["t"].to_numpy(), d["i"].to_numpy()
    q = np.r_[0.0, np.cumsum(i[:-1] * np.diff(t))] / 3600.0 / capacity
    return pd.DataFrame({"soc": q if sign > 0 else 1.0 + q, "v": d["v"].to_numpy(), "i": i})


def binned(b: pd.DataFrame, grid: np.ndarray, column: str) -> np.ndarray:
    """The mean in a bin around each grid point (half a bin at the ends), then linear between bins."""
    k = np.round(b["soc"] / BIN).astype(int)
    m = b.groupby(k)[["soc", column]].mean()
    return np.interp(grid, m["soc"], m[column])


def monotone(y: np.ndarray) -> np.ndarray:
    """The non-decreasing curve nearest to ``y`` (pool adjacent violators)."""
    vals, sizes = [], []
    for v in y:
        vals.append(float(v))
        sizes.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            n = sizes[-2] + sizes[-1]
            vals[-2:] = [(vals[-2] * sizes[-2] + vals[-1] * sizes[-1]) / n]
            sizes[-2:] = [n]
    return np.repeat(vals, sizes)


def points(grid: np.ndarray, curves: list[np.ndarray]) -> np.ndarray:
    """The grid indices kept: every 2.5 %, then the worst point until every curve is within the tolerance."""
    keep = set(range(0, len(grid), round(0.025 / BIN))) | {len(grid) - 1}
    while True:
        k = np.array(sorted(keep))
        err = np.max([np.abs(np.interp(grid, grid[k], c[k]) - c) for c in curves], axis=0)
        worst = int(np.argmax(err))
        if err[worst] <= TOLERANCE_V:
            return k
        keep.add(worst)


def ocv(capacity: float, tab: dict) -> dict:
    d = read_run(fetch("25C_capacity.csv"))
    chg, dis = branch(d, capacity, +1), branch(d, capacity, -1)
    grid = np.round(np.arange(0, round(1 / BIN) + 1) * BIN, 6)
    vc, vd = binned(chg, grid, "v"), binned(dis, grid, "v")
    ic, idis = binned(chg, grid, "i"), binned(dis, grid, "i")
    amps = 0.5 * (ic - idis)  # the mean of the two currents' sizes
    r0 = np.interp(grid, tab["soc"], tab["r0"][TEMPS.index(25)])
    raw = 0.5 * (vc + vd)
    mean = monotone(raw)
    bound = np.maximum(0.5 * (vc - vd) - r0 * amps, 0.0)
    k = points(grid, [mean, bound])
    # Equal neighbours (from the pooling) are kept: the curve may be level, not falling.
    return {"soc": grid[k], "ocv": mean[k], "hyst": bound[k], "moved_v": float(np.abs(mean - raw).max()),
            "charge_ah": float(chg["soc"].iloc[-1] * capacity), "discharge_ah": float((1 - dis["soc"].iloc[-1]) * capacity)}


# --- the hysteresis rate ------------------------------------------------------------------------
class Replay:
    """One HPPC run with everything prepared that does not depend on the hysteresis rate. One loop over the
    samples, in plain floats; ``sim_s10.replay`` does the same with the library's own block functions."""

    def __init__(self, cell: CellModel, d: pd.DataFrame, start: int) -> None:
        d = d.iloc[start:]
        self.t, self.i, self.v = (d[c].to_numpy(float) for c in ("t", "i", "v"))
        dt = np.diff(self.t)
        self.soc = np.r_[0.0, np.cumsum(self.i[:-1] * dt) / (3600.0 * cell.capacity_ah)]
        r0, r1, tau = cell.resistances(self.soc, d["temp_c"].to_numpy(float))
        decay = np.exp(-dt / tau[:-1])
        target = self.i * r1
        p = 0.0
        rc = np.zeros(len(self.t))
        for k in range(len(dt)):
            p = target[k] + (p - target[k]) * decay[k]
            rc[k + 1] = p
        self.base = np.interp(self.soc, cell.ocv_soc, cell.ocv_v) + self.i * r0 + rc
        self.step = np.abs(self.i[:-1]) * dt / (3600.0 * cell.capacity_ah)
        bound = cell.hyst_bound(self.soc)
        self.target, self.h0 = np.sign(self.i) * bound, -float(bound[0])  # it rests after a discharge to empty
        self.use = (self.soc >= 0.1) & (self.soc <= 0.9)

    def error(self, kappa: float) -> np.ndarray:
        """Model less measured voltage, at every sample."""
        decay, h = np.exp(-kappa * self.step).tolist(), self.h0
        target = self.target.tolist()
        out = [h]
        for k in range(len(decay)):
            h = target[k] + (h - target[k]) * decay[k]
            out.append(h)
        return self.base + np.array(out) - self.v


def hysteresis_rate(cell: CellModel) -> tuple[float, pd.DataFrame]:
    runs = {}
    for t in TEMPS:
        d = read_run(fetch(f"{t}C_hppc.csv"))
        runs[t] = Replay(cell, d, first_charge_start(d))
    rows = []
    for kappa in (0,) + KAPPA_GRID:
        row = {"kappa": kappa}
        total, n = 0.0, 0
        for t, r in runs.items():
            e = r.error(kappa)[r.use]
            row[f"rms_{t}_mv"] = 1000.0 * math.sqrt(float(np.mean(e**2)))
            total, n = total + float(np.sum(e**2)), n + len(e)
        row["rms_mv"] = 1000.0 * math.sqrt(total / n)
        rows.append(row)
    table = pd.DataFrame(rows)
    return float(table.loc[table["rms_mv"].idxmin(), "kappa"]), table


# --- the files ---------------------------------------------------------------------------------
def fmt(values, digits: int) -> str:
    return "[" + ", ".join(f"{float(v):.{digits}g}" for v in values) + "]"


def wrapped(name: str, values, digits: int, per_line: int = 12) -> str:
    parts = [f"{float(v):.{digits}g}" for v in values]
    lines = [", ".join(parts[k : k + per_line]) for k in range(0, len(parts), per_line)]
    return f"{name} = [\n    " + ",\n    ".join(lines) + ",\n]"


def rows_text(name: str, rows, digits: int) -> str:
    return f"{name} = [\n" + "".join(f"    {fmt(r, digits)},\n" for r in rows) + "]"


def toml_text(v: dict) -> str:
    temps = ", ".join(str(t) for t in TEMPS)
    return f"""# One measured LFP cell of 280 Ah, as a besslib.sim cell. Written by validation/sim_params_lic.py; do not edit.
# Data: Gray and Vagg, "LIC cell characterisation" (TEMPEST), Zenodo {RECORD}, CC BY 4.0 (presets/NOTICE.md).
# Not a model of any product: one cell's measured values, read as each comment says. Values are per cell.
name = "lfp"
source = "https://zenodo.org/records/{RECORD}, files named at each value"
credit = "Gray and Vagg, LIC cell characterisation, EU Horizon project TEMPEST, Zenodo {RECORD}, CC BY 4.0"
notes = "{v['notes']}"
# LIC_capacity_analysis.csv, 25 degC (between 2.50 and 3.65 V at C/10). The model's capacity does not depend on temperature.
capacity_ah = {v['capacity']:.7g}

# Open-circuit voltage: 25C_capacity.csv, the mean of the C/10 charge and discharge voltages at equal charge
# (state of charge = charge over the capacity above); points every 2.5 %, more where the curve bends (within 1 mV).
{wrapped('ocv_soc', v['ocv']['soc'], 4)}
{wrapped('ocv_v', v['ocv']['ocv'], 6)}

# Largest hysteresis voltage: the same file, half of the difference of the two curves less R0 times the current, not below 0.
{wrapped('hyst_soc', v['ocv']['soc'], 4)}
{wrapped('hyst_table', v['ocv']['hyst'], 4)}
# How fast the hysteresis voltage follows the charge: {v['kappa_text']}
hyst_rate = {v['kappa']:g}

# Resistances and time constant: LIC_hppc_analysis.csv at {temps} degC (its other rows are not used),
# R0_Ohm, R1_Ohm and tau1 = R1_Ohm * C1_F. One row per temperature, one column per state of charge.
r_soc = {fmt(v['tab']['soc'], 4)}
r_temp_c = [{', '.join(f'{t:.1f}' for t in TEMPS)}]
{rows_text('r0_table', v['tab']['r0'], 6)}
{rows_text('r1_table', v['tab']['r1'], 6)}
{rows_text('tau1_table', v['tab']['tau1'], 6)}

# Beyond {TEMPS[0]}-{TEMPS[-1]} degC: an extrapolation with the Arrhenius form of besslib.sim.cell. The activation energies are
# fitted to the tables above over {TEMPS[0]}-{TEMPS[-1]} degC (least squares, a constant of its own per state of charge;
# RMS misfit {100 * v['misfit0']:.1f} % for R0, {100 * v['misfit1']:.1f} % for R1).
ea_r0_j_mol = {v['ea0']:.0f}.0
ea_r1_j_mol = {v['ea1']:.0f}.0
# {v['hold_text']}
rc_hold = "{v['hold']}"

# The tables at 50 % and 25 degC, for a reader that takes one value; with tables the model does not use them.
r0_ohm = {v['r0_mid']:.6g}
r1_ohm = {v['r1_mid']:.6g}
tau1_s = {v['tau_mid']:.6g}
t_ref_c = 25.0
"""


def build() -> tuple[str, pd.DataFrame]:
    caps = pd.read_csv(fetch("LIC_capacity_analysis.csv"))
    capacity = float(caps.loc[caps["Temperature_C"] == 25, "Capacity_Ah"].iloc[0])
    tab = tables()
    curve = ocv(capacity, tab)
    (ea0, misfit0), (ea1, misfit1) = activation(tab["r0"]), activation(tab["r1"])
    spread = {k: float(np.log(tab[k]).std(axis=0).mean()) for k in ("c1", "tau1")}
    hold = "tau" if spread["tau1"] < spread["c1"] else "capacitance"
    mid = TEMPS.index(25)
    r_mid, r1_mid, tau_mid = (float(np.interp(0.5, tab["soc"], tab[k][mid])) for k in ("r0", "r1", "tau1"))
    cell = CellModel(
        ocv_soc=tuple(curve["soc"]), ocv_v=tuple(curve["ocv"]), capacity_ah=capacity, r0_ohm=r_mid, r1_ohm=r1_mid,
        tau1_s=tau_mid, ea_r0_j_mol=round(ea0), ea_r1_j_mol=round(ea1), rc_hold=hold, r_soc=tuple(tab["soc"]),
        r_temp_c=tuple(float(t) for t in TEMPS), r0_table=tuple(map(tuple, tab["r0"])),
        r1_table=tuple(map(tuple, tab["r1"])), tau1_table=tuple(map(tuple, tab["tau1"])),
        hyst_soc=tuple(curve["soc"]), hyst_table=tuple(curve["hyst"]))
    kappa, sweep = hysteresis_rate(cell)
    best = sweep.loc[sweep["rms_mv"].idxmin()]
    v = {
        "capacity": capacity, "tab": tab, "ocv": curve, "ea0": ea0, "ea1": ea1, "misfit0": misfit0, "misfit1": misfit1,
        "hold": hold, "kappa": kappa, "r0_mid": r_mid, "r1_mid": r1_mid, "tau_mid": tau_mid,
        "hold_text": f"Over the three temperatures the {'time constant' if hold == 'tau' else 'capacitance'} of the table varies less "
                     f"(standard deviation of the logarithm: time constant {spread['tau1']:.2f}, capacitance {spread['c1']:.2f}).",
        "kappa_text": f"the lowest RMS voltage error over the HPPC runs\n# of 15, 25 and 35 degC (15C_hppc.csv, 25C_hppc.csv, "
                      f"35C_hppc.csv; 10-90 %, sample by sample): {best['rms_mv']:.1f} mV;\n# the grid is in "
                      f"validation/results/sim/lfp_preset.csv.",
        "notes": "measured at 15-35 degC; beyond, an extrapolation. No ageing, no diffusion element: the voltage of a rest "
                 "longer than some minutes is the open-circuit voltage with the hysteresis",
    }
    rows = []

    def add(item, value, unit, file, method, soc=math.nan, temp=math.nan):
        rows.append(dict(item=item, temp_c=temp, soc=soc, value=value, unit=unit, file=file, method=method))

    add("capacity", capacity, "Ah", "LIC_capacity_analysis.csv", "read", temp=25)
    add("charge of the C/10 charge run", curve["charge_ah"], "Ah", "25C_capacity.csv", "current times time, with the tail", temp=25)
    add("charge of the C/10 discharge run", curve["discharge_ah"], "Ah", "25C_capacity.csv", "current times time", temp=25)
    add("largest change made to keep the curve from falling", 1000 * curve["moved_v"], "mV", "25C_capacity.csv", "pool adjacent violators", temp=25)
    add("curve points", len(curve["soc"]), "", "25C_capacity.csv", "2.5 % steps, then within 1 mV", temp=25)
    for name, ea, mis in (("ea_r0", ea0, misfit0), ("ea_r1", ea1, misfit1)):
        add(name, ea, "J/mol", "LIC_hppc_analysis.csv", "fit of ln(R/T) against 1/T")
        add(f"{name} RMS misfit", mis, "relative", "LIC_hppc_analysis.csv", "fit of ln(R/T) against 1/T")
    for k in ("tau1", "c1"):
        add(f"spread of ln {k} over temperature", spread[k], "", "LIC_hppc_analysis.csv", "mean of the standard deviations per state of charge")
    for key, unit in (("r0", "ohm"), ("r1", "ohm"), ("c1", "F"), ("tau1", "s")):
        for j, t in enumerate(TEMPS):
            for k, s in enumerate(tab["soc"]):
                add(key, tab[key][j, k], unit, "LIC_hppc_analysis.csv", "r1 times c1" if key == "tau1" else "read", s, t)
    for s, o, h in zip(curve["soc"], curve["ocv"], curve["hyst"]):
        add("ocv", o, "V", "25C_capacity.csv", "mean of the C/10 charge and discharge", s, 25)
        add("hysteresis bound", h, "V", "25C_capacity.csv", "half of the difference less R0 times the current", s, 25)
    for r in sweep.itertuples():
        add(f"RMS error at hyst_rate {r.kappa:g}", r.rms_mv, "mV", "15C_hppc.csv 25C_hppc.csv 35C_hppc.csv", "10-90 %, sample by sample")
    for t in TEMPS:
        add(f"RMS error at hyst_rate {kappa:g}", best[f"rms_{t}_mv"], "mV", f"{t}C_hppc.csv", "10-90 %, sample by sample", temp=t)
    return toml_text(v), pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    for name in FILES:
        fetch(name, args.verify)
    text, table = build()
    tomllib.loads(text)
    PRESET.write_text(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False, float_format="%.6g")
    print(f"wrote {PRESET.relative_to(ROOT)} and {OUT.relative_to(ROOT)} ({len(table)} rows)")
    print(table[~table["item"].isin(["r0", "r1", "c1", "tau1", "ocv", "hysteresis bound"])].to_string())


if __name__ == "__main__":
    main()
