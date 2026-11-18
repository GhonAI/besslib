"""A small synthetic home-storage pack with a known capacity fade: the seed of A2.

The pack follows a daily solar pattern: discharge at night, charge in the day (a stepped
half sine), discharge in the evening, at random rates. Its BMS stops a charge or a discharge at a terminal
voltage limit, with a small random error, so the state of charge at a stop depends on
the current, as in a real pack. Its capacity falls linearly at a
known rate, and a hidden current drains it without being measured (BMS supply). The
current sensor has a constant gain error and noise.

Voltage: an OCV curve times the cells in series, an ohmic drop, and one RC element.
The model is plain and is not fitted to any real cell. Its purpose is to give a known
truth to the estimators, not to look like a particular battery.

Options for the Annex VII checks, all off by default (with the defaults the pack is the
one the capacity checks were made on, sample for sample):

* both resistances grow linearly with time (``r_growth_per_year``) and fall with the
  day's mean pack temperature (``r_temp_coeff_per_c``);
* the pack temperature follows the season and the hour (``temp_mean_c``,
  ``temp_season_amp_c``, ``temp_daily_amp_c``);
* the voltage sensor has a constant gain error (``voltage_gain_error_max``);
* planted events: intervals of a fixed pack temperature (``planted_temps``) and days
  whose evening discharge runs down to a lower voltage stop (``planted_deep_days``).

The truth is available without sensor errors: ``frames(..., true_signals=True)`` gives
the same operation with the pack's own current and voltage; ``true_stops`` and
``true_segments`` give the BMS stops and the current program.

Frames are produced month by month in the besslib frame format, at ``step_s`` seconds.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from besslib.frames import PackSpec

_OCV_SOC = np.array([0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0, 1.1])
_OCV_V = np.array([3.0, 3.40, 3.50, 3.60, 3.75, 3.95, 4.05, 4.15, 4.30])

_DEEP_PEAK = 0.45  # x capacity: the solar peak of a planted deep-discharge day (a full charge)
_DEEP_LOAD = 0.30  # x capacity: its evening load, enough to reach the lower stop from full


@dataclass(frozen=True)
class SynthPack:
    capacity_ah: float = 100.0
    fade_points_per_year: float = 2.0  # points of capacity_ah lost per year
    cells_in_series: int = 14
    r_ohm: float = 0.02
    r1_ohm: float = 0.01
    tau1_s: float = 600.0
    hidden_current_a: float = -0.05  # unmeasured drain (BMS supply), negative = drain
    v_max_cell: float = 4.20  # the BMS ends a charge at this terminal voltage per cell
    v_min_cell: float = 3.30  # and a discharge at this one
    stop_sigma_v: float = 0.005  # random error of each stop, V per cell
    gain_error_max: float = 0.005  # current sensor gain error, uniform within +-
    current_noise_a: float = 0.05
    voltage_noise_v: float = 0.005
    start: str = "2020-01-01"
    # Options for the Annex VII checks; the defaults switch all of them off.
    r_growth_per_year: float = 0.0  # growth of r_ohm and r1_ohm per year, as a share of their start values
    r_temp_coeff_per_c: float = 0.0  # relative fall of both per degree of the day's mean temperature above 25
    temp_mean_c: float = 25.0  # yearly mean of the pack temperature
    temp_season_amp_c: float = 0.0  # seasonal swing, warmest around day 200 of the year
    temp_daily_amp_c: float = 0.0  # daily swing, warmest at 15:00
    voltage_gain_error_max: float = 0.0  # voltage sensor gain error, uniform within +-
    planted_temps: tuple = ()  # (day, start_hour, hours, temp_c): the pack temperature is temp_c in that interval
    planted_deep_days: tuple = ()  # days (0 = the first) with a full charge and an evening discharge down to deep_v_min_cell
    deep_v_min_cell: float = 3.10

    def spec(self) -> PackSpec:
        return PackSpec(
            nominal_capacity_ah=self.capacity_ah,
            nominal_energy_kwh=self.capacity_ah * 3.7 * self.cells_in_series / 1000.0,
            cells_in_series=self.cells_in_series,
            chemistry="synthetic",
            name="besslib synthetic pack",
        )

    def true_capacity_ah(self, times) -> np.ndarray:
        """The capacity between SOC 0 and 1. The usable capacity between the BMS stops is
        smaller; the fade rate of both is the same within the model's resolution."""
        years = (pd.DatetimeIndex(times) - pd.Timestamp(self.start)).total_seconds() / (365.25 * 86400.0)
        return self.capacity_ah * (1.0 - self.fade_points_per_year / 100.0 * np.asarray(years))

    def day_temperature_c(self, day) -> np.ndarray:
        """The mean pack temperature of each day (``day`` counts from ``start``, 0 = the first)."""
        day = np.asarray(day, dtype=float)
        return self.temp_mean_c + self.temp_season_amp_c * np.cos(2.0 * np.pi * (day - 200.0) / 365.25)

    def temperature_c(self, seconds) -> np.ndarray:
        """The pack temperature at ``seconds`` since ``start``: season, hour and planted intervals."""
        seconds = np.asarray(seconds, dtype=float)
        hour = (seconds % 86400.0) / 3600.0
        temp = self.day_temperature_c(np.floor(seconds / 86400.0)) + self.temp_daily_amp_c * np.sin(
            2.0 * np.pi * (hour - 9.0) / 24.0
        )
        for day, start_hour, hours, temp_c in self.planted_temps:
            begin = day * 86400.0 + start_hour * 3600.0
            temp = np.where((seconds >= begin) & (seconds < begin + hours * 3600.0), float(temp_c), temp)
        return temp

    def resistance_scale(self, day) -> np.ndarray:
        """The factor on ``r_ohm`` and ``r1_ohm`` on each day: growth with age, fall with
        the day's mean temperature. Constant within a day; exactly 1 with the defaults."""
        day = np.asarray(day, dtype=float)
        growth = 1.0 + self.r_growth_per_year * day / 365.25
        return growth * np.exp(-self.r_temp_coeff_per_c * (self.day_temperature_c(day) - 25.0))


