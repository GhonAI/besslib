"""Case 1, overheating: B4's temperature hours (T1a), A5's resistance by band (T1b), the hottest group (T1c).

The pack: 14 groups of the NMC cell scaled to 100 Ah, a thermal network with the sensor at the housing,
a fan that fails on a random day in months 13-30, derating from 45 degrees C. Sensor temperature noise is 0
in this case, so the sensor's trace is what B4 counts and the truth is the same trace without besslib.

* T1a: ``count_usage`` with ``Limits(temp_high_c=`` the derating start ``)`` on the measured frames. Hours per
  1-degree band and the hours at or above the limit, against the hours counted from the true sensor
  temperature at the sample times (``true_frame``: the same binning as the reduction, the first sample of the
  record has no step). Tolerance: one sample step for every crossing of a band's edge by the trace
  (``synthetic_usage.py`` allows one step per interval edge); for the hours above the limit, one step per
  crossing of the limit. Pass: every band and the limit within tolerance, on 200 of 200 packs.
* T1b: ``estimate_resistance`` at each year end; of two neighbouring 5-degree bands that both have an
  estimate, the warmer one reads the lower resistance (W4's form). Pass: at least 80 % of pairs.
* T1c (information): hours the hottest group spends at or above the limit (truth ``cells`` table, every
  600 s) over the sensor's hours above it. besslib cannot see a cell.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_case_common as common
import sim_truth

from besslib import sim
from besslib.soh.config import RecordConfig
from besslib.soh.reduce import TEMP_BIN_LOW_C, TEMP_BINS, temp_hours
from besslib.soh.resistance import estimate_resistance
from besslib.soh.usage import Limits, count_usage

CASE = "overheating"
SETTINGS = {
    "fan-fails": {"overrides": {}, "months": 36},
    "fan-works": {"overrides": {"event": []}, "months": 36},  # information: the set-up's other half
}
RESULT = "heat.csv"
PASS_B_SHARE = 0.80
MAIN = "fan-fails"


class _TrueTemps:
    """Hours per 1-degree bin and edge crossings of the sensor's true temperature, month by month."""

    def __init__(self, limit_c: float) -> None:
        self.limit_c = limit_c
        self.hours = np.zeros(TEMP_BINS)
        self.cross = np.zeros(TEMP_BINS + 2)  # cross[e]: crossings of the lower edge of bin e
        self.last: int | None = None
        self.hot_group_s = 0.0
        self.step_s = math.nan

    def month(self, out) -> None:
        t = out.true_frame["temp_pack_c"].to_numpy(float)
        step = float((out.true_frame.index[1] - out.true_frame.index[0]).total_seconds())
        self.step_s = step
        k = np.clip(np.floor(t) - TEMP_BIN_LOW_C, 0, TEMP_BINS - 1).astype(np.int64)
        counted = k if self.last is not None else k[1:]  # the record's first sample has no step
        self.hours += np.bincount(counted, minlength=TEMP_BINS) * step / 3600.0
        prev = np.concatenate([[self.last if self.last is not None else k[0]], k[:-1]])
        lo, hi = np.minimum(prev, k), np.maximum(prev, k)
        for d in range(1, int((hi - lo).max()) + 1 if len(k) else 1):
            use = (hi - lo) >= d
            self.cross += np.bincount(lo[use] + d, minlength=len(self.cross))[: len(self.cross)]
        self.last = int(k[-1])
        cells = out.truth.cells
        if len(cells):
            hottest = cells.groupby("seconds")["temp_c"].max()
            self.hot_group_s += float((hottest >= self.limit_c).sum()) * 600.0

    def tolerance_h(self) -> np.ndarray:
        return (self.cross[:TEMP_BINS] + self.cross[1 : TEMP_BINS + 1]) * self.step_s / 3600.0

    @property
    def limit_bin(self) -> int:
        return int(self.limit_c - TEMP_BIN_LOW_C)


def _pairs(table: pd.DataFrame) -> tuple[int, int]:
    """Neighbouring bands (one band width apart) that both have an estimate, per time: pairs, colder reads higher."""
    ok = table[table["status"] == "estimated"]
    width = RecordConfig().temp_band_c
    n = held = 0
    for _, g in ok.groupby("time"):
        g = g.sort_values("temp_band_c")
        bands, values = g["temp_band_c"].to_numpy(float), g["resistance_ohm"].to_numpy(float)
        for k in range(len(g) - 1):
            if abs(bands[k + 1] - bands[k] - width) < 1e-9:
                n += 1
                held += int(values[k] > values[k + 1])
    return n, held


