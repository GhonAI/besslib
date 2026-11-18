"""Tune the current-step filter of the 1-second resistance on the development systems.

Grid: ``min_step_c_rate`` 0.05, 0.10, 0.20 (C/20, C/10, C/5) x ``steady_s`` 2, 5, 10 s.
For each setting and each development system (1, 4, 7, 11, 14, 17, 19) the script reads
the reduced step and daily tables (``data/reduced/phase3/ID_XX_{steps,daily}.parquet``),
drops the steps inside the capacity-test masks (one day before to one day after each
test, ``tune_capacity.masks_for``; only the test times are read, never a capacity value),
and forms the daily values of the headline band (22.5 to 27.5 degrees C):
``r_d = sum dI dV / sum dI^2`` over that day's qualifying steps
(``resistance.day_band_events``, the estimator's own events). A band-day on which more
than ``RecordConfig.unrefreshed_max_share`` of the qualifying steps show no voltage change
(the voltage was not refreshed with the current) gives no daily value, as in the
estimator; those days are counted (``n_days_unrefreshed``).

Two figures per system:

* robust scatter: ``1.4826 x MAD(r_d - m_d) / median(r_d)``, with ``m_d`` the centred
  91-day running median of ``r_d`` (calendar days). The running median takes out slow
  changes (ageing, seasons), so the figure measures the day-to-day noise of the value
  a setting gives; 1.4826 x MAD is the standard deviation for normal scatter. A system
  with fewer than 3 daily values, or whose median ``r_d`` is at or below zero, gets an
  infinite scatter (no usable value); the median over the systems still exists;
* share of days with a value: days with an ``r_d`` among the days with data in the band.
  A day has data in the band when the daily table holds measured seconds (any mode) in
  the 1-degree bins 22, 23, ..., 27 (22 <= T < 28 degrees C): the bins that overlap the
  band, at the table's 1-degree resolution. Masked days and days left out by the
  voltage-refresh rule stay in the count.

Rule, fixed before any result: the setting with the lowest median robust scatter over
the 7 systems, among the settings whose median share over the 7 systems is at least
0.5; if none reaches 0.5, the setting with the highest median share.

Writes ``validation/results/tuning_resistance.csv`` (one row per setting: the medians,
the per-system values and ``selected``) and prints the selected setting. The setting is
frozen in ``RecordConfig`` by hand afterwards, with a pointer to this script.

Usage:
    python validation/tune_resistance.py           # all 7 development systems
    python validation/tune_resistance.py 1 4       # some systems (a trial; the rule needs all 7)
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.reduce import TEMP_BIN_LOW_C
from besslib.soh.resistance import day_band_events, qualifying_steps

from run_capacity import all_tests
from tune_capacity import DEVELOPMENT, masks_for

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "data" / "reduced" / "phase3"
RESULTS = ROOT / "validation" / "results"
STEP_C_RATES = (0.05, 0.10, 0.20)
STEADY_S = (2.0, 5.0, 10.0)
TEMP_REF_C = 25.0
BAND_BINS_C = range(22, 28)  # lower edges of the 1-degree bins that overlap 22.5 <= T < 27.5
WINDOW = "91D"
MIN_SHARE = 0.5
MAD_TO_SD = 1.4826


def load(system_id: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The step and daily tables of one development system."""
    if system_id not in DEVELOPMENT:
        raise ValueError(f"system {system_id} is not a development system")
    name = f"ID_{system_id:02d}"
    return pd.read_parquet(TABLES / f"{name}_steps.parquet"), pd.read_parquet(TABLES / f"{name}_daily.parquet")


def days_with_data(daily: pd.DataFrame) -> pd.DatetimeIndex:
    """Days with measured seconds in the 1-degree bins that overlap the headline band."""
    names = [f"s_{mode}_{t - TEMP_BIN_LOW_C:02d}" for mode in ("charge", "discharge", "idle") for t in BAND_BINS_C]
    seconds = daily[names].sum(axis=1).to_numpy(float)
    return pd.DatetimeIndex(daily.loc[seconds > 0, "day"])


