"""Write ``docs/limits.md`` and ``validation/results/limits.csv``: the known limits of version 0.3.

The four limits release 0.1.0 named are found again by the same rules, applied to today's result
files and to the files of the release tag v0.1.0 (``git show``). A limit whose rule finds nothing stays
under "Fixed in version 0.2", with the number its rule gives now; one it still finds stays open. The
limits version 0.3's own checks found (the simulator, V6) are open, with their rows. This
script holds no number of any system. Check R4 tokenizes this file and refuses any number literal
beyond a tiny set. The limits already published on the pages are listed with a link and no new
number.

Usage:
    python validation/make_limits.py
"""

from __future__ import annotations

import io
import json
import math
import re
import sys
import tokenize
from pathlib import Path

import pandas as pd

from besslib.io import figgener
from besslib.sim.duty import GridDuty
from besslib.soh import _core
from besslib.soh.config import CapacityConfig

from check_v02 import TAG, tag_file
from sim_case_leak import MEMORY_DAYS

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
DOCS = ROOT / "docs"
ALLOWED_NUMBERS = {"0", "1", "2", "100"}
MISMATCH = "04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with"
BAND = f"{CapacityConfig().level * 100:g} %"


def check_r4() -> None:
    """R4: this script's own source holds no number literal except a few small ones."""
    source = Path(__file__).read_text(encoding="utf-8")
    found = sorted(
        {t.string for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.NUMBER}
        - ALLOWED_NUMBERS
    )
    if found:
        sys.exit(f"R4: system literals in make_limits.py: {found}")
    print("R4: 0 system literals")


def systems_text(ids) -> str:
    return ", ".join(f"{int(i):02d}" for i in ids) if len(ids) else "none"


def records(tag: bool = False) -> list[tuple[int, dict]]:
    """Every record, today's or the release tag's: (system, fields by code)."""
    out = []
    for sid in figgener.SYSTEM_IDS:
        name = f"validation/results/phase3/system_{sid:02d}_record.json"
        text = tag_file(name) if tag else ((ROOT / name).read_text(encoding="utf-8") if (ROOT / name).exists() else None)
        if text is not None:
            out.append((sid, {f["code"]: f for f in json.loads(text)["fields"]}))
    if not out:
        sys.exit(f"no record files in validation/results/phase3{' at ' + TAG if tag else ''}")
    return out


def tag_csv(name: str, **kw) -> pd.DataFrame:
    text = tag_file(f"validation/results/{name}")
    if text is None:
        sys.exit(f"{TAG} has no validation/results/{name}")
    return pd.read_csv(io.StringIO(text), **kw)


# ----------------------------------------------------------------------------- the four rules


def w7_numbers(synth: pd.DataFrame, checks: pd.DataFrame) -> dict:
    rows = synth[synth["w7_error_ma"].notna()]
    n = len(rows)
    held = int(rows["w7_held"].astype(bool).sum())
    row = checks[checks["check"] == "W7"]
    if len(row) != 1 or f"{held} of {n}" not in str(row["value"].iloc[0]):
        sys.exit("a W7 row of checks_phase3.csv does not agree with synthetic_phase3.csv")
    err = rows["w7_error_ma"]
    return {"n": n, "held": held, "mean": err.mean(), "sd": err.std(ddof=1), "truth": rows["w7_truth_ma"].mean(),
            "above": int((err > 0).sum()), "result": str(row["result"].iloc[0])}  # fmt: skip


