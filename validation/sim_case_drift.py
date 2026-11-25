"""Case 6, a drifting BMS stop: the field statistic (S9) and what it does to A1 (T6a-T6d).

The pack: 40 LMO cells of 15 Ah in series (``cases/drifting-stop.toml``), home duty, 72 months, fade 2 points
a year. The discharge stop falls linearly from the end of month 12 to the end of month 72, so that the true relaxed
empty stop voltage falls by D mV per cell (settings ``D0``, ``D50``, ``D100``, ``D157``: the range of the field's
empty rest voltages, docs/04); the full stop stays. The relaxed voltage falls less than the limit, because the resistance
is higher at a lower state of charge; ``LIMIT_FALLS_MV`` holds the limit's fall for each D.
Information: ``D157-steps`` (the same fall of the limit in three equal steps on random days), ``D100-growth`` (``D100`` with
resistance +5 % a year), ``D0-c10`` and ``D157-c10`` (the tests at C/10, ending at the operational stop: the lower test
stop below was measured at about 0.84 C, and at C/10 it only adds charge below the operational stop; with the charge the
tests take 18 hours and fit into their day), ``D0-onestop`` and ``D157-onestop`` (the tests end at the operational stop).

Capacity tests on the 15th of months 18, 30, 42, 54 and 66: full charge, one hour of rest, discharge to the stop at the
median C-rate of the LMO/NMC field tests (``capacity_tests.csv``; the case file holds it, ``setup`` reads it again). The test's stop is the field tests' median lowest
voltage per cell (same file); it lies below the operational stop by a fixed offset, so the drift moves it too.

Two parts of this set-up were changed after runs on development seeds, before any held-out run: the test's stop (with
one stop for operation and test the rate term, 16.5 points, hid the drift; the ``onestop`` settings keep that
version, and the C/10 settings end there too) and the limit's fall (it had been D itself, and the relaxed voltage fell
by less).

Each is measured by ``soh.measure_capacity_test`` on the measured frame around it. That code needs 1 s samples as
the field data has; the 10 s samples are held for ten seconds each (the true current is constant within a step).
Each test is masked from one day before it begins to one day after it ends, and the estimate is the one at the
start of the mask, as ``run_capacity.py`` does with the field tests.

The truths at each test (no sensor error; charge in Ah):

* ``t_test``: the true terminal charge between the full stop and the empty stop of the test;
* ``t_ops``: the mean of the two operational stretches nearest the test, one from a full stop to the next empty
  stop and one the other way (the BMS supply added back; stops inside a mask left out);
* ``t_rest``: the charge between the two relaxed voltages the estimate reads at (``ref_full_v``, ``ref_empty_v``,
  read through the pack's own meter: ``sim_truth.Recorder.true_volts``);
* ``t_fixed``: the charge between the mean relaxed voltages of the first year's stops (the open-circuit voltage at the
  true state of charge of each stop);
* ``c_total``: state of charge 0 to 1 of the curve.

Checks (pass numbers fixed before any result):

Beside S9 stands the true fall: the line through the relaxed voltages of the operational empty stops of months 13-72,
at the end of month 72, less the mean of the first year's (with ``D157-steps`` the line only approximates the steps).

* S9: the docs/04 statistic (first and last month end with an empty rest voltage, ``ref_empty_v`` / cells) gives -D within
  10 mV, on at least 95 % of the packs, at each D.
* T6a: D = 0, the 0.2 band holds ``t_rest`` at 85-95 % of month ends with a band, mean error within 2 sd / sqrt(n).
* T6b: at each D, the test value lies in the 0.2 band at the test date, at least 80 % of tests. A test without a band counts as
  not held.
* T6c: estimate less test value in points of the nominal capacity: the median over the packs (of each pack's median over its
  tests) with its 90 % range, and the line a + b D through the per-pack medians (least squares), with 90 % bands from a
  bootstrap over packs (a pack's seeds are the same at each D and are drawn together).
* T6d (information): (estimate - t_rest) + (t_rest - t_ops) + (t_ops - t_test), the three medians at each D; the first is the
  estimator, the second the drift, the third the rate.

Usage:
    python validation/sim_case_drift.py setup     # the first-year rest voltages and the test rate, read from the field files;
                                                  # the BMS limits that bring the pack there and the limit's falls,
                                                  # on development seed 0, from the truth alone (besslib is not run)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import sim_truth
from besslib import sim
from besslib.sim.duty import DAY_S, Overlay
from besslib.soh.capacity import estimate_capacity
from besslib.soh.capacity_test import measure_capacity_test
from besslib.soh.config import CapacityConfig
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs

ROOT = Path(__file__).resolve().parents[1]
CASE = "drifting-stop"
RESULT = "drift.csv"
MONTHS = 72
TEST_MONTHS = (18, 30, 42, 54, 66)  # the 15th
DRIFTS = {"D0": 0.0, "D50": 50.0, "D100": 100.0, "D157": 157.0}  # mV per cell
DRIFT_DAYS = (366.0, 2192.0)  # the ends of months 12 and 72 (a run from 1 January 2020)
# The fall of the discharge limit (mV per cell) that makes the true relaxed empty stop voltage fall by D: found to 1 mV by
# `setup` on development seed 0, from the truth alone.
LIMIT_FALLS_MV = {0.0: 0.0, 50.0: 56.0, 100.0: 109.0, 157.0: 167.0}
S9_TOLERANCE_MV, S9_SHARE, T6B_SHARE = 10.0, 0.95, 0.80
BOOTSTRAP, BOOTSTRAP_SEED = 2000, 17


def _limit(fall_mv: float) -> dict:
    """The discharge stop's points: level to the end of month 12, then a line to the end of month 72."""
    if fall_mv == 0.0:
        return {}
    return {"bms.v_min_points": [[0.0, 0.0], [DRIFT_DAYS[0], 0.0], [DRIFT_DAYS[1], -fall_mv / 1000.0]]}


