"""The HTML page of a record, on the shipped sample."""

from __future__ import annotations

import sys
from html import escape

import pandas as pd
import pytest

import besslib
from besslib.soh import annex_vii, report
from besslib.soh.record import ANNEX_VII_FIELDS
from besslib.soh.report import NO_HISTORY, NO_MATPLOTLIB, NOT_A_CERTIFICATE


@pytest.fixture(scope="module")
def sample():
    reduced, spec = besslib.io.read_sample()
    return reduced, spec, annex_vii(None, spec, reduced=reduced)


def test_page_holds_the_record(sample, tmp_path):
    _, _, record = sample
    path = report(record, tmp_path / "record.html")
    assert path == tmp_path / "record.html" and path.exists()
    page = path.read_text(encoding="utf-8")
    for code, _ in ANNEX_VII_FIELDS:
        assert f"<code>{code}</code>" in page
    assert record.capacity_config_hash in page and record.record_config_hash in page
    assert "Zenodo" in record.data_credit
    assert escape(record.data_credit) in page
    assert NOT_A_CERTIFICATE in page
    assert NO_HISTORY in page
    assert record.annex_vii_version in page
    assert "<table" in page
    assert "<script" not in page.lower()
    assert 'src="http' not in page and "url(http" not in page and "<link" not in page


def test_page_escapes_record_text(sample, tmp_path):
    _, _, record = sample
    page = report(record, tmp_path / "record.html").read_text(encoding="utf-8")
    assert "system&#x27;s own end-of-discharge level" in page
    assert "system's own end-of-discharge level" not in page


def test_page_is_deterministic(sample, tmp_path):
    _, _, record = sample
    a = report(record, tmp_path / "a.html").read_bytes()
    b = report(record, tmp_path / "b.html").read_bytes()
    assert a == b


def test_history_adds_figures(sample, tmp_path):
    pytest.importorskip("matplotlib")
    reduced, spec, record = sample
    history = [annex_vii(None, spec, reduced=reduced, at=pd.Timestamp(f"{y}-12-31")) for y in (2018, 2019, 2020)]
    a = report(record, tmp_path / "a.html", history=history).read_text(encoding="utf-8")
    b = report(record, tmp_path / "b.html", history=history).read_text(encoding="utf-8")
    assert "data:image/png;base64," in a
    assert a == b
    assert NO_HISTORY not in a


def test_history_without_matplotlib(sample, tmp_path, monkeypatch):
    reduced, spec, record = sample
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # import matplotlib now raises ImportError
    history = [annex_vii(None, spec, reduced=reduced, at=pd.Timestamp("2019-12-31"))]
    page = report(record, tmp_path / "record.html", history=history).read_text(encoding="utf-8")
    assert escape(NO_MATPLOTLIB) in page
    assert "data:image/png" not in page