def limit_charge_loss() -> dict:
    now = w7_numbers(pd.read_csv(RESULTS / "synthetic_phase3.csv"), pd.read_csv(RESULTS / "checks_phase3.csv"))
    old = w7_numbers(tag_csv("synthetic_phase3.csv"), tag_csv("checks_phase3.csv"))
    limit = 2 * now["sd"] / math.sqrt(now["n"])
    residual = (f"just outside ±{limit:.2f} (2 sd/√{now['n']}): a small bias remains" if abs(now["mean"]) > limit
                else f"within ±{limit:.2f} (2 sd/√{now['n']})")  # fmt: skip
    found = (
        f"The band held the true drain in {now['held']} of {now['n']} packs ({now['held'] / now['n'] * 100:.0f} %). The "
        f"estimate is off by {now['mean']:+.2f} mA on average (standard deviation {now['sd']:.2f} mA) on a true drain of "
        f"{now['truth']:.0f} mA, {residual}."
    )
    before = (
        f"Release {TAG.lstrip('v')}: the band held {old['held']} of {old['n']}; {old['mean']:+.2f} mA on average "
        f"(standard deviation {old['sd']:.2f} mA); {old['above']} of {old['n']} packs read above the truth."
    )
    v03 = pd.read_csv(RESULTS / "checks_v03.csv", keep_default_na=False)
    v03 = v03[v03["check"] == "W7"]
    if len(v03) != 1 or f"{now['held']} of {now['n']}" not in str(v03["value"].iloc[0]):
        sys.exit("the W7 row of checks_v03.csv does not agree with synthetic_phase3.csv")
    rule03 = v03.iloc[0]
    fixed = now["result"] == "pass" and rule03["result"] == "pass"
    rule = ("W7 row, synthetic packs: the band's share, and the mean and the standard deviation of the error (`w7_error_ma`) "
            "against the true drain (`w7_truth_ma`); version 0.3's rule adds the mean clause "
            f"({rule03['pass_if']}: {rule03['result']}).")
    if fixed:
        return {"title": "Apparent charge loss (A4) was biased", "short": "A4 bias",
                "what": "The offset current of the capacity fit, and with it the apparent charge loss, read too high on synthetic packs.",
                "before": before, "found": found, "fixed": True, "rule": rule, "systems": "synthetic packs",
                "files": "validation/results/checks_phase3.csv, validation/results/checks_v03.csv, validation/results/synthetic_phase3.csv"}  # fmt: skip
    side = "below" if now["mean"] < 0 else "above"
    return {
        "title": "Apparent charge loss (A4) is biased on synthetic packs",
        "short": "A4 bias",
        "what": (f"The apparent charge loss reads {side} the true drain on synthetic packs: the band's share passes, the mean "
                 "error lies outside version 0.3's limit."),
        "found": f"{found} {before}",
        "readme": f"{found} Version 0.3's rule for W7 ({rule03['pass_if']}): {rule03['result']}.",
        "fixed": False,
        "rule": rule,
        "status": "Open: published as measured; no fix in 0.3.",
        "systems": "synthetic packs",
        "files": "validation/results/checks_phase3.csv, validation/results/checks_v03.csv, validation/results/synthetic_phase3.csv",
    }


def zero_capacity_hits(found: list[tuple[int, dict]]) -> list[tuple[int, float, str]]:
    hits = []
    for sid, fields in found:
        a1 = fields["A1"]
        if a1["value"] is not None and a1["value"] <= 0 and a1["status"] == "estimated":
            hits.append((sid, a1["value"], a1["unit"]))
    return hits


