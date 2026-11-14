"""Measure a field capacity test that is recorded inside the operating data.

A field capacity test, as in Figgener et al. (2024, Supplementary Note 6): the
system is charged until it stops by itself, then discharged at its maximum power
until it stops by itself. The usable capacity is the integral of the current over
the discharge, the usable energy the integral of the power.

This module finds that discharge near a documented start time and integrates it.
It is the *reference* of besslib's validation, so it uses no estimator: only the
measured current, the measured power and three plain rules for where the
discharge begins and ends.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from besslib.frames import PackSpec, check_frame


@dataclass(frozen=True)
class CapacityTest:
    """One measured capacity test. ``status`` is ``"ok"`` or says what went wrong."""

    status: str
    documented_start: pd.Timestamp
    onset: pd.Timestamp | None = None
    end: pd.Timestamp | None = None
    duration_min: float = np.nan
    capacity_ah: float = np.nan
    energy_kwh: float = np.nan
    soh_capacity: float = np.nan
    reference_capacity_ah: float = np.nan
    c_rate: float = np.nan
    test_c_rate: float = np.nan
    charge_end: pd.Timestamp | None = None
    rest_before_min: float = np.nan
    pre_start_ah: float = np.nan
    voltage_before_v: float = np.nan
    voltage_min_v: float = np.nan
    temp_pack_mean_c: float = np.nan
    interpolated_share: float = np.nan
    missing_seconds: int = 0
    probe_delay_min: float = np.nan
    discharged_hour_before_ah: float = np.nan

    def to_dict(self) -> dict:
        return asdict(self)


def measure_capacity_test(
    frame: pd.DataFrame,
    spec: PackSpec,
    start: str | pd.Timestamp,
    *,
    search_min: float = 60.0,
    probe_c_rate: float = 0.1,
    probe_search_min: int = 60,
    onset_fraction: float = 0.2,
    end_fraction: float = 0.05,
    end_hold_s: int = 300,
    tail_fraction: float = 0.01,
    charge_c_rate: float = 0.02,
    max_hours: float = 12.0,
) -> CapacityTest:
    """Find the full-power discharge near ``start`` and integrate it.

    Rules
    -----
    1. The *test current* is the median current from 2 to 12 minutes after the probe
       start. The probe starts at ``start``; while that median is not a discharge of at
       least ``probe_c_rate`` × C, the probe start moves one minute later, at most
       ``probe_search_min`` times. A moved probe must also begin inside the discharge:
       the 10-second mean current at its first second is a discharge of at least
       ``probe_c_rate`` × C. Some tests begin late or with a few minutes at low power,
       so the full-power discharge can follow the documented start by minutes.
       ``probe_delay_min`` says how far the probe moved.
    2. The discharge *begins* at the last moment before that where the 10-second mean
       current is weaker than ``onset_fraction`` of the test current (searched up to
       ``search_min`` minutes before the probe start).
    3. The discharge *ends* once the mean current stays weaker than ``end_fraction``
       of the test current for ``end_hold_s`` seconds. The end is the last moment in
       that time with more than ``tail_fraction`` of the test current, so a slow
       taper is kept.

    Parameters
    ----------
    frame
        Operating data in the besslib frame format. It should cover at least one day
        before ``start`` and ``max_hours`` after it.
    spec
        The pack; ``reference_capacity_ah`` is used for ``soh_capacity``.
    start
        The documented start time of the test.

    Returns
    -------
    CapacityTest
        ``capacity_ah`` and ``energy_kwh`` are the charge and energy delivered from
        the beginning to the end of the discharge. ``c_rate`` is the median discharge
        current over that time, divided by the nominal capacity; ``test_c_rate`` is the
        test current of rule 1, divided by the same. ``pre_start_ah`` is the charge that
        left the battery between the end of the last charge and the beginning of the
        test discharge; ``discharged_hour_before_ah`` counts only the discharge current
        in the hour before the beginning. Both are reported, not added.
    """
    check_frame(frame)
    start = pd.Timestamp(start)
    window = frame.loc[start - pd.Timedelta(hours=26) : start + pd.Timedelta(hours=max_hours + 1)]
    if window.empty or window.index[0] > start or window.index[-1] < start + pd.Timedelta(minutes=12):
        return CapacityTest("no data at the documented start", start)

    current = window["current_a"]
    smooth = current.rolling("10s").mean()
    full_power = -probe_c_rate * spec.nominal_capacity_ah
    # Rule 1: the test current, from a probe that may move later by whole minutes.
    documented = start
    for delay in range(probe_search_min + 1):
        probe_start = documented + pd.Timedelta(minutes=delay)
        probe = current.loc[probe_start + pd.Timedelta(minutes=2) : probe_start + pd.Timedelta(minutes=12)]
        if len(probe) < 300:
            return CapacityTest("data gap in the first minutes of the test", documented)
        test_current = float(probe.median())
        inside = delay == 0 or smooth.asof(probe_start + pd.Timedelta(minutes=2)) <= full_power
        if test_current <= full_power and inside:
            break
    else:
        return CapacityTest(
            "no full-power discharge within %d min after the documented start" % probe_search_min, documented
        )
    start = probe_start

    # Rule 2: the beginning of the discharge.
    before = smooth.loc[start - pd.Timedelta(minutes=search_min) : start + pd.Timedelta(minutes=2)]
    weak = before[before > onset_fraction * test_current]
    if weak.empty:
        return CapacityTest("the discharge began more than %d min before the probe start" % search_min, documented)
    # The 10-second mean lags the current, so step back by its window.
    onset = max(weak.index[-1] - pd.Timedelta(seconds=10), window.index[0])

    # Rule 3: the end of the discharge.
    tail = current.loc[onset + pd.Timedelta(minutes=2) :]
    forward = tail[::-1].rolling(f"{end_hold_s}s").mean()[::-1]
    calm = forward[forward > end_fraction * test_current]
    calm = calm[calm.index <= tail.index[-1] - pd.Timedelta(seconds=end_hold_s)]
    if calm.empty:
        return CapacityTest("the end of the discharge was not found", documented, onset=onset)
    # Keep the tail: the last moment in the hold time with more than 1 % of the test current.
    hold = smooth.loc[calm.index[0] : calm.index[0] + pd.Timedelta(seconds=end_hold_s)]
    tail_end = hold[hold < tail_fraction * test_current]
    end = tail_end.index[-1] if len(tail_end) else calm.index[0]

    segment = window.loc[onset:end]
    seconds = (segment.index - segment.index[0]).total_seconds().to_numpy()
    capacity_ah = -np.trapezoid(segment["current_a"].to_numpy(), seconds) / 3600.0
    energy_kwh = -np.trapezoid(segment["power_w"].to_numpy(), seconds) / 3.6e6
    steps = np.diff(seconds)
    missing = int(round(float((steps[steps > 1.5] - 1.0).sum()))) if len(steps) else 0

    # Discharge only, in the hour before the beginning: shows a test that did not start full.
    hour = window.loc[onset - pd.Timedelta(hours=1) : onset - pd.Timedelta(seconds=1)]
    discharged_hour_before_ah = 0.0
    if len(hour) > 1:
        hour_s = (hour.index - hour.index[0]).total_seconds().to_numpy()
        discharged_hour_before_ah = -np.trapezoid(np.minimum(hour["current_a"].to_numpy(), 0.0), hour_s) / 3600.0

    # The end of the last charge before the test, and what left the battery since.
    charge_limit = charge_c_rate * spec.nominal_capacity_ah
    charging = smooth.loc[:onset]
    charging = charging[charging > charge_limit]
    if charging.empty:
        charge_end, rest_before_min, pre_start_ah = None, np.nan, np.nan
    else:
        charge_end = charging.index[-1]
        gap = window.loc[charge_end:onset]
        gap_s = (gap.index - gap.index[0]).total_seconds().to_numpy()
        pre_start_ah = -np.trapezoid(gap["current_a"].to_numpy(), gap_s) / 3600.0
        rest_before_min = (onset - charge_end).total_seconds() / 60.0

    return CapacityTest(
        status="ok",
        documented_start=documented,
        onset=onset,
        end=end,
        duration_min=(end - onset).total_seconds() / 60.0,
        capacity_ah=float(capacity_ah),
        energy_kwh=float(energy_kwh),
        soh_capacity=float(capacity_ah / spec.reference_capacity_ah),
        reference_capacity_ah=float(spec.reference_capacity_ah),
        c_rate=float(-segment["current_a"].median() / spec.nominal_capacity_ah),
        test_c_rate=float(-test_current / spec.nominal_capacity_ah),
        charge_end=charge_end,
        rest_before_min=float(rest_before_min),
        pre_start_ah=float(pre_start_ah),
        voltage_before_v=float(window["voltage_v"].loc[: onset - pd.Timedelta(seconds=1)].iloc[-10:].mean()),
        voltage_min_v=float(segment["voltage_v"].min()),
        temp_pack_mean_c=float(segment["temp_pack_c"].mean()),
        interpolated_share=float(segment["interpolated"].mean()) if "interpolated" in segment else np.nan,
        missing_seconds=missing,
        probe_delay_min=float(delay),
        discharged_hour_before_ah=float(discharged_hour_before_ah),
    )
