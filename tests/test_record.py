"""Tests of the Annex VII record."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from besslib.soh import _core
from besslib.soh.capacity import estimate_capacity
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.record import FRAME_COLUMNS, NO_LIMITS, NOT_IN_DATASET, annex_vii
from besslib.soh.reduce import reduce_parts
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs
from besslib.soh.usage import Limits
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()
OK = _core.STATUS_TEXT[_core.OK]
# Three months of a pack without seasons: a 60-day level window and no warm-up.
SHORT = CapacityConfig(level_window_days=60.0, level_warmup_days=0.0)

# Regulation (EU) 2023/1542, Annex VII, stationary battery energy storage systems
# (consolidated version of 31 July 2025), typed here on purpose: the test checks the module's list.
LEGAL = [
    ("A1", "the remaining capacity"),
    ("A2", "where possible, the remaining power capability"),
    ("A3", "where possible, the remaining round trip efficiency"),
    ("A4", "the evolution of self-discharging rates"),
    ("A5", "where possible, the ohmic resistance"),
    ("B1", "the date of manufacture of the battery and, where appropriate, the date of putting into service"),
    ("B2", "the energy throughput"),
    ("B3", "the capacity throughput"),
    ("B4", "the tracking of harmful events, such as the number of deep discharge events, time spent in extreme "
           "temperatures, time spent charging in extreme temperatures"),
    ("B5", "the number of full equivalent charge-discharge cycles"),
]


@pytest.fixture(scope="module")
def reduced():
    return reduce_parts(frames(PACK, 3, step_s=10, seed=3), SPEC, SHORT)


@pytest.fixture(scope="module")
def record(reduced):
    return annex_vii(None, SPEC, reduced=reduced, capacity_config=SHORT)


def test_ten_fields_in_order_with_the_legal_names(record, reduced):
    table = record.to_frame()
    assert list(table.columns) == FRAME_COLUMNS
    assert list(zip(table["code"], table["field"])) == LEGAL
    assert record.date == reduced.daily["t_last"].max()
    assert record.capacity_config_hash == config_hash(SHORT)
    assert record.record_config_hash == config_hash(RecordConfig())
    assert record.reference_capacity_ah == SPEC.reference_capacity_ah
    assert record.sample_s == 10.0
    for f in record.fields:
        if f.status == OK:
            assert math.isfinite(f.value) and f.low <= f.value <= f.high and f.events > 0
        elif f.code != "B1":
            assert f.status.startswith("not possible from this data: ") and math.isnan(f.value)
    # What three months of this pack allow: capacity, charge loss, efficiency, the resistance
    # at 25 degrees C and the counts; not the growth of the resistance (it needs a year).
    status = dict(zip(table["code"], table["status"]))
    for code in ("A1", "A3", "A4", "A5", "B2", "B3", "B4", "B5"):
        assert status[code] == OK, code
    assert status["A2"].startswith("not possible from this data: ")
    assert status["B1"] == NOT_IN_DATASET
    eta = record.field("A3")
    assert 0.0 < eta.value < 1.0
    assert eta.details["voltage_part"] * eta.details["coulombic_part"] == pytest.approx(eta.value)


def test_capacity_is_the_capacity_estimator(record, reduced):
    rests = operating_rests(reduced.rests, SPEC, SHORT)
    pairs = rest_pairs(rest_chain(rests, SPEC, SHORT), SHORT)
    cap = estimate_capacity(None, SPEC, config=SHORT, pairs=pairs, levels_from=levels_from(rests, SHORT),
                            at=[record.date]).iloc[0]
    a1 = record.field("A1")
    assert (a1.value, a1.low, a1.high) == (cap["capacity_ah"], cap["capacity_ah_low"], cap["capacity_ah_high"])
    assert a1.details["share_of_reference"] == cap["soh_capacity"]
    assert record.field("A4").value == pytest.approx(-1000.0 * cap["offset_current_a"])


def test_a_record_too_short_for_anything(reduced):
    at = reduced.daily["t_last"].iloc[1]  # two days of data
    record = annex_vii(None, SPEC, reduced=reduced, at=at, capacity_config=SHORT)
    status = dict(zip(record.to_frame()["code"], record.to_frame()["status"]))
    for code in ("A1", "A2", "A3", "A4", "A5"):
        assert status[code].startswith("not possible from this data: "), code
        assert math.isnan(record.field(code).value)
    assert status["A3"] == "not possible from this data: too few closed loops"
    # The counts stop at the date: two whole days.
    b5 = record.field("B5")
    assert status["B5"] == OK and b5.events == 2.0
    assert b5.details["counted_to"] == reduced.daily["day"].iloc[1]
    # With the frozen settings no level exists in the first year: the warm-up reason.
    frozen = annex_vii(None, SPEC, reduced=reduced)
    assert frozen.field("A1").status == _core.STATUS_TEXT[_core.NO_LEVELS]
    assert frozen.field("A3").status == _core.STATUS_TEXT[_core.NO_LEVELS]


def test_json_round_trip_and_determinism(record, reduced):
    text = record.to_json()
    assert json.loads(text) == record.to_dict()
    again = annex_vii(None, SPEC, reduced=reduced, capacity_config=SHORT).to_json()
    assert again == text
    data = json.loads(text)
    assert [f["code"] for f in data["fields"]] == [c for c, _ in LEGAL]
    assert data["fields"][1]["value"] is None  # NaN is written as null
    assert "NaN" not in text


def test_limits(record, reduced):
    b4 = record.field("B4").details
    for name in ("deep_discharge_events", "hours_below_temp_low", "hours_above_temp_high",
                 "hours_charging_below_temp_low", "hours_charging_above_temp_high"):
        assert b4[name] == {"value": None, "status": NO_LIMITS}
    # The observed facts are there without limits.
    assert b4["voltage_min_v"] < b4["voltage_max_v"]
    assert isinstance(b4["hours_per_temperature_band"], pd.DataFrame)
    limited = annex_vii(None, SPEC, reduced=reduced, capacity_config=SHORT, limits=Limits(voltage_min_v=0.0, temp_high_c=30.0))
    d = limited.field("B4").details
    assert d["deep_discharge_events"]["value"] == 0 and d["deep_discharge_events"]["status"] == OK
    assert d["hours_above_temp_high"]["value"] == 0.0
    assert d["hours_below_temp_low"]["status"] == "not possible from this data: no limit given for temp_low_c"
