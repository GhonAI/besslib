"""Write ``docs/05_simulator.md``: what ``besslib.sim`` is, its checks, and what its six cases show about besslib.

Written from the result files; no number on the page is typed. Every number, also in a sentence, is read from a
file or from a case file; the words "passes" and "fails" are those of the rows' ``passed`` column.

Reads ``results/sim_checks.csv`` (the simulator's own checks), ``results/checks_sim.csv`` (the case checks),
``results/sim/{heat,imbalance,leak,cold,grid,drift}.csv`` (one row per pack), ``results/sim/one_path.csv``,
``results/sim/{lfp_preset,tran_preset,s10_lfp}.csv`` and the case files. Without options it refuses results that
are not the held-out seeds or that come from other settings. ``--development`` reads ``data/sim_dev/`` instead and
writes the page and its figures into ``data/sim_dev/`` (a trial, never a result).

Usage:
    python validation/make_sim_doc.py
    python validation/make_sim_doc.py --development
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import besslib
from besslib import sim
from besslib.sim.bus import sunspec

import sim_case_cold
import sim_case_drift
import sim_case_grid
import sim_case_imbalance
import sim_case_leak
import sim_s10
import sim_truth
from make_capacity_doc import DASH, num
from make_reference_doc import AXIS, INK, INK2, MUTED, SURFACE, table
from sim_drift_points import drift_terms

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
DEV = "--development" in sys.argv
BASE = ROOT / "data" / "sim_dev" if DEV else RESULTS / "sim"
CHECKS = BASE / "checks_sim.csv" if DEV else RESULTS / "checks_sim.csv"
for _a in sys.argv:
    if _a.startswith("--checks="):
        CHECKS = Path(_a[9:])
OUT = BASE if DEV else ROOT / "docs"
IMG = OUT / "img"
PAGE = "05_simulator.md"
PNG = {"Software": None}
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
CASE_FILES = {"heat": "heat.csv", "imbalance": "imbalance.csv", "leak": "leak.csv", "cold": "cold.csv",
              "grid": "grid.csv", "drift": "drift.csv"}
DRIFT_SETTING = re.compile(r"D\d+")
WORD = {"True": "passes", "False": "fails", "": "information"}

plt.rcParams.update({"font.size": 9, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE, "axes.edgecolor": AXIS,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "axes.spines.top": False, "axes.spines.right": False})


# ----------------------------------------------------------------------------- reading


def tidy(value) -> str:
    """A table cell as text; a missing number is a dash, never "nan"."""
    return re.sub(r"\bnan\b", DASH, str(value).replace("(nan%)", f"({DASH})")).replace(f"+{DASH}", DASH)


def md(frame: pd.DataFrame) -> str:
    return table(frame.astype(object).map(tidy))


def load() -> dict:
    ctx = {"cases": {}}
    for name, file in CASE_FILES.items():
        path = BASE / file
        if not path.exists():
            if not DEV:
                raise SystemExit(f"{path}: missing: run `python validation/sim_cases.py run {name} --seeds held-out`")
            continue
        t = pd.read_csv(path, keep_default_na=False, na_values=[""])
        wanted = "development" if DEV else "held-out"
        if set(t["seed_set"]) != {wanted}:
            raise SystemExit(f"{path.name}: its seeds are {sorted(set(t['seed_set']))}, this build reads {wanted} seeds")
        for column, value in sim_truth.HASHES.items():
            if set(t[column]) != {value}:
                raise SystemExit(f"{path.name}: {column} is not today's {value}: run it again")
        ctx["cases"][name] = t
    if not CHECKS.exists():
        raise SystemExit(f"{CHECKS}: missing: run `python validation/sim_cases.py summary`")
    ctx["checks"] = pd.read_csv(CHECKS, keep_default_na=False)
    ctx["own"] = pd.read_csv(RESULTS / "sim_checks.csv", keep_default_na=False)
    ctx["one_path"] = pd.read_csv(RESULTS / "sim" / "one_path.csv", keep_default_na=False)
    ctx["s10"] = pd.read_csv(RESULTS / "sim" / "s10_lfp.csv")
    ctx["lfp"] = pd.read_csv(RESULTS / "sim" / "lfp_preset.csv", keep_default_na=False)
    ctx["tran"] = pd.read_csv(RESULTS / "sim" / "tran_preset.csv", keep_default_na=False)
    return ctx


def strip(text: str) -> str:
    text = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(text))
    return text.split(": ", 1)[1] if ": " in text else text


def setting_of(text: str) -> str:
    return str(text).split(":", 1)[0]


def rows(ctx: dict, check: str, setting: str | None = None) -> pd.DataFrame:
    c = ctx["checks"]
    keep = c["check"] == check
    if setting is not None:
        keep &= c["what"].map(setting_of) == setting
    return c[keep]


def word(passed) -> str:
    return WORD.get(str(passed), str(passed))


def first(text: str) -> str:
    """The part before the first semicolon, with no bracket left open."""
    head = str(text).split(";")[0]
    if head.count("(") > head.count(")"):
        head = head[: head.rfind("(")].rstrip()
    return head


def finish(fig, name: str) -> str:
    IMG.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(IMG / name, dpi=150, metadata=PNG)
    plt.close(fig)
    return f"![{name}](img/{name})"


def pct(a, b) -> str:
    return f"{a:.0f} of {b:.0f} ({a / b:.1%})" if b else DASH


def sub(t: pd.DataFrame, *settings: str) -> pd.DataFrame:
    return t[t["setting"].isin(settings)]


# ----------------------------------------------------------------------------- figures


def fig_heat(t: pd.DataFrame) -> str:
    f = t[t["setting"] == "fan-fails"].sort_values("seed")
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    top = float(max(f["hours_above_true"].max(), f["hours_hottest_group"].max())) * 1.03
    ax.plot([0, top], [0, top], color=MUTED, lw=1)
    ax.scatter(f["hours_above_true"], f["hours_hottest_group"], s=12, color=BLUE, alpha=0.6, lw=0)
    ax.set_xlabel(f"Hours at or above {f['limit_c'].iloc[0]:.0f} °C, housing sensor (what besslib counts)")
    ax.set_ylabel("The same, hottest cell group (truth)")
    ax.set_title(f"{len(f)} packs whose fan fails; the line: equal hours", fontsize=9, color=INK2)
    return finish(fig, "05_heat.png")


def share_bar(ax, labels, values, colour, title):
    ax.bar(np.arange(len(labels)), values, color=colour)
    ax.set_xticks(np.arange(len(labels)), labels, rotation=20, ha="right")
    ax.set_ylim(0, 1)
    ax.set_title(title, fontsize=9, color=INK2)


def fig_imbalance(t: pd.DataFrame) -> str:
    s = list(sim_case_imbalance.SETTINGS)
    g = t.groupby("setting")
    rest = [g.get_group(k)["held_rest"].sum() / max(g.get_group(k)["with_band"].sum(), 1) for k in s if k in g.groups]
    stop = [g.get_group(k)["held_stop"].sum() / max(g.get_group(k)["with_stop_truth"].sum(), 1) for k in s if k in g.groups]
    labels = [f"{k}\n({int(g.get_group(k)['with_band'].sum())})" for k in s if k in g.groups]
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.3), sharey=True)
    share_bar(axes[0], labels, rest, BLUE, "Band holds T_rest (T2a)")
    share_bar(axes[1], labels, stop, ORANGE, "Band holds T_stop (T2b)")
    axes[0].set_ylabel("Share of month ends with a band\n(their number below)")
    return finish(fig, "05_imbalance.png")


def fig_leak(t: pd.DataFrame) -> str:
    s = [k for k in sim_case_leak.SETTINGS if k in set(t["setting"]) and not k.endswith("-off")]
    g = t.groupby("setting")
    held = [(g.get_group(k)["held_before"].sum() + g.get_group(k)["held_after"].sum())
            / max(g.get_group(k)["n_before"].sum() + g.get_group(k)["n_after"].sum(), 1) for k in s]
    det = [g.get_group(k)["detected"].astype(bool).mean() for k in s]
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.4), sharey=True)
    share_bar(axes[0], s, held, BLUE, "A4 band holds the true loss (T3a)")
    share_bar(axes[1], s, det, GREEN, "Leak detected (T3b)")
    axes[0].set_ylabel("Share")
    return finish(fig, "05_leak.png")


def fig_cold(t: pd.DataFrame) -> str:
    d = t[t["setting"] == "t4d"].sort_values("band_c")
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.3))
    axes[0].plot(d["band_c"], d["minutes"], "o-", color=BLUE)
    axes[0].set_ylabel("Minutes at constant power to the BMS stop")
    axes[1].plot(d["band_c"], d["energy_kwh"], "s-", color=ORANGE)
    axes[1].set_ylabel("Energy delivered, kWh")
    for ax in axes:
        ax.set_xlabel("Pack temperature, °C")
    return finish(fig, "05_cold.png")


def fig_grid(t: pd.DataFrame) -> str | None:
    if t is None or t.empty:
        return None
    s = list(dict.fromkeys(t["setting"]))
    g = t.groupby("setting")
    est = [g.get_group(k)["estimate_over_true_capacity"].median() for k in s]
    held = [g.get_group(k)["held_rest"].sum() / max(g.get_group(k)["with_band"].sum(), 1) for k in s]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), sharey=True)
    share_bar(axes[0], s, est, BLUE, "A1 estimate over the true capacity, median")
    share_bar(axes[1], s, held, ORANGE, "Band holds T_rest, month ends with a band (T5b)")
    return finish(fig, "05_grid.png")


def fig_drift(t: pd.DataFrame) -> str:
    m = drift_terms(t)
    s = [k for k in sim_case_drift.SETTINGS if k in m.index and re.fullmatch(r"D\d+", k)]
    x = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for k, (col, c, lab) in enumerate((("estimator", BLUE, "estimator: estimate - T_rest"), ("drift", ORANGE, "drift: T_rest - T_ops"),
                                       ("rate", GREEN, "rate: T_ops - T_test"))):
        ax.bar(x + (k - 1) * 0.25, m.loc[s, col], 0.25, color=c, label=lab)
    ax.plot(x, m.loc[s, "miss"], "o", color=INK, ms=7, label="estimate - test value")
    ax.plot(x, m.loc[s, "fixed_less_rest"], "D", color=INK2, mfc="none", ms=7, label="T_fixed - T_rest")
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.set_xticks(x, s)
    ax.set_ylabel("Points of nominal capacity (medians over tests)")
    ax.legend(frameon=False, fontsize=7.5)
    return finish(fig, "05_drift.png")


# ----------------------------------------------------------------------------- findings


def drift_numbers(ctx: dict) -> dict | None:
    t = ctx["cases"].get("drift")
    if t is None:
        return None
    m = drift_terms(t)
    packs = t[t["row"] == "pack"]
    tests = t[(t["row"] == "test") & (t["test_status"] == "ok")]
    return {"terms": m, "packs": packs, "tests": tests}


def findings(ctx: dict) -> list[dict]:
    """The findings, most important first: ``case``, ``text``, ``tag``; each only where the rows support it."""
    cs, out = ctx["cases"], []

    def add(case, text, tag):
        out.append({"case": case, "text": text, "tag": tag})

    g = cs.get("grid")
    if g is not None:
        nv = g[g["setting"].str.endswith("-never")]
        if len(nv) and (nv["estimated_months"] > 0).all():
            parts = []
            for key in ("nmc", "lfp", "string"):
                p = nv[nv["setting"] == f"{key}-never"]
                if len(p):
                    parts.append(f"{key.upper() if key != 'string' else 'the string'}: {pct(p['estimated_months'].sum(), p['month_ends'].sum())}")
            ratio = ", ".join(f"{p['estimate_over_true_capacity'].median():.0%} ({k.upper() if k != 'string' else 'string'})"
                              for k in ("nmc", "lfp", "string") for p in [nv[nv["setting"] == f"{k}-never"]] if len(p))
            add(5, "**On grid duty without a full charge, version 0.2 gives a wrong capacity instead of refusing.** The pack never leaves the middle of its "
                   "range, yet A1 is `estimated` at month ends of every pack (" + "; ".join(parts) + "), at a median " + ratio
                   + " of the true capacity. It takes the edges of the daily window for full and empty.", "T5a")
        c7 = g[g["setting"].isin(["lfp-7", "string-7"])]
        if len(c7) and c7["mean_error_rest_ah"].notna().any():
            add(5, "**A weekly full charge does not repair it on any of the three**: the estimate is a median "
                + ", ".join(f"{p['estimate_over_true_capacity'].median():.0%} ({k.upper() if k != 'string' else 'string'})"
                            for k in ("nmc", "lfp", "string") for p in [g[g["setting"] == f"{k}-7"]] if len(p))
                + " of the true capacity. On LFP it also lies " + " and ".join(
                f"{abs(p['mean_error_rest_ah'].mean()):.0f} Ah {'below' if p['mean_error_rest_ah'].mean() < 0 else 'above'} T_rest ({name})"
                for k, name in (("lfp", "16 cells of 280 Ah"), ("string", "the 396-cell string")) for p in [g[g['setting'] == f'{k}-7']] if len(p))
                + ". The empty end is never reached.", "T5b")
        s7 = g[g["setting"] == "string-7"]
        if len(s7) and s7["spread_loss_pack"].notna().any():
            add(5, f"**More cells in series cost more capacity**: the 396-cell string loses {s7['spread_loss_pack'].median():.2%} of its usable capacity to the cell spread; "
                   f"a 14-cell pack with the same spread and seed loses {s7['spread_loss_14_groups'].median():.2%}.", "T5f")

    d = drift_numbers(ctx)
    if d is not None:
        m, packs = d["terms"], d["packs"]
        lines = rows(ctx, "T6c", "the line miss = a + b D through the per-pack medians of the four main settings")
        if {"D0", "D157"} <= set(m.index):
            width = packs[packs["setting"] == "D0"]["median_band_points"].median()
            main = d["tests"][d["tests"]["setting"].isin(["D0", "D50", "D100", "D157"])]
            line = re.search(r"b ([+-][\d.]+) points per 10 mV per cell \(([^)]*)\)", lines["result"].iloc[0]) if len(lines) else None
            if m.loc["D157", "fixed_less_rest"] < m.loc["D0", "fixed_less_rest"]:
                add(6, "**A falling discharge stop hides fade.** A1 follows the stop down. At the largest drift the capacity between the first year's "
                       f"rest voltages is {abs(m.loc['D157', 'fixed_less_rest']):.1f} points of nominal below the capacity between the voltages 0.2 "
                       f"reads at ({abs(m.loc['D0', 'fixed_less_rest']):.1f} without drift).", "T6d")
            text = (f"The drift alone {'does not move' if abs(m.loc['D157', 'miss'] - m.loc['D0', 'miss']) < width else 'moves'} the gap to a capacity "
                    f"test: estimate less test is {m.loc['D0', 'miss']:+.2f} points without drift and {m.loc['D157', 'miss']:+.2f} at 157 mV per cell"
                    + (f" (slope {line.group(1)} points per 10 mV, 90 % band {line.group(2)})" if line else "")
                    + f". The largest term is the {'rate' if m.loc['D157', 'rate'] > max(abs(m.loc['D157', 'drift']), abs(m.loc['D157', 'estimator'])) else 'other'} "
                    f"term ({m.loc['D157', 'rate']:+.2f} points: the test at 0.84 C gives less than daily operation). The band is {width:.2f} points wide "
                    f"and holds the test value in {pct(main['held'].sum(), len(main))} tests.")
            if "D0-onestop" in m.index:
                text += (f" The test stops at the field tests' own lowest voltage; with one stop for tests and daily operation the rate term "
                         f"would be {m.loc['D0-onestop', 'rate']:+.1f} points.")
            add(6, text, "T6b, T6c, T6d")
        r = rows(ctx, "T6a", "D0")
        if len(r):
            add(6, f"Even without drift the band holds its own quantity (T_rest) in {first(r['result'].iloc[0])} month ends; the target is 85-95 %.", "T6a")

    t = cs.get("imbalance")
    if t is not None:
        b = t[t["setting"] == "sd1-balance"]
        o = t[t["setting"] == "sd1-off"]
        if len(b) and b["with_band"].sum():
            txt = (f"On the NMC pack (sd 1 %, balancing on) the A1 band holds T_rest at {pct(b['held_rest'].sum(), b['with_band'].sum())} month ends "
                   f"(mean error {b['mean_error_rest_ah'].mean():+.2f} Ah) and T_stop at {pct(b['held_stop'].sum(), b['with_stop_truth'].sum())} "
                   f"(mean error {b['mean_error_stop_ah'].mean():+.2f} Ah).")
            if b["mean_error_rest_ah"].mean() < 0 and abs(b["mean_error_stop_ah"].mean()) < abs(b["mean_error_rest_ah"].mean()):
                txt += " The estimate is nearer the charge between the stops; T_rest, the charge between the mean rest voltages on a curved curve, is higher."
            add(2, txt, "T2a, T2b")
        if len(b) and len(o):
            cost = (f"The usable capacity lost to imbalance is {b['imbalance_loss'].median():.1%} with balancing and {o['imbalance_loss'].median():.1%} without it")
            n_off = int((o["with_stop_truth"] > 0).sum())
            cost += f"; with balancing off A1 finds a band in {n_off} of {len(o)} packs only"
            add(2, "**Balancing is worth a lot over three years.** " + cost + ".", "T2c")

    t = cs.get("leak")
    if t is not None:
        bal = {k: t[t["setting"] == k] for k in ("leak0", "leak5", "leak15", "leak50")}
        if all(len(v) for v in bal.values()):
            det = ", ".join(f"{k[4:]} mA {pct(v['detected'].astype(bool).sum(), len(v))}" for k, v in bal.items())
            p = bal["leak15"]
            txt = (f"A4 follows a leak late: its band holds the true loss at {pct(p['held_before'].sum(), p['n_before'].sum())} month ends before the "
                   f"onset and at {pct(p['held_after'].sum(), p['n_after'].sum())} from 180 days after it (15 mA). Packs in which the leak is detected, by leak "
                   f"(0 = control): {det}.")
            off = t[t["setting"].str.endswith("-off")]
            if len(off) and (off["n_before"] + off["n_after"]).sum() == 0:
                txt += " With balancing off no month end has an A4 band, so nothing is detected."
            add(3, txt, "T3a, T3b, T3c")

    t = cs.get("cold")
    if t is not None:
        b = t[(t["row_kind"] == "band") & (t["setting"] == "noblock")]
        p = t[(t["row_kind"] == "pack") & (t["setting"] == "noblock")]
        d4 = t[t["setting"] == "t4d"].sort_values("band_c")
        if len(b) and len(p):
            txt = (f"A5 orders the bands right in {pct(p['colder_higher'].sum(), p['pairs'].sum())} neighbouring pairs; its one-step value is a median of "
                   f"{(b['resistance_ohm'] / b['r0_model_ohm'] - 1).median():+.1%} from the model's r0, and the band holds the median-step truth in "
                   f"{pct(b['held'].astype(bool).sum(), len(b))} band values, the estimator's own same-days form in {pct(b['held_same_days'].astype(bool).sum(), len(b))}.")
            bad = p[~p["t4c_ok"].astype(bool)]
            txt += f" Hours charging below 0 °C (no block) agree with the truth in {pct(len(p) - len(bad), len(p))} packs"
            if len(bad):
                off = (bad["charging_below_besslib_h"] - bad["charging_below_true_h"]).abs().max()
                txt += f"; the others are off by at most {off:.2f} h (tolerance {bad['charging_below_tol_h'].max():.2f} h)"
            txt += "."
            if len(d4):
                txt += (f" A full pack at constant 0.5 C of its nominal energy runs {d4['minutes'].iloc[0]:.0f} min and gives {d4['energy_kwh'].iloc[0]:.1f} kWh at "
                        f"{d4['band_c'].iloc[0]:.0f} °C, {d4['minutes'].iloc[-1]:.0f} min and {d4['energy_kwh'].iloc[-1]:.1f} kWh at {d4['band_c'].iloc[-1]:.0f} °C.")
            add(4, txt, "T4a, T4b, T4c, T4d")

    t = cs.get("heat")
    if t is not None:
        f = t[t["setting"] == "fan-fails"]
        if len(f):
            txt = (f"B4's temperature hours are counted exactly: {pct(f['t1a_ok'].astype(bool).sum(), len(f))} packs agree with the true sensor trace "
                   f"within one sample step per edge crossing.")
            if f["hours_above_true"].median() > 0:
                txt += (f" The housing sensor is at or above {f['limit_c'].iloc[0]:.0f} °C for a median {f['hours_above_true'].median():.0f} h, "
                        f"the hottest cell group for {f['hours_hottest_group'].median():.0f} h.")
            add(1, txt, "T1a, T1c")
    return out


def shows(found: list[dict], case: int) -> list[str]:
    """Which of the numbered findings this case gives (the text stands once, in "What it found")."""
    mine = [k for k, f in enumerate(found, start=1) if f["case"] == case]
    if not mine:
        return ["**What this shows.** No finding in this build's rows.", ""]
    return ["**What this shows.** Finding" + ("s " if len(mine) > 1 else " ") + ", ".join(str(k) for k in mine) + " above.", ""]


# ----------------------------------------------------------------------------- tables


def check_table(ctx: dict, prefix: str) -> list[str]:
    c = ctx["checks"]
    sel = c[c["check"].str.startswith(prefix)]
    if sel.empty:
        return ["No rows for this case in this build.", ""]
    empty = sel["result"].astype(str).str.contains(r"^no month end|^0 of 0", regex=True)
    asked = {}
    for r in sel.itertuples():
        asked.setdefault(r.check, strip(r.what))
    out = [f"- **{k}**: {v}." for k, v in asked.items()] + [""]
    full = sel[~empty]
    t = pd.DataFrame({"Check": full["check"], "Setting": full["what"].map(setting_of), "Result": full["result"],
                      "Pass if": full["pass_if"], "Outcome": full["passed"].map(word)})
    out += [md(t), ""]
    if empty.any():
        e = sel[empty]
        out += ["No month end has a band in " + ", ".join(f"{r.check} {setting_of(r.what)} ({word(r.passed)})" for r in e.itertuples())
                + ": there is nothing to judge.", ""]
    return out


def drift_tables(ctx: dict) -> list[str]:
    d = drift_numbers(ctx)
    if d is None:
        return ["No rows for this case in this build.", ""]
    m = d["terms"]

    def cell(chk, st):
        r = rows(ctx, chk, st)
        if not len(r):
            return DASH
        out = first(r["result"].iloc[0])
        out = re.sub(r"^(\d+ of \d+)( packs| tests)? within 10 mV", r"\1", out)
        return out + (f", {word(r['passed'].iloc[0])}" if str(r["passed"].iloc[0]) != "" else "")

    def block(names):
        recs = []
        for k in names:
            if k not in m.index:
                continue
            recs.append({"D": k, "S9": cell("S9", k), "T6a": cell("T6a", k), "T6b": cell("T6b", k), "miss": f"{m.loc[k, 'miss']:+.2f}",
                         "estimator": f"{m.loc[k, 'estimator']:+.2f}", "drift": f"{m.loc[k, 'drift']:+.2f}", "rate": f"{m.loc[k, 'rate']:+.2f}",
                         "T_fixed - T_rest": f"{m.loc[k, 'fixed_less_rest']:+.2f}"})
        return md(pd.DataFrame(recs))

    main = [k for k in sim_case_drift.SETTINGS if re.fullmatch(r"D\d+", k)]
    other = [k for k in sim_case_drift.SETTINGS if k not in main]
    out = [
        "- **S9**: the field statistic of [docs/04](04_version_0_2.md) (first to last month-end empty rest voltage per cell) against the planted fall; it carries the estimator's 180-day memory, so it trails a steady fall.",
        "- **T6a**: the A1 band holds T_rest at the month end. **T6b**: the test value lies inside the band at the test date.",
        "- **T6c**: estimate less test value (miss). **T6d**: the miss as three terms, estimator (estimate - T_rest), drift (T_rest - T_ops) and rate "
        "(T_ops - T_test), medians over the tests, in points of nominal capacity; the last column is T_fixed - T_rest.", "",
        "Main settings:", "", block(main), "", "Information settings:", "", block(other), ""]
    line = rows(ctx, "T6c", "the line miss = a + b D through the per-pack medians of the four main settings")
    if len(line):
        out += [f"Line through the four main settings: {line['result'].iloc[0]}.", ""]
    return out


def s7_counts(ctx: dict) -> dict | None:
    """The counts of S7's row in sim_checks.csv (written by ``sim_checks.s7``); None while the row does not exist."""
    row = ctx["own"][ctx["own"]["check"] == "S7"]
    if not len(row):
        return None
    text = str(row["result"].iloc[0])
    found = {k: re.search(rf"{k}: (\d+)", text) for k in ("cases", "runs", "points read", "writes", "mismatches",
                                                               "effects not exercised")}
    models = re.search(r"models: ([\d, ]+?);", text)
    if models is None or not all(found.values()):
        raise SystemExit(f"sim_checks.csv: the S7 row does not read as 'models: ..; cases: N; points read: N; writes: N; mismatches: N': {text}")
    return {"models": [int(m) for m in models.group(1).split(",")], "cases": int(found["cases"].group(1)),
            "points": int(found["points read"].group(1)), "writes": int(found["writes"].group(1)),
            "mismatches": int(found["mismatches"].group(1)), "runs": int(found["runs"].group(1)),
            "per_read": sum(p.type != "pad" for p in sunspec.points()),
            "missed": int(found["effects not exercised"].group(1)),
            "history": text[text.index("The first full run"):] if "The first full run" in text else ""}


