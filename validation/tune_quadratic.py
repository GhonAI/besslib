"""Version 0.3: tune the minimum for the quadratic voltage terms on the development systems only.

Each quadratic voltage term of the capacity fit is used while its kind (full or empty) has
at least ``CapacityConfig.quad_min_rests`` rests in memory (decayed count); with fewer the
fit keeps the linear term alone. Every other setting keeps its
value. The form of the fit was chosen on the simulator's development packs, not here.

Grid and rule, fixed with the plan of version 0.3 before any estimate was compared with a
test (25 Nov 2026):

* ``quad_min_rests``: 2 (the minimum of the linear term), 5, 10, 20;
* rule: that of ``tune_capacity.py``, unchanged: the lowest median absolute error over the
  22 development tests among the settings whose 90 % band holds at least 80 % of them;
  if none does, the highest coverage. Ties: the lower median absolute error, then the
  smaller minimum (the term used more often). A test without an estimate counts as an
  infinite error and as not held.

Each test window is masked as in ``tune_capacity.py``. Reads ``data/reduced/rests/`` and
``validation/results/capacity_tests.csv``. Writes ``validation/results/tuning_quadratic.csv``
(one row per setting) and ``validation/results/tuning_quadratic_tests.csv`` (one row per
setting and test).

The plan named the count in two ways: the rests of a kind in memory (its sentence) and pairs
(its grid's unit). Both were run. Counted in rests no grid value changes a held test, and the
tie goes to 2. Counted in same-kind pairs 20 is selected (8 of 22 tests held against 7), and
on the simulator's development packs that switches the term of the full end off. The owner
chose the rests after seeing both (25 Nov 2026), so the gate is not a blind choice.

Usage:
    python validation/tune_quadratic.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from besslib.soh.config import CapacityConfig, config_hash

from reduce_events import rest_file
from tune_capacity import DEVELOPMENT, MIN_COVERAGE, _plain, estimate_at_tests, reference_tests, summarise

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
GRID = (2.0, 5.0, 10.0, 20.0)


def select(table: pd.DataFrame) -> pd.Series:
    """The fixed rule of ``tune_capacity.select``, with its ties broken towards the smaller minimum."""
    ok = table[table["coverage"] >= MIN_COVERAGE]
    if len(ok):
        return ok.sort_values(["median_abs_error_points", "quad_min_rests"]).iloc[0]
    return table.sort_values(["coverage", "median_abs_error_points", "quad_min_rests"], ascending=[False, True, True]).iloc[0]


def main() -> None:
    tests = reference_tests("development")
    assert sorted(tests["system_id"].unique()) == sorted(DEVELOPMENT), "development split changed"
    base = CapacityConfig()
    candidates = {sid: pd.read_parquet(rest_file(sid, base.current_c_rate)) for sid in DEVELOPMENT}
    summary, details = [], []
    for minimum in GRID:
        config = base.with_(quad_min_rests=minimum)
        per_test = pd.concat(
            [_plain(estimate_at_tests(candidates[sid], sid, tests, config)[0]) for sid in DEVELOPMENT], ignore_index=True
        )
        per_test.insert(0, "config_hash", config_hash(config))
        details.append(per_test.assign(quad_min_rests=minimum))
        row = {"config_hash": config_hash(config), "quad_min_rests": minimum}
        row.update(summarise(per_test))
        summary.append(row)
        print(
            f"quad_min_rests {minimum:>3.0f}: median |error| {row['median_abs_error_points']:.2f} pts, "
            f"coverage {row['coverage']:.0%}, estimated {row['n_estimated']}/{row['n_tests']}, "
            f"median band {row['median_band_points']:.2f} pts"
        )
    table = pd.DataFrame(summary)
    chosen = select(table)
    table["selected"] = table["config_hash"] == chosen["config_hash"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "tuning_quadratic.csv", index=False, float_format="%.4f")
    pd.concat(details, ignore_index=True).to_csv(RESULTS / "tuning_quadratic_tests.csv", index=False, float_format="%.4f")
    print(
        f"selected: quad_min_rests {chosen['quad_min_rests']:.0f} (hash {chosen['config_hash']}): median |error| "
        f"{chosen['median_abs_error_points']:.2f} pts, coverage {chosen['coverage']:.0%}"
    )


if __name__ == "__main__":
    main()
