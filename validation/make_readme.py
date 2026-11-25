"""Write ``README.md`` from ``validation/readme_template.md`` and the result files.

Every number on the README is written here from a file in ``validation/results/``, from
``pyproject.toml``, ``CITATION.cff`` or the code itself. The template holds no number: check
R1 refuses a template with a digit left over after the allowed tokens (the Regulation,
licence names, Annex field codes, DOIs), and refuses a placeholder that is not filled.

Also written: ``docs/img/00_readme_05.png`` (system 05: the monthly capacity estimate with
its band and the field capacity tests) and ``validation/results/quickstart_05.csv`` (the
output of the quickstart, which this script runs).

Placeholders: ``{{name}}``, ``{{url:PATH}}`` (the file on GitHub at the release tag) and
``{{raw:PATH}}`` (the raw file at the release tag); ``{{url_main:PATH}}`` is the file on the main branch, for pages
that the release tag holds in an older form or not at all.

Usage:
    python validation/make_readme.py
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import re
import sys
import tomllib
from importlib import resources
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import besslib
from besslib.io import figgener
from besslib.soh import annex_vii
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

from make_reference_doc import GRID, INK, INK2, MUTED, SURFACE  # noqa: F401  (also sets the plot style)
from check_v02 import TAG, tag_file
from sim_drift_points import drift_points
from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
IMG = ROOT / "docs" / "img"
TEMPLATE = ROOT / "validation" / "readme_template.md"
PNG = {"Software": None}  # no version stamp: two runs give the same bytes
SAMPLE_SYSTEM = 5
VERSION_PAGE = ("0.2", "docs/04_version_0_2.md")  # the version page: what this version changed, every check before and after
BLUE = "#2a78d6"
RAW = "https://raw.githubusercontent.com/GhonAI/besslib"

QUICKSTART = """import besslib
reduced, spec = besslib.io.read_sample()     # {comment}
record = besslib.soh.annex_vii(None, spec, reduced=reduced)
print(record.to_frame())"""

ALLOWED = [
    r"2023/1542", r"CC BY-NC-ND 4\.0", r"CC BY 4\.0", r"BSD-3-Clause", r"Apache-2\.0", r"\b[AB][1-5]\b", r"10\.\d{4}/\S+",
]  # fmt: skip


def refuse(message: str) -> None:
    sys.exit(f"refused: {message}")


# ----------------------------------------------------------------------------- R1


def check_r1(template: str) -> None:
    """R1: no digit in the template outside the allowed tokens; no unknown placeholder syntax."""
    for number, line in enumerate(template.splitlines(), start=1):
        text = re.sub(r"\{\{[^}]*\}\}", "", line)
        for pattern in ALLOWED:
            text = re.sub(pattern, "", text)
        if re.search(r"\d", text):
            refuse(f"R1: the template has a digit on line {number}: {line!r}")
    print("R1: template digits 0")


# ----------------------------------------------------------------------------- sources


def project() -> dict:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    cff = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    found = re.search(r'^date-released:\s*"?([0-9-]+)"?\s*$', cff, re.M)
    if not found:
        refuse("CITATION.cff has no date-released line")
    released = pd.Timestamp(found.group(1))
    return {
        "version": meta["version"],
        "release_date": f"{released.day} {released:%B %Y}",
        "source": meta["urls"]["Source"].rstrip("/"),
        "issues_url": meta["urls"]["Issues"],
    }


def mb(size: float, digits: int = 0) -> str:
    return f"{size / 1e6:.{digits}f} MB"


def hashes() -> dict:
    cap2 = pd.read_csv(RESULTS / "checks_phase2.csv")
    cap3 = pd.read_csv(RESULTS / "checks_phase3.csv")
    capacity = set(cap2["config_hash"]) | set(cap3["capacity_config_hash"])
    record = set(cap3["record_config_hash"])
    if capacity != {config_hash(CapacityConfig())} or record != {config_hash(RecordConfig())}:
        refuse(f"the check files and the code disagree on the configuration hashes: {capacity} {record}")
    return {"capacity_hash": config_hash(CapacityConfig()), "record_hash": config_hash(RecordConfig())}


def check_row(table: pd.DataFrame, key: str) -> pd.Series:
    row = table[table["check"] == key]
    if len(row) != 1:
        refuse(f"checks_phase2.csv has no single row {key}")
    return row.iloc[0]


def result_word(result: str) -> str:
    words = {"pass": "passed", "fail": "failed"}
    if result not in words:
        refuse(f"unknown check result {result!r}")
    return words[result]


def parse(pattern: str, text: str, what: str) -> tuple:
    found = re.search(pattern, str(text))
    if not found:
        refuse(f"cannot read {what} from {text!r}")
    return found.groups()


def headline() -> dict:
    table = pd.read_csv(RESULTS / "checks_phase2.csv")
    v2, v3, v4 = (check_row(table, k) for k in ("V2", "V3", "V4"))
    (v2_value, _, v2_tests) = parse(r"median \|error\| ([\d.]+) points \((\d+)/(\d+) estimated\)", v2["value"], "V2")
    (v2_target,) = parse(r"<=\s*([\d.]+) points", v2["pass_if"], "V2 target")
    (v3_have, v3_tests) = parse(r"\((\d+)/(\d+) estimated\)", v3["value"], "V3")
    (v3_target,) = parse(r"<=\s*([\d.]+) points", v3["pass_if"], "V3 target")
    (v4_share, v4_tests) = parse(r"held the test in (\d+)% of (\d+)", v4["value"], "V4")
    (v4_target,) = parse(r">=\s*([\d.]+)\s*%", v4["pass_if"], "V4 target")
    level = inspect.signature(annex_vii).parameters["level"].default
    return {
        "v2_value": v2_value, "v2_tests": v2_tests, "v2_target": v2_target, "v2_result": result_word(v2["result"]),
        "v3_tests": v3_tests, "v3_missing": str(int(v3_tests) - int(v3_have)), "v3_target": v3_target,
        "v3_result": result_word(v3["result"]),
        "v4_share": v4_share, "v4_tests": v4_tests, "v4_target": v4_target, "v4_result": result_word(v4["result"]),
        "band_level": f"{level * 100:g}",
    }  # fmt: skip


NO_ESTIMATE = {  # why a test has no estimate, in the words of the README's first screen
    "not possible from this data: not enough history yet to set the full and empty levels": "in the first year",
    "not possible from this data: too few full-to-empty or empty-to-full pairs": "with too few full-to-empty pairs",
}


def held_tests(text: str | None = None) -> pd.DataFrame:
    """The held-out field tests of ``phase2_tests.csv``, today's or (``text``) the release tag's."""
    t = pd.read_csv(io.StringIO(text)) if text is not None else pd.read_csv(RESULTS / "phase2_tests.csv")
    t = t[t["set"] == "held-out"].copy()
    for col in ("capacity_ah", "error_points", "band_points"):
        t[col] = pd.to_numeric(t[col], errors="coerce")
    return t


def test_details() -> dict:
    """V2 over the tests with an estimate; V3's missing estimates by reason and the bias of the others."""
    t = held_tests()
    v2 = t[t["chemistry"] != "LFP"]
    v2_est = v2[v2["capacity_ah"].notna()]
    lfp = t[t["chemistry"] == "LFP"]
    lfp_est = lfp[lfp["capacity_ah"].notna()]
    missing = lfp.loc[lfp["capacity_ah"].isna(), "status"].value_counts()
    reasons = [f"{missing[k]} {v}" for k, v in NO_ESTIMATE.items() if k in missing]
    reasons += [f"{n} {k.removeprefix('not possible from this data: ')}" for k, n in missing.items() if k not in NO_ESTIMATE]
    bias = float(lfp_est["error_points"].median())
    return {
        "v2_have": str(len(v2_est)), "v2_est_error": f"{v2_est['error_points'].abs().median():.2f}",
        "v3_have": str(len(lfp_est)), "v3_reasons": ", ".join(reasons),
        "v3_bias": f"{abs(bias):.2f}", "v3_direction": "high" if bias > 0 else "low",
    }  # fmt: skip