def _drift(mv: float) -> dict:
    """The limit whose fall makes the relaxed empty voltage fall by ``mv``."""
    return _limit(LIMIT_FALLS_MV[mv])


def _tests(**changes) -> dict:
    """The case's capacity-test overlay with some settings replaced."""
    return {"duty.overlay": [{**sim.load_case(CASE)["duty"]["overlay"][0], **changes}]}


SETTINGS = {
    **{name: {"overrides": _drift(mv), "months": MONTHS, "drift_mv": mv} for name, mv in DRIFTS.items()},
    "D157-steps": {"overrides": {"event": [{"kind": "stop_steps", "total_v": LIMIT_FALLS_MV[157.0] / 1000.0, "steps": 3, "from_month": 13, "to_month": 72}]},
                   "months": MONTHS, "drift_mv": 157.0},
    "D100-growth": {"overrides": {**_drift(100.0), "pack.resistance_growth_per_year": 0.05}, "months": MONTHS, "drift_mv": 100.0},
    "D0-c10": {"overrides": _tests(c_discharge=0.1, v_min_offset_cell=0.0), "months": MONTHS, "drift_mv": 0.0},
    "D157-c10": {"overrides": {**_drift(157.0), **_tests(c_discharge=0.1, v_min_offset_cell=0.0)}, "months": MONTHS, "drift_mv": 157.0},
    "D0-onestop": {"overrides": _tests(v_min_offset_cell=0.0), "months": MONTHS, "drift_mv": 0.0},
    "D157-onestop": {"overrides": {**_drift(157.0), **_tests(v_min_offset_cell=0.0)}, "months": MONTHS, "drift_mv": 157.0},
}
MAIN = tuple(DRIFTS)


# --- one pack ----------------------------------------------------------------------------------
def test_starts(data: dict) -> list[float]:
    """The seconds at which each planned test discharge begins."""
    raw = dict(data["duty"]["overlay"][0])
    days = raw.pop("days")
    o = Overlay(**{"capacity_ah": data["duty"]["capacity_ah"], **raw, "days": tuple(days)})
    return [o.pieces(d, float(data["case"]["step_s"]))[2][2].start_s for d in days]


