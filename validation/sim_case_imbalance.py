"""Case 2, cell imbalance: A1 against T_rest (T2a) and against T_stop (T2b), and the capacity lost to imbalance.

The pack: 14 groups of the NMC cell scaled to 100 Ah, capacity spread sd 1 % or 3 %, a spread of
self-discharge (mean 2 mA, sd 0.5 mA per group), balancing on or off (bleed 20 ohm above 4.0 V when the spread
of the group voltages exceeds 10 mV); settings ``sd1-balance``, ``sd1-off``, ``sd3-balance``, ``sd3-off``.

* T2a: at every month end the estimate has a band, the band holds ``T_rest`` (``sim_truth``: the charge
  between the two relaxed rest voltages the 0.2 estimate reads at, on the groups' states at that date). Per
  pack: month ends with a band and a reference (a band without ``ref_full_v`` or ``ref_empty_v`` has no T_rest: left out, counted in ``n_no_reference``), those that hold, the mean error (estimate minus truth, Ah). Pass, per setting
  (``sim_truth.coverage_row``): 85-95 % of month ends with a band hold, and the mean of the packs' mean errors
  within 2 sd / sqrt(n) of zero.
* T2b (information): the same against ``T_stop``, the charge between the BMS stops of the 60 days before.
* Information, per pack, **usable capacity lost to imbalance**: for each pair of consecutive stops of different
  kinds (full to empty or empty to full), ``t`` is the terminal charge between them (the BMS's supply current
  added back), ``c_min`` the smallest group capacity at the pair's middle, and ``window`` the difference of the
  true states of charge of the groups that limited the two stops. The loss is
  ``1 - mean(t) / mean(c_min * window)``: what the string gives between its stops, against what its smallest
  group would give if every group stood where the limiting groups stand at the stops (aligned). Also the number
  of full and empty stops and how many were limited by the group with the smallest capacity.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_case_common as common
import sim_truth

CASE = "cell-imbalance"
SETTINGS = {
    "sd1-balance": {"overrides": {"pack.capacity_spread_sd": 0.01, "bms.balance": True}, "months": 36},
    "sd1-off": {"overrides": {"pack.capacity_spread_sd": 0.01, "bms.balance": False}, "months": 36},
    "sd3-balance": {"overrides": {"pack.capacity_spread_sd": 0.03, "bms.balance": True}, "months": 36},
    "sd3-off": {"overrides": {"pack.capacity_spread_sd": 0.03, "bms.balance": False}, "months": 36},
}
RESULT = "imbalance.csv"


def lost_to_imbalance(rec: sim_truth.Recorder) -> dict:
    """The loss defined in the module's docstring, and the stops' limiting groups."""
    s = rec.stops
    s = s[s["first"]] if len(s) else s
    weakest = int(np.argmin(rec.pack.capacity0()))
    out = {"n_full_stops": int((rec.stops["kind"] == "full").sum()) if len(rec.stops) else 0,
           "n_empty_stops": int((rec.stops["kind"] == "empty").sum()) if len(rec.stops) else 0}
    for kind in ("full", "empty"):
        sel = rec.stops[rec.stops["kind"] == kind] if len(rec.stops) else rec.stops
        out[f"{kind}_limited_by_weakest"] = int((sel["group"] == weakest).sum()) if len(sel) else 0
        out[f"{kind}_groups_limiting"] = int(sel["group"].nunique()) if len(sel) else 0
    if len(s) < 2:
        return {**out, "imbalance_loss": float("nan"), "n_stop_pairs": 0}
    sec, q, soc = s["seconds"].to_numpy(float), s["q_ah"].to_numpy(float), s["soc"].to_numpy(float)
    t = np.abs(np.diff(q) - rec.pack.bms.supply_a * np.diff(sec) / 3600.0)
    window = np.abs(np.diff(soc))
    c_min = np.array([rec._capacity(x).min() for x in 0.5 * (sec[1:] + sec[:-1])])
    return {**out, "imbalance_loss": float(1.0 - t.mean() / (c_min * window).mean()), "n_stop_pairs": int(len(t))}


def run_pack(setting: str, seed: int) -> list[dict]:
    rec = common.recorder(CASE, SETTINGS, setting, seed)
    reduced = sim_truth.reduce_pack(rec)
    cap = common.capacity_table(reduced, rec.spec, rec.start, rec.months)
    n = held = held_stop = n_stop = n_no_reference = 0
    err, err_stop = [], []
    for time, r in cap.iterrows():
        if not sim_truth.has_band(r):
            continue
        t_rest = math.nan
        if math.isfinite(r["ref_full_v"]) and math.isfinite(r["ref_empty_v"]):
            t_rest = rec.t_rest(time, r["ref_full_v"], r["ref_empty_v"])
        if np.isfinite(t_rest):  # a month end without a reference has no T_rest: left out and counted
            n += 1
            held += int(r["capacity_ah_low"] <= t_rest <= r["capacity_ah_high"])
            err.append(r["capacity_ah"] - t_rest)
        else:
            n_no_reference += 1
        t_stop = rec.t_stop(time)
        if np.isfinite(t_stop):
            n_stop += 1
            held_stop += int(r["capacity_ah_low"] <= t_stop <= r["capacity_ah_high"])
            err_stop.append(r["capacity_ah"] - t_stop)
    row = {
        **common.base_row(setting, seed),
        **common.estimated_counts(reduced, rec.spec, rec.start, rec.months),
        "month_ends": int(len(cap)), "with_band": n, "n_no_reference": n_no_reference, "held_rest": held,
        "mean_error_rest_ah": float(np.mean(err)) if err else float("nan"),
        "with_stop_truth": n_stop, "held_stop": held_stop,
        "mean_error_stop_ah": float(np.mean(err_stop)) if err_stop else float("nan"),
        **lost_to_imbalance(rec),
    }
    return [row]


def _coverage(check: str, what: str, t: pd.DataFrame, held: str, n: str, err: str, unit: str) -> dict:
    return sim_truth.coverage_row(check, what, int(t[held].sum()), int(t[n].sum()), t[err].to_numpy(float), unit)


def summary(table: pd.DataFrame) -> list[dict]:
    rows = []
    for setting, t in common.by_setting(table, SETTINGS):
        r = _coverage("T2a", f"{setting}: the A1 band holds T_rest at the month end",
                      t, "held_rest", "with_band", "mean_error_rest_ah", "Ah")
        r["result"] += f"; {int(t['n_no_reference'].sum())} month ends with a band left out (no reference voltages)"
        rows.append(r)
        r = _coverage("T2b", f"{setting}: the same against T_stop (information)", t, "held_stop", "with_stop_truth",
                      "mean_error_stop_ah", "Ah")
        r.update(pass_if="information", passed="")
        rows.append(r)
        rows.append(common.r_row(setting, t, judged=setting.endswith("-balance")))
        n = len(t)
        rows.append(common.info_row(
            "T2c", f"{setting}: usable capacity lost to imbalance, and the groups that limit the stops",
            f"loss median {t['imbalance_loss'].median():.2%} (packs {t['imbalance_loss'].notna().sum()}); "
            f"full stops {int(t['n_full_stops'].sum())}, of them limited by the smallest group "
            f"{int(t['full_limited_by_weakest'].sum())}; empty stops {int(t['n_empty_stops'].sum())}, "
            f"of them {int(t['empty_limited_by_weakest'].sum())}; groups limiting a full stop per pack, median "
            f"{t['full_groups_limiting'].median():.0f}, an empty stop {t['empty_groups_limiting'].median():.0f} ({n} packs)"))
    return rows
