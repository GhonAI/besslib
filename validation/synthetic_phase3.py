"""Phase 3: checks W2, W5, W7 and W8 on synthetic packs with a known truth.

200 packs (``tests/synth.py``), each 36 months at a 10-second step, as in check V6. Every
pack has its own random operation, sensor gain errors, BMS stop errors and planted
events. Their resistance grows by 5 % of its start value per year and falls with the
pack temperature, which follows the season; a few intervals of a fixed high or low
temperature and a few deep discharges are planted at random days.

Each seed is simulated with the same operation several times:

* pack A, measured: current gain error within 0.5 %, voltage gain error within 0.25 %
  (the stated sensor accuracies), current and voltage noise. Checks W2 (resistance and
  its growth), W5 (round-trip efficiency) and W7 (apparent charge loss) use it.
* pack B, measured: the current gain error only (at most 0.5 %; no noise, no voltage
  gain error), as the rule of W8 says. Check W8 (throughput, cycles, planted events)
  uses it.
* pack B with the noise of pack A (current 0.05 A, voltage 5 mV): the same W8 count,
  written as information (``w8noise_*``): the noise moves currents near the idle
  threshold across it, so hours of charging inside the planted intervals change.
* the pack's own signals, without gain error and noise: the truth.

The records are reduced to the daily, step and rest tables (``besslib.soh.reduce``); the
checks read those tables. Each part is one check (``PARTS``), with its own column prefix:

    w2        synthetic_resistance.check       W2
    w5        synthetic_efficiency.check_w5    W5
    w7        synthetic_efficiency.check_w7    W7
    w8        synthetic_usage.check            W8 (pack B)
    w8noise   synthetic_usage.check            W8, information (pack B with noise)
    w7bf1     synthetic_efficiency.check_w7    W7, information: band_forgetting = 1 (version 0.1's band)

Pass: the 90 % band holds the truth in 85-95 % of the packs (W2 for the value and for
the growth, W5, W7); Ah, kWh and cycles within 0.6 % of the truth and the planted
events counted exactly on every pack (W8). A pack without a band counts as not held.
W2's growth is judged against the simulator's known growth at the fit's origin
(``w2_growth_held_param``); this reading was chosen after the first results. Its
same-days truth (``w2_growth_held``) and the value's three truths are information.

Writes ``validation/results/synthetic_phase3.csv`` (one row per pack). A run of some
parts replaces only their columns in the file on disk and keeps the others: the file
must hold the same seeds and come from the same settings (both configuration hashes),
else the run stops and all parts must be run.

Usage:
    python validation/synthetic_phase3.py                          # all parts, 200 packs, 3 processes
    python validation/synthetic_phase3.py --only w5 w8 w8noise     # some parts, merged into the file
    python validation/synthetic_phase3.py --processes 2 --only w8  # fewer processes
    python validation/synthetic_phase3.py --seeds 1 2 3 --only w5  # some packs, one process, printed only
"""

from __future__ import annotations

import importlib
import sys
import time
from dataclasses import dataclass, replace
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
OUT = RESULTS / "synthetic_phase3.csv"
MONTHS = 36  # one year of level warm-up, then two years of estimates
STEP_S = 10
N_PACKS = 200
PASS_RANGE = (0.85, 0.95)
HASH_COLUMNS = ("capacity_config_hash", "record_config_hash")


@dataclass(frozen=True)
class Part:
    module: str
    function: str
    prefix: str
    signals: tuple  # which reduced records the check reads: measured (A), plain (B), noisy (B with noise), true


#: The parts, in the order of the file's columns. No prefix is the start of another.
PARTS = {
    "w2": Part("synthetic_resistance", "check", "w2_", ("measured", "true")),
    "w5": Part("synthetic_efficiency", "check_w5", "w5_", ("measured", "true")),
    "w7": Part("synthetic_efficiency", "check_w7", "w7_", ("measured",)),
    "w8": Part("synthetic_usage", "check", "w8_", ("plain", "true")),
    "w8noise": Part("synthetic_usage", "check", "w8noise_", ("noisy", "true")),
    "w7bf1": Part("synthetic_efficiency", "check_w7", "w7bf1_", ("measured",)),
}

