"""Why check T3a (A4 on the failing-cell case) moved with version 0.3: a diagnosis, not a check.

The leak case (``sim_case_leak``, settings leak0, leak5, leak15 and leak50) on the development seeds
0-59 only. Each pack is simulated once; A4 (``besslib.soh.charge_loss``) is computed on the same
reduced tables under four settings, which separate the two changes of version 0.3 that move a value:

    v02        ``CapacityConfig().v0_2()``                    release 0.2.0
    v02_read   0.2 with ``rest_time = "reading"``            0.2 + the reading times
    v03        ``CapacityConfig()``                           version 0.3 (quadratic terms + reading times)
    v03_start  0.3 with ``rest_time = "start"``               0.3 without the reading times

(Rules A and B and the other 0.3 settings refuse a month or not; they do not move a value.)

At every month end with a band, before the onset or from 180 days after it (as T3a): the band, the
true apparent charge loss of the last month (T3a's truth) and its memory-weighted value.
Writes ``validation/results/diag_a4_band.csv`` (one row per pack, setting and month end) and prints,
per case setting, part and setting: the share held, the median error and the median half-width.

Usage:
    python validation/diag_a4_band.py            # 240 packs, 3 processes (about 3 core-hours)
    python validation/diag_a4_band.py --summary  # the summary of the file on disk
"""

from __future__ import annotations

import dataclasses
import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "results" / "diag_a4_band.csv"
CASE_SETTINGS = ("leak0", "leak5", "leak15", "leak50")
SEEDS = range(0, 60)  # development seeds only
NAMES = ("v02", "v02_read", "v03", "v03_start")


def settings() -> dict:
    from besslib.soh.config import CapacityConfig

    c3 = CapacityConfig()
    c2 = c3.v0_2()
    return {"v02": c2, "v02_read": dataclasses.replace(c2, rest_time="reading"), "v03": c3,
            "v03_start": dataclasses.replace(c3, rest_time="start")}


def one_pack(job: tuple[str, int]) -> list[dict]:
    """The month-end rows of one pack under the four settings."""
    import sim_case_common as common
    import sim_case_leak as leak
    import sim_truth
    from besslib import sim
    from besslib.soh.charge_loss import charge_loss
    from besslib.soh.config import config_hash

    setting, seed = job
    rec = common.recorder(leak.CASE, leak.SETTINGS, setting, seed)
    event = sim.events(rec.data, seed)[0]
    onset = rec.start + pd.Timedelta(days=int(event["day"]))
    reduced = sim_truth.reduce_pack(rec)
    truth = leak.true_loss_ma(rec, event["group"] if event["leak_ohm"] is not None else None)
    rows = []
    for name, cfg in settings().items():
        loss = charge_loss(common.capacity_table(reduced, rec.spec, rec.start, rec.months, cfg), rec.spec)
        for time, r in loss.iterrows():
            part = "before" if time <= onset else ("after" if time >= onset + pd.Timedelta(days=leak.MEMORY_DAYS) else None)
            t = truth[truth["t_end"] <= time + leak.MONTH_END]
            if part is None or not len(t) or r["status"] != sim_truth.OK:
                continue
            if not (math.isfinite(r["charge_loss_ma_low"]) and math.isfinite(r["charge_loss_ma_high"])):
                continue
            rows.append({"case_setting": setting, "seed": seed, "setting": name, "time": time, "part": part,
                         "value_ma": r["charge_loss_ma"], "low_ma": r["charge_loss_ma_low"], "high_ma": r["charge_loss_ma_high"],
                         "truth_ma": float(t["loss_ma"].iloc[-1]), "memory_truth_ma": leak._memory_weighted(truth, time),
                         "config_hash": config_hash(cfg)})
    return rows


def summary(table: pd.DataFrame) -> pd.DataFrame:
    t = table.copy()
    t["held"] = (t["low_ma"] <= t["truth_ma"]) & (t["truth_ma"] <= t["high_ma"])
    t["held_memory"] = (t["low_ma"] <= t["memory_truth_ma"]) & (t["memory_truth_ma"] <= t["high_ma"])
    t["error"] = t["value_ma"] - t["truth_ma"]
    t["half"] = (t["high_ma"] - t["low_ma"]) / 2.0
    g = t.groupby(["case_setting", "part", "setting"], sort=True)
    out = g.agg(n=("held", "size"), held=("held", "sum"), held_memory=("held_memory", "sum"),
                median_error_ma=("error", "median"), median_half_ma=("half", "median")).reset_index()
    out["held_share"] = out["held"] / out["n"]
    pooled = t.groupby(["case_setting", "setting"]).agg(n=("held", "size"), held=("held", "sum")).reset_index()
    pooled["part"] = "both"
    pooled["held_share"] = pooled["held"] / pooled["n"]
    return pd.concat([out, pooled], ignore_index=True)


def main(argv: list[str]) -> None:
    if "--summary" not in argv:
        jobs = [(s, k) for s in CASE_SETTINGS for k in SEEDS]
        with Pool(3) as pool:
            parts = pool.map(one_pack, jobs, chunksize=1)
        table = pd.DataFrame([r for p in parts for r in p])
        table.to_csv(OUT, index=False, float_format="%.6g")
        print(f"written: {OUT.relative_to(ROOT)} ({len(table)} rows, {len(jobs)} packs)")
    table = pd.read_csv(OUT, parse_dates=["time"])
    with pd.option_context("display.width", 200, "display.max_rows", None):
        print(summary(table).round(3).to_string(index=False))


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "tests")]
    main(sys.argv[1:])
