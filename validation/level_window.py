"""Phase 2: the window of the end-of-charge and end-of-discharge levels, without any test value.

A rest is full (empty) when its edge voltage is near the system's end-of-charge
(end-of-discharge) level, a high (low) quantile of the edge voltages in a trailing
window. A home storage system is rarely emptied in summer and rarely filled in winter.
If the window is shorter than a year, its level follows the season into the normal
operating range: a shallow summer stop is then called empty, a partial winter charge
full, and those pairs carry too little charge. This script compares the 90-day window
with the one-year window on the development systems, each with a warm-up equal to its
window (no level before one window of history), and the earlier setting (90 days, no
warm-up) for reference.

The numbers are test-free: no measured test capacity is read. The test windows are
masked as in ``tune_capacity.py``, from their start and end times only.

Per system and setting:

* ``seasonal_amplitude_points``: the amplitude of a yearly sine fitted, together with a
  straight line, to the monthly estimates (in points of the nominal capacity);
* ``cross_pairs_per_year``: full-to-empty and empty-to-full pairs per year of pairs;
* ``fade_birge``: the Birge ratio (reduced chi-square, at least 1) of the fade fit;
* ``fade_points_per_year`` with its band;
* ``per_pair_capacity_sd_points``: the scatter of the per-pair capacity
  ``x (q - beta dt)`` of the cross pairs, with ``beta`` from the fade fit;
* ``share_below_70pct``: the share of cross pairs whose per-pair capacity is below 70 %
  of the median per-pair capacity of the system under that setting;
* ``months_estimated``: month ends with an estimate, out of ``record_months``, the month
  ends between the first and the last rest of the record.

Reads ``data/reduced/rests/`` and the start and end times in
``validation/results/capacity_tests.csv``. Writes ``validation/results/level_window.csv``.

Usage:
    python validation/level_window.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.capacity import estimate_capacity, fade_rate
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import classify_rests, levels_from, rest_chain, rest_pairs

from reduce_events import rest_file
from tune_capacity import DEVELOPMENT, masks_for

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
SETTINGS = (  # (name, level window in days, warm-up in days)
    ("90 d, no warm-up", 90.0, 0.0),
    ("90 d", 90.0, 90.0),
    ("365 d", 365.0, 365.0),
)
LOW_SHARE = 0.70


def development_test_times() -> pd.DataFrame:
    """The development tests' start and end times only (the masks need nothing else)."""
    columns = ["system_id", "set", "documented_start", "onset", "end"]
    tests = pd.read_csv(RESULTS / "capacity_tests.csv", usecols=columns, parse_dates=columns[2:])
    return tests[tests["set"] == "development"]


def seasonal_amplitude(monthly: pd.DataFrame, nominal: float) -> float:
    est = monthly.loc[monthly["status"] == "estimated", "capacity_ah"].dropna()
    if len(est) < 13:
        return np.nan
    years = (est.index - est.index[0]).days.to_numpy() / 365.25
    phase = 2.0 * np.pi * (est.index.day_of_year.to_numpy() - 1) / 365.25
    design = np.column_stack([np.ones_like(years), years, np.sin(phase), np.cos(phase)])
    coef, *_ = np.linalg.lstsq(design, est.to_numpy(), rcond=None)
    return float(np.hypot(coef[2], coef[3]) / nominal * 100.0)


def one_system(system_id: int, config: CapacityConfig, tests: pd.DataFrame) -> dict:
    spec = figgener.pack_spec(system_id)
    nominal = spec.nominal_capacity_ah
    cands = pd.read_parquet(rest_file(system_id, config.current_c_rate))
    cands = cands[cands["duration_min"] >= config.min_duration_min]
    mask = masks_for(tests, system_id)
    inside = np.zeros(len(cands), dtype=bool)
    for a, b in mask:
        inside |= ((cands["start"] >= a) & (cands["start"] <= b)).to_numpy()
    rests = classify_rests(cands[~inside], spec, config)
    pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    monthly = estimate_capacity(None, spec, config=config, pairs=pairs, levels_from=levels_from(rests, config))
    fade = fade_rate(pairs, spec, config)
    cross = pairs[pairs["x"] != 0]
    years = (pairs["t_end"].max() - pairs["t_start"].min()).days / 365.25 if len(pairs) else np.nan
    beta = -fade.get("offset_current_a", np.nan) * 24.0
    per_pair = cross["x"] * (cross["q_ah"] - beta * cross["dt_days"])
    median = float(per_pair.median()) if len(per_pair) else np.nan
    return {
        "system_id": system_id,
        "chemistry": spec.chemistry,
        "seasonal_amplitude_points": seasonal_amplitude(monthly, nominal),
        "cross_pairs": int(len(cross)),
        "cross_pairs_per_year": len(cross) / years if years and years > 0 else np.nan,
        "same_kind_pairs": int((pairs["x"] == 0).sum()),
        "fade_status": fade["status"],
        "fade_points_per_year": fade.get("rate_points_per_year", np.nan),
        "fade_points_per_year_low": fade.get("rate_points_per_year_low", np.nan),
        "fade_points_per_year_high": fade.get("rate_points_per_year_high", np.nan),
        "fade_birge": fade.get("birge", np.nan),
        "offset_current_a": fade.get("offset_current_a", np.nan),
        "per_pair_capacity_sd_points": float(per_pair.std() / nominal * 100.0) if len(per_pair) > 1 else np.nan,
        "share_below_70pct": float((per_pair < LOW_SHARE * median).mean()) if len(per_pair) else np.nan,
        "months_estimated": int((monthly["status"] == "estimated").sum()),
        "record_months": int(len(pd.date_range(cands["start"].min(), cands["start"].max(), freq="ME"))),
    }


def main() -> None:
    tests = development_test_times()
    assert sorted(tests["system_id"].unique()) == sorted(DEVELOPMENT), "development split changed"
    rows = []
    for name, window, warmup in SETTINGS:
        config = CapacityConfig(level_window_days=window, level_warmup_days=warmup)
        for sid in DEVELOPMENT:
            row = {"setting": name, "level_window_days": window, "level_warmup_days": warmup,
                   "config_hash": config_hash(config)}
            row.update(one_system(sid, config, tests))
            rows.append(row)
            print(
                f"{name:>16} system {sid:02d}: amplitude {row['seasonal_amplitude_points']:.2f} pts, "
                f"{row['cross_pairs_per_year']:.0f} cross pairs/year, Birge {row['fade_birge']:.2f}, "
                f"fade {row['fade_points_per_year']:+.2f} [{row['fade_points_per_year_low']:+.2f}, "
                f"{row['fade_points_per_year_high']:+.2f}] pts/year, below 70 %: {row['share_below_70pct']:.1%}, "
                f"{row['months_estimated']}/{row['record_months']} months",
                flush=True,
            )
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULTS / "level_window.csv", index=False, float_format="%.4f")


if __name__ == "__main__":
    main()