def limit_zero_capacity() -> dict:
    hits, old = zero_capacity_hits(records()), zero_capacity_hits(records(tag=True))
    months = []
    for path in sorted((RESULTS / "phase2").glob("system_*_monthly.csv")):
        table = pd.read_csv(path)
        bad = int(((table["capacity_ah"] <= 0) & (table["status"] == "estimated")).sum())
        if bad:
            months.append((int(path.stem.split("_")[1]), bad))
    rules = {_core.STATUS_TEXT[_core.LEVELS_CROSSED]: "crossed levels", _core.STATUS_TEXT[_core.WRONG_SIGN]: "a wrong-sign pair",
             _core.STATUS_TEXT[_core.OUT_OF_RANGE]: "a value or band at or below zero"}  # fmt: skip
    caught = []
    for sid, _, _ in old:
        table = pd.read_csv(RESULTS / "phase2" / f"system_{sid:02d}_monthly.csv")
        counts = table["status"].map(rules).value_counts()
        if len(counts):
            caught.append(f"System {sid:02d} now says `not possible from this data` at {int(counts.sum())} month ends ("
                          + ", ".join(f"{int(n)} with {k}" for k, n in counts.items()) + ")")
    text = "; ".join(f"system {s:02d}: {v:.2f} {u}" for s, v, u in hits) or "no record"
    found = f"Records with a capacity at or below zero and the status `estimated`: {text}."
    found += f" Month ends of the capacity tables with the same fault: {sum(b for _, b in months)}"
    noun = "system" if len(months) == 1 else "systems"
    found += f" in {noun} {systems_text([s for s, _ in months])}." if months else "."
    if caught:
        found += " " + "; ".join(caught) + "."
    before = f"Release {TAG.lstrip('v')}: " + ("; ".join(f"system {s:02d}, {v:.2f} {u} `estimated`" for s, v, u in old) or "no record") + "."
    return {
        "title": "Capacity at or below zero said estimated",
        "short": "capacity at or below zero",
        "what": "A capacity at or below zero is not a measurement, yet the status said `estimated`; it should say `not possible from this data`.",
        "before": before,
        "found": found,
        "fixed": not hits and not months,
        "rule": "A1 value at or below zero and A1 status `estimated`, over every record; the same rule over the month ends of the capacity tables, for information.",
        "systems": systems_text([s for s, _, _ in hits]),
        "files": "validation/results/phase3/system_*_record.json, validation/results/phase2/system_*_monthly.csv",
    }


def power_hits(found: list[tuple[int, dict]]) -> list[tuple[int, float]]:
    hits = []
    for sid, fields in found:
        a2, a5 = fields["A2"], fields["A5"]
        if a2["status"] == "estimated" and str(a5["status"]).startswith("not possible"):
            hits.append((sid, a2["value"]))
    return hits


def limit_power_without_resistance() -> dict:
    hits, old = power_hits(records()), power_hits(records(tag=True))
    listed = lambda h: "; ".join(f"system {s:02d} reads {v * 100:.0f} %" for s, v in h) or "none"  # noqa: E731
    return {
        "title": "Power capability estimated while resistance was not possible",
        "short": "power capability without resistance",
        "what": "A2 is derived from the resistance, so it should not be `estimated` where A5 is `not possible`.",
        "before": f"Release {TAG.lstrip('v')}: {listed(old)}.",
        "found": f"Power capability `estimated` while the resistance is `not possible`: {listed(hits)}.",
        "fixed": not hits,
        "rule": "A2 status `estimated` and A5 status starting with `not possible`, over every record.",
        "systems": systems_text([s for s, _ in hits]),
        "files": "validation/results/phase3/system_*_record.json",
    }


def sim_row(check: str, setting: str) -> pd.Series:
    """One row of the simulator checks of version 0.3 (seeds of ``sim_truth.HELD_OUT``)."""
    table = pd.read_csv(RESULTS / "checks_sim_v03.csv", keep_default_na=False)
    row = table[(table["check"] == check) & (table["what"].str.split(":").str[0] == setting)]
    if len(row) != 1:
        sys.exit(f"make_limits: checks_sim_v03.csv has {len(row)} rows for {check} {setting}")
    return row.iloc[0]


def head(text: str) -> str:
    """A result up to its first semicolon."""
    return str(text).split(";")[0]


def grab(pattern: str, text: str) -> tuple:
    m = re.search(pattern, str(text))
    if m is None:
        sys.exit(f"make_limits: {pattern!r} is not in {text!r}: write the sentence again")
    return m.groups()


