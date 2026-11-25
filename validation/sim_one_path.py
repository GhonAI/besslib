"""Information: V6, X3 and W7 of version 0.2 once more, on a pack with one state-of-charge path.

``tests/synth.py`` decides its stops on the BMS's own copy of the pack, whose capacity is
that of midnight, while its frames come from the plant, whose capacity is that of each
segment's start. The plant slowly falls behind the copy (check S0c), so the packs of V6, X3 and
W7 carried an empty stop that drifts down although no drift was planned. Here the BMS reads
the plant itself (``stop_rule = "cells"``): the same seeds, duties, sensors and daily stop
errors, one path. ``true_stops`` then gives the plant's own state of charge and capacity.

The published numbers (``validation/results/synthetic_coverage.csv``, ``synthetic_lag.csv``,
``synthetic_phase3.csv``) stay as they are; this run is shown beside them and judges nothing.

Writes ``validation/results/sim/one_path_{v6,x3,w7}.csv`` (one row per pack) and
``one_path.csv`` (each number, published and one-path, computed by the same lines of code).

Usage:
    python validation/sim_one_path.py                 # all three, 200 packs, 3 processes
    python validation/sim_one_path.py --summary       # the summary only, from the files on disk
    python validation/sim_one_path.py --seeds 1 2 3   # some packs, printed only
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "validation"))
OUT = ROOT / "validation" / "results" / "sim"
PUBLISHED = ROOT / "validation" / "results"
CHECKS = ("v6", "x3", "w7")
N_PACKS = 200


def one_path() -> None:
    """Make ``tests/synth.py`` a one-path pack in this process (``synth`` must not be in use yet)."""
    import synth

    if getattr(synth, "_one_path", False):
        return
    case, truth = synth.SynthPack.case, synth._truth

    def case_one_path(self, step_s: int = 10) -> dict:
        data = case(self, step_s)
        data["bms"]["stop_rule"] = "cells"
        return data

    def truth_one_path(pack, months, seed):
        stops, blocks = truth(pack, months, seed)
        return stops.assign(model_soc=stops["soc"], model_capacity_ah=stops["capacity_ah"]), blocks

    synth.SynthPack.case = case_one_path
    synth._truth = truth_one_path
    synth._one_path = True


def one(args) -> dict:
    """One pack of one check, on the one-path pack: the row the check's own script makes."""
    check, seed = args
    one_path()
    if check == "v6":
        import synthetic_coverage

        return synthetic_coverage.one_pack(seed)
    if check == "x3":
        import synthetic_lag
        from besslib.soh.config import CapacityConfig

        return synthetic_lag.one_pack((seed, CapacityConfig().band_forgetting))
    import synthetic_phase3

    row = synthetic_phase3.one_pack((seed, ["w7"]))
    return {k: v for k, v in row.items() if k != "w7_seconds"}


def numbers(check: str, table: pd.DataFrame) -> list[tuple[str, str]]:
    """What the check reports, from a table of its rows (published or one-path): (quantity, value)."""
    n = len(table)
    if check == "v6":
        truth = float(table["usable_fade_points_per_year"].mean())
        held = (table["low"] <= truth) & (truth <= table["high"])
        window = (table["low"] <= table["window_fade_points_per_year"]) & (
            table["window_fade_points_per_year"] <= table["high"])
        err = (table["rate_points_per_year"] - table["window_fade_points_per_year"]).mean()
        return [
            ("V6: the band holds the expected usable fade (V6's rule, 85-95 %)", f"{int(held.sum())} of {n} ({held.mean():.1%})"),
            ("expected usable fade, points per year", f"{truth:.3f}"),
            ("information: the band holds the fade between the mean rest voltages", f"{int(window.sum())} of {n} ({window.mean():.1%})"),
            ("mean of estimate - fade between the mean rest voltages, points per year", f"{err:+.3f}"),
        ]
    if check == "x3":
        total, held = int(table["n_band"].sum()), int(table["n_held_at_date"].sum())
        n_ref, held_ref = int(table["n_reference"].sum()), int(table["n_held_reference"].sum())
        lag, ref = table["mean_lag_points"].dropna(), table["mean_lag_reference_points"].dropna()
        return [
            ("X3: the band at the date holds the charge between the stops (X3's rule, 85-95 %)", f"{held} of {total} ({held / total:.1%})"),
            ("X3: mean lag, points (limit 2 sd / sqrt(n))", f"{lag.mean():+.3f} (+-{2.0 * lag.std() / len(lag) ** 0.5:.3f})"),
            ("information: the band holds the charge between the read-at rest voltages", f"{held_ref} of {n_ref} ({held_ref / n_ref:.1%})"),
            ("information: its mean lag, points", f"{ref.mean():+.3f} (+-{2.0 * ref.std() / len(ref) ** 0.5:.3f})"),
        ]
    held = table["w7_held"].fillna(False).astype(bool)
    e = table["w7_error_ma"].dropna()
    return [
        ("W7: the band holds the true apparent charge loss (W7's rule, 85-95 %)", f"{int(held.sum())} of {n} ({held.mean():.1%})"),
        ("W7: mean error, mA (sd over packs)", f"{e.mean():+.2f} ({e.std():.2f})"),
    ]


def summary() -> pd.DataFrame:
    """Each number, published beside one-path. ``OUT`` must hold the three one-path files."""
    files = {"v6": "synthetic_coverage.csv", "x3": "synthetic_lag.csv", "w7": "synthetic_phase3.csv"}
    rows = []
    for check in CHECKS:
        new = pd.read_csv(OUT / f"one_path_{check}.csv")
        old = pd.read_csv(PUBLISHED / files[check])
        if sorted(old["seed"]) != sorted(new["seed"]):
            raise SystemExit(f"{check}: the one-path file has other seeds than the published one")
        for (quantity, a), (_, b) in zip(numbers(check, old), numbers(check, new)):
            rows.append({"check": check.upper(), "quantity": quantity, "published": a, "one_path": b})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "one_path.csv", index=False)
    return table


def write(check: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    table = pd.DataFrame(rows).sort_values("seed").reset_index(drop=True)
    if len(table) != N_PACKS:
        raise SystemExit(f"{check}: {len(table)} packs, not {N_PACKS}")
    table.to_csv(OUT / f"one_path_{check}.csv", index=False, float_format="%.5f")


def main(argv: list[str]) -> None:
    if argv == ["--summary"]:
        print(summary().to_string())
        return
    seeds = [int(s) for s in argv[1:]] if argv[:1] == ["--seeds"] else None
    for check in CHECKS:
        jobs = [(check, s) for s in (seeds or range(1, N_PACKS + 1))]
        if seeds:
            rows = [one(j) for j in jobs]
            print(pd.DataFrame(rows).T.to_string())
            continue
        with Pool(3) as pool:
            write(check, pool.map(one, jobs, chunksize=1))
    if not seeds:
        print(summary().to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
