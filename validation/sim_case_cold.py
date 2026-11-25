"""Case 4, the cold: A5 per temperature band (T4a, T4b), charging in the cold (T4c), what the cold costs (T4d).

The pack: 16 groups of the LFP cell scaled to 280 Ah, the pack temperature by season from -15 to 25 degrees C,
12 months, no fade and no growth of the resistance. Settings: ``block`` (charging blocked below 0 degrees C) and
``noblock``. With the block on, the winter discharges do not start from full: the charge before them is
blocked, so the constant-power discharge of the 15th starts from whatever the pack holds.

* T4a: at the record's end, every temperature band that has an A5 estimate: the 90 % band holds the true
  one-step response at the band's mean step temperature. Truth: the model's pack ``r0`` (the cell's tables, from
  ``CellModel.resistances``) at the mean temperature and mean state of charge of the band's true steps, weighted
  as the estimator weighs them (``exp(-age / tau_days) dI^2``), times ``1 + m`` with ``m`` the median, over the
  band's true steps, of ``(dV / dI) / r0 - 1`` (``dV / dI`` of the true signals, ``r0`` of the block that
  holds the step, read from the truth's ``cells`` table, every 600 s): the open-circuit and RC part of a
  one-sample step. Steps are those of the reduction of the true signals. Pooled over packs and bands per
  setting. Pass: 85-95 % of band values. Also reported: W2's same-days form (the same estimator on the true
  signals).
* T4b: W4's form: of two neighbouring bands that both have an estimate, the colder reads the higher resistance.
  Pass: at least 80 % of pairs.
* T4c: ``hours_charging_below_temp_low`` with ``Limits(charge_temp_low_c=0)``: equal to the truth from the true
  current (at or above the rest detector's idle current) and the true temperature below 0 degrees C, within
  one step per edge of that condition; 0 in ``block``.
* T4d (information, no besslib in the runs): one pack, charged to its BMS's full stop at that temperature (block off), at -15, -5, 5, 15 and 25 degrees C
  prescribed, a constant 0.5 C of the nominal energy per hour to the BMS's stop: minutes and kWh. Next to them
  the median A5 over the packs in the same bands (their record's end), and A2 where the record gives it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim_case_common as common
import sim_truth

from besslib import sim
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.record import annex_vii
from besslib.soh.reduce import TEMP_BIN_LOW_C, TEMP_BINS
from besslib.soh.resistance import band_centre, estimate_resistance, qualifying_steps
from besslib.soh.usage import Limits, count_usage

CASE = "cold"
SETTINGS = {
    "block": {"overrides": {}, "months": 12},
    "noblock": {"overrides": {"bms.charge_block_below_c": None}, "months": 12},
    "t4d": {"overrides": {}, "months": 1, "first_seed_only": True},
}
RESULT = "cold.csv"
T4D_TEMPS = (-15.0, -5.0, 5.0, 15.0, 25.0)
PASS_A, PASS_B = sim_truth.PASS_RANGE, 0.80


class _Cells:
    """The truth's ``cells`` table, thinned to what T4a reads: time, mean state of charge, pack r0, mean temperature."""

    def __init__(self) -> None:
        self.parts: list[pd.DataFrame] = []

    def month(self, out) -> None:
        c = out.truth.cells
        g = c.groupby("seconds")
        self.parts.append(pd.DataFrame({"soc": g["soc"].mean(), "r0": g["r0_ohm"].sum(), "temp": g["temp_c"].mean()}))

    def table(self) -> pd.DataFrame:
        return pd.concat(self.parts)


class _Charging:
    """Hours charging below the limit, from the true current and temperature, with the tolerance."""

    def __init__(self, idle_a: float) -> None:
        self.idle_a = idle_a
        self.hours = 0.0
        self.edges = 0
        self.step_s = math.nan
        self.last: bool | None = None

    def month(self, out) -> None:
        f = out.true_frame
        self.step_s = float((f.index[1] - f.index[0]).total_seconds())
        on = (f["current_a"].to_numpy(float) >= self.idle_a) & (np.floor(f["temp_pack_c"].to_numpy(float)) + 1.0 <= 0.0)
        counted = on if self.last is not None else on[1:]
        self.hours += float(counted.sum()) * self.step_s / 3600.0
        prev = np.concatenate([[on[0] if self.last is None else self.last], on[:-1]])
        self.edges += int((prev != on).sum())
        self.last = bool(on[-1])


def _true_reduced(setting: str, seed: int):
    cells = _Cells()
    rec = common.recorder(CASE, SETTINGS, setting, seed, signals="true", on_month=cells.month)
    return rec, sim_truth.reduce_pack(rec), cells.table()


