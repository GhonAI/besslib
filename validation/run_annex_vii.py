"""Phase 3: the Annex VII record of each system, and its values at every year end.

For each system:

1. the reduced tables (``reduce_phase3.ensure_reduced``: daily, step and rest tables);
2. the capacity-test windows as the mask (one day before each test begins to one day
   after it ends, ``tune_capacity.masks_for``): every estimate leaves them out, the
   counts (throughput, cycles, harmful events) do not;
3. the record at the last sample (``besslib.soh.record.annex_vii``) →
   ``validation/results/phase3/system_XX_record.json``;
4. the record at every 31 December inside the data and at the last sample, as a long
   table → ``validation/results/phase3/system_XX_yearly.csv``: one row per date and
   quantity (``date``, ``field``, ``quantity``, ``value``, ``low``, ``high``, ``unit``,
   ``events``, ``status`` and both configuration hashes). It holds every temperature band
   of the resistance and both loop kinds of the efficiency. A 31 December row is the
   record at 00:00 of that day, the time of the capacity estimator's month ends; its
   counts hold the whole days before it;
5. check: the remaining capacity (A1) at every month end of
   ``validation/results/phase2/system_XX_monthly.csv`` is computed again by the record's
   path (operating rests of the reduced tables, the same mask) and compared at the
   printed precision. Development systems only.

Floats are written with a fixed format; two runs give identical files.

With ``--settings 0.1 --out DIR`` (or ``0.2``) the run uses the settings of release 0.1.0 (or
0.2.0) (``CapacityConfig().v0_1()``, ``RecordConfig().v0_1()``, or ``v0_2()``) and writes to DIR
(check F1, ``check_v03.py``); the A1 comparison of step 5 then reads the phase 2 files of
``--phase2 DIR`` and is skipped without it.

Usage:
    python validation/run_annex_vii.py 1 4         # some systems
    python validation/run_annex_vii.py development # the 7 development systems
    python validation/run_annex_vii.py held-out    # the 14 held-out systems (once, after the freeze)
    python validation/run_annex_vii.py --held-out 5  # one held-out system; held-out IDs are refused without it
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pandas as pd

from besslib.io import figgener
from besslib.soh.capacity import estimate_capacity
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.record import annex_vii
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs

from reduce_phase3 import ensure_reduced, load_reduced
from run_capacity import all_tests
from tune_capacity import DEVELOPMENT, masks_for

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "results" / "phase3"
PHASE2 = ROOT / "validation" / "results" / "phase2"
HELD_OUT = tuple(i for i in figgener.SYSTEM_IDS if i not in DEVELOPMENT)
FLOAT = "%.7g"
YEARLY_COLUMNS = [
    "date", "field", "quantity", "value", "low", "high", "unit", "events", "status",
    "capacity_config_hash", "record_config_hash",
]


def _num(value) -> float:
    return math.nan if value is None else float(value)


def quantities(record) -> list[dict]:
    """The rows of the yearly table for one record: the headline of every field and the quantities beside it."""
    rows = []

    def add(code, quantity, value, low, high, unit, events, status):
        rows.append(dict(field=code, quantity=quantity, value=_num(value), low=_num(low), high=_num(high), unit=unit,
                         events=_num(events), status=status))

    f = {x.code: x for x in record.fields}
    a1 = f["A1"]
    add("A1", "capacity_ah", a1.value, a1.low, a1.high, "Ah", a1.events, a1.status)
    d = a1.details
    add("A1", "capacity_share_of_reference", d["share_of_reference"], d["share_of_reference_low"],
        d["share_of_reference_high"], f"share of {d['reference_capacity_ah']:g} Ah", a1.events, a1.status)
    a2 = f["A2"]
    add("A2", "power_capability_share", a2.value, a2.low, a2.high, a2.unit, a2.events, a2.status)
    d = a2.details
    add("A2", "power_capability_w", d["power_capability_w"], d["power_capability_w_low"], d["power_capability_w_high"],
        "W", a2.events, d["power_capability_w_status"])
    a3 = f["A3"]
    add("A3", "efficiency_full_loops", a3.value, a3.low, a3.high, "share", a3.events, a3.status)
    e = a3.details["empty_loops"]
    add("A3", "efficiency_empty_loops", e["value"], e["low"], e["high"], "share", e["n_loops"], e["status"])
    add("A3", "coulombic_part_full_loops", a3.details["coulombic_part"], None, None, "share", a3.events, a3.status)
    add("A3", "voltage_part_full_loops", a3.details["voltage_part"], None, None, "share", a3.events, a3.status)
    a4 = f["A4"]
    add("A4", "charge_loss_ma", a4.value, a4.low, a4.high, "mA", a4.events, a4.status)
    d = a4.details
    add("A4", "charge_loss_percent_per_month", d["percent_of_reference_per_month"],
        d["percent_of_reference_per_month_low"], d["percent_of_reference_per_month_high"],
        f"% of {d['reference_capacity_ah']:g} Ah per month", a4.events, a4.status)
    t = d["evolution"]
    add("A4", "charge_loss_trend_ma_per_year", t["slope_ma_per_year"], t["slope_ma_per_year_low"],
        t["slope_ma_per_year_high"], "mA per year", t["n_years"], t["status"])
    a5 = f["A5"]
    for _, b in a5.details["bands"].iterrows():
        add("A5", f"resistance_ohm@{b['temp_band_c']:.1f}", b["resistance_ohm"], b["resistance_ohm_low"],
            b["resistance_ohm_high"], "ohm", b["n_days"], b["status"])
    d = a5.details
    add("A5", "resistance_growth_percent_per_year", d["growth_percent_per_year"], d["growth_percent_per_year_low"],
        d["growth_percent_per_year_high"], "% of the start value per year", d["growth_n_days"], d["growth_status"])
    b1 = f["B1"]
    add("B1", "date_of_manufacture", None, None, None, "date", 0.0, b1.details["date_of_manufacture_status"])
    for code, quantity in (("B2", "energy_throughput_kwh"), ("B3", "capacity_throughput_ah"),
                           ("B5", "full_equivalent_cycles")):
        x = f[code]
        add(code, quantity, x.value, x.low, x.high, x.unit, x.events, x.status)
    b4 = f["B4"]
    add("B4", "n_empty_rests", b4.value, b4.low, b4.high, "events", b4.events, b4.status)
    d = b4.details
    add("B4", "voltage_min_v", d["voltage_min_v"], None, None, "V", b4.events, b4.status)
    add("B4", "voltage_max_v", d["voltage_max_v"], None, None, "V", b4.events, b4.status)
    deep = d["deep_discharge_events"]
    add("B4", "n_deep_discharge", deep["value"], None, None, "events", b4.events, deep["status"])
    for r in rows:
        r.update(date=record.date, capacity_config_hash=record.capacity_config_hash,
                 record_config_hash=record.record_config_hash)
    return rows


def year_ends(reduced) -> list[pd.Timestamp]:
    """Every 31 December (00:00) inside the data, and the last sample."""
    first, last = reduced.daily["t_first"].min(), reduced.daily["t_last"].max()
    ends = [pd.Timestamp(year=y, month=12, day=31) for y in range(first.year, last.year + 1)]
    return [d for d in ends if first < d <= last] + [pd.Timestamp(last)]


def check_a1(system_id: int, reduced, spec, mask, config: CapacityConfig | None = None, phase2_dir: Path | None = PHASE2) -> dict:
    """A1 by the record's path against the phase 2 monthly file, at its printed precision (5 decimals).

    Check W10 of version 0.2: the record's capacity equals the capacity run at every month end.
    """
    config = config or CapacityConfig()
    path = None if phase2_dir is None else phase2_dir / f"system_{system_id:02d}_monthly.csv"
    if path is None or not path.exists():
        return {"compared": 0, "different": 0}
    phase2 = pd.read_csv(path, parse_dates=["time"]).set_index("time")
    rests = operating_rests(reduced.rests, spec, config, mask=mask)
    pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    mine = estimate_capacity(None, spec, rests=rests, config=config, pairs=pairs, levels_from=levels_from(rests, config),
                             at=phase2.index)
    columns = ["capacity_ah", "capacity_ah_low", "capacity_ah_high", "offset_current_a", "n_pairs"]
    different = 0
    for col in columns:
        a = phase2[col].map(lambda v: "" if pd.isna(v) else f"{v:.5f}")
        b = mine[col].map(lambda v: "" if pd.isna(v) else f"{v:.5f}")
        different += int((a != b).sum())
    different += int((phase2["status"] != mine["status"]).sum())
    return {"compared": int(len(phase2)), "different": different}


def run_system(
    system_id: int, tests: pd.DataFrame, config: CapacityConfig | None = None, record_config: RecordConfig | None = None,
    out: Path = OUT, phase2_dir: Path | None = PHASE2,
) -> dict:
    """Write the record and yearly table of one system into ``out``; return the A1 comparison (``check_a1``)."""
    which = "development" if system_id in DEVELOPMENT else "held-out"
    started = time.time()
    spec = figgener.pack_spec(system_id)
    ensure_reduced(system_id)
    reduced = load_reduced(system_id)
    mask = masks_for(tests, system_id)
    out.mkdir(parents=True, exist_ok=True)
    kw = dict(capacity_config=config, config=record_config)

    record = annex_vii(None, spec, reduced=reduced, mask=mask, **kw)
    (out / f"system_{system_id:02d}_record.json").write_text(record.to_json())
    rows = []
    for when in year_ends(reduced):
        rows += quantities(record if when == record.date else annex_vii(None, spec, reduced=reduced, at=when, mask=mask, **kw))
    yearly = pd.DataFrame(rows, columns=YEARLY_COLUMNS)
    yearly["date"] = pd.to_datetime(yearly["date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    yearly.to_csv(out / f"system_{system_id:02d}_yearly.csv", index=False, float_format=FLOAT)
    a1 = check_a1(system_id, reduced, spec, mask, config, phase2_dir)

    print(f"system {system_id:02d} ({which}), record at {record.date}, {time.time() - started:.0f} s", flush=True)
    for f in record.fields:
        value = "" if math.isnan(f.value) else f"{f.value:.6g} [{f.low:.6g}, {f.high:.6g}] {f.unit}"
        print(f"  {f.code} {f.label}: {value}; events {f.events:.4g}; {f.status}", flush=True)
    if a1["compared"]:
        print(f"  A1 against phase 2: {a1['compared']} month ends, {a1['different']} values differ", flush=True)
    return a1


def main(argv: list[str]) -> None:
    config, record_config, out, phase2_dir = CapacityConfig(), RecordConfig(), OUT, PHASE2
    if "--settings" in argv:
        k = argv.index("--settings")
        if argv[k + 1] not in ("0.1", "0.2"):
            sys.exit("--settings takes 0.1 or 0.2 (releases 0.1.0 and 0.2.0); the default is the current settings")
        old = argv[k + 1] == "0.1"
        config = config.v0_1() if old else config.v0_2()
        record_config, phase2_dir = record_config.v0_1() if old else record_config.v0_2(), None
        del argv[k : k + 2]
    if "--out" in argv:
        k = argv.index("--out")
        out = Path(argv[k + 1])
        del argv[k : k + 2]
    if "--phase2" in argv:
        k = argv.index("--phase2")
        phase2_dir = Path(argv[k + 1])
        del argv[k : k + 2]
    held_out = "--held-out" in argv
    argv = [a for a in argv if a != "--held-out"]
    if argv == ["development"]:
        ids = list(DEVELOPMENT)
    elif argv == ["held-out"]:
        ids = list(HELD_OUT)
        held_out = True
    else:
        ids = [int(a) for a in argv]
    refused = [i for i in ids if i in HELD_OUT]
    if refused and not held_out:
        sys.exit(f"refused: systems {refused} are held out; give --held-out (or the mode held-out) to run them")
    print(f"configuration: capacity {config_hash(config)}, record {config_hash(record_config)}; output {out}", flush=True)
    tests = all_tests()
    for system_id in ids:
        run_system(system_id, tests, config, record_config, out, phase2_dir)


if __name__ == "__main__":
    main(sys.argv[1:])
