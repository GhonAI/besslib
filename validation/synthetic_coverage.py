"""Check V6: on synthetic packs with a known fade, does the 90 % band hold the truth?

200 packs (``tests/synth.py``), each 24 months at a 10-second step, each with its own
random operation, sensor gain error and BMS stop errors. Their capacity (state of charge
0 to 1) fades by 2 points per year. Their BMS stops at voltage limits, so the *usable*
capacity, which the estimator measures, is a few points smaller and fades a little
slower. The truth of the check is the expected usable fade: the mean, over the 200
packs, of each pack's true usable fade from the simulator's own state of charge at its
stops (``synth.true_usable_fade``). The full chain runs on each pack: rest detection,
classification, pairing and the fade rate, with the frozen settings.

Pass: the 90 % band holds the truth in 85-95 % of packs. Also reported: coverage of each
pack's own realised usable fade, and of the 2-point capacity fade.

Writes ``validation/results/synthetic_coverage.csv`` (one row per pack).

Usage:
    python validation/synthetic_coverage.py            # 200 packs, 3 processes
    python validation/synthetic_coverage.py 20         # fewer packs
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from synth import SynthPack, frames, true_usable_fade  # noqa: E402

from besslib.soh.capacity import fade_rate  # noqa: E402
from besslib.soh.config import CapacityConfig, config_hash  # noqa: E402
from besslib.soh.rests import classify_rests, detect_rests_in_parts, rest_chain, rest_pairs  # noqa: E402

RESULTS = ROOT / "validation" / "results"
MONTHS = 24
STEP_S = 10
PASS_RANGE = (0.85, 0.95)


def one_pack(seed: int) -> dict:
    pack = SynthPack()
    spec = pack.spec()
    config = CapacityConfig()
    candidates = detect_rests_in_parts(frames(pack, MONTHS, step_s=STEP_S, seed=seed), spec, config)
    pairs = rest_pairs(rest_chain(classify_rests(candidates, spec, config), spec, config), config)
    out = fade_rate(pairs, spec, config)
    truth = -pack.fade_points_per_year
    return {
        "seed": seed,
        "capacity_fade_points_per_year": truth,
        "usable_fade_points_per_year": true_usable_fade(pack, MONTHS, seed),
        "rate_points_per_year": out.get("rate_points_per_year"),
        "low": out.get("rate_points_per_year_low"),
        "high": out.get("rate_points_per_year_high"),
        "t_low": out.get("rate_t_band_low"),
        "t_high": out.get("rate_t_band_high"),
        "boot_low": out.get("rate_boot_low"),
        "boot_high": out.get("rate_boot_high"),
        "offset_current_a": out.get("offset_current_a"),
        "true_offset_current_a": pack.hidden_current_a,
        "n_pairs": out.get("n_pairs"),
        "rho": out.get("rho"),
        "birge": out.get("birge"),
        "status": out["status"],
        "config_hash": config_hash(config),
    }


def main(argv: list[str]) -> None:
    n = int(argv[0]) if argv else 200
    with Pool(3) as pool:
        rows = pool.map(one_pack, range(1, n + 1))
    table = pd.DataFrame(rows)
    truth = float(table["usable_fade_points_per_year"].mean())
    table["truth_points_per_year"] = truth
    table["held"] = (table["low"] <= truth) & (truth <= table["high"])
    table["held_own_usable"] = (table["low"] <= table["usable_fade_points_per_year"]) & (
        table["usable_fade_points_per_year"] <= table["high"]
    )
    table["held_capacity_fade"] = (table["low"] <= table["capacity_fade_points_per_year"]) & (
        table["capacity_fade_points_per_year"] <= table["high"]
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "synthetic_coverage.csv", index=False, float_format="%.5f")
    share = table["held"].mean()
    verdict = "pass" if PASS_RANGE[0] <= share <= PASS_RANGE[1] else "fail"
    print(
        f"V6: truth {truth:.3f} points/year (expected usable fade); the band held it in "
        f"{int(table['held'].sum())} of {len(table)} packs ({share:.1%}): {verdict}. "
        f"Mean estimate {table['rate_points_per_year'].mean():.3f}; own usable fade held in "
        f"{table['held_own_usable'].mean():.1%}; capacity fade -2.0 held in {table['held_capacity_fade'].mean():.1%} "
        f"(config {table['config_hash'].iloc[0]})"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
