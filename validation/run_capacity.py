"""Phase 2: the frozen capacity run, one system at a time.

For each system:

1. held-out systems only: download the archive from Zenodo (MD5 checked), reduce it to
   rest candidates with the frozen current threshold, delete the archive;
2. classify the rests and pair them, with every capacity-test window masked
   (one day before the test begins to one day after it ends);
3. estimate the capacity at the start of every masked window and at every month end;
4. the fade rate over the record, and the offset current found the paper's way (the
   intercept of the mean current of full-to-full and empty-to-empty pairs against their
   voltage difference, Figgener et al. 2024, Suppl. Fig. 13) next to besslib's.

Every output row carries the configuration hash. The settings are the defaults of
``CapacityConfig`` (frozen after ``tune_capacity.py``).

Writes ``validation/results/phase2/system_XX_{tests,monthly}.csv`` and
``system_XX_summary.json``.

Usage:
    python validation/run_capacity.py 1 4 7          # some systems
    python validation/run_capacity.py development    # the 7 development systems
    python validation/run_capacity.py held-out       # the 14 held-out systems (once, after the freeze)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.capacity import estimate_capacity, fade_rate
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import classify_rests, rest_chain, rest_pairs

from build_reference import fetch
from reduce_events import RAW, reduce_system, rest_file
from tune_capacity import DEVELOPMENT, estimate_at_tests, masks_for

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "results" / "phase2"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)


def all_tests() -> pd.DataFrame:
    tests = pd.read_csv(ROOT / "validation" / "results" / "capacity_tests.csv", parse_dates=["onset", "end", "documented_start"])
    tests["test_id"] = tests["system_id"].map("{:02d}".format) + "/" + tests["test"].astype(str)
    return tests


def paper_offset(pairs: pd.DataFrame) -> dict:
    """Offset current the paper's way: F->F and E->E pairs, mean current against voltage difference."""
    same = pairs[pairs["kind"].isin(["F->F", "E->E"]) & (pairs["dt_days"] > 0.1)]
    if len(same) < 10:
        return {"paper_offset_current_a": np.nan, "paper_offset_pairs": int(len(same))}
    mean_current = same["q_ah"] / (same["dt_days"] * 24.0)  # A, positive = net charge in
    slope, intercept = np.polyfit(same["dv"], mean_current, 1)
    # At zero voltage difference, the net charge put in equals what the meter did not see.
    return {"paper_offset_current_a": float(-intercept), "paper_offset_pairs": int(len(same))}


def ensure_candidates(system_id: int, config: CapacityConfig) -> Path:
    path = rest_file(system_id, config.current_c_rate)
    if path.exists():
        return path
    archive = fetch(system_id)
    started = time.time()
    reduce_system(archive.parent, system_id, (config.current_c_rate,))
    print(f"  reduced in {time.time() - started:.0f} s", flush=True)
    if system_id not in DEVELOPMENT:
        archive.unlink()
        print("  archive deleted (held-out system)", flush=True)
    return path


def run_system(system_id: int, tests: pd.DataFrame, config: CapacityConfig) -> None:
    which = "development" if system_id in DEVELOPMENT else "held-out"
    print(f"system {system_id:02d} ({which})", flush=True)
    spec = figgener.pack_spec(system_id)
    candidates = pd.read_parquet(ensure_candidates(system_id, config))
    digest = config_hash(config)
    OUT.mkdir(parents=True, exist_ok=True)

    mine = tests[tests["system_id"] == system_id]
    mask = masks_for(tests, system_id)
    if len(mine):
        per_test, pairs = estimate_at_tests(candidates, system_id, tests, config)
        per_test.insert(1, "set", which)
        per_test["config_hash"] = digest
        per_test.to_csv(OUT / f"system_{system_id:02d}_tests.csv", index=False, float_format="%.5f")
    else:
        cands = candidates[candidates["duration_min"] >= config.min_duration_min]
        pairs = rest_pairs(rest_chain(classify_rests(cands, spec, config), spec, config), config)

    monthly = estimate_capacity(None, spec, config=config, pairs=pairs)
    monthly["config_hash"] = digest
    monthly.to_csv(OUT / f"system_{system_id:02d}_monthly.csv", float_format="%.5f")

    fade = fade_rate(pairs, spec, config)
    summary = {
        "system_id": system_id,
        "set": which,
        "chemistry": spec.chemistry,
        "nominal_capacity_ah": spec.nominal_capacity_ah,
        "n_candidates": int(len(candidates)),
        "n_pairs_used": int(len(pairs)),
        "pairs_by_kind": {k: int(v) for k, v in pairs["kind"].value_counts().items()},
        "masked_windows": len(mask),
        **{k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in fade.items()},
        **paper_offset(pairs),
    }
    (OUT / f"system_{system_id:02d}_summary.json").write_text(json.dumps(summary, indent=1, default=float) + "\n")
    rate = summary.get("rate_points_per_year")
    print(
        f"  {summary['n_pairs_used']} pairs {summary['pairs_by_kind']}; fade "
        f"{rate if rate is None else round(rate, 2)} points/year; config {digest}",
        flush=True,
    )


def main(argv: list[str]) -> None:
    if argv == ["development"]:
        ids = list(DEVELOPMENT)
    elif argv == ["held-out"]:
        ids = list(HELD_OUT)
    else:
        ids = [int(a) for a in argv]
    config = CapacityConfig()
    print(f"frozen configuration {config_hash(config)}", flush=True)
    tests = all_tests()
    for system_id in ids:
        run_system(system_id, tests, config)


if __name__ == "__main__":
    main(sys.argv[1:])
