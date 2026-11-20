"""Write ``docs/limits.md`` and ``validation/results/limits.csv``: the known limits of version 0.1.

Four limits that version 0.2 will fix are found by a rule applied to the result files; this
script holds no number of any system. Check R4 tokenizes this file and refuses any number
literal beyond a tiny set. The limits already published on the pages are listed with a link
and no new number.

Usage:
    python validation/make_limits.py
"""

from __future__ import annotations

import io
import json
import sys
import tokenize
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
DOCS = ROOT / "docs"
ALLOWED_NUMBERS = {"0", "1", "2", "100"}


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


def records() -> list[tuple[int, dict]]:
    out = []
    for path in sorted((RESULTS / "phase3").glob("system_*_record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        out.append((int(path.stem.split("_")[1]), {f["code"]: f for f in record["fields"]}))
    if not out:
        sys.exit("no record files in validation/results/phase3")
    return out


def limit_charge_loss() -> dict:
    table = pd.read_csv(RESULTS / "synthetic_phase3.csv")
    rows = table[table["w7_error_ma"].notna()]
    n = len(rows)
    held = int(rows["w7_held"].astype(bool).sum())
    above = int((rows["w7_error_ma"] > 0).sum())
    mean, sd, truth = rows["w7_error_ma"].mean(), rows["w7_error_ma"].std(ddof=1), rows["w7_truth_ma"].mean()
    checks = pd.read_csv(RESULTS / "checks_phase3.csv")
    row = checks[checks["check"] == "W7"]
    if len(row) != 1 or f"{held} of {n}" not in str(row["value"].iloc[0]):
        sys.exit("the W7 row of checks_phase3.csv does not agree with synthetic_phase3.csv")
    found = (
        f"The band held the true drain in {held} of {n} packs ({held / n * 100:.0f} %). The estimate is off by "
        f"{mean:+.2f} mA on average (standard deviation {sd:.2f} mA) on a true drain of {truth:.0f} mA; "
        f"{above} of {n} packs read above the truth."
    )
    return {
        "title": "Apparent charge loss (A4) is biased",
        "what": "The offset current of the capacity fit, and with it the apparent charge loss, reads too high on synthetic packs.",
        "found": found,
        "rule": "W7 row, synthetic packs: the mean and the standard deviation of the error (`w7_error_ma`) against the true drain (`w7_truth_ma`).",
        "systems": "synthetic packs",
        "files": "validation/results/checks_phase3.csv, validation/results/synthetic_phase3.csv",
    }


def limit_zero_capacity() -> dict:
    hits = []
    for sid, fields in records():
        a1 = fields["A1"]
        if a1["value"] is not None and a1["value"] <= 0 and a1["status"] == "estimated":
            hits.append((sid, a1["value"], a1["unit"]))
    months = []
    for path in sorted((RESULTS / "phase2").glob("system_*_monthly.csv")):
        table = pd.read_csv(path)
        bad = int(((table["capacity_ah"] <= 0) & (table["status"] == "estimated")).sum())
        if bad:
            months.append((int(path.stem.split("_")[1]), bad))
    found = "; ".join(f"system {s:02d}: {v:.2f} {u}" for s, v, u in hits) or "no record"
    found = f"Records with a capacity at or below zero and the status `estimated`: {found}."
    found += f" Month ends of the capacity tables with the same fault: {sum(b for _, b in months)}"
    noun = "system" if len(months) == 1 else "systems"
    found += f" in {noun} {systems_text([s for s, _ in months])}." if months else "."
    return {
        "title": "Capacity at or below zero still says estimated",
        "what": "A capacity at or below zero is not a measurement, yet the status says `estimated`; it should say `not possible from this data`.",
        "found": found,
        "rule": "A1 value at or below zero and A1 status `estimated`, over every record; the same rule over the month ends of the capacity tables, for information.",
        "systems": systems_text([s for s, _, _ in hits]),
        "files": "validation/results/phase3/system_*_record.json, validation/results/phase2/system_*_monthly.csv",
    }


def limit_lag() -> dict:
    table = pd.read_csv(RESULTS / "synthetic_lag.csv")
    total = int(table["n_band"].sum())
    at_date = int(table["n_held_at_date"].sum())
    at_age = int(table["n_held_at_mean_age"].sum())
    found = (
        f"On {len(table)} synthetic packs the monthly 90 % band held the true usable capacity at the date in "
        f"{at_date} of {total} month ends ({at_date / total * 100:.0f} %), and at the weighted mean age of the pairs "
        f"in memory in {at_age} ({at_age / total * 100:.0f} %). The estimate reads {table['mean_lag_points'].mean():+.2f} "
        f"points of nominal capacity above the truth at the date on average."
    )
    return {
        "title": "The capacity band leaves out the lag",
        "what": "The estimate is causal and weighs the past, so on a fading pack it lags the capacity at the date; the band does not include the lag.",
        "found": found,
        "rule": "Share of month ends whose band holds the true usable capacity at the date, against the share at the weighted mean age of the pairs.",
        "systems": "synthetic packs",
        "files": "validation/results/synthetic_lag.csv",
    }


def limit_power_without_resistance() -> dict:
    hits = []
    for sid, fields in records():
        a2, a5 = fields["A2"], fields["A5"]
        if a2["status"] == "estimated" and str(a5["status"]).startswith("not possible"):
            hits.append((sid, a2["value"]))
    found = "Power capability `estimated` while the resistance is `not possible`: " + "; ".join(
        f"system {s:02d} reads {v * 100:.0f} %" for s, v in hits
    )
    return {
        "title": "Power capability estimated while resistance is not possible",
        "what": "A2 is derived from the resistance, so it should not be `estimated` where A5 is `not possible`.",
        "found": found + ".",
        "rule": "A2 status `estimated` and A5 status starting with `not possible`, over every record.",
        "systems": systems_text([s for s, _ in hits]),
        "files": "validation/results/phase3/system_*_record.json",
    }


PUBLISHED = [
    ("The method needs regular full cycles, as in home storage; it does not suit mid-state-of-charge grid duty.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("The capacity checks V3 (LFP) and V4 (the band) failed.", "02_capacity_validation.md#why-a-check-failed"),
    ("A4, the apparent charge loss, is an upper bound for self-discharge, not a measure of it.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("A3, the efficiency, is DC at the battery terminals, not the AC efficiency of a plant.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("A5 is a 1-second resistance, not the ohmic value of impedance spectroscopy.",
     "03_annex_vii.md#method-and-limits-per-field"),
    ("The first year of a record gives no capacity estimate, because the full and empty levels need a year of history.",
     "02_capacity_validation.md#limits"),
    ("The checks W2 (value), W6 and W7 failed and W3 was not possible.", "03_annex_vii.md#result"),
]


def main() -> None:
    check_r4()
    found = [limit_charge_loss(), limit_zero_capacity(), limit_lag(), limit_power_without_resistance()]
    rows = [
        {"id": k, "title": f["title"], "found": f["found"], "systems": f["systems"], "files": f["files"]}
        for k, f in enumerate(found, start=1)
    ]
    pd.DataFrame(rows).to_csv(RESULTS / "limits.csv", index=False)

    lines = [
        "# Known limits of version 0.1",
        "",
        "Every number on this page is written by `validation/make_limits.py` from the result files.",
        "",
        "## To be fixed in version 0.2",
        "",
    ]
    for k, f in enumerate(found, start=1):
        lines += [
            f"### {k}. {f['title']}",
            "",
            f"- **What:** {f['what']}",
            f"- **The number:** {f['found']}",
            f"- **The rule that found it:** {f['rule']}",
            "- **File:** " + ", ".join(f"`{name}`" for name in f["files"].split(", ")),
            "- **To be fixed in version 0.2.**",
            "",
        ]
    lines += ["## Limits already published on the pages", ""]
    for text, link in PUBLISHED:
        lines.append(f"- {text} ([page]({link}))")
    lines.append("")
    (DOCS / "limits.md").write_text("\n".join(lines), encoding="utf-8")
    print("docs/limits.md written")


if __name__ == "__main__":
    main()
