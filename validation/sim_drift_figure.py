"""One simulated pack of case 6 (a drifting stop) for the figures of docs/05 and the README: its month ends and tests.

The pack is setting ``D157`` (the discharge stop falls 157 mV per cell over the run), seed 2000, the first of the held-out
seeds, run with today's settings by ``sim_case_drift.run_pack``. Writes ``validation/results/sim_v03/drift_pack.csv``:
one row per month end with a band (``kind`` month: the A1 estimate, its band and T_rest, the charge between the voltages it
reads at) and one per simulated capacity test (``kind`` test: the measured test value and T_rest at its date).

Usage:
    python validation/sim_drift_figure.py
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "results" / "sim_v03" / "drift_pack.csv"
PACK = ("D157", 2000)


def one_pack(job: tuple[str, int]) -> dict:
    import sim_case_drift

    series: dict = {}
    sim_case_drift.run_pack(*job, series=series)
    return series


def main() -> None:
    from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

    with Pool(1) as pool:
        (series,) = pool.map(one_pack, [PACK])
    months = pd.DataFrame(series["months"]).assign(kind="month")
    tests = pd.DataFrame(series["tests"]).assign(kind="test")
    table = pd.concat([months, tests], ignore_index=True)
    table.insert(0, "setting", PACK[0])
    table.insert(1, "seed", PACK[1])
    table["nominal_ah"] = series["nominal_ah"]
    table["capacity_config_hash"] = config_hash(CapacityConfig())
    table["record_config_hash"] = config_hash(RecordConfig())
    table.to_csv(OUT, index=False, float_format="%.6g")
    print(f"written: {OUT.relative_to(ROOT)} ({len(months)} month ends, {len(tests)} tests)")


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "tests")]
    main()