def limit_band() -> dict:
    table = pd.read_csv(RESULTS / "synthetic_lag.csv")
    total, at_date = int(table["n_band"].sum()), int(table["n_held_at_date"].sum())
    ref_total, ref_held = int(table["n_reference"].sum()), int(table["n_held_reference"].sum())
    found = (
        f"On {len(table)} synthetic packs the monthly {BAND} band held the charge between the stops at the date in "
        f"{at_date} of {total} month ends ({at_date / total * 100:.0f} %); the estimate reads "
        f"{table['mean_lag_points'].mean():+.2f} points of nominal capacity above it on average. Against the capacity "
        f"between the rest voltages it reports, the band held {ref_held} of {ref_total} ({ref_held / ref_total * 100:.0f} %), "
        f"{table['mean_lag_reference_points'].mean():+.2f} points on average; that truth was chosen after the first results."
    )
    readme = (
        f"On {len(table)} synthetic packs the monthly {BAND} band held the charge between the stops at the date in {at_date} of "
        f"{total} month ends ({at_date / total * 100:.0f} %). It held the capacity between the rest voltages it reports in "
        f"{ref_held} of {ref_total} ({ref_held / ref_total * 100:.0f} %), against a truth chosen after the first results."
    )
    settings = ("D0", "D50", "D100", "D157")
    shares = [float(grab(r"\(([\d.]+)%\)", sim_row("T6b", d)["result"])[0]) for d in settings]
    rate = [float(grab(r"T_ops - T_test ([+-][\d.]+) \(rate\)", sim_row("T6d", d)["result"])[0]) for d in settings]
    return {
        "title": "The capacity band does not hold the charge between the BMS stops",
        "short": "band and BMS stops",
        "what": "Since version 0.2 the capacity is the capacity between a full and an empty rest at two relaxed voltages: the "
                "mean full and empty rest voltages of the memory time, which the record gives. A BMS stops at voltage limits "
                "under load. Its stops move as the cells age, so the charge between them is a different quantity. The band "
                "holds the first, not the second.",
        "found": found,
        "readme": readme,
        "fixed": False,
        "rule": "Share of month ends whose band holds the true charge between the stops at the date (release 0.1.0's rule), "
                "with the share against the capacity between the rest voltages beside it.",
        "status": f"On the simulator, version 0.3 holds the capacity it is built for ({head(sim_row('T6a', 'D0')['result'])} month ends "
                  f"without drift), but a simulated capacity test lies in its band at {min(shares):.1f} to {max(shares):.1f} % of the test "
                  f"dates: the test's rate gives {min(rate):+.2f} to {max(rate):+.2f} points of the gap "
                  "([simulator](05_simulator.md#version-03-on-new-seeds)). No band term for the test's quantity is planned. The cause of "
                  "the field misses is not verified ([version 0.3 page](06_version_0_3.md)).",
        "systems": "synthetic packs",
        "files": "validation/results/synthetic_lag.csv, validation/results/checks_sim_v03.csv",
    }


def limit_partial_windows() -> dict:
    kinds = (("nmc", "NMC"), ("lfp", "LFP"), ("string", "the string"))
    refused = [sim_row("T5a-L", f"{k}-never") for k, _ in kinds]
    wrong = [sim_row("T5a", f"{k}-never") for k, _ in kinds]
    if not all(r["passed"] == "True" for r in refused):
        sys.exit("make_limits: the refusal with limits did not pass on every pack: write the limit again")
    pattern = r"the estimate is a median ([\d.]+)% of the true capacity"
    share = ", ".join(f"{grab(pattern, r['result'])[0]} % ({name})"
                      for r, (_, name) in zip(wrong, kinds))
    text = (
        "Do not use the capacity (A1) on storage that works at a middle state of charge unless the pack's documented voltage "
        "limits are given (`PackSpec.voltage_max_v`, `voltage_min_v`). With them, version 0.3 refuses: on simulated packs cycled "
        f"daily between {GridDuty.cycle_low * 100:g} and {GridDuty.cycle_high * 100:g} % and never charged full, "
        f"{head(refused[0]['result'])}, for NMC, LFP and the string. Without them it still takes the edges of the daily window for "
        f"full and empty and reports a median {share} of the true capacity, with the status `estimated` and the label \"full and "
        "empty are the ends of the observed window; not checked against voltage limits\"."
    )
    return {
        "title": "Storage at a middle state of charge gets a wrong capacity without the pack's voltage limits",
        "short": "partial windows",
        "what": "Systems without regular full cycles need the partial-window form of the capacity fit (Plett's form). Version 0.3 "
                "refuses such storage when the pack's voltage limits are given; it cannot recognise it without them.",
        "found": text,
        "readme": text,
        "fixed": False,
        "rule": "The A1 estimate over the true capacity and the refusals, per setting, in case 5 of the pack simulator "
                "([page](05_simulator.md#version-03-on-new-seeds)).",
        "status": "Partly fixed: the refusal with documented limits is built; the partial-window form is not.",
        "systems": "simulated packs",
        "files": "validation/results/checks_sim_v03.csv, validation/results/sim_v03/grid.csv",
    }


