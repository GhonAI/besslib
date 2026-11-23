"""Phase 3: write docs/03_annex_vii.md and its figures from the result files.

Every number on the page comes from ``validation/results/`` or from ``RecordConfig()`` and
``CapacityConfig()``; none is typed. The page works with whatever results exist: a system
without a record is shown as not run, and every pass, fail and not-run word is the one in
``checks_phase3.csv``.

Reads ``validation/results/phase3/system_XX_{record.json,yearly.csv}``,
``checks_phase3.csv``, ``synthetic_phase3.csv``, ``onset_resistance.csv`` (held-out, when
it exists), ``onset_resistance_development.csv``, ``w6_loop_ratios.csv`` and
``tuning_resistance.csv``. It refuses to build when any row of these comes from settings
other than the frozen capacity and record settings.

Usage:
    python validation/make_annex_vii_doc.py
"""

from __future__ import annotations

import json
import re
import sys
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
from besslib.soh import efficiency as eff
from besslib.soh.charge_loss import HOURS_PER_MONTH
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

from check_annex_vii import ONSET_MIN_REFERENCES, w4_pairs
from make_capacity_doc import DASH, ids_text, num, plural, sid_text, threshold
from make_reference_doc import AXIS, COLOUR, INK, INK2, MUTED, ORDER, SURFACE, table
from onset_resistance import AFTER, BEFORE
from synthetic_phase3 import packs_for
from tune_capacity import DEVELOPMENT
from tune_resistance import MAD_TO_SD, MIN_SHARE, WINDOW

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
PHASE3 = RESULTS / "phase3"
DOCS = ROOT / "docs"
IMG = DOCS / "img"
PAGE = "03_annex_vii.md"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
RECORD_SYSTEM = 5  # the system whose full record is shown: held-out, named before any result
FROZEN = {"capacity": config_hash(CapacityConfig()), "record": config_hash(RecordConfig())}  # read from the settings
V0_1 = {"capacity": config_hash(CapacityConfig().v0_1()), "record": config_hash(RecordConfig().v0_1())}  # release 0.1.0
NOT_RUN = "not run"
NOT_POSSIBLE_CHECK = "not possible"  # a check whose rule says so when its references are too few (W3)
NOT_POSSIBLE = "not possible from this data: "
ESTIMATED = "estimated"
PNG = {"Software": None}  # no version stamp in the figures: two runs give the same bytes
BAND = f"{RecordConfig().level:.0%}".replace("%", " %")

#: How each field's value is shown: scale, unit on the page, format.
SHOW = {
    "A1": (1.0, "Ah", "{:.2f}"),
    "A2": (100.0, "% of the first day", "{:.1f}"),
    "A3": (100.0, "%", "{:.2f}"),
    "A4": (1.0, "mA", "{:.1f}"),
    "A5": (1000.0, "mΩ", "{:.2f}"),
    "B2": (1.0, "kWh", "{:.0f}"),
    "B3": (1.0, "Ah", "{:.0f}"),
    "B4": (1.0, "events", "{:.0f}"),
    "B5": (1.0, "cycles", "{:.0f}"),
}

#: What the event count of each field counts.
EVENTS = {
    "A1": "pairs of full and empty rests in memory (decayed count)",
    "A2": "days with current steps in the growth fit",
    "A3": "closed full-to-full loops in memory (decayed count)",
    "A4": "pairs of full and empty rests in memory (decayed count)",
    "A5": "days with current steps in the band, in memory (decayed count)",
    "B1": "none",
    "B2": "days with data",
    "B3": "days with data",
    "B4": "discharges that reached the system's own end-of-discharge level",
    "B5": "days with data",
}

#: The definitions of Regulation (EU) 2023/1542, Annex IV, quoted.
ANNEX_IV = {
    "A2": "(4) \"'Power fade' means the decrease over time and upon usage in the amount of power that a battery can "
          "deliver at the rated voltage.\"",
    "A3": "(6) \"'Energy round trip efficiency' means the ratio of the net energy delivered by a battery during a "
          "discharge test to the total energy required to restore the initial state of charge by a standard charge.\"",
    "A5": "(5) \"'Internal resistance' means the opposition to the flow of current within a cell or a battery under "
          "reference conditions, that is, the sum of electronic resistance and ionic resistance to the contribution to "
          "total effective resistance including inductive/capacitive properties.\"",
}  # fmt: skip
REGULATION = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02023R1542-20250731"

#: The limits of each field, in words (the numbers behind them are on this page or in the record).
LIMITS = {
    "A1": "The capacity estimator of the capacity page: no value in the first year (the full and empty levels need a "
          "year of history). It is the capacity between the mean full and empty rest voltages of the memory time, "
          "which the record gives. The band does not hold the drift of the BMS stops against those voltages "
          "([version 0.2 page](04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with)).",
    "A2": "Derived from the 1-second resistance, not measured: no power test. With an ohmic model the power at a fixed "
          "voltage limit goes as 1 ÷ R. The resistance grows with the length of the pulse (Barai 2018; "
          "Schweiger 2010), so the power of a pulse longer than one sample is lower than this share suggests. Watts need the pack's voltage limits, which the dataset "
          "does not give. Since version 0.2 it is `estimated` only when A5 is `estimated` at the same date.",
    "A3": "DC at the battery terminals, not the AC efficiency of the plant: Schimpe et al. (2018) give AC system "
          "figures well below the battery's, and are cited for that scale only. It depends on the use. The energy in "
          "also restores the charge the meter does not see. A sensor gain error cancels in the ratio; a zero offset "
          "does not, and is not in the band. No value in the first year.",
    "A4": "\"Apparent charge loss\": an upper bound for self-discharge. It holds cell self-discharge, BMS supply, "
          "balancing and the current sensor's zero offset, so it is mostly not cell self-discharge.",
    "A5": "\"1-second resistance\": at one-second sampling it holds the ohmic part and a part of the charge-transfer "
          "resistance; it is not the ohmic resistance of an impedance spectrum. It is an average over the states of "
          f"charge the system visits, per {RecordConfig().temp_band_c:g}-degree band of pack temperature, with no "
          "temperature model. Records with "
          "another sampling interval are not comparable; the record prints the interval.",
    "B1": "The date of manufacture is not in the dataset. The date of putting into service is the dataset's "
          "installation date, passed through.",
    "B2": "Since the first day of data, not a lifetime total: the data start after installation. Gaps are not filled; "
          "the hours missing are printed. The band is the sensors' gain error only.",
    "B3": "As B2.",
    "B4": "The temperature is the pack housing, not a cell. The cell voltage is the pack voltage ÷ cells in series, an "
          "average: one cell can be lower. Without limits (none are given here) the counts against limits are "
          "`not possible from this data: no limits given`; the observed facts are shown.",
    "B5": "As B2; cycles = capacity throughput ÷ (2 × reference capacity).",
}  # fmt: skip

#: What each record setting does (words only; the values come from ``RecordConfig()``).
MEANING = {
    "min_step_c_rate": "current step: smallest change between two samples (x C); tuned, then frozen",
    "steady_s": "current step: steady time before it, s; tuned, then frozen",
    "steady_tolerance": "current step: the current stays within this share of the step while steady",
    "unrefreshed_max_share": "a band-day is left out when more of its steps than this show no voltage change",
    "temp_band_c": "width of a pack-temperature band, degrees C",
    "resistance_min_days": "resistance: fewest days in memory in the band (decayed count)",
    "growth_min_span_years": "resistance growth: shortest span of days, years",
    "efficiency_min_loops": "efficiency: fewest closed loops in memory (decayed count)",
    "tau_days": "memory time of the forgetting, days (the capacity estimator's, not tuned again)",
    "level": "band level",
    "current_accuracy": "current sensor accuracy, read as a rectangular half-width",
    "voltage_accuracy": "voltage sensor accuracy, read as a rectangular half-width",
    "charge_loss_min_years": "apparent charge loss trend: fewest year-end values",
    "a2_needs_a5": "power capability (A2) only where the resistance (A5) is estimated at the same date",
}


# ----------------------------------------------------------------------------- data


def _refuse(path: Path, found) -> None:
    name = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    raise SystemExit(
        f"{name}: results come from settings {sorted(map(str, found))}, not capacity "
        f"{FROZEN['capacity']} and record {FROZEN['record']}: rebuild them first"
    )


def _record_hashes(value) -> set:
    """Every value of a key ending in ``config_hash`` anywhere in a record."""
    found = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k.endswith("config_hash"):
                found.add(str(v))
            else:
                found |= _record_hashes(v)
    elif isinstance(value, list):
        for v in value:
            found |= _record_hashes(v)
    return found