def _segments(pack: SynthPack, days: int, rng: np.random.Generator, stops: list | None = None):
    """The current program as (start_s, duration_s, current_a), with the BMS stops applied.

    The BMS stops a charge when the terminal voltage reaches ``v_max_cell`` and a
    discharge when it falls to ``v_min_cell`` (each stop with a small random error), so
    the state of charge at a stop depends on the current, as in a real pack. Each stop
    is appended to ``stops`` as ``(time_s, kind, true_soc, true_capacity_ah)``.
    """
    c0 = pack.capacity_ah
    n = pack.cells_in_series
    starts = pd.Timestamp(pack.start)
    soc, p = 0.5, 0.0
    segs = []
    grid = np.arange(0.0, 7.0 * 3600.0 + 1.0, 10.0)
    deep_days = set(pack.planted_deep_days)
    r0, r1 = pack.r_ohm, pack.r1_ohm

    def advance(soc, p, current, length, cap):
        rate = (current + pack.hidden_current_a) / (3600.0 * cap)
        target = current * r1
        return soc + rate * length, target + (p - target) * np.exp(-length / pack.tau1_s)

    for d in range(days):
        cap = float(pack.true_capacity_ah([starts + pd.Timedelta(days=d)])[0])
        base = d * 86400.0
        scale = float(pack.resistance_scale(d))
        r0, r1 = pack.r_ohm * scale, pack.r1_ohm * scale
        # Night load; a solar day in eight hourly steps of a half sine (it fades out, as
        # sunlight does); then the evening load. The random draws keep their order on a
        # planted day, so planting a day does not change the days after it.
        deep = d in deep_days
        peak = rng.uniform(0.0, 0.45) * c0
        if deep:
            peak = _DEEP_PEAK * c0
        solar = [(8.0 + h, 9.0 + h, peak * np.sin(np.pi * (h + 0.5) / 8.0)) for h in range(8)]
        program = [
            (0.0, 7.0, -rng.uniform(0.03, 0.10) * c0),
            (7.0, 8.0, 0.0),
            *solar,
            (16.0, 18.0, 0.0),
            (18.0, 24.0, -rng.uniform(0.05, 0.25) * c0),
        ]
        v_max = n * (pack.v_max_cell + rng.normal(0.0, pack.stop_sigma_v))
        v_min = n * (pack.v_min_cell + rng.normal(0.0, pack.stop_sigma_v))
        v_min_evening = v_min
        if deep:
            program[-1] = (18.0, 24.0, -_DEEP_LOAD * c0)
            v_min_evening = v_min - n * (pack.v_min_cell - pack.deep_v_min_cell)
        for h0, h1, current in program:
            start, length = base + h0 * 3600.0, (h1 - h0) * 3600.0
            v_low = v_min_evening if h0 == 18.0 else v_min
            hit = None
            if current != 0.0:
                tau = grid[grid <= length]
                rate = (current + pack.hidden_current_a) / (3600.0 * cap)
                target = current * r1
                volts = (
                    n * np.interp(soc + rate * tau, _OCV_SOC, _OCV_V)
                    + current * r0
                    + target
                    + (p - target) * np.exp(-tau / pack.tau1_s)
                )
                over = volts >= v_max if current > 0 else volts <= v_low
                if over.any():
                    hit = float(tau[np.argmax(over)])
            if hit is not None:
                if hit > 0:
                    segs.append((start, hit, current))
                    soc, p = advance(soc, p, current, hit, cap)
                if stops is not None:
                    stops.append((start + hit, "full" if current > 0 else "empty", soc, cap))
                segs.append((start + hit, length - hit, 0.0))
                soc, p = advance(soc, p, 0.0, length - hit, cap)
            else:
                segs.append((start, length, current))
                soc, p = advance(soc, p, current, length, cap)
    return segs