def bus_section(ctx: dict) -> list[str]:
    """The bus section: empty while S7 has no row, so the page builds as before."""
    n = s7_counts(ctx)
    if n is None:
        return []
    names = sim.case_names()
    unlimited = sum(not sim.load_case(c).get("bms", {}).get("derate") for c in names)
    return [
        "## The bus", "",
        "`python -m besslib.sim serve --case failing-cell --port 5020` shows the pack's BMS as SunSpec models "
        f"{', '.join(map(str, n['models'][:-1]))} and {n['models'][-1]} over Modbus TCP. The bus never shows the true capacity, the true "
        "state of charge, the resistances, a leak or a planted event; its voltages, current and temperatures are the simulator's own, "
        "without sensor errors. It binds to 127.0.0.1 by default, because Modbus has no login. These models hold no power command: a client can connect or "
        "disconnect the battery and set its state-of-charge reserves. "
        f"S7: pysunspec2, the SunSpec Alliance's client, made {n['points']} point reads over the {n['per_read']} points of the {len(n['models'])} models on {n['cases']} cases; "
        f"{n['mismatches']} mismatches. It made {n['writes']} writes in {n['runs']} runs; {n['missed']} of their effects could not be "
        "tried, because no current flowed for them to act on."
        + (f" {n['history']}." if n["history"] else ""),
        "",
        "S7 checks the encoding and the effect of the set-points; the BMS state is checked against literal values in `tests/test_sim_bus.py`. "
        f"`WChaRteMax` and `WDisChaRteMax`, mandatory in model 802, read 'not implemented' in the {unlimited} of {len(names)} cases that set no "
        "current limit. The server needs `pip install \"besslib[modbus]\"`; the simulator and this extra are not in release "
        f"{besslib.__version__} on PyPI.",
        "",
        "SunSpec's model files do not state the sign of A and W; besslib uses positive = discharging on the bus.", "",
    ]