def version_sentence() -> str:
    """What this version improved, what got worse, and why: held-out LMO/NMC and NMC tests, now against the tag."""
    def summary(t: pd.DataFrame) -> tuple[float, float, float, int, int]:
        """Median error over the tests with an estimate, median band width, median error over all tests (a test
        without an estimate counted as infinite, as V2 judges), and the two counts."""
        t = t[t["chemistry"] != "LFP"]
        est = t[t["capacity_ah"].notna()]
        return (float(est["error_points"].abs().median()), float(est["band_points"].median()),
                float(t["error_points"].abs().fillna(np.inf).median()), len(est), len(t))

    text = tag_file("validation/results/phase2_tests.csv")
    checks = tag_file("validation/results/checks_phase2.csv")
    if text is None or checks is None:
        refuse(f"the files of {TAG} cannot be read")
    (e_old, w_old, a_old, _, _), (e_new, w_new, a_new, n_est, n_all) = summary(held_tests(text)), summary(held_tests())
    pattern = r"held the test in (\d+)% of"
    (s_old,) = parse(pattern, check_row(pd.read_csv(io.StringIO(checks)), "V4")["value"], "V4 at the tag")
    (s_new,) = parse(pattern, check_row(pd.read_csv(RESULTS / "checks_phase2.csv"), "V4")["value"], "V4")
    if not (e_new < e_old and w_new < w_old and int(s_new) < int(s_old) and a_new > a_old):
        refuse("the version sentence no longer fits the numbers: write it again")
    return (
        f"**Version {VERSION_PAGE[0]}** is closer to the held-out field tests where it gives an estimate (median error "
        f"{e_old:.2f} → {e_new:.2f} points over the {n_est} tests with one), but the median over all {n_all}, which V2 "
        f"judges, rose ({a_old:.2f} → {a_new:.2f}); its band narrowed (median width {w_old:.2f} → {w_new:.2f} points) "
        f"and held fewer tests ({s_old} % → {s_new} %). The band is built for the capacity between two rest voltages, "
        "not for the charge a test measures between the stops; which band change narrowed it most is not separated. "
        f"[Why →]({{{{url_main:{VERSION_PAGE[1]}}}}}#what-the-capacity-measures-and-what-the-checks-compare-it-with)"
    )


