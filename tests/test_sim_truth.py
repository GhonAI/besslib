"""The truth tables, the writer, the manifest and the command line of besslib.sim."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pandas as pd
import pytest

import besslib
from besslib import frames, sim
from besslib.sim.truth import run_to_dir, tables


@pytest.fixture(scope="module")
def month():
    return next(iter(sim.run("synth", 1, 1)))


def test_tables_have_a_time_column_and_the_documented_steps(month):
    t = tables(month)
    assert set(t) == {"steps", "cells", "stops", "blocks", "balance"}
    assert list(t["steps"].columns) == ["time", "current_a", "voltage_v", "temp_sensor_c", "temp_ambient_c",
                                         "v_cell_max", "v_cell_min"]
    for name, table in t.items():
        assert table.columns[0] == "time" and "seconds" not in table.columns, name
        assert pd.api.types.is_datetime64_any_dtype(table["time"]), name
    assert len(t["steps"]) == len(month.frame)
    assert (t["steps"]["time"].to_numpy() == month.frame.index.to_numpy()).all()
    assert (t["balance"]["time"] == pd.Timestamp("2020-01-01")).all()
    first_stop = month.truth.stops.seconds.iloc[0]
    assert t["stops"]["time"].iloc[0] == pd.Timestamp("2020-01-01") + pd.Timedelta(seconds=first_stop)


def test_writer_manifest_and_files(tmp_path):
    out = tmp_path / "run"
    manifest = run_to_dir("synth", 2, 3, out, {"sensors.current_noise_a": 0.02}, true_frames=True)
    saved = json.loads((out / "manifest.json").read_text())
    assert saved == manifest
    assert manifest["seed"] == 3 and manifest["months"] == 2
    assert manifest["besslib_version"] == besslib.__version__
    assert manifest["case"]["sensors"]["current_noise_a"] == 0.02
    assert manifest["spec"]["cells_in_series"] == 14
    expected = {f"{kind}/2020-0{m}.parquet" for kind in ("frames", "true_frames") for m in (1, 2)}
    expected |= {f"truth/{t}/2020-0{m}.parquet" for t in ("steps", "cells", "stops", "blocks", "balance") for m in (1, 2)}
    assert set(manifest["files"]) == expected
    for rel, digest in manifest["files"].items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    frame = pd.read_parquet(out / "frames/2020-01.parquet")
    frames.check_frame(frame)
    frame.index = frame.index.as_unit("s")  # parquet keeps milliseconds at the least
    pd.testing.assert_frame_equal(frame, next(iter(sim.run("synth", 2, 3, {"sensors.current_noise_a": 0.02}))).frame)


def test_same_run_twice_gives_the_same_bytes_and_another_seed_does_not(tmp_path):
    a = run_to_dir("synth", 1, 5, tmp_path / "a")
    b = run_to_dir("synth", 1, 5, tmp_path / "b")
    c = run_to_dir("synth", 1, 6, tmp_path / "c")
    assert a["files"] == b["files"]
    assert (tmp_path / "a/manifest.json").read_bytes() == (tmp_path / "b/manifest.json").read_bytes()
    assert a["files"]["frames/2020-01.parquet"] != c["files"]["frames/2020-01.parquet"]


def test_cli_run_and_cases(tmp_path):
    out = tmp_path / "cli"
    done = subprocess.run(
        [sys.executable, "-m", "besslib.sim", "run", "--case", "synth", "--months", "1", "--seed", "2",
         "--out", str(out), "--set", "sensors.voltage_noise_v=0.002", "--set", "case.step_s=10"],
        capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["months"] == 1 and manifest["case"]["sensors"]["voltage_noise_v"] == 0.002
    assert (out / "frames/2020-01.parquet").exists() and not (out / "true_frames").exists()
    listed = subprocess.run([sys.executable, "-m", "besslib.sim", "cases"], capture_output=True, text=True, timeout=60)
    assert listed.returncode == 0 and "synth:" in listed.stdout and "demo:" in listed.stdout
    bad = subprocess.run([sys.executable, "-m", "besslib.sim", "run", "--case", "synth", "--out", str(out),
                          "--set", "novalue"], capture_output=True, text=True, timeout=60)
    assert bad.returncode != 0
