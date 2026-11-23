"""Check V6: on synthetic packs with a known fade, does the 90 % band hold the truth?

200 packs (``tests/synth.py``), each 36 months at a 10-second step, each with its own
random operation, sensor gain error and BMS stop errors. The first year of each record
only sets the full and empty levels (``CapacityConfig.level_warmup_days``); the fade is
measured on the two years after it, which keeps the span of the fade fit above
``fade_min_span_years``. Their capacity (state of charge
0 to 1) fades by 2 points per year. Their BMS stops at voltage limits, so the *usable*
capacity, which the estimator measures, is a few points smaller and fades a little
slower. The truth of the check is the expected usable fade: the mean, over the 200
packs, of each pack's true usable fade from the simulator's own state of charge at its
stops (``synth.true_usable_fade``). The full chain runs on each pack: rest detection,
classification, pairing and the fade rate, with the frozen settings.

Pass: the 90 % band holds the truth in 85-95 % of packs. Also reported: coverage of each
pack's own realised usable fade, and of the 2-point capacity fade.

Information, chosen after the first results (version 0.2): with the voltage terms the
rate is that of the capacity between two fixed rest voltages; its truth is the 2-point
capacity fade times the state-of-charge window between the pack's mean full and mean
empty rest voltage on the simulator's own open-circuit voltage curve
(``window_fade_points_per_year``, ``held_window``).

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

import numpy as np  # noqa: E402

import synth  # noqa: E402
from synth import SynthPack, frames, true_usable_fade  # noqa: E402

from besslib.soh.capacity import fade_rate  # noqa: E402
from besslib.soh.config import CapacityConfig, config_hash  # noqa: E402
from besslib.soh.rests import classify_rests, detect_rests_in_parts, rest_chain, rest_pairs  # noqa: E402

RESULTS = ROOT / "validation" / "results"
MONTHS = 36  # one year of level warm-up, then two years of pairs
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
    kind = pairs["kind"].to_numpy()
    starts, ends = np.array([k[0] for k in kind]), np.array([k[-1] for k in kind])
    u = {f: np.r_[pairs["u_start"].to_numpy()[starts == f], pairs["u_end"].to_numpy()[ends == f]] for f in ("F", "E")}
    soc = np.interp(np.array([u["F"].mean(), u["E"].mean()]) / pack.cells_in_series, synth._OCV_V, synth._OCV_SOC)
    return {
        "seed": seed,
        "capacity_fade_points_per_year": truth,
        "usable_fade_points_per_year": true_usable_fade(pack, MONTHS, seed),
        "window_fade_points_per_year": truth * float(soc[0] - soc[1]),
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
    table["held_window"] = (table["low"] <= table["window_fade_points_per_year"]) & (
        table["window_fade_points_per_year"] <= table["high"]
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
    print(
        f"Information (chosen after the first results): the window fade (mean {table['window_fade_points_per_year'].mean():.3f}) "
        f"held in {int(table['held_window'].sum())} of {len(table)} packs ({table['held_window'].mean():.1%})"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