def data_showed() -> str:
    """The stop drift on the LMO/NMC systems: the empty rest voltage per cell at the first and the last month end
    that has one, from the monthly capacity files (as on the version page)."""
    changes = []
    for sid in figgener.SYSTEM_IDS:
        spec = figgener.pack_spec(sid)
        if spec.chemistry != "LMO/NMC":
            continue
        m = pd.read_csv(RESULTS / "phase2" / f"system_{sid:02d}_monthly.csv")
        e = m[m["status"] == "estimated"].dropna(subset=["ref_empty_v"])
        if len(e) > 1:
            changes.append(1000 * (e["ref_empty_v"].iloc[-1] - e["ref_empty_v"].iloc[0]) / spec.cells_in_series)
    if not changes or max(changes) >= 0:
        refuse("the drift line no longer fits the numbers: write it again")
    d = drift_points()
    low, high = sorted(abs(round(c)) for c in changes)[0], sorted(abs(round(c)) for c in changes)[-1]
    return (
        f"**What the data showed.** On the {len(changes)} LMO/NMC systems the relaxed voltage of the empty rests fell by "
        f"{low} to {high} mV per cell between the first and the last month end that has one: the systems were "
        "discharged to a lower voltage as they aged (cause not verified). On simulated LMO packs a discharge stop that "
        f"lowers the empty rest voltage by {d['mv']} mV per cell hides fade in A1: the capacity between the first year's rest voltages is "
        f"{d['hidden_last']:.1f} points below the capacity between the voltages A1 is read at ({d['hidden_none']:.1f} without "
        f"drift). In this simulation, whose test stop falls with the daily stop, the drift does not account for the missed tests: the estimate reads {d['miss_none']:.2f} points above the simulated "
        f"test without drift and {d['miss_last']:.2f} with it. The cause of the field misses is not verified. "
        f"[Data →]({{{{url_main:{VERSION_PAGE[1]}}}}}#what-the-capacity-measures-and-what-the-checks-compare-it-with) "
        "[Simulator →]({{url_main:docs/05_simulator.md}}#6-a-drifting-stop)"
    )


