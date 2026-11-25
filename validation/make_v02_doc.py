"""Write ``docs/04_version_0_2.md``: what version 0.2 changed, and what it does on the same data.

Every number comes from ``validation/results/`` (today's files) or from the files of release
0.1.0 (``git show v0.1.0:<path>``), or from the settings themselves (``CapacityConfig()``,
``RecordConfig()`` and their ``v0_1()``); none is typed. It refuses to build from results of
other settings.

Reads ``checks_v02.csv``, ``checks_phase2.csv``, ``checks_phase3.csv``, ``phase2_tests.csv``
(today and at the tag), ``w7_cause.csv`` (X1), ``synthetic_lag.csv`` (X3),
``capacity_chains.csv`` (X7), ``synthetic_coverage.csv`` (V6), ``phase2/system_*_monthly.csv``
and ``phase2/system_*_summary.json`` (today and at the tag), and the records of both, and ``sim/drift.csv`` (case 6 of the simulator).

Usage:
    python validation/make_v02_doc.py
"""

from __future__ import annotations

import io
import json
import math
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.config import _V0_1, CapacityConfig, RecordConfig, config_hash

from check_v02 import TAG, tag_file
from make_capacity_doc import DASH, ids_text, num, sid_text
from sim_drift_points import OTHER_SET_UPS, drift_points
from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
PHASE2 = RESULTS / "phase2"
DOCS = ROOT / "docs"
PAGE = "04_version_0_2.md"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
FROZEN = {"capacity": config_hash(CapacityConfig()), "record": config_hash(RecordConfig())}
V0_1 = {"capacity": config_hash(CapacityConfig().v0_1()), "record": config_hash(RecordConfig().v0_1())}
RELEASE = TAG.lstrip("v")


def cell(value) -> str:
    """A Markdown table cell: "|" escaped, and a "<" that opens a tag-like word (``<id>``) so GitHub shows it."""
    return re.sub(r"<(?=[A-Za-z/!?])", "&lt;", str(value).replace("|", "\\|").replace("\n", " "))


