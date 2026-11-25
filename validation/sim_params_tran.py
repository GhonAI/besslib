"""The NMC and LMO cells of ``besslib.sim``: ``presets/nmc.toml`` and ``presets/lmo.toml`` from Tran et al. 2021.

Source: M.-K. Tran, A. DaCosta, A. Mevawalla, S. Panchal, M. Fowler, "Comparative Study of Equivalent
Circuit Models Performance in Four Common Lithium-Ion Batteries: LFP, NMC, LMO, NCA", Batteries 7, 51
(2021), doi:10.3390/batteries7030051, CC BY 4.0. The article and its Figure 6 are read from the Internet
Archive (the publisher refuses scripts) into ``data/tran2021/`` (git-ignored).

How each value is read:

* capacity: Table 5, the nominal capacity (NMC: Samsung INR18650-20S; LMO: EFEST IMR18650V1);
* R0, R1, tau1 = R1 C1: the 1RC columns of Table 7 (NMC) and Table 8 (LMO), 10 to 90 % state of charge,
  out of the HTML; measured at 23 degC. At 0 and 100 % the nearest tabulated value holds (the model's rule);
* open-circuit voltage: digitised from the figure. The article's data are not public; Figure 6 (b) and (c)
  show the C/25 curves, and the article takes their average (yellow) as the open-circuit voltage. The axes
  are calibrated on the tick marks (least squares through all of them); the curve is the centre of the yellow
  pixels of each pixel column (edge pixels in proportion), read every 1 % of state of charge (the figure's
  own line has its corners there). Where less than two yellow pixels show in a column (hidden under another
  line) the curve is interpolated between its neighbours; at 0 and 100 %, under the frame, the line through the
  nearest 8 readable columns is continued. The result is made non-decreasing;
* temperature: the article has one temperature. The activation energies are those of Ecker et al. 2015
  (doi:10.1149/2.0551509jes), measured on another cell, as in ``cases/demo.toml``.

Self-check: the low (blue) and high (red) curves are read the same way; the average curve against their
mean says how well the reading works, and their distance is a bound on what any reading of the average
curve could get wrong.

Needs matplotlib (to read the PNG). Writes the two presets and ``validation/results/sim/tran_preset.csv``.

Usage: python validation/sim_params_tran.py
"""

from __future__ import annotations

import hashlib
import math
import re
import tomllib
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd

from sim_params_lic import fmt, monotone, rows_text, wrapped

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "data" / "tran2021"
PRESETS = ROOT / "src" / "besslib" / "sim" / "presets"
OUT = ROOT / "validation" / "results" / "sim" / "tran_preset.csv"
DOI = "10.3390/batteries7030051"
SOURCES = {
    "tran.htm": "http://web.archive.org/web/20220802092027id_/https://www.mdpi.com/2313-0105/7/3/51/htm",
    "g006.png": "http://web.archive.org/web/20260529133456im_/https://www.mdpi.com/batteries/batteries-07-00051/"
                "article_deploy/html/images/batteries-07-00051-g006.png",
}
TEMP_C = 23.0  # "a room temperature of 23 degC", section 3.1
# Ecker et al. 2015, as in cases/demo.toml: the electrolyte conductivity and the graphite exchange current density.
EA_R0, EA_R1 = 17120.0, 53400.0
CELLS = {
    "nmc": dict(chemistry="NMC", table="t007", number=7, panel="b", cell_name="INR18650-20S"),
    "lmo": dict(chemistry="LMO", table="t008", number=8, panel="c", cell_name="IMR18650V1"),
}
# Where each panel lies in the image (rows, columns, pixels, generously), and its axes' end values.
PANELS = {"b": dict(rows=(20, 1620), cols=(2280, 4100)), "c": dict(rows=(1930, 3490), cols=(180, 2010))}
SOC_TICKS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
VOLT_TICKS = (2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.0, 4.2)
# How strongly a pixel (r, g, b) shows one line: 0 for white, grey, black and the two other lines, and in
# proportion for a pixel at a line's edge. The figure's lines are (255, 179, 0), (0, 115, 193) and (249, 82, 9).
WEIGHTS = {
    "average": lambda r, g, b: (g - b) - 0.5 * (r - g),
    "low": lambda r, g, b: b - r,
    "high": lambda r, g, b: (r - g) - 0.425 * (g - b),
}
FULL = {name: f(*map(float, rgb)) for (name, f), rgb in
        zip(WEIGHTS.items(), ((255, 179, 0), (0, 115, 193), (249, 82, 9)))}