def counts() -> dict:
    coverage = pd.read_csv(RESULTS / "coverage.csv")
    tests = pd.read_csv(RESULTS / "capacity_tests.csv")
    return {
        "n_systems": str(coverage["system_id"].nunique()),
        "n_months": f"{len(coverage):,}",
        "n_tests": str(len(tests)),
        "n_development": str(len(DEVELOPMENT)),
        "n_heldout": str(len([i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT])),
    }


# ----------------------------------------------------------------------------- tables


def cell(text) -> str:
    """A Markdown table cell: "|" escaped, and a "<" that opens a tag-like word (``<id>``) so GitHub shows it."""
    return re.sub(r"<(?=[A-Za-z/!?])", "&lt;", str(text).replace("|", "\\|").replace("\n", " "))


def md_table(header: list, rows: list) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def checks_table(path: Path, leave_out: tuple[str, ...] = ()) -> str:
    table = pd.read_csv(path, keep_default_na=False)
    rows = []
    for _, r in table.iterrows():
        if r["check"] in leave_out:
            continue
        info = r["result"] == ""
        rows.append([r["check"], r["scope"], r["value"], "information" if info else r["pass_if"],
                     "–" if info else {"pass": "passed", "fail": "failed"}.get(r["result"], r["result"])])  # fmt: skip
    return md_table(["Check", "Scope", "Value", "Pass if", "Result"], rows)


def annex_table() -> str:
    record = json.loads((RESULTS / "phase3" / f"system_{SAMPLE_SYSTEM:02d}_record.json").read_text(encoding="utf-8"))
    rows = [[f["code"], f'"{f["field"]}"', f["label"], f["method"]] for f in record["fields"]]
    return md_table(["Code", "Annex VII (quoted)", "What besslib reports", "Method and source"], rows)


def limits_list() -> str:
    limits = pd.read_csv(RESULTS / "limits.csv", keep_default_na=False)
    still, fixed = limits[limits["status"] == "open"], limits[limits["status"] == "fixed"]
    lines = [f"{k}. **{row.title}.** {row.readme}" for k, row in enumerate(still.itertuples(), start=1)]
    lines.append(
        f"{len(still) + 1}. Already published on the pages: the method needs regular full cycles; the first year of a "
        "record gives no capacity estimate; apparent charge loss is an upper bound for self-discharge; efficiency is DC "
        "at the battery terminals; resistance is a short-pulse value, not the impedance-spectroscopy one."
    )
    if len(fixed):
        lines += ["", f"Fixed in version {VERSION_PAGE[0]}, by the same rules: " + ", ".join(fixed["short"])
                  + " ([limits]({{url_main:docs/limits.md}}))."]  # fmt: skip
    return "\n".join(lines)


# ----------------------------------------------------------------------------- quickstart


def sample_days() -> tuple[pd.Timestamp, pd.Timestamp]:
    reduced, _ = besslib.io.read_sample()
    return pd.Timestamp(reduced.daily["day"].min()), pd.Timestamp(reduced.daily["day"].max())