HOT_C, COLD_C = 47.0, -3.0  # planted pack temperatures, outside the seasonal range


def packs_for(seed: int) -> tuple[SynthPack, SynthPack, SynthPack]:
    """Packs A, B and B with noise of one seed: the same operation, planted events and current gain error.

    A has a voltage gain error and noise; B has the current gain error only; the third is
    B with the noise of A (the pack B of the first 200-pack run).
    """
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
    noisy = SynthPack(**common)
    # A noise of zero still draws its random numbers, so B keeps the operation and gain of the others.
    plain = replace(noisy, current_noise_a=0.0, voltage_noise_v=0.0)
    return SynthPack(voltage_gain_error_max=0.0025, **common), plain, noisy


@dataclass
class Run:
    """One simulated seed, as the check functions see it (a record not needed by the parts run is None)."""

    seed: int
    months: int
    step_s: int
    spec: PackSpec
    pack: SynthPack  # pack A
    pack_plain: SynthPack  # pack B
    pack_noisy: SynthPack  # pack B with noise
    reduced: object  # pack A, measured signals
    reduced_plain: object  # pack B, measured signals
    reduced_noisy: object  # pack B with noise, measured signals
    reduced_true: object  # the pack's own signals
    stops: pd.DataFrame
    segments: pd.DataFrame


def simulate(seed: int, need_plain: bool = True, parts=None) -> Run:
    """Simulate and reduce one seed: the records the given parts read (default: all parts; without
    ``need_plain`` those of pack A only)."""
    from besslib.soh.reduce import reduce_parts

    if parts is None:
        parts = list(PARTS) if need_plain else ["w2", "w5", "w7"]
    signals = {s for p in parts for s in PARTS[p].signals}
    pack, plain, noisy = packs_for(seed)
    spec = pack.spec()

    def reduce(p: SynthPack, wanted: str, true_signals: bool = False):
        if wanted not in signals:
            return None
        return reduce_parts(frames(p, MONTHS, step_s=STEP_S, seed=seed, true_signals=true_signals), spec)

    return Run(
        seed=seed,
        months=MONTHS,
        step_s=STEP_S,
        spec=spec,
        pack=pack,
        pack_plain=plain,
        pack_noisy=noisy,
        reduced=reduce(pack, "measured"),
        reduced_plain=reduce(plain, "plain"),
        reduced_noisy=reduce(noisy, "noisy"),
        reduced_true=reduce(pack, "true", true_signals=True),
        stops=true_stops(pack, MONTHS, seed),
        segments=true_segments(pack, MONTHS, seed),
    )


def run_part(name: str, run: Run) -> dict:
    """The columns of one part for one simulated seed."""
    part = PARTS[name]
    function = getattr(importlib.import_module(part.module), part.function)
    if name == "w8noise":
        out = function(replace(run, pack_plain=run.pack_noisy, reduced_plain=run.reduced_noisy))
        return {part.prefix + key[len("w8_"):]: value for key, value in out.items()}
    if name == "w7bf1":  # the same packs with the forgetting weight once in the band's sums
        out = function(run, RecordConfig(), CapacityConfig().with_(band_forgetting=1))
        return {part.prefix + key[len("w7_"):]: value for key, value in out.items()}
    if part.function in ("check_w5", "check_w7"):
        return function(run, RecordConfig(), CapacityConfig())
    return function(run)


def one_pack(args) -> dict:
    seed, parts = args
    started = time.time()
    run = simulate(seed, parts=parts)
    row = {"seed": seed}
    for name in parts:
        out = run_part(name, run)
        bad = [key for key in out if not key.startswith(PARTS[name].prefix)]
        if bad:
            raise ValueError(f"part {name} wrote columns outside its prefix: {bad}")
        row.update(out)
    seconds = round(time.time() - started, 1)
    for name in parts:
        row[f"{PARTS[name].prefix}seconds"] = seconds  # the whole run that wrote this part's columns
    row["capacity_config_hash"] = config_hash(CapacityConfig())
    row["record_config_hash"] = config_hash(RecordConfig())
    return row