STEP = 0.01
MIN_PX = 2.0  # a column with less of the colour than this is not read
WINDOW_PX = 3  # a grid point is the straight line through the columns this near to it


def fetch(name: str) -> Path:
    path = FOLDER / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SOURCES[name]) as r:
            path.write_bytes(r.read())
    return path


# --- the tables ---------------------------------------------------------------------------------
class _Rows(HTMLParser):
    """The rows of the tables inside one ``<div id=...>``, as lists of cell texts."""

    def __init__(self, wanted: str) -> None:
        super().__init__()
        self.wanted, self.depth, self.rows, self._cell = wanted, 0, [], None

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif dict(attrs).get("id") == self.wanted:
                self.depth = 1
        if not self.depth:
            return
        if tag == "tr":
            self.rows.append([])
        if tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append("".join(self._cell).strip())
            self._cell = None
        if tag == "div":
            self.depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_rows(table: str) -> list[list[str]]:
    p = _Rows(f"table_body_display_batteries-07-00051-{table}")
    p.feed(fetch("tran.htm").read_text(encoding="utf-8"))
    return p.rows


def capacity_ah(c: dict) -> float:
    rows = html_rows("t005")
    col = next(k for k, name in enumerate(rows[0]) if name.startswith("Nominal Capacity (mAh)"))
    row = next(r for r in rows if r[0] == c["chemistry"])
    if row[2] != c["cell_name"]:
        raise ValueError(f"Table 5 names {row[2]}, expected {c['cell_name']}")
    return float(row[col]) / 1000.0


def parameters(c: dict) -> pd.DataFrame:
    """R0, R1, C1 of the 1RC model against state of charge, increasing."""
    rows = html_rows(c["table"])
    if rows[0][1] != "1RC" or rows[1][:4] != ["SOC", "R0", "R1", "C1"] or [re.sub(r"\W", "", u) for u in rows[2][-13:][:3]] != ["Ω", "Ω", "F"]:
        raise ValueError(f"Table {c['number']} does not have the expected head: {rows[:3]}")
    data = [[float(x) for x in r[:4]] for r in rows if re.fullmatch(r"0\.\d", r[0] if r else "")]
    out = pd.DataFrame(data, columns=["soc", "r0", "r1", "c1"]).sort_values("soc", ignore_index=True)
    if len(out) != 9:
        raise ValueError(f"Table {c['number']}: {len(out)} states of charge")
    return out


# --- the figure ---------------------------------------------------------------------------------
def image() -> np.ndarray:
    import matplotlib.image

    return np.round(matplotlib.image.imread(fetch("g006.png"))[..., :3] * 255.0)


def groups(mask: np.ndarray) -> list[np.ndarray]:
    k = np.flatnonzero(mask)
    return np.split(k, np.flatnonzero(np.diff(k) > 1) + 1) if len(k) else []