def limit_a4_lag() -> dict:
    t3 = {k: sim_row("T3a", k) for k in ("leak0", "leak5", "leak15")}
    found = (f"On simulated packs with a cell leak the A4 band holds the true apparent charge loss at {head(t3['leak0']['result'])} "
             f"month ends without a leak, {head(t3['leak5']['result'])} at 5 mA and {head(t3['leak15']['result'])} at 15 mA, "
             f"before the onset and from {MEMORY_DAYS:g} days after it. Version 0.3's band is narrower; the estimate weighs its "
             f"history with a {CapacityConfig().tau_days:g}-day memory, so after a new leak it trails the change and the band no "
             "longer covers it.")
    return {
        "title": "Apparent charge loss (A4) trails a new leak",
        "short": "A4 after a leak",
        "what": "A4 is a weighted value over the memory time; a step in the true loss reaches it late.",
        "found": found,
        "fixed": False,
        "rule": "T3a of the pack simulator, case 3, and the four-setting diagnosis `validation/diag_a4_band.py` "
                "([page](05_simulator.md#version-03-on-new-seeds)).",
        "status": "Open: published as measured; no fix in 0.3.",
        "systems": "simulated packs",
        "files": "validation/results/checks_sim_v03.csv, validation/results/diag_a4_band.csv",
    }


def limit_fade_band() -> dict:
    row = pd.read_csv(RESULTS / "checks_phase2.csv", keep_default_na=False)
    v6 = row[row["check"] == "V6"].iloc[0]
    found = (f"On {v6['scope']} the band of the fade rate {head(v6['value']).replace('band held', 'held')} (rule "
             f"{v6['pass_if']}: {v6['result']}). The reading times move the rate's bias, the quadratic terms narrow its band; "
             "together the band is too narrow for the bias.")
    return {
        "title": "The fade rate's band is too narrow on synthetic packs",
        "short": "fade-rate band",
        "what": "The capacity fade per year (`fade_rate`) carries a band that should hold the true fade.",
        "found": found,
        "fixed": False,
        "rule": "V6, and the five-setting diagnosis `validation/diag_fade_band.py` ([version 0.3 page](06_version_0_3.md#why-v6-fell)).",
        "status": "Open: published as measured; no fix in 0.3.",
        "systems": "synthetic packs",
        "files": "validation/results/synthetic_coverage.csv, validation/results/diag_fade_band.csv",
    }


def check_name(check: str, scope: str) -> str:
    """A check as the limits list names it: V3 (LFP), V5 NMC, W2 (value) ..."""
    if check == "V3":
        return f"V3 ({scope.split()[-1]})"
    if check == "V4":
        return "V4 (the band)"
    if check == "V5":
        return f"V5 {scope.split()[1]}"
    if check == "W2":
        return "W2 (growth)" if "growth" in scope else "W2 (value)"
    return check