def held_1s(frame: pd.DataFrame) -> pd.DataFrame:
    """The frame at 1 s, each sample held to the next."""
    index = pd.date_range(frame.index[0], frame.index[-1], freq="1s", name=frame.index.name)
    return frame.reindex(index, method="ffill")


def relaxed(rec: sim_truth.Recorder, soc) -> np.ndarray:
    """The relaxed pack voltage at these states of charge (one group; the cell has no hysteresis)."""
    return rec.ocv(soc) * rec.pack.cells_in_series


def charge_between(rec: sim_truth.Recorder, seconds: float, full_v: float, empty_v: float) -> float:
    """Ah between two true relaxed pack voltages at a time. The pack is one group: its state of charge then does not matter."""
    cap = rec._capacity(seconds)
    at = np.full(1, 0.5)
    return rec.charge_at(at, cap, full_v, +1.0) - rec.charge_at(at, cap, empty_v, -1.0)


def stretches(rec: sim_truth.Recorder, masks) -> pd.DataFrame:
    """The charge between consecutive operational stops of different kinds: ``time`` (middle), ``kind``, ``ah``."""
    s = rec.stops
    s = s[s["first"]].reset_index(drop=True)
    sec, q, kind = s["seconds"].to_numpy(float), s["q_ah"].to_numpy(float), s["kind"].to_numpy()
    ah = np.abs(np.diff(q) - rec.pack.bms.supply_a * np.diff(sec) / 3600.0)
    out = pd.DataFrame({"t0": s["time"].iloc[:-1].to_numpy(), "t1": s["time"].iloc[1:].to_numpy(), "kind": kind[:-1], "ah": ah})
    keep = np.ones(len(out), dtype=bool)
    for a, b in masks:
        keep &= ~((out["t1"] >= a) & (out["t0"] <= b)).to_numpy()
    out = out[keep].reset_index(drop=True)
    out["time"] = out["t0"] + (out["t1"] - out["t0"]) / 2
    return out


def nearest_ops(table: pd.DataFrame, time: pd.Timestamp) -> float:
    out = []
    for kind in ("full", "empty"):
        t = table[table["kind"] == kind]
        if len(t):
            out.append(float(t["ah"].iloc[int((t["time"] - time).abs().argmin())]))
    return float(np.mean(out)) if out else float("nan")


def true_test(rec: sim_truth.Recorder, start_s: float, hours: float) -> float:
    """The true terminal charge from the full stop before the test discharge to its empty stop."""
    s = rec.stops
    sec = s["seconds"].to_numpy(float)
    before = s[(s["kind"] == "full") & (sec <= start_s) & (sec >= start_s - DAY_S)]
    after = s[(s["kind"] == "empty") & (sec > start_s) & (sec <= start_s + hours * 3600.0)]
    if not len(before) or not len(after):
        return float("nan")
    return float(before["q_ah"].iloc[-1] - after["q_ah"].iloc[0])


def true_fall(stops: pd.DataFrame, volts) -> tuple[float, float]:
    """The first year's mean relaxed voltage at the operational empty stops, and its change (V) to the end of month 72
    on the line through months 13-72. ``stops`` with a column ``first``; ``volts``: state of charge to volts."""
    e = stops[stops["first"] & (stops["kind"] == "empty") & (stops["tag"] != "capacity-test")]
    sec, v = e["seconds"].to_numpy(float), np.asarray(volts(e["soc"].to_numpy(float)))
    early, t1 = sec < DRIFT_DAYS[0] * DAY_S, DRIFT_DAYS[1] * DAY_S
    year1 = float(v[early].mean())
    slope, at_end = np.polyfit(sec[~early] - t1, v[~early], 1)
    return year1, float(at_end - year1)


def rounded(row: dict) -> dict:
    """Floats with six significant digits."""
    return {k: float(f"{v:.6g}") if isinstance(v, float) else v for k, v in row.items()}