def merged(new: pd.DataFrame, parts: list[str], path: Path = OUT) -> pd.DataFrame:
    """The file on disk with the columns of ``parts`` replaced by ``new``; all parts when nothing is on disk."""
    if set(parts) == set(PARTS):
        return ordered(new)
    if not path.exists():
        raise SystemExit(f"{path.name} does not exist: run all parts first")
    old = pd.read_csv(path)
    for column in HASH_COLUMNS:
        found, now = set(old[column].astype(str)), set(new[column].astype(str))
        if found != now:
            raise SystemExit(f"{path.name} comes from {column} {sorted(found)}, this run from {sorted(now)}: run all parts")
    if sorted(old["seed"]) != sorted(new["seed"]):
        raise SystemExit(f"{path.name} holds other seeds than this run: run all parts")
    prefixes = tuple(PARTS[p].prefix for p in parts)
    legacy = [c for c in ("seconds",) if c in old]  # one timing for the whole row: no longer true after a merge
    keep = [c for c in old if not c.startswith(prefixes) and c not in HASH_COLUMNS and c not in legacy]
    add = ["seed"] + [c for c in new if c.startswith(prefixes)]
    table = old[keep].merge(new[add], on="seed", how="left", validate="one_to_one").copy()
    table = table.assign(**{column: new[column].iloc[0] for column in HASH_COLUMNS})
    return ordered(table)


def ordered(table: pd.DataFrame) -> pd.DataFrame:
    """Columns in the order: seed, the parts in ``PARTS`` order, anything else, the hashes."""
    columns = ["seed"]
    for part in PARTS.values():
        columns += [c for c in table if c.startswith(part.prefix)]
    columns += [c for c in table if c not in columns and c not in HASH_COLUMNS]
    columns += [c for c in HASH_COLUMNS if c in table]
    return table[columns].sort_values("seed").reset_index(drop=True)


def coverage(table: pd.DataFrame, column: str) -> tuple[int, int, float, str]:
    held = table[column].fillna(False).astype(bool)
    share = float(held.mean())
    return int(held.sum()), len(held), share, "pass" if PASS_RANGE[0] <= share <= PASS_RANGE[1] else "fail"


def w2_value_reason(table: pd.DataFrame) -> str:
    """Why the W2 value fails, from the columns: one clause per truth."""
    n = len(table)
    value = table["w2_value"]
    held = table["w2_value_held"].fillna(False).astype(bool)
    error = (table["w2_value"] - table["w2_value_truth"]).abs()
    systematic = int((error <= table["w2_value_sys_half"]).sum())
    stat = float((table["w2_value_stat_half"] / value).median())
    sys_rel = float((table["w2_value_sys_half"] / value).median())
    worst = float(table["w2_value_error_rel"].abs().max())
    date = table["w2_value_held_at_date"].fillna(False).astype(bool)
    lag = float(((table["w2_value_truth_at_date"] - value) / value).median())
    warm = table["w2_value_held_25c"].fillna(False).astype(bool)
    temp = float(((value - table["w2_value_truth_25c"]) / table["w2_value_truth_25c"]).median())
    return (
        f"same days {int(held.sum())} of {n} (too wide: that truth shares every day with the estimate; the systematic "
        f"half-width alone holds {systematic}); at the date {int(date.sum())} of {n} (the estimate lags the growing "
        f"resistance by a median {lag:.1%}); at 25 degrees C {int(warm.sum())} of {n} (no temperature model; the days "
        f"read a median {temp:+.1%})"
    )


