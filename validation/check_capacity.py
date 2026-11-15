"""Phase 2: the checks V2-V8, from the frozen run's result files.

Rules fixed before the held-out run:

* A test without an estimate counts as an infinite error and as not held (as in tuning).
* V2 and V4 use all 28 held-out LMO/NMC tests. Tests 03/3 and 08/1 (more than 1 % of
  their capacity discharged in the hour before) are flagged, with an information line
  without them.
* V5 compares the mean fade rate of the held-out systems of each chemistry with the
  paper's (3.1, 1.9 and 2.0 points per year of decrease for LMO/NMC, NMC and LFP); all 21
  systems are an information line.
* V7 runs the step-by-step core against the batch path at every day of three
  development test months.

Writes ``validation/results/checks_phase2.csv`` and ``validation/results/phase2_tests.csv``.

Usage:
    python validation/check_capacity.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.capacity import _solve, _z, capacity_state_at, days, run_core
from besslib.soh.config import CapacityConfig, config_hash

from tune_capacity import DEVELOPMENT, estimate_at_tests, reference_tests
from reduce_events import rest_file

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
PHASE2 = RESULTS / "phase2"
FLAGGED = ("03/3", "08/1")
PAPER_FADE = {"LMO/NMC": -3.1, "NMC": -1.9, "LFP": -2.0}  # points per year, algorithm (Figgener et al. 2024)
V7_TESTS = ("01/1", "07/3", "17/1")


def load():
    tests = pd.concat([pd.read_csv(p, parse_dates=["time"]) for p in sorted(PHASE2.glob("system_*_tests.csv"))], ignore_index=True)
    summaries = pd.DataFrame([json.loads(p.read_text()) for p in sorted(PHASE2.glob("system_*_summary.json"))])
    return tests, summaries


def accuracy(rows: pd.DataFrame) -> dict:
    err = rows["error_points"].abs().fillna(np.inf)
    return {
        "n": int(len(rows)),
        "n_estimated": int(rows["capacity_ah"].notna().sum()),
        "median_abs_error": float(np.median(err)) if len(rows) else np.nan,
        "median_error": float(rows["error_points"].median()),
        "coverage": float(rows["held"].fillna(False).astype(bool).mean()) if len(rows) else np.nan,
    }


def v7(config: CapacityConfig) -> dict:
    """Core against batch path, every day of three development test months."""
    tests = reference_tests("development")
    worst = 0.0
    n = 0
    for test_id in V7_TESTS:
        sid = int(test_id[:2])
        spec = figgener.pack_spec(sid)
        _, pairs = estimate_at_tests(pd.read_parquet(rest_file(sid, config.current_c_rate)), sid, tests, config)
        onset = tests.loc[tests["test_id"] == test_id, "onset"].iloc[0]
        at = pd.date_range(onset.to_period("M").start_time, onset.to_period("M").end_time.normalize(), freq="D")
        core = run_core(pairs, spec, config, at)
        z = _z(config.level)
        for when, c in zip(days(at), core):
            b = _solve(capacity_state_at(pairs, spec, config, float(when)), float(when), z, config)
            if c.status == 0 and b.status == 0:
                worst = max(worst, abs(b.theta[0] - c.theta[0]) / abs(c.theta[0]), abs(b.high - c.high) / abs(c.high))
                n += 1
    return {"max_relative_difference": worst, "n_estimates": n}


def main() -> None:
    config = CapacityConfig()
    digest = config_hash(config)
    tests, summaries = load()
    hashes = set(tests["config_hash"]) | set(summaries["config_hash"])
    if hashes != {digest}:
        raise SystemExit(f"results come from settings {sorted(hashes)}, not the frozen {digest}: rerun run_capacity.py")
    tests["flag"] = np.where(tests["test_id"].isin(FLAGGED), "discharged >1 % in the hour before", "")
    tests.to_csv(RESULTS / "phase2_tests.csv", index=False, float_format="%.4f")

    held = tests[tests["set"] == "held-out"]
    lmo_nmc = held[held["chemistry"].isin(["LMO/NMC", "NMC"])]
    lfp = held[held["chemistry"] == "LFP"]
    a2 = accuracy(lmo_nmc)
    a2b = accuracy(lmo_nmc[~lmo_nmc["test_id"].isin(FLAGGED)])
    a3 = accuracy(lfp)
    rows = [
        ("V2", "held-out LMO/NMC and NMC", f"median |error| {a2['median_abs_error']:.2f} points ({a2['n_estimated']}/{a2['n']} estimated)", "<= 3 points", a2["median_abs_error"] <= 3.0),
        ("V2 info", "same, without 03/3 and 08/1", f"median |error| {a2b['median_abs_error']:.2f} points ({a2b['n']} tests)", "information", None),
        ("V3", "held-out LFP", f"median |error| {a3['median_abs_error']:.2f} points; median error {a3['median_error']:+.2f} ({a3['n_estimated']}/{a3['n']} estimated)", "<= 5 points, bias shown", a3["median_abs_error"] <= 5.0),
        ("V4", "held-out LMO/NMC and NMC", f"band held the test in {a2['coverage']:.0%} of {a2['n']}", ">= 80 %", a2["coverage"] >= 0.80),
        ("V4 info", "held-out LFP", f"band held the test in {a3['coverage']:.0%} of {a3['n']}", "information", None),
        ("V4 info", "same as V4, without 03/3 and 08/1", f"band held the test in {a2b['coverage']:.0%} of {a2b['n']}", "information", None),
    ]
    for chem, paper in PAPER_FADE.items():
        mine = summaries[(summaries["chemistry"] == chem) & (summaries["set"] == "held-out")]["rate_points_per_year"].dropna()
        every = summaries[summaries["chemistry"] == chem]["rate_points_per_year"].dropna()
        mean = float(mine.mean()) if len(mine) else np.nan
        rows.append(("V5", f"held-out {chem} ({len(mine)} systems)", f"mean fade {mean:+.2f} points/year; paper {paper:+.1f}", "within +-0.5", bool(abs(mean - paper) <= 0.5)))
        rows.append(("V5 info", f"all {chem} ({len(every)} systems)", f"mean fade {every.mean():+.2f} points/year", "information", None))
    synth_path = RESULTS / "synthetic_coverage.csv"
    if synth_path.exists():
        synth = pd.read_csv(synth_path)
        share = synth["held"].mean()
        rows.append(("V6", f"{len(synth)} synthetic packs", f"band held the true usable fade ({synth['truth_points_per_year'].iloc[0]:.2f}) in {share:.1%}", "85-95 %", bool(0.85 <= share <= 0.95)))
    v = v7(config)
    rows.append(("V7", f"core against batch path, {v['n_estimates']} daily estimates", f"max relative difference {v['max_relative_difference']:.1e}", "<= 1e-6", v["max_relative_difference"] <= 1e-6))
    complete = len(summaries) == 21 and set(summaries["config_hash"]) == {digest}
    rows.append(("V8", "21 systems", "one command per system: python validation/run_capacity.py <id>", "all 21 rebuilt with one hash", complete))
    table = pd.DataFrame(rows, columns=["check", "scope", "value", "pass_if", "passed"])
    table["result"] = table["passed"].map({True: "pass", False: "fail", None: ""})
    table["config_hash"] = digest
    table.drop(columns="passed").to_csv(RESULTS / "checks_phase2.csv", index=False)
    print(table[["check", "scope", "value", "result"]].to_string(index=False))


if __name__ == "__main__":
    main()
