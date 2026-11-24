"""The truth of a run: tables with a ``time`` column, and the writer.

One month of ``pack.Month`` becomes five tables (``tables``):

* ``steps``: per step, the true current and voltage, the temperature at the sensor's point,
  the ambient temperature, and the highest and lowest mean cell voltage of any group;
* ``cells``: per cell group every ``cell_every_s``, its state of charge, voltage,
  temperature, capacity, R0, and its leak and bleed currents;
* ``stops``: every BMS stop, with its kind, rule, limiting group, limit, true state of charge
  and capacity, the BMS's own state of charge, and the piece's tag;
* ``blocks``: the engine's constant-current blocks (start, duration, current);
* ``balance``: per group, the month's sums of charge and energy (``time`` = the month's start).

``run_to_dir`` writes a run to a folder: ``frames/YYYY-MM.parquet``, ``true_frames/...``
(optional), ``truth/<table>/YYYY-MM.parquet`` and ``manifest.json``. The parquet files
are written the same way every time, so the same case and seed give the same bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import besslib

from .run import build, load_case, run, spec

TABLES = ("steps", "cells", "stops", "blocks", "balance")


def _times(origin: pd.Timestamp, seconds) -> pd.DatetimeIndex:
    ms = np.round(np.asarray(seconds, dtype=float) * 1000.0).astype("int64")
    return pd.DatetimeIndex(origin + pd.to_timedelta(ms, unit="ms"), name="time").as_unit("ms")


def tables(month) -> dict[str, pd.DataFrame]:
    """The truth tables of one month (a ``pack.Month``, or a ``MonthOut`` with ``.truth``)."""
    m = getattr(month, "truth", month)
    origin = m.start - pd.to_timedelta(float(m.seconds[0]), unit="s")

    def with_time(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.insert(0, "time", _times(origin, out["seconds"]))
        return out.drop(columns="seconds").reset_index(drop=True)

    steps = pd.DataFrame({
        "time": _times(origin, m.seconds),
        "current_a": m.current_a, "voltage_v": m.voltage_v,
        "temp_sensor_c": m.temp_sensor_c, "temp_ambient_c": m.temp_ambient_c,
        "v_cell_max": m.v_cell_max, "v_cell_min": m.v_cell_min,
    })
    balance = m.balance.copy()
    balance.insert(0, "time", pd.DatetimeIndex([m.start] * len(balance)).as_unit("ms"))
    return {
        "steps": steps,
        "cells": with_time(m.cells),
        "stops": with_time(m.stops),
        "blocks": with_time(m.blocks),
        "balance": balance.reset_index(drop=True),
    }


def write_parquet(frame: pd.DataFrame, path: Path) -> str:
    """Write ``frame`` the same way every time; return the file's sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=isinstance(frame.index, pd.DatetimeIndex))
    pq.write_table(table, path, compression="zstd", use_dictionary=False, write_statistics=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    return x


def run_to_dir(case, months: int | None, seed: int, out_dir, overrides: dict | None = None,
               true_frames: bool = False) -> dict:
    """Run a case and write it to ``out_dir``; return the manifest (also written as ``manifest.json``).

    The manifest holds the case (with the overrides applied), the seed, the number of months,
    the besslib version, the pack's spec and the sha256 of every file written."""
    out = Path(out_dir)
    data = load_case(case, overrides)
    pack, _ = build(data, seed)
    hashes: dict[str, str] = {}
    written = 0
    for mo in run(data, months, seed):
        label = mo.truth.start.strftime("%Y-%m")
        path = f"frames/{label}.parquet"
        hashes[path] = write_parquet(mo.frame, out / path)
        if true_frames:
            path = f"true_frames/{label}.parquet"
            hashes[path] = write_parquet(mo.true_frame, out / path)
        for name, table in tables(mo).items():
            path = f"truth/{name}/{label}.parquet"
            hashes[path] = write_parquet(table, out / path)
        written += 1
    manifest = {
        "case": _jsonable(data),
        "seed": int(seed),
        "months": written,
        "overrides": _jsonable(overrides or {}),
        "besslib_version": besslib.__version__,
        "spec": _jsonable({k: v for k, v in asdict(spec(data, pack)).items() if k != "extra"}),
        "files": dict(sorted(hashes.items())),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    return manifest
