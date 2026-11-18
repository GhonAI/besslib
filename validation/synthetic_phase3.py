"""Phase 3: checks W2, W5, W7 and W8 on synthetic packs with a known truth.

200 packs (``tests/synth.py``), each 36 months at a 10-second step, as in check V6. Every
pack has its own random operation, sensor gain errors, BMS stop errors and planted
events. Their resistance grows by 5 % of its start value per year and falls with the
pack temperature, which follows the season; a few intervals of a fixed high or low
temperature and a few deep discharges are planted at random days.

Each seed is simulated three times with the same operation:

* pack A, measured: current gain error within 0.5 %, voltage gain error within 0.25 %
  (the stated sensor accuracies). Checks W2 (resistance and its growth), W5 (round-trip
  efficiency) and W7 (apparent charge loss) use it.
* pack B, measured: current gain error only, at most 0.5 %. Check W8 (throughput,
  cycles, planted events) uses it, as its rule says.
* the pack's own signals, without gain error and noise: the truth.

The three records are reduced to the daily, step and rest tables
(``besslib.soh.reduce``); the checks read those tables. Each check lives in its own
module with one function ``check(run) -> dict``:

    synthetic_usage.py        W8
    synthetic_resistance.py   W2
    synthetic_efficiency.py   W5, W7

Pass: the 90 % band holds the truth in 85-95 % of the packs (W2 for the value and for
the growth, W5, W7); Ah, kWh and cycles within 0.6 % of the truth and the planted
events counted exactly on every pack (W8). A pack without a band counts as not held.

Writes ``validation/results/synthetic_phase3.csv`` (one row per pack).

Usage:
    python validation/synthetic_phase3.py                          # 200 packs, 3 processes
    python validation/synthetic_phase3.py --seeds 1 2 3            # some packs, one process
    python validation/synthetic_phase3.py --seeds 1 2 --only resistance
"""

from __future__ import annotations

import importlib
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from synth import SynthPack, frames, true_segments, true_stops  # noqa: E402

from besslib.frames import PackSpec  # noqa: E402
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash  # noqa: E402

RESULTS = ROOT / "validation" / "results"
MONTHS = 36  # one year of level warm-up, then two years of estimates
STEP_S = 10
N_PACKS = 200
PASS_RANGE = (0.85, 0.95)
PARTS = {"usage": "synthetic_usage", "resistance": "synthetic_resistance", "efficiency": "synthetic_efficiency"}

HOT_C, COLD_C = 47.0, -3.0  # planted pack temperatures, outside the seasonal range


def packs_for(seed: int) -> tuple[SynthPack, SynthPack]:
    """Pack A and pack B of one seed: the same operation and planted events; B has no voltage gain error."""
    rng = np.random.default_rng(1_000_000 + seed)
    days = MONTHS * 30
    chosen = rng.choice(np.arange(30, days), size=int(rng.integers(3, 9)) + int(rng.integers(3, 9)), replace=False)
    n_deep = int(rng.integers(3, min(9, len(chosen) - 2)))
    deep = tuple(sorted(int(d) for d in chosen[:n_deep]))
    planted = []
    for k, day in enumerate(sorted(int(d) for d in chosen[n_deep:])):
        if k % 3 == 2:
            planted.append((day, float(rng.integers(0, 5)), float(rng.integers(2, 6)), COLD_C))
        else:
            planted.append((day, float(rng.integers(9, 14)), float(rng.integers(2, 7)), HOT_C))
    common = dict(
        r_growth_per_year=0.05,
        r_temp_coeff_per_c=0.01,
        temp_mean_c=22.0,
        temp_season_amp_c=6.0,
        temp_daily_amp_c=1.5,
        planted_temps=tuple(planted),
        planted_deep_days=deep,
    )
    return SynthPack(voltage_gain_error_max=0.0025, **common), SynthPack(**common)