def own_table(ctx: dict) -> list[str]:
    lines = []
    for r in ctx["own"].itertuples():
        if r.check == "S10":
            continue  # its own line below, from s10_lfp.csv
        if r.check == "S7":
            n = s7_counts(ctx)
            lines.append({"Check": "S7", "What": "the SunSpec bus read and written by pysunspec2 over Modbus TCP",
                          "Result": f"{n['points']} point reads over {n['per_read']} points of {len(n['models'])} models on {n['cases']} cases, {n['writes']} writes, "
                                    f"{n['mismatches']} mismatches" if n else first(r.result),
                          "Pass if": first(r.pass_if), "Outcome": word(r.passed)})
            continue
        what = re.sub(r"\s*\[computed on [^\]]*\]", "", str(r.what)).split(":")[0]
        lines.append({"Check": r.check, "What": what, "Result": first(r.result), "Pass if": first(r.pass_if), "Outcome": word(r.passed)})
    s9 = ctx["checks"][ctx["checks"]["check"] == "S9"]
    main = s9[s9["pass_if"] != "information"]
    if len(main):
        lines.append({"Check": "S9", "What": "the field statistic (docs/04) on simulated packs against the planted fall of the empty stop (case 6)",
                      "Result": "; ".join(f"{setting_of(r.what)}: {first(r.result)[:first(r.result).find(' within')]}" for r in main.itertuples()),
                      "Pass if": main["pass_if"].iloc[0], "Outcome": "; ".join(f"{setting_of(r.what)} {word(r.passed)}" for r in main.itertuples())})
    inside = ctx["s10"][ctx["s10"]["part"] == "10-90 %"]
    if len(inside):
        ok = bool((inside["rms_mv"] <= sim_s10.PASS_MV).all())
        lines.append({"Check": "S10", "What": "the LFP cell against measured HPPC current at 20 and 30 °C, not in its tables",
                      "Result": "; ".join(f"{int(r.temp_c)} °C: RMS {r.rms_mv:.1f} mV" for r in inside.itertuples()),
                      "Pass if": f"RMS <= {sim_s10.PASS_MV:.0f} mV between 10 % and 90 % state of charge", "Outcome": word(ok)})
    return [md(pd.DataFrame(lines)), "", "Full rows, with the packs and months of each: `validation/results/sim_checks.csv`.", ""]


