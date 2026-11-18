"""The parameters of Annex VII of Regulation (EU) 2023/1542 for one battery at one date.

``annex_vii`` computes, from operating data, the ten parameters that Annex VII lists for
stationary battery energy storage systems: five for the state of health (part A) and
five for the expected lifetime (part B). Each field carries the legal wording, besslib's
own label for what is actually computed, a value with a band, the number of events it
rests on, its method with the source, and a status: ``estimated`` or ``not possible
from this data`` with the reason. Nothing is filled in where the data do not allow it.

The record computes the Annex VII parameters; it does not certify them. It is not a
compliance certificate and not legal advice.

Estimates (A1-A5) leave the ``mask`` intervals out (for example capacity tests) and use
only data up to the record's date. Counts (B2-B5) are facts about the battery: they count
everything, masked intervals included, over the whole days of data up to the date, and
they start on the first day of data, not on the battery's first day.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.capacity import estimate_capacity
from besslib.soh.charge_loss import charge_loss, charge_loss_trend
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.efficiency import estimate_efficiency
from besslib.soh.reduce import Reduced, reduce_frame
from besslib.soh.resistance import estimate_resistance, resistance_growth
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs
from besslib.soh.usage import Limits, count_usage

#: The version of the Regulation the wording below is taken from.
ANNEX_VII_VERSION = "Regulation (EU) 2023/1542, Annex VII, consolidated version of 31 July 2025"

#: The parameters of Annex VII for stationary battery energy storage systems, in the
#: Annex's order and wording (part A: state of health; part B: expected lifetime).
ANNEX_VII_FIELDS = (
    ("A1", "the remaining capacity"),
    ("A2", "where possible, the remaining power capability"),
    ("A3", "where possible, the remaining round trip efficiency"),
    ("A4", "the evolution of self-discharging rates"),
    ("A5", "where possible, the ohmic resistance"),
    ("B1", "the date of manufacture of the battery and, where appropriate, the date of putting into service"),
    ("B2", "the energy throughput"),
    ("B3", "the capacity throughput"),
    (
        "B4",
        "the tracking of harmful events, such as the number of deep discharge events, time spent in extreme "
        "temperatures, time spent charging in extreme temperatures",
    ),
    ("B5", "the number of full equivalent charge-discharge cycles"),
)
_LEGAL = dict(ANNEX_VII_FIELDS)

ESTIMATED = _core.STATUS_TEXT[_core.OK]
NOT_IN_DATASET = "not possible from this data: not in the dataset"
NO_LIMITS = "not possible from this data: no limits given"

_FIGGENER = "Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9"
_SENSORS = (
    "band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width "
    "(JCGM 100:2008, 4.3.7)"
)
_RESISTANCE = (
    "dV/dI of current steps between consecutive samples (Barai et al., Sci. Rep. 8, 21 (2018), "
    "doi:10.1038/s41598-017-18424-5; Schweiger et al., Sensors 10, 5604 (2010), doi:10.3390/s100605604)"
)
_METHODS = {
    "A1": f"capacity between full and empty rests by coulomb counting, fitted with the offset current ({_FIGGENER})",
    "A2": f"ratio of the resistance on the first day to the resistance at the date, from {_RESISTANCE}; no power test",
    "A3": (
        "energy out / energy in over closed loops between full rests (Regulation (EU) 2023/1542, Annex IV (6)); "
        "band: the ratio over these loops had each closed exactly, from the error of the state of charge at their "
        "end rests, estimated as the mean lag-2 product of the loop scores minus the mean lag-1 product"
    ),
    "A4": f"minus the offset current of the capacity fit ({_FIGGENER}); band with the current sensor's gain error",
    "A5": _RESISTANCE,
    "B1": "the pack's documents (PackSpec), passed through",
    "B2": f"trapezoid sums of charging and discharging power over the measured time; {_SENSORS}",
    "B3": f"trapezoid sums of charging and discharging current over the measured time; {_SENSORS}",
    "B4": (
        "measured hours per pack-temperature band and mode; discharges that reached the system's own "
        "end-of-discharge level (empty rests); voltage extremes; counts against limits when limits are given"
    ),
    "B5": f"capacity throughput / (2 x reference capacity); {_SENSORS}",
}

#: Columns of ``AnnexVII.to_frame``.
FRAME_COLUMNS = ["code", "field", "value", "low", "high", "unit", "events", "status"]

#: Significant digits of the floats in ``AnnexVII.to_json``.
JSON_DIGITS = 8


@dataclass
class Field:
    """One parameter of the record: the legal ``field`` name and besslib's own ``label``."""

    code: str
    field: str
    label: str
    value: float
    low: float
    high: float
    unit: str
    method: str
    events: float
    status: str
    details: dict = field(default_factory=dict)


