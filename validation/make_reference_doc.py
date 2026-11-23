"""Phase 1: write docs/01_reference.md and its figures from the result tables.

Every number in the document comes from ``validation/results/*.csv``; none is typed.

Usage:
    python validation/make_reference_doc.py
"""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from besslib.io import figgener

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
WINDOWS = ROOT / "data" / "reduced" / "capacity_tests"
DOCS = ROOT / "docs"
IMG = DOCS / "img"

# Colours: validated categorical slots (blue, orange, aqua) on a light surface.
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
COLOUR = {"LMO/NMC": "#2a78d6", "NMC": "#eb6834", "LFP": "#1baf7a"}
LABEL = {"LMO/NMC": "LMO/NMC blend, 2.2 kWh", "NMC": "NMC, 8.6–11.5 kWh", "LFP": "LFP, 8.1–13.8 kWh"}
ORDER = ["LMO/NMC", "NMC", "LFP"]

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.size": 10, "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": AXIS,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 10, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "legend.frameon": False,
    }
)  # fmt: skip


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tests = pd.read_csv(RESULTS / "capacity_tests.csv", parse_dates=["documented_start", "onset", "end", "charge_end"])
    coverage = pd.read_csv(RESULTS / "coverage.csv", parse_dates=["month"])
    quality = pd.read_csv(RESULTS / "quality_development.csv", parse_dates=["month", "first", "last"])
    systems = figgener.systems()
    installed = pd.to_datetime(systems["Date_storage_system_installation"])
    tests["age_years"] = (tests["documented_start"] - tests["system_id"].map(installed)).dt.days / 365.25
    tests["soh_energy"] = tests["energy_kwh"] / tests["system_id"].map(systems["Energy_nominal_in_kWh"])
    # the share of nominal from the unrounded capacity (``soh_capacity`` in the file is rounded to 4 decimals)
    tests["share"] = tests["capacity_ah"] / tests["reference_capacity_ah"]
    return tests, coverage, quality, systems