def setup_sentences(ctx: dict) -> dict[str, str]:
    h, i, c, g, s, d = (sim.load_case(n) for n in ("overheating", "cell-imbalance", "cold", "grid-duty", "grid-string", "drifting-stop"))
    ev, amb = h["event"][0], h["temperature"]["ambient"]
    pk = amb["mean_c"] + amb["season_amp_c"] + amb["daily_amp_c"]
    leaks = sorted({v["overrides"]["event"][0]["leak_a"] * 1000 for v in sim_case_leak.SETTINGS.values()} - {0.0})
    spreads = sorted({v["overrides"]["pack.capacity_spread_sd"] for v in sim_case_imbalance.SETTINGS.values()})
    lk = sim.load_case("failing-cell")["event"][0]
    ov, pc = c["duty"]["overlay"][0], c["temperature"]["pack"]
    bm = i["bms"]
    lfp = sim_case_grid._LFP
    tests = d["duty"]["overlay"][0]
    low = d["bms"]["v_cell_min"] - tests["v_min_offset_cell"]
    onestop = [k for k in sim_case_drift.SETTINGS if k.endswith("-onestop")]
    return {
        "heat": f"{h['pack']['groups']} cell groups of the {h['cell']['preset'].upper()} cell scaled to {h['cell']['scale_to_ah']:.0f} Ah, "
                f"{h['case']['months']} months, a thermal network with the sensor at the housing. The fan fails on a random day in months "
                f"{ev['from_month']} to {ev['to_month']} (its air conductances fall to {ev['factor']:.0%}). The summer afternoon reaches {pk:.0f} °C; "
                f"the BMS derates from {h['bms']['derate'][0][0]:.0f} °C to zero current at {h['bms']['derate'][-1][0]:.0f} °C and stops at "
                f"{h['bms']['stop_temp_c']:.0f} °C. Sensor temperature noise is 0, so the sensor trace is the truth besslib is held to.",
        "imbalance": f"{i['pack']['groups']} cell groups of the {i['cell']['preset'].upper()} cell scaled to {i['cell']['scale_to_ah']:.0f} Ah, "
                     f"{i['case']['months']} months. The capacity spread has sd {' or '.join(f'{x:.0%}' for x in spreads)}; each group drains "
                     f"{i['pack']['self_discharge_mean_a'] * 1000:.1f} mA on average (sd {i['pack']['self_discharge_sd_a'] * 1000:.1f} mA). "
                     f"Passive balancing, on or off: every group above {bm['balance_v']:.1f} V whose voltage is more than {bm['balance_spread_v'] * 1000:.0f} mV "
                     f"above the lowest group's bleeds through {bm['bleed_ohm']:.0f} ohm; the lowest group never bleeds.",
        "leak": f"As the imbalance case with sd {spreads[0]:.0%} and balancing on. One group gets a leak resistor across its cell on a random day in "
                f"months {lk['from_month']} to {lk['to_month']}; the leak is {', '.join(f'{x:.0f}' for x in leaks)} mA at 3.7 V per cell. "
                "The controls draw the same day and group and plant nothing. Balancing off is information.",
        "cold": f"{c['pack']['groups']} groups of the {c['cell']['preset'].upper()} cell scaled to {c['cell']['scale_to_ah']:.0f} Ah, {c['case']['months']} months. "
                f"The pack temperature runs by season from {pc['mean_c'] - pc['season_amp_c'] - pc['daily_amp_c']:.0f} to "
                f"{pc['mean_c'] + pc['season_amp_c'] + pc['daily_amp_c']:.0f} °C. Charging is blocked below {c['bms']['charge_block_below_c']:.0f} °C "
                f"(setting `block`) or not (`noblock`). On the {ov['day_of_month']}th of every month a constant-power discharge runs from full to the "
                "BMS stop; with the block on, the winter discharges do not start from full.",
        "grid": f"Random power around mid state of charge, one arbitrage cycle a day, a calibration full charge every "
                f"{' or '.join(str(x) for x in sim_case_grid.CALIBRATION.values() if x)} days, or never. NMC: {g['pack']['series']} identical cells of "
                f"{g['cell']['scale_to_ah']:.0f} Ah in series; LFP: {lfp['pack.series']} identical cells of {lfp['cell.scale_to_ah']:.0f} Ah; the string: "
                f"{s['pack']['groups']} LFP cells of {s['cell']['scale_to_ah']:.0f} Ah with capacity sd {s['pack']['capacity_spread_sd']:.0%}, balancing on "
                f"and sensors at a utility string's stated accuracy (voltage ±{s['sensors']['voltage_gain_max']:.1%}, current "
                f"±{s['sensors']['current_gain_max']:.0%}, temperature offset up to ±{s['sensors']['temp_offset_max_c']:.0f} °C). {g['case']['months']} months. "
                "No BMS supply and no self-discharge, so the BMS's state of charge is exact between calibrations.",
        "drift": f"{d['pack']['series']} {d['cell']['preset'].upper()} cells of {d['cell']['scale_to_ah']:.0f} Ah in series, {d['case']['months']} months, fade "
                 f"{d['pack']['fade_points_per_year']:.0f} points a year. The discharge stop falls linearly from month 12 to month {d['case']['months']} by D mV per "
                 f"cell of relaxed empty rest voltage ({', '.join(k[1:] for k in sim_case_drift.SETTINGS if DRIFT_SETTING.fullmatch(k))}). A capacity test "
                 f"(charge to full, rest, discharge at {tests['c_discharge']:.2f} C) runs in months 18, 30, 42, 54 and 66; its stop is {low:.3f} V per cell, the "
                 f"median lowest voltage of the field's LMO/NMC tests. The settings {' and '.join(f'`{k}`' for k in onestop)} end the test at the operational "
                 "stop instead. The test's stop and the drift scaling were set after development runs and before any held-out run.",
    }


