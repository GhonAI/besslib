"""Case 5, grid duty at mid state of charge: no estimate without a calibration (T5a), coverage (T5b), the spread's cost (T5f).

Two case files: ``grid-duty`` (NMC, 14 cells of 100 Ah in series; the ``lfp-*`` settings: LFP, 16 cells of 280 Ah; identical cells)
and ``grid-string`` (396 LFP cells of 280 Ah in series, capacity sd 1 %, balancing on, sensors at the stated accuracy
of a utility string, a temperature offset drawn per string). Random power around mid state of charge within 30-70 %,
one arbitrage cycle a day, a calibration full charge every 7 days, every 30 days or never (settings ``*-7``,
``*-30``, ``*-never``). No BMS supply and no self-discharge, so the BMS's counter is exact between calibrations
(a simplification, said in the case files). 36 months, 10 s steps.

* T5a: in the ``never`` settings, ``estimate_capacity`` at every month end gives 0 estimates; each month says
  ``not possible from this data`` with its reason. Per pack: month ends, estimated month ends, the reasons with
  their month counts. Pass, per chemistry and string: 200 of 200 packs without an estimate and with a reason.
* T5b (information): in the other settings, coverage and error of the A1 band against ``T_rest``
  (``sim_truth``), as case 2; a band without reference voltages has no T_rest and is left out (counted in ``n_no_reference``).
* T5f (information, from the truth, no besslib): the usable capacity lost to the cell spread,
  ``1 - (charge between the BMS stops of the string) / (charge of the mean cell between the same states)``.
  Quasi-static (columns ``spread_loss_*``): every group aligned at full, discharged until the group with the
  smallest capacity reaches empty, so the string gives ``c_min`` and the mean cell ``mean(c)`` over the same window:
  ``1 - c_min / mean(c)``; for the pack's own groups and for a 14-group pack with the same spread and seed (the
  first 14 draws of the same stream). From the run (columns ``stops_loss_*``): consecutive full and empty stops,
  ``1 - mean(t) / mean(mean(c) * window)`` with ``t`` the terminal charge between two stops and ``window`` the
  difference of the true states of charge of the groups that limited them; the mid-state duty reaches no empty stop,
  so it exists only where both kinds of stop occur.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_case_common as common
import sim_truth

from besslib import sim

CASE = "grid-duty"
CALIBRATION = {"7": 7, "30": 30, "never": 0}
_LFP = {"cell.preset": "lfp", "cell.scale_to_ah": 280.0, "pack.series": 16, "duty.capacity_ah": 280.0,
        "bms.v_cell_max": 3.60, "bms.v_cell_min": 2.80, "case.chemistry": "LFP", "case.nominal_cell_v": 3.2}
SETTINGS = {
    **{f"nmc-{k}": {"overrides": {"duty.calibration_days": d}, "months": 36} for k, d in CALIBRATION.items()},
    **{f"lfp-{k}": {"overrides": {**_LFP, "duty.calibration_days": d}, "months": 36} for k, d in CALIBRATION.items()},
    **{f"string-{k}": {"case": "grid-string", "overrides": {"duty.calibration_days": d}, "months": 36}
       for k, d in CALIBRATION.items()},
}
RESULT = "grid.csv"


def spread_loss(pack) -> float:
    """``1 - c_min / mean(c)``: the quasi-static loss of aligned groups (module docstring)."""
    c = pack.capacity0()
    return float(1.0 - c.min() / c.mean())


def run_loss(rec: sim_truth.Recorder) -> float:
    """The loss from the run's own stops (module docstring); NaN without a full and an empty stop in turn."""
    s = rec.stops
    s = s[s["first"]] if len(s) else s
    if len(s) < 2:
        return math.nan
    sec, q, soc = s["seconds"].to_numpy(float), s["q_ah"].to_numpy(float), s["soc"].to_numpy(float)
    t = np.abs(np.diff(q) - rec.pack.bms.supply_a * np.diff(sec) / 3600.0)
    window = np.abs(np.diff(soc))
    mean_c = np.array([rec._capacity(x).mean() for x in 0.5 * (sec[1:] + sec[:-1])])
    return float(1.0 - t.mean() / (mean_c * window).mean())


def estimate_share(rec: sim_truth.Recorder, estimated: pd.DataFrame) -> float:
    """Information: the median, over the estimated month ends, of the estimate over the mean true capacity
    (state of charge 0 to 1) of the groups at that date; NaN without an estimate."""
    if not len(estimated):
        return math.nan
    ratio = [float(r["capacity_ah"]) / float(rec.cells_at(time)[1].mean()) for time, r in estimated.iterrows()]
    return float(np.median(ratio))


def _reasons(cap: pd.DataFrame) -> str:
    counts = cap.loc[cap["status"] != sim_truth.OK, "status"].value_counts()
    return " | ".join(f"{k} x{v}" for k, v in counts.items())