def joined(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def checks_with(file: str, result: str) -> list[str]:
    """The checks of a results table with this result, in their order, each once."""
    table = pd.read_csv(RESULTS / file)
    names = [check_name(c, str(sc)) for c, sc, r in zip(table["check"], table["scope"], table["result"]) if r == result]
    return list(dict.fromkeys(names))


def failed_text() -> tuple[str, str]:
    """The published failed checks, read from the check tables."""
    v_fail = checks_with("checks_phase2.csv", "fail")
    w_fail, w_none = checks_with("checks_phase3.csv", "fail"), checks_with("checks_phase3.csv", "not possible")
    v = f"The capacity checks {joined(v_fail)} failed." if v_fail else "No capacity check failed."
    w = (f"The checks {joined(w_fail)} failed" if w_fail else "No check of the other fields failed")
    w += (f" and {joined(w_none)} {'was' if len(w_none) == 1 else 'were'} not possible." if w_none else ".")
    return v, w


V_FAILED, W_FAILED = failed_text()

PUBLISHED = [
    ("The method needs regular full cycles, as in home storage; it does not suit mid-state-of-charge grid duty.",
     "02_capacity_validation.md#limits"),
    (V_FAILED, "02_capacity_validation.md#why-a-check-failed"),
    ("A4, the apparent charge loss, is an upper bound for self-discharge, not a measure of it.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("A3, the efficiency, is DC at the battery terminals, not the AC efficiency of a plant.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("A5 is a 1-second resistance, not the ohmic value of impedance spectroscopy.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("The first year of a record gives no capacity estimate, because the full and empty levels need a year of history.",
     "02_capacity_validation.md#limits"),
    (W_FAILED, "03_annex_vii.md#result"),
]


def main() -> None:
    check_r4()
    known = [limit_band(), limit_charge_loss(), limit_zero_capacity(), limit_power_without_resistance()]
    fixed = [f for f in known if f["fixed"]]
    still = [f for f in known if not f["fixed"]] + [limit_partial_windows(), limit_a4_lag(), limit_fade_band()]
    rows = [
        {"id": k, "status": "fixed" if f["fixed"] else "open", "title": f["title"], "short": f["short"],
         "found": f["found"], "readme": f.get("readme", f["found"]), "systems": f["systems"], "files": f["files"]}
        for k, f in enumerate(still + fixed, start=1)
    ]  # fmt: skip
    pd.DataFrame(rows).to_csv(RESULTS / "limits.csv", index=False)

    def files_line(f: dict) -> list[str]:
        return ["- **File:** " + ", ".join(f"`{name}`" for name in f["files"].split(", "))] if f["files"] else []

    lines = [
        "# Known limits of version 0.3",
        "",
        "Every number on this page is written by `validation/make_limits.py` from the result files, today's and those of "
        f"release {TAG.lstrip('v')} (tag `{TAG}`).",
        "",
        "## Open in version 0.3",
        "",
    ]
    for k, f in enumerate(still, start=1):
        lines += [f"### {k}. {f['title']}", "", f"- **What:** {f['what']}", f"- **The number:** {f['found']}",
                  f"- **The rule that found it:** {f['rule']}", *files_line(f), f"- **Status:** {f['status']}", ""]  # fmt: skip
    lines += ["## Fixed in version 0.2", "",
              "Found in release 0.1.0 and fixed in version 0.2: the same rule, applied to today's results, still finds no fault.", ""]  # fmt: skip
    for k, f in enumerate(fixed, start=1):
        lines += [f"### {k}. {f['title']}", "", f"- **What:** {f['what']}", f"- **Before:** {f['before']}",
                  f"- **Now:** {f['found']}", f"- **The rule:** {f['rule']}", *files_line(f), ""]  # fmt: skip
    lines += ["## Limits already published on the pages", ""]
    for text, link in PUBLISHED:
        lines.append(f"- {text} ([page]({link}))")
    lines += [
        "",
        "## Source and credit",
        "",
        "Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in capacity "
        "estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.",
        "Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 "
        "(CC BY-NC-ND 4.0): cited only; no figure or text of it is copied.",
        "",
    ]
    (DOCS / "limits.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"docs/limits.md written: {len(still)} open, {len(fixed)} fixed")


if __name__ == "__main__":
    main()
