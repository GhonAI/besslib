"""Why check V6 (the fade rate's band on synthetic packs) fell with version 0.3: a diagnosis, not a check.

The 200 synthetic packs of check V6 (``synthetic_coverage.py``, the same seeds). Each pack is
simulated once; the fade rate (``besslib.soh.capacity.fade_rate``) is computed on the same rests
under five settings, which separate the two changes of version 0.3 that move a value:

    v02        ``CapacityConfig().v0_2()``                     release 0.2.0
    v02_read   0.2 with ``rest_time = "reading"``             0.2 + the reading times
    v03        ``CapacityConfig()``                            version 0.3 (quadratic terms + reading times)
    v03_start  0.3 with ``rest_time = "start"``                0.3 without the reading times
    v03_lin    0.3 with ``voltage_terms = "every_pair"``       0.3 without the quadratic terms

The truth is V6's: the mean true usable fade over the packs. Writes
``validation/results/diag_fade_band.csv`` (one row per pack and setting) and prints, per setting, the
share of packs whose band holds the truth, the median half-width and the mean error.

Usage:
    python validation/diag_fade_band.py            # 200 packs, 3 processes (about 1 core-hour)
    python validation/diag_fade_band.py --summary  # the summary of the file on disk
"""

from __future__ import annotations

import dataclasses
import sys
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "results" / "diag_fade_band.csv"


def settings() -> dict:
    from besslib.soh.config import CapacityConfig

    c3 = CapacityConfig()
    c2 = c3.v0_2()
    return {"v02": c2, "v02_read": dataclasses.replace(c2, rest_time="reading"), "v03": c3,
            "v03_start": dataclasses.replace(c3, rest_time="start"), "v03_lin": dataclasses.replace(c3, voltage_terms="every_pair")}


def one_pack(seed: int) -> list[dict]:
    import synthetic_coverage as sc
    from besslib.soh.capacity import fade_rate
    from besslib.soh.config import CapacityConfig, config_hash
    from besslib.soh.rests import classify_rests, detect_rests_in_parts, rest_chain, rest_pairs

    pack = sc.SynthPack()
    spec = pack.spec()
    candidates = detect_rests_in_parts(sc.frames(pack, sc.MONTHS, step_s=sc.STEP_S, seed=seed), spec, CapacityConfig())
    usable = sc.true_usable_fade(pack, sc.MONTHS, seed)
    rows = []
    for name, cfg in settings().items():
        pairs = rest_pairs(rest_chain(classify_rests(candidates, spec, cfg), spec, cfg), cfg)
        out = fade_rate(pairs, spec, cfg)
        rows.append({"seed": seed, "setting": name, "usable_fade_points_per_year": usable,
                     "rate_points_per_year": out.get("rate_points_per_year"), "low": out.get("rate_points_per_year_low"),
                     "high": out.get("rate_points_per_year_high"), "status": out["status"], "config_hash": config_hash(cfg)})
    return rows


def summary(table: pd.DataFrame) -> pd.DataFrame:
    t = table.copy()
    truth = float(t.groupby("seed")["usable_fade_points_per_year"].first().mean())
    t["held"] = (t["low"] <= truth) & (truth <= t["high"])
    t["half"] = (t["high"] - t["low"]) / 2.0
    t["error"] = t["rate_points_per_year"] - truth
    out = t.groupby("setting", sort=False).agg(n=("held", "size"), held=("held", "sum"), median_half=("half", "median"),
                                                mean_error=("error", "mean")).reset_index()
    out["held_share"] = out["held"] / out["n"]
    out["truth_points_per_year"] = truth
    return out


def main(argv: list[str]) -> None:
    if "--summary" not in argv:
        with Pool(3) as pool:
            parts = pool.map(one_pack, range(1, 201), chunksize=1)  # V6's seeds
        table = pd.DataFrame([r for p in parts for r in p])
        table.to_csv(OUT, index=False, float_format="%.6g")
        print(f"written: {OUT.relative_to(ROOT)} ({len(table)} rows)")
    table = pd.read_csv(OUT)
    with pd.option_context("display.width", 200):
        print(summary(table).round(4).to_string(index=False))


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "tests")]
    main(sys.argv[1:])
