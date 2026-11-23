"""Phase 2: write docs/02_capacity_validation.md and its figures from the result files.

Every number on the page comes from ``validation/results/`` or from ``CapacityConfig()``;
none is typed. The page works with whatever results exist: systems without a result
file are shown as not run, and every pass/fail word, comparison and count is computed.

Reads ``validation/results/phase2/system_XX_{tests,monthly}.csv`` and
``system_XX_summary.json``, ``checks_phase2.csv``, ``phase2_tests.csv``,
``capacity_tests.csv``, ``coverage.csv``, ``quality_development.csv``,
``tuning_development.csv``, ``edge_tolerance.csv``, ``level_window.csv``,
``synthetic_coverage.csv``, ``synthetic_lag.csv``, ``tuning_trend.csv`` and ``zenodo_files.json``; for the level diagnostics also the
reduced rest tables ``data/reduced/rests/`` (read only, with the library's own
``classify_rests``, ``rest_chain`` and ``rest_pairs`` and the test windows masked as in the run).

Usage:
    python validation/make_capacity_doc.py
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from besslib.io import figgener
from besslib.soh import _core
from besslib.soh.config import CapacityConfig, config_hash
from besslib.soh.rests import classify_rests, rest_chain, rest_pairs

from check_capacity import FLAGGED, PAPER_FADE, accuracy
from edge_tolerance import MAX_DROP, MIN_PAIRS
from level_window import LOW_SHARE
from reduce_events import rest_file
from make_reference_doc import AXIS, COLOUR, INK, INK2, LABEL, MUTED, ORDER, SURFACE, table
from tune_capacity import DEVELOPMENT, MIN_COVERAGE, masks_for

V0_1 = config_hash(CapacityConfig().v0_1())  # release 0.1.0, read from the settings
MISMATCH = "04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with"

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
PHASE2 = RESULTS / "phase2"
DOCS = ROOT / "docs"
IMG = DOCS / "img"
PAGE = "02_capacity_validation.md"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
DASH = "–"
BAND = f"{CapacityConfig().level:.0%}".replace("%", " %")  # the band level, as printed
PALE = "#ecebe6"  # months before the first pair: a quiet surface step, not a data colour

#: Short names of the statuses, for table columns. The full texts are printed below each table.
SHORT = {
    _core.TOO_FEW_FULL_EMPTY: "too few full-empty pairs",
    _core.TOO_FEW_SAME_KIND: "too few same-kind pairs",
    _core.SINGULAR: "capacity and offset not separable",
    _core.STALE: "no full-empty pair within the memory time",
    _core.NO_BAND: "no positive variance for the band",
    _core.NO_LEVELS: "no levels yet",
    _core.SHORT_SPAN: "span too short",
}
CODE = {text: code for code, text in _core.STATUS_TEXT.items()}

#: What each setting does (words only; the values come from ``CapacityConfig()``).
MEANING = {
    "current_c_rate": "rest: current below this share of the nominal capacity (x C)",
    "min_duration_min": "rest: shortest duration, min",
    "peak_s": "rest: longest current excursion tolerated inside it, s",
    "min_throughput": "rest: charge moved since the last accepted rest, x C",
    "pre_window_s": "edge voltage: median over this time before the rest, s",
    "break_gap_s": "a data step longer than this adds no charge and ends a rest, s",
    "edge_quantile": "end-of-charge level: this quantile of the charge-side edge voltages (end-of-discharge: 1 minus it)",
    "edge_tolerance": "full (empty): edge voltage within this share of the level span of its level",
    "level_window_days": "levels: trailing window, days",
    "level_warmup_days": "levels: none before this much history, days",
    "min_level_rests": "levels: fewest rests of each direction in the window",
    "ocv_fit_max_min": "open-circuit fit length, min (reported, not used for capacity)",
    "max_link_days": "pairs: longest time between the two rests, days",
    "max_missing_s": "pairs: most missing data between the two rests, s",
    "tau_days": "memory time of the forgetting, days",
    "level": "band level",
    "sigma_q_rel": "relative weight: charge error of a pair, x charge moved",
    "sigma_soc_edge": "relative weight: state-of-charge error of one rest",
    "gain_error": "current sensor gain error, added to the capacity band",
    "min_cross": "fewest full-to-empty or empty-to-full pairs in memory (decayed count)",
    "min_same": "fewest full-to-full or empty-to-empty pairs in memory (decayed count)",
    "fade_min_span_years": "fade rate: shortest span of full-to-empty pairs, years",
    "voltage_terms": "relaxed rest-voltage terms: `every_pair` (each end its own kind's voltage minus a reference); "
                     "`none` as in 0.1",
    "local_trend": "the fit with forgetting carries a linear trend and reads the value at the date",
    "band_forgetting": "how often the forgetting weight enters the band's sums (2: weighted least squares; 1: as in 0.1)",
    "plausibility": "crossed levels, a value or band at or below zero, or a wrong-sign pair give `not possible`",
    "trend_min_cross": "fewest full-to-empty or empty-to-full pairs in memory for the trend (decayed count); tuned on "
                       "the development systems",
    "gain_rectangular": "the gain error is read as a rectangular half-width (÷ √3)",
}


# ----------------------------------------------------------------------------- formatting


def num(value, fmt: str = "{:.2f}", none: str = DASH) -> str:
    """A number formatted, or a dash when it is missing or not finite."""
    try:
        if value is None or not np.isfinite(float(value)):
            return none
    except (TypeError, ValueError):
        return none
    return fmt.format(value)


def sid_text(sid: int) -> str:
    return f"{sid:02d}"


def ids_text(ids) -> str:
    ids = [sid_text(i) for i in ids]
    if not ids:
        return "none"
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " and " + ids[-1]


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def status_short(text) -> str:
    if not isinstance(text, str) or not text:
        return "no result file yet"
    if text == _core.STATUS_TEXT[_core.OK]:
        return "estimated"
    return "not possible: " + SHORT.get(CODE.get(text, -1), text)


def threshold(rule: str) -> float:
    """The number in a pass rule such as ``<= 3 points`` or ``>= 80 %``."""
    found = re.search(r"[-+]?\d+(?:\.\d+)?", rule)
    return float(found.group()) if found else np.nan


# ----------------------------------------------------------------------------- data


def load() -> dict:
    config = CapacityConfig()
    digest = config_hash(config)
    reference = pd.read_csv(RESULTS / "capacity_tests.csv", parse_dates=["documented_start", "onset", "end"])
    reference["test_id"] = reference["system_id"].map(sid_text) + "/" + reference["test"].astype(str)
    reference["began"] = reference["onset"].fillna(reference["documented_start"])

    specs = {sid: figgener.pack_spec(sid) for sid in figgener.SYSTEM_IDS}
    summaries = pd.DataFrame([json.loads(p.read_text()) for p in sorted(PHASE2.glob("system_*_summary.json"))])
    if summaries.empty:
        summaries = pd.DataFrame(columns=["system_id", "set", "chemistry", "config_hash"])
    summaries = summaries.set_index("system_id", drop=False).sort_index()
    test_files = sorted(PHASE2.glob("system_*_tests.csv"))
    per_test = (
        pd.concat([pd.read_csv(p, parse_dates=["time"]) for p in test_files], ignore_index=True)
        if test_files
        else pd.DataFrame(columns=["test_id", "config_hash"])
    )
    monthly = {}
    for path in sorted(PHASE2.glob("system_*_monthly.csv")):
        frame = pd.read_csv(path, parse_dates=["time"])
        monthly[int(path.stem.split("_")[1])] = frame
    checks = pd.read_csv(RESULTS / "checks_phase2.csv", keep_default_na=False)
    checked = pd.read_csv(RESULTS / "phase2_tests.csv") if (RESULTS / "phase2_tests.csv").exists() else pd.DataFrame()

    lag = pd.read_csv(RESULTS / "synthetic_lag.csv", dtype={"capacity_config_hash": str})
    trend_tuning = pd.read_csv(RESULTS / "tuning_trend.csv", dtype={"config_hash": str})
    hashes = set(summaries["config_hash"]) | set(per_test["config_hash"]) | set(checks["config_hash"])
    hashes |= set(lag["capacity_config_hash"])
    for frame in monthly.values():
        hashes |= set(frame["config_hash"])
    if hashes - {digest}:
        raise SystemExit(f"results come from settings {sorted(hashes)}, not the frozen {digest}: rebuild them first")

    # One row per field test (all 60), with the estimate where a result file has it.
    keep = ["test_id", "time", "capacity_ah", "capacity_ah_low", "capacity_ah_high", "error_points", "band_points",
            "held", "status", "n_pairs", "n_full_empty", "n_same_kind"]  # fmt: skip
    measured = reference.rename(columns={"capacity_ah": "test_capacity_ah", "status": "test_status"})
    tests = measured.merge(per_test[keep] if len(per_test) else pd.DataFrame(columns=keep), on="test_id", how="left")
    tests["nominal_ah"] = tests["system_id"].map(lambda s: specs[s].nominal_capacity_ah)
    tests["run"] = tests["system_id"].isin(summaries.index)
    tests["held"] = tests["held"].map(lambda v: bool(v) if isinstance(v, (bool, np.bool_)) else (v == "True" if isinstance(v, str) else False))
    tests["flagged"] = tests["test_id"].isin(FLAGGED)
    tests = tests.sort_values(["system_id", "test"]).reset_index(drop=True)

    return {
        "config": config,
        "hash": digest,
        "specs": specs,
        "reference": reference,
        "tests": tests,
        "summaries": summaries,
        "monthly": monthly,
        "checks": checks,
        "checked": checked,
        "coverage": pd.read_csv(RESULTS / "coverage.csv", parse_dates=["month"]),
        "quality": pd.read_csv(RESULTS / "quality_development.csv", parse_dates=["month", "first", "last"]),
        "tuning": pd.read_csv(RESULTS / "tuning_development.csv"),
        "edge": pd.read_csv(RESULTS / "edge_tolerance.csv"),
        "levels": pd.read_csv(RESULTS / "level_window.csv"),
        "synth": pd.read_csv(RESULTS / "synthetic_coverage.csv"),
        "lag": lag,
        "trend_tuning": trend_tuning,
        "zenodo": json.loads((RESULTS / "zenodo_files.json").read_text()),
    }


def chemistry_of(ctx: dict, sid: int) -> str:
    return ctx["specs"][sid].chemistry


def set_of(sid: int) -> str:
    return "development" if sid in DEVELOPMENT else "held-out"


def month_table(ctx: dict) -> pd.DataFrame:
    """Per system: months with data, and what the estimate file says about each of them."""
    rows = []
    for sid in figgener.SYSTEM_IDS:
        months = ctx["coverage"].loc[ctx["coverage"]["system_id"] == sid, "month"].dt.to_period("M")
        row = {"system_id": sid, "with_data": int(len(months))}
        m = ctx["monthly"].get(sid)
        if m is None:
            rows.append(row)
            continue
        status = pd.Series(m["status"].to_numpy(), index=m["time"].dt.to_period("M"))
        first, last = status.index.min(), status.index.max()
        inside = months[(months >= first) & (months <= last)]
        row["before_first_pair"] = int((months < first).sum())
        row["after_last_pair"] = int((months > last).sum())
        codes = status.reindex(inside).map(lambda t: CODE.get(t, -1))
        row["estimated"] = int((codes == _core.OK).sum())
        for code in SHORT:
            row[code] = int((codes == code).sum())
        no_data = status.index.difference(pd.PeriodIndex(months))
        row["estimated_without_data"] = int((status.reindex(no_data) == _core.STATUS_TEXT[_core.OK]).sum())
        row["month_ends"] = int(len(status))
        est = m[m["status"] == _core.STATUS_TEXT[_core.OK]]
        row["median_band_points"] = float(100 * (est["soh_capacity_high"] - est["soh_capacity_low"]).median()) if len(est) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("system_id")


def implausible(ctx: dict) -> pd.DataFrame:
    """Per system: month ends with the status ``estimated`` whose value is not physical
    (capacity at or below zero, or a lower band edge below zero)."""
    rows = []
    for sid, m in sorted(ctx["monthly"].items()):
        est = m[m["status"] == _core.STATUS_TEXT[_core.OK]]
        bad = est[(est["soh_capacity"] <= 0) | (est["soh_capacity_low"] < 0)]
        rows.append({
            "system_id": sid,
            "estimated": len(est),
            "bad": len(bad),
            "first_bad": bad["time"].min() if len(bad) else pd.NaT,
            "widest_band_points": float(100 * (est["soh_capacity_high"] - est["soh_capacity_low"]).max()) if len(est) else np.nan,
        })  # fmt: skip
    return pd.DataFrame(rows).set_index("system_id")


def level_diagnostics(ctx: dict) -> dict:
    """The full and empty levels and the pairs of every system, as the run builds them.

    Reads the reduced rest tables (``data/reduced/rests/``) and applies the library's own
    ``classify_rests``, ``rest_chain`` and ``rest_pairs`` with the frozen settings and the
    test windows masked as in ``tune_capacity.estimate_at_tests``. Nothing is fitted here.
    """
    c = ctx["config"]
    out = {}
    for sid in figgener.SYSTEM_IDS:
        path = rest_file(sid, c.current_c_rate)
        if not path.exists():
            continue
        spec = ctx["specs"][sid]
        cands = pd.read_parquet(path)
        cands = cands[cands["duration_min"] >= c.min_duration_min]
        mask = masks_for(ctx["reference"], sid)
        inside = np.zeros(len(cands), dtype=bool)
        for a, b in mask:
            inside |= ((cands["start"] >= a) & (cands["start"] <= b)).to_numpy()
        rests = classify_rests(cands[~inside], spec, c)
        pairs = rest_pairs(rest_chain(rests, spec, c, mask=mask), c)
        cells = spec.cells_in_series
        lv = rests.dropna(subset=["level_full_v", "level_empty_v"])
        span = lv["level_full_v"] - lv["level_empty_v"]
        quarterly = (lv.set_index("start")["level_empty_v"] / cells).groupby(lambda t: t.to_period("Q")).median()
        steps = quarterly.diff().dropna()
        cross = pairs[pairs["x"] != 0]
        charge = 100 * cross["x"] * cross["q_ah"] / spec.nominal_capacity_ah  # points of nominal, positive when physical
        wrong = cross[charge <= 0]
        out[sid] = {
            "rests": rests,
            "pairs": pairs,
            "cells": cells,
            "full_cell": (lv["level_full_v"] / cells),
            "empty_cell": (lv["level_empty_v"] / cells),
            "inverted": int((span <= 0).sum()),
            "first_inverted": lv.loc[span <= 0, "start"].min() if (span <= 0).any() else pd.NaT,
            "step_mv": float(1000 * steps.loc[steps.abs().idxmax()]) if len(steps) else np.nan,
            "step_quarter": steps.abs().idxmax() if len(steps) else None,
            "quarterly_empty": quarterly,
            "cross_charge": charge,
            "wrong": int(len(wrong)),
            "first_wrong": wrong["t_end"].min() if len(wrong) else pd.NaT,
            "n_pairs": int(len(pairs)),
        }
    return out


# ----------------------------------------------------------------------------- figures


def _style_time_axis(ax) -> None:
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="x", visible=False)


def figure_errors(ctx: dict) -> None:
    """The lead figure: estimate minus field test, with the band, for every test."""
    tests = ctx["tests"]
    widths = [max(1, (tests["chemistry"] == c).sum()) for c in ORDER]
    fig, axes = plt.subplots(1, 3, figsize=(13, 5.2), sharey=True, gridspec_kw={"width_ratios": widths})
    est = tests[tests["capacity_ah"].notna()]
    span = np.abs(est["error_points"]).max() if len(est) else 5.0
    limit = float(np.ceil(max(5.0, 1.2 * span)))
    for ax, chemistry in zip(axes, ORDER):
        part = tests[tests["chemistry"] == chemistry].reset_index(drop=True)
        colour = COLOUR[chemistry]
        ax.axhline(0, color=INK, lw=1, zorder=2)
        for k, t in part.iterrows():
            held_out = t["set"] == "held-out"
            if pd.notna(t["capacity_ah"]):
                lo = 100 * (t["capacity_ah_low"] - t["test_capacity_ah"]) / t["nominal_ah"]
                hi = 100 * (t["capacity_ah_high"] - t["test_capacity_ah"]) / t["nominal_ah"]
                ax.plot([k, k], [lo, hi], color=colour if held_out else MUTED, lw=3 if held_out else 2,
                        solid_capstyle="round", alpha=0.55 if held_out else 0.7, zorder=3)  # fmt: skip
                ax.plot(k, t["error_points"], marker="o", ms=7, color=colour if held_out else SURFACE,
                        mec=colour if held_out else INK2, mew=1.6, zorder=4)  # fmt: skip
            elif t["run"]:
                ax.plot(k, -limit * 0.9, marker="x", ms=7, mew=1.8, color=INK2, zorder=4)
            else:
                ax.plot(k, 0, marker=".", ms=5, color=MUTED, zorder=4)
        labels = [f"{t['test_id']}{'*' if t['flagged'] else ''}" for _, t in part.iterrows()]
        ax.set_xticks(range(len(part)), labels, rotation=90, fontsize=7.5)
        ax.set_xlim(-0.7, len(part) - 0.3)
        ax.grid(axis="x", visible=False)
        held = part[(part["set"] == "held-out") & part["run"]]
        if len(held):
            a = accuracy(held)
            mae = mae_text(a, " points").replace(" tests without an estimate", " without an estimate").replace("infinite (", "infinite\n(")
            stats = f"held-out median |error|: {mae}\nheld-out band held {int(held['held'].sum())} of {len(held)}"
        else:
            stats = "held-out: no results yet"
        ax.set_title(f"{LABEL[chemistry]}\n{stats}", color=INK, fontsize=9.5)
    axes[0].set_ylim(-limit, limit)
    axes[0].set_ylabel("Estimate minus field test, points of nominal")
    handles = [
        Line2D([], [], marker="o", ms=7, color="none", mfc=INK2, mec=INK2, label="held-out test (colour of its chemistry)"),
        Line2D([], [], marker="o", ms=7, color="none", mfc=SURFACE, mec=INK2, mew=1.6, label="development test (used for tuning)"),
        Line2D([], [], color=MUTED, lw=3, label=f"{BAND} band of the estimate, relative to the test"),
        Line2D([], [], marker="x", ms=7, mew=1.8, color=INK2, linestyle="none", label="no estimate (status in the table)"),
    ]
    if (~tests["run"]).any():
        handles.append(Line2D([], [], marker=".", ms=5, color=MUTED, linestyle="none", label="no result file yet"))
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), fontsize=8, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(f"Every field capacity test against besslib's estimate before it (configuration {ctx['hash']})",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")  # fmt: skip
    fig.text(0.01, 0.055, "A band that crosses zero held the test. * the test had charge discharged in the hour before it began. "
             "Data: Figgener et al. 2024, Zenodo 12091223, CC BY 4.0.", fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))
    fig.savefig(IMG / "02_errors.png", dpi=150)
    plt.close(fig)


def _runs(flags: np.ndarray):
    """Start and end (inclusive) of the True runs."""
    d = np.diff(np.concatenate([[0], flags.astype(np.int8), [0]]))
    return zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1)


def figure_systems(ctx: dict, chemistry: str, months: pd.DataFrame) -> str:
    ids = [sid for sid in figgener.SYSTEM_IDS if chemistry_of(ctx, sid) == chemistry]
    columns = 2
    rows = int(np.ceil(len(ids) / columns))
    fig, axes = plt.subplots(rows, columns, figsize=(12, 2.55 * rows + 1.2), sharex=True, sharey=True, squeeze=False)
    colour = COLOUR[chemistry]
    coverage = ctx["coverage"]
    x0 = coverage["month"].min()
    x1 = coverage["month"].max() + pd.offsets.MonthEnd(1) + pd.Timedelta(days=20)
    tests = ctx["tests"]

    # One y range for the chemistry, from the tests and the physical estimates (lower band edge above zero).
    # Wide bands run off the panel; estimates outside the range are marked at the edge.
    values = [100 * tests.loc[tests["chemistry"] == chemistry, "soh_capacity"]]
    for sid in ids:
        m = ctx["monthly"].get(sid)
        if m is not None:
            values.append(100 * m.loc[m["soh_capacity_low"] > 0, "soh_capacity"].dropna())
    values = pd.concat(values)
    y0, y1 = np.floor(values.min() - 4), np.ceil(min(values.max() + 4, 110))

    for ax, sid in zip(axes.ravel(), ids):
        nominal = ctx["specs"][sid].nominal_capacity_ah
        mine = coverage.loc[coverage["system_id"] == sid, "month"]
        m = ctx["monthly"].get(sid)
        status = {}
        if m is not None:
            status = dict(zip(m["time"].dt.to_period("M"), m["status"]))
            first = m["time"].min().to_period("M").start_time
            if len(mine) and mine.min() < first:
                ax.axvspan(mine.min(), first, color=PALE, lw=0, zorder=0)
            ok = (m["status"] == _core.STATUS_TEXT[_core.OK]).to_numpy()
            t = m["time"].to_numpy()
            mid = 100 * m["soh_capacity"].to_numpy()
            lo = 100 * m["soh_capacity_low"].to_numpy()
            hi = 100 * m["soh_capacity_high"].to_numpy()
            inside = (mid >= y0) & (mid <= y1)
            for a, b in _runs(ok & inside):
                if b > a:
                    ax.fill_between(t[a : b + 1], lo[a : b + 1], hi[a : b + 1], color=colour, alpha=0.28, lw=0, zorder=1)
                    ax.plot(t[a : b + 1], mid[a : b + 1], color=colour, lw=1.6, zorder=2)
                else:
                    ax.errorbar(t[a], mid[a], yerr=[[mid[a] - lo[a]], [hi[a] - mid[a]]], color=colour, lw=1.6,
                                marker="o", ms=3.5, capsize=0, zorder=2)  # fmt: skip
            off = np.flatnonzero(ok & ~inside)
            for k in off:
                below = mid[k] < y0
                ax.plot(t[k], 0.1 if below else 0.9, transform=ax.get_xaxis_transform(), marker="v" if below else "^",
                        ms=7, color=INK, linestyle="none", zorder=6)  # fmt: skip
        else:
            ax.text(0.5, 0.3, "no result file yet", transform=ax.transAxes, ha="center", va="center", color=INK2, fontsize=9)
        # Rug: every month with data; coloured where the month end has an estimate.
        good = [d for d in mine if status.get(d.to_period("M")) == _core.STATUS_TEXT[_core.OK]]
        bad = [d for d in mine if status.get(d.to_period("M")) != _core.STATUS_TEXT[_core.OK]]
        for days, c in ((good, colour), (bad, AXIS)):
            if days:
                ax.broken_barh([(mdates.date2num(d) + 2, 26) for d in days], (0, 0.045), transform=ax.get_xaxis_transform(),
                               color=c, lw=0, zorder=3)  # fmt: skip
        # Field tests and the estimate that was compared with each.
        mine_tests = tests[tests["system_id"] == sid]
        for _, test in mine_tests.iterrows():
            y = 100 * test["test_capacity_ah"] / nominal
            if pd.notna(test["capacity_ah"]):
                e = 100 * np.array([test["capacity_ah"], test["capacity_ah_low"], test["capacity_ah_high"]]) / nominal
                ax.errorbar(test["began"] - pd.Timedelta(days=25), e[0], yerr=[[e[0] - e[1]], [e[2] - e[0]]], color=INK2,
                            lw=1.2, capsize=2.5, marker="_", ms=6, zorder=4)  # fmt: skip
            ax.plot(test["began"], y, marker="D", ms=6.5, color=SURFACE if test["flagged"] else INK, mec=INK, mew=1.4,
                    linestyle="none", zorder=5)  # fmt: skip
            if test["flagged"]:
                ax.annotate(f"{test['test_id']} flagged", (test["began"], y), xytext=(-6, -13), textcoords="offset points", ha="right",
                            fontsize=7.5, color=INK2)  # fmt: skip
        n_est = int(months.loc[sid, "estimated"]) if "estimated" in months and pd.notna(months.loc[sid].get("estimated")) else None
        with_data = int(months.loc[sid, "with_data"])
        where = "held-out" if sid not in DEVELOPMENT else "development, used for tuning"
        ax.set_title(f"System {sid_text(sid)} · {where}", color=INK if sid not in DEVELOPMENT else INK2,
                     fontweight="bold" if sid not in DEVELOPMENT else "normal", fontsize=9.5)  # fmt: skip
        note = f"{n_est} of {with_data} months with data estimated" if n_est is not None else f"{with_data} months with data"
        if not len(mine_tests):
            note += "; no field test"
        if m is not None and len(off):
            note += f"; {plural(len(off), 'estimate')} off the scale (▼)"
        # Above the axes, level with the title, so it never sits on the band or the test points.
        ax.text(1.0, 1.0, note, transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=INK2)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        _style_time_axis(ax)
    for ax in axes.ravel()[len(ids) :]:
        ax.set_axis_off()
    for k in range(len(ids)):
        if k + columns >= len(ids):  # nothing drawn below it: give it its own year labels
            axes.ravel()[k].xaxis.set_tick_params(labelbottom=True)
    for ax in axes[:, 0]:
        ax.set_ylabel("Capacity, % of nominal", fontsize=8.5)
    handles = [
        Patch(color=colour, alpha=0.28, label=f"{BAND} band of the monthly estimate"),
        Line2D([], [], color=colour, lw=1.6, label="monthly estimate"),
        Line2D([], [], color=INK2, lw=1.2, marker="_", ms=6, label="estimate compared with the test"),
        Line2D([], [], marker="D", ms=6.5, color=INK, linestyle="none", label="field capacity test"),
        Line2D([], [], marker="D", ms=6.5, color=SURFACE, mec=INK, mew=1.4, linestyle="none", label="flagged test"),
        Patch(color=colour, label="month with data, estimated"),
        Patch(color=AXIS, label="month with data, no estimate"),
        Patch(color=PALE, label="before the first pair"),
        Line2D([], [], marker="v", ms=7, color=INK, linestyle="none", label="estimate off the scale (no line drawn)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8, bbox_to_anchor=(0.5, 0.0))
    count = len(ids)
    fig.suptitle(f"{LABEL[chemistry]}: monthly capacity estimate and field tests, {count} systems", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")  # fmt: skip
    fig.tight_layout(rect=(0, 0.06 if rows > 3 else 0.085, 1, 0.97))
    name = f"02_systems_{chemistry.lower().replace('/', '_')}.png"
    fig.savefig(IMG / name, dpi=130)
    plt.close(fig)
    return name


def figure_levels(ctx: dict, diag: dict, sid: int) -> str:
    """One system's full and empty levels per cell, and the charge of its full-to-empty pairs."""
    d = diag[sid]
    colour = COLOUR[chemistry_of(ctx, sid)]
    rests = d["rests"].dropna(subset=["level_full_v", "level_empty_v"])
    cross = d["pairs"][d["pairs"]["x"] != 0]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True)
    # One dot per rest: the level in force at that rest. No line, so gaps in the data stay gaps.
    top.plot(rests["start"], rests["level_full_v"] / d["cells"], linestyle="none", marker="o", ms=2, color=INK,
             label="end-of-charge level")  # fmt: skip
    top.plot(rests["start"], rests["level_empty_v"] / d["cells"], linestyle="none", marker="o", ms=2, color=colour,
             label="end-of-discharge level")  # fmt: skip
    top.set_ylabel("Level, V per cell")
    top.legend(loc="center left", fontsize=8, ncol=2, markerscale=3)
    good = d["cross_charge"] > 0
    bottom.plot(cross["t_end"][good], d["cross_charge"][good], linestyle="none", marker="o", ms=4, color=colour,
                label="full-to-empty or empty-to-full pair")  # fmt: skip
    bottom.plot(cross["t_end"][~good], d["cross_charge"][~good], linestyle="none", marker="o", ms=5, mfc=SURFACE, mec=INK,
                mew=1.4, label="same, charge of the wrong sign")  # fmt: skip
    bottom.axhline(0, color=INK, lw=1)
    bottom.set_ylabel("Charge of the pair,\npoints of nominal")
    bottom.legend(loc="lower left", fontsize=8, ncol=2)
    for ax in (top, bottom):
        if pd.notna(d["first_inverted"]):
            ax.axvline(d["first_inverted"], color=MUTED, lw=1, linestyle=(0, (4, 3)))
        _style_time_axis(ax)
    if pd.notna(d["first_inverted"]):
        top.annotate(f"from {d['first_inverted']:%d %b %Y}:\nempty level at or above the full level", (d["first_inverted"], 0.45),
                     xycoords=("data", "axes fraction"), xytext=(6, 0), textcoords="offset points", ha="left",
                     fontsize=8, color=INK2)  # fmt: skip
    fig.suptitle(f"System {sid_text(sid)}: the levels that decide full and empty, and the pairs they make", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")  # fmt: skip
    fig.text(0.01, 0.005, "Computed with besslib's classify_rests and rest_pairs on the reduced rest table, test windows masked. "
             "Data: Figgener et al. 2024, Zenodo 12091223, CC BY 4.0.", fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    name = f"02_levels_{sid_text(sid)}.png"
    fig.savefig(IMG / name, dpi=140)
    plt.close(fig)
    return name


def figure_fade(ctx: dict) -> None:
    s = ctx["summaries"]
    ids = list(figgener.SYSTEM_IDS)
    fig, ax = plt.subplots(figsize=(11, 7.2))
    y = {sid: k for k, sid in enumerate(ids)}
    # x range: the bands of the rates inside Tukey's far-out fences (3 x IQR) and the paper's means;
    # a rate outside is drawn as an arrow at the edge with its value.
    rates = s["rate_points_per_year"].dropna() if len(s) else pd.Series(dtype=float)
    if len(rates):
        q1, q3 = rates.quantile([0.25, 0.75])
        core = rates[(rates >= q1 - 3 * (q3 - q1)) & (rates <= q3 + 3 * (q3 - q1))].index
        lo_x = min(s.loc[core, "rate_t_band_low"].min(), min(PAPER_FADE.values()), 0.0)
        hi_x = max(s.loc[core, "rate_t_band_high"].max(), 0.0)
        pad = 0.06 * (hi_x - lo_x)
        xlim = (lo_x - pad, hi_x + pad)
    else:
        xlim = (min(PAPER_FADE.values()) - 1, 1)
    for chemistry in ORDER:
        rows = [y[sid] for sid in ids if chemistry_of(ctx, sid) == chemistry]
        lo, hi = min(rows) - 0.45, max(rows) + 0.45
        ax.plot([PAPER_FADE[chemistry]] * 2, [lo, hi], color=INK, lw=1.2, linestyle=(0, (4, 3)), zorder=1)
        held = s[(s["chemistry"] == chemistry) & (s["set"] == "held-out")]["rate_points_per_year"].dropna() if len(s) else []
        if len(held):
            ax.plot([held.mean()] * 2, [lo, hi], color=COLOUR[chemistry], lw=2, zorder=1)
    for sid in ids:
        colour = COLOUR[chemistry_of(ctx, sid)]
        k = y[sid]
        if sid not in s.index or num(s.loc[sid].get("rate_points_per_year")) == DASH:
            text = "no result file yet" if sid not in s.index else f"no rate: {status_short(s.loc[sid, 'status'])}"
            ax.text(0.0, k, text, va="center", ha="center", fontsize=7.5, color=INK2)
            continue
        r = s.loc[sid]
        dev = sid in DEVELOPMENT
        ax.plot([r["rate_boot_low"], r["rate_boot_high"]], [k, k], color=INK2, lw=1, zorder=2)
        for edge in ("rate_boot_low", "rate_boot_high"):
            ax.plot([r[edge]] * 2, [k - 0.18, k + 0.18], color=INK2, lw=1, zorder=2)
        ax.plot([r["rate_t_band_low"], r["rate_t_band_high"]], [k, k], color=colour, lw=6, alpha=0.45 if dev else 0.75,
                solid_capstyle="butt", zorder=3)  # fmt: skip
        rate = r["rate_points_per_year"]
        if xlim[0] <= rate <= xlim[1]:
            ax.plot(rate, k, marker="o", ms=7, color=SURFACE if dev else colour, mec=colour, mew=1.8, zorder=4)
        else:
            edge = xlim[0] if rate < xlim[0] else xlim[1]
            ax.plot(edge, k, marker="<" if rate < xlim[0] else ">", ms=9, color=INK, zorder=5, clip_on=False)
            ax.annotate(f"{rate:+.1f}, band {r['rate_t_band_low']:+.1f} to {r['rate_t_band_high']:+.1f} (off the scale)", (edge, k),
                        xytext=(10 if rate < xlim[0] else -10, 0), textcoords="offset points", va="center",
                        ha="left" if rate < xlim[0] else "right", fontsize=7.5, color=INK)  # fmt: skip
    ax.set_yticks(range(len(ids)), [f"{sid_text(i)}  {'development' if i in DEVELOPMENT else 'held-out'}" for i in ids], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(*xlim)
    ax.axvline(0, color=AXIS, lw=1, zorder=0)
    ax.set_xlabel("Fade rate, points of nominal capacity per year (negative = capacity falls)")
    ax.grid(axis="y", visible=False)
    handles = [
        Line2D([], [], color=INK2, lw=6, alpha=0.6, label=f"{BAND} band (t, lag-1 sandwich)"),
        Line2D([], [], color=INK2, lw=1, marker="|", ms=8, label=f"{BAND} month bootstrap (check)"),
        Line2D([], [], marker="o", ms=7, color="none", mfc=INK2, mec=INK2, label="held-out system"),
        Line2D([], [], marker="o", ms=7, color="none", mfc=SURFACE, mec=INK2, mew=1.8, label="development system"),
        Line2D([], [], color=INK2, lw=2, label="mean of the held-out systems"),
        Line2D([], [], color=INK, lw=1.2, linestyle=(0, (4, 3)), label="paper, algorithm mean (Figgener et al. 2024)"),
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=3, fontsize=8)
    for chemistry in ORDER:
        rows = [y[sid] for sid in ids if chemistry_of(ctx, sid) == chemistry]
        ax.text(1.005, np.mean(rows), chemistry, transform=ax.get_yaxis_transform(), va="center", fontsize=9,
                color=INK, fontweight="bold")  # fmt: skip
    ax.set_title(f"Fade rate over each record (configuration {ctx['hash']})", color=INK, fontsize=12)
    fig.text(0.01, 0.005, "Data: Figgener et al. 2024, Zenodo 12091223, CC BY 4.0. Paper means from its Results section.",
             fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.02, 0.97, 1))
    fig.savefig(IMG / "02_fade.png", dpi=150)
    plt.close(fig)


def figure_synthetic(ctx: dict) -> None:
    synth = ctx["synth"].sort_values("rate_points_per_year").reset_index(drop=True)
    truth = float(synth["truth_points_per_year"].iloc[0])
    fig, ax = plt.subplots(figsize=(11, 3.8))
    for held, colour, lw in ((True, AXIS, 1.2), (False, INK, 1.6)):
        part = synth[synth["held"] == held]
        ax.vlines(part.index, part["low"], part["high"], color=colour, lw=lw, zorder=2)
    ax.plot(synth.index, synth["rate_points_per_year"], linestyle="none", marker="o", ms=2.2, color=INK2, zorder=3)
    ax.axhline(truth, color=COLOUR["LFP"], lw=2, zorder=4)
    ax.set_xlim(-2, len(synth) + 1)
    ax.set_xlabel("Synthetic pack, sorted by its estimate")
    ax.set_ylabel("Fade rate, points per year")
    ax.grid(axis="x", visible=False)
    handles = [
        Line2D([], [], color=AXIS, lw=1.2, label=f"{BAND} band that held the truth"),
        Line2D([], [], color=INK, lw=1.6, label=f"{BAND} band that missed it"),
        Line2D([], [], color=COLOUR["LFP"], lw=2, label="truth: expected usable fade"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8, ncol=3)
    ax.set_title(f"Check V6: {len(synth)} synthetic packs with a known fade, band held the truth in "
                 f"{int(synth['held'].sum())}", color=INK, fontsize=11)  # fmt: skip
    fig.tight_layout()
    fig.savefig(IMG / "02_synthetic.png", dpi=150)
    plt.close(fig)


# ----------------------------------------------------------------------------- page parts


def mae_text(a: dict, unit: str = "") -> str:
    """The median absolute error as printed: a number (with ``unit``), or 'infinite' with the reason."""
    if not a["n"]:
        return DASH
    if np.isfinite(a["median_abs_error"]):
        return num(a["median_abs_error"]) + unit
    return f"infinite ({a['n'] - a['n_estimated']} of {a['n']} tests without an estimate)"


def accuracy_row(name: str, rows: pd.DataFrame, total: int) -> dict:
    a = accuracy(rows) if len(rows) else {"n": 0, "n_estimated": 0, "median_abs_error": np.nan, "median_error": np.nan, "coverage": np.nan}
    return {
        "Tests": name,
        "Compared": f"{a['n']} of {total}",
        "With an estimate": a["n_estimated"],
        "Median abs. error, points": mae_text(a),
        "Median error, points": num(a["median_error"], "{:+.2f}"),
        "Band held the test": f"{int(rows['held'].sum())} of {a['n']} ({a['coverage']:.0%})" if a["n"] else DASH,
        "Median band width, points": num(rows["band_points"].median()) if a["n"] else DASH,
    }


def check_label(row: pd.Series, checks: pd.DataFrame) -> str:
    """``V5 NMC`` where a check has one line per chemistry, else the check's name."""
    main = checks[checks["result"] != ""]
    if (main["check"] == row["check"]).sum() > 1:
        found = re.match(r"held-out (\S+)", row["scope"])
        if found:
            return f"{row['check']} {found.group(1)}"
    return row["check"]


def held_group(ctx: dict, chems) -> pd.DataFrame:
    t = ctx["tests"]
    return t[(t["set"] == "held-out") & t["chemistry"].isin(chems) & t["run"]]


def held_rates(ctx: dict, chemistry: str) -> pd.Series:
    s = ctx["summaries"]
    if not len(s):
        return pd.Series(dtype=float)
    return s[(s["chemistry"] == chemistry) & (s["set"] == "held-out")]["rate_points_per_year"].dropna()


def one_line(ctx: dict, row: pd.Series) -> str:
    """What a check's numbers say, in one computed line."""
    name, rule = row["check"], row["pass_if"]
    if name in ("V2", "V3"):
        rows = held_group(ctx, ["LMO/NMC", "NMC"] if name == "V2" else ["LFP"])
        a = accuracy(rows)
        est = rows[rows["capacity_ah"].notna()]
        line = f"median absolute error {mae_text(a, ' points')} (rule {rule}); median error {num(a['median_error'], '{:+.2f}')} points"
        if name == "V3" and len(est):
            line += f"; {int((est['error_points'] > 0).sum())} of the {len(est)} estimates read above the test"
        return line
    if name == "V4":
        rows = held_group(ctx, ["LMO/NMC", "NMC"])
        return f"the band held {int(rows['held'].sum())} of {len(rows)} tests (rule {rule})"
    if name == "V5":
        found = re.match(r"held-out (\S+)", row["scope"])
        chemistry = found.group(1) if found else None
        if chemistry in PAPER_FADE:
            rates = held_rates(ctx, chemistry)
            return (f"held-out mean {num(rates.mean(), '{:+.2f}')} points per year against the paper's "
                    f"{PAPER_FADE[chemistry]:+.1f} (rule {rule})")  # fmt: skip
    return f"{row['value']} (rule {rule})"


def verdict(ctx: dict) -> list[str]:
    checks = ctx["checks"]
    main = checks[checks["result"].isin(["pass", "fail", "not run"])]
    names = {r: [check_label(row, checks) for _, row in main[main["result"] == r].iterrows()] for r in ("pass", "fail", "not run")}
    line = f"- **Checks passed:** {', '.join(names['pass']) or 'none'}. **Failed:** {', '.join(names['fail']) or 'none'}."
    if names["not run"]:
        line += f" **Not run:** {', '.join(names['not run'])}."
    lines = [line]
    for _, row in main[main["result"] == "fail"].iterrows():
        lines.append(f"  - {check_label(row, checks)} failed: {one_line(ctx, row)}.")
    return lines


def headline(ctx: dict) -> list[str]:
    tests, s = ctx["tests"], ctx["summaries"]
    held_ids = [i for i in HELD_OUT if i in s.index]
    held = tests[(tests["set"] == "held-out") & tests["run"]]
    all_held = tests[tests["set"] == "held-out"]
    lines = [f"- **Configuration:** `{ctx['hash']}`, frozen on the development systems {ids_text(DEVELOPMENT)} "
             f"and on synthetic packs before the held-out systems were run at it. They had been run once before, at "
             f"the release 0.1.0 settings `{V0_1}`."]  # fmt: skip
    lines.append(f"- **Held-out systems with results:** {len(held_ids)} of {len(HELD_OUT)}"
                 + (f" (missing: {ids_text([i for i in HELD_OUT if i not in s.index])})." if len(held_ids) < len(HELD_OUT) else "."))  # fmt: skip
    lines += verdict(ctx)
    if len(held):
        a = accuracy(held)
        lines.append(f"- **Held-out field tests compared:** {a['n']} of {len(all_held)}; with an estimate: {a['n_estimated']}.")
        for name, chems in (("LMO/NMC and NMC", ["LMO/NMC", "NMC"]), ("LFP", ["LFP"])):
            part = held[held["chemistry"].isin(chems)]
            if len(part):
                b = accuracy(part)
                lines.append(f"- **Held-out {name}:** median absolute error {mae_text(b, ' points of nominal capacity')}; "
                             f"{BAND} band held the test in {int(part['held'].sum())} of {b['n']} ({b['coverage']:.0%}).")  # fmt: skip
        lines.append("- A test without an estimate counts as an infinite error and as not held.")
    else:
        lines.append(f"- **Held-out field tests compared:** 0 of {len(all_held)}. No held-out figure exists yet; "
                     "every held-out check below says `not run`.")  # fmt: skip
    implaus = ctx["implausible"]
    bad = implaus[implaus["bad"] > 0]
    if len(bad):
        lines.append(f"- **Impossible output:** {plural(int(bad['bad'].sum()), 'monthly estimate')} on system "
                     f"{ids_text(bad.index)} {'is' if int(bad['bad'].sum()) == 1 else 'are'} at or below zero or have a band below zero "
                     "and still carry the status `estimated` (see Limits).")  # fmt: skip
    groups = [
        ("Held-out LMO/NMC and NMC", ["LMO/NMC", "NMC"]),
        ("Held-out LFP", ["LFP"]),
        ("Held-out, all", ORDER),
    ]
    rows = []
    for name, chems in groups:
        part = tests[(tests["set"] == "held-out") & tests["chemistry"].isin(chems)]
        rows.append(accuracy_row(name, part[part["run"]], len(part)))
    return lines + ["", table(pd.DataFrame(rows))]


def development_block(ctx: dict) -> list[str]:
    tests = ctx["tests"]
    dev = tests[tests["set"] == "development"]
    rows = []
    for name, chems in (("Development LMO/NMC and NMC", ["LMO/NMC", "NMC"]), ("Development LFP", ["LFP"]), ("Development, all", ORDER)):
        part = dev[dev["chemistry"].isin(chems)]
        rows.append(accuracy_row(name, part[part["run"]], len(part)))
    tuning = ctx["tuning"]
    rest_search = tuning[tuning["selected"]].iloc[0]
    trend = ctx["trend_tuning"]
    chosen = trend[trend["selected"].astype(bool)].iloc[0]
    same = chosen["config_hash"] == ctx["hash"]
    now = dev[dev["run"]]
    a = accuracy(now) if len(now) else None
    matches = (
        a is not None
        and a["n"] == int(chosen["n_tests"])
        and a["n_estimated"] == int(chosen["n_estimated"])
        and np.isclose(a["median_abs_error"], chosen["median_abs_error_points"], atol=0.01)
        and np.isclose(a["coverage"], chosen["coverage"], atol=0.001)
    )
    return [
        "These are the systems the settings were tuned on. They are not a result and are never mixed into one.",
        "",
        table(pd.DataFrame(rows)),
        "",
        f"The rest search and the memory time were chosen in version 0.1 (`{rest_search['config_hash']}`) and kept. "
        f"Version 0.2 tuned one setting on these {int(chosen['n_tests'])} tests, the trend gate (`validation/tune_trend.py`). "
        f"It chose `{chosen['config_hash']}`, "
        + ("the frozen configuration" if same else f"not the frozen `{ctx['hash']}`")
        + f": median absolute error {num(chosen['median_abs_error_points'])} points, "
        f"{int(chosen['n_estimated'])} with an estimate, band coverage {chosen['coverage']:.0%}. "
        + ("The frozen run gives the same numbers." if matches else "**The frozen run does not give the same numbers.**"),
    ]


def checks_block(ctx: dict) -> list[str]:
    checks = ctx["checks"]
    shown = pd.DataFrame(
        {
            "Check": checks["check"],
            "Scope": checks["scope"],
            "Value": checks["value"],
            "Pass rule": checks["pass_if"],
            "Result": checks["result"].map(lambda r: {"fail": "**fail**"}.get(r, r)),
        }
    )
    reference = ctx["reference"]
    lmo = reference[(reference["set"] == "held-out") & reference["chemistry"].isin(["LMO/NMC", "NMC"])]
    flagged = reference[reference["test_id"].isin(FLAGGED)]
    share = 100 * flagged["discharged_hour_before_ah"] / flagged["capacity_ah"]
    flag_text = " and ".join(f"{t} ({num(v, '{:.1f}')} % of its capacity)" for t, v in zip(flagged["test_id"], share))
    lines = [table(shown), ""]
    for _, row in checks[checks["value"].str.contains(r"\binf\b")].iterrows():
        chems = ["LFP"] if row["check"] == "V3" else ["LMO/NMC", "NMC"]
        rows = held_group(ctx, chems)
        missing = int(rows["capacity_ah"].isna().sum())
        share_word = "half" if 2 * missing == len(rows) else ("more than half" if 2 * missing > len(rows) else "fewer than half")
        lines += [
            f"\"inf\" in {row['check']}: {missing} of {len(rows)} tests have no estimate ({share_word} of the tests). A missing estimate counts "
            "as an infinite error, so the median absolute error is infinite.",
            "",
        ]
    lines += [
        "Rules fixed before the held-out run:",
        "",
        "- A test without an estimate counts as an infinite error and as not held.",
        f"- All {len(lmo)} held-out LMO/NMC and NMC tests count in V2 and V4.",
        f"- Tests {flag_text} had charge discharged in the hour before the test began (phase 1). They count, are flagged",
        "  in the tables, and get one information line without them.",
        "- Fade (V5) is judged per chemistry on the held-out systems against the paper's algorithm means;",
        f"  the mean over all {len(figgener.SYSTEM_IDS)} systems is an information line.",
        "- A check without results is written as `not run`, never as pass or fail.",
    ]
    checked = len(ctx["checked"])
    now = int(ctx["tests"]["status"].notna().sum())
    if checked != now:
        lines += [
            "",
            f"**The check table is older than the result files:** it was written from {checked} tests, the result files "
            f"now hold {now}. Run `python validation/check_capacity.py` again.",
        ]
    return lines


def test_rows(ctx: dict, rows: pd.DataFrame) -> pd.DataFrame:
    """A compact per-test table for the evidence blocks."""
    pct = lambda col: 100 * rows[col] / rows["nominal_ah"]  # noqa: E731
    return pd.DataFrame(
        {
            "Test": rows["test_id"] + np.where(rows["flagged"], "*", ""),
            "Began": rows["began"].dt.strftime("%Y-%m-%d"),
            "Test, % of nominal": pct("test_capacity_ah").map("{:.1f}".format),
            "Test C-rate": rows["test_c_rate"].map("{:.2f}".format),
            f"Estimate [{BAND} band]": [
                f"{m:.1f} [{lo:.1f} – {hi:.1f}]" if np.isfinite(m) else DASH
                for m, lo, hi in zip(pct("capacity_ah"), pct("capacity_ah_low"), pct("capacity_ah_high"))
            ],
            "Error, points": rows["error_points"].map(lambda v: num(v, "{:+.2f}")),
            "Held": np.where(rows["held"], "yes", "no"),
            "Status": rows["status"].map(status_short),
        }
    )


def consecutive_tests(ctx: dict, ids=None) -> pd.DataFrame:
    """Consecutive field tests of the same system and the change between them (points of nominal)."""
    t = ctx["tests"].sort_values(["system_id", "began"])
    if ids is not None:
        t = t[t["system_id"].isin(ids)]
    t = t.assign(points=100 * t["test_capacity_ah"] / t["nominal_ah"])
    t["change"] = t.groupby("system_id")["points"].diff()
    t["previous"] = t.groupby("system_id")["test_id"].shift()
    return t[t["change"].notna()]


def evidence_v3(ctx: dict) -> list[str]:
    rows = held_group(ctx, ["LFP"])
    if rows.empty:
        return ["No held-out LFP test has a result yet."]
    est = rows[rows["capacity_ah"].notna()]
    reasons = rows.loc[rows["capacity_ah"].isna(), "status"].map(status_short).value_counts()
    above = int((est["error_points"] > 0).sum())
    steps = consecutive_tests(ctx, rows["system_id"].unique())
    drops = steps.sort_values("change").head(2)
    diag = ctx["diag"]
    out = [
        table(test_rows(ctx, rows)),
        "",
        f"- Tests without an estimate: {len(rows) - len(est)} of {len(rows)}; by reason: "
        + "; ".join(f"{r} {n}" for r, n in reasons.items()) + ".",
        f"- Over the {len(est)} tests with an estimate: median absolute error {num(np.median(np.abs(est['error_points'])) if len(est) else np.nan)} "
        f"points, median error {num(est['error_points'].median(), '{:+.2f}')} points; {above} of {len(est)} read above the test.",
        "- The field tests themselves, consecutive pairs of the same system with the largest drops: "
        + "; ".join(f"{p} to {t} {c:+.1f} points ({d:%b %Y})" for p, t, c, d in zip(drops["previous"], drops["test_id"], drops["change"], drops["began"]))
        + ".",
    ]  # fmt: skip
    tests = ctx["tests"]
    for _, drop in drops.iterrows():
        d = diag.get(drop["system_id"])
        if d is None:
            continue
        q = d["quarterly_empty"]
        prev = tests.loc[tests["test_id"] == drop["previous"], "began"].iloc[0].to_period("Q")
        now = drop["began"].to_period("Q")
        a, b = q[q.index <= prev], q[q.index <= now]
        if len(a) and len(b):
            out.append(f"- System {sid_text(drop['system_id'])}: its end-of-discharge level (quarterly median) is {a.iloc[-1]:.3f} V per "
                       f"cell in {a.index[-1]} and {b.iloc[-1]:.3f} in {b.index[-1]}, the quarters of {drop['previous']} and "
                       f"{drop['test_id']}: {1000 * (b.iloc[-1] - a.iloc[-1]):+.0f} mV per cell between the two tests.")  # fmt: skip
    out += [
        "",
        f"The numbers show two parts: {len(rows) - len(est)} of {len(rows)} tests have no estimate, and {above} of the {len(est)} "
        "estimates read above their test. Among the possible reasons for the high readings — the drift of the stops against "
        "the rest voltages, a change of the end-of-discharge level, the difference between a maximum-power test and daily "
        "operation — the share of each is not separated.",
    ]  # fmt: skip
    return out


def evidence_v6(ctx: dict) -> list[str]:
    synth = ctx["synth"]
    est = synth[synth["status"] == _core.STATUS_TEXT[_core.OK]]
    truth = float(synth["truth_points_per_year"].iloc[0])
    return [
        f"- Truth: the expected fade of the charge between the BMS stops, {truth:+.2f} points per year. Mean estimate "
        f"{est['rate_points_per_year'].mean():+.2f}.",
        f"- Held: {int(synth['held'].sum())} of {len(synth)} ({synth['held'].mean():.1%}). Against the fade of the capacity "
        f"between the rest voltages the fit reads at (mean {synth['window_fade_points_per_year'].mean():+.2f}; chosen after "
        f"the first results, information): {int(synth['held_window'].sum())} of {len(synth)} ({synth['held_window'].mean():.1%}).",
        "",
        "The two truths differ because the fit reads the capacity at rest voltages, not at the BMS stops "
        f"([version 0.2 page]({MISMATCH})).",
    ]


def evidence_v4(ctx: dict) -> list[str]:
    rows = held_group(ctx, ["LMO/NMC", "NMC"])
    if rows.empty:
        return ["No held-out LMO/NMC or NMC test has a result yet."]
    est = rows[rows["capacity_ah"].notna()]
    missed = est[~est["held"]]
    above = int((missed["error_points"] > 0).sum())
    half = est["band_points"] / 2
    systems = rows["system_id"].unique()
    steps = consecutive_tests(ctx, systems)
    rises = steps[steps["change"] > 0]
    every = consecutive_tests(ctx)
    every_rises = every[every["change"] > 0]
    out = [
        f"- Tests without an estimate: {len(rows) - len(est)} of {len(rows)}; they count as not held. Over the {len(est)} tests "
        f"with an estimate the band held {int(est['held'].sum())} ({est['held'].mean():.0%}).",
        f"- Misses: {len(missed)}; the estimate was above the test in {above} and below it in {len(missed) - above}.",
        f"- Median half-width of the band: {num(half.median())} points. Median absolute error of the estimated tests: "
        f"{num(np.median(np.abs(est['error_points'])))} points.",
        "- Lag: the fit reads the value at the date. On synthetic packs it still reads "
        f"{ctx['lag']['mean_lag_points'].mean():+.2f} points above the charge between the stops (X3, "
        f"[version 0.2 page]({MISMATCH})).",
        f"- Field tests of the same system, consecutive pairs (these {len(systems)} systems): {len(steps)}; the later test reads "
        f"higher in {len(rises)}"
        + (f" (median rise {rises['change'].median():+.1f}, largest {rises['change'].max():+.1f} points)" if len(rises) else "")
        + f". Over all systems: {len(every_rises)} of {len(every)}"
        + (f" (median rise {every_rises['change'].median():+.1f}, largest {every_rises['change'].max():+.1f} points)." if len(every_rises) else "."),
        "",
        "A battery does not gain capacity, so where a later test reads higher by d, at least one of the two tests is off the",
        "capacity by d/2 or more, and a band whose half-width is below d/2 cannot hold both.",
        "The band is built from the scatter of the pairs only. It holds none of: the drift of the BMS stops against the rest",
        "voltages it reads at, the difference between a maximum-power test and daily operation, the test's own repeatability.",
    ]  # fmt: skip
    narrower = half.median() < np.median(np.abs(est["error_points"]))
    out += [
        "",
        f"The numbers show that {len(rows) - len(est)} of {len(rows)} tests had no estimate and that the median band half-width is "
        f"{'smaller' if narrower else 'not smaller'} than the median error. How much of the misses comes from the drift of the stops, from the "
        "test conditions and from the tests' own repeatability is not separated.",
    ]  # fmt: skip
    return out


def evidence_v5(ctx: dict, chemistry: str, rule: str) -> list[str]:
    s = ctx["summaries"]
    part = s[(s["chemistry"] == chemistry) & (s["set"] == "held-out")]
    if part.empty:
        return [f"No held-out {chemistry} system has a result yet."]
    rates = part["rate_points_per_year"].dropna()
    paper = PAPER_FADE[chemistry]
    tolerance = abs(threshold(rule))
    rows, zero = [], []
    for sid, r in part.iterrows():
        rest = rates.drop(sid, errors="ignore")
        low, high = r.get("rate_t_band_low", np.nan), r.get("rate_t_band_high", np.nan)
        if np.isfinite(low) and np.isfinite(high) and low <= 0 <= high:
            zero.append(sid)
        rows.append({
            "System": sid_text(sid),
            "Rate, points/year": num(r.get("rate_points_per_year"), "{:+.2f}"),
            f"{BAND} band": f"{num(low, '{:+.2f}')} to {num(high, '{:+.2f}')}",
            "Pairs full-empty / same-kind": f"{int(r.get('n_full_empty', 0))} / {int(r.get('n_same_kind', 0))}",
            "Span, years": num(r.get("span_years"), "{:.1f}"),
            "Reduced chi-square": num(r.get("birge")),
            "Mean of the others": num(rest.mean() if len(rest) else np.nan, "{:+.2f}"),
        })  # fmt: skip
    out = [
        table(pd.DataFrame(rows)),
        "",
        f"- Mean of the {len(rates)} held-out {chemistry} rates: {num(rates.mean(), '{:+.2f}')} points per year; paper "
        f"{paper:+.1f}; difference {num(rates.mean() - paper, '{:+.2f}')} (rule: within ±{tolerance:g}).",
        f"- Bands that include zero: {ids_text(zero) if zero else 'none'}.",
    ]
    if len(rates) > 1:
        loo = pd.Series({sid: rates.drop(sid).mean() for sid in rates.index})
        key = (loo - rates.mean()).abs().idxmax()
        inside = abs(loo[key] - paper) <= tolerance
        out.append(f"- Without system {sid_text(key)}, the one that moves the mean most: {loo[key]:+.2f} points per year, "
                   f"difference {loo[key] - paper:+.2f}; the check would {'pass' if inside else 'still fail'} without it.")  # fmt: skip
        bad = ctx["implausible"]
        if key in bad.index and bad.loc[key, "bad"] > 0:
            out.append(f"- System {sid_text(key)} also has impossible monthly estimates (see Limits); its fade fit's reduced "
                       f"chi-square is {s.loc[key, 'birge']:.1f}.")  # fmt: skip
        spread = rates.max() - rates.min()
        out += [
            "",
            f"The held-out {chemistry} rates range over {spread:.2f} points per year across {len(rates)} systems; the mean of so few "
            "is moved by any one of them, as the last column shows. Whether the systems age differently from the paper's fleet "
            "mean or the estimator reads them differently is not separated.",
        ]
    return out


def failures_block(ctx: dict) -> list[str]:
    checks = ctx["checks"]
    failed = checks[checks["result"] == "fail"]
    if failed.empty:
        ran = checks[checks["result"].isin(["pass", "fail"])]
        return [f"No check failed. {len(ran)} of {int((checks['result'] != '').sum())} checks have run."]
    out = []
    for _, row in failed.iterrows():
        label = check_label(row, checks)
        out += [f"### {label}: {row['scope']}", "", f"Value: {row['value']}. Pass rule: {row['pass_if']}.", ""]
        if row["check"] == "V3":
            out += evidence_v3(ctx)
        elif row["check"] == "V4":
            out += evidence_v4(ctx)
        elif row["check"] == "V2":
            out += [table(test_rows(ctx, held_group(ctx, ["LMO/NMC", "NMC"])))]
        elif row["check"] == "V5":
            found = re.match(r"held-out (\S+)", row["scope"])
            if found and found.group(1) in PAPER_FADE:
                out += evidence_v5(ctx, found.group(1), row["pass_if"])
        elif row["check"] == "V6":
            out += evidence_v6(ctx)
        elif row["check"] == "V8":
            s = ctx["summaries"]
            out.append(f"- Systems with a summary: {len(s)} of {len(figgener.SYSTEM_IDS)}; missing: "
                       f"{ids_text([i for i in figgener.SYSTEM_IDS if i not in s.index])}.")  # fmt: skip
        out.append("")
    return out


def tests_block(ctx: dict) -> list[str]:
    t = ctx["tests"]
    pct = lambda col: 100 * t[col] / t["nominal_ah"]  # noqa: E731
    est = pct("capacity_ah")
    band = [f"{num(m, '{:.1f}')} [{num(lo, '{:.1f}')} – {num(hi, '{:.1f}')}]" if np.isfinite(m) else DASH
            for m, lo, hi in zip(est, pct("capacity_ah_low"), pct("capacity_ah_high"))]  # fmt: skip
    frame = pd.DataFrame(
        {
            "Test": t["test_id"],
            "Set": t["set"],
            "Chemistry": t["chemistry"],
            "Began": t["began"].dt.strftime("%Y-%m-%d"),
            "Test, Ah": t["test_capacity_ah"].map("{:.2f}".format),
            "Test, % of nominal": pct("test_capacity_ah").map("{:.1f}".format),
            "Test C-rate": t["test_c_rate"].map("{:.2f}".format),
            f"Estimate, % of nominal [{BAND} band]": band,
            "Error, points": t["error_points"].map(lambda v: num(v, "{:+.2f}")),
            "Held": np.where(t["capacity_ah"].notna(), np.where(t["held"], "yes", "no"), np.where(t["run"], "no", DASH)),
            "Status": t["status"].map(status_short),
            "Flag": np.where(t["flagged"], "discharged before the start", ""),
        }
    )
    return [table(frame)]


def bias_block(ctx: dict) -> list[str]:
    t = ctx["tests"]
    rows = []
    for chemistry in ORDER:
        for which in ("held-out", "development"):
            part = t[(t["chemistry"] == chemistry) & (t["set"] == which)]
            est = part[part["capacity_ah"].notna()]
            split = float(t.loc[t["chemistry"] == chemistry, "test_c_rate"].median())
            low, high = est[est["test_c_rate"] < split], est[est["test_c_rate"] >= split]
            rows.append({
                "Chemistry": chemistry,
                "Set": which,
                "Tests with an estimate": f"{len(est)} of {len(part)}",
                "Median error, points": num(est["error_points"].median(), "{:+.2f}") if len(est) else DASH,
                "Estimate below the test": f"{int((est['error_points'] < 0).sum())} of {len(est)}" if len(est) else DASH,
                "Test C-rate (min – max)": f"{part['test_c_rate'].min():.2f} – {part['test_c_rate'].max():.2f}",
                "Median error, tests below / at or above the chemistry's median test C-rate": (
                    f"{num(low['error_points'].median(), '{:+.2f}')} (n = {len(low)}) / {num(high['error_points'].median(), '{:+.2f}')} "
                    f"(n = {len(high)}); split at {split:.2f} C"
                ),
            })  # fmt: skip
    return [table(pd.DataFrame(rows))]


def fade_block(ctx: dict) -> list[str]:
    s = ctx["summaries"]
    rows = []
    for sid in figgener.SYSTEM_IDS:
        base = {"System": sid_text(sid), "Set": set_of(sid), "Chemistry": chemistry_of(ctx, sid)}
        if sid not in s.index:
            rows.append({**base, "Status": "no result file yet"})
            continue
        r = s.loc[sid]
        width_t = r.get("rate_t_band_high", np.nan) - r.get("rate_t_band_low", np.nan)
        width_b = r.get("rate_boot_high", np.nan) - r.get("rate_boot_low", np.nan)
        rows.append({
            **base,
            f"Rate, points/year [{BAND} band]": f"{num(r.get('rate_points_per_year'), '{:+.2f}')} [{num(r.get('rate_t_band_low'), '{:+.2f}')}, {num(r.get('rate_t_band_high'), '{:+.2f}')}]",
            f"Bootstrap {BAND}": f"[{num(r.get('rate_boot_low'), '{:+.2f}')}, {num(r.get('rate_boot_high'), '{:+.2f}')}]",
            "Bootstrap ÷ band width": num(width_b / width_t if width_t else np.nan),
            f"Per 100 EFC [{BAND} band]": f"{num(r.get('rate_points_per_100_efc'), '{:+.2f}')} [{num(r.get('rate_points_per_100_efc_low'), '{:+.2f}')}, {num(r.get('rate_points_per_100_efc_high'), '{:+.2f}')}]",
            "EFC per year": num(r.get("efc_per_year"), "{:.0f}"),
            "Span, years": num(r.get("span_years"), "{:.1f}"),
            "Pairs full-empty / same-kind": f"{int(r.get('n_full_empty', 0))} / {int(r.get('n_same_kind', 0))}",
            "Status": status_short(r["status"]),
        })  # fmt: skip
    per_system = pd.DataFrame(rows).astype(object).fillna(DASH)

    means = []
    for chemistry in ORDER:
        def mean(which):
            part = s[(s["chemistry"] == chemistry) & ((s["set"] == which) if which else True)] if len(s) else s
            r = part["rate_points_per_year"].dropna() if len(part) else pd.Series(dtype=float)
            return (f"{r.mean():+.2f} (n = {len(r)})" if len(r) else "no result yet"), (r.mean() if len(r) else np.nan)

        held_text, held_value = mean("held-out")
        all_text, _ = mean(None)
        dev_text, _ = mean("development")
        means.append({
            "Chemistry": chemistry,
            "Held-out mean, points/year": held_text,
            "Paper, algorithm mean": f"{PAPER_FADE[chemistry]:+.1f}",
            "Held-out minus paper": num(held_value - PAPER_FADE[chemistry], "{:+.2f}"),
            "All systems (information)": all_text,
            "Development only (information)": dev_text,
        })  # fmt: skip
    ratio = []
    for sid in s.index:
        r = s.loc[sid]
        wt = r.get("rate_t_band_high", np.nan) - r.get("rate_t_band_low", np.nan)
        wb = r.get("rate_boot_high", np.nan) - r.get("rate_boot_low", np.nan)
        if np.isfinite(wt) and np.isfinite(wb) and wt > 0:
            ratio.append((sid, wb / wt))
    wider = [sid for sid, q in ratio if q > 1.0]
    lines = [
        table(per_system),
        "",
        "EFC: equivalent full cycles, the charge moved divided by twice the nominal capacity.",
        "The band is the core's: a Student-t interval on the lag-1 sandwich standard error of the fit over all pairs.",
        "The month bootstrap resamples whole calendar months of pairs; it also keeps any correlation within a month",
        "(temperature, season, operation) that the band does not model. The two are shown side by side, not merged.",
    ]
    for which in ("held-out", "development"):
        part = [(sid, v) for sid, v in ratio if set_of(sid) == which]
        if not part:
            continue
        q = np.array([v for _, v in part])
        n_wider = sum(1 for sid, _ in part if sid in wider)
        lines.append(f"{which.capitalize()} systems: the bootstrap interval is wider than the band in {n_wider} of {len(part)} "
                     f"(median ratio {np.median(q):.2f}, largest {q.max():.2f} on system {sid_text(part[int(np.argmax(q))][0])}).")  # fmt: skip
    if ratio:
        lines.append("Where the bootstrap is much wider, the band understates the uncertainty of the rate.")
    lines += [
        "",
        "Means per chemistry, against the paper's algorithm means (Figgener et al. 2024, Results):",
        "",
        table(pd.DataFrame(means)),
    ]
    return lines


def offset_block(ctx: dict) -> list[str]:
    s = ctx["summaries"]
    rows = []
    for sid in s.index:
        r = s.loc[sid]
        mine, paper = r.get("offset_current_a", np.nan), r.get("paper_offset_current_a", np.nan)
        if mine is None or paper is None or not (np.isfinite(mine) and np.isfinite(paper)):
            continue
        rows.append({
            "System": sid_text(sid), "Set": r["set"], "Chemistry": r["chemistry"],
            "besslib, A": f"{mine:+.3f}", "The paper's way, A": f"{paper:+.3f}",
            "Difference, A": f"{mine - paper:+.3f}", "Same-kind pairs used": int(r.get("paper_offset_pairs", 0)),
        })  # fmt: skip
    if not rows:
        return ["No system has both values yet."]
    frame = pd.DataFrame(rows)
    diff = np.array([float(v) for v in frame["Difference, A"]])
    return [
        table(frame),
        "",
        f"Systems with both: {len(frame)}. Largest difference: {np.abs(diff).max():.3f} A. Negative means the battery loses",
        "charge the meter does not see (BMS supply, balancing, self-discharge, sensor offset).",
    ]


def synthetic_block(ctx: dict) -> list[str]:
    synth = ctx["synth"]
    checks = ctx["checks"]
    v6 = checks[checks["check"] == "V6"]
    v7 = checks[checks["check"] == "V7"]
    est = synth[synth["status"] == _core.STATUS_TEXT[_core.OK]]
    truth = float(synth["truth_points_per_year"].iloc[0])
    capacity_fade = synth["capacity_fade_points_per_year"].unique()
    wt = synth["t_high"] - synth["t_low"]
    wb = synth["boot_high"] - synth["boot_low"]
    lines = [
        "![Synthetic check](img/02_synthetic.png)",
        "",
        f"- Packs: {len(synth)}; with a fade rate: {len(est)}. Each pack has its own operation, current-sensor gain error and",
        "  stop errors of its BMS; the full chain runs on each (rest detection, classification, pairs, fade rate) with the frozen settings.",
        f"- Truth: the expected fade of the usable capacity, {truth:+.2f} points per year (the simulated capacity fades by "
        f"{', '.join(f'{v:+.1f}' for v in capacity_fade)}; the BMS stops at voltage limits, so the usable part fades a little slower).",
        f"- Mean estimate: {est['rate_points_per_year'].mean():+.2f} points per year.",
        f"- The band held the truth in {int(synth['held'].sum())} of {len(synth)} packs ({synth['held'].mean():.1%}); "
        f"each pack's own usable fade in {synth['held_own_usable'].mean():.1%}; the capacity fade in {synth['held_capacity_fade'].mean():.1%}.",
        f"- Offset current: median absolute error {np.median(np.abs(est['offset_current_a'] - est['true_offset_current_a'])):.4f} A "
        f"(true value {', '.join(f'{v:+.3f}' for v in synth['true_offset_current_a'].unique())} A).",
        f"- Bootstrap ÷ band width: median {np.median(wb / wt):.2f}.",
    ]
    if len(v6):
        r = v6.iloc[0]
        lines.append(f"- **V6** ({r['pass_if']}): **{r['result']}**.")
    if len(v7):
        r = v7.iloc[0]
        lines += [
            "",
            f"**V7, the code path.** {r['scope'][0].upper() + r['scope'][1:]}: {r['value']} (rule {r['pass_if']}): **{r['result']}**.",
            "The step-by-step core, one update per pair as a BMS or the C port runs it, gives the same estimate and band as",
            "the batch path that builds the same sums in one step.",
        ]
    return lines


def method_block(ctx: dict) -> list[str]:
    c = ctx["config"]
    sample = pd.DataFrame({"system_id": [0], "onset": [pd.Timestamp("2020-01-02")], "end": [pd.Timestamp("2020-01-02 03:00")],
                           "documented_start": [pd.Timestamp("2020-01-02")]})  # fmt: skip
    (a, b), = masks_for(sample, 0)
    before = (sample["onset"].iloc[0] - a).days
    after = (b - sample["end"].iloc[0]).days
    z = c.level
    return [
        "Rest-anchored coulomb counting, after Figgener et al. (2024, Supplementary Methods), with besslib's own estimator.",
        "",
        f"1. **Rests.** A rest is a stretch where the current stays below C/{1 / c.current_c_rate:.0f} for at least "
        f"{c.min_duration_min:g} minutes (excursions up to {c.peak_s:g} s tolerated), after at least {c.min_throughput:g} × C of charge has moved.",
        f"2. **Full and empty.** The *edge voltage* of a rest is the median voltage in the {c.pre_window_s:g} s before it, while",
        "   current still flows: under load the voltage rises or falls steeply at both ends of the charge window, also for LFP.",
        f"   The system's end-of-charge and end-of-discharge levels are the {c.edge_quantile:g} and {1 - c.edge_quantile:g} quantiles",
        f"   of its edge voltages over the trailing {c.level_window_days:g} days, so every season is in the window. A rest is full",
        f"   (empty) when its edge voltage lies within {c.edge_tolerance:.0%} of the level span of the end-of-charge (end-of-discharge) level.",
        f"   No level exists, and no rest is full or empty, before {c.level_warmup_days:g} days of history.",
        f"3. **Pairs.** Two consecutive full or empty rests form a pair when they are at most {c.max_link_days:g} days apart with at",
        f"   most {c.max_missing_s:g} s of missing data between them. Kinds: full-to-empty, empty-to-full (x = ∓1), full-to-full and",
        "   empty-to-empty (x = 0).",
        "4. **Model.** The charge measured over a pair is `q = C · x − I_offset · Δt + γF · ΔuF + γE · ΔuE`: C the usable",
        "   capacity, I_offset the current the meter does not see (BMS supply, balancing, self-discharge, sensor offset), Δt the",
        "   time between the rests. Each end of a pair adds its own kind's relaxed rest voltage minus a reference, because two",
        "   stops of one kind are not at the same state of charge; γF and γE (Ah per volt near full and near empty) turn that",
        "   voltage into charge. The capacity is read at the forgetting-weighted mean full and empty rest voltages of the memory",
        "   time; the record gives both. All are fitted together by weighted least squares.",
        f"5. **Forgetting.** A pair of age s counts with weight exp(−s / {c.tau_days:g} days).",
        "6. **Trend.** The fit with forgetting also carries a linear trend, `C = c0 + r · years`. The value is read at the date,",
        f"   so a steady fade does not lag (`trend_min_cross` = {c.trend_min_cross:g}"
        + (": always on)." if c.trend_min_cross == 0 else ")."),
        f"7. **Band.** {BAND}, from the scatter of the pairs themselves: a lag-1 sandwich covariance of the residuals (consecutive",
        "   pairs share a rest, so their errors are correlated at lag 1) and a Student-t quantile. The forgetting weight enters",
        f"   the band's sums squared, as for a weighted fit. The current sensor's gain error ({c.gain_error:.1%}, Figgener et al.",
        f"   2024, Supplementary Table 2) is read as a rectangular half-width, {c.gain_error:.1%}/√3 (JCGM 100:2008, 4.3.7), and",
        "   added as a systematic term.",
        "8. **Causal.** The estimate at a date uses only pairs that end before it, as a BMS would.",
        f"9. **Tests masked.** From {plural(before, 'day')} before each field test begins to {plural(after, 'day')} after it ends, the",
        "   rests are removed and no pair spans the window. The estimate compared with a test is the one at the start of",
        "   its masked window.",
        "10. **Fade rate.** The same fit without forgetting, over the whole record: the fade of the capacity between fixed rest",
        f"   voltages, the record's mean. It needs full-to-empty pairs spanning at least {c.fade_min_span_years:g} year.",
        "11. **Plausibility.** An estimate is `not possible from this data` when its levels have crossed, its value or band",
        "   reaches zero, or its memory holds a full-to-empty or empty-to-full pair of the wrong sign. These rules were found on",
        "   held-out system 09.",
        "",
        "When a step is not possible, the output says `not possible from this data` with the reason, and gives no value.",
        "",
        f"Settings (`CapacityConfig()`, hash `{ctx['hash']}`):",
        "",
        table(pd.DataFrame([{"Setting": f"`{k}`", "Value": f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v), "Meaning": MEANING.get(k, "")} for k, v in asdict(c).items()])),
    ]


def settings_block(ctx: dict) -> list[str]:
    tuning, edge, levels = ctx["tuning"], ctx["edge"], ctx["levels"]
    chosen = tuning[tuning["selected"]].iloc[0]
    grid = {
        "current threshold": ", ".join(f"C/{1 / v:.0f}" for v in sorted(tuning["current_c_rate"].unique(), reverse=True)),
        "minimum rest duration, min": ", ".join(f"{v:g}" for v in sorted(tuning["min_duration_min"].unique())),
        "memory time, days": ", ".join(f"{v:g}" for v in sorted(tuning["tau_days"].unique())),
    }
    tolerance = float(edge["tolerance"].iloc[0])
    by_setting = (
        levels.groupby("setting", sort=False)
        .agg(amplitude=("seasonal_amplitude_points", "median"), low=("share_below_70pct", "median"),
             est=("months_estimated", "sum"), months=("record_months", "sum"))
        .reset_index()
    )  # fmt: skip
    level_rows = pd.DataFrame(
        {
            "Level window (warm-up)": by_setting["setting"],
            "Seasonal amplitude of the estimate, points (median of systems)": by_setting["amplitude"].map("{:.2f}".format),
            f"Full-empty pairs below {LOW_SHARE:.0%} of the median charge (median of systems)": (100 * by_setting["low"]).map("{:.2f} %".format),
            "Month ends estimated": [f"{e} of {m}" for e, m in zip(by_setting["est"], by_setting["months"])],
        }
    )  # fmt: skip
    c = ctx["config"]
    reached = tuning[tuning["coverage"] >= MIN_COVERAGE]
    if len(reached):
        outcome = (f"{plural(len(reached), 'setting')} reached that coverage; among them the lowest median absolute error "
                   f"chose C/{1 / chosen['current_c_rate']:.0f}, {chosen['min_duration_min']:g} min, {chosen['tau_days']:g} days "
                   f"(coverage {chosen['coverage']:.0%}, median absolute error {num(chosen['median_abs_error_points'])} points).")  # fmt: skip
    else:
        top = np.isclose(chosen["coverage"], tuning["coverage"].max())
        outcome = (f"**No setting reached it**: the highest development coverage was {tuning['coverage'].max():.0%}. "
                   + ("The rule's fallback, the highest coverage, chose " if top else "The chosen setting, not the highest coverage, is ")
                   + f"C/{1 / chosen['current_c_rate']:.0f}, {chosen['min_duration_min']:g} min, "
                   f"{chosen['tau_days']:g} days (coverage {chosen['coverage']:.0%}, median absolute error "
                   f"{num(chosen['median_abs_error_points'])} points)."
                   + (f" So before the held-out run the {BAND} band was already known to hold only {chosen['coverage']:.0%} of the "
                      "development tests." if chosen["coverage"] < c.level else ""))  # fmt: skip
    return [
        "All settings were fixed on the development systems, by rules written before any estimate was compared with a test.",
        "",
        f"- **Grid search** ({len(tuning)} settings: {'; '.join(f'{k} {v}' for k, v in grid.items())}). Rule: the lowest median",
        f"  absolute error among the settings whose {BAND} band holds at least {MIN_COVERAGE:.0%} of the development tests;".replace("0%", "0 %"),
        "  if none does, the highest coverage. " + outcome,
        f"- **Edge tolerance** {tolerance:.0%} of the level span (`edge_tolerance.py`): the widest tolerance at which the",
        f"  median full-to-empty charge falls by at most {MAX_DROP:.0%} in every bin, each bin with at least {MIN_PAIRS} pairs;",
        "  from pair charges only, no test value. "
        + ("It is the value in `CapacityConfig()`." if np.isclose(tolerance, c.edge_tolerance) else
           f"**`CapacityConfig()` holds {c.edge_tolerance:g} instead.**"),
        "- **Level window** (`level_window.py`, no test value read): a home storage system is rarely emptied in summer and",
        "  rarely filled in winter, so a short window lets the levels follow the season. One year was kept:",
        "",
        table(level_rows),
    ]


def limits_block(ctx: dict, months: pd.DataFrame) -> list[str]:
    c = ctx["config"]
    s = ctx["summaries"]
    tests = ctx["tests"]
    present = [code for code in SHORT if code in months and months[code].fillna(0).sum() > 0]
    rows = []
    for sid in figgener.SYSTEM_IDS:
        r = months.loc[sid]
        row = {"System": sid_text(sid), "Set": set_of(sid), "Months with data": int(r["with_data"])}
        if pd.isna(r.get("estimated")):
            rows.append({**row, "Estimated": "no result file yet"})
            continue
        row["Estimated"] = f"{int(r['estimated'])} ({r['estimated'] / r['with_data']:.0%})"
        row["Before the first pair"] = str(int(r["before_first_pair"]))
        for code in present:
            row[SHORT[code]] = str(int(r[code]))
        row["After the last pair"] = str(int(r["after_last_pair"]))
        row["Estimated month ends without data that month"] = str(int(r["estimated_without_data"]))
        row["Median band width, points"] = num(r["median_band_points"], "{:.1f}")
        rows.append(row)
    month_rows = pd.DataFrame(rows).astype(object).fillna(DASH)
    run = months[months["estimated"].notna()] if "estimated" in months else months.iloc[:0]
    total, estimated = int(run["with_data"].sum()), int(run["estimated"].sum()) if len(run) else 0
    first = int(run["before_first_pair"].sum()) if len(run) else 0
    no_levels_tests = tests[tests["status"] == _core.STATUS_TEXT[_core.NO_LEVELS]]
    not_estimated = tests[tests["run"] & tests["capacity_ah"].isna()]
    reasons = not_estimated["status"].map(status_short).value_counts()

    lines = [
        "**No estimate in the first year.** The full and empty levels need "
        f"{c.level_warmup_days:g} days of history, so the first year of every record gives none "
        f"(`{_core.STATUS_TEXT[_core.NO_LEVELS]}`). Over the {len(run)} systems with results, "
        f"{estimated} of {total} months with data have an estimate; {first} lie before the first pair.",
        f"Field tests in that first year: {len(no_levels_tests)} ({', '.join(no_levels_tests['test_id']) or 'none'}).",
        "",
        table(month_rows),
        "",
        "Column names are short forms of the statuses: "
        + "; ".join(f"*{SHORT[code]}*: `{_core.STATUS_TEXT[code]}`" for code in present) + ".",
        "\"Before the first pair\" months lie before the levels exist or before the first pair after them.",
        "\"Estimated month ends without data that month\": the estimate carries the earlier pairs forward, decaying; its",
        "band widens as they age, but no new measurement enters. The figures show these months as band without a rug mark.",
        "",
        "**Tests without an estimate, by reason** (systems with results): "
        + ("; ".join(f"{reason}: {n}" for reason, n in reasons.items()) if len(reasons) else "none") + ".",
        "This is the designed answer: where the operation gives too few full and empty rests, the output says so and gives no value.",
        "This version covers home storage that reaches full and empty regularly. Storage that works at a middle state of",
        "charge, or only in partial windows, gives no full-to-empty pairs and therefore no estimate.",
        "",
    ]
    lag = ctx["lag"]
    total, at_date = int(lag["n_band"].sum()), int(lag["n_held_at_date"].sum())
    ref_total, ref_held = int(lag["n_reference"].sum()), int(lag["n_held_reference"].sum())
    lines += [
        "**The band is on the capacity between two rest voltages, not on the charge between the BMS stops.** The trend term",
        f"reads the value at the date. On {len(lag)} synthetic packs the band held the capacity between the rest voltages at",
        f"{ref_held} of {ref_total} month ends ({ref_held / ref_total:.0%}), but the charge between the stops only at {at_date} of",
        f"{total} ({at_date / total:.0%}). The stops move as the system ages ([version 0.2 page]({MISMATCH})). The band does not",
        "include that drift, nor a systematic difference between daily operation and a field test at maximum power.",
        "",
        "**The paper's interval is not this band.** In Figgener et al. (2024, Fig. 4) the shaded interval contains a fixed share of all",
        f"estimates: it is the spread of the estimates. besslib's {BAND} band is a band on the capacity, and V4 tests how often it",
        "holds the field test.",
        "",
        "**Field tests are not daily operation.** A field test discharges at the system's maximum power, often deeper than",
        "daily operation. No rate correction is applied; the estimate is compared as it is. The bias table above shows the",
        "median signed error per chemistry next to the test C-rates.",
        "",
    ]
    lines += plausibility_block(ctx) + levels_block(ctx) + findings_block(ctx) + system_notes(ctx, months)
    return lines


def plausibility_block(ctx: dict) -> list[str]:
    bad, s, diag = ctx["implausible"], ctx["summaries"], ctx["diag"]
    hit = bad[bad["bad"] > 0]
    rules = {_core.STATUS_TEXT[_core.LEVELS_CROSSED]: "crossed levels", _core.STATUS_TEXT[_core.WRONG_SIGN]: "wrong-sign pair",
             _core.STATUS_TEXT[_core.OUT_OF_RANGE]: "value or band at or below zero"}
    found = {}
    for sid, m in sorted(ctx["monthly"].items()):
        counts = m["status"].map(rules).value_counts()
        if len(counts):
            found[sid] = counts
    total = int(sum(c.sum() for c in found.values()))
    if found:
        where = "; ".join(f"system {sid_text(sid)}: " + ", ".join(f"{int(n)} {k}" for k, n in c.items()) for sid, c in found.items())
        rule_line = f"Month ends that are `not possible` by the plausibility rules: {total} ({where})."
    else:
        rule_line = "Month ends that are `not possible` by the plausibility rules: none."
    out = [f"**Plausibility.** {rule_line} Estimated month ends with a value or band at or below zero: {int(bad['bad'].sum())}."]
    if hit.empty:
        return out + [""]
    for sid, r in hit.iterrows():
        text = (f"System {sid_text(sid)}: {int(r['bad'])} of its {int(r['estimated'])} estimated month ends have an estimate at or "
                f"below zero or a lower band edge below zero; the first is {r['first_bad']:%b %Y}. Its widest monthly band is "
                f"{r['widest_band_points']:.0f} points.")  # fmt: skip
        if sid in s.index and np.isfinite(s.loc[sid].get("rate_points_per_year", np.nan)):
            m = s.loc[sid]
            text += (f" Its fade fit reads {m['rate_points_per_year']:+.2f} points per year [{m['rate_t_band_low']:+.2f}, "
                     f"{m['rate_t_band_high']:+.2f}], reduced chi-square {m['birge']:.1f}.")  # fmt: skip
        out.append(text)
    others = bad.drop(index=hit.index)
    out += [
        f"Over the other {len(others)} systems: {int(others['bad'].sum())} such month ends. A value like this should have come out "
        "as `not possible from this data`; it was let through, and its fade fit enters V5.",
        "",
    ]
    for sid in hit.index:
        d = diag.get(sid)
        if d is None:
            out += [f"System {sid_text(sid)}: cause not established (its reduced rest table is not available).", ""]
            continue
        split = d["first_wrong"] if pd.notna(d["first_wrong"]) else d["first_inverted"]
        if pd.isna(split):
            out += [f"System {sid_text(sid)}: its levels and pairs look like those of the other systems; cause not established.", ""]
            continue
        lv = d["rests"].dropna(subset=["level_full_v", "level_empty_v"])
        before, after = lv[lv["start"] < split], lv[lv["start"] >= split]
        cross = d["pairs"][d["pairs"]["x"] != 0]
        charge = d["cross_charge"]
        cb, ca = charge[(cross["t_end"] < split).to_numpy()], charge[(cross["t_end"] >= split).to_numpy()]
        clean = [i for i, e in diag.items() if i != sid and e["inverted"] == 0 and e["wrong"] == 0]
        cells = d["cells"]
        text = (f"Cause, from the levels that decide full and empty (figure below; the run's own functions on the reduced rest table, "
                f"the {d['n_pairs']} pairs equal to the run's): before {split:%d %b %Y} the median end-of-charge level of system "
                f"{sid_text(sid)} is {(before['level_full_v'] / cells).median():.3f} V per cell and the end-of-discharge level "
                f"{(before['level_empty_v'] / cells).median():.3f}; from then on {(after['level_full_v'] / cells).median():.3f} and "
                f"{(after['level_empty_v'] / cells).median():.3f}.")  # fmt: skip
        if pd.notna(d["first_inverted"]):
            text += (f" From {d['first_inverted']:%d %b %Y} the end-of-discharge level lies at or above the end-of-charge level "
                     f"({d['inverted']} rests).")  # fmt: skip
        text += (f" Full and empty then no longer mark the two ends of the charge window: {d['wrong']} of the {len(ca)} full-to-empty "
                 f"and empty-to-full pairs from {split:%d %b %Y} on carry charge of the wrong sign, with a median of "
                 f"{num(ca.median(), '{:+.1f}')} points of nominal against {num(cb.median(), '{:+.1f}')} before.")  # fmt: skip
        text += (f" {len(clean)} of the other {len(diag) - 1} systems have neither an inverted level nor a pair of the wrong sign.")
        out += [
            text,
            "These pairs explain the impossible estimates and the fade fit. Why the system's operation changed so that its "
            "levels moved is not established from the data on this page.",
            "",
            f"![System {sid_text(sid)} levels](img/{figure_levels(ctx, diag, sid)})",
            "",
        ]
    return out


def levels_block(ctx: dict) -> list[str]:
    diag, s = ctx["diag"], ctx["summaries"]
    if not diag:
        return []
    rows = []
    same = 0
    for sid, d in sorted(diag.items()):
        if sid in s.index and int(s.loc[sid, "n_pairs_used"]) == d["n_pairs"]:
            same += 1
        rows.append({
            "System": sid_text(sid),
            "Chemistry": chemistry_of(ctx, sid),
            "Cells in series": d["cells"],
            "End-of-charge level, V per cell (min – max)": f"{d['full_cell'].min():.3f} – {d['full_cell'].max():.3f}",
            "End-of-discharge level, V per cell (min – max)": f"{d['empty_cell'].min():.3f} – {d['empty_cell'].max():.3f}",
            "Largest quarter-to-quarter step of the end-of-discharge level, mV per cell": (
                f"{d['step_mv']:+.0f} ({d['step_quarter']})" if d["step_quarter"] is not None else DASH),
            "Rests with empty level ≥ full level": d["inverted"],
            "Full-empty pairs with charge of the wrong sign": d["wrong"],
        })  # fmt: skip
    return [
        "**The levels that decide full and empty**, per system, as the run builds them (the library's own functions on the",
        f"reduced rest tables, test windows masked; the rebuilt pairs equal the run's in {same} of {len(diag)} systems):",
        "",
        table(pd.DataFrame(rows)),
        "",
    ]


def findings_block(ctx: dict) -> list[str]:
    tests, s = ctx["tests"], ctx["summaries"]
    held = tests[(tests["set"] == "held-out") & tests["capacity_ah"].notna()]
    out = []
    if len(held):
        worst = held.reindex(held["error_points"].abs().sort_values(ascending=False).index).head(3)
        out.append("**Largest held-out misses:** " + "; ".join(
            f"{t} {e:+.1f} points (band {b:.1f} wide, {'held' if h else 'not held'})"
            for t, e, b, h in zip(worst["test_id"], worst["error_points"], worst["band_points"], worst["held"])) + ".")  # fmt: skip
        kept = held[held["held"]].sort_values("band_points", ascending=False).head(3)
        if len(kept):
            out.append(f"**Held because the band is wide:** the median band of the held-out estimates is "
                       f"{held['band_points'].median():.1f} points; the widest bands among the held tests are "
                       + "; ".join(f"{t} {b:.1f} points (error {e:+.1f})" for t, b, e in zip(kept["test_id"], kept["band_points"], kept["error_points"]))
                       + ". A test held by a wide band says little about accuracy.")  # fmt: skip
    above, below_tests = [], []
    for sid, m in sorted(ctx["monthly"].items()):
        est = m[m["status"] == _core.STATUS_TEXT[_core.OK]]
        over = est[est["soh_capacity"] > 1]
        if len(over):
            top = tests.loc[tests["system_id"] == sid, "soh_capacity"]
            if len(top) and top.max() <= 1:
                below_tests.append(sid)
            above.append(f"{sid_text(sid)} ({len(over)} of {len(est)}, up to {100 * over['soh_capacity'].max():.1f} %; "
                         + (f"its highest field test {100 * top.max():.1f} %)" if len(top) else "no field test)"))
    if above:
        out.append("**Estimates above the nominal capacity** (month ends, highest value): " + "; ".join(above) + "."
                   + (f" On {ids_text(below_tests)} no field test reaches the nominal capacity." if below_tests else ""))  # fmt: skip
    if len(s):
        zero = s[(s["rate_t_band_low"] <= 0) & (s["rate_t_band_high"] >= 0)]
        out.append("**Fade bands that include zero:** "
                   + (", ".join(f"{sid_text(i)} ({r['set']}, {r['rate_points_per_year']:+.2f} [{r['rate_t_band_low']:+.2f}, {r['rate_t_band_high']:+.2f}])"
                                for i, r in zero.iterrows()) if len(zero) else "none")
                   + ". On these records the fade is not resolved.")  # fmt: skip
        few = s.loc[s["n_pairs_used"].idxmin()]
        out.append(f"**Fewest pairs:** system {sid_text(int(few['system_id']))} with {int(few['n_pairs_used'])} pairs "
                   f"({int(few['n_full_empty'])} full-to-empty or empty-to-full) over {few['span_years']:.1f} years; its fade "
                   f"{few['rate_points_per_year']:+.2f} points per year, band [{few['rate_t_band_low']:+.2f}, {few['rate_t_band_high']:+.2f}], "
                   f"month bootstrap [{few['rate_boot_low']:+.1f}, {few['rate_boot_high']:+.1f}], reduced chi-square {few['birge']:.1f}.")  # fmt: skip
    return [line for item in out for line in (item, "")]


def system_notes(ctx: dict, months: pd.DataFrame) -> list[str]:
    out = []
    s, tests, quality, diag = ctx["summaries"], ctx["tests"], ctx["quality"], ctx["diag"]
    m17 = ctx["monthly"].get(17)
    if m17 is not None:
        q = quality[quality["system_id"] == 17].sort_values("month")
        last, first = pd.to_datetime(q["last"]).to_numpy(), pd.to_datetime(q["first"]).to_numpy()
        gaps = pd.DataFrame({"from": last[:-1], "to": first[1:]})
        gaps["days"] = (gaps["to"] - gaps["from"]).dt.total_seconds() / 86400
        g = gaps.loc[gaps["days"].idxmax()] if len(gaps) else None
        text = "**System 17: a capacity step after a data gap.**"
        if g is not None:
            near = m17[(m17["time"] >= g["from"] - pd.offsets.MonthEnd(1)) & (m17["time"] <= g["to"] + pd.offsets.MonthEnd(2))]
            series = ", ".join(f"{100 * v:.1f} ({t:%b %Y})" for t, v in zip(near["time"], near["soh_capacity"]) if np.isfinite(v))
            text += (f" The data stop on {g['from']:%d %b %Y} and resume on {g['to']:%d %b %Y} ({g['days']:.0f} days). The monthly "
                     f"estimate around the gap, % of nominal: {series}.")  # fmt: skip
        ref = tests[tests["system_id"] == 17].sort_values("began")
        if len(ref) >= 2:
            a, b = ref.iloc[-2], ref.iloc[-1]
            text += (f" The field tests move the same way: {a['test_id']} read {100 * a['test_capacity_ah'] / a['nominal_ah']:.1f} %, "
                     f"{b['test_id']} {100 * b['test_capacity_ah'] / b['nominal_ah']:.1f} %.")  # fmt: skip
        if 17 in s.index and np.isfinite(s.loc[17].get("rate_points_per_year", np.nan)):
            r = s.loc[17]
            sign = "positive" if r["rate_points_per_year"] > 0 else "negative"
            text += (f" A straight line through a record with a step reads {sign} here: {r['rate_points_per_year']:+.2f} points per "
                     f"year [{r['rate_t_band_low']:+.2f}, {r['rate_t_band_high']:+.2f}]. Cells do not gain capacity, so the step is in "
                     "the usable capacity the system gives; the fade model has no term for a step.")  # fmt: skip
        out += [text, ""]
    if 19 in months.index and pd.notna(months.loc[19].get("median_band_points")):
        others = months.drop(index=19)["median_band_points"].dropna()
        r = s.loc[19] if 19 in s.index else None
        text = (f"**System 19: high scatter.** Its median monthly band is {months.loc[19, 'median_band_points']:.1f} points wide, against "
                f"{others.median():.1f} for the median of the other systems with results.")  # fmt: skip
        if r is not None and np.isfinite(r.get("birge", np.nan)):
            text += f" The fade fit's reduced chi-square is {r['birge']:.1f}."
        if 19 in diag:
            lfp = {i: float(d["empty_cell"].median()) for i, d in diag.items() if chemistry_of(ctx, i) == "LFP" and i != 19}
            text += (f" Its median end-of-discharge level is {diag[19]['empty_cell'].median():.3f} V per cell; the other LFP systems: "
                     + ", ".join(f"{sid_text(i)} {v:.3f}" for i, v in sorted(lfp.items())) + ".")  # fmt: skip
        text += (" That this stop lies on the flat part of the LFP voltage curve, where the edge voltage says little about the state"
                 " of charge, is an observation from the development work that this page does not back with a number.")  # fmt: skip
        out += [text, ""]
    if 11 in diag:
        d = diag[11]
        q = d["quarterly_empty"]
        text = (f"**System 11: its end-of-discharge level moves.** Over the record it ranges from {d['empty_cell'].min():.3f} to "
                f"{d['empty_cell'].max():.3f} V per cell; the largest step between two quarters is {d['step_mv']:+.0f} mV per cell, "
                f"into {d['step_quarter']} (quarterly medians "
                + ", ".join(f"{k} {v:.3f}" for k, v in q.items() if abs((k - d['step_quarter']).n) <= 1) + ").")  # fmt: skip
        text += (" That the system's own software made this change, and how the usable capacity responds to it, is an observation"
                 " from the development work that this page does not back with a number.")  # fmt: skip
        out += [text, ""]
    return out


def rebuild_block(ctx: dict) -> list[str]:
    archives = {k: v for k, v in ctx["zenodo"].items() if k.startswith("Data_ID_")}
    size = sum(v["size"] for v in archives.values()) / 1e9
    return [
        "```",
        "pip install -e .[dev]",
        "python validation/run_capacity.py <id>        # one system, 1 to 21; downloads its archive if needed",
        "python validation/check_capacity.py           # the checks V2-V8",
        "python validation/make_capacity_doc.py        # this page and its figures",
        "```",
        "",
        f"The {len(archives)} system archives together are {size:.0f} GB (sizes from the Zenodo record, "
        "`validation/results/zenodo_files.json`). `run_capacity.py` reduces each held-out archive to its rest candidates and",
        "then deletes it. The tuning and the settings studies are rebuilt with `tune_capacity.py`, `edge_tolerance.py`,",
        "`level_window.py` and `synthetic_coverage.py` (V6) in `validation/`.",
        f"Every result row carries the configuration hash; this page refuses to build from results of any hash other than `{ctx['hash']}`.",
    ]


def document(ctx: dict, months: pd.DataFrame, system_figures: dict) -> str:
    parts = [
        "# Phase 2 — capacity from operating data, checked against the field tests",
        "",
        "Written by `validation/make_capacity_doc.py` from `validation/results/`. No number on this page is typed by hand.",
        "",
        f"besslib estimates the usable capacity of a home storage system from its daily operation, with a {BAND} band, and",
        "compares it with the field capacity tests measured in phase 1 ([`01_reference.md`](01_reference.md)). The settings were",
        f"tuned on the {len(DEVELOPMENT)} development systems, then frozen. The {len(HELD_OUT)} held-out systems were run once "
        "at the settings of release 0.1.0 and once at these ([what changed](04_version_0_2.md))"
        + ("." if all(i in ctx["summaries"].index for i in HELD_OUT) else
           f" ({sum(i in ctx['summaries'].index for i in HELD_OUT)} of {len(HELD_OUT)} have results)."),
        "The headline comes from held-out systems only.",
        "",
        "## Result",
        "",
        *headline(ctx),
        "",
        "![Every test against its estimate](img/02_errors.png)",
        "",
        "## Checks V2–V8",
        "",
        *checks_block(ctx),
        "",
        "## Why a check failed",
        "",
        *failures_block(ctx),
        "",
        "## Development systems (tuning, not a result)",
        "",
        *development_block(ctx),
        "",
        "## Every system over time",
        "",
        "Each panel is one system: the monthly estimate with its band, the field tests, and the estimate each test was",
        "compared with (drawn just before the test). Gaps in the band are months without an estimate; nothing is",
        "interpolated. The strip at the bottom marks every month with data.",
        "",
    ]
    for chemistry in ORDER:
        parts += [f"![{chemistry} systems](img/{system_figures[chemistry]})", ""]
    parts += [
        "## Every test",
        "",
        "The estimate is the one at the start of the test's masked window, from operating data before it only.",
        "Error = estimate minus test, in points of nominal capacity.",
        "",
        *tests_block(ctx),
        "",
        "### Bias per chemistry",
        "",
        *bias_block(ctx),
        "",
        "No rate correction is applied: a field test discharges at the system's maximum power and often deeper than daily",
        "operation, and the estimate measures the capacity between the system's own end of charge and end of discharge",
        "under its normal load.",
        "",
        "## Fade rates",
        "",
        "![Fade rates](img/02_fade.png)",
        "",
        *fade_block(ctx),
        "",
        "## Offset current",
        "",
        "besslib fits the offset current together with the capacity. The paper finds it from full-to-full and",
        "empty-to-empty pairs: the intercept of their mean current against their voltage difference (Figgener et al. 2024,",
        "Supplementary Fig. 13). Both are computed here from the same pairs; besslib's is the whole-record fit.",
        "",
        *offset_block(ctx),
        "",
        "## Synthetic check V6 and code-path check V7",
        "",
        *synthetic_block(ctx),
        "",
        "## How it works",
        "",
        *method_block(ctx),
        "",
        "## How the settings were fixed",
        "",
        *settings_block(ctx),
        "",
        "## Limits",
        "",
        *limits_block(ctx, months),
        "## Rebuild",
        "",
        *rebuild_block(ctx),
        "",
        "## Source and credit",
        "",
        "Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in",
        "capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.",
        "Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9.",
        "The values on this page are computed by besslib from that data; only the fade means marked as the paper's are taken from it.",
        "besslib computes capacity figures; it does not certify them.",
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    ctx = load()
    ctx["implausible"] = implausible(ctx)
    ctx["diag"] = level_diagnostics(ctx)
    months = month_table(ctx)
    figure_errors(ctx)
    system_figures = {chemistry: figure_systems(ctx, chemistry, months) for chemistry in ORDER}
    figure_fade(ctx)
    figure_synthetic(ctx)
    (DOCS / PAGE).write_text(document(ctx, months, system_figures))
    held = ctx["tests"][(ctx["tests"]["set"] == "held-out") & ctx["tests"]["run"]]
    print(f"docs/{PAGE} written: {len(ctx['summaries'])} of {len(figgener.SYSTEM_IDS)} systems, "
          f"{len(held)} held-out tests with results, configuration {ctx['hash']}")  # fmt: skip


if __name__ == "__main__":
    main()
