"""Phase 2: set the edge tolerance from development pair charges, without any test value.

A rest counts as full (empty) when its edge voltage lies within the tolerance below the
end-of-charge level (above the end-of-discharge level), as a share of the span between
the two levels. Too wide a tolerance lets half-full rests in, and the full-to-empty
charge falls.

Rule (decided before any estimate was compared with a test): pairs from full to empty
and back are grouped by how far their full rest (and, separately, their empty rest) lies
from its level, in bins up to 3, 6, 10 and 15 % of the span. The tolerance is the widest
bin edge up to which every bin has at least 10 pairs and a median charge within 2 % of
the innermost bin (up to 3 %). The narrowest over the seven development systems and both
edges is used. Rests are found with C/100 and 20 minutes; the test windows are masked.

Writes ``validation/results/edge_tolerance.csv`` (one row per system, edge and bin).

Usage:
    python validation/edge_tolerance.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig
from besslib.soh.rests import classify_rests, rest_chain

from reduce_events import rest_file
from tune_capacity import DEVELOPMENT, masks_for, reference_tests

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
EDGES = (0.03, 0.06, 0.10, 0.15)
MAX_DROP = 0.02
MIN_PAIRS = 10


def pair_distances(system_id: int, config: CapacityConfig, tests: pd.DataFrame) -> pd.DataFrame:
    spec = figgener.pack_spec(system_id)
    cands = pd.read_parquet(rest_file(system_id, config.current_c_rate))
    cands = cands[cands["duration_min"] >= config.min_duration_min]
    mask = masks_for(tests, system_id)
    inside = np.zeros(len(cands), dtype=bool)
    for a, b in mask:
        inside |= ((cands["start"] >= a) & (cands["start"] <= b)).to_numpy()
    rests = classify_rests(cands[~inside], spec, config).set_index("start")
    chain = rest_chain(rests.reset_index(), spec, config, mask=mask)
    a, b = chain.iloc[:-1].reset_index(drop=True), chain.iloc[1:].reset_index(drop=True)
    cross = ((b["new_segment"] == 0) & (a["kind"] != b["kind"])).to_numpy()
    a, b = a[cross], b[cross]
    full_t = np.where(a["kind"] == "full", a["time"], b["time"])
    empty_t = np.where(a["kind"] == "full", b["time"], a["time"])
    full, empty = rests.loc[full_t], rests.loc[empty_t]
    span = full["level_full_v"].to_numpy() - empty["level_empty_v"].to_numpy()
    return pd.DataFrame(
        {
            "charge_points": np.abs(b["q_ah"].to_numpy() - a["q_ah"].to_numpy()) / spec.nominal_capacity_ah * 100.0,
            "full": (full["level_full_v"].to_numpy() - full["edge_v"].to_numpy()) / span,
            "empty": (empty["edge_v"].to_numpy() - empty["level_empty_v"].to_numpy()) / span,
        }
    )


def main() -> None:
    config = CapacityConfig(edge_tolerance=max(EDGES))
    tests = reference_tests("development")
    rows, chosen = [], []
    for sid in DEVELOPMENT:
        d = pair_distances(sid, config, tests)
        for edge in ("full", "empty"):
            inner = d.loc[d[edge] <= EDGES[0], "charge_points"]
            ref = float(inner.median())
            tol = EDGES[0]
            ok = True
            for lo, hi in zip(EDGES[:-1], EDGES[1:]):
                sel = d.loc[(d[edge] > lo) & (d[edge] <= hi), "charge_points"]
                med = float(sel.median()) if len(sel) else np.nan
                passed = len(sel) >= MIN_PAIRS and med >= ref * (1.0 - MAX_DROP)
                rows.append({"system_id": sid, "edge": edge, "bin_low": lo, "bin_high": hi, "n_pairs": len(sel),
                             "median_charge_points": med, "inner_median_points": ref, "inner_n": len(inner), "passed": passed})
                if ok and passed:
                    tol = hi
                else:
                    ok = False
            chosen.append(tol)
            print(f"system {sid:02d} {edge:>5}: inner median {ref:.1f} points ({len(inner)} pairs); tolerance {tol:.2f}")
    table = pd.DataFrame(rows)
    table["tolerance"] = min(chosen)
    RESULTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "edge_tolerance.csv", index=False, float_format="%.4f")
    print(f"edge tolerance: {min(chosen):.2f} of the voltage span")


if __name__ == "__main__":
    main()