def sig4(x) -> str:
    if pd.isna(x):
        return ""
    text = f"{float(f'{float(x):.4g}'):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def run_quickstart() -> tuple[str, str]:
    if not hasattr(besslib.io, "read_sample"):
        refuse("quickstart waits for read_sample")
    first, last = sample_days()
    _, spec = besslib.io.read_sample()
    (number,) = parse(r"system (\d+)", spec.name, "the system number of the sample")
    comment = f"system {number}, {first:%b %Y} – {last:%b %Y}, CC BY 4.0"
    code = QUICKSTART.format(comment=comment)
    scope: dict = {}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(code, scope)  # noqa: S102  the README's code is run, not copied
    scope["record"].to_frame().to_csv(RESULTS / "quickstart_05.csv", index=False, float_format="%.7g")
    frame = pd.read_csv(RESULTS / "quickstart_05.csv")
    rows = [
        [r.code, r.field, sig4(r.value), sig4(r.low), sig4(r.high), r.unit, sig4(r.events), r.status]
        for r in frame.itertuples()
    ]
    units = dict(zip(frame["code"], frame["unit"]))
    if units["A5"] != "ohm" or units["A3"] != "share" or "share" not in units["A2"]:
        refuse(f"the units of the frame changed: {units}")
    lead = "Output (the frame, as a table; resistance in ohm, efficiency and power capability as shares):"
    table = md_table(["code", "field", "value", "low", "high", "unit", "events", "status"], rows)
    return f"```python\n{code}\n```", f"{lead}\n\n{table}\n\n{quickstart_caveat(scope['record'], spec)}"


def quickstart_caveat(record, spec) -> str:
    """What the bands and the events of the quickstart are, and what A2 is relative to."""
    level = inspect.signature(annex_vii).parameters["level"].default
    (share, tests) = parse(r"held the test in (\d+)% of (\d+)", check_row(pd.read_csv(RESULTS / "checks_phase2.csv"), "V4")["value"], "V4")
    first = pd.Timestamp(record.field("A2").details["first_day"])
    b1 = record.field("B1")
    service = b1.details.get("date_put_into_service")
    no_manufacture = str(b1.details.get("date_of_manufacture_status", "")).startswith("not possible")
    return (
        f"Each band is a {level * 100:g} % band from its field's method (fit scatter, sensor accuracy or both; see the "
        f"method column below). On the {tests} held-out LMO/NMC and NMC field tests the capacity band held {share} %, so a "
        f"band is not the whole uncertainty. For A1, A3, A4 and A5, \"events\" is a count weighted by the "
        f"{CapacityConfig().tau_days:g}-day memory, hence the decimals; for A2 and B2–B5 it counts days, for B4 "
        f"discharges. A2 is relative to the first day of the resistance fit ({first:%Y-%m-%d})"
        + ((f", not to the system's installation ({pd.Timestamp(service):%Y-%m-%d}, in B1's details"
            + ("; B1 is not possible because the date of manufacture is missing)." if no_manufacture else ").")) if service else ".")
    )


def sample_note() -> str:
    first, last = sample_days()
    spec = figgener.pack_spec(SAMPLE_SYSTEM)
    folder = resources.files("besslib.data").joinpath("sample_05")
    size = sum(p.stat().st_size for p in Path(str(folder)).glob("*.parquet"))
    raw = json.loads((RESULTS / "zenodo_files.json").read_text(encoding="utf-8"))[f"Data_ID_{SAMPLE_SYSTEM:02d}.zip"]
    return (
        f"The sample is system {SAMPLE_SYSTEM:02d}, {spec.chemistry}, {spec.nominal_capacity_ah:g} Ah nominal, from "
        f"{first:%B %Y} to {last:%B %Y}: three reduced tables (one row per day, per current step and per rest "
        f"candidate, not the raw samples), {mb(size, 1)} in the package; the raw archive is {mb(raw['size'])}."
    )