def run_pack(setting: str, seed: int) -> list[dict]:
    cfg = SETTINGS[setting]
    frames = {}

    def keep(out) -> None:
        for k, s in enumerate(starts):
            t = rec.start + pd.Timedelta(seconds=s)
            part = out.frame.loc[t - pd.Timedelta(hours=26) : t + pd.Timedelta(hours=14)]
            if len(part):
                frames[k] = pd.concat([frames[k], part]) if k in frames else part

    rec = sim_truth.Recorder(CASE, cfg["months"], seed, cfg["overrides"], on_month=keep)
    starts = test_starts(rec.data)
    reduced = sim_truth.reduce_pack(rec)
    spec, nominal = rec.spec, rec.spec.nominal_capacity_ah

    # The tests, as besslib measures them, and their masks (as run_capacity.py's masks_for).
    tests, masks = [], []
    day = pd.Timedelta(days=1)
    for k, s in enumerate(starts):
        at = rec.start + pd.Timedelta(seconds=s)
        m = measure_capacity_test(held_1s(frames[k]), spec, at)
        tests.append(m)
        ok = m.onset is not None and m.end is not None
        masks.append((m.onset - day, m.end + day) if ok else (at - day, at + pd.Timedelta(hours=12) + day))

    config = CapacityConfig()
    rests = operating_rests(reduced.rests, spec, config, mask=masks)
    pairs = rest_pairs(rest_chain(rests, spec, config, mask=masks), config)
    ready = levels_from(rests, config)
    monthly = estimate_capacity(None, spec, config=config, pairs=pairs, levels_from=ready)
    at_tests = estimate_capacity(None, spec, config=config, pairs=pairs, levels_from=ready, at=[m[0] for m in masks])

    # The first year's relaxed stop voltages (true).
    stops = rec.stops
    year1 = stops[(stops["seconds"] < DRIFT_DAYS[0] * DAY_S) & stops["first"]]
    full1, empty1 = (float(relaxed(rec, year1.loc[year1["kind"] == k, "soc"].to_numpy(float)).mean()) for k in ("full", "empty"))
    ops = stretches(rec, masks)

    base = {"setting": setting, "seed": int(seed), **sim_truth.tags()}
    rows = []
    for k, (m, s) in enumerate(zip(tests, starts)):
        e = at_tests.iloc[k]
        time = rec.start + pd.Timedelta(seconds=s)
        band = sim_truth.has_band(e)
        rows.append({
            **base, "row": "test", "month": TEST_MONTHS[k], "test_status": m.status, "has_band": int(band),
            "held": int(bool(band and e["capacity_ah_low"] <= m.capacity_ah <= e["capacity_ah_high"])),
            "estimate_ah": float(e["capacity_ah"]), "test_ah": float(m.capacity_ah),
            "t_test": true_test(rec, s, 14.0), "t_ops": nearest_ops(ops, time),
            "t_rest": charge_between(rec, s, rec.true_volts(float(e["ref_full_v"])), rec.true_volts(float(e["ref_empty_v"]))),
            "t_fixed": charge_between(rec, s, full1, empty1), "c_total": float(rec._capacity(s)[0]),
        })

    # The month ends: T6a's counts and S9's statistic.
    n = held = no_truth = without = 0
    err = []
    for time, e in monthly.iterrows():
        if not sim_truth.has_band(e):
            without += int(time >= ready)
            continue
        truth = charge_between(rec, (time - rec.start).total_seconds(), rec.true_volts(float(e["ref_full_v"])),
                               rec.true_volts(float(e["ref_empty_v"])))
        if not np.isfinite(truth):
            no_truth += 1
            continue
        n += 1
        held += int(e["capacity_ah_low"] <= truth <= e["capacity_ah_high"])
        err.append(float(e["capacity_ah"]) - truth)
    est = monthly[monthly["status"] == sim_truth.OK].dropna(subset=["ref_empty_v"])
    v = est["ref_empty_v"].to_numpy(float) / spec.cells_in_series
    _, fall = true_fall(stops, rec.ocv)
    rows.append({
        **base, "row": "pack", "month_ends": n, "month_ends_held": held, "month_ends_without_truth": no_truth,
        "month_ends_without_band": without, "mean_error_ah": float(np.mean(err)) if err else float("nan"),
        "empty_drift_mv": 1000.0 * float(v[-1] - v[0]) if len(v) > 1 else float("nan"),
        "true_fall_mv": 1000.0 * fall,
        "median_band_points": float(100.0 * (monthly["capacity_ah_high"] - monthly["capacity_ah_low"]).median() / nominal),
        "nominal_ah": float(nominal),
    })
    return [rounded(r) for r in rows]