@dataclass
class AnnexVII:
    """The Annex VII parameters of one battery at one date (module docstring).

    It computes the parameters; it does not certify them and is not a compliance
    certificate. ``fields`` holds the ten parameters in the order of the Annex.
    """

    date: pd.Timestamp
    pack: dict
    reference_capacity_ah: float
    capacity_config_hash: str
    record_config_hash: str
    data_credit: str | None
    sample_s: float
    level: float
    annex_vii_version: str
    fields: list

    def field(self, code: str) -> Field:
        """The field with this code (``"A1"`` ... ``"B5"``)."""
        for f in self.fields:
            if f.code == code:
                return f
        raise KeyError(code)

    def to_frame(self) -> pd.DataFrame:
        """One row per field: code, field (legal name), value, low, high, unit, events, status."""
        return pd.DataFrame(
            [[getattr(f, c) for c in FRAME_COLUMNS] for f in self.fields], columns=FRAME_COLUMNS
        )

    def to_dict(self) -> dict:
        """The whole record as plain values (dates as ISO text, NaN as None)."""
        out = {
            "date": self.date,
            "pack": self.pack,
            "reference_capacity_ah": self.reference_capacity_ah,
            "capacity_config_hash": self.capacity_config_hash,
            "record_config_hash": self.record_config_hash,
            "data_credit": self.data_credit,
            "sample_s": self.sample_s,
            "level": self.level,
            "annex_vii_version": self.annex_vii_version,
            "not_a_certificate": "computes the Annex VII parameters; does not certify them",
            "fields": [
                {
                    "code": f.code, "field": f.field, "label": f.label, "value": f.value, "low": f.low,
                    "high": f.high, "unit": f.unit, "method": f.method, "events": f.events, "status": f.status,
                    "details": f.details,
                }
                for f in self.fields
            ],
        }
        return _plain(out)

    def to_json(self) -> str:
        """Deterministic JSON: sorted keys, floats to ``JSON_DIGITS`` significant digits, NaN as null."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=1, allow_nan=False) + "\n"


def _plain(value):
    """Plain JSON values: rounded floats, None for NaN, ISO text for times, lists for tables."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return [_plain(r) for r in value.to_dict("records")]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return float(f"{v:.{JSON_DIGITS}g}") if math.isfinite(v) else None
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat() if not np.isnat(value) else None
    return value if isinstance(value, str) else str(value)


def _nan_field(code: str, label: str, unit: str, status: str, details: dict | None = None) -> Field:
    return Field(code, _LEGAL[code], label, math.nan, math.nan, math.nan, unit, _METHODS[code], 0.0, status,
                 details or {})



def _row_value(row, name: str):
    """``(value, low, high)`` of a quantity in a row, NaN unless the row is estimated."""
    if row["status"] != ESTIMATED:
        return math.nan, math.nan, math.nan
    return float(row[name]), float(row[f"{name}_low"]), float(row[f"{name}_high"])