def fulldata_code() -> str:
    raw = json.loads((RESULTS / "zenodo_files.json").read_text(encoding="utf-8"))[f"Data_ID_{SAMPLE_SYSTEM:02d}.zip"]
    return (
        "```\n"
        "git clone https://github.com/GhonAI/besslib && cd besslib\n"
        "pip install -e .[dev]\n"
        f"# downloads Data_ID_{SAMPLE_SYSTEM:02d}.zip ({mb(raw['size'])}), reduces it, deletes it, and writes\n"
        f"# validation/results/phase3/system_{SAMPLE_SYSTEM:02d}_record.json and system_{SAMPLE_SYSTEM:02d}_yearly.csv\n"
        f"python validation/run_annex_vii.py --held-out {SAMPLE_SYSTEM}\n"
        "```"
    )


# ----------------------------------------------------------------------------- figure


def figure() -> dict:
    sid = SAMPLE_SYSTEM
    monthly = pd.read_csv(RESULTS / "phase2" / f"system_{sid:02d}_monthly.csv", parse_dates=["time"])
    tests = pd.read_csv(RESULTS / "phase2" / f"system_{sid:02d}_tests.csv", parse_dates=["time"])
    coverage = pd.read_csv(RESULTS / "coverage.csv", parse_dates=["month"])
    months_with_data = set(coverage.loc[coverage["system_id"] == sid, "month"].dt.to_period("M"))
    ok = (monthly["status"] == "estimated").to_numpy()
    has_data = monthly["time"].dt.to_period("M").isin(months_with_data).to_numpy()
    show = ok & has_data
    span = monthly[monthly["time"].dt.to_period("M").isin(months_with_data)]
    gap_months = int((~has_data).sum())
    ref = float(monthly["reference_capacity_ah"].iloc[0])
    first_est = monthly.loc[ok, "time"].iloc[0]
    record_start = min(months_with_data).to_timestamp()

    def curve(column):
        return np.where(show, monthly[column], np.nan)

    fig, ax = plt.subplots(figsize=(6.4, 3.7))
    ax.axvspan(record_start, first_est - pd.offsets.MonthEnd(1), color=GRID, alpha=0.8, lw=0, zorder=0)
    ax.text(record_start + (first_est - record_start) / 2, 0.14, "no estimate:\nthe levels need\na year of history",
            transform=ax.get_xaxis_transform(), ha="center", va="center", fontsize=7.5, color=INK2)  # fmt: skip
    ax.axhline(ref, color=MUTED, lw=1, linestyle=(0, (3, 3)), zorder=1)
    ax.text(monthly["time"].iloc[-1], ref, f"nominal {ref:g} Ah", ha="right", va="bottom", fontsize=7.5, color=MUTED)
    ax.fill_between(monthly["time"], curve("capacity_ah_low"), curve("capacity_ah_high"), color=BLUE, alpha=0.22, lw=0,
                    zorder=2, label="monthly estimate, 90 % band")  # fmt: skip
    ax.plot(monthly["time"], curve("capacity_ah"), color=BLUE, lw=1.6, zorder=3)
    with_est = (tests["status"] == "estimated").to_numpy()
    ax.plot(tests["time"][with_est], tests["test_capacity_ah"][with_est], linestyle="none", marker="o", ms=7, color=INK,
            zorder=5, label="field capacity test, with an estimate")  # fmt: skip
    ax.plot(tests["time"][~with_est], tests["test_capacity_ah"][~with_est], linestyle="none", marker="o", ms=7,
            mfc=SURFACE, mec=INK, mew=1.5, zorder=5, label="field capacity test, no estimate")  # fmt: skip
    low = np.nanmin([monthly["capacity_ah_low"].min(), tests["test_capacity_ah"].min()])
    ax.set_ylim(low - 0.06 * (ref - low), ref + 0.12 * (ref - low))
    ax.set_xlim(record_start, monthly["time"].iloc[-1] + pd.Timedelta(days=20))
    ax.set_ylabel(f"Capacity, Ah (nominal {ref:g} Ah)")
    missing = monthly.loc[~has_data, "time"]
    if len(missing):  # the months without data are blank: say so in the plot
        ax.text(missing.iloc[0] + (missing.iloc[-1] - missing.iloc[0]) / 2, 0.5, "no data", transform=ax.get_xaxis_transform(),
                ha="center", va="center", fontsize=7.5, color=INK2)  # fmt: skip
    ax.legend(loc="upper right", fontsize=7.5, bbox_to_anchor=(1.0, 0.86), ncol=1, borderaxespad=0.3).set_zorder(6)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    IMG.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMG / "00_readme_05.png", dpi=150, metadata=PNG)
    plt.close(fig)

    n, k, held = len(tests), int(with_est.sum()), int(tests.loc[with_est, "held"].sum())
    chemistry = tests["chemistry"].iloc[0]
    caption = (
        f"System {sid:02d} ({chemistry}, {ref:g} Ah nominal): the capacity from operating data, at the end of each "
        f"month, with its 90 % band, against the {n} field capacity tests (dots). {k} of the {n} tests have an "
        f"estimate at their date, and the band holds the test value in {held} of them. The first estimate is at "
        f"the end of {first_est:%B %Y}: the first year gives none (grey). Hollow dots: tests without an estimate."
        + (f" {gap_months} month ends without data are left blank." if gap_months else "")
    )
    alt = (
        f"Capacity of system {sid:02d} in Ah from {record_start:%B %Y} to {span['time'].iloc[-1]:%B %Y}: a line "
        f"with its 90 % band from {first_est:%B %Y}, and the {n} field capacity tests as dots."
    )
    return {"figure_caption": caption, "figure_alt": alt}