# --- the checks --------------------------------------------------------------------------------
def _pct(a: np.ndarray, q) -> list[float]:
    return [float(x) for x in np.percentile(a, q)]


def _medians(tests: pd.DataFrame, column: str) -> pd.Series:
    """Per pack, the median over its tests."""
    return tests.groupby("seed")[column].median().dropna()


def line_fit(per_pack: dict[float, pd.Series]) -> dict:
    """a (points) and b (points per 10 mV per cell) of the line through the per-pack medians against D, with 90 % bands."""
    table = pd.DataFrame(per_pack).dropna()
    d = np.array(table.columns, dtype=float) / 10.0
    y = table.to_numpy(float)

    def fit(m: np.ndarray) -> np.ndarray:  # least squares through every pack's medians = through their means per D
        return np.polyfit(d, m, 1)

    b, a = fit(y.mean(axis=0))
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = np.array([fit(y[rng.integers(0, len(y), len(y))].mean(axis=0)) for _ in range(BOOTSTRAP)])
    return {"a": float(a), "b": float(b), "a_band": _pct(draws[:, 1], (5, 95)), "b_band": _pct(draws[:, 0], (5, 95)), "packs": len(y)}


def _row(check: str, what: str, result: str, pass_if: str = "information", passed="") -> dict:
    return {"check": check, "what": what, "result": result, "pass_if": pass_if, "passed": passed}


