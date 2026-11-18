"""Phase 3: the checks W1-W10, from the result files of the Annex VII run.

Rules fixed before any result:

* W1, W3, W4 and W6 pass or fail on the 14 held-out systems. The development systems
  and all 21 together are information lines.
* W1 counts the fields of a record that are neither a value with a band and an event
  count nor ``not possible from this data`` with a reason, and the fields A2-A5 and
  B2-B5 that say ``estimated`` with an impossible value (an efficiency above 1 or at or
  below 0, a resistance or a power share at or below 0, a negative throughput or cycle
  count, a band that does not hold its value). Pass: 0.
* W2, W5, W7 and W8 come from the synthetic packs (``synthetic_phase3.py``): the 90 %
  band must hold the truth in 85-95 % of the packs; a pack without a band counts as not
  held. W8: Ah, kWh and cycles within 0.6 % of the truth and the planted events counted
  exactly, on every pack.
* W3 compares the resistance from operation (tests masked) with the same estimator on
  the first minute of each capacity test (``onset_resistance.py``): the median absolute
  difference in % of the test's value. A test without an operating estimate counts as
  infinite. A test whose first minute holds no qualifying step has no reference and is
  left out; with fewer than 30 of 38 references W3 is ``not possible``.
* W4: at every year end, every two neighbouring temperature bands that both have an
  estimate form a pair; the colder band must read the higher resistance in at least
  80 % of the pairs.
* W6: at every year end with an estimate from full-to-full loops and one from
  empty-to-empty loops, the two must agree within 1 point in at least 80 % of
  system-years.
* W9 has two parts. (a) The step-by-step core against the batch path, for resistance
  and for efficiency, at every day of three development test months: for systems 01, 07
  and 17, the month of test 01/1, 07/1 and 17/2. (b) The daily counters against the
  rest counters at every rest of every system that has reduced tables: charge,
  throughput, energy in, energy out and missing seconds.
* W10: the capacity settings still hash to ``109e9eb1efa5``; every committed phase 2
  result file is identical to its committed version; rebuilding each system's record
  gives the bytes on disk.

A check without results is written as ``not run``, never as ``fail``.

Writes ``validation/results/checks_phase3.csv``.

Usage:
    python validation/check_annex_vii.py                 # all checks (after the held-out run)
    python validation/check_annex_vii.py --development   # development systems only; held-out rows "not run"
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
PHASE3 = RESULTS / "phase3"
REDUCED = ROOT / "data" / "reduced" / "phase3"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
CAPACITY_HASH = "109e9eb1efa5"
NOT_RUN = "not run"
PASS_RANGE = (0.85, 0.95)
W9_TESTS = ("01/1", "07/1", "17/2")  # the three development test months of check V7


def set_of(system_id: int) -> str:
    return "development" if system_id in DEVELOPMENT else "held-out"


# --- W9 (b): daily counters against rest counters --------------------------------------


def counters_difference(system_id: int) -> dict | None:
    """The largest relative difference between the two counter paths at any rest of one system."""
    daily_path = REDUCED / f"ID_{system_id:02d}_daily.parquet"
    rests_path = REDUCED / f"ID_{system_id:02d}_rests.parquet"
    if not (daily_path.exists() and rests_path.exists()):
        return None
    daily = pd.read_parquet(daily_path).sort_values("day").reset_index(drop=True)
    rests = pd.read_parquet(rests_path)
    if rests.empty:
        return None
    # Sums of all days before each rest's day, by the daily path.
    before = {
        name: np.concatenate([[0.0], np.cumsum(daily[name].to_numpy(float))])
        for name in ("ah_in", "ah_out", "wh_in", "wh_out", "measured_s")
    }
    days = daily["day"].to_numpy("datetime64[s]")
    k = np.searchsorted(days, rests["start"].to_numpy("datetime64[s]").astype("datetime64[D]").astype("datetime64[s]"))
    total = {name: before[name][k] + rests[f"{name}_today"].to_numpy(float) for name in before}
    first = daily["t_first"].dropna().min()
    elapsed = (rests["start"] - first).dt.total_seconds().to_numpy(float)
    throughput = np.maximum(total["ah_in"] + total["ah_out"], 1.0)
    out = {
        "charge": np.abs(rests["charge_ah"] - (total["ah_in"] - total["ah_out"])) / throughput,
        "throughput": np.abs(rests["throughput_ah"] - (total["ah_in"] + total["ah_out"])) / throughput,
        "energy in": np.abs(rests["energy_in_wh"] - total["wh_in"]) / np.maximum(total["wh_in"], 1.0),
        "energy out": np.abs(rests["energy_out_wh"] - total["wh_out"]) / np.maximum(total["wh_out"], 1.0),
        "missing seconds": np.abs(rests["missing_s"] - (elapsed - total["measured_s"])) / np.maximum(elapsed, 1.0),
    }
    worst = {name: float(np.max(v)) for name, v in out.items()}
    return {"n_rests": int(len(rests)), "worst": max(worst.values()), "worst_counter": max(worst, key=worst.get)}


def w9_counters() -> dict:
    found = {sid: counters_difference(sid) for sid in figgener.SYSTEM_IDS}
    found = {sid: v for sid, v in found.items() if v is not None}
    if not found:
        return {"systems": [], "n_rests": 0, "worst": np.nan, "where": ""}
    worst_sid = max(found, key=lambda sid: found[sid]["worst"])
    return {
        "systems": sorted(found),
        "n_rests": sum(v["n_rests"] for v in found.values()),
        "worst": found[worst_sid]["worst"],
        "where": f"{found[worst_sid]['worst_counter']}, system {worst_sid:02d}",
    }


# --- W10: phase 2 untouched ------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def phase2_differences() -> tuple[int, int]:
    """Committed phase 2 result files, and how many of them differ from their committed version."""
    listed = [line.split("\t") for line in git("ls-tree", "-r", "HEAD", "validation/results/phase2").splitlines()]
    files = [(meta.split()[2], path) for meta, path in listed]
    different = 0
    for blob, path in files:
        target = ROOT / path
        if not target.exists() or git("hash-object", str(target)).strip() != blob:
            different += 1
    return len(files), different


def phase2_rebuild(system_ids, folder: Path) -> dict:
    """Rebuild the phase 2 results of some systems into ``folder`` and compare each file with its committed bytes.

    The committed files are never overwritten: ``run_capacity.OUT`` is pointed at ``folder``.
    """
    import run_capacity

    folder.mkdir(parents=True, exist_ok=True)
    run_capacity.OUT = folder
    tests = run_capacity.all_tests()
    for system_id in system_ids:
        run_capacity.run_system(system_id, tests, CapacityConfig())
    compared, different = 0, []
    for path in sorted(folder.glob("system_*")):
        committed = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:validation/results/phase2/{path.name}"], capture_output=True, check=False
        )
        compared += 1
        if committed.returncode != 0 or committed.stdout != path.read_bytes():
            different.append(path.name)
    return {"compared": compared, "different": different}


# --- W2, W5, W7, W8: synthetic packs ---------------------------------------------------


def synthetic_rows(record_hash: str) -> list[tuple]:
    path = RESULTS / "synthetic_phase3.csv"
    names = (
        ("W2", "w2_value_held", "resistance at the weighted mean age of the days in memory"),
        ("W2", "w2_growth_held", "resistance growth per year, whole record"),
        ("W5", "w5_held", "round-trip efficiency, full-to-full loops"),
        ("W7", "w7_held", "apparent charge loss (hidden drain)"),
    )
    if not path.exists():
        return [(check, f"synthetic packs: {what}", "no results yet", "85-95 %", NOT_RUN) for check, _, what in names] + [
            ("W8", "synthetic packs: throughput, cycles, planted events", "no results yet", "all packs", NOT_RUN)
        ]
    table = pd.read_csv(path)
    hashes = set(table["record_config_hash"]) | {f"capacity {h}" for h in table["capacity_config_hash"]}
    if hashes != {record_hash, f"capacity {CAPACITY_HASH}"}:
        raise SystemExit(f"synthetic results come from settings {sorted(hashes)}: rerun synthetic_phase3.py")
    rows = []
    for check, column, what in names:
        if column not in table:
            rows.append((check, f"synthetic packs: {what}", "column missing", "85-95 %", NOT_RUN))
            continue
        held = table[column].fillna(False).astype(bool)
        share = float(held.mean())
        rows.append(
            (check, f"{len(table)} synthetic packs: {what}", f"band held the truth in {int(held.sum())} of {len(held)} ({share:.1%})",
             "85-95 %", bool(PASS_RANGE[0] <= share <= PASS_RANGE[1]))
        )
    if "w8_pass" in table:
        ok = table["w8_pass"].fillna(False).astype(bool)
        errors = table[[c for c in table if c.startswith("w8_") and c.endswith("_rel_error")]].abs()
        exact = table["w8_events_exact"].fillna(False).astype(bool)
        rows.append(
            ("W8", f"{len(table)} synthetic packs: Ah, kWh, cycles; planted events",
             f"largest relative error {errors.max().max():.3%}; events exact on {int(exact.sum())} of {len(exact)}; "
             f"{int(ok.sum())} of {len(ok)} packs within the rule", "<= 0.6 % and exact, every pack", bool(ok.all()))
        )
    else:
        rows.append(("W8", "synthetic packs: throughput, cycles, planted events", "column missing", "all packs", NOT_RUN))
    return rows


# --- W4, W6: from the yearly tables of the Annex VII run --------------------------------


def yearly(system_ids, record_hash: str) -> pd.DataFrame:
    """The yearly tables of some systems, one frame; refuses rows of other settings."""
    frames = []
    for sid in system_ids:
        path = PHASE3 / f"system_{sid:02d}_yearly.csv"
        if path.exists():
            t = pd.read_csv(path, parse_dates=["date"])
            t.insert(0, "system_id", sid)
            frames.append(t)
    if not frames:
        return pd.DataFrame()
    table = pd.concat(frames, ignore_index=True)
    hashes = set(table["record_config_hash"]) | {f"capacity {h}" for h in table["capacity_config_hash"]}
    if hashes != {record_hash, f"capacity {CAPACITY_HASH}"}:
        raise SystemExit(f"yearly tables come from settings {sorted(hashes)}: rerun run_annex_vii.py")
    return table


def w4_pairs(table: pd.DataFrame) -> pd.DataFrame:
    """Neighbouring temperature bands, same system and date, both estimated: does the colder read higher?"""
    if table.empty:
        return pd.DataFrame(columns=["system_id", "date", "cold_c", "warm_c", "cold_ohm", "warm_ohm", "colder_higher"])
    r = table[table["quantity"].str.startswith("resistance_ohm@") & (table["status"] == "estimated")].copy()
    r["band_c"] = r["quantity"].str.split("@").str[1].astype(float)
    rows = []
    for (sid, date), g in r.groupby(["system_id", "date"]):
        g = g.sort_values("band_c")
        bands, values = g["band_c"].to_numpy(), g["value"].to_numpy(float)
        for k in range(len(g) - 1):
            step = bands[k + 1] - bands[k]
            if abs(step - RecordConfig().temp_band_c) < 1e-9:
                rows.append((sid, date, bands[k], bands[k + 1], values[k], values[k + 1], bool(values[k] > values[k + 1])))
    return pd.DataFrame(rows, columns=["system_id", "date", "cold_c", "warm_c", "cold_ohm", "warm_ohm", "colder_higher"])


def w6_years(table: pd.DataFrame) -> pd.DataFrame:
    """Full-to-full against empty-to-empty efficiency, same system and date, both estimated."""
    cols = ["system_id", "date", "full", "empty", "difference_points", "within_1_point"]
    if table.empty:
        return pd.DataFrame(columns=cols)
    e = table[table["quantity"].isin(["efficiency_full_loops", "efficiency_empty_loops"]) & (table["status"] == "estimated")]
    wide = e.pivot_table(index=["system_id", "date"], columns="quantity", values="value").dropna().reset_index()
    if wide.empty:
        return pd.DataFrame(columns=cols)
    wide = wide.rename(columns={"efficiency_full_loops": "full", "efficiency_empty_loops": "empty"})
    wide["difference_points"] = 100.0 * (wide["full"] - wide["empty"])
    wide["within_1_point"] = wide["difference_points"].abs() <= 1.0
    return wide[cols]


# --- W9 (a): the step-by-step core against the batch path -------------------------------


def _to_f32(state) -> None:
    """Round every float of a core state to 32 bits (the C port's arithmetic, approximately)."""
    import struct

    def r(v: float) -> float:
        return struct.unpack("f", struct.pack("f", v))[0] if np.isfinite(v) and abs(v) < 3e38 else v

    for name in state.__slots__:
        v = getattr(state, name)
        if isinstance(v, float):
            setattr(state, name, r(v))
        elif isinstance(v, list):
            setattr(state, name, [r(e) for e in v])


def _core_f32(t, a, b, linked, at_days, tau_days, level, min_events, lower, upper) -> list:
    """The step-by-step path with the state rounded to 32 bits after every update."""
    from statistics import NormalDist

    from besslib.soh import _core

    z = NormalDist().inv_cdf(0.5 + level / 2.0)
    state = _core.ratio_state(tau_days)
    out, k = [], 0
    for tq in at_days:
        while k < len(t) and t[k] <= tq:
            _core.ratio_update(state, float(t[k]), float(a[k]), float(b[k]), int(linked[k]))
            _to_f32(state)
            k += 1
        out.append(_core.ratio_solve(state, float(tq), z, min_events, 0.0, lower, upper))
    return out


def _compare(t, a, b, linked, at_days, tau_days, level, min_events, lower, upper) -> tuple[float, float, int]:
    """Largest relative difference of value and band half-width: batch against core (64-bit) and (32-bit)."""
    from besslib.soh import _ratio

    args = (t, a, b, linked, at_days, tau_days, level, min_events, lower, upper)
    batch = _ratio.estimate_ratio(*args)
    core = _ratio.run_ratio_core(*args)
    c32 = _core_f32(*args)
    worst64 = worst32 = 0.0
    n = 0
    for k in range(len(at_days)):
        x, y, z32 = batch.iloc[k], core.iloc[k], c32[k]
        if not (int(x["status"]) == 0 and int(y["status"]) == 0):
            continue
        n += 1
        hx, hy = x["high"] - x["value"], y["high"] - y["value"]
        worst64 = max(worst64, abs(x["value"] - y["value"]) / abs(y["value"]), abs(hx - hy) / abs(hy))
        if z32.status == 0:
            worst32 = max(worst32, abs(z32.theta[0] - y["value"]) / abs(y["value"]),
                          abs((z32.high - z32.theta[0]) - hy) / abs(hy))
        else:
            worst32 = np.inf
    return worst64, worst32, n


def w9_core() -> dict:
    """Resistance (every temperature band) and efficiency (both loop kinds), every day of three test months."""
    from besslib.soh.capacity import days
    from besslib.soh.efficiency import estimate_efficiency
    from besslib.soh.resistance import estimate_resistance
    from besslib.soh.reduce import read_reduced
    from run_capacity import all_tests
    from tune_capacity import masks_for

    tests = all_tests()
    config = RecordConfig()
    out = {"resistance": [0.0, 0.0, 0], "efficiency": [0.0, 0.0, 0], "months": []}
    for test_id in W9_TESTS:
        sid = int(test_id.split("/")[0])
        if not (REDUCED / f"ID_{sid:02d}_steps.parquet").exists():
            continue
        reduced = read_reduced(REDUCED, f"ID_{sid:02d}")
        spec = figgener.pack_spec(sid)
        mask = masks_for(tests, sid)
        onset = tests.loc[tests["test_id"] == test_id, "onset"].iloc[0]
        at = pd.date_range(onset.to_period("M").start_time, onset.to_period("M").end_time.normalize(), freq="D")
        at_days = days(at)
        out["months"].append(f"{test_id} ({onset:%Y-%m})")
        events = estimate_resistance(None, spec, reduced=reduced, at=at[:1], mask=mask).attrs["events"]
        for _, sel in events.groupby("temp_band_c"):
            w = _compare(sel["t_days"].to_numpy(float), sel["a"].to_numpy(float), sel["b"].to_numpy(float),
                         np.zeros(len(sel), dtype=int), at_days, config.tau_days, config.level,
                         config.resistance_min_days, 0.0, np.inf)
            r = out["resistance"]
            r[0], r[1], r[2] = max(r[0], w[0]), max(r[1], w[1]), r[2] + w[2]
        for kind in ("full", "empty"):
            loops = estimate_efficiency(None, spec, reduced=reduced, at=at[:1], mask=mask, kind=kind).attrs["loops"]
            w = _compare(days(loops["t_end"]), loops["energy_in_wh"].to_numpy(float), loops["energy_out_wh"].to_numpy(float),
                         loops["linked"].to_numpy(int), at_days, config.tau_days, config.level,
                         config.efficiency_min_loops, 0.0, 1.0)
            e = out["efficiency"]
            e[0], e[1], e[2] = max(e[0], w[0]), max(e[1], w[1]), e[2] + w[2]
    return out


# --- W1: every field has a value with a band and a count, or "not possible" with a reason ----

NOT_POSSIBLE = "not possible from this data: "
PHASE3_FIELDS = ("A2", "A3", "A4", "A5", "B2", "B3", "B4", "B5")


def impossible(code: str, value: float, low: float, high: float) -> bool:
    """An estimated value that cannot be true, or a band that does not hold its value."""
    if not (low <= value <= high):
        return True
    if code == "A3":
        return not (0.0 < value <= 1.0)
    if code in ("A2", "A5"):
        return value <= 0.0
    if code in ("B2", "B3", "B4", "B5"):
        return value < 0.0
    return False


def w1_record(path: Path) -> dict:
    """Fields of one record that break the rule, with the reason."""
    record = json.loads(path.read_text())
    bad = []
    for f in record["fields"]:
        status, value, low, high, events = f["status"], f["value"], f["low"], f["high"], f["events"]
        if status == "estimated":
            if any(v is None for v in (value, low, high, events)):
                bad.append(f"{f['code']}: estimated without value, band or count")
            elif f["code"] in PHASE3_FIELDS and impossible(f["code"], value, low, high):
                bad.append(f"{f['code']}: estimated with an impossible value {value:g} [{low:g}, {high:g}]")
        elif not (status.startswith(NOT_POSSIBLE) and len(status) > len(NOT_POSSIBLE)):
            bad.append(f"{f['code']}: status '{status}' is neither estimated nor not possible with a reason")
    return {"n_fields": len(record["fields"]), "bad": bad, "record_config_hash": record["record_config_hash"]}


# --- W10: one command per system rebuilds its record, twice identical --------------------


def records_rebuild(system_ids, folder: Path) -> dict:
    """Run the Annex VII run twice into two folders; compare both with each other and with the files on disk."""
    import run_annex_vii

    tests = run_annex_vii.all_tests()
    for name in ("first", "second"):
        run_annex_vii.OUT = folder / name
        for sid in system_ids:
            run_annex_vii.run_system(sid, tests)
    compared, different = 0, []
    for path in sorted((folder / "first").glob("system_*")):
        twin = folder / "second" / path.name
        on_disk = PHASE3 / path.name
        compared += 1
        if not twin.exists() or twin.read_bytes() != path.read_bytes():
            different.append(f"{path.name} (run against run)")
        if not on_disk.exists() or on_disk.read_bytes() != path.read_bytes():
            different.append(f"{path.name} (run against the file on disk)")
    return {"compared": compared, "different": different}


# --- W3: resistance from operation against the first minute of the capacity tests ---------

ONSET_MIN_REFERENCES = 30


def w3_rows(scope_ids, label: str, path: Path) -> tuple:
    if not path.exists():
        return ("W3" if label == "held-out" else "W3 info", f"{label} capacity tests", "no results yet", "<= 20 %",
                NOT_RUN if label == "held-out" else None)
    t = pd.read_csv(path)
    t = t[t["system_id"].isin(scope_ids)]
    refs = t[t["onset_status"] == "estimated"]
    diff = refs["difference_percent"].abs().fillna(np.inf)
    median = float(np.median(diff)) if len(diff) else np.nan
    bias = refs.groupby("chemistry")["difference_percent"].median().round(1).to_dict()
    value = (f"median |difference| {median:.1f} % over {len(refs)} of {len(t)} tests with a reference; "
             f"median difference by chemistry {bias}")
    if label != "held-out":
        return ("W3 info", f"{label} capacity tests", value, "information", None)
    if len(refs) < ONSET_MIN_REFERENCES:
        return ("W3", "held-out capacity tests", f"{len(refs)} of {len(t)} tests have a reference: not possible",
                f">= {ONSET_MIN_REFERENCES} references, then <= 20 %", False)
    return ("W3", "held-out capacity tests", value, "<= 20 %, bias shown", bool(median <= 20.0))


# --- the table -------------------------------------------------------------------------------


def main(argv: list[str]) -> None:
    development_only = "--development" in argv
    record_hash = config_hash(RecordConfig())
    if config_hash(CapacityConfig()) != CAPACITY_HASH:
        raise SystemExit(f"the capacity settings hash to {config_hash(CapacityConfig())}, not {CAPACITY_HASH}")
    scopes = [("development", tuple(DEVELOPMENT))] + ([] if development_only else [("held-out", HELD_OUT)])
    rows = []

    # W1
    for label, ids in scopes:
        found = [PHASE3 / f"system_{i:02d}_record.json" for i in ids if (PHASE3 / f"system_{i:02d}_record.json").exists()]
        checks = [w1_record(p) for p in found]
        if any(c["record_config_hash"] != record_hash for c in checks):
            raise SystemExit("records of other settings on disk: rerun run_annex_vii.py")
        bad = [b for c in checks for b in c["bad"]]
        check = "W1" if label == "held-out" else "W1 info"
        value = f"{len(checks)} of {len(ids)} records; {len(bad)} fields break the rule" + (f": {'; '.join(bad)}" if bad else "")
        passed = (len(bad) == 0 if len(checks) == len(ids) else False) if label == "held-out" else None
        rows.append((check, f"{label} records", value if checks else "no records yet", "0", passed if checks else (NOT_RUN if label == "held-out" else None)))
    if development_only:
        rows.append(("W1", "held-out records", "not run in this mode", "0", NOT_RUN))

    # W2, W5, W7, W8
    rows += synthetic_rows(record_hash)

    # W3
    rows.append(w3_rows(DEVELOPMENT, "development", RESULTS / "onset_resistance_development.csv"))
    rows.append(w3_rows(HELD_OUT, "held-out", RESULTS / "onset_resistance.csv") if not development_only
                else ("W3", "held-out capacity tests", "not run in this mode", "<= 20 %", NOT_RUN))

    # W4, W6
    for label, ids in scopes + ([("held-out", ())] if development_only else []):
        table = yearly(ids, record_hash) if ids else pd.DataFrame()
        pairs, years = w4_pairs(table), w6_years(table)
        is_held = label == "held-out"
        for check, frame, column, what, need in (
            ("W4", pairs, "colder_higher", "neighbouring 5-degree bands: the colder reads higher", 0.80),
            ("W6", years, "within_1_point", "full-to-full against empty-to-empty loops within 1 point", 0.80),
        ):
            name = check if is_held else f"{check} info"
            if len(frame) == 0:
                rows.append((name, f"{label}: {what}", "not run in this mode" if is_held and development_only else "no pairs",
                             f">= {need:.0%}", NOT_RUN if is_held else None))
                continue
            share = float(frame[column].mean())
            rows.append((name, f"{label}: {what}", f"{int(frame[column].sum())} of {len(frame)} ({share:.0%})",
                         f">= {need:.0%}", bool(share >= need) if is_held else None))

    # W9
    core = w9_core()
    for part in ("resistance", "efficiency"):
        w64, w32, n = core[part]
        rows.append(("W9", f"core against batch, {part}: {n} daily estimates (months of tests {', '.join(core['months'])})",
                     f"max relative difference {w64:.1e} (64-bit); {w32:.1e} (32-bit, information)", "<= 1e-6", bool(n and w64 <= 1e-6)))
    counters = w9_counters()
    rows.append(("W9", f"daily counters against rest counters: {counters['n_rests']} rests, systems {counters['systems']}",
                 f"max relative difference {counters['worst']:.1e} ({counters['where']})", "<= 1e-6",
                 bool(counters["n_rests"] and counters["worst"] <= 1e-6)))

    # W10
    n_files, n_diff = phase2_differences()
    rows.append(("W10", "capacity settings and committed phase 2 files",
                 f"hash {config_hash(CapacityConfig())}; {n_diff} of {n_files} committed files differ", "0 differences",
                 n_diff == 0))
    scratch = Path(argv[argv.index("--scratch") + 1]) if "--scratch" in argv else ROOT / "data" / "rebuild"
    ids = list(DEVELOPMENT) if development_only else list(figgener.SYSTEM_IDS)
    rebuilt = phase2_rebuild(ids, scratch / "phase2")
    rows.append(("W10", f"phase 2 rebuilt for {len(ids)} systems", f"{len(rebuilt['different'])} of {rebuilt['compared']} files differ"
                 + (f": {rebuilt['different']}" if rebuilt["different"] else ""), "0 differences", not rebuilt["different"]))
    again = records_rebuild(ids, scratch / "phase3")
    rows.append(("W10", f"records rebuilt twice for {len(ids)} systems", f"{len(again['different'])} differences over {again['compared']} files"
                 + (f": {again['different']}" if again["different"] else ""), "0 differences", not again["different"]))

    table = pd.DataFrame(rows, columns=["check", "scope", "value", "pass_if", "passed"])
    table["result"] = table["passed"].map(lambda v: {True: "pass", False: "fail", None: "", NOT_RUN: "not run"}.get(v, str(v)))
    table["capacity_config_hash"] = CAPACITY_HASH
    table["record_config_hash"] = record_hash
    table.drop(columns="passed").to_csv(RESULTS / "checks_phase3.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 140):
        print(table[["check", "scope", "value", "result"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
