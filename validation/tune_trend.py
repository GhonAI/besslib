"""Version 0.2: tune the minimum for the trend term on the development systems only.

The capacity fit with forgetting carries a linear trend in the capacity only while the
decayed count of full-to-empty and empty-to-full pairs in memory is at least
``CapacityConfig.trend_min_cross``; with fewer the trend is left out of the solve, and
the estimate is the fit without it. Every other setting keeps its frozen value.

Grid and rule, fixed before any estimate was compared with a test (23 Nov 2026). The grid
was set from the decayed pair counts at the 22 development test dates (0 to 92), not from
any error:

* ``trend_min_cross``: 0 (always, the first 0.2 build), 5, 10, 15, 20, 30, 50;
* rule: that of ``tune_capacity.py``, unchanged: the lowest median absolute error over the
  22 development tests among the settings whose 90 % band holds at least 80 % of them;
  if none does, the highest coverage. Ties: the lower median absolute error, then the
  smaller minimum (the trend fitted more often). A test without an estimate counts as an
  infinite error and as not held.

Each test window is masked as in ``tune_capacity.py``. Reads ``data/reduced/rests/`` and
``validation/results/capacity_tests.csv``. Writes ``validation/results/tuning_trend.csv``
(one row per setting) and ``validation/results/tuning_trend_tests.csv`` (one row per
setting and test).

Usage:
    python validation/tune_trend.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from besslib.soh.config import CapacityConfig, config_hash

from reduce_events import rest_file
from tune_capacity import DEVELOPMENT, estimate_at_tests, reference_tests, summarise

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
GRID = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0)
MIN_COVERAGE = 0.80


def _plain(frame: pd.DataFrame) -> pd.DataFrame:
    """The table without its ``attrs`` (the rest table there would make ``pd.concat`` compare frames)."""
    frame.attrs = {}
    return frame


def select(table: pd.DataFrame) -> pd.Series:
    """The fixed rule of ``tune_capacity.select``, with its ties broken towards the smaller minimum."""
    ok = table[table["coverage"] >= MIN_COVERAGE]
    if len(ok):
        return ok.sort_values(["median_abs_error_points", "trend_min_cross"]).iloc[0]
    return table.sort_values(
        ["coverage", "median_abs_error_points", "trend_min_cross"], ascending=[False, True, True]
    ).iloc[0]


def main() -> None:
    tests = reference_tests("development")
    assert sorted(tests["system_id"].unique()) == sorted(DEVELOPMENT), "development split changed"
    base = CapacityConfig()
    candidates = {sid: pd.read_parquet(rest_file(sid, base.current_c_rate)) for sid in DEVELOPMENT}
    summary, details = [], []
    for minimum in GRID:
        config = base.with_(trend_min_cross=minimum)
        per_test = pd.concat(
            [_plain(estimate_at_tests(candidates[sid], sid, tests, config)[0]) for sid in DEVELOPMENT], ignore_index=True
        )
        per_test.insert(0, "config_hash", config_hash(config))
        details.append(per_test.assign(trend_min_cross=minimum))
        row = {"config_hash": config_hash(config), "trend_min_cross": minimum}
        row.update(summarise(per_test))
        summary.append(row)
        print(
            f"trend_min_cross {minimum:>4.0f}: median |error| {row['median_abs_error_points']:.2f} pts, "
            f"coverage {row['coverage']:.0%}, estimated {row['n_estimated']}/{row['n_tests']}, "
            f"median band {row['median_band_points']:.2f} pts"
        )
    table = pd.DataFrame(summary)
    chosen = select(table)
    table["selected"] = table["config_hash"] == chosen["config_hash"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "tuning_trend.csv", index=False, float_format="%.4f")
    pd.concat(details, ignore_index=True).to_csv(RESULTS / "tuning_trend_tests.csv", index=False, float_format="%.4f")
    print(
        f"selected: trend_min_cross {chosen['trend_min_cross']:.0f} (hash {chosen['config_hash']}): median |error| "
        f"{chosen['median_abs_error_points']:.2f} pts, coverage {chosen['coverage']:.0%}"
    )


if __name__ == "__main__":
    main()
