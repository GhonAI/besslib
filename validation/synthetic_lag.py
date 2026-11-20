"""Known limit: the monthly capacity band leaves out the lag of a fading capacity.

The estimate at a month end is causal: it weighs the pairs of rests before it with the
memory time ``tau_days``, so on a fading pack it measures the capacity of the past. The
band is a band on that weighted value; it does not include the lag. This script measures
how often the band holds the true usable capacity

* at the date of the estimate, and
* at the weighted mean age of the pairs in memory.

The 200 packs, their seeds and the frozen configuration are those of
``validation/synthetic_coverage.py`` (check V6), 36 months each.

Definitions (all from the simulator's own state of charge, without sensor errors):

* The true usable capacity is the charge between a BMS stop at full and the next stop
  at empty (or the reverse), ``|delta soc| x mean capacity`` (``tests/synth.py``,
  ``true_stops``; repeated stops of one kind are dropped as in ``true_usable_fade``).
  Its value at a time is the least-squares line through these values over the whole
  record, taken at that time (the same line whose slope is the V6 truth).
* The weighted mean age of the pairs is the mean time of the pairs in memory at the
  month end, weighted as the estimator weighs them: ``exp(-(t - t_pair) / tau)`` times
  the inverse variance of the pair. Only full-to-empty and empty-to-full pairs are used,
  because only they carry the capacity. A pair's time is the middle of its two rests.
  The truth at the mean age is the line at ``t - age``.
* The lag is the estimate minus the truth at the date, in points of nominal capacity.

Writes ``validation/results/synthetic_lag.csv`` (one row per pack).

Usage:
    python validation/synthetic_lag.py            # 200 packs, 3 processes
    python validation/synthetic_lag.py 6          # fewer packs, printed only
"""

from __future__ import annotations

import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from synth import SynthPack, frames, true_stops  # noqa: E402

from besslib.soh.capacity import estimate_capacity  # noqa: E402
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash  # noqa: E402
from besslib.soh.rests import classify_rests, detect_rests_in_parts, rest_chain, rest_pairs  # noqa: E402

RESULTS = ROOT / "validation" / "results"
MONTHS = 36
STEP_S = 10
YEAR_S = 365.25 * 86400.0


def usable_line(pack: SynthPack, seed: int) -> np.ndarray:
    """Slope and intercept (Ah per year, Ah) of the true usable capacity against time in years."""
    stops = true_stops(pack, MONTHS, seed)
    kept = stops[stops["first"]]
    t = kept["seconds"].to_numpy(float) / YEAR_S
    soc = kept["soc"].to_numpy(float)
    cap = kept["capacity_ah"].to_numpy(float)
    usable = np.abs(np.diff(soc)) * 0.5 * (cap[1:] + cap[:-1])
    return np.polyfit(0.5 * (t[1:] + t[:-1]), usable, 1)


def one_pack(seed: int) -> dict:
    pack = SynthPack()
    spec = pack.spec()
    config = CapacityConfig()
    candidates = detect_rests_in_parts(frames(pack, MONTHS, step_s=STEP_S, seed=seed), spec, config)
    rests = classify_rests(candidates, spec, config)
    pairs = rest_pairs(rest_chain(rests, spec, config), config)
    est = estimate_capacity(None, spec, rests=rests, config=config, pairs=pairs)
    line = usable_line(pack, seed)
    t0 = pd.Timestamp(pack.start)

    cross = pairs[pairs["x"] != 0]
    start_s = (cross["t_start"] - t0).dt.total_seconds().to_numpy(float)
    end_s = (cross["t_end"] - t0).dt.total_seconds().to_numpy(float)
    t_pair = 0.5 * (start_s + end_s) / YEAR_S
    weight = 1.0 / (cross["sigma_q_ah"].to_numpy(float) ** 2 + (spec.nominal_capacity_ah * cross["sigma_x"].to_numpy(float)) ** 2)
    t_end = end_s / YEAR_S

    n_band = n_date = n_age = 0
    lags = []
    ages = []
    for time, row in est.iterrows():
        if not (np.isfinite(row["capacity_ah_low"]) and np.isfinite(row["capacity_ah_high"])):
            continue
        t = (time - t0).total_seconds() / YEAR_S
        keep = t_end <= t
        w = weight[keep] * np.exp(-(t - t_end[keep]) * YEAR_S / 86400.0 / config.tau_days)
        age = t - float((w * t_pair[keep]).sum() / w.sum())
        truth_date = np.polyval(line, t)
        truth_age = np.polyval(line, t - age)
        n_band += 1
        n_date += int(row["capacity_ah_low"] <= truth_date <= row["capacity_ah_high"])
        n_age += int(row["capacity_ah_low"] <= truth_age <= row["capacity_ah_high"])
        lags.append((row["capacity_ah"] - truth_date) / spec.nominal_capacity_ah * 100.0)
        ages.append(age * 365.25)
    return {
        "seed": seed,
        "n_band": n_band,
        "n_held_at_date": n_date,
        "n_held_at_mean_age": n_age,
        "mean_lag_points": float(np.mean(lags)) if lags else float("nan"),
        "mean_age_days": float(np.mean(ages)) if ages else float("nan"),
        "capacity_config_hash": config_hash(config),
        "record_config_hash": config_hash(RecordConfig()),
    }


def main(argv: list[str]) -> None:
    n = int(argv[0]) if argv else 200
    with Pool(3) as pool:
        rows = pool.map(one_pack, range(1, n + 1))
    table = pd.DataFrame(rows)
    total = int(table["n_band"].sum())
    at_date = int(table["n_held_at_date"].sum())
    at_age = int(table["n_held_at_mean_age"].sum())
    if len(argv) == 0:
        table.to_csv(RESULTS / "synthetic_lag.csv", index=False, float_format="%.5f")
    print(
        f"Lag: {total} month ends with a band in {len(table)} packs; the band held the true usable capacity "
        f"at the date in {at_date} ({at_date / total:.1%}) and at the weighted mean age of the pairs in "
        f"{at_age} ({at_age / total:.1%}); mean lag {table['mean_lag_points'].mean():+.2f} points, "
        f"mean age {table['mean_age_days'].mean():.0f} days (capacity {table['capacity_config_hash'].iloc[0]}, record {table['record_config_hash'].iloc[0]})"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
