"""Phase 2: tune the capacity estimator on the development systems only.

The grid (27 settings) and the selection rule were fixed before any estimate was
compared with a test:

* current threshold: C/200, C/100, C/50;
* minimum rest duration: 10, 20, 40 minutes;
* memory time: 30, 90, 180 days.

Rule: the lowest median absolute error over the 22 development tests among the settings
whose 90 % band holds at least 80 % of them; if none does, the highest coverage. A test
without an estimate counts as an infinite error and as not held.

Each test window (from one day before the test begins to one day after it ends) is
masked: its rests are removed before the rests are classified, and no pair spans it. The estimate at a test uses only rests before it.

Reads ``data/reduced/rests/`` (from ``reduce_events.py``) and
``validation/results/capacity_tests.csv``. Writes ``validation/results/tuning_development.csv``
(one row per setting) and ``validation/results/tuning_development_tests.csv`` (one row
per setting and test).

Usage:
    python validation/tune_capacity.py
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.capacity import estimate_capacity
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import classify_rests, rest_chain, rest_pairs

from reduce_events import CURRENT_GRID, DURATION_GRID, rest_file

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
TAU_GRID = (30.0, 90.0, 180.0)
DEVELOPMENT = (1, 4, 7, 11, 14, 17, 19)
MIN_COVERAGE = 0.80


def reference_tests(set_name: str) -> pd.DataFrame:
    tests = pd.read_csv(RESULTS / "capacity_tests.csv", parse_dates=["onset", "end", "documented_start"])
    tests = tests[tests["set"] == set_name].copy()
    tests["test_id"] = tests["system_id"].map("{:02d}".format) + "/" + tests["test"].astype(str)
    return tests


def masks_for(tests: pd.DataFrame, system_id: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """One day before the beginning to one day after the end of every test of the system."""
    day = pd.Timedelta(days=1)
    rows = tests[tests["system_id"] == system_id]
    out = []
    for onset, end, start in zip(rows["onset"], rows["end"], rows["documented_start"]):
        begin = onset if pd.notna(onset) else start
        finish = end if pd.notna(end) else start + pd.Timedelta(hours=12)
        out.append((begin - day, finish + day))
    return out


def estimate_at_tests(candidates: pd.DataFrame, system_id: int, tests: pd.DataFrame, config: CapacityConfig):
    """Estimates at every test of one system, from masked operating data only; also the pairs."""
    spec = figgener.pack_spec(system_id)
    mask = masks_for(tests, system_id)
    cands = candidates[candidates["duration_min"] >= config.min_duration_min]
    inside = np.zeros(len(cands), dtype=bool)
    for a, b in mask:
        inside |= ((cands["start"] >= a) & (cands["start"] <= b)).to_numpy()
    rests = classify_rests(cands[~inside], spec, config)
    pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    rows = tests[tests["system_id"] == system_id]
    at = [m[0] for m in mask]  # the estimate as of the start of each masked window
    est = estimate_capacity(None, spec, config=config, at=at, pairs=pairs)
    out = rows[["test_id", "system_id", "chemistry", "capacity_ah", "test_c_rate", "status"]].rename(
        columns={"capacity_ah": "test_capacity_ah", "status": "test_status"}
    )
    out = out.reset_index(drop=True)
    est = est.reset_index()
    for col in ("time", "capacity_ah", "capacity_ah_low", "capacity_ah_high", "offset_current_a", "n_pairs", "n_full_empty", "n_same_kind", "birge", "rho", "status"):
        out[col] = est[col].to_numpy()
    nominal = spec.nominal_capacity_ah
    out["error_points"] = (out["capacity_ah"] - out["test_capacity_ah"]) / nominal * 100.0
    out["band_points"] = (out["capacity_ah_high"] - out["capacity_ah_low"]) / nominal * 100.0
    out["held"] = (out["capacity_ah_low"] <= out["test_capacity_ah"]) & (out["test_capacity_ah"] <= out["capacity_ah_high"])
    return out, pairs


def summarise(per_test: pd.DataFrame) -> dict:
    err = per_test["error_points"].abs().fillna(np.inf)
    return {
        "n_tests": len(per_test),
        "n_estimated": int(per_test["capacity_ah"].notna().sum()),
        "median_abs_error_points": float(np.median(err)),
        "coverage": float(per_test["held"].mean()),
        "median_band_points": float(per_test["band_points"].median()),
        "median_error_points": float(per_test["error_points"].median()),
    }


def select(table: pd.DataFrame) -> pd.Series:
    """The fixed rule: lowest median absolute error among settings with coverage >= 80 %."""
    ok = table[table["coverage"] >= MIN_COVERAGE]
    if len(ok):
        return ok.sort_values(["median_abs_error_points", "coverage"], ascending=[True, False]).iloc[0]
    return table.sort_values(["coverage", "median_abs_error_points"], ascending=[False, True]).iloc[0]


def main() -> None:
    tests = reference_tests("development")
    assert sorted(tests["system_id"].unique()) == sorted(DEVELOPMENT), "development split changed"
    candidates = {
        (sid, c): pd.read_parquet(rest_file(sid, c)) for sid in DEVELOPMENT for c in CURRENT_GRID
    }
    summary, details = [], []
    for c, d, tau in itertools.product(CURRENT_GRID, DURATION_GRID, TAU_GRID):
        config = CapacityConfig(current_c_rate=c, min_duration_min=d, tau_days=tau)
        per_test = pd.concat(
            [estimate_at_tests(candidates[(sid, c)], sid, tests, config)[0] for sid in DEVELOPMENT],
            ignore_index=True,
        )
        per_test.insert(0, "config_hash", config_hash(config))
        details.append(per_test.assign(current_c_rate=c, min_duration_min=d, tau_days=tau))
        row = {"config_hash": config_hash(config), "current_c_rate": c, "min_duration_min": d, "tau_days": tau}
        row.update(summarise(per_test))
        summary.append(row)
        print(
            f"C/{1 / c:.0f} {d:>4.0f} min {tau:>4.0f} d: median |error| {row['median_abs_error_points']:.2f} pts, "
            f"coverage {row['coverage']:.0%}, estimated {row['n_estimated']}/{row['n_tests']}"
        )
    table = pd.DataFrame(summary)
    chosen = select(table)
    table["selected"] = table["config_hash"] == chosen["config_hash"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "tuning_development.csv", index=False, float_format="%.4f")
    pd.concat(details, ignore_index=True).to_csv(RESULTS / "tuning_development_tests.csv", index=False, float_format="%.4f")
    print(
        f"selected: C/{1 / chosen['current_c_rate']:.0f}, {chosen['min_duration_min']:.0f} min, "
        f"{chosen['tau_days']:.0f} days (hash {chosen['config_hash']}): median |error| "
        f"{chosen['median_abs_error_points']:.2f} pts, coverage {chosen['coverage']:.0%}"
    )


if __name__ == "__main__":
    main()