def _check_rows(path: Path, frame: pd.DataFrame, record_column: str = "record_config_hash") -> None:
    found = set(frame["capacity_config_hash"].astype(str)) | set(frame[record_column].astype(str))
    if frame.empty:
        return
    if set(frame["capacity_config_hash"].astype(str)) != {FROZEN["capacity"]} or set(
        frame[record_column].astype(str)
    ) != {FROZEN["record"]}:
        _refuse(path, found)


def load() -> dict:
    hashes = {"capacity": config_hash(CapacityConfig()), "record": config_hash(RecordConfig())}
    if hashes != FROZEN:
        raise SystemExit(f"the settings hash to {hashes}, not the frozen {FROZEN}: this page is built from the frozen settings only")

    records, yearly = {}, {}
    for sid in figgener.SYSTEM_IDS:
        path = PHASE3 / f"system_{sid:02d}_record.json"
        if path.exists():
            r = json.loads(path.read_text())
            found = _record_hashes(r)
            if r["capacity_config_hash"] != FROZEN["capacity"] or found - {FROZEN["capacity"], FROZEN["record"]} or (
                r["record_config_hash"] != FROZEN["record"]
            ):
                _refuse(path, found)
            records[sid] = r
        path = PHASE3 / f"system_{sid:02d}_yearly.csv"
        if path.exists():
            t = pd.read_csv(path, parse_dates=["date"], dtype={"capacity_config_hash": str, "record_config_hash": str})
            _check_rows(path, t)
            t.insert(0, "system_id", sid)
            yearly[sid] = t

    def read(name: str, **kw) -> pd.DataFrame | None:
        path = RESULTS / name
        if not path.exists():
            return None
        t = pd.read_csv(path, dtype={"capacity_config_hash": str, "record_config_hash": str}, **kw)
        _check_rows(path, t)
        return t

    checks = read("checks_phase3.csv", keep_default_na=False)
    if checks is None:
        raise SystemExit("validation/results/checks_phase3.csv is missing: run check_annex_vii.py first")
    synth = read("synthetic_phase3.csv")
    onset = [read(n, parse_dates=["onset"]) for n in ("onset_resistance_development.csv", "onset_resistance.csv")]
    onset = pd.concat([t for t in onset if t is not None], ignore_index=True) if any(t is not None for t in onset) else None
    if onset is not None:
        onset["set"] = onset["system_id"].map(set_of)
    w6 = read("w6_loop_ratios.csv", parse_dates=["date"])

    path = RESULTS / "tuning_resistance.csv"
    tuning = pd.read_csv(path, dtype={"capacity_config_hash": str, "record_config_hash": str})
    selected = tuning[tuning["selected"].astype(bool)]
    # The current-step filter was tuned in version 0.1 and kept frozen: this one file may carry the 0.1 hashes
    # (read from the settings, not typed), as long as the chosen row holds today's two step settings.
    cfg = RecordConfig()
    accepted = [FROZEN, V0_1]
    same_steps = len(selected) == 1 and np.isclose(selected["min_step_c_rate"].iloc[0], cfg.min_step_c_rate) and (
        np.isclose(selected["steady_s"].iloc[0], cfg.steady_s))
    if not same_steps or not any(
        set(tuning["capacity_config_hash"]) == {h["capacity"]} and list(selected["record_config_hash"]) == [h["record"]]
        for h in accepted
    ):
        _refuse(path, set(tuning["capacity_config_hash"]) | set(selected["record_config_hash"]))

    return {
        "hashes": hashes,
        "specs": {sid: figgener.pack_spec(sid) for sid in figgener.SYSTEM_IDS},
        "records": records,
        "yearly": yearly,
        "checks": checks,
        "synth": synth,
        "onset": onset,
        "w6": w6,
        "tuning": tuning,
    }


def set_of(sid: int) -> str:
    return "development" if sid in DEVELOPMENT else "held-out"


def chemistry_of(ctx: dict, sid: int) -> str:
    return ctx["specs"][sid].chemistry


def fields(record: dict) -> dict:
    return {f["code"]: f for f in record["fields"]}


def headline_band(ctx: dict) -> float | None:
    for r in ctx["records"].values():
        return float(fields(r)["A5"]["details"]["headline_band_c"])
    return None


def yearly_all(ctx: dict) -> pd.DataFrame:
    frames = [ctx["yearly"][sid] for sid in sorted(ctx["yearly"])]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def series(ctx: dict, sid: int, quantity: str) -> pd.DataFrame:
    """One quantity of one system over its year ends; of two rows on the same day only the later is kept."""
    t = ctx["yearly"][sid]
    t = t[t["quantity"] == quantity].sort_values("date")
    return t.groupby(t["date"].dt.normalize(), sort=True).tail(1).reset_index(drop=True)


# ----------------------------------------------------------------------------- formatting


def shown(code: str, value) -> float:
    return float(value) * SHOW[code][0] if value is not None and np.isfinite(float(value)) else np.nan


def value_band(code: str, f: dict) -> str:
    scale, _, fmt = SHOW[code]
    if f["status"] != ESTIMATED or f["value"] is None:
        return DASH
    v, lo, hi = (shown(code, f[k]) for k in ("value", "low", "high"))
    if lo == v == hi:
        return fmt.format(v)
    return f"{fmt.format(v)} [{fmt.format(lo)}, {fmt.format(hi)}]"


def events_text(value) -> str:
    if value is None or not np.isfinite(float(value)):
        return DASH
    value = float(value)
    return f"{value:.0f}" if value == round(value) else f"{value:.1f}"


class Reasons:
    """Short letters for the reasons of ``not possible``, listed once below a table."""

    def __init__(self):
        self.texts: list[str] = []

    def mark(self, status: str) -> str:
        if status == NOT_RUN:
            return NOT_RUN
        if status not in self.texts:
            self.texts.append(status)
        return f"not possible ({chr(ord('a') + self.texts.index(status))})"

    def legend(self) -> list[str]:
        return [f"- ({chr(ord('a') + k)}) `{text}`" for k, text in enumerate(self.texts)]


def cell(code: str, f: dict, reasons: Reasons) -> str:
    return value_band(code, f) if f["status"] == ESTIMATED else reasons.mark(f["status"])


def check_key(row: pd.Series) -> str:
    if row["check"] == "W2":
        return "W2 growth" if "growth" in row["scope"] else "W2 value"
    return row["check"]


def bold_result(result: str) -> str:
    return {"fail": "**fail**", "": "information"}.get(result, result)


# ----------------------------------------------------------------------------- figures


def _finish(fig, name: str, dpi: int = 150) -> str:
    fig.savefig(IMG / name, dpi=dpi, metadata=PNG)
    plt.close(fig)
    return name


def _set_handles() -> list:
    return [
        Line2D([], [], color=INK2, lw=1.6, marker="o", ms=6, label="held-out system"),
        Line2D([], [], color=INK2, lw=1.2, linestyle=(0, (4, 2)), marker="o", ms=6, mfc=SURFACE, mew=1.5,
               label="development system"),
    ]  # fmt: skip


def _time_panels(ctx: dict, title: str, ylabel: str, draw) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.4))
    for ax, chemistry in zip(axes, ORDER):
        ids = [sid for sid in sorted(ctx["yearly"]) if chemistry_of(ctx, sid) == chemistry]
        for sid in ids:
            draw(ax, sid, COLOUR[chemistry], sid in DEVELOPMENT)
        ax.set_title(chemistry, color=INK)
        ax.xaxis.set_major_locator(mdates.YearLocator(2, month=12, day=31))  # a year end sits on its own year
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.grid(axis="x", visible=False)
        ax.set_xlabel("Year end (31 December, or the last sample)", fontsize=9)
        if not ids:
            ax.text(0.5, 0.5, "no record yet", transform=ax.transAxes, ha="center", va="center", color=INK2)
    axes[0].set_ylabel(ylabel)
    fig.suptitle(title, x=0.01, ha="left", fontsize=11.5, fontweight="bold", color=INK)
    return fig


def _label_end(ax, x, y, sid: int, colour: str) -> None:
    """Remember an end-of-line label; ``_place_end_labels`` draws them once the layout is fixed."""
    if not hasattr(ax, "_end_labels"):
        ax._end_labels = []
    ax._end_labels.append((x, y, sid_text(sid), colour))


_LABEL_GAP_PT = 9.0  # smallest vertical distance between two end labels of a panel (font size 7.5 pt)