def summary(table: pd.DataFrame) -> list[dict]:
    packs = table[table["row"] == "pack"]
    tests = table[table["row"] == "test"].copy()
    nominal = float(packs["nominal_ah"].iloc[0])
    points = 100.0 / nominal
    tests["miss"] = (tests["estimate_ah"] - tests["test_ah"]) * points
    tests["term_estimator"] = (tests["estimate_ah"] - tests["t_rest"]) * points
    tests["term_drift"] = (tests["t_rest"] - tests["t_ops"]) * points
    tests["term_rate"] = (tests["t_ops"] - tests["t_test"]) * points
    present = [s for s in SETTINGS if (packs["setting"] == s).any()]
    rows = []

    for s in present:
        p = packs[packs["setting"] == s]
        mv = SETTINGS[s]["drift_mv"]
        e = p["empty_drift_mv"].to_numpy(float)
        off = np.abs(e + mv) <= S9_TOLERANCE_MV
        good = sim_truth.share(int(off.sum()), len(p))
        main = s in MAIN
        rows.append(_row("S9", f"{s}: first to last month-end empty rest voltage per cell (the docs/04 rule) against -{mv:.0f} mV",
                         f"{int(off.sum())} of {len(p)} packs within {S9_TOLERANCE_MV:.0f} mV ({good:.1%}); median {np.nanmedian(e):+.1f} mV, "
                         f"5-95 % {np.nanpercentile(e, 5):+.1f} to {np.nanpercentile(e, 95):+.1f}; true fall of the relaxed "
                         f"empty stop voltage, year 1 to the end of month 72 on its line: {p['true_fall_mv'].median():+.1f} mV (median)",
                         f">= {S9_SHARE:.0%} of packs within {S9_TOLERANCE_MV:.0f} mV" if main else "information",
                         bool(good >= S9_SHARE) if main else ""))
    for s in present:
        p = packs[packs["setting"] == s]
        row = sim_truth.coverage_row("T6a", f"{s}: the A1 band holds T_rest at the month end", int(p["month_ends_held"].sum()),
                                     int(p["month_ends"].sum()), p["mean_error_ah"].to_numpy(float), "Ah",
                                     extra=f"{int(p['month_ends_without_band'].sum())} month ends after the first year without a band, "
                                           f"{int(p['month_ends_without_truth'].sum())} with a band but without a reading voltage; "
                                           f"median band width {p['median_band_points'].median():.2f} points of nominal")
        if s != "D0":
            row.update(pass_if="information", passed="")
        rows.append(row)
    for s in present:
        t = tests[tests["setting"] == s]
        held = int(t["held"].sum())
        sh = sim_truth.share(held, len(t))
        bad = int((t["test_status"] != "ok").sum())
        main = s in MAIN
        rows.append(_row("T6b", f"{s}: the test value lies in the A1 band at the test date",
                         f"{held} of {len(t)} tests ({sh:.1%}); {int((t['has_band'] == 0).sum())} without a band, {bad} tests not measured",
                         f">= {T6B_SHARE:.0%} of tests" if main else "information", bool(sh >= T6B_SHARE) if main else ""))
    for s in present:
        t = tests[tests["setting"] == s]
        m = _medians(t, "miss").to_numpy(float)
        lo, hi = _pct(m, (5, 95))
        rows.append(_row("T6c", f"{s}: estimate less test value, points of nominal, per-pack medians over the tests",
                         f"median {np.median(m):+.2f}, 90 % range {lo:+.2f} to {hi:+.2f} ({len(m)} packs)"))
    if all(s in present for s in MAIN):
        fit = line_fit({DRIFTS[s]: _medians(tests[tests["setting"] == s], "miss") for s in MAIN})
        rows.append(_row("T6c", "the line miss = a + b D through the per-pack medians of the four main settings",
                         f"a {fit['a']:+.2f} points (90 % band {fit['a_band'][0]:+.2f} to {fit['a_band'][1]:+.2f}); b {fit['b']:+.3f} points per "
                         f"10 mV per cell ({fit['b_band'][0]:+.3f} to {fit['b_band'][1]:+.3f}); {fit['packs']} packs, bootstrap over packs"))
    for s in present:
        t = tests[tests["setting"] == s]
        rows.append(_row("T6d", f"{s}: where the miss comes from, points of nominal, medians over the tests",
                         f"estimate - T_rest {t['term_estimator'].median():+.2f} (estimator); T_rest - T_ops {t['term_drift'].median():+.2f} (drift); "
                         f"T_ops - T_test {t['term_rate'].median():+.2f} (rate); their sum per test against estimate - T_test: "
                         f"{(t['term_estimator'] + t['term_drift'] + t['term_rate']).median():+.2f}; estimate - test value {t['miss'].median():+.2f}; "
                         f"T_fixed - T_rest {((t['t_fixed'] - t['t_rest']) * points).median():+.2f}; "
                         f"measured test less T_test {((t['test_ah'] - t['t_test']) * points).median():+.2f}"))
    return rows


# --- the case file's numbers, from the field files -------------------------------------------------
def field_targets() -> dict:
    """The first-year mean rest voltages per cell of the development LMO/NMC systems, the median test C-rate and
    the median lowest voltage per cell of the LMO/NMC tests."""
    from besslib.io import figgener
    from tune_capacity import DEVELOPMENT

    full, empty, used = [], [], []
    for sid in DEVELOPMENT:
        spec = figgener.pack_spec(sid)
        if spec.chemistry != "LMO/NMC":
            continue
        m = pd.read_csv(ROOT / "validation" / "results" / "phase2" / f"system_{sid:02d}_monthly.csv", parse_dates=["time"])
        m = m[m["status"] == "estimated"]
        year = m[m["time"] < m["time"].iloc[0] + pd.DateOffset(years=1)]
        empty.append(float(year["ref_empty_v"].mean()) / spec.cells_in_series)
        full.append(float(year["ref_full_v"].mean()) / spec.cells_in_series)
        used.append(f"{sid:02d}")
    t = pd.read_csv(ROOT / "validation" / "results" / "capacity_tests.csv")
    t = t[(t["chemistry"] == "LMO/NMC") & (t["status"] == "ok")]
    return {"systems": used, "empty_cell_v": float(np.mean(empty)), "full_cell_v": float(np.mean(full)),
            "c_rate": float(t["test_c_rate"].median()), "tests": len(t),
            "lowest_cell_v": float((t["voltage_min_v"] / t["system_id"].map(lambda i: figgener.pack_spec(i).cells_in_series)).median())}


