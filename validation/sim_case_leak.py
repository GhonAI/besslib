"""Case 3, a failing cell: A4's band against the true apparent charge loss (T3a), detection of the leak (T3b), A1 (T3c).

The pack: as case 2 with sd 1 %. One group gets a leak (a resistor across its cell, ``leak_a`` mA at 3.7 V
per cell) on a random day in months 13-24. Settings ``leak0`` (the controls: the same onset day and group are
drawn, nothing is planted), ``leak5``, ``leak15``, ``leak50``; ``-off`` is the same with balancing off.

* Truth of the apparent loss at a month end: the BMS supply plus the mean, over the groups without the leak,
  of the group's self-discharge and balancing bleed (``balance`` table), in mA, over the hours of that month.
  The same truth weighted over the estimator's memory time (``exp(-age / 180 days)`` over the months so
  far, by hours) is reported next to it as information.
* T3a: every month end with an A4 band, before the onset (the month end is not after the onset day) and from
  180 days after it (the memory time): the band holds the truth. Per pack: month ends with a band, those that
  hold. Pass, per setting, pooled over packs: 85-95 % hold.
* T3b: A4 at the last month end at or before the onset and at the first month end at or after onset + 180 days.
  Detected when both have a band and the second band's low end lies above the first band's high end; not
  estimated counts as not detected. The controls use their drawn onset day.
  Pass: at least 90 % of packs at 15 mA and at 50 mA; at most 10 % of the controls; 5 mA is reported.
* T3c (information): with balancing off, the same share of detections and the change of the A1 estimate between
  the same two dates, against the balanced settings.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_case_common as common
import sim_truth

from besslib import sim
from besslib.soh.charge_loss import charge_loss

CASE = "failing-cell"
MEMORY_DAYS = 180.0
MONTH_END = pd.Timedelta(days=1.5)  # besslib's month end is the last day at 00:00, the truth's the next day's


def _setting(leak_a: float, balance: bool) -> dict:
    event = {"kind": "leak", "from_month": 13, "to_month": 24, "leak_a": leak_a}
    return {"overrides": {"event": [event], "bms.balance": balance}, "months": 36}


SETTINGS = {
    **{f"leak{ma}": _setting(ma / 1000.0, True) for ma in (0, 5, 15, 50)},
    **{f"leak{ma}-off": _setting(ma / 1000.0, False) for ma in (0, 5, 15, 50)},
}
RESULT = "leak.csv"
PASS_HOLD = sim_truth.PASS_RANGE
PASS_DETECT = {"leak15": 0.90, "leak50": 0.90, "leak15-off": 0.90, "leak50-off": 0.90}
PASS_CONTROL = 0.10


def true_loss_ma(rec: sim_truth.Recorder, excluded: int | None) -> pd.DataFrame:
    """Per month: ``t_end``, the true apparent loss (mA) and its hours."""
    b = common.month_end_hours(rec.balance)
    rows = []
    for t_end, g in b.groupby("t_end", sort=True):
        keep = g if excluded is None else g[g["group"] != excluded]
        hours = float(g["hours"].iloc[0])
        drain = float(g["supply_ah"].mean() + (keep["self_ah"] + keep["bleed_ah"]).mean())
        rows.append((t_end, 1000.0 * drain / hours, hours))
    return pd.DataFrame(rows, columns=["t_end", "loss_ma", "hours"])


def _memory_weighted(truth: pd.DataFrame, at: pd.Timestamp) -> float:
    use = truth[truth["t_end"] <= at + MONTH_END]
    if not len(use):
        return math.nan
    age = (at - (use["t_end"] - pd.to_timedelta(use["hours"] / 2.0, unit="h"))).dt.total_seconds() / 86400.0
    w = np.exp(-age.to_numpy(float) / MEMORY_DAYS) * use["hours"].to_numpy(float)
    return float((w * use["loss_ma"].to_numpy(float)).sum() / w.sum())


def run_pack(setting: str, seed: int) -> list[dict]:
    rec = common.recorder(CASE, SETTINGS, setting, seed)
    event = sim.events(rec.data, seed)[0]
    onset = rec.start + pd.Timedelta(days=int(event["day"]))
    planted = event["leak_ohm"] is not None
    reduced = sim_truth.reduce_pack(rec)
    cap = common.capacity_table(reduced, rec.spec, rec.start, rec.months)
    loss = charge_loss(cap, rec.spec)
    truth = true_loss_ma(rec, event["group"] if planted else None)
    n = {"before": 0, "after": 0}
    held = {"before": 0, "after": 0}
    held_memory = {"before": 0, "after": 0}
    for time, r in loss.iterrows():
        if r["status"] != sim_truth.OK or not (math.isfinite(r["charge_loss_ma_low"]) and math.isfinite(r["charge_loss_ma_high"])):
            continue
        part = "before" if time <= onset else ("after" if time >= onset + pd.Timedelta(days=MEMORY_DAYS) else None)
        if part is None:
            continue
        t = truth[truth["t_end"] <= time + MONTH_END]
        if not len(t):
            continue
        n[part] += 1
        held[part] += int(r["charge_loss_ma_low"] <= float(t["loss_ma"].iloc[-1]) <= r["charge_loss_ma_high"])
        held_memory[part] += int(r["charge_loss_ma_low"] <= _memory_weighted(truth, time) <= r["charge_loss_ma_high"])
    # T3b: the two dates
    ends = loss.index
    before = ends[ends <= onset]
    after = ends[ends >= onset + pd.Timedelta(days=MEMORY_DAYS)]
    detected, change_a1 = False, float("nan")
    if len(before) and len(after):
        b, a = loss.loc[before[-1]], loss.loc[after[0]]
        if b["status"] == sim_truth.OK and a["status"] == sim_truth.OK:
            detected = bool(a["charge_loss_ma_low"] > b["charge_loss_ma_high"])
        cb, ca = cap.loc[before[-1]], cap.loc[after[0]]
        if sim_truth.has_band(cb) and sim_truth.has_band(ca):
            change_a1 = float(ca["capacity_ah"] - cb["capacity_ah"])
    row = {
        **common.base_row(setting, seed),
        "onset_day": int(event["day"]), "leak_group": int(event["group"]), "leak_planted": bool(planted),
        "n_before": n["before"], "held_before": held["before"], "n_after": n["after"], "held_after": held["after"],
        "held_memory_before": held_memory["before"], "held_memory_after": held_memory["after"],
        "detected": bool(detected), "a1_change_ah": change_a1,
    }
    return [row]


def summary(table: pd.DataFrame) -> list[dict]:
    rows = []
    for setting, t in common.by_setting(table, SETTINGS):
        n = int(t["n_before"].sum() + t["n_after"].sum())
        h = int(t["held_before"].sum() + t["held_after"].sum())
        share = common.fraction(h, n)
        hm = int(t["held_memory_before"].sum() + t["held_memory_after"].sum())
        rows.append({
            "check": "T3a", "what": f"{setting}: the A4 band holds the true apparent charge loss, before the onset "
                                    "and from 180 days after it, month ends with a band pooled over packs",
            "result": f"{h} of {n} ({share:.1%}); before {int(t['held_before'].sum())} of {int(t['n_before'].sum())}, "
                      f"after {int(t['held_after'].sum())} of {int(t['n_after'].sum())}; against the memory-weighted "
                      f"truth (information) {hm} of {n} ({common.fraction(hm, n):.1%})",
            "pass_if": "information (T3c)" if setting.endswith("-off") else "85-95 % of month ends with a band",
            "passed": "" if setting.endswith("-off") else bool(n and PASS_HOLD[0] <= share <= PASS_HOLD[1])})
        d = int(t["detected"].sum())
        dshare = common.fraction(d, len(t))
        control = setting.startswith("leak0")
        if control:
            pass_if, ok = f"at most {PASS_CONTROL:.0%} of the controls", dshare <= PASS_CONTROL
        elif setting in PASS_DETECT:
            pass_if, ok = f"at least {PASS_DETECT[setting]:.0%} of packs", dshare >= PASS_DETECT[setting]
        else:
            pass_if, ok = "reported", ""
        if setting.endswith("-off"):
            pass_if, ok = "information (T3c)", ""
        a1 = t["a1_change_ah"].dropna()
        rows.append({
            "check": "T3b" if not setting.endswith("-off") else "T3c",
            "what": f"{setting}: A4 at the last month end before the onset and the first one 180 days after it: "
                    "the second band's low end above the first band's high end (not estimated = not detected)"
                    "; and the change of the A1 estimate over the same dates",
            "result": f"{d} of {len(t)} packs ({dshare:.1%}); A1 changes by a median of "
                      f"{a1.median() if len(a1) else float('nan'):+.2f} Ah over the same dates ({len(a1)} packs with both)",
            "pass_if": pass_if, "passed": ok})
    return rows