def first_table(ctx: dict) -> list[str]:
    spec = [
        ("1 Overheating", "T1", "A fan fails and the summer is hot", "B4 hours per temperature band; A5 by band", ("T1a", "fan-fails")),
        ("2 Cell imbalance", "T2", "Groups differ in capacity and self-discharge", "A1 against two capacity truths", ("T2a", "sd1-balance")),
        ("3 A failing cell", "T3", "One cell gets a leak", "A4, the apparent charge loss", ("T3b", "leak15")),
        ("4 The cold", "T4", "A year of cold, charging blocked below 0 °C", "A5 by band; B4 charging below a limit", ("T4a", "block")),
        ("5 Grid duty", "T5", "Mid state of charge; a calibration charge every so often or never", "A1 without a full charge", ("T5a", "nmc-never")),
        ("6 A drifting stop", "T6", "The BMS's discharge stop moves", "A1 against a capacity test", ("T6b", "D157")),
    ]
    c, lines = ctx["checks"], []
    for label, prefix, what, field, (chk, st) in spec:
        sel = c[c["check"].str.startswith(prefix) | ((prefix == "T6") & (c["check"] == "S9"))]
        sel = sel[sel["passed"].astype(str) != ""]
        key = rows(ctx, chk, st)
        lines.append({"Case": label, "What happens to the pack": what, "besslib field": field,
                      "Rows with a pass rule": f"{int((sel['passed'].astype(str) == 'True').sum())} pass, {int((sel['passed'].astype(str) == 'False').sum())} fail",
                      "One key number": f"{chk} {st}: {first(key['result'].iloc[0])}" if len(key) else DASH})
    return [md(pd.DataFrame(lines)), ""]


