"""What the battery has been through: energy and charge moved, cycles, temperature and voltage extremes.

Everything is counted from the reduced tables (``besslib.soh.reduce``): sums of the
measured data, not estimates. The data starts when the measurement starts, so every total
is *since the first day of data*, never a lifetime total of the battery. Time that was
not measured (gaps, steps with a missing current) is reported as ``hours_missing`` and is
not counted: the totals are lower bounds of what happened in that time.

Charge and energy are trapezoid sums over the steps between samples (see ``reduce``).
Their bands hold only the systematic sensor term: a gain error of the current sensor
(and of the voltage sensor, for energy) moves every reading the same way and does not
average out. Half-width = z x relative standard uncertainty x value, with the relative
standard uncertainty of ``RecordConfig`` (accuracy / sqrt(3), JCGM 100:2008, 4.3.7,
Eq. 7) and ``z`` the two-sided normal quantile of ``level``; the accuracies are those of
Figgener et al., Nature Energy 9, 1438 (2024), Supplementary Table 2.

A *full equivalent cycle* is the charge moved in and out divided by twice the reference
capacity of the pack.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.reduce import (
    MODES,
    TEMP_BIN_LOW_C,
    TEMP_BINS,
    Reduced,
    reduce_frame,
    temp_hours,
)
from besslib.soh.rests import operating_rests

NO_LIMITS = "not possible from this data: no limits given"
_BAND_WIDTH = 5  # degrees C of one band of the ``temp_hours`` table


@dataclass(frozen=True)
class Limits:
    """Operating limits to count against (all optional): pack temperature (degrees C), the same while
    charging, and the lowest allowed pack voltage (V)."""

    temp_low_c: float | None = None
    temp_high_c: float | None = None
    charge_temp_low_c: float | None = None
    charge_temp_high_c: float | None = None
    voltage_min_v: float | None = None


@dataclass
class Usage:
    """The counts of ``count_usage``; see there for the definitions. ``.to_dict()`` gives plain values."""

    first_day: pd.Timestamp | None
    last_day: pd.Timestamp | None
    hours_measured: float
    hours_missing: float
    share_interpolated: float
    energy_in_kwh: float
    energy_out_kwh: float
    energy_throughput_kwh: float
    energy_throughput_kwh_low: float
    energy_throughput_kwh_high: float
    charge_in_ah: float
    charge_out_ah: float
    capacity_throughput_ah: float
    capacity_throughput_ah_low: float
    capacity_throughput_ah_high: float
    full_equivalent_cycles: float
    full_equivalent_cycles_low: float
    full_equivalent_cycles_high: float
    reference_capacity_ah: float
    temp_hours: pd.DataFrame
    hours_no_temperature: float
    n_empty_rests: int
    voltage_min_v: float
    voltage_max_v: float
    voltage_min_cell_v: float
    voltage_max_cell_v: float
    hours_below_temp_low: float | None
    hours_above_temp_high: float | None
    hours_charging_below_temp_low: float | None
    hours_charging_above_temp_high: float | None
    n_deep_discharge: int | None
    limits_status: str
    status: str
    level: float = 0.90
    n_days_with_data: int = 0

    def to_dict(self) -> dict:
        """The fields as plain values: dates as ISO text, the temperature table as a list of rows."""
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, pd.DataFrame):
                value = value.to_dict("records")
            elif isinstance(value, pd.Timestamp):
                value = value.isoformat()
            out[f.name] = value
        return out


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def _band(value: float, rel_sigma: float, z: float) -> tuple[float, float]:
    half = z * rel_sigma * abs(value)
    return value - half, value + half


def _bins(daily: pd.DataFrame, mode: str) -> np.ndarray:
    """Measured hours of ``mode`` in each 1-degree bin (the bin without a reading left out)."""
    names = [f"s_{mode}_{k:02d}" for k in range(TEMP_BINS)]
    return daily[names].sum(axis=0).to_numpy(dtype=float) / 3600.0


def _hours_vs_limit(hours: np.ndarray, limit: float | None, high: bool) -> float | None:
    """Hours at or above a high limit (bins whose lower edge is at or above it) or at or below a low limit
    (bins whose upper edge is at or below it). ``None`` when no limit is given or it lies outside the
    resolvable range (the end bins hold everything beyond them)."""
    if limit is None or not (TEMP_BIN_LOW_C + 1 <= limit <= TEMP_BIN_LOW_C + TEMP_BINS - 1):
        return None
    lower = TEMP_BIN_LOW_C + np.arange(TEMP_BINS)
    keep = lower >= limit if high else lower + 1 <= limit
    return float(hours[keep].sum())


def count_usage(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    limits: Limits | None = None,
    *,
    config: RecordConfig | None = None,
    capacity_config: CapacityConfig | None = None,
    reduced: Reduced | None = None,
    level: float = 0.90,
) -> Usage:
    """Count energy, charge, cycles, temperature hours and voltage extremes since the first day of data.

    ``reduced`` is the output of ``reduce_frame`` / ``reduce_parts``; when it is not given, ``frame`` is
    reduced first. All totals are sums over the days of the record (a record is never a lifetime total),
    and time without measurement is in ``hours_missing``.

    ``hours_measured`` is the sum of measured seconds; ``hours_missing`` the time from the first to the last
    sample that was not measured; ``share_interpolated`` the interpolated seconds over that whole time.
    Energy and charge are in and out separately, and ``energy_throughput_kwh`` and
    ``capacity_throughput_ah`` are in + out, each with a band of the systematic sensor term only.
    ``full_equivalent_cycles`` = (charge in + charge out) / (2 x ``spec.reference_capacity_ah``).

    ``temp_hours`` counts measured hours per 5-degree band and mode (charging, discharging, idle);
    ``hours_no_temperature`` those without a reading. Temperature limits are applied at the resolution of
    the 1-degree bins: hours above a high limit are the bins whose lower edge is at or above it, hours below
    a low limit those whose upper edge is at or below it; the charging limits use the charging bins only.
    A limit outside -19 to 69 degrees cannot be resolved (the end bins hold everything beyond them) and gives
    ``None``. Pack temperature, not cell temperature, is what is measured.

    ``n_empty_rests`` counts the rests of normal operation labelled ``empty`` (a discharge that reached the
    system's own end-of-discharge level, ``rests.operating_rests`` with no mask). ``n_deep_discharge``
    counts the accepted rests after a discharge whose edge voltage is below ``limits.voltage_min_v``; a
    discharge below the limit that is not followed by a rest of at least ``capacity_config.min_duration_min``
    is not counted, so the count is a lower bound.
    ``voltage_min_cell_v`` and ``voltage_max_cell_v`` are the pack extremes divided by the cells in series:
    a pack average, one cell can be lower or higher.
    """
    config = config or RecordConfig()
    capacity_config = capacity_config or CapacityConfig()
    limits = limits or Limits()
    if reduced is None:
        if frame is None:
            raise ValueError("give a frame or a reduced record")
        reduced = reduce_frame(frame, spec, capacity_config)
    daily = reduced.daily
    z = _z(level)
    ref = float(spec.reference_capacity_ah)

    if len(daily) == 0:
        reason = "not possible from this data: the record has no samples"
        nan = float("nan")
        return Usage(
            first_day=None, last_day=None, hours_measured=0.0, hours_missing=0.0, share_interpolated=nan,
            energy_in_kwh=nan, energy_out_kwh=nan, energy_throughput_kwh=nan, energy_throughput_kwh_low=nan,
            energy_throughput_kwh_high=nan, charge_in_ah=nan, charge_out_ah=nan, capacity_throughput_ah=nan,
            capacity_throughput_ah_low=nan, capacity_throughput_ah_high=nan, full_equivalent_cycles=nan,
            full_equivalent_cycles_low=nan, full_equivalent_cycles_high=nan, reference_capacity_ah=ref,
            temp_hours=_empty_temp_hours(), hours_no_temperature=nan, n_empty_rests=0, voltage_min_v=nan,
            voltage_max_v=nan, voltage_min_cell_v=nan, voltage_max_cell_v=nan, hours_below_temp_low=None,
            hours_above_temp_high=None, hours_charging_below_temp_low=None, hours_charging_above_temp_high=None,
            n_deep_discharge=None, limits_status=NO_LIMITS, status=reason, level=level, n_days_with_data=0,
        )

    measured_s = float(daily["measured_s"].sum())
    t_first, t_last = daily["t_first"].min(), daily["t_last"].max()
    elapsed_s = float((t_last - t_first).total_seconds())
    hours_measured = measured_s / 3600.0
    hours_missing = (elapsed_s - measured_s) / 3600.0
    share_interp = float(daily["interpolated_s"].sum()) / elapsed_s if elapsed_s > 0 else float("nan")

    kwh_in, kwh_out = float(daily["wh_in"].sum()) / 1000.0, float(daily["wh_out"].sum()) / 1000.0
    ah_in, ah_out = float(daily["ah_in"].sum()), float(daily["ah_out"].sum())
    kwh = kwh_in + kwh_out
    ah = ah_in + ah_out
    kwh_low, kwh_high = _band(kwh, config.energy_sigma, z)
    ah_low, ah_high = _band(ah, config.charge_sigma, z)
    cycles = ah / (2.0 * ref)

    hours = {mode: _bins(daily, mode) for mode in MODES}
    per_band = {
        mode: hours[mode].reshape(-1, _BAND_WIDTH).sum(axis=1) if TEMP_BINS % _BAND_WIDTH == 0 else None
        for mode in MODES
    }
    table = pd.DataFrame(
        {
            "temp_band_c": TEMP_BIN_LOW_C + _BAND_WIDTH * np.arange(TEMP_BINS // _BAND_WIDTH),
            "hours_charge": per_band["charge"],
            "hours_discharge": per_band["discharge"],
            "hours_idle": per_band["idle"],
        }
    )
    no_temp = float(sum(daily[f"s_{mode}_nan"].sum() for mode in MODES)) / 3600.0
    every = hours["charge"] + hours["discharge"] + hours["idle"]

    rests = operating_rests(reduced.rests, spec, capacity_config)
    n_empty = int((rests["kind"] == "empty").sum())
    n_deep = None
    if limits.voltage_min_v is not None:
        deep = rests["accepted"] & (rests["direction"] == "discharge") & (rests["edge_v"] < limits.voltage_min_v)
        n_deep = int(deep.sum())

    vmin, vmax = float(daily["voltage_min_v"].min()), float(daily["voltage_max_v"].max())
    cells = spec.cells_in_series
    given = {name: getattr(limits, name) for name in (f.name for f in fields(limits))}
    missing = [name for name, value in given.items() if value is None]
    if len(missing) == len(given):
        limits_status = NO_LIMITS
    elif missing:
        limits_status = f"not possible from this data: no limit given for {', '.join(missing)}"
    else:
        limits_status = "ok"
    return Usage(
        first_day=pd.Timestamp(daily["day"].iloc[0]),
        last_day=pd.Timestamp(daily["day"].iloc[-1]),
        hours_measured=hours_measured,
        hours_missing=hours_missing,
        share_interpolated=share_interp,
        energy_in_kwh=kwh_in,
        energy_out_kwh=kwh_out,
        energy_throughput_kwh=kwh,
        energy_throughput_kwh_low=kwh_low,
        energy_throughput_kwh_high=kwh_high,
        charge_in_ah=ah_in,
        charge_out_ah=ah_out,
        capacity_throughput_ah=ah,
        capacity_throughput_ah_low=ah_low,
        capacity_throughput_ah_high=ah_high,
        full_equivalent_cycles=cycles,
        full_equivalent_cycles_low=ah_low / (2.0 * ref),
        full_equivalent_cycles_high=ah_high / (2.0 * ref),
        reference_capacity_ah=ref,
        temp_hours=table,
        hours_no_temperature=no_temp,
        n_empty_rests=n_empty,
        voltage_min_v=vmin,
        voltage_max_v=vmax,
        voltage_min_cell_v=vmin / cells,
        voltage_max_cell_v=vmax / cells,
        hours_below_temp_low=_hours_vs_limit(every, limits.temp_low_c, high=False),
        hours_above_temp_high=_hours_vs_limit(every, limits.temp_high_c, high=True),
        hours_charging_below_temp_low=_hours_vs_limit(hours["charge"], limits.charge_temp_low_c, high=False),
        hours_charging_above_temp_high=_hours_vs_limit(hours["charge"], limits.charge_temp_high_c, high=True),
        n_deep_discharge=n_deep,
        limits_status=limits_status,
        status=(
            f"counted since the first day of data ({pd.Timestamp(daily['day'].iloc[0]).date()}), "
            "not a lifetime total; time without measurement is in hours_missing"
        ),
        level=level,
        n_days_with_data=int((daily["measured_s"] > 0).sum()),
    )


def _empty_temp_hours() -> pd.DataFrame:
    return pd.DataFrame(columns=["temp_band_c", "hours_charge", "hours_discharge", "hours_idle"])


def usage_by_year(reduced: Reduced, spec: PackSpec, config: RecordConfig | None = None) -> pd.DataFrame:
    """One row per calendar year of the record: ``year``, ``hours_measured``, ``hours_missing``,
    ``energy_in_kwh``, ``energy_out_kwh``, ``charge_in_ah``, ``charge_out_ah`` and ``full_equivalent_cycles``.

    A step counts in the year of its end sample. ``hours_missing`` is the part of the year inside the record
    (from the first to the last sample) that was not measured, so a gap over New Year is split between the
    two years. The first and the last year are partial years. ``config`` is not used (counts have no
    setting); it is accepted so that the call matches the other functions of the record.
    """
    daily = reduced.daily
    cols = ["year", "hours_measured", "hours_missing", "energy_in_kwh", "energy_out_kwh", "charge_in_ah",
            "charge_out_ah", "full_equivalent_cycles"]
    if len(daily) == 0:
        return pd.DataFrame(columns=cols)
    t_first, t_last = daily["t_first"].min(), daily["t_last"].max()
    year = daily["day"].dt.year
    rows = []
    for y, g in daily.groupby(year, sort=True):
        begin = max(t_first, pd.Timestamp(year=int(y), month=1, day=1))
        end = min(t_last, pd.Timestamp(year=int(y) + 1, month=1, day=1))
        span = max(0.0, (end - begin).total_seconds())
        measured = float(g["measured_s"].sum())
        ah_in, ah_out = float(g["ah_in"].sum()), float(g["ah_out"].sum())
        rows.append(
            (
                int(y), measured / 3600.0, (span - measured) / 3600.0, float(g["wh_in"].sum()) / 1000.0,
                float(g["wh_out"].sum()) / 1000.0, ah_in, ah_out, (ah_in + ah_out) / (2.0 * spec.reference_capacity_ah),
            )
        )
    return pd.DataFrame(rows, columns=cols)