def _days(pack: SynthPack, months: int) -> int:
    t0 = pd.Timestamp(pack.start)
    return int(((t0 + pd.DateOffset(months=months)) - t0).days)


def frames(pack: SynthPack, months: int, step_s: int = 10, seed: int = 0, true_signals: bool = False):
    """Yield the record month by month as besslib frames; ``frames(...)`` is a generator.

    With ``true_signals`` the frames hold the pack's own current, voltage and power for
    the same operation: no sensor gain error and no noise.
    """
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp(pack.start)
    end = t0 + pd.DateOffset(months=months)
    days = int((end - t0).days)
    segs = _segments(pack, days, rng)
    starts = np.array([s[0] for s in segs])
    lengths = np.array([s[1] for s in segs])
    currents = np.array([s[2] for s in segs])
    gain = rng.uniform(-pack.gain_error_max, pack.gain_error_max)
    voltage_gain = 0.0
    if pack.voltage_gain_error_max > 0.0:
        voltage_gain = rng.uniform(-pack.voltage_gain_error_max, pack.voltage_gain_error_max)
    scale = pack.resistance_scale(np.floor(starts / 86400.0))
    r0s, r1s = pack.r_ohm * scale, pack.r1_ohm * scale

    # State at each segment start: SOC and the RC voltage, carried in time order.
    soc0 = np.empty(len(segs))
    p0 = np.empty(len(segs))
    soc, p = 0.5, 0.0
    caps = pack.true_capacity_ah(t0 + pd.to_timedelta(starts, unit="s"))
    for k in range(len(segs)):
        soc0[k], p0[k] = soc, p
        soc += (currents[k] + pack.hidden_current_a) * lengths[k] / (3600.0 * caps[k])
        target = currents[k] * r1s[k]
        p = target + (p - target) * np.exp(-lengths[k] / pack.tau1_s)

    month_starts = pd.date_range(t0, end, freq="MS")
    for m0, m1 in zip(month_starts[:-1], month_starts[1:]):
        s0, s1 = (m0 - t0).total_seconds(), (m1 - t0).total_seconds()
        ts = np.arange(s0, s1, step_s, dtype=float)
        k = np.searchsorted(starts, ts, side="right") - 1
        tau = ts - starts[k]
        cur = currents[k]
        soc_t = soc0[k] + (cur + pack.hidden_current_a) * tau / (3600.0 * caps[k])
        target = cur * r1s[k]
        p_t = target + (p0[k] - target) * np.exp(-tau / pack.tau1_s)
        ocv = np.interp(soc_t, _OCV_SOC, _OCV_V) * pack.cells_in_series
        true_volts = ocv + cur * r0s[k] + p_t
        if true_signals:
            volts, measured = true_volts, cur
        else:
            volts = true_volts * (1.0 + voltage_gain) + rng.normal(0.0, pack.voltage_noise_v, len(ts))
            measured = cur * (1.0 + gain) + rng.normal(0.0, pack.current_noise_a, len(ts)) * (cur != 0)
        index = pd.DatetimeIndex(t0 + pd.to_timedelta(ts, unit="s"), name="time").as_unit("s")
        yield pd.DataFrame(
            {
                "voltage_v": volts,
                "current_a": measured,
                "power_w": volts * measured,
                "temp_pack_c": pack.temperature_c(ts),
                "temp_ambient_c": np.full(len(ts), 20.0),
                "interpolated": np.zeros(len(ts), dtype=bool),
            },
            index=index,
        )