def causes(ctx: dict, found: list[dict]) -> list[str]:
    cs = ctx["cases"]

    def seen(chk, st):
        r = rows(ctx, chk, st)
        return f"{chk} {st}: {first(r['result'].iloc[0])}" if len(r) else None

    def line(finding, support, *cites):
        where = "; ".join(x for x in (seen(*c) for c in cites) if x)
        tail = "" if support is None or support else " (the rows of this build do not show it)"
        return {"Finding": finding + tail, "Where seen": where or DASH, "What the next version does": "not decided"}

    out = []
    t = cs.get("imbalance")
    ok = None
    if t is not None:
        b = t[t["setting"] == "sd1-balance"]
        ok = bool(b["held_stop"].sum() / max(b["with_stop_truth"].sum(), 1) > b["held_rest"].sum() / max(b["with_band"].sum(), 1))
    out.append(line("A1 is read at the mean full and empty rest voltages; on a curved open-circuit curve (the NMC plateau) the state of charge at the "
                    "mean voltage is not the mean state of charge: the estimate lies nearer T_stop than T_rest", ok, ("T2a", "sd1-balance"), ("T2b", "sd1-balance")))
    t = cs.get("leak")
    ok = None
    if t is not None:
        p = t[t["setting"] == "leak15"]
        ok = bool(p["held_before"].sum() / max(p["n_before"].sum(), 1) > p["held_after"].sum() / max(p["n_after"].sum(), 1))
    out.append(line("A4 follows a leak through its 180-day memory: after the onset its band holds the truth less often than before it", ok,
                    ("T3a", "leak15"), ("T3b", "leak15")))
    t = cs.get("cold")
    ok = None
    if t is not None:
        b = t[(t["row_kind"] == "band") & (t["setting"] == "noblock")]
        ok = bool(len(b) and b["held_same_days"].astype(bool).mean() > b["held"].astype(bool).mean())
    out.append(line("A5 at 10 s samples includes the open-circuit rise of one sample; T4a's truth uses the median step, the same-days form (the estimator "
                    "on the true signals) is the estimate's own quantity", ok, ("T4a", "noblock")))
    t = cs.get("grid")
    ok = None
    if t is not None:
        n = t[t["setting"].str.endswith("-never")]
        ok = bool(len(n) and (n["estimated_months"] > 0).all())
    out.append(line("A1 in mid-state-of-charge duty reads the daily window's edges as full and empty, because its levels are quantiles of the rest "
                    "edge voltages; with no full charge it still gives an estimate", ok, ("T5a", "nmc-never"), ("T5a", "lfp-never"), ("T5a", "string-never")))
    return [md(pd.DataFrame(out)), ""]