def ticks(darkness: np.ndarray, expected: int, length: int) -> np.ndarray:
    """Centres of the tick marks: the peaks of the darkness along a frame side, ``expected`` of them, the two
    frame lines included."""
    level = 0.3 * np.sort(darkness)[-max(3, length // 400):].mean()
    found = [g for g in groups(darkness > level)]
    centres = np.array([np.average(g, weights=darkness[g]) for g in found])
    if len(centres) != expected:
        raise ValueError(f"{len(centres)} tick marks found, {expected} expected: {centres}")
    return centres


def axes(img: np.ndarray, panel: dict) -> dict:
    """Pixel positions to axis values, by least squares through the tick marks of the
    bottom side and of the right side (on the left the curves cover two of them)."""
    (r0, r1), (c0, c1) = panel["rows"], panel["cols"]
    dark = 255.0 - img[r0:r1, c0:c1].max(axis=2)
    rows, cols = dark.mean(axis=1), dark.mean(axis=0)
    # The frame: the longest dark lines.
    height, width = dark.shape
    top = int(np.argmax(rows[: height // 2]))
    bottom = height // 2 + int(np.argmax(rows[height // 2 :]))
    left = int(np.argmax(cols[: width // 2]))
    right = width // 2 + int(np.argmax(cols[width // 2 :]))
    # Tick marks are inside the frame, about 16 pixels long: a strip 6 to 12 pixels from the side.
    x_strip = dark[bottom - 12 : bottom - 6, left - 4 : right + 5].mean(axis=0)
    part = (slice(top - 4, bottom + 5), slice(right - 12, right - 6))
    y_strip = np.where(colored(img[r0:r1, c0:c1][part]), 0.0, dark[part])
    px = ticks(x_strip, len(SOC_TICKS), width) + left - 4 + c0
    py = ticks(y_strip.min(axis=1), len(VOLT_TICKS), height) + top - 4 + r0
    fx = np.polyfit(px, SOC_TICKS, 1)
    fy = np.polyfit(py, VOLT_TICKS[::-1], 1)
    return {"soc_per_px": fx[0], "soc0": fx[1], "volt_per_px": fy[0], "volt0": fy[1],
            "tick_px_x": float(np.abs(np.polyval(fx, px) - SOC_TICKS).max() / abs(fx[0])),
            "tick_px_y": float(np.abs(np.polyval(fy, py) - VOLT_TICKS[::-1]).max() / abs(fy[0])),
            "box": (top + r0, bottom + r0, left + c0, right + c0)}


def colored(a: np.ndarray) -> np.ndarray:
    return a.max(axis=-1) - a.min(axis=-1) > 60.0


def curve(img: np.ndarray, ax: dict, color: str) -> dict:
    """The centre of the line of one colour in every pixel column, as volts; NaN where it is hidden."""
    top, bottom, left, right = ax["box"]
    block = img[top + 4 : bottom - 3, left + 4 : right - 3]
    weight = np.maximum(WEIGHTS[color](*np.moveaxis(block, 2, 0)), 0.0) / FULL[color]
    weight[weight < 0.1] = 0.0
    # The legend lies in the lower right; the curves do not.
    h, w = weight.shape
    weight[int(0.70 * h) :, int(0.6 * w) :] = 0.0
    total = weight.sum(axis=0)
    rows = np.arange(h)[:, None] + top + 4
    with np.errstate(invalid="ignore", divide="ignore"):
        centre = np.where(total >= MIN_PX, (weight * rows).sum(axis=0) / total, np.nan)
    x = np.arange(w) + left + 4
    return {"soc": ax["soc_per_px"] * x + ax["soc0"], "v": ax["volt_per_px"] * centre + ax["volt0"], "hidden": int((total < MIN_PX).sum()),
            "columns": w}


def sample(c: dict, grid: np.ndarray) -> np.ndarray:
    """The curve at the grid: the line through the columns near it; at the ends the line of the nearest 8."""
    ok = np.isfinite(c["v"])
    s, v = c["soc"][ok], c["v"][ok]
    out = np.interp(grid, s, v)
    for j, g in enumerate(grid):
        near = np.abs(s - g) <= WINDOW_PX * (c["soc"][1] - c["soc"][0])
        if near.sum() >= 4 and s[0] < g < s[-1]:
            out[j] = np.polyval(np.polyfit(s[near] - g, v[near], 1), 0.0)
    for side, k in ((grid < s[0], slice(0, 8)), (grid > s[-1], slice(-8, None))):
        if side.any():
            out[side] = np.polyval(np.polyfit(s[k], v[k], 1), grid[side])
    return out


def digitise(panel: str) -> dict:
    img = image()
    ax = axes(img, PANELS[panel])
    grid = np.round(np.arange(0, round(1 / STEP) + 1) * STEP, 6)
    read = {name: curve(img, ax, name) for name in WEIGHTS}
    values = {name: sample(read[name], grid) for name in WEIGHTS}
    ocv = monotone(values["average"])
    inside = (grid >= 0.02) & (grid <= 0.98)
    gap = values["average"] - 0.5 * (values["low"] + values["high"])
    half = 0.5 * (values["high"] - values["low"])
    return {"soc": grid, "ocv": ocv, "axes": ax, "hidden": {n: read[n]["hidden"] for n in read},
            "columns": read["average"]["columns"],
            "moved_mv": 1000 * float(np.abs(ocv - values["average"]).max()),
            "check_mean_mv": 1000 * float(gap[inside].mean()), "check_rms_mv": 1000 * float(np.sqrt(np.mean(gap[inside] ** 2))),
            "check_max_mv": 1000 * float(np.abs(gap[inside]).max()), "check_ends_mv": 1000 * float(np.abs(gap[~inside]).max()),
            "half_mean_mv": 1000 * float(half[inside].mean()), "half_max_mv": 1000 * float(half[inside].max())}


# --- the files ---------------------------------------------------------------------------------
def toml_text(name: str, c: dict, cap: float, par: pd.DataFrame, fig: dict) -> str:
    ax = fig["axes"]
    mid = par.set_index("soc").loc[0.5]
    return f"""# One measured {c['chemistry']} cell ({c['cell_name']}, {cap:g} Ah), as a besslib.sim cell.
# Written by validation/sim_params_tran.py; do not edit.
# Source: Tran et al., Batteries 7, 51 (2021), doi:{DOI}, CC BY 4.0 (presets/NOTICE.md).
# Not a model of any product: one cell's published values, read as each comment says. Values are per cell.
name = "{name}"
source = "https://doi.org/{DOI}, tables and figure named at each value"
credit = "Tran et al., Batteries 7, 51 (2021), doi:{DOI}, CC BY 4.0"
notes = "measured at 23 degC; other temperatures follow an activation energy measured on another cell. The open-circuit curve is digitised from the figure"
# Table 5, the nominal capacity.
capacity_ah = {cap:g}

# Open-circuit voltage: digitised from the figure. Figure 6 ({c['panel']}), the average of the C/25 charge and discharge, every 1 %.
# One pixel is {abs(1000 * ax['volt_per_px']):.2f} mV and {100 * ax['soc_per_px']:.3f} % state of charge. Against the mean of the low and high
# curves, read the same way: RMS {fig['check_rms_mv']:.1f} mV, largest {fig['check_max_mv']:.1f} mV (2-98 %), {fig['check_ends_mv']:.0f} mV at the ends.
# The low and high curves lie {fig['half_mean_mv']:.0f} mV on average either side of it.
{wrapped('ocv_soc', fig['soc'], 4)}
{wrapped('ocv_v', fig['ocv'], 5)}

# Resistances and time constant: Table {c['number']}, the 1RC columns R0, R1 and tau1 = R1 * C1, one row at {TEMP_C:g} degC.
# Below 10 % and above 90 % the nearest value holds.
r_soc = {fmt(par['soc'], 3)}
r_temp_c = [{TEMP_C:.1f}]
{rows_text('r0_table', [par['r0']], 5)}
{rows_text('r1_table', [par['r1']], 5)}
{rows_text('tau1_table', [par['r1'] * par['c1']], 6)}

# Activation energies of Ecker et al. 2015 (doi:10.1149/2.0551509jes), measured on their 7.5 Ah NMC/graphite cell, not on
# this one: R0 follows the electrolyte conductivity (17.12 kJ/mol), R1 the graphite exchange current density
# (53.4 kJ/mol). Which resistance follows which is besslib's reading, not Ecker's.
ea_r0_j_mol = {EA_R0:.1f}
ea_r1_j_mol = {EA_R1:.1f}
rc_hold = "capacitance"  # the time constant scales with R1 when the temperature changes it: besslib's choice, as the demo case

# The table at 50 %, for a reader that takes one value; with the tables the model does not use them.
r0_ohm = {mid['r0']:g}
r1_ohm = {mid['r1']:g}
tau1_s = {mid['r1'] * mid['c1']:.6g}
t_ref_c = {TEMP_C:.1f}
"""


def build() -> tuple[dict, pd.DataFrame]:
    texts, rows = {}, []
    for name, c in CELLS.items():
        cap, par, fig = capacity_ah(c), parameters(c), digitise(c["panel"])
        texts[name] = toml_text(name, c, cap, par, fig)

        def add(item, value, unit, where, soc=math.nan):
            rows.append(dict(preset=name, item=item, soc=soc, value=value, unit=unit, where=where))

        figure, ax = f"Figure 6 ({c['panel']})", fig["axes"]
        add("nominal capacity", cap, "Ah", "Table 5")
        for item, value, unit in (
            ("one pixel, voltage", abs(1000 * ax["volt_per_px"]), "mV"), ("one pixel, state of charge", 100 * ax["soc_per_px"], "%"),
            ("largest distance of a tick mark from the fitted axis, state of charge", ax["tick_px_x"], "pixels"),
            ("largest distance of a tick mark from the fitted axis, voltage", ax["tick_px_y"], "pixels"),
            ("pixel columns read", fig["columns"], ""),
            ("pixel columns without a reading, average curve", fig["hidden"]["average"], ""),
            ("pixel columns without a reading, low curve", fig["hidden"]["low"], ""),
            ("pixel columns without a reading, high curve", fig["hidden"]["high"], ""),
            ("largest change made to keep the curve from falling", fig["moved_mv"], "mV"),
            ("average curve less the mean of low and high, 2-98 %, mean", fig["check_mean_mv"], "mV"),
            ("average curve less the mean of low and high, 2-98 %, RMS", fig["check_rms_mv"], "mV"),
            ("average curve less the mean of low and high, 2-98 %, largest", fig["check_max_mv"], "mV"),
            ("average curve less the mean of low and high, below 2 % and above 98 %, largest", fig["check_ends_mv"], "mV"),
            ("half distance of the low and high curves, 2-98 %, mean", fig["half_mean_mv"], "mV"),
            ("half distance of the low and high curves, 2-98 %, largest", fig["half_max_mv"], "mV")):
            add(item, value, unit, figure)
        for r in par.itertuples():
            for item, value, unit in (("r0", r.r0, "ohm"), ("r1", r.r1, "ohm"), ("c1", r.c1, "F"), ("tau1", r.r1 * r.c1, "s")):
                add(item, value, unit, f"Table {c['number']}, 1RC", r.soc)
        for s, v in zip(fig["soc"], fig["ocv"]):
            add("ocv", v, "V", figure + ", average curve", s)
    for f in SOURCES:
        rows.append(dict(preset="both", item=f"sha256 of {f}", soc=math.nan, value=math.nan,
                         unit=hashlib.sha256(fetch(f).read_bytes()).hexdigest()[:16], where=SOURCES[f]))
    return texts, pd.DataFrame(rows)


def main() -> None:
    texts, table = build()
    for name, text in texts.items():
        tomllib.loads(text)
        (PRESETS / f"{name}.toml").write_text(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False, float_format="%.6g")
    print(table[table["soc"].isna()].to_string())


if __name__ == "__main__":
    main()