def true_stops(pack: SynthPack, months: int, seed: int) -> pd.DataFrame:
    """Every BMS stop of ``frames(pack, months, seed=seed)``: ``time``, ``kind`` (``full``
    or ``empty``), the true state of charge ``soc`` and the true ``capacity_ah`` then.

    A stop that follows another of the same kind (a short top-up, or a load that finds
    the pack already at its limit) is listed too; ``first`` is False for it.
    """
    rng = np.random.default_rng(seed)  # the same draws as ``frames(pack, months, seed=seed)``
    stops: list = []
    _segments(pack, _days(pack, months), rng, stops)
    table = pd.DataFrame(stops, columns=["seconds", "kind", "soc", "capacity_ah"])
    table.insert(0, "time", pd.Timestamp(pack.start) + pd.to_timedelta(table["seconds"], unit="s"))
    table["first"] = table["kind"] != table["kind"].shift()
    return table


def true_segments(pack: SynthPack, months: int, seed: int) -> pd.DataFrame:
    """The current program of ``frames(pack, months, seed=seed)`` after the BMS stops:
    ``start``, ``seconds`` (since the pack's start), ``duration_s`` and the true
    ``current_a``, constant within a segment (positive = charging)."""
    rng = np.random.default_rng(seed)
    segs = _segments(pack, _days(pack, months), rng)
    table = pd.DataFrame(segs, columns=["seconds", "duration_s", "current_a"])
    table.insert(0, "start", pd.Timestamp(pack.start) + pd.to_timedelta(table["seconds"], unit="s"))
    return table


def true_usable_fade(pack: SynthPack, months: int, seed: int) -> float:
    """The pack's true fade of usable capacity, in points of ``capacity_ah`` per year.

    The usable capacity is the charge between a BMS stop at full and the next stop at
    empty (or the reverse), from the simulator's own state of charge, without sensor
    errors. Its fade differs a little from ``fade_points_per_year`` because the state of
    charge at a voltage stop depends on the current. The slope is a least-squares line
    over time, as in the estimator's fade model.
    """
    rng = np.random.default_rng(seed)  # the same draws as ``frames(pack, months, seed=seed)``
    stops: list = []
    _segments(pack, _days(pack, months), rng, stops)
    # Keep the first stop of each run of equal kinds: a repeated stop at the same edge
    # after a short top-up is not a new full or empty state.
    kept = [s for k, s in enumerate(stops) if k == 0 or s[1] != stops[k - 1][1]]
    t = np.array([s[0] for s in kept]) / (365.25 * 86400.0)
    soc = np.array([s[2] for s in kept])
    cap = np.array([s[3] for s in kept])
    usable = np.abs(np.diff(soc)) * 0.5 * (cap[1:] + cap[:-1])
    years = 0.5 * (t[1:] + t[:-1])
    slope = np.polyfit(years, usable, 1)[0]
    return float(slope / pack.capacity_ah * 100.0)