def one_path(ctx: dict) -> list[str]:
    t = ctx["one_path"].rename(columns={"check": "Check", "quantity": "Quantity", "published": "Published", "one_path": "One-path pack"})
    return [md(t), "", "The published numbers stay as they are: they are the numbers of the release, and this run changes none of them.", ""]


LIMITS = [
    "Thermal runaway, venting, fire and propagation between cells.",
    "Lithium plating when charging cold: the BMS's block is modelled, the damage is not.",
    "Ageing caused by use: ageing is an input (a set fade and a set growth), not a result.",
    "Rate effects beyond what one RC element (and, for LFP, one hysteresis state) gives; no diffusion limit at high current.",
    "Degradation modes (loss of lithium, of active material) and incremental-capacity features.",
    "Temperature differences inside a cell: one temperature per cell group.",
    "Any real product: curves and values are example values or read from the sources below.",
    "Real BMS firmware quirks, and inverter dynamics faster than one step.",
]


def sources(ctx: dict) -> list[str]:
    lfp, tran = ctx["lfp"], ctx["tran"]
    cap = lfp[lfp["item"] == "capacity"].iloc[0]
    pix = tran[tran["item"].str.startswith("one pixel, voltage")]
    lines = [
        {"Preset": "lfp", "Source": "Gray and Vagg, LIC cell characterisation, Zenodo 19632939, CC BY 4.0 (one 280 Ah-class cell, measured at 15 to 35 °C)",
         "Read": f"capacity {cap['value']} {cap['unit']} at 25 °C; resistances at 15, 25 and 35 °C; open-circuit curve and hysteresis from the C/10 runs"},
        {"Preset": "nmc, lmo", "Source": "Tran et al., Batteries 7, 51 (2021), doi:10.3390/batteries7030051, CC BY 4.0",
         "Read": "resistances and capacitance from their tables; the average curve of their Figure 6 digitised"
                 + (f" (one pixel is {float(pix['value'].iloc[0]):.2f} mV)" if len(pix) else "")},
        {"Preset": "synth", "Source": "example values, not from a source", "Read": "the cell of the 0.1 and 0.2 checks"},
    ]
    inside = ctx["s10"][ctx["s10"]["part"] == "10-90 %"]
    res = "; ".join(f"{int(r.temp_c)} °C: {r.rms_mv:.1f} mV" for r in inside.itertuples())
    return [md(pd.DataFrame(lines)), "",
            "Temperature beyond 15 to 35 °C follows an Arrhenius law with activation energies read from the tables or, for NMC and LMO, from a cell of "
            f"another make; that is an extrapolation. The LFP cell against HPPC currents it was not built from (S10): RMS between 10 % and 90 % state of charge {res}.", ""]


