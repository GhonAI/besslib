"""Write ``docs/06_version_0_3.md``: what version 0.3 changed, and what it does on the same data.

Every number comes from ``validation/results/`` (today's files, at the 0.3 settings), from the files of
release 0.2.0 (``git show v0.2.0:<path>``) or from the settings themselves (``CapacityConfig()`` and its
``v0_2()``); none is typed. It refuses to build from results of other settings.

Reads ``checks_phase2.csv`` and ``checks_phase3.csv`` (today and at the tag), ``checks_v03.csv`` (F1, R2, W7),
``checks_sim_v03.csv`` (the simulator, seeds 2000-2199), ``diag_fade_band.csv`` (why V6 fell),
``phase2/system_*_monthly.csv`` and ``phase2/system_*_summary.json`` (today and at the tag).

Usage:
    python validation/make_v03_doc.py
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

import pandas as pd

import diag_fade_band
import sim_truth
from besslib.io import figgener
from besslib.soh import _core
from besslib.soh.config import _V0_2, CapacityConfig, RecordConfig, config_hash
from make_v02_doc import cell, table, value_text
from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
DOCS = ROOT / "docs"
PAGE = "06_version_0_3.md"
TAG = "v0.2.0"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
NOW = {"capacity": config_hash(CapacityConfig()), "record": config_hash(RecordConfig())}
THEN = {"capacity": config_hash(CapacityConfig().v0_2()), "record": config_hash(RecordConfig().v0_2())}

#: What each setting of version 0.3 does (words only; the values are read from the settings).
WHAT = {
    "voltage_terms": "a quadratic voltage term at each end of the capacity fit, beside the linear one",
    "quad_min_rests": "each quadratic term is used only while its kind has this many rests in memory (tuned on the development systems, "
                      "`validation/tune_quadratic.py`)",
    "rest_time": "a pair's time runs between the moments its two rest voltages are read, not between the rests' starts",
    "above_full_min_rests": "rule A: this many charges in the trailing year that end above the full level mean the full level is not the pack's",
    "above_full_share": "rule A: how far above the full level, as a share of the span between the levels",
    "limits_share": "rule B (only with the pack's documented voltage limits): a level this far inside them, as a share of the window, is not "
                    "the pack's (chosen on the simulator's development packs)",
}


def tag_text(path: str) -> str:
    done = subprocess.run(["git", "show", f"{TAG}:{path}"], cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"{TAG} has no {path}")
    return done.stdout


def read(name: str, tag: bool = False) -> pd.DataFrame:
    if tag:
        return pd.read_csv(io.StringIO(tag_text(f"validation/results/{name}")), keep_default_na=False)
    return pd.read_csv(RESULTS / name, keep_default_na=False)


def load() -> dict:
    ctx = {"p2": read("checks_phase2.csv"), "p3": read("checks_phase3.csv"), "p2_tag": read("checks_phase2.csv", True),
           "p3_tag": read("checks_phase3.csv", True), "v03": read("checks_v03.csv"), "sim": read("checks_sim_v03.csv"),
           "fade": pd.read_csv(RESULTS / "diag_fade_band.csv")}
    found = set(ctx["p2"]["config_hash"]) | set(ctx["p3"]["capacity_config_hash"])
    if found != {NOW["capacity"]} or set(ctx["p3"]["record_config_hash"]) != {NOW["record"]}:
        raise SystemExit(f"results come from {sorted(found)}, not today's {NOW}: rebuild them first")
    if set(ctx["p2_tag"]["config_hash"]) != {THEN["capacity"]}:
        raise SystemExit(f"{TAG}'s checks are not of the 0.2 settings")
    monthly, summary = {}, {}
    for sid in figgener.SYSTEM_IDS:
        name = f"phase2/system_{sid:02d}"
        monthly[sid] = (pd.read_csv(RESULTS / f"{name}_monthly.csv"),
                        pd.read_csv(io.StringIO(tag_text(f"validation/results/{name}_monthly.csv"))))
        summary[sid] = (json.loads((RESULTS / f"{name}_summary.json").read_text()),
                        json.loads(tag_text(f"validation/results/{name}_summary.json")))
    ctx["monthly"], ctx["summary"] = monthly, summary
    return ctx


def settings_table() -> str:
    now, then = asdict(CapacityConfig()), _V0_2["CapacityConfig"]
    rows = [{"Setting": f"`{k}`", "0.2": value_text(then[k]), "0.3": value_text(now[k]), "What it does": WHAT[k]} for k in then]
    return table(pd.DataFrame(rows))


def compare(now: pd.DataFrame, then: pd.DataFrame, value: str = "value") -> str:
    """Row by row: both files come from the same check scripts in the same order (a scope's wording may differ)."""
    if list(now["check"]) != list(then["check"]):
        raise SystemExit("the check rows of 0.2 and 0.3 are not in the same order: compare them by hand first")
    rows = []
    for r, o in zip(now.itertuples(), zip(then[value], then["result"], then["scope"])):
        own = "" if o[2] == r.scope else f"; 0.2's scope: {o[2]}"
        rows.append({"Check": r.check, "Scope": r.scope, "0.2": f"{o[0]} ({o[1] or 'information'}{own})",
                     "0.3": f"{getattr(r, value)} ({r.result or 'information'})"})
    return table(pd.DataFrame(rows))


def rule_a_lines(ctx: dict) -> list[str]:
    ok, above = _core.STATUS_TEXT[_core.OK], _core.STATUS_TEXT[_core.ABOVE_FULL]
    out = []
    for sid, (now, then) in ctx["monthly"].items():
        both = then.merge(now, on="time", suffixes=("_02", ""))
        a = both["status"] == above
        if a.any():
            out.append(f"{sid:02d} ({'held-out' if sid in HELD_OUT else 'development'}): {int(a.sum())} month end{'s' if a.sum() != 1 else ''}, "
                       f"{int((a & (both['status_02'] == ok)).sum())} of them estimated by 0.2")
    refused = [sid for sid, (now, then) in ctx["summary"].items()
               if now.get("status") != ok and then.get("status") == ok and "charges end above" in str(now.get("status"))]
    other = []
    for sid, (now, then) in ctx["monthly"].items():
        both = then.merge(now, on="time", suffixes=("_02", ""))
        lost = both[(both["status_02"] == ok) & (both["status"] != ok) & (both["status"] != above)]
        for reason, rows in lost.groupby("status"):
            other.append(f"{sid:02d} ({'held-out' if sid in HELD_OUT else 'development'}): {len(rows)} ({reason.removeprefix('not possible from this data: ')})")
    return [f"- Rule A refuses month ends on: {'; '.join(out) if out else 'no system'}. "
            + (f"On {', '.join(f'{s:02d}' for s in refused)} the fade rate is then `not possible` (a capacity in the record breaks rule A), so "
               f"check V5 counts {'it' if len(refused) == 1 else 'them'} out." if refused else ""),
            "- Information: month ends that 0.2 estimated and 0.3 refuses for another reason than rule A: "
            + ("; ".join(other) if other else "none") + ".",
            *fade_rate_lines(ctx)]


def fade_rate_lines(ctx: dict) -> list[str]:
    """The fade rate of each held-out LFP system (check V5), 0.2 against 0.3, from the capacity summaries."""
    ok = _core.STATUS_TEXT[_core.OK]

    def rate(summary: dict) -> str:
        if summary.get("status") != ok or summary.get("rate_points_per_year") is None:
            return str(summary.get("status"))
        return (f"{summary['rate_points_per_year']:+.2f} ({summary['rate_points_per_year_low']:+.2f} to "
                f"{summary['rate_points_per_year_high']:+.2f})")

    rows = [{"System": f"{sid:02d}", "0.2, points/year (band)": rate(then), "0.3, points/year (band)": rate(now)}
            for sid, (now, then) in ctx["summary"].items() if sid in HELD_OUT and now.get("chemistry") == "LFP"]
    return ["", "The fade rate of the held-out LFP systems (check V5), 0.2 against 0.3:", "", table(pd.DataFrame(rows))]


def fade_lines(ctx: dict) -> list[str]:
    s = diag_fade_band.summary(ctx["fade"]).set_index("setting")
    names = {"v02": "0.2", "v02_read": "0.2 with the reading times", "v03_start": "0.3 without the reading times",
             "v03_lin": "0.3 without the quadratic terms", "v03": "0.3"}
    rows = [{"Settings": names[k], "Band holds the true fade": f"{int(s.loc[k, 'held'])} of {int(s.loc[k, 'n'])} ({s.loc[k, 'held_share']:.1%})",
             "Median half-width, points/year": f"{s.loc[k, 'median_half']:.3f}", "Mean error, points/year": f"{s.loc[k, 'mean_error']:+.3f}"}
            for k in names]
    v = s.loc["v03"]
    return [
        f"Check V6 judges the fade rate's band on {int(v['n'])} synthetic packs against the true usable fade "
        f"({v['truth_points_per_year']:.3f} points per year). It fell from {s.loc['v02', 'held_share']:.1%} to {v['held_share']:.1%}. The same packs "
        "under five settings (`validation/diag_fade_band.py`) separate the two changes: the reading times double the bias "
        f"({s.loc['v02', 'mean_error']:+.3f} → {s.loc['v02_read', 'mean_error']:+.3f} points per year), and the quadratic terms narrow the band "
        f"(median half-width {s.loc['v03_lin', 'median_half']:.3f} → {v['median_half']:.3f}). Each alone leaves "
        f"{s.loc['v02_read', 'held_share']:.1%} (the reading times) and {s.loc['v03_start', 'held_share']:.1%} (the quadratic terms); together "
        f"{v['held_share']:.1%}. The bias is "
        f"{abs(v['mean_error'] / v['truth_points_per_year']):.1%} of the true fade. Not fixed in 0.3; no pass rule moved.",
        "",
        table(pd.DataFrame(rows)),
        "",
    ]


def document(ctx: dict) -> str:
    t6a = ctx["sim"][(ctx["sim"]["check"] == "T6a") & ctx["sim"]["what"].str.startswith("D0:")]["result"].iloc[0]
    v03 = ctx["v03"]
    v4 = ctx["p2_tag"][ctx["p2_tag"]["check"] == "V4"]["value"].iloc[0]
    share, n_v4 = re.search(r"in (\d+)% of (\d+)", v4).groups()
    out = [
        "# Version 0.3: what changed, and what it does on the same data",
        "",
        f"Written by `validation/make_v03_doc.py` from `validation/results/` and from the files of release {TAG[1:]} "
        f"(`git show {TAG}:<path>`). No number on this page is typed by hand.",
        "",
        "## A third look, not a blind test",
        "",
        f"The {len(HELD_OUT)} held-out field systems were run at the settings of releases 0.1.0 and 0.2.0 before. Version 0.3 was built and "
        "judged on the simulator first (`besslib.sim`, [page](05_simulator.md#version-03-on-new-seeds)), on the development field systems as "
        "information, and then run once on the held-out field systems. Its numbers are published as they came; nothing was changed after "
        "that run.",
        "",
        "## What changed",
        "",
        f"Version 0.2's capacity band held {share} % of the {n_v4} held-out LMO/NMC and NMC field tests (V4), and on simulated packs it did not hold "
        "even the capacity it is built for, the charge between the two rest voltages it reads at. Version 0.3 reads that capacity better and refuses a capacity whose full "
        f"or empty level is not the pack's. Settings `{NOW['capacity']}` (0.2: `{THEN['capacity']}`); the record's settings are unchanged "
        f"(`{NOW['record']}`).",
        "",
        settings_table(),
        "",
        f"On the simulator (seeds {sim_truth.HELD_OUT.start}-{sim_truth.HELD_OUT.stop - 1}) A1's band now holds its own quantity at {t6a.split(';')[0]} "
        "month ends without drift; the other rows, passed and failed, are on the [simulator page](05_simulator.md#version-03-on-new-seeds).",
        "",
        "## The checks of version 0.3",
        "",
        table(pd.DataFrame({"Check": v03["check"], "Value": v03["value"], "Pass if": v03["pass_if"], "Result": v03["result"]})),
        "",
        "F1: the settings of releases 0.1.0 and 0.2.0 still give those releases' published numbers. R2: rule A removes no estimate of the "
        "development field systems. W7: version 0.3's rule for A4 on the synthetic packs adds the mean clause.",
        "",
        "## The field systems, 0.2 against 0.3",
        "",
        "Capacity ([page](02_capacity_validation.md)):",
        "",
        compare(ctx["p2"], ctx["p2_tag"]),
        "",
        *rule_a_lines(ctx),
        "",
        "The other Annex VII fields ([page](03_annex_vii.md)):",
        "",
        compare(ctx["p3"], ctx["p3_tag"]),
        "",
        "## Why V6 fell",
        "",
        *fade_lines(ctx),
        "## Why A4's band moved",
        "",
        "The quadratic terms narrow A4's band too; a new leak then lies outside it for longer. The numbers are on the "
        "[simulator page](05_simulator.md#version-03-on-new-seeds) (finding 7).",
        "",
        "## Rebuild",
        "",
        "```",
        "python validation/run_capacity.py <id>        # capacity, one system (all 21 for the page)",
        "python validation/run_annex_vii.py <id>       # the record",
        "python validation/check_capacity.py && python validation/check_annex_vii.py && python validation/check_v03.py",
        "python validation/diag_fade_band.py           # why V6 fell (200 synthetic packs)",
        "python validation/make_v03_doc.py             # this page",
        "```",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    ctx = load()
    (DOCS / PAGE).write_text(document(ctx), encoding="utf-8")
    print(f"docs/{PAGE} written: capacity {NOW['capacity']}, record {NOW['record']}")


if __name__ == "__main__":
    main()
