"""Check W3 reference: the 1-second resistance at the onset of each field capacity test, next to the operating estimate.

A field capacity test starts with a large current step from rest. The reference is the
same estimator on a short window: ``resistance.resistance_of_steps`` over the steps of
the samples from 10 s before the test's onset to 60 s after it, extracted from the
test's own window (``data/reduced/capacity_tests/ID_XX_test_N.parquet``) with
``reduce.reduce_frame``, with the frozen settings (``RecordConfig()``). The
voltage-refresh rule applies: a window in which more than
``RecordConfig.unrefreshed_max_share`` of the steps show no voltage change has no
reference. A test whose window holds no qualifying step has no reference either; both
are listed with their status.

The operating estimate is ``resistance.estimate_resistance`` on the system's reduced
tables (``data/reduced/phase3/``), with every capacity test masked (one day before to
one day after, ``tune_capacity.masks_for``), in the 5-degree band of the pack
temperature at onset, at the start of the test's mask: it uses only operation before
the test. ``difference_percent = 100 (operating - onset) / onset``.

This is information, not a pass or fail: the onset step is one step at one state of
charge (full), one current (the test's) and one temperature, while the operating value
is an average over the days in memory. Only the test times are read from
``validation/results/capacity_tests.csv``, never a capacity value.

Writes ``validation/results/onset_resistance_development.csv`` (the 22 development
tests). With ``--held-out``, the held-out tests are read and
``validation/results/onset_resistance.csv`` is written (once, after the freeze); without
it a held-out system is refused.

Usage:
    python validation/onset_resistance.py              # development tests
    python validation/onset_resistance.py --held-out   # held-out tests (once, after the freeze)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

from besslib.io import figgener
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.reduce import read_reduced, reduce_frame
from besslib.soh.resistance import band_centre, estimate_resistance, resistance_of_steps

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
WINDOWS = ROOT / "data" / "reduced" / "capacity_tests"
TABLES = ROOT / "data" / "reduced" / "phase3"
BEFORE = pd.Timedelta(seconds=10)
AFTER = pd.Timedelta(seconds=60)
COLUMNS = [
    "test_id", "system_id", "test", "chemistry", "onset", "temp_pack_c", "temp_band_c",
    "onset_ohm", "onset_low", "onset_high", "onset_n_steps", "onset_n_unrefreshed", "onset_band", "onset_status",
    "operating_time", "operating_ohm", "operating_low", "operating_high", "operating_n_days", "operating_status",
    "difference_percent", "sample_s", "record_config_hash", "capacity_config_hash",
]
TIME_COLUMNS = ["system_id", "test", "set", "chemistry", "onset", "end", "documented_start"]


def onset_window(window: pd.DataFrame, onset: pd.Timestamp) -> pd.DataFrame:
    """The samples from 10 s before ``onset`` to 60 s after it."""
    return window.loc[(window.index >= onset - BEFORE) & (window.index <= onset + AFTER)]


def onset_value(window: pd.DataFrame, onset: pd.Timestamp, spec, config: RecordConfig) -> tuple[dict, float]:
    """The reference at one onset (``resistance_of_steps`` on the onset window) and the
    pack temperature at the first sample at or after onset (NaN when none)."""
    part = onset_window(window, onset)
    after = part.loc[part.index >= onset, "temp_pack_c"]
    temp = float(after.iloc[0]) if len(after) else math.nan
    if len(part) < 2:
        return resistance_of_steps(pd.DataFrame(columns=["time", "current_before_a", "current_after_a",
                                                          "voltage_before_v", "voltage_after_v", "temp_pack_c",
                                                          "steady_s"]), spec, config), temp
    steps = reduce_frame(part.astype({c: "float64" for c in part.columns if c != "interpolated"}), spec).steps
    return resistance_of_steps(steps, spec, config), temp


def test_rows(tests: pd.DataFrame, system_id: int, config: RecordConfig, capacity_config: CapacityConfig) -> list[dict]:
    from tune_capacity import masks_for

    spec = figgener.pack_spec(system_id)
    reduced = read_reduced(TABLES, f"ID_{system_id:02d}")
    masks = masks_for(tests, system_id)
    rows = []
    for test, mask in zip(tests[tests["system_id"] == system_id].itertuples(), masks):
        window = pd.read_parquet(WINDOWS / f"ID_{system_id:02d}_test_{int(test.test)}.parquet")
        onset = pd.Timestamp(test.onset)
        ref, temp = onset_value(window, onset, spec, config)
        centre = float(band_centre([temp], 25.0, config.temp_band_c)[0])
        row = {
            "test_id": test.test_id, "system_id": system_id, "test": int(test.test), "chemistry": test.chemistry,
            "onset": onset, "temp_pack_c": temp, "temp_band_c": centre,
            "onset_ohm": ref["resistance_ohm"], "onset_low": ref["resistance_ohm_low"],
            "onset_high": ref["resistance_ohm_high"], "onset_n_steps": ref["n_steps"],
            "onset_n_unrefreshed": ref.get("n_unrefreshed", 0), "onset_band": ref["band"],
            "onset_status": ref["status"], "operating_time": mask[0],
            "operating_ohm": math.nan, "operating_low": math.nan, "operating_high": math.nan,
            "operating_n_days": 0.0, "operating_status": "not possible from this data: no pack temperature at onset",
            "sample_s": float(reduced.sample_s),
        }
        if math.isfinite(temp):
            est = estimate_resistance(None, spec, 25.0, config.level, config=config, capacity_config=capacity_config,
                                      reduced=reduced, at=[mask[0]], mask=masks)
            band = est[est["temp_band_c"] == centre]
            if len(band):
                b = band.iloc[0]
                row.update(operating_ohm=b["resistance_ohm"], operating_low=b["resistance_ohm_low"],
                           operating_high=b["resistance_ohm_high"], operating_n_days=b["n_days"],
                           operating_status=b["status"])
            else:
                row["operating_status"] = f"not possible from this data: too few current steps near {centre:g} °C"
        row["difference_percent"] = 100.0 * (row["operating_ohm"] - row["onset_ohm"]) / row["onset_ohm"]
        rows.append(row)
    return rows


def main(argv: list[str]) -> None:
    from tune_capacity import DEVELOPMENT

    held_out = "--held-out" in argv
    tests = pd.read_csv(
        RESULTS / "capacity_tests.csv", usecols=TIME_COLUMNS, parse_dates=["onset", "end", "documented_start"]
    )
    tests = tests[tests["set"] == ("held-out" if held_out else "development")].copy()
    tests["test_id"] = tests["system_id"].map("{:02d}".format) + "/" + tests["test"].astype(str)
    systems = sorted(int(i) for i in tests["system_id"].unique())
    if not held_out and any(i not in DEVELOPMENT for i in systems):
        raise SystemExit("a held-out system among the development tests: refused")
    config = RecordConfig()
    capacity_config = CapacityConfig()
    rows = []
    for system_id in systems:
        if system_id not in DEVELOPMENT and not held_out:
            raise SystemExit(f"system {system_id} is held out: give --held-out")
        rows += test_rows(tests, system_id, config, capacity_config)
        print(f"system {system_id:02d}: {len(rows)} tests so far", flush=True)
    table = pd.DataFrame(rows)
    table["record_config_hash"] = config_hash(config)
    table["capacity_config_hash"] = config_hash(capacity_config)
    table = table[COLUMNS]
    out = RESULTS / ("onset_resistance.csv" if held_out else "onset_resistance_development.csv")
    table.to_csv(out, index=False, float_format="%.6g")
    has = table["onset_status"] == "estimated"
    both = has & (table["operating_status"] == "estimated")
    print(f"wrote {out}: {len(table)} tests, {int(has.sum())} with a reference, {int(both.sum())} with both values")
    if both.any():
        diff = table.loc[both, "difference_percent"]
        print(f"median |difference| {diff.abs().median():.1f} %")
        print(table.loc[both].groupby("chemistry")["difference_percent"].agg(["count", "median"]).round(1).to_string())
    print(f"configuration: record {config_hash(config)}, capacity {config_hash(capacity_config)}")


if __name__ == "__main__":
    main(sys.argv[1:])