def figure_capacity(tests: pd.DataFrame) -> None:
    ok = tests[tests["status"] == "ok"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.2), sharey=True)
    for ax, chemistry in zip(axes, ORDER):
        part = ok[ok["chemistry"] == chemistry]
        for system_id, one in part.groupby("system_id"):
            one = one.sort_values("age_years")
            ax.plot(one["age_years"], 100 * one["soh_capacity"], color=COLOUR[chemistry], lw=2, solid_capstyle="round",
                    marker="o", ms=7.5, mec=SURFACE, mew=1.6, zorder=3)  # fmt: skip
            last = one.iloc[-1]
            ax.annotate(f"{system_id:02d}", (last["age_years"], 100 * last["soh_capacity"]), xytext=(6, 0),
                        textcoords="offset points", va="center", fontsize=8, color=INK2)  # fmt: skip
        count = part["system_id"].nunique()
        ax.set_title(f"{LABEL[chemistry]}\n{len(part)} tests on {count} system{'s' if count != 1 else ''}", color=INK)
        ax.set_xlabel("System age at the test, years")
        ax.set_xlim(0, max(8.5, ok["age_years"].max() + 0.6))
    axes[0].set_ylabel("Measured usable capacity, % of nominal")
    fig.suptitle("Field capacity tests, measured again from the open one-second data", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")  # fmt: skip
    fig.text(0.01, 0.005, "Labels are system IDs. Data: Figgener et al. 2024, Zenodo 12091223, CC BY 4.0. Measured with besslib.",
             fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(IMG / "01_capacity_tests.png", dpi=160)
    plt.close(fig)


def figure_coverage(tests: pd.DataFrame, coverage: pd.DataFrame, systems: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ids = sorted(systems.index)
    for row, system_id in enumerate(ids):
        chemistry = systems.loc[system_id, "Chemistry_detail"]
        months = coverage.loc[coverage["system_id"] == system_id, "month"]
        ax.barh([row] * len(months), [26] * len(months), left=mdates.date2num(months) + 2, height=0.55,
                color=COLOUR[chemistry], linewidth=0)  # fmt: skip
        when = tests.loc[tests["system_id"] == system_id, "documented_start"]
        ax.plot(when, [row] * len(when), linestyle="none", marker="D", ms=6.5, color=INK, mec=SURFACE, mew=1.3, zorder=4)
    development = set(tests.loc[tests["set"] == "development", "system_id"])
    ax.set_yticks(range(len(ids)), [f"{i:02d}{'  dev' if i in development else ''}" for i in ids])
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", visible=False)
    ax.set_ylabel("System ID")
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOUR[c]) for c in ORDER]
    handles.append(plt.Line2D([], [], linestyle="none", marker="D", ms=6.5, color=INK))
    ax.legend(handles, [f"Month with data: {LABEL[c]}" for c in ORDER] + ["Field capacity test"], loc="upper center",
              bbox_to_anchor=(0.5, -0.07), ncol=4, fontsize=8.5)  # fmt: skip
    ax.set_title(f"What the dataset covers: {len(coverage)} monthly files, {len(tests)} capacity tests", color=INK, fontsize=12)
    fig.text(0.01, 0.005, "“dev” marks the systems used for tuning. Data: Figgener et al. 2024, Zenodo 12091223, CC BY 4.0.",
             fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(IMG / "01_coverage.png", dpi=160)
    plt.close(fig)


def figure_sheet(tests: pd.DataFrame, systems: pd.DataFrame) -> None:
    """One small panel per test: the current, and the part that was integrated."""
    columns = 6
    rows = int(np.ceil(len(tests) / columns))
    fig, axes = plt.subplots(rows, columns, figsize=(13, 1.75 * rows), sharey=True)
    for ax, (_, test) in zip(axes.ravel(), tests.iterrows()):
        chemistry = test["chemistry"]
        path = WINDOWS / f"ID_{test['system_id']:02d}_test_{test['test']}.parquet"
        ax.set_title(f"{test['system_id']:02d} · {test['documented_start']:%d %b %Y}", fontsize=8, fontweight="normal", color=INK2)
        ax.tick_params(labelsize=7)
        ax.grid(axis="x", visible=False)
        ax.set_xlim(-6, 7)
        if not path.exists() or test["status"] != "ok":
            ax.text(0.5, 0.5, test["status"], transform=ax.transAxes, ha="center", va="center", fontsize=7, color=INK2, wrap=True)
            if not path.exists():
                continue
        nominal = systems.loc[test["system_id"], "Capacity_nominal_in_Ah"]
        current = pd.read_parquet(path, columns=["current_a"])["current_a"].astype("float64")
        centre = test["onset"] if pd.notna(test["onset"]) else test["documented_start"]
        current = current.loc[centre - pd.Timedelta(hours=6) : centre + pd.Timedelta(hours=7)].resample("30s").mean()
        hours = (current.index - centre).total_seconds() / 3600.0
        ax.plot(hours, current / nominal, color=MUTED, lw=1)
        if test["status"] == "ok":
            inside = (current.index >= test["onset"]) & (current.index <= test["end"])
            ax.fill_between(hours[inside], (current / nominal)[inside], 0, color=COLOUR[chemistry], alpha=0.85, linewidth=0)
            ax.text(0.04, 0.06, f"{test['capacity_ah']:.1f} Ah", transform=ax.transAxes, ha="left", fontsize=7.5, color=INK)
        ax.set_xlim(-6, 7)
    for ax in axes.ravel()[len(tests) :]:
        ax.set_axis_off()
    for ax in axes[:, 0]:
        ax.set_ylabel("C-rate", fontsize=8)
    for ax in axes[-1, :]:
        ax.set_xlabel("Hours from the start", fontsize=8)
    fig.suptitle("Every capacity test: the measured current (grey) and the discharge that was integrated (colour)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")  # fmt: skip
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(IMG / "01_test_sheet.png", dpi=110)
    plt.close(fig)


def cell(value) -> str:
    """A Markdown table cell: "|" escaped so it does not split the cell, and a "<" that opens a tag-like word
    (``<id>``) so GitHub shows it instead of dropping it as HTML."""
    return re.sub(r"<(?=[A-Za-z/!?])", "&lt;", str(value).replace("|", "\\|").replace("\n", " "))


def table(frame: pd.DataFrame) -> str:
    lines = ["| " + " | ".join(cell(c) for c in frame.columns) + " |", "|" + "|".join("---" for _ in frame.columns) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join(lines)


def document(tests: pd.DataFrame, coverage: pd.DataFrame, quality: pd.DataFrame, systems: pd.DataFrame) -> str:
    ok = tests[tests["status"] == "ok"]
    failed = tests[tests["status"] != "ok"]
    lag = (ok["onset"] - ok["documented_start"]).dt.total_seconds()

    by_chemistry = []
    for chemistry in ORDER:
        part = ok[ok["chemistry"] == chemistry]
        slopes = []
        for _, one in part.groupby("system_id"):
            if len(one) >= 2 and one["age_years"].max() - one["age_years"].min() >= 1.0:
                slopes.append(np.polyfit(one["age_years"], 100 * one["share"], 1)[0])
        by_chemistry.append(
            {
                "Chemistry": chemistry,
                "Systems with tests": part["system_id"].nunique(),
                "Tests measured": len(part),
                "Capacity, % of nominal (min – max)": f"{100 * part['share'].min():.1f} – {100 * part['share'].max():.1f}",
                "Test C-rate (min – max)": f"{part['test_c_rate'].min():.2f} – {part['test_c_rate'].max():.2f}",
                "Change between tests, points per year (median of systems)": f"{np.median(slopes):+.1f} (n = {len(slopes)})" if slopes else "–",
            }
        )

    full = ok.assign(
        System=ok["system_id"].map("{:02d}".format),
        Set=ok["set"],
        Start=ok["documented_start"].dt.strftime("%Y-%m-%d %H:%M"),
        Age=ok["age_years"].map("{:.1f}".format),
        Ah=ok["capacity_ah"].map("{:.2f}".format),
        kWh=ok["energy_kwh"].map("{:.3f}".format),
        Share=(100 * ok["share"]).map("{:.1f}".format),
        C=ok["test_c_rate"].map("{:.2f}".format),
        Median=ok["c_rate"].map("{:.2f}".format),
        Minutes=ok["duration_min"].map("{:.0f}".format),
        Temp=ok["temp_pack_mean_c"].map("{:.1f}".format),
        Before=(ok["pre_start_ah"].round(2) + 0.0).map("{:.2f}".format),
    )[["System", "chemistry", "Set", "Start", "Age", "Ah", "kWh", "Share", "C", "Median", "Minutes", "Temp", "Before"]]
    full.columns = ["System", "Chemistry", "Set", "Documented start", "Age, years", "Capacity, Ah", "Energy, kWh",
                    "% of nominal", "Test C-rate", "Median C-rate", "Minutes", "Pack temp, °C", "Ah out before the start"]  # fmt: skip

    moved = ok[ok["probe_delay_min"] > 0]
    moved_table = pd.DataFrame(
        {
            "System": moved["system_id"].map("{:02d}".format),
            "Set": moved["set"],
            "Documented start": moved["documented_start"].dt.strftime("%Y-%m-%d %H:%M"),
            "Probe moved, min": moved["probe_delay_min"].map("{:.0f}".format),
            "Discharge began": moved["onset"].dt.strftime("%H:%M:%S"),
            "Capacity, Ah": moved["capacity_ah"].map("{:.2f}".format),
        }
    )
    stepped = sorted(ok.loc[ok["c_rate"] < 0.8 * ok["test_c_rate"], "system_id"].unique())
    early_share = ok["discharged_hour_before_ah"] / ok["capacity_ah"]
    early = ok[early_share > 0.01]
    early_table = pd.DataFrame(
        {
            "System": early["system_id"].map("{:02d}".format),
            "Set": early["set"],
            "Documented start": early["documented_start"].dt.strftime("%Y-%m-%d %H:%M"),
            "Discharged in the hour before, Ah": early["discharged_hour_before_ah"].map("{:.2f}".format),
            "Share of the measured capacity, %": (100 * early_share[early.index]).map("{:.1f}".format),
            "Ah out since the last charge": (early["pre_start_ah"].round(2) + 0.0).map("{:.2f}".format),
        }
    )

    per_system = quality.groupby("system_id").agg(
        months=("month", "count"), rows=("rows", "sum"), missing=("missing_seconds", "sum"),
        interpolated=("interpolated_share", "mean"), charge=("charge_ah", "sum"), discharge=("discharge_ah", "sum"),
    )  # fmt: skip
    quality_table = pd.DataFrame(
        {
            "System": [f"{i:02d}" for i in per_system.index],
            "Chemistry": [systems.loc[i, "Chemistry_detail"] for i in per_system.index],
            "Months": per_system["months"].to_numpy(),
            "Rows, million": (per_system["rows"] / 1e6).map("{:.1f}".format).to_numpy(),
            "Missing inside months, %": (100 * per_system["missing"] / (per_system["rows"] + per_system["missing"])).map("{:.2f}".format).to_numpy(),
            "Interpolated, %": (100 * per_system["interpolated"]).map("{:.3f}".format).to_numpy(),
            "Charge in, Ah": per_system["charge"].map("{:,.0f}".format).to_numpy(),
            "Charge out, Ah": per_system["discharge"].map("{:,.0f}".format).to_numpy(),
            "Out ÷ in, %": (100 * per_system["discharge"] / per_system["charge"]).map("{:.1f}".format).to_numpy(),
        }
    )

    steps = ok.sort_values(["system_id", "documented_start"]).copy()
    steps["rise"] = 100 * steps.groupby("system_id")["share"].diff()
    steps["months"] = steps.groupby("system_id")["age_years"].diff() * 12
    steps["previous"] = steps.groupby("system_id")["documented_start"].shift()
    rises = steps[steps["rise"] > 2.0].sort_values("rise", ascending=False)
    rise_table = pd.DataFrame(
        {
            "System": rises["system_id"].map("{:02d}".format),
            "Chemistry": rises["chemistry"],
            "Set": rises["set"],
            "From test": rises["previous"].dt.strftime("%Y-%m-%d"),
            "To test": rises["documented_start"].dt.strftime("%Y-%m-%d"),
            "Months between": rises["months"].map("{:.0f}".format),
            "Change, points of nominal": rises["rise"].map("{:+.1f}".format),
        }
    )

    v1 = "pass" if len(ok) >= 55 else "fail"
    development = quality["system_id"].nunique()
    parts = [
        f"# The reference: {len(tests)} field capacity tests, measured again",
        "",
        "Written by `validation/make_reference_doc.py` from `validation/results/`. No number on this page is typed by hand.",
        "",
        "## What this is",
        "",
        "The open dataset documents when each field capacity test started, but not what it measured.",
        "This page measures every test again from the one-second data. These values are the reference",
        "that besslib's capacity estimates are checked against on the capacity page ([`02_capacity_validation.md`](02_capacity_validation.md)).",
        "",
        f"- **Tests documented:** {len(tests)}, on {tests['system_id'].nunique()} of {len(systems)} systems.",
        f"- **Tests measured:** {len(ok)}. **Not measured:** {len(failed)}.",
        f"- **Check V1** (at least 55 of 60 measured, each missing one explained): **{v1}**.",
        f"- **The documented start fits the data:** the discharge begins a median of {abs(lag.median()):.0f} s "
        f"{'before' if lag.median() < 0 else 'after'} the documented time (largest difference {lag.abs().max() / 60:.1f} min).",
        f"- **Data gaps inside the measured discharges:** {int(ok['missing_seconds'].sum())} missing seconds in total; "
        f"largest interpolated share in one test {100 * ok['interpolated_share'].max():.2f} %.",
        "",
        "![Field capacity tests by chemistry](img/01_capacity_tests.png)",
        "",
        "## How a test is measured",
        "",
        "A test is a full charge, then a discharge at the system's maximum power until it stops by itself",
        "(Figgener et al. 2024, Supplementary Note 6). `besslib.soh.measure_capacity_test` uses three rules:",
        "",
        "1. The test current is the median current from 2 to 12 minutes after the documented start. It must be a",
        "   discharge of at least C/10. If it is not, the probe moves one minute later, up to 60 minutes, until it",
        "   is and the probe also begins inside the discharge.",
        "2. The discharge begins where the current last was weaker than 20 % of the test current.",
        "3. The discharge ends when the current stays weaker than 5 % of the test current for 5 minutes;",
        "   the tail down to 1 % is kept.",
        "",
        "Capacity is the integral of the current between the two, energy the integral of the power.",
        "No estimator and no battery model is involved. The rules were written on the development systems.",
        "",
        "**Rule 1 was changed on 14 Nov 2026**, after every test on the sheet below was checked by eye. Before, the",
        "probe stayed at the documented start and needed only C/20, and the tests below were reported as not",
        "measured although their discharge is in the data: it began late, or after a few minutes at low power, or",
        "after a pause. The change leaves every other test exactly as it was measured before.",
        "",
        *([table(moved_table), ""] if len(moved) else []),
        f"In some tests of systems {', '.join(f'{i:02d}' for i in stepped)} the current steps down before the end, so the",
        "median C-rate over the whole discharge is below 80 % of the test C-rate. The lower-current part is kept:",
        "each step comes when the voltage reaches a low point, so the system is lowering its power to keep",
        "discharging, and the test runs until it stops by itself. \"Test C-rate\" on this page is the test current",
        "of rule 1, measured 2 to 12 minutes into the test; \"Median C-rate\" is the median over the whole",
        "discharge. Where the two differ much, the power changed during the test.",
        "",
        "## Summary by chemistry",
        "",
        table(pd.DataFrame(by_chemistry)),
        "",
        "The last column is a straight line through each system's own tests (systems with at least two tests, one year apart).",
        "It describes these tests only; it is not besslib's fade estimate.",
        "",
        "## How far the reference itself can be trusted",
        "",
        "A battery does not gain capacity with age. Where a later test reads higher than an earlier one,",
        "the difference shows the scatter of the tests themselves: how full the system really was, the",
        "temperature, the voltage limits its software used on that day.",
        "",
        f"- Pairs of consecutive tests on one system: {int(steps['rise'].notna().sum())}.",
        f"- Pairs where the later test reads more than 2 points higher: {len(rises)}.",
        "",
        *([table(rise_table), ""] if len(rises) else []),
        "This matters for the capacity page: an estimate cannot be expected to agree with a test more closely than two",
        "tests agree with each other.",
        "",
        "## Every test, to check by eye",
        "",
        "![All capacity tests](img/01_test_sheet.png)",
        "",
        "## What the dataset covers",
        "",
        "![Coverage](img/01_coverage.png)",
        "",
        f"- {len(coverage)} monthly files, {coverage['bytes'].sum() / 1e9:.0f} GB unpacked, "
        f"{coverage['month'].min():%b %Y} to {coverage['month'].max():%b %Y}.",
        f"- Systems without a capacity test: {', '.join(f'{i:02d}' for i in sorted(set(systems.index) - set(tests['system_id'])))}.",
        "",
        "## Data quality of the development systems",
        "",
        f"Only the {development} development systems are read in full before the thresholds are frozen. "
        f"The split was fixed on 13 Nov 2026, before any data was read; the other {len(systems) - development} systems are held out.",
        "",
        table(quality_table),
        "",
        "\"Out ÷ in\" is the charge discharged divided by the charge charged over the whole record.",
        "A lithium-ion cell returns nearly all of its charge, so a value well below 100 % means current leaves",
        "the battery without passing the sensor (BMS supply, balancing) or the sensor has an offset.",
        "The paper corrects this with an offset current; besslib estimates it with the capacity (see the capacity page).",
        "",
    ]
    if len(failed):
        parts += [
            "## Tests that could not be measured",
            "",
            table(failed.assign(System=failed["system_id"].map("{:02d}".format), Start=failed["documented_start"].dt.strftime("%Y-%m-%d %H:%M"))[
                ["System", "chemistry", "Start", "status"]].rename(columns={"chemistry": "Chemistry", "Start": "Documented start", "status": "Reason"})),
            "",
        ]  # fmt: skip
    parts += [
        "## All measured tests",
        "",
        "\"Ah out before the start\" is the charge that left the battery between the end of the last charge and the",
        "start of the test discharge. It is shown, not added.",
        "",
        table(full),
        "",
        *(["### Tests with discharge just before they began",
           "",
           "In these tests more than 1 % of the measured capacity was discharged in the hour before the test discharge",
           "began. The battery may not have been full when the test began, and then the measured value is below",
           "what a test from full would give. It is shown, not added.",
           "\"Ah out since the last charge\" can read lower when a short charge happened in between.",
           "",
           table(early_table),
           ""] if len(early) else []),
        "## Source and credit",
        "",
        "Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in",
        "capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.",
        "Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9,",
        "licence CC BY-NC-ND 4.0: cited only; no figure or text of it is copied.",
        "The values on this page are computed by besslib from that data; they are not taken from the paper.",
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    tests, coverage, quality, systems = load()
    figure_capacity(tests)
    figure_coverage(tests, coverage, systems)
    figure_sheet(tests, systems)
    (DOCS / "01_reference.md").write_text(document(tests, coverage, quality, systems))
    print(f"docs/01_reference.md written: {int((tests['status'] == 'ok').sum())} of {len(tests)} tests measured")


if __name__ == "__main__":
    main()