def _until(reduced: Reduced, at: pd.Timestamp) -> Reduced:
    """The reduced tables up to ``at``: the days whose samples all lie at or before it, the rests that start
    at or before it. Days are whole: a day that goes on after ``at`` is left out."""
    d = reduced.daily
    last = d["t_last"]
    keep = (last <= at) | (last.isna() & (d["day"] < at.normalize()))
    days = d[keep.to_numpy()]
    # Days without samples after the last kept day add nothing; drop them, so the record ends at its data.
    if len(days) and days["t_last"].notna().any():
        days = days[days["day"] <= days.loc[days["t_last"].notna(), "day"].max()]
    rests = reduced.rests[reduced.rests["start"] <= at]
    steps = reduced.steps[reduced.steps["time"] <= at]
    return Reduced(daily=days.reset_index(drop=True), steps=steps, rests=rests, sample_s=reduced.sample_s)


# --- A1 and A4: the capacity fit ---------------------------------------------------------


def _capacity_fields(reduced, spec, at, mask, level, config, capacity_config) -> list[Field]:
    rests = operating_rests(reduced.rests, spec, capacity_config, mask=mask)
    pairs = rest_pairs(rest_chain(rests, spec, capacity_config, mask=mask), capacity_config)
    ready = levels_from(rests, capacity_config)
    first = reduced.daily["day"].min()
    times = pd.DatetimeIndex(pd.date_range(first, at, freq="ME").append(pd.DatetimeIndex([at])).unique()).sort_values()
    cap = estimate_capacity(None, spec, config=capacity_config, pairs=pairs, levels_from=ready, at=times)
    now = cap.loc[at]
    value, low, high = _row_value(now, "capacity_ah")
    ref = float(spec.reference_capacity_ah)
    a1 = Field(
        "A1", _LEGAL["A1"], "remaining capacity between the system's own end of charge and end of discharge",
        value, low, high, "Ah", _METHODS["A1"], float(now["n_pairs"]), now["status"],
        {
            "share_of_reference": value / ref,
            "share_of_reference_low": low / ref,
            "share_of_reference_high": high / ref,
            "reference_capacity_ah": ref,
            "n_full_empty_pairs": float(now["n_full_empty"]),
            "n_same_kind_pairs": float(now["n_same_kind"]),
            "band_level": capacity_config.level,
            "levels_from": ready,
        },
    )
    loss = charge_loss(cap, spec, level=level, config=config)
    trend = charge_loss_trend(loss, config=config, level=level)
    row = loss.loc[at]
    value, low, high = _row_value(row, "charge_loss_ma")
    pv, pl, ph = _row_value(row, "charge_loss_percent_per_month")
    a4 = Field(
        "A4", _LEGAL["A4"], "apparent charge loss (an upper bound for self-discharge)", value, low, high, "mA",
        _METHODS["A4"], float(row["n_pairs"]), row["status"],
        {
            "what_it_holds": (
                "the charge the battery loses that its meter does not see: cell self-discharge, BMS supply, "
                "balancing and the current sensor's zero offset; mostly not cell self-discharge"
            ),
            "percent_of_reference_per_month": pv,
            "percent_of_reference_per_month_low": pl,
            "percent_of_reference_per_month_high": ph,
            "reference_capacity_ah": ref,
            "evolution": {k: v for k, v in trend.items() if k != "year_end"},
            "year_end_values": trend["year_end"],
        },
    )
    return [a1, a4]


# --- A3: round-trip efficiency -------------------------------------------------------------


def _efficiency_field(reduced, spec, at, mask, level, config, capacity_config) -> Field:
    common = dict(config=config, capacity_config=capacity_config, reduced=reduced, at=[at], mask=mask)
    full = estimate_efficiency(None, spec, level=level, kind="full", **common).iloc[0]
    empty = estimate_efficiency(None, spec, level=level, kind="empty", **common).iloc[0]
    value, low, high = _row_value(full, "round_trip_efficiency")
    ev, el, eh = _row_value(empty, "round_trip_efficiency")
    return Field(
        "A3", _LEGAL["A3"], "round-trip efficiency (DC, at the battery terminals) over full-to-full loops",
        value, low, high, "share", _METHODS["A3"], float(full["n_loops"]), full["status"],
        {
            "coulombic_part": float(full["coulombic_efficiency"]),
            "voltage_part": float(full["voltage_efficiency"]),
            "rho": float(full["rho"]),
            "dof": float(full["dof"]),
            "rest_error_wh": float(full["rest_error_wh"]),
            "empty_loops": {
                "value": ev, "low": el, "high": eh, "n_loops": float(empty["n_loops"]), "status": empty["status"],
            },
            "limits": (
                "DC at the battery terminals, not the AC efficiency of the plant; depends on the use "
                "(Schimpe et al., Applied Energy 210, 211 (2018), doi:10.1016/j.apenergy.2017.10.129); the band "
                "holds the ratio over these loops, not the efficiency under other use; a change of the loops' own "
                "efficiency within two loops makes the band too narrow; below one degree of freedom there is no "
                "band; the energy in "
                "also restores the charge the meter does not see; the band holds no zero offset of the current "
                "sensor"
            ),
        },
    )


