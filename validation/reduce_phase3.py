"""Reduce one system's one-second data to the daily, step and rest tables (``besslib.soh.reduce``).

Every month is read once, from ``data/raw``, and reduced with the rest candidates of
``CapacityConfig(min_duration_min=10.0)`` (the same candidates as
``data/reduced/rests/ID_XX_c0050.parquet``). The three tables are written to
``data/reduced/phase3/ID_XX_{daily,steps,rests}.parquet`` (git-ignored).

A held-out system is refused unless ``--held-out`` is given.

Usage:
    python validation/reduce_phase3.py 1 4         # some development systems
    python validation/reduce_phase3.py development  # the 7 development systems, one after the other
    python validation/reduce_phase3.py --held-out 2 # held-out systems (once, after the freeze)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig
from besslib.soh.reduce import Reduced, read_reduced, reduce_parts, write_reduced

from build_reference import fetch
from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "reduced" / "phase3"
CONFIG = CapacityConfig(min_duration_min=10.0)
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)


def reduced_files(system_id: int) -> dict[str, Path]:
    """The three table files of one system, by name (``daily``, ``steps``, ``rests``)."""
    return {k: OUT / f"ID_{system_id:02d}_{k}.parquet" for k in ("daily", "steps", "rests")}


def load_reduced(system_id: int) -> Reduced:
    """The reduced tables of one system, as written by ``reduce_system``."""
    return read_reduced(OUT, f"ID_{system_id:02d}")


def reduce_system(root: Path, system_id: int, log=print) -> Reduced:
    """Read a system month by month from ``root``, reduce it and write the three tables."""
    spec = figgener.pack_spec(system_id)
    months = figgener.list_months(root, system_id)["month"].tolist()
    parts = (figgener.read_figgener(root, system_id, m, m + pd.offsets.MonthEnd(0))[0] for m in months)
    started = time.time()

    def progress(n):
        if n % 12 == 0:
            log(f"  system {system_id:02d}: {n}/{len(months)} months, {time.time() - started:.0f} s")

    reduced = reduce_parts(parts, spec, CONFIG, log=progress)
    write_reduced(reduced, OUT, f"ID_{system_id:02d}")
    log(f"system {system_id:02d}: reduced in {time.time() - started:.0f} s")
    for name, path in reduced_files(system_id).items():
        log(f"  {path.name}: {path.stat().st_size / 1e6:.2f} MB, {len(getattr(reduced, name))} rows")
    return reduced


def ensure_reduced(system_id: int) -> dict[str, Path]:
    """The table files of one system; if they are missing, fetch the archive, reduce it, and delete the
    archive of a held-out system (as ``run_capacity.ensure_candidates`` does)."""
    files = reduced_files(system_id)
    if all(p.exists() for p in files.values()):
        return files
    archive = fetch(system_id)
    reduce_system(archive.parent, system_id)
    if system_id not in DEVELOPMENT:
        archive.unlink()
        print("  archive deleted (held-out system)", flush=True)
    return files


def check_reduced(system_id: int) -> dict:
    """The checks of a development system's tables: the phase 2 rest columns are reproduced exactly, the
    running counters equal the daily sums (largest relative difference), and the temperature seconds add up to
    the measured seconds."""
    import numpy as np

    red = load_reduced(system_id)
    d, r = red.daily, red.rests
    old = pd.read_parquet(ROOT / "data" / "reduced" / "rests" / f"ID_{system_id:02d}_c0050.parquet")
    pd.testing.assert_frame_equal(r[list(old.columns)], old, check_exact=True)
    first = d["t_first"].iloc[0]
    before = np.searchsorted(d["day"].to_numpy(), r["start"].dt.normalize().to_numpy())
    cum = {c: np.concatenate([[0.0], np.cumsum(d[c].to_numpy())]) for c in ("ah_in", "ah_out", "wh_in", "wh_out", "measured_s")}
    ai = cum["ah_in"][before] + r["ah_in_today"].to_numpy()
    ao = cum["ah_out"][before] + r["ah_out_today"].to_numpy()
    wi = cum["wh_in"][before] + r["wh_in_today"].to_numpy()
    wo = cum["wh_out"][before] + r["wh_out_today"].to_numpy()
    elapsed = (r["start"] - first).dt.total_seconds().to_numpy()
    meas = cum["measured_s"][before] + r["measured_s_today"].to_numpy()

    def worst(a, b, scale):
        return float(np.max(np.abs(a - b) / np.maximum(np.abs(scale), 1.0))) if len(a) else 0.0

    bins = [c for c in d.columns if c.startswith("s_")]
    return {
        "system": system_id,
        "rows_daily": len(d), "rows_steps": len(red.steps), "rows_rests": len(r),
        "phase2_columns_exact": True,
        "charge_rel": worst(ai - ao, r["charge_ah"].to_numpy(), r["throughput_ah"].to_numpy()),
        "throughput_rel": worst(ai + ao, r["throughput_ah"].to_numpy(), r["throughput_ah"].to_numpy()),
        "energy_in_rel": worst(wi, r["energy_in_wh"].to_numpy(), r["energy_in_wh"].to_numpy()),
        "energy_out_rel": worst(wo, r["energy_out_wh"].to_numpy(), r["energy_in_wh"].to_numpy()),
        "seconds_rel": worst(elapsed - meas, r["missing_s"].to_numpy(), elapsed),
        "bins_vs_measured_rel": float(np.max(np.abs(d[bins].sum(axis=1) - d["measured_s"]) / np.maximum(d["measured_s"], 1.0))),
    }


def main(argv: list[str]) -> None:
    if "--check" in argv:
        ids = [int(a) for a in argv if not a.startswith("--")]
        if [i for i in ids if i not in DEVELOPMENT]:
            sys.exit("refused: the check reads development systems only")
        for system_id in ids:
            print(check_reduced(system_id), flush=True)
        return
    held_out = "--held-out" in argv
    args = [a for a in argv if a != "--held-out"]
    ids = list(DEVELOPMENT) if args == ["development"] else [int(a) for a in args]
    refused = [i for i in ids if i not in DEVELOPMENT]
    if refused and not held_out:
        sys.exit(f"refused: systems {refused} are held out; give --held-out to reduce them")
    for system_id in ids:
        reduce_system(RAW, system_id)


if __name__ == "__main__":
    main(sys.argv[1:])