def run_pack(setting: str, seed: int) -> list[dict]:
    data = sim.load_case(CASE, SETTINGS[setting]["overrides"])
    limit = float(data["bms"]["derate"][0][0])
    truth = _TrueTemps(limit)
    rec = common.recorder(CASE, SETTINGS, setting, seed, on_month=truth.month)
    reduced = sim_truth.reduce_pack(rec)
    usage = count_usage(None, rec.spec, Limits(temp_high_c=limit), reduced=reduced)
    seen = temp_hours(reduced.daily).dropna(subset=["temp_c"]).groupby("temp_c")["hours"].sum()
    counted = np.zeros(TEMP_BINS)
    counted[(seen.index.to_numpy(float) - TEMP_BIN_LOW_C).astype(int)] = seen.to_numpy(float)
    tol = truth.tolerance_h() + 1e-9
    err = counted - truth.hours
    used = (truth.hours > 0) | (counted > 0)
    outside = used & (np.abs(err) > tol)
    kb = truth.limit_bin
    above_true = float(truth.hours[kb:].sum())
    above_tol = float(truth.cross[kb]) * truth.step_s / 3600.0 + 1e-9
    above_besslib = float(usage.hours_above_temp_high)
    years = pd.DatetimeIndex([f"{y}-01-01" for y in range(rec.start.year + 1, rec.start.year + rec.months // 12 + 1)])
    resistance = estimate_resistance(None, rec.spec, reduced=reduced, at=years)
    n_pairs, n_colder = _pairs(resistance)
    fan_day = next((e["day"] for e in sim.events(rec.data, seed) if e["kind"] == "fan_failure"), -1)
    row = {
        **common.base_row(setting, seed),
        "fan_day": int(fan_day), "limit_c": limit, "step_s": truth.step_s,
        "bands_used": int(used.sum()), "bands_outside": int(outside.sum()),
        "worst_band_error_h": float(np.abs(err[used]).max()) if used.any() else 0.0,
        "hours_above_true": above_true, "hours_above_besslib": above_besslib,
        "hours_above_error_h": above_besslib - above_true, "hours_above_tol_h": above_tol,
        "t1a_ok": bool(not outside.any() and abs(above_besslib - above_true) <= above_tol),
        "resistance_pairs": n_pairs, "resistance_colder_higher": n_colder,
        "hours_hottest_group": truth.hot_group_s / 3600.0,
    }
    return [row]


def summary(table: pd.DataFrame) -> list[dict]:
    rows = []
    for setting, t in common.by_setting(table, SETTINGS):
        n = len(t)
        ok = int(t["t1a_ok"].sum())
        rows.append({
            "check": "T1a", "what": f"{setting}: B4 hours per 1-degree band and hours at or above the derating start "
                                    "against the true sensor trace, tolerance one step per edge crossing",
            "result": f"{ok} of {n} packs within tolerance; {int(t['bands_outside'].sum())} bands outside; "
                      f"worst band error {t['worst_band_error_h'].max():.3g} h",
            "pass_if": "200 of 200 packs" if setting == MAIN else "information",
            "passed": bool(ok == n and n > 0) if setting == MAIN else ""})
        pairs, colder = int(t["resistance_pairs"].sum()), int(t["resistance_colder_higher"].sum())
        share = common.fraction(colder, pairs)
        rows.append({
            "check": "T1b", "what": f"{setting}: A5 at each year end, neighbouring 5-degree bands with estimates: "
                                    "the colder reads the higher resistance",
            "result": f"{colder} of {pairs} pairs ({share:.1%})", "pass_if": f"at least {PASS_B_SHARE:.0%} of pairs" if setting == MAIN else "information",
            "passed": bool(pairs and share >= PASS_B_SHARE) if setting == MAIN else ""})
        above = t["hours_above_true"]
        hot = t["hours_hottest_group"]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = (hot / above).replace([np.inf, -np.inf], np.nan)
        rows.append(common.info_row(
            "T1c", f"{setting}: hours at or above the limit, hottest group (truth, 600 s) over the sensor",
            f"sensor median {above.median():.1f} h, hottest group median {hot.median():.1f} h, ratio median "
            f"{ratio.median():.2f} over the {int(ratio.notna().sum())} packs with sensor hours above the limit; "
            f"{int((above > 0).sum())} of {n} packs have sensor hours above the limit"))
    return rows
