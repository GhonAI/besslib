"""Phase 2: reduce one system's one-second data to a table of rest candidates.

Every month is read once and searched for all detection settings (the current
thresholds), with one day of the neighbouring months on both sides
(``besslib.soh.rests.detect_rests_in_parts``). The running counters are joined across
months, so a pair of rests in different months gets the right charge.

The candidates are found with the shortest minimum duration of the tuning grid; longer
minimum durations are applied later as a filter. Classification (full, empty, other)
and pairing are cheap and run on these tables.

Output: ``data/reduced/rests/ID_XX_cYYYY.parquet`` (git-ignored), one per system and
current threshold (``YYYY`` = threshold in 1/10000 C).

Usage:
    python validation/reduce_events.py 1 4 7            # development systems, all thresholds
    python validation/reduce_events.py --frozen 2 3 5    # held-out run: frozen threshold only
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig
from besslib.soh.rests import detect_rests_in_parts

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "reduced" / "rests"

#: The tuning grid: current threshold (x C) and minimum rest duration (min).
CURRENT_GRID = (0.005, 0.01, 0.02)
DURATION_GRID = (10.0, 20.0, 40.0)


def rest_file(system_id: int, current_c_rate: float) -> Path:
    return OUT / f"ID_{system_id:02d}_c{round(current_c_rate * 10000):04d}.parquet"


def reduce_system(root: Path, system_id: int, currents=CURRENT_GRID, log=print) -> None:
    spec = figgener.pack_spec(system_id)
    months = figgener.list_months(root, system_id)["month"].tolist()
    configs = [CapacityConfig(current_c_rate=c, min_duration_min=min(DURATION_GRID)) for c in currents]
    parts = (figgener.read_figgener(root, system_id, m, m + pd.offsets.MonthEnd(0))[0] for m in months)
    started = time.time()

    def progress(n):
        if n % 12 == 0:
            log(f"  system {system_id:02d}: {n}/{len(months)} months, {time.time() - started:.0f} s")

    tables = detect_rests_in_parts(parts, spec, configs, log=progress)
    OUT.mkdir(parents=True, exist_ok=True)
    for c, table in zip(currents, tables):
        table.to_parquet(rest_file(system_id, c), index=False)
        log(f"system {system_id:02d}, threshold C/{1 / c:.0f}: {len(table)} candidates")


def main(argv: list[str]) -> None:
    frozen = "--frozen" in argv
    ids = [int(a) for a in argv if not a.startswith("--")]
    currents = (CapacityConfig().current_c_rate,) if frozen else CURRENT_GRID
    for system_id in ids:
        reduce_system(RAW, system_id, currents)


if __name__ == "__main__":
    main(sys.argv[1:])