def table(frame: pd.DataFrame) -> str:
    lines = ["| " + " | ".join(frame.columns) + " |", "|" + "|".join("---" for _ in frame.columns) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join(lines)


def set_of(sid: int) -> str:
    return "development" if sid in DEVELOPMENT else "held-out"


def refuse(name: str, found) -> None:
    raise SystemExit(f"{name}: results come from settings {sorted(map(str, found))}, not the frozen "
                     f"{FROZEN}: rebuild them first")  # fmt: skip


def tag_csv(name: str) -> pd.DataFrame:
    text = tag_file(f"validation/results/{name}")
    if text is None:
        raise SystemExit(f"{TAG} has no validation/results/{name}")
    return pd.read_csv(io.StringIO(text), keep_default_na=False)


def records(tag: bool) -> dict[int, dict]:
    out = {}
    for sid in figgener.SYSTEM_IDS:
        name = f"validation/results/phase3/system_{sid:02d}_record.json"
        text = tag_file(name) if tag else ((ROOT / name).read_text() if (ROOT / name).exists() else None)
        if text is not None:
            out[sid] = json.loads(text)
    return out


# ----------------------------------------------------------------------------- load


def load() -> dict:
    checks_v02 = pd.read_csv(RESULTS / "checks_v02.csv", keep_default_na=False)
    p2 = pd.read_csv(RESULTS / "checks_phase2.csv", keep_default_na=False)
    p3 = pd.read_csv(RESULTS / "checks_phase3.csv", keep_default_na=False)
    tests = pd.read_csv(RESULTS / "phase2_tests.csv")
    lag = pd.read_csv(RESULTS / "synthetic_lag.csv", dtype={"capacity_config_hash": str})
    coverage = pd.read_csv(RESULTS / "synthetic_coverage.csv", dtype={"config_hash": str})
    chains = pd.read_csv(RESULTS / "capacity_chains.csv", dtype={"config_hash": str})
    cause = pd.read_csv(RESULTS / "w7_cause.csv", dtype={"capacity_config_hash": str, "record_config_hash": str})
    monthly = {int(p.stem.split("_")[1]): pd.read_csv(p, parse_dates=["time"]) for p in sorted(PHASE2.glob("system_*_monthly.csv"))}
    summaries = {int(p.stem.split("_")[1]): json.loads(p.read_text()) for p in sorted(PHASE2.glob("system_*_summary.json"))}

    capacity = (set(checks_v02["capacity_config_hash"]) | set(p2["config_hash"]) | set(p3["capacity_config_hash"])
                | set(tests["config_hash"]) | set(lag["capacity_config_hash"]) | set(coverage["config_hash"])
                | {s["config_hash"] for s in summaries.values()} | set().union(*(set(m["config_hash"]) for m in monthly.values())))  # fmt: skip
    record = set(checks_v02["record_config_hash"]) | set(p3["record_config_hash"])
    if capacity != {FROZEN["capacity"]} or record != {FROZEN["record"]}:
        refuse("validation/results", capacity | record)
    # X1 runs W7 as published in release 0.1.0, so at the 0.1 settings by design.
    if set(cause["capacity_config_hash"]) != {V0_1["capacity"]} or set(cause["record_config_hash"]) != {V0_1["record"]}:
        refuse("w7_cause.csv", set(cause["capacity_config_hash"]) | set(cause["record_config_hash"]))
    # X7 is judged at gain term 0 (its own hash); the frozen gain term is information.
    tau = CapacityConfig().tau_days
    expect = {("0.2, gain 0", tau): config_hash(CapacityConfig().with_(gain_error=0.0)),
              ("0.2, frozen gain term", tau): FROZEN["capacity"]}  # fmt: skip
    for (setting, t), digest in expect.items():
        got = set(chains.loc[(chains["setting"] == setting) & (chains["tau_days"] == t), "config_hash"])
        if got != {digest}:
            refuse(f"capacity_chains.csv ({setting}, {t:g} days)", got)

    tag_summaries = {}
    for sid in figgener.SYSTEM_IDS:
        text = tag_file(f"validation/results/phase2/system_{sid:02d}_summary.json")
        if text is not None:
            tag_summaries[sid] = json.loads(text)
    date = subprocess.run(["git", "log", "-1", "--format=%cs", TAG], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {
        "checks_v02": checks_v02, "p2": p2, "p3": p3, "p2_tag": tag_csv("checks_phase2.csv"),
        "p3_tag": tag_csv("checks_phase3.csv"), "tests": tests, "tests_tag": tag_csv("phase2_tests.csv"),
        "lag": lag, "coverage": coverage, "chains": chains, "cause": cause, "monthly": monthly,
        "summaries": summaries, "tag_summaries": tag_summaries, "records": records(False),
        "tag_records": records(True), "tag_date": pd.Timestamp(date) if date else None,
    }  # fmt: skip


# ----------------------------------------------------------------------------- findings by rule


def power_hits(recs: dict) -> list[int]:
    out = []
    for sid, r in sorted(recs.items()):
        f = {x["code"]: x for x in r["fields"]}
        if f["A2"]["status"] == "estimated" and str(f["A5"]["status"]).startswith("not possible"):
            out.append(sid)
    return out


def zero_hits(recs: dict) -> list[int]:
    out = []
    for sid, r in sorted(recs.items()):
        a1 = {x["code"]: x for x in r["fields"]}["A1"]
        if a1["status"] == "estimated" and a1["value"] is not None and a1["value"] <= 0:
            out.append(sid)
    return out


def by_set(ids: list[int]) -> str:
    parts = []
    for name in ("held-out", "development"):
        mine = [i for i in ids if set_of(i) == name]
        if mine:
            parts.append(f"{name} {ids_text(mine)}")
    return "; ".join(parts) or "none"


# ----------------------------------------------------------------------------- page parts


def seen_block(ctx: dict) -> list[str]:
    zero, power = zero_hits(ctx["tag_records"]), power_hits(ctx["tag_records"])
    when = f" (tagged {ctx['tag_date'].day} {ctx['tag_date']:%B %Y})" if ctx["tag_date"] is not None else ""
    return [
        f"The {len(HELD_OUT)} held-out systems were seen once before, at the settings of release {RELEASE}{when}. Two faults "
        f"this version fixes were found in those results: a capacity at or below zero marked `estimated` "
        f"({by_set(zero)}) and power capability `estimated` without a resistance ({by_set(power)}). The 0.2 "
        f"settings were fixed on synthetic packs and the {len(DEVELOPMENT)} development systems, except the plausibility "
        "rules and the power-capability rule, which answer the two faults found in the held-out results; then the held-out "
        "systems were run once with them. **The 0.2 held-out results are a second look, not a blind test.**",
    ]


def value_text(v) -> str:
    return str(v) if isinstance(v, (bool, str)) else f"{v:g}"


def setting_text(cls, name: str) -> str:
    old, new = _V0_1[cls.__name__][name], asdict(cls())[name]
    if old == new:  # a new setting whose 0.1 value only keeps the 0.1 hash
        return f"`{name}` = {value_text(new)}"
    return f"`{name}`: {value_text(old)} → {value_text(new)}"


def changes_block(ctx: dict) -> list[str]:
    zero, power = zero_hits(ctx["tag_records"]), power_hits(ctx["tag_records"])
    C, R = CapacityConfig, RecordConfig
    rows = [
        ("Apparent charge loss (A4) biased on synthetic packs (W7)",
         "the capacity fit took two rests of one kind as the same state of charge",
         "two relaxed rest-voltage terms on every pair; the capacity is read at the mean full and empty rest voltages "
         "of the memory time, which the record gives", setting_text(C, "voltage_terms"), "synthetic packs"),
        ("The capacity lagged a fading capacity",
         "a forgetting-weighted mean: about the memory time behind",
         "a linear trend in the fit with forgetting, value read at the date; its gate tuned on the development systems",
         setting_text(C, "local_trend") + "; " + setting_text(C, "trend_min_cross"), "synthetic packs"),
        ("The capacity band used the forgetting weight once",
         "the weight entered the band's sums once", "squared, as for a weighted least-squares fit",
         setting_text(C, "band_forgetting"), "synthetic chains"),
        ("The gain error in the capacity band", f"{CapacityConfig().v0_1().gain_error * 100:g} % added as it is",
         "read as a rectangular half-width, ÷ √3 (JCGM 100:2008, 4.3.7), as A4 already did",
         setting_text(C, "gain_rectangular"), "synthetic chains (X7), before any 0.2 held-out run"),
        ("A capacity at or below zero said `estimated`", "no plausibility rule",
         "crossed levels, a value or band at or below zero, or a wrong-sign pair in memory give `not possible from this data`",
         setting_text(C, "plausibility"), by_set(zero)),
        ("Power capability `estimated` without a resistance", "A2 used the whole-record growth fit",
         "A2 is `estimated` only where A5 is `estimated` at the same date", setting_text(R, "a2_needs_a5"),
         by_set(power)),
        ("W4 and W6 counted a day twice", "31 December at 00:00 and a last sample on 31 December were two rows",
         "one row per system and day (X5)", "none: check only", "held-out results"),
        ("Empty-slice warnings in the rest search", "a warning when a rest had no temperature reading",
         "the statistic is NaN without a warning; values unchanged (X8)", "none", "held-out results"),
        ("The A1 label", "remaining capacity between the system's own end of charge and end of discharge",
         "the capacity between two stated relaxed rest voltages, not between the BMS stops", "none: text", "the README"),
        ("The A3 method text", "read awkwardly", "new wording (X9)", "none: text", "the README"),
        ("A warning in `soh.report` on pandas 2.2.2", "`dt.to_pydatetime` FutureWarning",
         "Python datetimes built per value; the figure is the same (X10)", "none", "the lowest-version test"),
    ]  # fmt: skip
    frame = pd.DataFrame(rows, columns=["Fault or change", f"Release {RELEASE}", "Version 0.2", "Setting", "Found on"])
    return [
        table(frame),
        "",
        f"With every new setting at its {RELEASE} value (`CapacityConfig().v0_1()`, `RecordConfig().v0_1()`) the code gives "
        f"the {RELEASE} hashes (capacity `{V0_1['capacity']}`, record `{V0_1['record']}`) and the {RELEASE} numbers (X0). "
        f"The 0.2 hashes: capacity `{FROZEN['capacity']}`, record `{FROZEN['record']}`. Every other threshold keeps its "
        f"frozen {RELEASE} value.",
        "",
        "Not in version 0.2: partial windows (Plett's form), for systems without regular full cycles. They come after the "
        "pack simulator (besslib.sim), which tests them at middle state of charge.",
    ]


TESTS = {  # what each check of version 0.2 tests, in plain words
    "X0": f"the 0.2 code at the release {RELEASE} settings gives release {RELEASE} exactly",
    "X1": "the cause of the A4 bias",
    "X2": "no capacity at or below zero, with crossed levels or with a wrong-sign pair is marked `estimated`",
    "X3": "the monthly capacity band holds the true capacity at the date",
    "X4": "no power capability without a resistance",
    "X5": "each system and day is counted once in W4 and W6",
    "X7": "the capacity band holds the truth on simulated chains of pairs",
    "X8": "no warnings from the rest search",
    "X9": "the new A3 method text is the one published (record, README, report)",
    "X10": "no warnings on the lowest supported library versions",
}


def x_rows(ctx: dict) -> pd.DataFrame:
    rows = []
    cause = ctx["cause"]
    true_err, label_err = cause["true_error_ma"].dropna(), cause["labels_error_ma"].dropna()
    lim = 2 * true_err.std(ddof=1) / math.sqrt(len(true_err))
    rows.append(("X1", f"cause check, {len(cause)} W7 packs, true change of state of charge in place of the labels",
                 f"mean error {true_err.mean():+.2f} mA (sd {true_err.std(ddof=1):.2f}); with the labels "
                 f"{label_err.mean():+.2f} mA", f"mean within ±{lim:.3f} mA (2 sd/√{len(true_err)})",
                 "pass" if abs(true_err.mean()) <= lim else "fail"))  # fmt: skip
    for _, r in ctx["checks_v02"].iterrows():
        if r["check"].startswith("X0") or r["check"].startswith("X2") or r["check"] == "X4":
            rows.append((r["check"], DASH, r["value"], r["pass_if"], r["result"]))
    lag = ctx["lag"]
    total, held = int(lag["n_band"].sum()), int(lag["n_held_at_date"].sum())
    lim = 2 * lag["mean_lag_points"].std(ddof=1) / math.sqrt(len(lag))
    share = held / total
    ok = 0.85 <= share <= 0.95 and abs(lag["mean_lag_points"].mean()) <= lim
    rows.append(("X3", f"band at the date, {len(lag)} synthetic packs, against the charge between the stops",
                 f"{held} of {total} month ends ({share:.1%}); mean lag {lag['mean_lag_points'].mean():+.3f} points",
                 f"85-95 %; mean lag within ±{lim:.3f} (2 sd/√{len(lag)})", "pass" if ok else "fail"))  # fmt: skip
    for _, r in ctx["p3"][ctx["p3"]["check"] == "X5"].iterrows():
        rows.append(("X5", r["scope"], r["value"], r["pass_if"], r["result"]))
    g = ctx["chains"][ctx["chains"]["setting"] == "0.2, gain 0"]
    parts, ok = [], True
    for tau in sorted(g["tau_days"].unique(), reverse=True):
        h = g[g["tau_days"] == tau]["held"].astype(bool)
        parts.append(f"{int(h.sum())} of {len(h)} ({h.mean():.1%}) " + (f"with {tau:g} days of memory" if tau > 0 else "without memory"))
        ok &= 0.85 <= h.mean() <= 0.95
    rows.append(("X7", "capacity band on simulated pair chains without fade, gain term 0", "; ".join(parts), "85-95 % each",
                 "pass" if ok else "fail"))  # fmt: skip
    for _, r in ctx["checks_v02"].iterrows():
        if r["check"].startswith(("X8", "X9", "X10")):
            rows.append((r["check"], DASH, r["value"] if not r["value"].strip().startswith(".") else "tests passed",
                         r["pass_if"], r["result"]))  # fmt: skip
    frame = pd.DataFrame(rows, columns=["Check", "Scope", "Value", "Pass rule", "Result"])
    frame.insert(1, "What it tests", [TESTS.get(c.split()[0], DASH) for c in frame["Check"]])
    frame["Result"] = frame["Result"].map(lambda v: f"**{v}**" if v == "fail" else v)
    return frame


def x_block(ctx: dict) -> list[str]:
    frame = x_rows(ctx)
    failed = [c for c, r in zip(frame["Check"], frame["Result"]) if r == "**fail**"]
    x10 = ctx["checks_v02"][ctx["checks_v02"]["check"] == "X10"]
    cause = ctx["cause"]
    exact = cause["exact_error_ma"].dropna()
    return [
        "The pass rules were fixed before any 0.2 result was computed.",
        "",
        table(frame),
        "",
        f"Failed: {', '.join(failed) or 'none'}.",
        "",
        *x1_text(cause, exact),
        f"- **X3** and **X7** fail against truths built from the BMS stops. Against the capacity between the rest voltages "
        f"the fit reports, {reference_verdict(ctx)}; see "
        "[what the capacity measures](#what-the-capacity-measures-and-what-the-checks-compare-it-with).",
    ] + ([f"- **X10**, the check's note: {x10['note'].iloc[0]}."] if len(x10) and x10["note"].iloc[0] else [])


def x1_text(cause: pd.DataFrame, exact: pd.Series) -> list[str]:
    """X1 in plain words, from ``w7_cause.csv``."""
    true_err = cause["true_error_ma"].dropna()
    lim = 2 * true_err.std(ddof=1) / math.sqrt(len(true_err))
    old, new, ex = f"{cause['labels_error_ma'].mean():+.2f}", f"{true_err.mean():+.2f}", f"{exact.mean():+.2f}"
    if abs(true_err.mean()) <= lim:
        return [f"- **X1** tested the cause of the A4 bias. With the true change of state of charge in place of the full and "
                f"empty labels, the bias falls from {old} to {new} mA, within the pre-set ±{lim:.3f} mA, so X1 passes. W7 tests the fix."]
    same = f"the same {ex} mA" if ex == new else f"{ex} mA"
    return [f"- **X1** tested the cause of the A4 bias. With the true change of state of charge in place of the full and empty "
            f"labels, the bias falls from {old} to {new} mA, so the labels cause almost all of it. The {new} mA left is outside "
            f"the pre-set ±{lim:.3f} mA, so X1 fails; it does not come from the sampling, because the exact charge gives "
            f"{same}. W7 tests the fix."]


def reference_verdict(ctx: dict) -> str:
    """X3 and X7 judged by their own pass numbers against the reference-voltage truth (information only)."""
    lag = ctx["lag"]
    share = lag["n_held_reference"].sum() / lag["n_reference"].sum()
    lim = 2 * lag["mean_lag_reference_points"].std(ddof=1) / math.sqrt(len(lag))
    x3 = 0.85 <= share <= 0.95 and abs(lag["mean_lag_reference_points"].mean()) <= lim
    g = ctx["chains"][ctx["chains"]["setting"] == "0.2, gain 0"]
    x7 = all(0.85 <= g[g["tau_days"] == t]["held_reference"].astype(bool).mean() <= 0.95 for t in g["tau_days"].unique())
    word = {True: "would pass", False: "would fail"}
    return f"X3 {word[x3]} and X7 {word[x7]} by their own pass numbers (information, not a result)"


def _key(check: str, scope: str) -> tuple[str, str]:
    return check, re.sub(r"\s*\(\d+ systems?\)", "", scope)


def vw_block(ctx: dict) -> list[str]:
    out = []
    for name, now, old in (("Capacity, V2–V8", ctx["p2"], ctx["p2_tag"]), ("The other fields, W1–W10", ctx["p3"], ctx["p3_tag"])):
        old = old.assign(key=[_key(c, s) for c, s in zip(old["check"], old["scope"])])
        now = now[now["check"] != "X5"].assign(key=lambda t: [_key(c, s) for c, s in zip(t["check"], t["scope"])])
        keys = list(dict.fromkeys(list(now["key"]) + list(old["key"])))
        rows = []
        for key in keys:
            a, b = old[old["key"] == key], now[now["key"] == key]
            word = lambda t: (t["result"].iloc[0] or "information") if len(t) else DASH  # noqa: E731
            scope = (b if len(b) else a)["scope"].iloc[0]
            if len(a) and len(b) and a["scope"].iloc[0] != b["scope"].iloc[0]:
                scope += f" ({RELEASE}: {a['scope'].iloc[0]})"
            rows.append({
                "Check": key[0], "Scope": scope,
                f"{RELEASE} value": a["value"].iloc[0] if len(a) else DASH, f"{RELEASE}": word(a),
                "0.2 value": b["value"].iloc[0] if len(b) else DASH, "0.2": word(b),
            })  # fmt: skip
        frame = pd.DataFrame(rows)
        changed = [f"{r['Check']} ({r['Scope']})" for r in rows if r[RELEASE] in ("pass", "fail") and r["0.2"] in ("pass", "fail") and r[RELEASE] != r["0.2"]]
        for col in (RELEASE, "0.2"):
            frame[col] = frame[col].map(lambda v: f"**{v}**" if v == "fail" else v)
        out += [f"### {name}", "", table(frame), "",
                f"Result changed: {'; '.join(changed) or 'none'}. A dash: the check has no such row in that version.", ""]  # fmt: skip
    return out


def test_rows(ctx: dict, pick) -> list[dict]:
    """One row per version over the field tests that ``pick`` keeps, from ``phase2_tests.csv`` now and at the tag."""
    rows = []
    for label, t in ((RELEASE, ctx["tests_tag"]), ("0.2", ctx["tests"])):
        d = pick(t).copy()
        for col in ("capacity_ah", "error_points", "band_points"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        est = d[d["capacity_ah"].notna()]
        held = d["held"].astype(str).str.lower().eq("true")
        rows.append({"Version": label, "Tests": len(d), "With an estimate": len(est),
                     "Median abs. error, points (estimated tests)": est["error_points"].abs().median(),
                     "Median abs. error, points (all tests; no estimate = infinite)": d["error_points"].abs().fillna(np.inf).median(),
                     "Band held the test": f"{int(held.sum())} of {len(d)}", "Median band width, points": est["band_points"].median()})  # fmt: skip
    return rows


def shown_rows(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for col in ("Median abs. error, points (estimated tests)", "Median abs. error, points (all tests; no estimate = infinite)",
                "Median band width, points"):
        frame[col] = frame[col].map(lambda v: "infinite" if v == np.inf else num(v))
    return frame


def dev_block(ctx: dict) -> list[str]:
    rows = test_rows(ctx, lambda t: t[t["set"] == "development"])
    return ["The tests of the development systems, where the settings were tuned; not a result.", "", table(shown_rows(rows))]


def held_block(ctx: dict) -> list[str]:
    rows = test_rows(ctx, lambda t: t[(t["set"] == "held-out") & (t["chemistry"] != "LFP")])
    old, new = rows
    w, e = "Median band width, points", "Median abs. error, points (estimated tests)"
    verb = "narrowed" if new[w] < old[w] else "widened"
    return [
        "The held-out LMO/NMC and NMC tests of checks V2 and V4.",
        "",
        table(shown_rows(rows)),
        "",
        f"Version 0.2 {verb} the band: on these tests its median width {'fell' if new[w] < old[w] else 'rose'} from "
        f"{num(old[w])} to {num(new[w])} points while the median error of the estimated tests "
        f"{'fell' if new[e] < old[e] else 'rose'} from {num(old[e])} to {num(new[e])}. Which of the band changes "
        f"{verb} it most is not separated.",
    ]


def drift_rows(ctx: dict) -> pd.DataFrame:
    rows = []
    for sid, m in sorted(ctx["monthly"].items()):
        spec = figgener.pack_spec(sid)
        n = spec.cells_in_series
        est = m[m["status"] == "estimated"]
        e, f = est.dropna(subset=["ref_empty_v"]), est.dropna(subset=["ref_full_v"])
        old, new = ctx["tag_summaries"].get(sid, {}), ctx["summaries"].get(sid, {})
        rows.append({
            "sid": sid, "chem": spec.chemistry, "n": n,
            "first": e["time"].iloc[0] if len(e) else pd.NaT, "last": e["time"].iloc[-1] if len(e) else pd.NaT,
            "empty_first": e["ref_empty_v"].iloc[0] / n if len(e) else np.nan,
            "empty_last": e["ref_empty_v"].iloc[-1] / n if len(e) else np.nan,
            "full_mv": 1000 * (f["ref_full_v"].iloc[-1] - f["ref_full_v"].iloc[0]) / n if len(f) > 1 else np.nan,
            "fade_old": old.get("rate_points_per_year"), "fade_new": new.get("rate_points_per_year"),
        })  # fmt: skip
    t = pd.DataFrame(rows)
    t["empty_mv"] = 1000 * (t["empty_last"] - t["empty_first"])
    return t


def v_value(frame: pd.DataFrame, check: str, scope_start: str, pattern: str) -> float:
    rows = frame[(frame["check"] == check) & frame["scope"].str.startswith(scope_start)]
    found = re.search(pattern, str(rows["value"].iloc[0])) if len(rows) else None
    return float(found.group(1)) if found else np.nan


def v_word(frame: pd.DataFrame, check: str, scope_start: str) -> str:
    rows = frame[(frame["check"] == check) & frame["scope"].str.startswith(scope_start)]
    return {"pass": "passes", "fail": "fails"}.get(rows["result"].iloc[0], DASH) if len(rows) else DASH


def mismatch_block(ctx: dict) -> list[str]:
    lag, chains, cov = ctx["lag"], ctx["chains"], ctx["coverage"]
    tot, held = int(lag["n_band"].sum()), int(lag["n_held_at_date"].sum())
    rtot, rheld = int(lag["n_reference"].sum()), int(lag["n_held_reference"].sum())
    lim = 2 * lag["mean_lag_points"].std(ddof=1) / math.sqrt(len(lag))
    rlim = 2 * lag["mean_lag_reference_points"].std(ddof=1) / math.sqrt(len(lag))
    synth = [{
        "Check": "X3, band at the date", "Truth fixed before the results: charge between the stops": (
            f"{held} of {tot} month ends ({held / tot:.1%}); mean lag {lag['mean_lag_points'].mean():+.3f} points (limit ±{lim:.3f})"),
        "Capacity between the rest voltages (information)": (
            f"{rheld} of {rtot} ({rheld / rtot:.1%}); mean lag {lag['mean_lag_reference_points'].mean():+.3f} (limit ±{rlim:.3f})"),
    }]  # fmt: skip
    g = chains[chains["setting"] == "0.2, gain 0"]
    for tau in sorted(g["tau_days"].unique(), reverse=True):
        h = g[g["tau_days"] == tau]
        a, b = h["held"].astype(bool), h["held_reference"].astype(bool)
        synth.append({"Check": f"X7, {f'{tau:g} days of memory' if tau > 0 else 'no memory'}",
                      "Truth fixed before the results: charge between the stops": f"{int(a.sum())} of {len(h)} ({a.mean():.1%})",
                      "Capacity between the rest voltages (information)": f"{int(b.sum())} of {len(h)} ({b.mean():.1%})"})  # fmt: skip
    synth.append({"Check": "V6, fade rate",
                  "Truth fixed before the results: charge between the stops": f"{int(cov['held'].sum())} of {len(cov)} ({cov['held'].mean():.1%})",
                  "Capacity between the rest voltages (information)": f"{int(cov['held_window'].sum())} of {len(cov)} ({cov['held_window'].mean():.1%})"})  # fmt: skip

    d, gap = drift_rows(ctx), drift_points()
    lmo = d[d["chem"] == "LMO/NMC"]
    rest = d[d["chem"] != "LMO/NMC"]
    rest_max = rest["empty_mv"].abs().max()
    rest_chems = " and ".join(f"{c} {ids_text(list(rest.loc[rest['chem'] == c, 'sid']))}" for c in dict.fromkeys(rest["chem"]))
    lower = bool((lmo["empty_mv"] < 0).all())
    shown = pd.DataFrame({
        "System": d["sid"].map(sid_text), "Set": d["sid"].map(set_of), "Chemistry": d["chem"], "Cells in series": d["n"],
        "First and last month end with an empty rest voltage": [f"{a:%b %Y} – {b:%b %Y}" if pd.notna(a) else DASH for a, b in zip(d["first"], d["last"])],
        "Empty rest voltage, V per cell (first → last)": [f"{a:.3f} → {b:.3f}" if np.isfinite(a) else DASH for a, b in zip(d["empty_first"], d["empty_last"])],
        "Change, mV per cell": d["empty_mv"].map(lambda v: num(v, "{:+.0f}")),
        "Full rest voltage change, mV per cell": d["full_mv"].map(lambda v: num(v, "{:+.0f}")),
        f"Fade {RELEASE}, points/year": d["fade_old"].map(lambda v: num(v, "{:+.2f}")),
        "Fade 0.2, points/year": d["fade_new"].map(lambda v: num(v, "{:+.2f}")),
    })  # fmt: skip
    pairs = ", ".join(f"{sid_text(r.sid)} {num(r.fade_old, '{:+.2f}')} → {num(r.fade_new, '{:+.2f}')}" for r in lmo.itertuples())
    p2, p2t = ctx["p2"], ctx["p2_tag"]
    both = lmo.dropna(subset=["fade_old", "fade_new"])
    steeper = int((both["fade_new"] < both["fade_old"]).sum())
    v6_rule = p2.loc[p2["check"] == "V6", "pass_if"].iloc[0]
    v5 = r"mean fade (-?[\d.]+)"
    v4 = r"held the test in (\d+)%"
    v2 = r"median \|error\| ([\d.]+)"
    return [
        "Version 0.2 gives each rest a voltage term. The capacity it reports is then the capacity between a full and an "
        "empty rest at two given relaxed voltages. For A1 and the monthly estimate these are the forgetting-weighted mean "
        "rest voltages of the memory time; the record gives both. For the fade rate they are the record's mean, fixed. "
        f"Release {RELEASE} reported the charge between the BMS's own stops, wherever they lay.",
        "",
        "The checks' truths are charges between BMS stops. In the simulator these are the stops at its voltage limits; in "
        "the field, the stops of the capacity test, at the system's maximum power. The truths were fixed before version 0.2 "
        "and are kept: no truth is changed after the results. The second truth below was chosen after the first 0.2 "
        "results and is information only.",
        "",
        table(pd.DataFrame(synth)),
        "",
        "**In the field the stops move.** The mean empty rest voltage at the first and the last month end that has one, per "
        "cell (`ref_empty_v` of the monthly capacity files ÷ cells in series): on the LMO/NMC systems it changes by "
        + ", ".join(f"{sid_text(r.sid)} {r.empty_mv:+.0f}" for r in lmo.itertuples())
        + f" mV; on {rest_chems} by at most {rest_max:.0f} mV."
        + (" The LMO/NMC systems are discharged to a lower relaxed voltage as they age." if lower and len(lmo) else ""),
        "",
        table(shown),
        "",
        "So the capacity between fixed voltages and the charge between the stops drift apart: on the LMO/NMC systems the "
        f"0.2 fade is steeper on {steeper} of {len(both)}. LMO/NMC fade, "
        f"{RELEASE} → 0.2, points per year: {pairs}; held-out mean {num(v_value(p2t, 'V5', 'held-out LMO/NMC', v5))} → "
        f"{num(v_value(p2, 'V5', 'held-out LMO/NMC', v5))} (V5 {v_word(p2t, 'V5', 'held-out LMO/NMC')} in {RELEASE}, "
        f"{v_word(p2, 'V5', 'held-out LMO/NMC')} in 0.2). On the field tests the band held "
        f"{num(v_value(p2t, 'V4', 'held-out LMO/NMC', v4), '{:.0f}')} % of the held-out LMO/NMC and NMC tests in {RELEASE} and "
        f"{num(v_value(p2, 'V4', 'held-out LMO/NMC', v4), '{:.0f}')} % in 0.2 (V4 {v_word(p2, 'V4', 'held-out LMO/NMC')}); "
        f"their median error is {num(v_value(p2t, 'V2', 'held-out LMO/NMC', v2))} and "
        f"{num(v_value(p2, 'V2', 'held-out LMO/NMC', v2))} points (V2 {v_word(p2t, 'V2', 'held-out LMO/NMC')} in {RELEASE}, "
        f"{v_word(p2, 'V2', 'held-out LMO/NMC')} in 0.2).",
        "",
        f"They are two quantities. Against the capacity between the rest voltages, a truth chosen after the first results, the "
        f"monthly band held {100 * rheld / rtot:.0f} % of month ends (X3) and the fade-rate band {100 * cov['held_window'].mean():.1f} % "
        f"(V6, rule {v6_rule}); neither band includes the drift of the stops. On simulated packs, where the true drift is known and the test stop falls with the daily stop, the drift does not "
        f"carry the gap to a capacity test ({gap['miss_none']:+.2f} points without drift, {gap['miss_last']:+.2f} at {gap['mv']} mV per cell; "
        f"[simulator](05_simulator.md#6-a-drifting-stop)). {OTHER_SET_UPS.format(**gap)} No band term for it is planned. The cause of the field misses is not verified.",
    ]


def document(ctx: dict) -> str:
    parts = [
        "# Version 0.2: what changed, and what it does on the same data",
        "",
        "Written by `validation/make_v02_doc.py` from `validation/results/` and from the files of release "
        f"{RELEASE} (tag `{TAG}`). No number on this page is typed by hand.",
        "",
        "## Seen once",
        "",
        *seen_block(ctx),
        "",
        "## What changed and why",
        "",
        *changes_block(ctx),
        "",
        "## The checks of version 0.2",
        "",
        *x_block(ctx),
        "",
        f"## Every V and W check, {RELEASE} against 0.2",
        "",
        "The same checks with the same pass rules; the held-out systems as above.",
        "",
        *vw_block(ctx),
        f"## Development tests, {RELEASE} against 0.2",
        "",
        *dev_block(ctx),
        "",
        f"## Held-out tests, {RELEASE} against 0.2",
        "",
        *held_block(ctx),
        "",
        "## What the capacity measures, and what the checks compare it with",
        "",
        *mismatch_block(ctx),
        "",
        "## Rebuild",
        "",
        "```",
        "pip install -e .[dev]",
        "python validation/w7_cause.py              # X1",
        "python validation/capacity_chains.py       # X7",
        "python validation/run_capacity.py <id>     # capacity, one system",
        "python validation/synthetic_coverage.py    # V6",
        "python validation/synthetic_lag.py         # X3",
        "python validation/check_capacity.py        # V2-V8",
        "python validation/run_annex_vii.py <id>    # the record, one system",
        "python validation/synthetic_phase3.py      # W2, W5, W7, W8",
        "python validation/check_annex_vii.py       # W1-W10, X5",
        "python validation/check_v02.py --systems all   # X0, X2, X4, X8-X10",
        "python validation/make_v02_doc.py          # this page",
        "```",
        "",
        f"The {RELEASE} files are read with `git show {TAG}:<path>`. This page refuses results of any hash other than "
        f"capacity `{FROZEN['capacity']}` and record `{FROZEN['record']}` (X1: the {RELEASE} hashes, by design).",
        "",
        "## Source and credit",
        "",
        "Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in "
        "capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.",
        "Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 "
        "(CC BY-NC-ND 4.0): cited only; no figure or text of it is copied.",
        "The values on this page are computed by besslib from that data.",
        "Not a compliance certificate, not legal advice: besslib computes the Annex VII parameters; it does not certify them.",
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    ctx = load()
    (DOCS / PAGE).write_text(document(ctx), encoding="utf-8")
    print(f"docs/{PAGE} written: capacity {FROZEN['capacity']}, record {FROZEN['record']}")


if __name__ == "__main__":
    main()
