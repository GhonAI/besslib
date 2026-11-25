"""What the case modules ``sim_case_<name>.py`` share: one pack's recorder, its estimates and the truth's helpers.

A case module has ``CASE`` (a case file), ``SETTINGS`` (name -> overrides, months), ``run_pack(setting, seed)``
(the result rows of one pack: floats, ints, strings and bools only, no file written, no global state),
``RESULT`` (the results file under ``validation/results/sim/``) and ``summary(table)`` (the check rows).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_truth

MONTH_END = pd.Timedelta(0)


def base_row(setting: str, seed: int) -> dict:
    return {"setting": setting, "seed": int(seed), **sim_truth.tags()}


def recorder(case: str, settings: dict, setting: str, seed: int, signals: str = "measured", on_month=None):
    cfg = settings[setting]
    return sim_truth.Recorder(cfg.get("case", case), cfg["months"], seed, cfg.get("overrides"), signals=signals,
                              on_month=on_month)


def seed_set(table: pd.DataFrame) -> str:
    return str(table["seed_set"].iloc[0]) if len(table) and "seed_set" in table.columns else ""


def by_setting(table: pd.DataFrame, order) -> list[tuple[str, pd.DataFrame]]:
    return [(s, table[table["setting"] == s]) for s in order if (table["setting"] == s).any()]


def fraction(held: float, n: float) -> float:
    return held / n if n else math.nan


def info_row(check: str, what: str, result: str) -> dict:
    return {"check": check, "what": what, "result": result, "pass_if": "information", "passed": ""}


def band_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """The rows of an estimate table that carry a band."""
    ok = rows.apply(sim_truth.has_band, axis=1) if len(rows) else pd.Series(dtype=bool)
    return rows[ok.to_numpy(dtype=bool)] if len(rows) else rows


def month_end_hours(balance: pd.DataFrame) -> pd.DataFrame:
    """The recorder's balance table, one row per month and group, with the month's end time."""
    out = balance.copy()
    out["t_end"] = out["t_start"] + pd.to_timedelta(out["hours"], unit="h")
    return out


def pass_range_text() -> str:
    lo, hi = sim_truth.PASS_RANGE
    return f"{lo:.0%}-{hi:.0%} of month ends with a band"


def capacity_table(reduced, spec, start: pd.Timestamp, months: int) -> pd.DataFrame:
    """``sim_truth.capacity_rows`` at the end of every month of the run, also where there are no pairs at all."""
    from besslib.soh.capacity import estimate_capacity
    from besslib.soh.config import CapacityConfig
    from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs

    config = CapacityConfig()
    rests = operating_rests(reduced.rests, spec, config)
    pairs = rest_pairs(rest_chain(rests, spec, config), config)
    at = pd.date_range(pd.Timestamp(start), periods=int(months), freq="ME")
    return estimate_capacity(None, spec, config=config, pairs=pairs, levels_from=levels_from(rests, config), at=at)