def _place_end_labels(fig, name: str) -> None:
    """Draw the end labels of every panel, pushed apart vertically to a minimum gap in
    display units, in their order, with a thin leader line where a label moved. Check R8:
    afterwards no two labels of a panel overlap, or the script stops."""
    from matplotlib.text import Text

    renderer = fig.canvas.get_renderer()
    pt = fig.dpi / 72.0
    overlaps = 0
    for ax in fig.axes:
        items = getattr(ax, "_end_labels", [])
        if not items:
            continue
        probes = [
            ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=7.5)
            for x, y, text, _ in items
        ]
        fig.canvas.draw()
        boxes = [Text.get_window_extent(n, renderer) for n in probes]
        for n in probes:
            n.remove()
        centre = np.array([b.y0 + b.height / 2.0 for b in boxes])
        home = centre.copy()
        order = sorted(range(len(items)), key=lambda k: centre[k])
        gap = max(_LABEL_GAP_PT * pt, max(b.height for b in boxes))
        for _ in range(500):  # relax: labels that share x and are closer than the gap move apart, half each
            moved = False
            for pos, i in enumerate(order):
                for j in order[pos + 1:]:
                    if boxes[i].x1 < boxes[j].x0 or boxes[j].x1 < boxes[i].x0:
                        continue  # side by side: no clash
                    short = gap - (centre[j] - centre[i])
                    if short > 1e-6:
                        centre[i] -= short / 2.0
                        centre[j] += short / 2.0
                        moved = True
            if not moved:
                break
        notes = []
        for k, (x, y, text, colour) in enumerate(items):
            shift = (centre[k] - home[k]) / pt
            leader = None
            if abs(shift) > 0.5:
                leader = dict(arrowstyle="-", lw=0.6, color=colour, shrinkA=0, shrinkB=1)
            notes.append(ax.annotate(text, (x, y), xytext=(6, shift), textcoords="offset points",
                                     va="center", fontsize=7.5, color=INK2, annotation_clip=False,
                                     arrowprops=leader))  # fmt: skip
        fig.canvas.draw()
        boxes = [Text.get_window_extent(n, renderer) for n in notes]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if boxes[i].overlaps(boxes[j]):
                    overlaps += 1
    print(f"{name}: end-label overlaps: {overlaps}")
    if overlaps:
        sys.exit(f"R8: {overlaps} overlapping end labels in {name}; not written")


def figure_resistance(ctx: dict, band_c: float) -> str:
    quantity = f"resistance_ohm@{band_c:.1f}"

    def draw(ax, sid, colour, dev):
        s = series(ctx, sid, quantity)
        ok = s["status"] == ESTIMATED
        if not ok.any():
            return
        v = np.where(ok, s["value"] * 1000.0, np.nan)
        lo, hi = np.where(ok, s["low"] * 1000.0, np.nan), np.where(ok, s["high"] * 1000.0, np.nan)
        ax.plot(s["date"], v, color=colour, lw=1.4 if not dev else 1.1, linestyle=(0, (4, 2)) if dev else "-")
        ax.errorbar(s["date"], v, yerr=[v - lo, hi - v], fmt="o", ms=5, color=colour, mfc=SURFACE if dev else colour,
                    mew=1.4, elinewidth=1, capsize=0)  # fmt: skip
        last = np.flatnonzero(ok.to_numpy())[-1]
        _label_end(ax, s["date"].iloc[last], v[last], sid, colour)

    fig = _time_panels(ctx, f"1-second resistance in the {band_c:.0f} °C band of pack temperature, at every year end, "
                       f"with its {BAND} band", "Resistance of the pack, mΩ", draw)  # fmt: skip
    fig.legend(handles=_set_handles(), loc="lower center", ncol=2, fontsize=8)  # noqa
    fig.text(0.99, 0.01, "Each panel has its own scale. Gaps: year ends without an estimate.", ha="right",
             fontsize=7.5, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    _place_end_labels(fig, "03_resistance.png")
    return _finish(fig, "03_resistance.png")


def figure_efficiency(ctx: dict) -> str:
    w6 = ctx["w6"]

    def draw(ax, sid, colour, dev):
        s = series(ctx, sid, "efficiency_full_loops")
        ratio = pd.Series(np.nan, index=s.index)
        if w6 is not None:
            r = w6[w6["system_id"] == sid]
            lookup = dict(zip(r["date"], r["full"]))
            ratio = s["date"].map(lookup).astype(float)
        ok = (s["status"] == ESTIMATED).to_numpy()
        v = np.where(ok, s["value"], ratio) * 100.0
        if not np.isfinite(v).any():
            return
        keep = np.isfinite(v)
        ax.plot(s["date"][keep], v[keep], color=colour, lw=1.1, linestyle=(0, (4, 2)) if dev else "-", zorder=1)
        lo, hi = s["low"].to_numpy() * 100.0, s["high"].to_numpy() * 100.0
        ax.errorbar(s["date"][ok], v[ok], yerr=[v[ok] - lo[ok], hi[ok] - v[ok]], fmt="o", ms=5.5, color=colour,
                    mfc=SURFACE if dev else colour, mew=1.4, elinewidth=1, capsize=0, zorder=3)  # fmt: skip
        nob = keep & ~ok
        ax.plot(s["date"][nob], v[nob], linestyle="none", marker="D", ms=4.5, mfc=SURFACE, mec=colour, mew=1.2, zorder=2)
        last = np.flatnonzero(keep)[-1]
        _label_end(ax, s["date"].iloc[last], v[last], sid, colour)

    fig = _time_panels(ctx, f"Round-trip efficiency (DC) over full-to-full loops, at every year end", "Energy out ÷ energy in, %", draw)
    handles = _set_handles() + [
        Line2D([], [], linestyle="none", marker="o", ms=6, color=INK2, label=f"record value with its {BAND} band"),
        Line2D([], [], linestyle="none", marker="D", ms=4.5, mfc=SURFACE, mec=INK2, mew=1.2,
               label="loop ratio without a band (check W6 table)"),
    ]  # fmt: skip
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8)
    fig.text(0.99, 0.01, "Each panel has its own scale.", ha="right", fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.07, 1, 0.97))
    _place_end_labels(fig, "03_efficiency.png")
    return _finish(fig, "03_efficiency.png")


def figure_onset(ctx: dict, rule: float) -> str | None:
    t = ctx["onset"]
    if t is None:
        return None
    both = t[(t["onset_status"] == ESTIMATED) & (t["operating_status"] == ESTIMATED)]
    fig, ax = plt.subplots(figsize=(6.6, 6.0))
    for chemistry in ORDER:
        for label in ("held-out", "development"):
            part = both[(both["chemistry"] == chemistry) & (both["set"] == label)]
            ax.plot(part["onset_ohm"] * 1000.0, part["operating_ohm"] * 1000.0, linestyle="none", marker="o", ms=7,
                    color=COLOUR[chemistry], mfc=COLOUR[chemistry] if label == "held-out" else SURFACE, mew=1.6)  # fmt: skip
    if len(both):
        values = np.concatenate([both["onset_ohm"], both["operating_ohm"]]) * 1000.0
        lo, hi = values.min() / 1.5, values.max() * 1.5
    else:
        lo, hi = 1.0, 1000.0
    x = np.geomspace(lo, hi, 50)
    ax.plot(x, x, color=INK, lw=1.2)
    for f in (1 - rule / 100.0, 1 + rule / 100.0):
        ax.plot(x, f * x, color=MUTED, lw=1, linestyle=(0, (3, 3)))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal")
    ax.set_xlabel("Reference: first minute of the capacity test, mΩ")
    ax.set_ylabel("From operation before the test (tests masked), mΩ")
    handles = [Patch(color=COLOUR[c], label=c) for c in ORDER] + [
        Line2D([], [], linestyle="none", marker="o", ms=7, color=INK2, label="held-out test"),
        Line2D([], [], linestyle="none", marker="o", ms=7, color=INK2, mfc=SURFACE, mew=1.6, label="development test"),
        Line2D([], [], color=INK, lw=1.2, label="equal"),
        Line2D([], [], color=MUTED, lw=1, linestyle=(0, (3, 3)), label=f"±{rule:g} %"),
    ]
    ax.legend(handles=handles, loc="upper left", fontsize=8)
    ax.set_title(f"Check W3: 1-second resistance, {len(both)} tests with both values", color=INK)
    fig.tight_layout()
    return _finish(fig, "03_onset.png")