# --- A2 and A5: resistance -----------------------------------------------------------------


def _resistance_fields(reduced, spec, at, mask, level, config, capacity_config) -> list[Field]:
    common = dict(config=config, capacity_config=capacity_config, reduced=reduced, mask=mask)
    table = estimate_resistance(None, spec, level=level, at=[at], **common)
    growth = resistance_growth(None, spec, level=level, at=at, **common)
    seconds = f"{reduced.sample_s:g}-second resistance"
    head = table[table["headline"].astype(bool)]
    bands = table.sort_values("temp_band_c")[
        ["temp_band_c", "resistance_ohm", "resistance_ohm_low", "resistance_ohm_high", "n_days", "n_steps", "status"]
    ]
    if len(head):
        row = head.iloc[0]
        value, low, high = _row_value(row, "resistance_ohm")
        events, status, band_c = float(row["n_days"]), row["status"], float(row["temp_band_c"])
    else:
        value = low = high = math.nan
        events, band_c = 0.0, math.nan
        status = "not possible from this data: no temperature band at the reference temperature"
    a5 = Field(
        "A5", _LEGAL["A5"], f"{seconds}, pack temperature band at {band_c:g} degrees C", value, low, high, "ohm",
        _METHODS["A5"], events, status,
        {
            "sample_s": reduced.sample_s,
            "headline_band_c": band_c,
            "n_steps": float(head.iloc[0]["n_steps"]) if len(head) else 0.0,
            "bands": bands,
            "growth_percent_per_year": growth["growth_percent_per_year"],
            "growth_percent_per_year_low": growth["growth_percent_per_year_low"],
            "growth_percent_per_year_high": growth["growth_percent_per_year_high"],
            "growth_status": growth["status"],
            "growth_n_days": growth["n_days"],
            "growth_span_years": growth["span_years"],
            "what_it_is": (
                f"the voltage step over the current step between two samples {reduced.sample_s:g} s apart; it "
                "depends on that timescale, the state of charge and the temperature, and is not the cell's "
                "ohmic resistance alone"
            ),
        },
    )
    ok = growth["status"] == ESTIMATED
    share = float(growth["power_capability_share"]) if ok else math.nan
    a2 = Field(
        "A2", _LEGAL["A2"], f"power capability share, derived from the {seconds}, not measured",
        share, float(growth["power_capability_share_low"]) if ok else math.nan,
        float(growth["power_capability_share_high"]) if ok else math.nan, "share of the first day", _METHODS["A2"],
        float(growth["n_days"]), growth["status"],
        {
            "power_capability_w": growth["power_capability_w"],
            "power_capability_w_low": growth["power_capability_w_low"],
            "power_capability_w_high": growth["power_capability_w_high"],
            "power_capability_w_status": growth["power_capability_w_status"],
            "resistance_start_ohm": growth["resistance_start_ohm"],
            "resistance_at_ohm": growth["resistance_at_ohm"],
            "first_day": growth["first_day"],
            "what_it_is": (
                "the resistance on the first day divided by the resistance at the date: the share of the first "
                "day's power that the same voltage limits allow now, if the resistance alone limits the power"
            ),
        },
    )
    return [a2, a5]