def _band_truth(rec, steps: pd.DataFrame, cells: pd.DataFrame, at: pd.Timestamp, tau_days: float) -> dict:
    """Per band centre: the true one-step response, from the true signals' qualifying steps."""
    t = (pd.DatetimeIndex(steps["time"]) - rec.start).total_seconds().to_numpy(float)
    k = np.clip(np.searchsorted(cells.index.to_numpy(float), t, side="right") - 1, 0, len(cells) - 1)
    r0_step = cells["r0"].to_numpy(float)[k]
    soc_step = cells["soc"].to_numpy(float)[k]
    centre = band_centre(steps["temp_pack_c"].to_numpy(float))
    d_i, d_v = steps["d_current_a"].to_numpy(float), steps["d_voltage_v"].to_numpy(float)
    day_end = pd.DatetimeIndex(steps["time"]).normalize() + pd.Timedelta(days=1)
    age = (at - day_end).total_seconds() / 86400.0
    weight = np.exp(-age.to_numpy(float) / tau_days) * d_i**2
    out = {}
    for c in np.unique(centre[np.isfinite(centre)]):
        m = centre == c
        w = weight[m]
        temp = float((w * steps["temp_pack_c"].to_numpy(float)[m]).sum() / w.sum())
        soc = float((w * soc_step[m]).sum() / w.sum())
        r0 = float(rec.pack.cells_in_series * np.asarray(rec.pack.cell.resistances(soc, temp)[0]))
        extra = float(np.median((d_v[m] / d_i[m]) / r0_step[m] - 1.0))
        out[float(c)] = {"mean_temp_c": temp, "mean_soc": soc, "r0_model_ohm": r0, "extra": extra,
                         "truth_ohm": r0 * (1.0 + extra), "n_steps": int(m.sum())}
    return out


def _pairs(table: pd.DataFrame) -> tuple[int, int]:
    ok = table[table["status"] == "estimated"].sort_values("temp_band_c")
    bands, values = ok["temp_band_c"].to_numpy(float), ok["resistance_ohm"].to_numpy(float)
    width = RecordConfig().temp_band_c
    n = held = 0
    for k in range(len(ok) - 1):
        if abs(bands[k + 1] - bands[k] - width) < 1e-9:
            n += 1
            held += int(values[k] > values[k + 1])
    return n, held


def run_pack(setting: str, seed: int) -> list[dict]:
    if setting == "t4d":
        return t4d_rows(seed)
    config = RecordConfig()
    idle = CapacityConfig().current_c_rate * 280.0
    charging = _Charging(idle)
    rec = common.recorder(CASE, SETTINGS, setting, seed, on_month=charging.month)
    reduced = sim_truth.reduce_pack(rec)
    end = rec.start + pd.DateOffset(months=rec.months)
    at = pd.DatetimeIndex([end])
    est = estimate_resistance(None, rec.spec, reduced=reduced, at=at)
    rec_true, reduced_true, cells = _true_reduced(setting, seed)
    est_true = estimate_resistance(None, rec.spec, reduced=reduced_true, at=at)
    steps = qualifying_steps(reduced_true.steps, rec.spec, config)
    truth = _band_truth(rec_true, steps, cells, end, config.tau_days)
    usage = count_usage(None, rec.spec, Limits(charge_temp_low_c=0.0), reduced=reduced)
    n_pairs, n_colder = _pairs(est)
    a2 = annex_vii(None, rec.spec, reduced=reduced, at=end).field("A2")
    below = float(usage.hours_charging_below_temp_low)
    tol = charging.edges * charging.step_s / 3600.0 + 1e-9
    rows = [{
        **common.base_row(setting, seed), "row_kind": "pack",
        "charging_below_besslib_h": below, "charging_below_true_h": charging.hours, "charging_below_tol_h": tol,
        "t4c_ok": bool(abs(below - charging.hours) <= tol and (setting != "block" or (below == 0.0 and charging.hours == 0.0))),
        "pairs": n_pairs, "colder_higher": n_colder,
        "a2_value": float(a2.value) if a2.status == "estimated" else float("nan"), "a2_status": a2.status,
    }]
    for _, r in est.iterrows():
        if r["status"] != "estimated" or r["temp_band_c"] not in truth:
            continue
        t = truth[r["temp_band_c"]]
        w2 = est_true[(est_true["temp_band_c"] == r["temp_band_c"]) & (est_true["status"] == "estimated")]
        w2_value = float(w2["resistance_ohm"].iloc[0]) if len(w2) else float("nan")
        rows.append({
            **common.base_row(setting, seed), "row_kind": "band", "band_c": float(r["temp_band_c"]),
            "resistance_ohm": float(r["resistance_ohm"]), "low_ohm": float(r["resistance_ohm_low"]),
            "high_ohm": float(r["resistance_ohm_high"]), "truth_ohm": t["truth_ohm"], "r0_model_ohm": t["r0_model_ohm"],
            "extra": t["extra"], "mean_temp_c": t["mean_temp_c"], "mean_soc": t["mean_soc"], "n_true_steps": t["n_steps"],
            "held": bool(r["resistance_ohm_low"] <= t["truth_ohm"] <= r["resistance_ohm_high"]),
            "held_same_days": bool(r["resistance_ohm_low"] <= w2_value <= r["resistance_ohm_high"]) if math.isfinite(w2_value) else False,
        })
    return rows


