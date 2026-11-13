"""Phase 1: build the reference of the validation from the Zenodo files.

For each system, one archive at a time:

1. download ``Data_ID_XX.zip`` from Zenodo record 12091223 if it is not there, and check its MD5;
2. list its monthly files (coverage);
3. save a one-second window around every documented field capacity test of that system
   (27 hours before to 14 hours after the documented start);
4. for development systems only: check the data quality of every month;
5. delete the archive again if the system is a held-out system.

Then every capacity test is measured from the saved windows.

Held-out systems are opened here only to list their files and to integrate their
capacity tests (the split is ``DEVELOPMENT`` below). Results are written per system to
``validation/results/parts/`` so that a stopped run continues where it was.

Usage:
    python validation/build_reference.py            # all 21 systems
    python validation/build_reference.py 17 19      # some systems
    python validation/build_reference.py measure    # only measure again, from the saved windows
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh import measure_capacity_test

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
WINDOWS = ROOT / "data" / "reduced" / "capacity_tests"
WINDOW_BEFORE = pd.Timedelta(hours=27)
WINDOW_AFTER = pd.Timedelta(hours=14)
RESULTS = ROOT / "validation" / "results"
PARTS = RESULTS / "parts"
RECORD = "https://zenodo.org/api/records/12091223"

#: Development systems, fixed on 2026-11-13 before any data was read. All others are held out.
DEVELOPMENT = (1, 4, 7, 11, 14, 17, 19)


def zenodo_files() -> dict[str, dict]:
    """File name -> size and MD5, from the Zenodo record (cached in validation/results)."""
    cache = RESULTS / "zenodo_files.json"
    if not cache.exists():
        with urllib.request.urlopen(RECORD, timeout=60) as response:
            record = json.load(response)
        files = {f["key"]: {"size": f["size"], "md5": f["checksum"].split(":")[1]} for f in record["files"]}
        cache.write_text(json.dumps(files, indent=1, sort_keys=True) + "\n")
    return json.loads(cache.read_text())


def fetch(system_id: int) -> Path:
    """Download one system's archive if needed; return its path."""
    name = f"Data_ID_{system_id:02d}.zip"
    target = RAW / name[:-4] / name
    if target.exists():
        return target
    info = zenodo_files()[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    digest = hashlib.md5()
    started = time.time()
    with urllib.request.urlopen(f"{RECORD}/files/{name}/content", timeout=120) as response, open(part, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
    if part.stat().st_size != info["size"] or digest.hexdigest() != info["md5"]:
        part.unlink()
        raise OSError(f"{name}: size or MD5 does not match the Zenodo record")
    part.rename(target)
    print(f"  downloaded {name}: {info['size'] / 1e9:.2f} GB in {time.time() - started:.0f} s", flush=True)
    return target


def month_quality(frame: pd.DataFrame) -> dict:
    """Plain data-quality figures of one month of data."""
    seconds = frame.index.to_series().diff().dt.total_seconds().to_numpy()[1:]
    span = int((frame.index[-1] - frame.index[0]).total_seconds()) + 1
    return {
        "first": frame.index[0],
        "last": frame.index[-1],
        "rows": len(frame),
        "missing_seconds": span - len(frame),
        "longest_gap_s": float(seconds.max()) if len(seconds) else 0.0,
        "interpolated_share": float(frame["interpolated"].mean()),
        "nan_share": float(frame.drop(columns="interpolated").isna().to_numpy().mean()),
        "current_min_a": float(frame["current_a"].min()),
        "current_max_a": float(frame["current_a"].max()),
        "voltage_min_v": float(frame["voltage_v"].min()),
        "voltage_max_v": float(frame["voltage_v"].max()),
        "temp_pack_min_c": float(frame["temp_pack_c"].min()),
        "temp_pack_max_c": float(frame["temp_pack_c"].max()),
        "charge_ah": float(frame["current_a"].clip(lower=0).sum() / 3600.0),
        "discharge_ah": float(-frame["current_a"].clip(upper=0).sum() / 3600.0),
    }


def run_system(system_id: int) -> None:
    done = PARTS / f"{system_id:02d}.done"
    if done.exists():
        print(f"system {system_id:02d}: already done", flush=True)
        return
    development = system_id in DEVELOPMENT
    print(f"system {system_id:02d} ({'development' if development else 'held-out'})", flush=True)
    archive = fetch(system_id)
    spec = figgener.pack_spec(system_id)

    months = figgener.list_months(archive, system_id)
    months.insert(0, "system_id", system_id)
    months.drop(columns="member").to_csv(PARTS / f"{system_id:02d}_coverage.csv", index=False)

    # Save one-second windows around each documented test, so the measurement can be
    # repeated without the archive.
    WINDOWS.mkdir(parents=True, exist_ok=True)
    tests = figgener.capacity_tests().query("system_id == @system_id")
    for number, start in enumerate(tests["start_time"], 1):
        try:
            frame, _ = figgener.read_figgener(archive, system_id, start - WINDOW_BEFORE, start + WINDOW_AFTER)
        except ValueError:
            print(f"  test {number}: no data in the archive around {start}", flush=True)
            continue
        frame.astype({c: "float32" for c in frame.columns if c != "interpolated"}).to_parquet(
            WINDOWS / f"ID_{system_id:02d}_test_{number}.parquet", compression="zstd"
        )
    print(f"  windows: {len(tests)} tests", flush=True)

    if development:
        quality = []
        for month in months["month"]:
            frame, _ = figgener.read_figgener(archive, system_id, month, month + pd.offsets.MonthEnd(0))
            quality.append({"system_id": system_id, "month": month.date(), **month_quality(frame)})
        pd.DataFrame(quality).to_csv(PARTS / f"{system_id:02d}_quality.csv", index=False)
        print(f"  quality: {len(quality)} months", flush=True)
    else:
        archive.unlink()
        print("  archive deleted (held-out system)", flush=True)
    done.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"))


def measure() -> None:
    """Measure all documented capacity tests from the saved one-second windows."""
    rows = []
    for system_id, group in figgener.capacity_tests().groupby("system_id"):
        spec = figgener.pack_spec(system_id)
        for number, start in enumerate(group["start_time"], 1):
            window = WINDOWS / f"ID_{system_id:02d}_test_{number}.parquet"
            which = "development" if system_id in DEVELOPMENT else "held-out"
            head = {"system_id": system_id, "test": number, "set": which, "chemistry": spec.chemistry}
            if not window.exists():
                extracted = (PARTS / f"{system_id:02d}.done").exists()
                status = "no data in the archive" if extracted else "archive not processed yet"
                rows.append({**head, "status": status, "documented_start": start})
                continue
            result = measure_capacity_test(pd.read_parquet(window).astype("float64", errors="ignore"), spec, start)
            rows.append({**head, **result.to_dict()})
    table = pd.DataFrame(rows)
    table = table.map(lambda x: round(x, 4) if isinstance(x, float) and np.isfinite(x) else x)
    table.to_csv(RESULTS / "capacity_tests.csv", index=False)
    print(f"capacity_tests.csv: {len(table)} rows, {int((table['status'] == 'ok').sum())} ok", flush=True)


def merge() -> None:
    """Join the per-system parts into the three result tables."""
    for kind in ("coverage", "quality"):
        parts = sorted(PARTS.glob(f"*_{kind}.csv"))
        frames = [pd.read_csv(p) for p in parts]
        frames = [f for f in frames if len(f)]
        if frames:
            name = {"coverage": "coverage.csv", "quality": "quality_development.csv"}[kind]
            table = pd.concat(frames, ignore_index=True)
            table = table.map(lambda x: round(x, 4) if isinstance(x, float) and np.isfinite(x) else x)
            table.to_csv(RESULTS / name, index=False)
            print(f"{name}: {len(table)} rows from {len(parts)} systems", flush=True)


def main(arguments: list[str]) -> None:
    PARTS.mkdir(parents=True, exist_ok=True)
    if arguments == ["measure"]:
        measure()
        return
    systems = [int(a) for a in arguments] or list(figgener.SYSTEM_IDS)
    for system_id in systems:
        run_system(system_id)
    merge()
    measure()


if __name__ == "__main__":
    main(sys.argv[1:])