# --- B: the counts ---------------------------------------------------------------------------


def _date_field(spec: PackSpec) -> Field:
    made = spec.date_of_manufacture
    service = spec.date_put_into_service
    status = "stated in the pack's documents" if made is not None else NOT_IN_DATASET
    return _nan_field(
        "B1", "date of manufacture; date of putting into service", "date", status,
        {
            "date_of_manufacture": made,
            "date_of_manufacture_status": "stated in the pack's documents" if made is not None else NOT_IN_DATASET,
            "date_put_into_service": service,
            "date_put_into_service_status": (
                "stated in the pack's documents" if service is not None else NOT_IN_DATASET
            ),
        },
    )


def _usage_fields(reduced, spec, at, limits, level, config, capacity_config) -> list[Field]:
    use = count_usage(None, spec, limits, config=config, capacity_config=capacity_config, reduced=_until(reduced, at),
                      level=level)
    days = float(use.n_days_with_data)
    ok = use.first_day is not None and math.isfinite(use.energy_throughput_kwh)
    status = ESTIMATED if ok else use.status
    coverage = {
        "counted_from": use.first_day,
        "counted_to": use.last_day,
        "since": "the first day of data, not a lifetime total",
        "hours_measured": use.hours_measured,
        "hours_missing": use.hours_missing,
        "share_interpolated": use.share_interpolated,
        "n_days_with_data": use.n_days_with_data,
        "note": use.status,
    }
    b2 = Field(
        "B2", _LEGAL["B2"], "energy throughput (in + out) since the first day of data", use.energy_throughput_kwh,
        use.energy_throughput_kwh_low, use.energy_throughput_kwh_high, "kWh", _METHODS["B2"], days, status,
        {"energy_in_kwh": use.energy_in_kwh, "energy_out_kwh": use.energy_out_kwh, **coverage},
    )
    b3 = Field(
        "B3", _LEGAL["B3"], "capacity throughput (in + out) since the first day of data", use.capacity_throughput_ah,
        use.capacity_throughput_ah_low, use.capacity_throughput_ah_high, "Ah", _METHODS["B3"], days, status,
        {"charge_in_ah": use.charge_in_ah, "charge_out_ah": use.charge_out_ah, **coverage},
    )
    b5 = Field(
        "B5", _LEGAL["B5"], "full equivalent cycles since the first day of data", use.full_equivalent_cycles,
        use.full_equivalent_cycles_low, use.full_equivalent_cycles_high, "cycles", _METHODS["B5"], days, status,
        {"reference_capacity_ah": use.reference_capacity_ah, **coverage},
    )
    given = limits is not None and any(getattr(limits, n) is not None for n in limits.__dataclass_fields__)

    def against(value, limit_name):
        if not given:
            return {"value": None, "status": NO_LIMITS}
        if getattr(limits, limit_name) is None:
            return {"value": None, "status": f"not possible from this data: no limit given for {limit_name}"}
        if value is None:
            return {"value": None, "status": f"not possible from this data: {limit_name} lies outside -19 to 69 degrees C"}
        return {"value": value, "status": ESTIMATED, "limit": getattr(limits, limit_name)}

    n_empty = float(use.n_empty_rests)
    b4 = Field(
        "B4", _LEGAL["B4"], "discharges to the system's own end-of-discharge level (observed; not a limit count)",
        n_empty if ok else math.nan, n_empty if ok else math.nan, n_empty if ok else math.nan, "events",
        _METHODS["B4"], n_empty if ok else 0.0, status,
        {
            "hours_per_temperature_band": use.temp_hours,
            "hours_no_temperature": use.hours_no_temperature,
            "temperature_measured": "pack temperature, not cell temperature",
            "voltage_min_v": use.voltage_min_v,
            "voltage_max_v": use.voltage_max_v,
            "voltage_min_cell_v": use.voltage_min_cell_v,
            "voltage_max_cell_v": use.voltage_max_cell_v,
            "cell_voltage_note": "pack voltage / cells in series: an average, one cell can be lower or higher",
            "deep_discharge_events": against(use.n_deep_discharge, "voltage_min_v"),
            "hours_below_temp_low": against(use.hours_below_temp_low, "temp_low_c"),
            "hours_above_temp_high": against(use.hours_above_temp_high, "temp_high_c"),
            "hours_charging_below_temp_low": against(use.hours_charging_below_temp_low, "charge_temp_low_c"),
            "hours_charging_above_temp_high": against(use.hours_charging_above_temp_high, "charge_temp_high_c"),
            "limits_status": use.limits_status,
            **coverage,
        },
    )
    return [b2, b3, b4, b5]