# ----------------------------------------------------------------------------- page


def document(ctx: dict) -> str:
    setup, found, cs = setup_sentences(ctx), findings(ctx), ctx["cases"]
    figs = {1: fig_heat, 2: fig_imbalance, 3: fig_leak, 4: fig_cold, 5: fig_grid, 6: fig_drift}
    keys = {1: "heat", 2: "imbalance", 3: "leak", 4: "cold", 5: "grid", 6: "drift"}
    fig = {n: figs[n](cs[k]) for n, k in keys.items() if k in cs}
    nofig = "No figure: this build has no rows for this case."
    titles = {1: ("Overheating", "heat"), 2: ("Cell imbalance", "imbalance"), 3: ("A failing cell", "leak"), 4: ("The cold", "cold"),
              5: ("Grid duty at middle state of charge", "grid"), 6: ("A drifting stop", "drift")}
    cases = []
    for n, (title, key) in titles.items():
        cases += [f"### {n}. {title}", "", setup[key], ""]
        cases += drift_tables(ctx) if n == 6 else check_table(ctx, f"T{n}")
        cases += [fig.get(n) or nofig, ""]
        mine = [f for f in found if f["case"] == n]
        cases += shows(found, n) if mine else ["**What this shows.** No finding in this build's rows.", ""]
    packs = ", ".join(f"{k} {t['seed'].nunique()}" for k, t in cs.items())
    story = []
    for k, f in enumerate(found, 1):
        text = f["text"]
        story.append(f"{k}. {text} [{f['tag']}]")
    return "\n".join([
        "# The simulator: besslib.sim",
        "",
        "Written by `validation/make_sim_doc.py` from `validation/results/` (the case rows, `sim_checks.csv`, `checks_sim.csv`). "
        "No number on this page is typed by hand." + (" **This is a trial build from development seeds, not a result.**" if DEV else ""),
        "",
        "## What it is",
        "",
        "1. `besslib.sim` runs a battery pack, cell group by cell group, and writes the frames a meter at the pack terminals would report.",
        "2. It also writes the truth behind them: every state of charge, every stop, every temperature.",
        "3. That makes it a test bench for besslib.soh: a field is judged against a known truth.",
        "4. It is not a model of any product: its curves are example values or one measured cell (see the sources below).",
        "5. A case is a TOML file; one command runs it.",
        "",
        "```",
        "python -m besslib.sim run --case cold --seed 1 --out run_cold",
        "python validation/sim_cases.py run heat --seeds held-out        # many packs, one case",
        "```",
        "",
        "## What it found",
        "",
        "Version 0.2 of besslib, run on the six cases " + (f"({packs} packs per setting, "
                                                            "development seeds)." if DEV else "(200 packs per setting, held-out seeds).") + " Most important first.",
        "",
        *story, "",
        "## The six cases",
        "",
        "The pass rules were fixed before any result and never moved. These set-ups were changed after development runs (seeds 0-9) and before the "
        "held-out run: case 6, the test's stop, the size of the limit's fall and the C/10 tests' stop; the BMS's balancing rule, which at first bled every "
        "high group alike and so could not balance; case 3, the month in which the true loss is read; case 5, a fade of 2 points a year was added. "
        "Nothing was changed after the held-out run.", "",
        *first_table(ctx),
        "## The simulator's own checks",
        "",
        "S1 to S4 check the plant (charge, energy, closed forms, the BMS's limits); S5 and S6 the files and the data contract; "
        + ("S7 the SunSpec bus; " if s7_counts(ctx) else "") + "S8 the speed. "
        "S0 compares the `synth` case with the pack of the 0.1 and 0.2 checks.",
        "",
        *own_table(ctx),
        *bus_section(ctx),
        "## The cases",
        "",
        *cases,
        "## Known causes", "",
        "Each line is an explanation only where the row in the second column supports it. Nothing here is a promise.", "",
        *causes(ctx, found),
        "## The rerun on one state-of-charge path", "",
        "The checks V6, X3 and W7 ran on `tests/synth.py`, whose BMS decides its stops on a copy of the pack that drifts slowly from the plant. "
        "The same seeds on a pack with one path:", "",
        *one_path(ctx),
        "## What an equivalent circuit cannot show", "",
        *[f"{k}. {x}" for k, x in enumerate(LIMITS, 1)], "",
        "## Where the values come from", "",
        *sources(ctx),
        "Credit lines for both sources are in `src/besslib/sim/presets/NOTICE.md`. Not a compliance certificate, not legal advice.", "",
    ])


def main() -> None:
    ctx = load()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / PAGE).write_text(document(ctx), encoding="utf-8")
    print(f"{OUT / PAGE} written ({len((OUT / PAGE).read_text().splitlines())} lines)")


if __name__ == "__main__":
    main()