def t4d_rows(seed: int) -> list[dict]:
    """One discharge at 0.5 C of the nominal energy per hour from full at each temperature (no besslib)."""
    rows = []
    for temp in T4D_TEMPS:
        over = {"temperature.pack": {"mean_c": temp}, "pack.capacity_spread_sd": 0.0, "bms.stop_sigma_v": 0.0,
                "bms.charge_block_below_c": None, "duty.overlay": [{**sim.load_case(CASE)["duty"]["overlay"][0],
                                                                    "day_of_month": 0, "days": [0]}]}
        data = sim.load_case(CASE, over)
        out = next(iter(sim.run(data, 1, seed, measured=False, log_blocks=False)))
        t = out.truth
        stops = t.stops[(t.stops["kind"] == "empty") & (t.stops["tag"] == "cp-discharge")]
        row = {**common.base_row("t4d", seed), "row_kind": "t4d", "band_c": temp, "minutes": float("nan"),
               "energy_kwh": float("nan")}
        if len(stops):
            te = float(stops["seconds"].iloc[0])
            k_end = int(round((te - t.seconds[0]) / (t.seconds[1] - t.seconds[0])))
            cur = t.current_a
            k = k_end - 1
            while k >= 0 and cur[k] < 0:
                k -= 1
            seg = slice(k + 1, k_end)
            dt = float(t.seconds[1] - t.seconds[0])
            row["minutes"] = (k_end - (k + 1)) * dt / 60.0
            row["energy_kwh"] = float(-(t.voltage_v[seg] * cur[seg]).sum() * dt / 3.6e6)
        rows.append(row)
    return rows


def summary(table: pd.DataFrame) -> list[dict]:
    rows = []
    bands = table[table.get("row_kind") == "band"] if "row_kind" in table else table.iloc[0:0]
    packs = table[table.get("row_kind") == "pack"] if "row_kind" in table else table.iloc[0:0]
    for setting in ("block", "noblock"):
        b, p = bands[bands["setting"] == setting], packs[packs["setting"] == setting]
        if not len(p):
            continue
        n, h = len(b), int(b["held"].sum())
        share = common.fraction(h, n)
        w2 = int(b["held_same_days"].sum())
        rows.append({
            "check": "T4a", "what": f"{setting}: the A5 band holds the true one-step response at the band's mean step "
                                    "temperature, bands with an estimate at the record's end pooled over packs; "
                                    "W2's same-days form next to it",
            "result": f"{h} of {n} ({share:.1%}); same-days form {w2} of {n} ({common.fraction(w2, n):.1%})",
            "pass_if": "85-95 % of band values", "passed": bool(n and PASS_A[0] <= share <= PASS_A[1])})
        pairs, colder = int(p["pairs"].sum()), int(p["colder_higher"].sum())
        rows.append({
            "check": "T4b", "what": f"{setting}: neighbouring bands with estimates at the record's end: the colder reads higher",
            "result": f"{colder} of {pairs} pairs ({common.fraction(colder, pairs):.1%})",
            "pass_if": "at least 80 % of pairs", "passed": bool(pairs and colder / pairs >= PASS_B)})
        ok = int(p["t4c_ok"].sum())
        rows.append({
            "check": "T4c", "what": f"{setting}: B4 hours charging below 0 degrees C against the true current and "
                                    "temperature, within one step per edge" + ("; 0 with the block" if setting == "block" else ""),
            "result": f"{ok} of {len(p)} packs equal; besslib median {p['charging_below_besslib_h'].median():.1f} h, "
                      f"truth median {p['charging_below_true_h'].median():.1f} h",
            "pass_if": "all packs", "passed": bool(ok == len(p))})
    t4d = table[table["setting"] == "t4d"] if "setting" in table else table.iloc[0:0]
    if len(t4d):
        parts = []
        for _, r in t4d.sort_values("band_c").iterrows():
            a5 = bands[(bands["band_c"] == r["band_c"]) & (bands["setting"] == "noblock")]["resistance_ohm"]
            a5_text = f"A5 median {a5.median() * 1000:.2f} mOhm ({len(a5)} packs)" if len(a5) else "A5 not estimated in this band"
            parts.append(f"{r['band_c']:+.0f} C: {r['minutes']:.0f} min, {r['energy_kwh']:.2f} kWh, {a5_text}")
        a2 = packs[packs["setting"] == "noblock"]
        a2_text = (f"A2 estimated in {int(a2['a2_value'].notna().sum())} of {len(a2)} packs"
                   + (f", median {a2['a2_value'].median():.3f}" if a2["a2_value"].notna().any() else ""))
        rows.append(common.info_row(
            "T4d", "constant power 0.5 C of the nominal energy per hour from full to the BMS stop, one pack with the "
                   "spread off (no besslib), next to besslib's A5 (noblock packs, same band) and A2 (a record's single ratio)",
            "; ".join(parts) + f"; {a2_text}"))
    return rows