# ----------------------------------------------------------------------------- main


def fill(template: str, values: dict, project_values: dict) -> str:
    version = project_values["version"]
    text = template
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    # Links last, so a filled value may carry a {{url:PATH}} of its own.
    text = re.sub(r"\{\{url:([^}]+)\}\}", lambda m: f"{project_values['source']}/blob/v{version}/{m.group(1)}", text)
    text = re.sub(r"\{\{url_main:([^}]+)\}\}", lambda m: f"{project_values['source']}/blob/main/{m.group(1)}", text)
    text = re.sub(r"\{\{raw:([^}]+)\}\}", lambda m: f"{RAW}/v{version}/{m.group(1)}", text)
    left = re.findall(r"\{\{[^}]*\}\}", text)
    if left:
        refuse(f"placeholders left unfilled: {sorted(set(left))}")
    return text


def main() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    check_r1(template)
    info = project()
    values = {**info, **hashes(), **headline(), **test_details(), **counts(), **figure()}
    values["quickstart_code"], values["quickstart_table"] = run_quickstart()
    values.update(
        sample_note=sample_note(),
        fulldata_code=fulldata_code(),
        annex_table=annex_table(),
        v_table=checks_table(RESULTS / "checks_phase2.csv"),
        w_table=checks_table(RESULTS / "checks_phase3.csv", leave_out=("X5",)),
        version_sentence=version_sentence(),
        data_showed=data_showed(),
        limits_list=limits_list(),
        previous_release=TAG.lstrip("v"),
        version_page_link=f"[version {VERSION_PAGE[0]} page]({{{{url_main:{VERSION_PAGE[1]}}}}})",
        data_credit=figgener.CREDIT,
        paper_credit=figgener.PAPER,
    )
    (ROOT / "README.md").write_text(fill(template, values, info), encoding="utf-8")
    print(f"README.md written: version {info['version']}, capacity {values['capacity_hash']}, record {values['record_hash']}")


if __name__ == "__main__":
    main()