def year1_voltages(v_max: float, v_min: float, seed: int = 0) -> tuple[float, float]:
    """The mean relaxed cell voltage at the full and the empty stops of the first year, no drift, without besslib."""
    over = {"bms.v_cell_max": v_max, "bms.v_cell_min": v_min}
    data = sim.load_case(CASE, over)
    pack, _ = sim.build(data, seed)
    stops = pd.concat([m.truth.stops for m in sim.run(data, 12, seed, measured=False, log_blocks=False)])
    stops = stops[stops["kind"] != stops["kind"].shift()]
    ocv = np.interp(stops["soc"], pack.cell.ocv_soc, pack.cell.ocv_v)
    return tuple(float(ocv[(stops["kind"] == k).to_numpy()].mean()) for k in ("full", "empty"))


def true_fall_at(limit_fall_mv: float, seed: int = 0) -> float:
    """The true fall (mV per cell) of the relaxed empty stop voltage for a fall of the limit, without besslib."""
    data = sim.load_case(CASE, _limit(limit_fall_mv))
    pack, _ = sim.build(data, seed)
    stops = pd.concat([m.truth.stops for m in sim.run(data, MONTHS, seed, measured=False, log_blocks=False)], ignore_index=True)
    stops = stops[stops["kind"].isin(["full", "empty"])].reset_index(drop=True)
    stops["first"] = stops["kind"] != stops["kind"].shift()
    return 1000.0 * true_fall(stops, lambda soc: np.interp(soc, pack.cell.ocv_soc, pack.cell.ocv_v))[1]


def find_limit_fall(mv: float) -> float:
    """The limit's fall, to 1 mV, whose true fall is nearest to ``mv``: secant steps, then the neighbours."""
    tried = {}

    def off(fall: float) -> float:
        if fall not in tried:
            tried[fall] = true_fall_at(fall) + mv
            print(f"D {mv:.0f} mV: limit falls {fall:.0f} mV, true relaxed empty voltage {tried[fall] - mv:+.1f} mV", flush=True)
        return tried[fall]

    fall = round(1.06 * mv)
    for _ in range(6):
        step = round(off(fall) * 1.06)  # still short by this much (positive) or too far
        if step == 0 or fall + step in tried:
            break
        fall += step
    for near in (fall - 1, fall + 1):
        off(near)
    return min(tried, key=lambda f: abs(tried[f]))


def setup() -> None:
    target = field_targets()
    print(target)
    data = sim.load_case(CASE)
    hi, lo = data["bms"]["v_cell_max"], data["bms"]["v_cell_min"]
    for _ in range(4):
        full, empty = year1_voltages(hi, lo)
        print(f"limits {hi:.3f} / {lo:.3f} V: year-1 relaxed full {full:.4f}, empty {empty:.4f} V per cell")
        hi, lo = round(hi + target["full_cell_v"] - full, 2), round(lo + target["empty_cell_v"] - empty, 2)
    print(f"limits rounded to 10 mV: {hi:.2f} / {lo:.2f} V; the case file has {data['bms']['v_cell_max']} / {data['bms']['v_cell_min']}, "
          f"c_discharge {data['duty']['overlay'][0]['c_discharge']}, test stop "
          f"{data['bms']['v_cell_min'] - data['duty']['overlay'][0]['v_min_offset_cell']:.3f} V per cell")
    print("limit falls found:", {mv: find_limit_fall(mv) for mv in DRIFTS.values() if mv}, "the module has", LIMIT_FALLS_MV)


if __name__ == "__main__":
    if sys.argv[1:] == ["setup"]:
        setup()
    else:
        print(__doc__)
