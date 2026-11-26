"""The shipped sample: its record equals the held-out result file, and it carries its credit."""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

import pyarrow.parquet as pq
import pytest

import besslib
from besslib.soh import annex_vii

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
KEYS = (
    "creator", "title", "source", "licence", "disclaimer", "modified", "capacity_config_hash", "record_config_hash",
)


@pytest.fixture(scope="module")
def sample():
    return besslib.io.read_sample()


def _validation_mask(system_id: int):
    """The capacity-test mask of ``validation/run_annex_vii.py``, from the scripts themselves."""
    if not (VALIDATION / "results" / "capacity_tests.csv").exists():
        pytest.skip("validation/ is not present (the check needs the repository checkout)")
    sys.path.insert(0, str(VALIDATION))
    try:
        from run_capacity import all_tests
        from tune_capacity import masks_for
    finally:
        sys.path.remove(str(VALIDATION))
    return masks_for(all_tests(), system_id)


def test_sample_reproduces_the_held_out_record(sample):
    """The shipped sample, reduced once with the frozen settings, gives the held-out record of
    system 05 at today's settings exactly: every field, band, count, method text and hash."""
    import json

    expected = VALIDATION / "results" / "phase3" / "system_05_record.json"
    if not expected.exists():
        pytest.skip("validation/ is not present (the check needs the repository checkout)")
    reduced, spec = sample
    record = annex_vii(None, spec, reduced=reduced, mask=_validation_mask(5))
    assert json.loads(record.to_json()) == json.loads(expected.read_text())


def test_metadata_in_every_file():
    folder = resources.files("besslib.data").joinpath("sample_05")
    for name in ("daily", "steps", "rests"):
        with resources.as_file(folder.joinpath(f"{name}.parquet")) as path:
            meta = pq.read_schema(path).metadata
        for key in KEYS:
            assert meta.get(key.encode(), b"").strip(), (name, key)
        assert b"CC BY 4.0" in meta[b"licence"]
        assert b"10.5281/zenodo.12091223" in meta[b"source"]
    assert "CC BY 4.0" in folder.joinpath("NOTICE.md").read_text(encoding="utf-8")


def test_metadata_hashes_are_the_frozen_settings():
    """The sample was reduced with the settings of release 0.1.0; version 0.2 changes no
    setting the reduction uses, so the sample is unchanged and keeps their hashes."""
    from besslib.soh import CapacityConfig, RecordConfig, config_hash

    folder = resources.files("besslib.data").joinpath("sample_05")
    with resources.as_file(folder.joinpath("daily.parquet")) as path:
        meta = pq.read_schema(path).metadata
    assert meta[b"capacity_config_hash"].decode() == config_hash(CapacityConfig().v0_1())
    assert meta[b"record_config_hash"].decode() == config_hash(RecordConfig().v0_1())


def test_quickstart_without_mask(sample):
    reduced, spec = sample
    record = besslib.soh.annex_vii(None, spec, reduced=reduced)
    assert [f.code for f in record.fields] == ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5"]
    assert record.data_credit and "CC BY 4.0" in record.data_credit