# --- the record --------------------------------------------------------------------------------


def annex_vii(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    level: float = 0.90,
    limits: Limits | None = None,
    reduced: Reduced | None = None,
    *,
    at=None,
    mask=None,
    config: RecordConfig | None = None,
    capacity_config: CapacityConfig | None = None,
) -> AnnexVII:
    """The Annex VII parameters of one battery at the date ``at`` (module docstring).

    Parameters
    ----------
    frame
        Operating data in the besslib frame format; reduced first (``reduce.reduce_frame``).
        May be ``None`` when ``reduced`` is given.
    spec
        The pack: reference capacity, dates, data credit (``spec.extra["data_credit"]``).
    level
        Band level of A2-A5 and B2-B5. A1 uses the level of ``capacity_config``.
    limits
        Operating limits for the harmful events of B4. Without them, the counts against
        limits are ``not possible from this data: no limits given``; the observed facts
        (hours per temperature band, voltage extremes, discharges to the system's own
        end-of-discharge level) are always given.
    reduced
        The reduced tables (``reduce.reduce_parts`` or ``reduce.reduce_frame``).
    at
        The date of the record. Default: the last sample.
    mask
        Intervals ``(start, end)`` left out of the estimates A1-A5 (for example capacity
        tests). The counts B2-B5 count everything.
    config, capacity_config
        Settings; the defaults are the frozen ones. Both hashes are in the record.

    Returns
    -------
    AnnexVII with the ten fields in the order of the Annex. It computes the parameters; it
    does not certify them.
    """
    config = config or RecordConfig()
    capacity_config = capacity_config or CapacityConfig()
    if reduced is None:
        if frame is None:
            raise ValueError("give a frame or reduced tables")
        reduced = reduce_frame(frame, spec, capacity_config)
    if len(reduced.daily) == 0 or reduced.daily["t_last"].isna().all():
        raise ValueError("the record has no samples")
    at = pd.Timestamp(reduced.daily["t_last"].max() if at is None else at)
    args = (reduced, spec, at, mask, level, config, capacity_config)
    a1, a4 = _capacity_fields(*args)
    a3 = _efficiency_field(*args)
    a2, a5 = _resistance_fields(*args)
    b2, b3, b4, b5 = _usage_fields(reduced, spec, at, limits, level, config, capacity_config)
    fields = [a1, a2, a3, a4, a5, _date_field(spec), b2, b3, b4, b5]
    assert [f.code for f in fields] == [c for c, _ in ANNEX_VII_FIELDS]
    for f in fields:
        if f.status == ESTIMATED and not (f.low <= f.value <= f.high):
            raise RuntimeError(f"field {f.code}: an estimate outside its own band")
    return AnnexVII(
        date=at,
        pack={
            "name": spec.name,
            "chemistry": spec.chemistry,
            "nominal_capacity_ah": spec.nominal_capacity_ah,
            "nominal_energy_kwh": spec.nominal_energy_kwh,
            "cells_in_series": spec.cells_in_series,
            "cells_in_parallel": spec.cells_in_parallel,
        },
        reference_capacity_ah=float(spec.reference_capacity_ah),
        capacity_config_hash=config_hash(capacity_config),
        record_config_hash=config_hash(config),
        data_credit=spec.extra.get("data_credit"),
        sample_s=float(reduced.sample_s),
        level=level,
        annex_vii_version=ANNEX_VII_VERSION,
        fields=fields,
    )