def robust_scatter(r: pd.Series) -> float:
    """``1.4826 x MAD(r - centred 91-day running median) / median(r)``; ``r`` indexed by day."""
    if len(r) < 3:
        return float("inf")  # too few days for a scatter: no usable value
    level = r.rolling(WINDOW, center=True, min_periods=1).median()
    dev = (r - level).to_numpy(float)
    mad = float(np.median(np.abs(dev - np.median(dev))))
    centre = float(np.median(r.to_numpy(float)))
    # A median at or below zero gives no relative scatter: the setting gives no usable value there.
    return MAD_TO_SD * mad / centre if centre > 0 else float("inf")


def system_figures(system_id: int, steps, daily, tests, config: RecordConfig) -> dict:
    spec = figgener.pack_spec(system_id)
    events, counts = day_band_events(steps, spec, config, TEMP_REF_C, masks_for(tests, system_id))
    head = events[events["temp_band_c"] == TEMP_REF_C]
    r = pd.Series(head["resistance_ohm"].to_numpy(float), index=pd.DatetimeIndex(head["day"])).sort_index()
    with_data = days_with_data(daily)
    return {
        "scatter": robust_scatter(r),
        "share": len(r.index.intersection(with_data)) / len(with_data) if len(with_data) else float("nan"),
        "n_days": int(len(r)),
        "n_days_data": int(len(with_data)),
        "median_ohm": float(r.median()) if len(r) else float("nan"),
        "n_steps": counts["n_steps"],
        "n_days_unrefreshed": int((counts["dropped"]["temp_band_c"] == TEMP_REF_C).sum()),
        "share_steps_no_voltage_change": float((qualifying_steps(steps, spec, config, masks_for(tests, system_id))
                                                ["d_voltage_v"] == 0.0).mean()),
    }


def select(table: pd.DataFrame) -> int:
    """The row the rule picks (module docstring)."""
    eligible = table[table["median_share"] >= MIN_SHARE]
    if len(eligible):
        return int(eligible["median_scatter"].idxmin())
    return int(table["median_share"].idxmax())


def main(argv: list[str]) -> None:
    systems = [int(a) for a in argv] if argv else list(DEVELOPMENT)
    tests = all_tests()
    data = {i: load(i) for i in systems}
    rows = []
    for rate, steady in itertools.product(STEP_C_RATES, STEADY_S):
        config = RecordConfig().with_(min_step_c_rate=rate, steady_s=steady)
        row = {"min_step_c_rate": rate, "steady_s": steady}
        figures = {i: system_figures(i, *data[i], tests, config) for i in systems}
        row["median_scatter"] = float(np.median([f["scatter"] for f in figures.values()]))
        row["median_share"] = float(np.nanmedian([f["share"] for f in figures.values()]))
        for i, f in figures.items():
            for key, value in f.items():
                row[f"{key}_ID_{i:02d}"] = value
        row["record_config_hash"] = config_hash(config)
        rows.append(row)
        print(f"C-rate {rate:.2f}, steady {steady:g} s: median scatter {row['median_scatter']:.4f}, "
              f"median share {row['median_share']:.3f}", flush=True)
    table = pd.DataFrame(rows)
    table["selected"] = False
    table.loc[select(table), "selected"] = True
    table["systems"] = " ".join(f"{i:02d}" for i in systems)
    table["capacity_config_hash"] = config_hash(CapacityConfig())
    best = table[table["selected"]].iloc[0]
    if sorted(systems) == sorted(DEVELOPMENT):
        RESULTS.mkdir(parents=True, exist_ok=True)
        table.to_csv(RESULTS / "tuning_resistance.csv", index=False, float_format="%.6g")
        print(f"wrote {RESULTS / 'tuning_resistance.csv'}")
    else:
        print("a trial on some systems: nothing written (the rule needs all 7 development systems)")
    print(f"selected: min_step_c_rate {best['min_step_c_rate']:g}, steady_s {best['steady_s']:g} "
          f"(median scatter {best['median_scatter']:.4f}, median share {best['median_share']:.3f})")


if __name__ == "__main__":
    main(sys.argv[1:])