def run_pack(setting: str, seed: int) -> list[dict]:
    rec = common.recorder(CASE, SETTINGS, setting, seed)
    reduced = sim_truth.reduce_pack(rec)
    cap = common.capacity_table(reduced, rec.spec, rec.start, rec.months)
    estimated = cap[cap["status"] == sim_truth.OK]
    n = held = n_no_reference = 0
    err = []
    for time, r in cap.iterrows():
        if not sim_truth.has_band(r):
            continue
        t_rest = math.nan
        if math.isfinite(r["ref_full_v"]) and math.isfinite(r["ref_empty_v"]):
            t_rest = rec.t_rest(time, r["ref_full_v"], r["ref_empty_v"])
        if not np.isfinite(t_rest):  # no reference voltages, no T_rest: left out and counted
            n_no_reference += 1
            continue
        n += 1
        held += int(r["capacity_ah_low"] <= t_rest <= r["capacity_ah_high"])
        err.append(r["capacity_ah"] - t_rest)
    sd = float(rec.data["pack"].get("capacity_spread_sd", 0.0))
    small = sim.build(sim.load_case(rec.data, {"pack.groups": 14}), seed)[0] if sd > 0 else None
    refusing = cap[cap["status"] != sim_truth.OK]
    row = {
        **common.base_row(setting, seed),
        "month_ends": int(len(cap)), "estimated_months": int(len(estimated)),
        "all_refusals_say_not_possible": bool(refusing["status"].str.startswith("not possible from this data").all()),
        "reasons": _reasons(cap),
        "with_band": n, "n_no_reference": n_no_reference, "held_rest": held, "mean_error_rest_ah": float(np.mean(err)) if err else math.nan,
        "spread_loss_pack": spread_loss(rec.pack), "spread_loss_14_groups": spread_loss(small) if small else math.nan,
        "stops_loss": run_loss(rec),
        "estimate_over_true_capacity": estimate_share(rec, estimated),
        "n_full_stops": int((rec.stops["kind"] == "full").sum()) if len(rec.stops) else 0,
        "n_empty_stops": int((rec.stops["kind"] == "empty").sum()) if len(rec.stops) else 0,
    }
    return [row]


def summary(table: pd.DataFrame) -> list[dict]:
    rows = []
    for setting, t in common.by_setting(table, SETTINGS):
        n = len(t)
        if setting.endswith("-never"):
            ok = int(((t["estimated_months"] == 0) & t["all_refusals_say_not_possible"]).sum())
            reasons = pd.Series(" | ".join(t["reasons"].fillna("")).split(" | ")).str.rsplit(" x", n=1, expand=True)
            reasons = reasons.dropna()
            reasons[1] = reasons[1].astype(int)
            top = reasons.groupby(0)[1].sum().sort_values(ascending=False).head(3)
            share = top / max(int(t["month_ends"].sum()), 1)
            rows.append({
                "check": "T5a", "what": f"{setting}: no calibration charge: month ends with an A1 estimate, and the reason "
                                        "of every month that has none",
                "result": f"{ok} of {n} packs with 0 estimated month ends and every reason 'not possible from this data'; "
                          f"{int(t['estimated_months'].sum())} estimated of {int(t['month_ends'].sum())} month ends; "
                          f"the estimate is a median {t['estimate_over_true_capacity'].median():.1%} of the true capacity; "
                          "reasons: " + "; ".join(f"{k} ({v:.1%} of month ends)" for k, v in share.items()),
                "pass_if": "200 of 200 packs", "passed": bool(ok == n and n > 0)})
        else:
            r = sim_truth.coverage_row("T5b", f"{setting}: the A1 band holds T_rest at month ends with a band (information)",
                                       int(t["held_rest"].sum()), int(t["with_band"].sum()),
                                       t["mean_error_rest_ah"].to_numpy(float), "Ah")
            r.update(pass_if="information", passed="")
            r["result"] += f"; {int(t['n_no_reference'].sum())} left out (no reference voltages); {int(t['estimated_months'].sum())} of {int(t['month_ends'].sum())} month ends estimated"
            rows.append(r)
        if setting.startswith("string-") and setting.endswith("-7"):
            rows.append(common.info_row(
                "T5f", "usable capacity lost to the cell spread: 1 - c_min / mean(c) of aligned groups (quasi-static), "
                       "for the 396-group string and for a 14-group pack with the same spread and seed; from the run's "
                       "own stops where full and empty stops follow each other",
                f"string {t['spread_loss_pack'].median():.2%}, 14 groups {t['spread_loss_14_groups'].median():.2%} (medians "
                f"over {n} packs); from the run's stops: {int(t['stops_loss'].notna().sum())} of {n} packs have a loss "
                f"({t['stops_loss'].median():.2%}); empty stops in the run: {int(t['n_empty_stops'].sum())}"))
    return rows