@dataclass
class Run:
    """One simulated seed, as the check functions see it."""

    seed: int
    months: int
    step_s: int
    spec: PackSpec
    pack: SynthPack  # pack A
    pack_plain: SynthPack  # pack B
    reduced: object  # pack A, measured signals
    reduced_plain: object  # pack B, measured signals
    reduced_true: object  # the pack's own signals
    stops: pd.DataFrame
    segments: pd.DataFrame


def simulate(seed: int, need_plain: bool = True) -> Run:
    from besslib.soh.reduce import reduce_parts

    pack, plain = packs_for(seed)
    spec = pack.spec()

    def reduce(p: SynthPack, true_signals: bool = False):
        return reduce_parts(frames(p, MONTHS, step_s=STEP_S, seed=seed, true_signals=true_signals), spec)

    return Run(
        seed=seed,
        months=MONTHS,
        step_s=STEP_S,
        spec=spec,
        pack=pack,
        pack_plain=plain,
        reduced=reduce(pack),
        reduced_plain=reduce(plain) if need_plain else None,
        reduced_true=reduce(pack, true_signals=True),
        stops=true_stops(pack, MONTHS, seed),
        segments=true_segments(pack, MONTHS, seed),
    )


def one_pack(args) -> dict:
    seed, parts = args
    started = time.time()
    run = simulate(seed, need_plain="usage" in parts)
    row = {"seed": seed}
    for part in parts:
        row.update(importlib.import_module(PARTS[part]).check(run))
    row["capacity_config_hash"] = config_hash(CapacityConfig())
    row["record_config_hash"] = config_hash(RecordConfig())
    row["seconds"] = round(time.time() - started, 1)
    return row


def coverage(table: pd.DataFrame, column: str) -> tuple[int, int, float, str]:
    held = table[column].fillna(False).astype(bool)
    share = float(held.mean())
    return int(held.sum()), len(held), share, "pass" if PASS_RANGE[0] <= share <= PASS_RANGE[1] else "fail"


def summary(table: pd.DataFrame) -> list[str]:
    """One line per check, from the columns the check modules wrote."""
    lines = []
    for check, column, what in (
        ("W2", "w2_value_held", "resistance"),
        ("W2", "w2_growth_held", "resistance growth"),
        ("W5", "w5_held", "round-trip efficiency"),
        ("W7", "w7_held", "apparent charge loss"),
    ):
        if column in table:
            n, total, share, verdict = coverage(table, column)
            lines.append(f"{check} {what}: the band held the truth in {n} of {total} packs ({share:.1%}): {verdict}")
    if "w8_pass" in table:
        ok = table["w8_pass"].fillna(False).astype(bool)
        worst = table[[c for c in table if c.startswith("w8_") and c.endswith("_rel_error")]].abs().max().max()
        lines.append(
            f"W8 throughput and planted events: {int(ok.sum())} of {len(ok)} packs within the rule; "
            f"largest relative error {worst:.4%}: {'pass' if ok.all() else 'fail'}"
        )
    return lines


def main(argv: list[str]) -> None:
    parts = list(PARTS)
    if "--only" in argv:
        k = argv.index("--only")
        parts = [argv[k + 1]]
        argv = argv[:k] + argv[k + 2 :]
    if "--seeds" in argv:
        seeds = [int(a) for a in argv[argv.index("--seeds") + 1 :]]
        rows = [one_pack((seed, parts)) for seed in seeds]
        table = pd.DataFrame(rows)
        with pd.option_context("display.width", 200, "display.max_columns", None, "display.max_rows", None):
            print(table.T)
    else:
        n = int(argv[0]) if argv else N_PACKS
        with Pool(3) as pool:
            rows = pool.map(one_pack, [(seed, parts) for seed in range(1, n + 1)], chunksize=1)
        table = pd.DataFrame(rows)
        RESULTS.mkdir(parents=True, exist_ok=True)
        table.to_csv(RESULTS / "synthetic_phase3.csv", index=False, float_format="%.6g")
    for line in summary(table):
        print(line)
    print(f"configuration: capacity {config_hash(CapacityConfig())}, record {config_hash(RecordConfig())}")


if __name__ == "__main__":
    main(sys.argv[1:])
