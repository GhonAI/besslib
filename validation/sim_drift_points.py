"""The numbers of case 6 (a drifting stop) that more than one page quotes; no plotting, so that any page script may
import it. Reads ``validation/results/sim/drift.csv``."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).resolve().parent / "results"


def drift_terms(t: pd.DataFrame) -> pd.DataFrame:
    """Medians over the tests, per setting, in points of nominal capacity (nominal from the pack rows)."""
    nominal = t[t["row"] == "pack"].groupby("setting")["nominal_ah"].median()
    x = t[(t["row"] == "test") & (t["test_status"] == "ok")].copy()
    x["nominal_ah"] = x["setting"].map(nominal)
    for a, b, name in (("estimate_ah", "t_rest", "estimator"), ("t_rest", "t_ops", "drift"), ("t_ops", "t_test", "rate"),
                       ("estimate_ah", "t_test", "miss"), ("t_fixed", "t_rest", "fixed_less_rest")):
        x[name] = 100.0 * (x[a] - x[b]) / x["nominal_ah"]
    return x.groupby("setting")[["estimator", "drift", "rate", "miss", "fixed_less_rest"]].median()


def drift_points() -> dict:
    """The numbers of case 6 that other pages quote: estimate less test and T_fixed - T_rest, in points of nominal
    capacity, without drift and at the largest planted drift (held-out seeds). Refuses where they no longer say that
    the drift does not carry the gap to a test and that a falling stop hides fade."""
    t = pd.read_csv(RESULTS / "sim" / "drift.csv", keep_default_na=False, na_values=[""])
    if set(t["seed_set"]) != {"held-out"}:
        raise SystemExit("drift.csv: not the held-out seeds")
    m = drift_terms(t)
    last = max((k for k in m.index if re.fullmatch(r"D\d+", k)), key=lambda k: int(k[1:]))
    out = {"mv": int(last[1:]), "miss_none": m.loc["D0", "miss"], "miss_last": m.loc[last, "miss"],
           "c10_none": m.loc["D0-c10", "miss"], "c10_last": m.loc[f"{last}-c10", "miss"],
           "one_none": m.loc["D0-onestop", "miss"], "one_last": m.loc[f"{last}-onestop", "miss"],
           "hidden_none": -m.loc["D0", "fixed_less_rest"], "hidden_last": -m.loc[last, "fixed_less_rest"]}
    if not (out["miss_none"] > 0 and out["miss_last"] > 0 and out["miss_last"] - out["miss_none"] < 0.5 * out["miss_none"]
            and out["hidden_last"] > 2 * out["hidden_none"] > 0):
        raise SystemExit("the drift sentences no longer fit the numbers: write them again")
    return out


OTHER_SET_UPS = ("With other test set-ups it moves: {c10_none:+.2f} to {c10_last:+.2f} at C/10, {one_none:+.2f} to {one_last:+.2f} with one stop "
                 "for tests and daily operation.")