def _strip(ctx: dict, frame: pd.DataFrame, column: str, ylabel: str, title: str, zone: float | None, name: str) -> str:
    ids = sorted(frame["system_id"].unique())
    fig, ax = plt.subplots(figsize=(11, 4.2))
    if zone is not None:
        ax.axhspan(-zone, zone, color=AXIS, alpha=0.35, lw=0, zorder=0)
    ax.axhline(0.0, color=INK2, lw=1, zorder=1)
    for k, sid in enumerate(ids):
        part = frame[frame["system_id"] == sid].sort_values([c for c in ("date", "cold_c") if c in frame])
        offsets = np.linspace(-0.3, 0.3, len(part)) if len(part) > 1 else np.zeros(1)
        colour = COLOUR[chemistry_of(ctx, sid)]
        dev = sid in DEVELOPMENT
        ax.plot(k + offsets, part[column], linestyle="none", marker="o", ms=6, color=colour,
                mfc=SURFACE if dev else colour, mew=1.4, zorder=3)  # fmt: skip
    ax.set_xticks(range(len(ids)), [sid_text(i) for i in ids])
    ax.set_xlim(-0.7, max(len(ids), 1) - 0.3)
    ax.set_xlabel("System")
    ax.set_ylabel(ylabel)
    ax.grid(axis="x", visible=False)
    handles = [Patch(color=COLOUR[c], label=c) for c in ORDER] + [
        Line2D([], [], linestyle="none", marker="o", ms=6, color=INK2, label="held-out"),
        Line2D([], [], linestyle="none", marker="o", ms=6, color=INK2, mfc=SURFACE, mew=1.4, label="development"),
    ]
    if zone is not None:
        handles.append(Patch(color=AXIS, alpha=0.6, label=f"within {zone:g} point"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=len(handles), fontsize=8)
    ax.set_title(title, color=INK)
    fig.tight_layout()
    return _finish(fig, name)


def figure_w4(ctx: dict, pairs: pd.DataFrame) -> str | None:
    if pairs.empty:
        return None
    p = pairs.assign(diff=100.0 * (pairs["cold_ohm"] - pairs["warm_ohm"]) / pairs["warm_ohm"])
    return _strip(ctx, p, "diff", "Colder band minus warmer band, % of the warmer",
                  f"Check W4: neighbouring {RecordConfig().temp_band_c:g}-degree bands, same system and year end ({len(p)} pairs; above zero: the "
                  "colder reads higher)", None, "03_temperature.png")  # fmt: skip


def figure_w6(ctx: dict, zone: float) -> str | None:
    w6 = ctx["w6"]
    if w6 is None or w6.empty:
        return None
    return _strip(ctx, w6, "difference_points", "Full-to-full minus empty-to-empty ratio, points",
                  f"Check W6: the two loop kinds at the same year end ({len(w6)} system-years)", zone, "03_loops.png")


# ----------------------------------------------------------------------------- page parts


def verdict_block(ctx: dict) -> list[str]:
    checks = ctx["checks"]
    judged = checks[checks["result"].isin(["pass", "fail", NOT_RUN, NOT_POSSIBLE_CHECK])].copy()
    judged["key"] = judged.apply(check_key, axis=1)
    order = list(dict.fromkeys(judged["key"]))
    word = {}
    for key in order:
        results = set(judged.loc[judged["key"] == key, "result"])
        word[key] = ("fail" if "fail" in results else NOT_POSSIBLE_CHECK if NOT_POSSIBLE_CHECK in results
                     else NOT_RUN if NOT_RUN in results else "pass")
    held = [sid for sid in HELD_OUT if sid in ctx["records"]]
    lines = [
        f"- **Configuration:** capacity `{ctx['hashes']['capacity']}` (unchanged from the capacity page), record "
        f"`{ctx['hashes']['record']}`, frozen on the development systems {ids_text(DEVELOPMENT)} and on synthetic packs "
        f"before the held-out systems were run at them. They had been run once before, at the release 0.1.0 settings "
        f"(capacity `{V0_1['capacity']}`, record `{V0_1['record']}`).",
        f"- **Held-out records:** {len(held)} of {len(HELD_OUT)}"
        + (f" (not run: {ids_text([s for s in HELD_OUT if s not in held])})." if len(held) < len(HELD_OUT) else "."),
        f"- **Passed:** {', '.join(k for k in order if word[k] == 'pass') or 'none'}. "
        f"**Failed:** {', '.join(k for k in order if word[k] == 'fail') or 'none'}. "
        f"**Not possible:** {', '.join(k for k in order if word[k] == NOT_POSSIBLE_CHECK) or 'none'}. "
        f"**Not run:** {', '.join(k for k in order if word[k] == NOT_RUN) or 'none'}.",
    ]
    for _, row in judged[judged["result"] == "fail"].iterrows():
        lines.append(f"  - {row['key']} failed ({row['scope']}): {row['value']} (rule {row['pass_if']}).")
        lines += [f"    {text}" for text in failure_note(ctx, row)]
    for _, row in judged[judged["result"] == NOT_POSSIBLE_CHECK].iterrows():
        lines.append(f"  - {row['key']} not possible ({row['scope']}): {row['value']} (rule {row['pass_if']}). "
                     "The rule, fixed before the held-out run, publishes this case as `not possible`. In version 0.1 "
                     "the check script first wrote it as `fail`, and that label was corrected after that run, with no "
                     "result changed.")
    for _, row in judged[judged["result"] == NOT_RUN].iterrows():
        lines.append(f"  - {row['key']} not run ({row['scope']}): {row['value']}.")
    return lines


def failure_note(ctx: dict, row: pd.Series) -> list[str]:
    """Numbers behind a failure, where the result files hold more than the check row."""
    s = ctx["synth"]
    if row["check"] == "W7" and s is not None and "w7_error_ma" in s:
        err, truth = s["w7_error_ma"].dropna(), s["w7_truth_ma"].dropna()
        above = int((err > 0).sum())
        return [
            f"The estimate reads a mean {err.mean():+.2f} mA (standard deviation {err.std():.2f}) against a hidden drain of "
            f"{truth.median():.0f} mA; {above} of {len(err)} packs read above it."
        ]
    return []


def checks_block(ctx: dict) -> list[str]:
    c = ctx["checks"]
    rows = pd.DataFrame({
        "Check": c["check"], "Scope": c["scope"], "Value": c["value"], "Pass rule": c["pass_if"],
        "Result": c["result"].map(bold_result),
    })  # fmt: skip
    return [
        table(rows),
        "",
        "W1, W3, W4 and W6 pass or fail on the held-out systems; their development and all-systems lines are "
        "information. A check without results says `not run`, never pass or fail. Every word in the Result column is "
        "the one in `validation/results/checks_phase3.csv`.",
    ]


def chosen_after_block(ctx: dict) -> list[str]:
    s = ctx["synth"]
    growth = 100.0 * packs_for(1)[0].r_growth_per_year
    at_origin = f"; median {s['w2_growth_truth_param'].median():.2f} % per year at the fit's origin" if s is not None else ""
    cfg = RecordConfig()
    return [
        "Three readings of the checks were chosen after the development results were seen, and before any held-out "
        "data was read. They are stated here because they were not fixed with the pass numbers.",
        "",
        f"1. **W2, the growth.** The resistance growth on the synthetic packs is judged against the simulator's known "
        f"growth, {growth:.0f} % of the start value per year{at_origin}. The same-days truth is shown next to it as "
        "information.",
        f"2. **W6, the two loop kinds.** W6 compares the full-to-full and the empty-to-empty loop ratios themselves; no "
        f"band is needed. The ratio is Σ λ·E_out ÷ Σ λ·E_in over the loops in memory, with the estimator's weights; it "
        f"exists once the full and empty levels are set and at least {cfg.efficiency_min_loops:g} loops are in memory, "
        "and it equals the record's value wherever the record has one (the check stops otherwise).",
        "3. **The efficiency band.** Each loop's score is g_k = E_out,k − η·E_in,k. A loop runs from one rest to the "
        "next, so its score holds the state-of-charge error of its two end rests with opposite signs. Two neighbouring "
        "loops share a rest: the mean lag-1 product of the scores is the loops' own covariance minus the rest error's "
        "variance s². Loops two apart share no rest: the mean lag-2 product is the loops' own covariance alone. So s² "
        "is estimated as the mean lag-2 product minus the mean lag-1 product, each weighted by the forgetting weights "
        "of the loops it joins. The band is η ± t·√(s²·C₂) ÷ A, with A = Σ λ·E_in, C₂ the sum of the squared weights "
        "of the rests, and t the Student-t quantile at the degrees of freedom of a chi-square variable with the mean "
        "and variance of s² (normal scores, autocovariances up to lag 2). Below one degree of freedom there is no "
        f"band: `{eff.REST_ERROR_UNKNOWN}`. Without three loops in a row: `{eff.NO_SHARED_REST}`. With s² negative "
        f"beyond rounding: `{eff.NO_REST_ERROR}`.",
    ]


def record_block(ctx: dict) -> list[str]:
    sid = RECORD_SYSTEM
    if sid not in ctx["records"]:
        return [f"System {sid_text(sid)} ({set_of(sid)}) has no record yet: {NOT_RUN}."]
    r = ctx["records"][sid]
    f = fields(r)
    pack = r["pack"]
    lines = [
        f"System {sid_text(sid)} is a {set_of(sid)} system; it was named for this page before any result existed. Its record "
        f"is `validation/results/phase3/system_{sid:02d}_record.json`.",
        "",
        f"- **Date of the record:** {r['date'].replace('T', ' ')} (the last sample).",
        f"- **Pack:** {pack['name']}; {pack['chemistry']}; {pack['cells_in_series']} cells in series, "
        f"{pack['cells_in_parallel']} in parallel; nominal {pack['nominal_capacity_ah']:g} Ah, "
        f"{pack['nominal_energy_kwh']:g} kWh. Reference capacity: {r['reference_capacity_ah']:g} Ah.",
        f"- **Sampling interval:** {r['sample_s']:g} s. **Band level:** {r['level']:.0%}.".replace("%.", " %."),
        f"- **Configuration:** capacity `{r['capacity_config_hash']}`, record `{r['record_config_hash']}`.",
        f"- **Field list:** {r['annex_vii_version']}.",
        "",
    ]
    rows = []
    for code, x in f.items():
        unit = SHOW[code][1] if code in SHOW else x["unit"]
        status = x["status"]
        if code == "A1" and status == ESTIMATED:
            d = x["details"]
            unit += f" ({100 * d['share_of_reference']:.1f} % of {d['reference_capacity_ah']:g} Ah)"
        rows.append({
            "Code": code, "Annex VII field": f"\"{x['field']}\"", "What is computed": x["label"],
            "Value [band]": value_band(code, x) if code in SHOW else DASH, "Unit": unit,
            "Events": events_text(x["events"]) if code != "B1" else DASH, "Status": f"`{status}`",
        })  # fmt: skip
    lines += [table(pd.DataFrame(rows)), "", "Events: " + "; ".join(f"{c} {EVENTS[c]}" for c in f) + ".", ""]
    lines += ["**Method and source of each field, as the record states it:**", ""]
    lines += [f"- **{code}:** {x['method']}." for code, x in f.items()]
    lines += ["", *record_details(r, f)]
    return lines


def record_details(r: dict, f: dict) -> list[str]:
    out = []
    a2 = f["A2"]["details"]
    out += [
        "**A2.** " + (
            f"Resistance of the growth fit on the first day ({a2['first_day'][:10]}): {1000 * a2['resistance_start_ohm']:.2f} mΩ; "
            f"at the date: {1000 * a2['resistance_at_ohm']:.2f} mΩ. "
            if a2.get("resistance_start_ohm") is not None else ""
        ) + f"Power in watts: `{a2['power_capability_w_status']}`. What it is: {a2['what_it_is']}.",
        "",
    ]
    a3 = f["A3"]["details"]
    e = a3.get("empty_loops", {})
    parts = []
    if a3.get("coulombic_part") is not None:
        parts.append(f"Coulombic part {100 * a3['coulombic_part']:.2f} %, voltage part {100 * a3['voltage_part']:.2f} %")
    for key, label, fmt in (("dof", "degrees of freedom", "{:.1f}"), ("rest_error_wh", "rest error", "{:.0f} Wh"),
                            ("rho", "lag-1 correlation of the loop scores", "{:+.2f}")):  # fmt: skip
        if a3.get(key) is not None:
            parts.append(f"{label} {fmt.format(a3[key])}")
    empty = (f"{100 * e['value']:.2f} % [{100 * e['low']:.2f}, {100 * e['high']:.2f}]" if e.get("status") == ESTIMATED
             else f"`{e.get('status', DASH)}`")  # fmt: skip
    out += [
        "**A3.** " + ("; ".join(parts) + ". " if parts else "")
        + f"Empty-to-empty loops (cross-check W6, {events_text(e.get('n_loops'))} loops in memory): {empty}. "
        + f"Limits as the record states them: {a3.get('limits', DASH)}.",
        "",
    ]
    a4 = f["A4"]
    d = a4["details"]
    if a4["status"] == ESTIMATED:
        out.append(
            f"**A4.** {a4['value']:.1f} mA is {d['percent_of_reference_per_month']:.1f} % "
            f"[{d['percent_of_reference_per_month_low']:.1f}, {d['percent_of_reference_per_month_high']:.1f}] of "
            f"{d['reference_capacity_ah']:g} Ah per month ({HOURS_PER_MONTH:g} h). It holds {d['what_it_holds']}."
        )
    else:
        out.append(f"**A4.** `{a4['status']}`.")
    years = d.get("year_end_values") or []
    if years:
        out += ["", table(pd.DataFrame([{
            "Last value of the calendar year": y["time"][:10], "Apparent charge loss, mA [band]":
                f"{y['charge_loss_ma']:.1f} [{y['charge_loss_ma_low']:.1f}, {y['charge_loss_ma_high']:.1f}]"
                if y.get("charge_loss_ma") is not None else DASH,
            "Pairs in memory": events_text(y.get("n_pairs")),
        } for y in years]))]  # fmt: skip
    t = d.get("evolution", {})
    trend = (f"{t['slope_ma_per_year']:+.2f} mA per year [{t['slope_ma_per_year_low']:+.2f}, {t['slope_ma_per_year_high']:+.2f}] "
             f"over {t['n_years']} year-end values" if t.get("status") == ESTIMATED else f"`{t.get('status', DASH)}`")  # fmt: skip
    out += ["", f"Evolution (a weighted line through the last value of each calendar year): {trend}.", ""]
    a5 = f["A5"]["details"]
    out += [f"**A5.** Every {RecordConfig().temp_band_c:g}-degree band of pack temperature; the headline is the band at {a5['headline_band_c']:g} °C. "
            f"{a5['what_it_is'][:1].upper()}{a5['what_it_is'][1:]}.", ""]  # fmt: skip
    out.append(table(pd.DataFrame([{
        "Band, °C": f"{b['temp_band_c'] - RecordConfig().temp_band_c / 2:g} to {b['temp_band_c'] + RecordConfig().temp_band_c / 2:g}",
        "Resistance, mΩ [band]": (f"{1000 * b['resistance_ohm']:.2f} [{1000 * b['resistance_ohm_low']:.2f}, "
                                  f"{1000 * b['resistance_ohm_high']:.2f}]" if b["status"] == ESTIMATED else DASH),
        "Days in memory": events_text(b["n_days"]), "Steps in memory": events_text(b["n_steps"]),
        "Status": f"`{b['status']}`",
    } for b in a5["bands"]])))  # fmt: skip
    growth = (f"{a5['growth_percent_per_year']:+.2f} % of the start value per year [{a5['growth_percent_per_year_low']:+.2f}, "
              f"{a5['growth_percent_per_year_high']:+.2f}], over {events_text(a5['growth_n_days'])} days spanning "
              f"{a5['growth_span_years']:.1f} years" if a5["growth_status"] == ESTIMATED else f"`{a5['growth_status']}`")  # fmt: skip
    out += ["", f"Resistance increase (Annex VII Part A asks for it in %): {growth}.", ""]
    b1 = f["B1"]["details"]
    out += [f"**B1.** Date of manufacture: `{b1['date_of_manufacture_status']}`. Date of putting into service: "
            f"{b1.get('date_put_into_service') or DASH} ({b1['date_put_into_service_status']}).", ""]  # fmt: skip
    b2, b3 = f["B2"]["details"], f["B3"]["details"]
    out += [
        f"**B2, B3, B5.** Counted from {b2['counted_from'][:10]} to {b2['counted_to'][:10]}: since {b2['since']}. "
        f"Energy in {b2['energy_in_kwh']:.0f} kWh, out {b2['energy_out_kwh']:.0f} kWh; charge in {b3['charge_in_ah']:.0f} Ah, "
        f"out {b3['charge_out_ah']:.0f} Ah. Days with data {b2['n_days_with_data']}; hours measured "
        f"{b2['hours_measured']:.0f}, hours missing {b2['hours_missing']:.0f}; share of samples interpolated "
        f"{b2['share_interpolated']:.2e}.",
        "",
    ]
    b4 = f["B4"]["details"]
    hours = pd.DataFrame(b4["hours_per_temperature_band"])
    hours = hours[(hours[["hours_charge", "hours_discharge", "hours_idle"]].sum(axis=1) > 0)]
    width = float(np.diff(sorted(pd.DataFrame(b4["hours_per_temperature_band"])["temp_band_c"]))[0])
    out += [
        f"**B4.** {b4['temperature_measured'].capitalize()}. Discharges that reached the system's own end-of-discharge "
        f"level: {events_text(f['B4']['value'])}. Lowest and highest voltage: {b4['voltage_min_v']:.1f} and "
        f"{b4['voltage_max_v']:.1f} V for the pack, {b4['voltage_min_cell_v']:.3f} and {b4['voltage_max_cell_v']:.3f} V "
        f"per cell in series ({b4['cell_voltage_note']}). Hours without a temperature: {b4['hours_no_temperature']:.0f}. "
        f"Counts against limits (deep discharges, hours beyond the temperature limits, hours charging beyond them): "
        f"`{b4['limits_status']}`.",
        "",
        table(pd.DataFrame({
            "Pack temperature, °C": [f"{c:g} to {c + width:g}" for c in hours["temp_band_c"]],
            "Hours charging": [f"{v:.1f}" for v in hours["hours_charge"]],
            "Hours discharging": [f"{v:.1f}" for v in hours["hours_discharge"]],
            "Hours idle": [f"{v:.1f}" for v in hours["hours_idle"]],
        })),  # fmt: skip
        "",
        "Bands with no measured hour are left out of the table.",
    ]
    return out


def fleet_block(ctx: dict, band_c: float) -> list[str]:
    reasons = Reasons()
    rows = []
    for sid in figgener.SYSTEM_IDS:
        row = {"System": sid_text(sid), "Set": set_of(sid), "Chemistry": chemistry_of(ctx, sid)}
        if sid not in ctx["records"]:
            for col in ("Record date", "A1, % of nominal", "A2, % of first day", "A3, %", "A4, mA",
                        "A5, mΩ", "A5 growth, % per year", "B1 put into service", "B2, kWh", "B3, Ah", "B4, empty rests",
                        "B5, cycles"):  # fmt: skip
                row[col] = NOT_RUN if col == "Record date" else DASH
            rows.append(row)
            continue
        r = ctx["records"][sid]
        f = fields(r)
        a1 = f["A1"]
        d = a1["details"]
        share = dict(a1, value=d["share_of_reference"] * 100.0, low=d["share_of_reference_low"] * 100.0,
                     high=d["share_of_reference_high"] * 100.0) if a1["status"] == ESTIMATED else a1  # fmt: skip
        g = f["A5"]["details"]
        growth = {"status": g["growth_status"], "value": g["growth_percent_per_year"],
                  "low": g["growth_percent_per_year_low"], "high": g["growth_percent_per_year_high"]}  # fmt: skip
        row["Record date"] = r["date"][:10]
        row["A1, % of nominal"] = (f"{share['value']:.1f} [{share['low']:.1f}, {share['high']:.1f}]"
                                   if a1["status"] == ESTIMATED else reasons.mark(a1["status"]))  # fmt: skip
        row["A2, % of first day"] = cell("A2", f["A2"], reasons)
        row["A3, %"] = cell("A3", f["A3"], reasons)
        row["A4, mA"] = cell("A4", f["A4"], reasons)
        row["A5, mΩ"] = cell("A5", f["A5"], reasons)
        row["A5 growth, % per year"] = (f"{growth['value']:+.1f} [{growth['low']:+.1f}, {growth['high']:+.1f}]"
                                        if growth["status"] == ESTIMATED else reasons.mark(growth["status"]))  # fmt: skip
        row["B1 put into service"] = f["B1"]["details"].get("date_put_into_service") or DASH
        for code, col in (("B2", "B2, kWh"), ("B3", "B3, Ah"), ("B4", "B4, empty rests"), ("B5", "B5, cycles")):
            row[col] = cell(code, f[code], reasons)
        rows.append(row)
    made = sorted({fields(r)["B1"]["details"]["date_of_manufacture_status"] for r in ctx["records"].values()})
    n = len(ctx["records"])
    frame = pd.DataFrame(rows).rename(columns={"A5, mΩ": f"A5 at {band_c:g} °C, mΩ"})
    # A1 comes from the capacity estimator; its plausibility rules (version 0.2) should leave none, but
    # name every record whose capacity is estimated at or below zero, or whose band reaches zero.
    impossible_a1 = []
    for sid, r in sorted(ctx["records"].items()):
        a1 = fields(r)["A1"]
        if a1["status"] == ESTIMATED and (a1["value"] <= 0.0 or a1["low"] <= 0.0):
            impossible_a1.append(f"{sid_text(sid)} ({a1['value']:.2f} Ah [{a1['low']:.2f}, {a1['high']:.2f}])")
    a1_note = (
        []
        if not impossible_a1
        else [
            "",
            "**A1 says `estimated` for a capacity at or below zero, or a band that reaches zero:** "
            + ", ".join(impossible_a1) + ". W1 counts impossible values in A2–A5 and B2–B5 only. The capacity page "
            "([02_capacity_validation.md](02_capacity_validation.md)) shows the cause.",
        ]
    )
    return [
        table(frame),
        "",
        f"Value [{BAND} band] where the status is `estimated`; otherwise the reason, below. Records: {n} of "
        f"{len(figgener.SYSTEM_IDS)}. A5 is the headline temperature band. B1, date of manufacture, on all {n} records: "
        + (", ".join(f"`{m}`" for m in made) or DASH) + ". B2, B3 and B5 count since the first day of data.",
        *a1_note,
        "",
        *reasons.legend(),
    ]


def over_time_block(ctx: dict, band_c: float, figures: dict) -> list[str]:
    lines = []
    if "resistance" in figures:
        lines += [f"![Resistance over time](img/{figures['resistance']})", ""]
    rows = []
    for sid in sorted(ctx["records"]):
        g = fields(ctx["records"][sid])["A5"]["details"]
        if g["growth_status"] != ESTIMATED:
            continue
        rows.append((chemistry_of(ctx, sid), set_of(sid), g["growth_percent_per_year"]))
    if rows:
        t = pd.DataFrame(rows, columns=["chemistry", "set", "growth"])
        summary = []
        for chemistry in ORDER:
            for label in ("held-out", "development"):
                part = t[(t["chemistry"] == chemistry) & (t["set"] == label)]["growth"]
                if len(part):
                    summary.append({"Chemistry": chemistry, "Set": label, "Systems with a growth": len(part),
                                    "Median growth, % per year": f"{part.median():+.1f}",
                                    "Range": f"{part.min():+.1f} to {part.max():+.1f}"})  # fmt: skip
        lines += [
            f"Resistance growth over the whole record, {band_c:g} °C band, no forgetting (R = a + b·years; growth = b ÷ a):",
            "",
            table(pd.DataFrame(summary)),
            "",
        ]
    if "efficiency" in figures:
        lines += [f"![Efficiency over time](img/{figures['efficiency']})", ""]
    yearly = yearly_all(ctx)
    if len(yearly):
        e = yearly[yearly["quantity"] == "efficiency_full_loops"].copy()
        e = e.groupby(["system_id", e["date"].dt.normalize()]).tail(1)
        n_ok = int((e["status"] == ESTIMATED).sum())
        counts = e.loc[e["status"] != ESTIMATED, "status"].value_counts().sort_index()
        lines += [
            f"Year ends with a full-loop efficiency and its band: {n_ok} of {len(e)}. The others, by reason: "
            + "; ".join(f"`{k}` {v}" for k, v in counts.items()) + ".",
            "Where the record has no band, the figure shows the loop ratio of check W6 (the same value, no band).",
        ]
    return lines


def w3_block(ctx: dict, rule: float, figures: dict) -> list[str]:
    t = ctx["onset"]
    if t is None:
        return [f"No onset reference yet: {NOT_RUN}."]
    held_present = (t["set"] == "held-out").any()
    lines = [
        f"The reference is the same estimator on the samples from {BEFORE.total_seconds():g} s before to "
        f"{AFTER.total_seconds():g} s after the onset of each field capacity test. The operating value is the record's "
        "estimate in the band of the pack temperature at the onset, at the start of the test's masked window: it uses "
        "operation before the test only. Difference = (operating − reference) ÷ reference.",
        "",
        "A test starts from full at the system's maximum power, which is not the daily operating point: a bias is "
        "expected and is shown per chemistry, not corrected.",
        "",
    ]
    if not held_present:
        lines += ["The held-out tests have not been run yet; the development tests below are information.", ""]
    if "onset" in figures:
        lines += [f"![Onset against operation](img/{figures['onset']})", ""]
    summary = []
    for label in ("held-out", "development"):
        for chemistry in ORDER:
            part = t[(t["set"] == label) & (t["chemistry"] == chemistry)]
            if not len(part):
                continue
            refs = part[part["onset_status"] == ESTIMATED]
            diff = refs["difference_percent"]
            absd = diff.abs().fillna(np.inf)
            summary.append({
                "Set": label, "Chemistry": chemistry, "Tests": len(part), "With a reference": len(refs),
                "Without an operating value": int(diff.isna().sum()),
                "Median difference, %": num(diff.median(), "{:+.1f}"),
                "Median absolute difference, %": num(np.median(absd), "{:.1f}") if len(absd) and np.isfinite(np.median(absd))
                else ("infinite" if len(absd) else DASH),
            })  # fmt: skip
    lines += [table(pd.DataFrame(summary)), "",
              f"A test with a reference but no operating value counts as an infinite difference. With fewer than "
              f"{ONSET_MIN_REFERENCES} references among the held-out tests, W3 is not possible.", ""]  # fmt: skip
    rows = []
    for _, x in t.sort_values(["system_id", "test"]).iterrows():
        def band(prefix):
            v = x[f"{prefix}_ohm"]
            return (f"{1000 * v:.1f} [{1000 * x[f'{prefix}_low']:.1f}, {1000 * x[f'{prefix}_high']:.1f}]"
                    if x[f"{prefix}_status"] == ESTIMATED else f"`{x[f'{prefix}_status']}`")  # fmt: skip
        rows.append({
            "Test": x["test_id"], "Set": x["set"], "Chemistry": x["chemistry"], "Onset": f"{x['onset']:%Y-%m-%d}",
            "Pack, °C": num(x["temp_pack_c"], "{:.1f}"), "Band, °C": num(x["temp_band_c"], "{:g}"),
            "Reference, mΩ [band]": band("onset"), "Reference steps": events_text(x["onset_n_steps"]),
            "Operating, mΩ [band]": band("operating"), "Difference, %": num(x["difference_percent"], "{:+.1f}"),
        })  # fmt: skip
    lines += [table(pd.DataFrame(rows)), ""]
    trend = []
    both = t[(t["onset_status"] == ESTIMATED) & (t["operating_status"] == ESTIMATED)]
    for sid, g in both.groupby("system_id"):
        if len(g) < 2:
            continue
        g = g.sort_values("onset")
        a, b = g.iloc[0], g.iloc[-1]
        trend.append({
            "System": sid_text(sid), "Set": set_of(sid), "Tests": f"{a['test_id']} to {b['test_id']}",
            "Reference change, %": f"{100 * (b['onset_ohm'] / a['onset_ohm'] - 1):+.0f}",
            "Operating change, %": f"{100 * (b['operating_ohm'] / a['operating_ohm'] - 1):+.0f}",
        })  # fmt: skip
    if trend:
        lines += ["Between a system's first and last test with both values (information):", "", table(pd.DataFrame(trend))]
    return lines


def w4_block(ctx: dict, pairs: pd.DataFrame, figures: dict) -> list[str]:
    if pairs.empty:
        return [f"No pairs yet: {NOT_RUN}."]
    lines = [
        "At every year end, every two neighbouring temperature bands of one system that both have an estimate form a "
        "pair (the pairs of the check); resistance falls with temperature, so the colder should read higher.",
        "",
    ]
    if "w4" in figures:
        lines += [f"![Colder against warmer band](img/{figures['w4']})", ""]
    rows = []
    for sid, g in pairs.groupby("system_id"):
        rows.append({"System": sid_text(sid), "Set": set_of(sid), "Chemistry": chemistry_of(ctx, sid),
                     "Pairs": len(g), "Colder reads higher": int(g["colder_higher"].sum()),
                     "Share": f"{g['colder_higher'].mean():.0%}"})  # fmt: skip
    return lines + [table(pd.DataFrame(rows))]


def w6_block(ctx: dict, zone: float, figures: dict) -> list[str]:
    w6 = ctx["w6"]
    if w6 is None or w6.empty:
        return [f"No loop ratios yet: {NOT_RUN}."]
    lines = []
    if "w6" in figures:
        lines += [f"![Full against empty loops](img/{figures['w6']})", ""]
    rows = []
    for sid, g in w6.groupby("system_id"):
        rows.append({"System": sid_text(sid), "Set": set_of(sid), "Chemistry": chemistry_of(ctx, sid),
                     "System-years": len(g), f"Within {zone:g} point": int(g["within_1_point"].sum()),
                     "Median full minus empty, points": f"{g['difference_points'].median():+.2f}",
                     "Largest, points": f"{g['difference_points'].abs().max():.2f}"})  # fmt: skip
    same_day = int(w6.groupby(["system_id", w6["date"].dt.normalize()])["date"].transform("size").gt(1).sum() // 2)
    return lines + [
        table(pd.DataFrame(rows)),
        "",
        f"Full-to-full reads higher than empty-to-empty in {int((w6['difference_points'] > 0).sum())} of {len(w6)} "
        "system-years. A system-year is a year end (31 December at 00:00, or the last sample) where both loop kinds "
        "have a ratio. "
        + (f"In {plural(same_day, 'system')} the last sample falls on a 31 December, so that day gives two "
           "rows; they are counted as the check counts them. " if same_day else "")
        + "Where the two kinds differ, at least one of them does not "
        "close at the same state of charge each time; which one is not separated here.",
    ]


def synthetic_block(ctx: dict, band_c: float) -> list[str]:
    s = ctx["synth"]
    if s is None:
        return [f"No synthetic results yet: {NOT_RUN}."]
    n = len(s)
    w5 = s["w5_status"].fillna("")
    banded = w5 == ESTIMATED
    held = s["w5_held"].fillna(False).astype(bool)
    growth = 100.0 * packs_for(1)[0].r_growth_per_year
    rows = [
        {"Check": "W2 value, same days", "Packs": n, "Held": int(s["w2_value_held"].astype(bool).sum()),
         "Note": "truth on the same days as the estimate (the defined truth)"},
        {"Check": "W2 value, at the date", "Packs": n, "Held": int(s["w2_value_held_at_date"].astype(bool).sum()),
         "Note": "the estimate lags a growing resistance"},
        {"Check": f"W2 value, at {band_c:g} °C", "Packs": n, "Held": int(s["w2_value_held_25c"].astype(bool).sum()),
         "Note": f"no temperature model; the days in the band are not all at {band_c:g} °C"},
        {"Check": "W2 growth", "Packs": n, "Held": int(s["w2_growth_held_param"].astype(bool).sum()),
         "Note": f"against the known {growth:.0f} % per year; median estimate {s['w2_growth'].median():.2f} % per year"},
        {"Check": "W5", "Packs": n, "Held": int(held.sum()),
         "Note": f"{int(banded.sum())} with a band, {int(held[banded].sum())} held among them; median degrees of "
                 f"freedom {s.loc[banded, 'w5_dof'].median():.1f}"},
        {"Check": "W7", "Packs": n, "Held": int(s["w7_held"].astype(bool).sum()),
         "Note": f"truth {s['w7_truth_ma'].median():.0f} mA; mean error {s['w7_error_ma'].mean():+.2f} mA"},
        {"Check": "W8", "Packs": n, "Held": int(s["w8_pass"].astype(bool).sum()),
         "Note": "pack with the current gain error only; within the rule"},
    ]  # fmt: skip
    t = pd.DataFrame(rows)
    t["Share"] = (t["Held"] / t["Packs"]).map(lambda v: f"{v:.1%}")
    return [
        f"{n} simulated packs, each with its own operation, sensor gain errors, BMS stop errors and planted harmful "
        f"events; their resistance grows by {growth:.0f} % of its start value per year and falls with the pack "
        "temperature, which follows the season. The full chain runs on each with the frozen settings.",
        "",
        table(t[["Check", "Packs", "Held", "Share", "Note"]]),
    ]


def method_block(ctx: dict) -> list[str]:
    any_record = next(iter(ctx["records"].values()), None)
    labels = {x["code"]: (x["field"], x["label"]) for x in any_record["fields"]} if any_record else {}
    rows = []
    for code in LIMITS:
        field, label = labels.get(code, (DASH, DASH))
        rows.append({"Code": code, "Annex VII field": f"\"{field}\"", "besslib computes": label,
                     "Annex IV definition": ANNEX_IV.get(code, DASH), "Limits": LIMITS[code]})  # fmt: skip
    cfg = RecordConfig()
    return [
        f"Annex IV of the Regulation ([consolidated text]({REGULATION})) defines three of the terms; they are quoted. "
        "Part A asks for \"internal resistance increase (in %)\" and \"power fade (in %)\".",
        "",
        table(pd.DataFrame(rows)),
        "",
        "Shared rules:",
        "",
        "1. Every estimate has a status: `estimated`, or `not possible from this data` with the reason and no value. An "
        "impossible value (an efficiency above 100 % or at or below zero, a resistance at or below zero) is never "
        "`estimated`. Since version 0.2 a capacity whose levels have crossed, whose value or band reaches zero, or whose "
        "memory holds a full-to-empty or empty-to-full pair of the wrong sign is not `estimated` either.",
        f"2. Forgetting: an event of age s counts with weight λ = exp(−s ÷ {cfg.tau_days:g} days), the capacity "
        "estimator's memory time, not tuned again.",
        "3. Resistance and efficiency share one small core: a fit through the origin, R = Σ λ·b ÷ Σ λ·a, one update "
        "per event, fixed-size state, plain floats. The resistance band is the sandwich on the day scores (days share "
        "no sample, so no lag term; Newey and West 1987) plus the sensors' gain errors as a systematic term. The "
        "efficiency band is described above.",
        f"4. Sensor accuracies (Figgener et al. 2024, Supplementary Table 2): current {cfg.current_accuracy:.1%}, "
        f"voltage {cfg.voltage_accuracy:.2%}, each read as a rectangular half-width (JCGM 100:2008, 4.3.7).",
        "5. Capacity-test windows (one day before to one day after) are masked from every estimate; the counts (B2–B5) "
        "include them.",
        "",
        "Sources:",
        "",
        f"- Regulation (EU) 2023/1542, Annexes IV and VII, consolidated version of 31 July 2025: {REGULATION}.",
        "- Barai, A. et al., *Scientific Reports* 8, 21 (2018), https://doi.org/10.1038/s41598-017-18424-5 (CC BY 4.0): "
        "a DC resistance from any current step; its value depends on the measurement timescale.",
        "- Schweiger, H.-G. et al., *Sensors* 10, 5604–5625 (2010), https://doi.org/10.3390/s100605604 (CC BY 3.0): "
        "current-step methods; the internal resistance depends on the method, the state of charge and the temperature.",
        "- Schimpe, M. et al., *Applied Energy* 210, 211–229 (2018), https://doi.org/10.1016/j.apenergy.2017.10.129: "
        "cited for scale only (AC system efficiency against the battery's; efficiency depends on use). Not a target "
        "for besslib's DC figure.",
        "- Newey, W. K. and West, K. D., *Econometrica* 55, 703–708 (1987), https://doi.org/10.2307/1913610: the "
        "sandwich covariance.",
        "- JCGM 100:2008, *Evaluation of measurement data — Guide to the expression of uncertainty in measurement*, "
        "4.3.7.",
        "- Figgener, J. et al., *Nature Energy* 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 (the "
        "capacity method and the sensor accuracies; cited, nothing copied).",
        "",
        "Not verified: any published resistance or efficiency figure for these 21 systems. The dataset has none, and "
        "none is compared here.",
    ]


def settings_block(ctx: dict) -> list[str]:
    cfg = RecordConfig()
    rows = [{"Setting": f"`{k}`", "Value": str(v) if isinstance(v, bool) else f"{v:g}", "Meaning": MEANING.get(k, DASH)}
            for k, v in asdict(cfg).items()]
    t = ctx["tuning"]
    tune = pd.DataFrame({
        "Smallest step, x C": t["min_step_c_rate"].map("{:g}".format),
        "Steady, s": t["steady_s"].map("{:g}".format),
        "Median robust scatter": t["median_scatter"].map("{:.3f}".format),
        "Median share of days with a value": t["median_share"].map("{:.2f}".format),
        "Chosen": t["selected"].map(lambda v: "**yes**" if bool(v) else ""),
        "Record hash": t["record_config_hash"].map(lambda h: f"`{h}`"),
    })  # fmt: skip
    return [
        f"Record settings (`RecordConfig()`, hash `{ctx['hashes']['record']}`). The capacity settings "
        f"(`CapacityConfig()`, hash `{ctx['hashes']['capacity']}`) are those of the capacity page, unchanged.",
        "",
        table(pd.DataFrame(rows)),
        "",
        f"**How the current-step filter was fixed** (development systems only; no test value read): a "
        f"{len(t)}-setting grid. The rule, written before any result: the lowest median, over the "
        f"{len(DEVELOPMENT)} development systems, of the robust scatter ({MAD_TO_SD:g} × MAD ÷ median) of the daily "
        f"values in the headline band around their {WINDOW.rstrip('D')}-day running median, among the settings whose "
        f"median share of days with a value is at least {MIN_SHARE:g}; if none reaches it, the highest share.",
        "",
        table(tune),
        "",
        "The current-step filter was tuned in version 0.1 (the record hashes above) and kept frozen in version 0.2; "
        "its two settings are unchanged.",
    ]


def version_block(ctx: dict) -> list[str]:
    return [
        "Version 0.2 changes the capacity fit (rest-voltage terms, a trend, the band's forgetting, plausibility rules) "
        "and makes A2 follow A5. The held-out systems were seen once before, at the release 0.1.0 settings. What "
        "changed, and every check before and after, is on the [version 0.2 page](04_version_0_2.md).",
    ]


def rebuild_block(ctx: dict) -> list[str]:
    n = len(ctx["synth"]) if ctx["synth"] is not None else 0
    return [
        "```",
        "pip install -e .[dev]",
        "python validation/run_annex_vii.py development      # the development records",
        "python validation/run_annex_vii.py --held-out <id>  # one held-out record; downloads, reduces, deletes its archive",
        "python validation/onset_resistance.py [--held-out]  # the W3 references",
        f"python validation/synthetic_phase3.py               # W2, W5, W7, W8 on {n} synthetic packs",
        "python validation/check_annex_vii.py                # the checks W1-W10",
        "python validation/make_annex_vii_doc.py             # this page and its figures",
        "```",
        "",
        "The current-step filter is tuned again with `validation/tune_resistance.py`. Every result row carries both "
        f"configuration hashes; this page refuses to build from results of any hash other than capacity "
        f"`{ctx['hashes']['capacity']}` and record `{ctx['hashes']['record']}`.",
    ]


def document(ctx: dict, figures: dict, pairs: pd.DataFrame, band_c: float, w3_rule: float, w6_zone: float) -> str:
    parts = [
        "# Phase 3 — the Annex VII record from operating data",
        "",
        "Written by `validation/make_annex_vii_doc.py` from `validation/results/`. No number on this page is typed by hand.",
        "",
        "besslib computes the ten parameters that Annex VII of Regulation (EU) 2023/1542 lists for the state of health "
        "and the expected lifetime of a stationary battery energy storage system, from operating data only: each with a "
        f"value, a {BAND} band and the number of events it rests on, or `not possible from this data` with the reason. "
        f"The settings were tuned on the {len(DEVELOPMENT)} development systems, then frozen. The {len(HELD_OUT)} "
        "held-out systems were run once at the settings of release 0.1.0 and once at these "
        "([what changed](04_version_0_2.md)).",
        "",
        "**Not a compliance certificate, not legal advice: it computes the Annex VII parameters; it does not certify "
        "them.**",
        "",
        "## Result",
        "",
        *verdict_block(ctx),
        "",
        "## Checks W1–W10",
        "",
        *checks_block(ctx),
        "",
        "### Readings chosen after the development results",
        "",
        *chosen_after_block(ctx),
        "",
        f"## The record of system {sid_text(RECORD_SYSTEM)}",
        "",
        *record_block(ctx),
        "",
        "## The fleet",
        "",
        *fleet_block(ctx, band_c),
        "",
        "## Resistance and efficiency over time",
        "",
        *over_time_block(ctx, band_c, figures),
        "",
        "## W3: resistance from operation against the capacity-test onsets",
        "",
        *w3_block(ctx, w3_rule, figures),
        "",
        "## W4: the colder band reads higher",
        "",
        *w4_block(ctx, pairs, figures),
        "",
        "## W6: full-to-full against empty-to-empty loops",
        "",
        *w6_block(ctx, w6_zone, figures),
        "",
        "## Synthetic packs: W2, W5, W7, W8",
        "",
        *synthetic_block(ctx, band_c),
        "",
        "## Method and limits per field",
        "",
        *method_block(ctx),
        "",
        "## Settings",
        "",
        *settings_block(ctx),
        "",
        "## Version 0.2",
        "",
        *version_block(ctx),
        "",
        "## Rebuild",
        "",
        *rebuild_block(ctx),
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


def _rule(ctx: dict, check: str, pattern: str, column: str) -> float:
    rows = ctx["checks"][ctx["checks"]["check"] == check]
    for text in rows[column]:
        found = re.search(pattern, text)
        if found:
            return float(found.group(1))
    raise SystemExit(f"checks_phase3.csv: no {check} row states its rule ({pattern})")


def main() -> None:
    IMG.mkdir(parents=True, exist_ok=True)
    ctx = load()
    band_c = headline_band(ctx)
    if band_c is None:
        raise SystemExit("no record yet: run run_annex_vii.py first")
    w3_rule = threshold(ctx["checks"].loc[ctx["checks"]["check"] == "W3", "pass_if"].iloc[0])
    w6_zone = _rule(ctx, "W6", r"within (\d+(?:\.\d+)?) point", "scope")
    yearly = yearly_all(ctx)
    pairs = w4_pairs(yearly) if len(yearly) else pd.DataFrame()
    figures = {"resistance": figure_resistance(ctx, band_c), "efficiency": figure_efficiency(ctx)}
    for key, name in (("onset", figure_onset(ctx, w3_rule)), ("w4", figure_w4(ctx, pairs)), ("w6", figure_w6(ctx, w6_zone))):
        if name:
            figures[key] = name
    (DOCS / PAGE).write_text(document(ctx, figures, pairs, band_c, w3_rule, w6_zone))
    print(f"docs/{PAGE} written: {len(ctx['records'])} of {len(figgener.SYSTEM_IDS)} records, figures "
          f"{', '.join(sorted(figures.values()))}; capacity {ctx['hashes']['capacity']}, record {ctx['hashes']['record']}")  # fmt: skip


if __name__ == "__main__":
    main()