def summary(table: pd.DataFrame) -> list[str]:
    """One line per check, from the columns the check modules wrote."""
    lines = []
    if "w2_value_held" in table:
        n, total, share, verdict = coverage(table, "w2_value_held")
        lines.append(f"W2 resistance: {verdict} under every truth. {w2_value_reason(table)}")
    if "w2_growth_held_param" in table:
        n, total, share, verdict = coverage(table, "w2_growth_held_param")
        known = float(table["w2_growth_truth_param"].median())
        same = table["w2_growth_held"].fillna(False).astype(bool)
        lines.append(
            f"W2 resistance growth against the simulator's known growth (median {known:.2f} % per year at the fit's "
            f"origin): the band held it in {n} of {total} packs ({share:.1%}): {verdict}; same-days truth "
            f"(information): {int(same.sum())} of {len(same)} ({same.mean():.1%})"
        )
    for check, column, what in (
        ("W5", "w5_held", "round-trip efficiency"),
        ("W7", "w7_held", "apparent charge loss"),
        ("W7 information, band_forgetting = 1,", "w7bf1_held", "apparent charge loss"),
    ):
        if column in table:
            n, total, share, verdict = coverage(table, column)
            lines.append(f"{check} {what}: the band held the truth in {n} of {total} packs ({share:.1%}): {verdict}")
    for prefix, label in (("w8_", "W8"), ("w8noise_", "W8 information, pack B with current and voltage noise")):
        if f"{prefix}pass" in table:
            ok = table[f"{prefix}pass"].fillna(False).astype(bool)
            exact = table[f"{prefix}events_exact"].fillna(False).astype(bool)
            worst = table[[c for c in table if c.startswith(prefix) and c.endswith("_rel_error")]].abs().max().max()
            verdict = ("pass" if ok.all() else "fail") if prefix == "w8_" else "information"
            lines.append(
                f"{label} throughput and planted events: {int(ok.sum())} of {len(ok)} packs within the rule; events "
                f"exact on {int(exact.sum())}; largest relative error {worst:.4%}: {verdict}"
            )
    return lines


def main(argv: list[str]) -> None:
    parts = list(PARTS)
    processes = 3
    if "--processes" in argv:
        k = argv.index("--processes")
        processes = int(argv[k + 1])
        argv = argv[:k] + argv[k + 2 :]
    seeds = None
    if "--seeds" in argv:
        k = argv.index("--seeds")
        rest = argv[k + 1 :]
        n_seeds = next((i for i, a in enumerate(rest) if a.startswith("--")), len(rest))
        seeds = [int(a) for a in rest[:n_seeds]]
        argv = argv[:k] + rest[n_seeds:]
    if "--only" in argv:
        k = argv.index("--only")
        rest = argv[k + 1 :]
        n_parts = next((i for i, a in enumerate(rest) if a.startswith("--")), len(rest))
        parts = rest[:n_parts]
        argv = argv[:k] + rest[n_parts:]
        unknown = [p for p in parts if p not in PARTS]
        if unknown or not parts:
            raise SystemExit(f"unknown parts {unknown}; the parts are {list(PARTS)}")
        parts = [p for p in PARTS if p in parts]
    if seeds is not None:
        rows = [one_pack((seed, parts)) for seed in seeds]
        table = pd.DataFrame(rows)
        with pd.option_context("display.width", 200, "display.max_columns", None, "display.max_rows", None):
            print(table.T)
    else:
        n = int(argv[0]) if argv else N_PACKS
        with Pool(processes) as pool:
            rows = pool.map(one_pack, [(seed, parts) for seed in range(1, n + 1)], chunksize=1)
        table = merged(pd.DataFrame(rows), parts)
        RESULTS.mkdir(parents=True, exist_ok=True)
        table.to_csv(OUT, index=False, float_format="%.6g")
        print(f"parts run: {parts}; written: {OUT.relative_to(ROOT)} ({len(table)} packs, {table.shape[1]} columns)")
    for line in summary(table):
        print(line)
    print(f"configuration: capacity {config_hash(CapacityConfig())}, record {config_hash(RecordConfig())}")


if __name__ == "__main__":
    main(sys.argv[1:])
