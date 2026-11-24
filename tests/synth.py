"""A small synthetic home-storage pack with a known capacity fade, simulated by ``besslib.sim``.

This was the generator of besslib 0.2.0; it is now a thin wrapper around the ``synth`` case of
``besslib.sim`` (check S0: the same samples, bit for bit). The pack follows a daily solar
pattern: discharge at night, charge in the day (a stepped half sine), discharge in the
evening, at random rates. Its BMS stops a charge or a discharge at a terminal voltage
limit with a small random error, decided on the BMS's own copy of the pack (the
``synth`` stop rule of ``besslib.sim.bms``). Its capacity falls linearly at a known rate,
and a hidden current drains it without being measured (BMS supply). The current sensor
has a constant gain error and noise.

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
from functools import lru_cache

import numpy as np
import pandas as pd

from besslib import sim
from besslib.frames import PackSpec

_OCV_SOC = np.array([0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0, 1.1])
_OCV_V = np.array([3.0, 3.40, 3.50, 3.60, 3.75, 3.95, 4.05, 4.15, 4.30])

_DEEP_PEAK = 0.45  # x capacity: the solar peak of a planted deep-discharge day (a full charge)
_DEEP_LOAD = 0.30  # x capacity: its evening load, enough to reach the lower stop from full
_GRID_S = 10  # the BMS checks its stop every 10 s, whatever the frame step


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
        return self._profile().day_mean(day)

    def temperature_c(self, seconds) -> np.ndarray:
        """The pack temperature at ``seconds`` since ``start``: season, hour and planted intervals."""
        return self._profile()(seconds)

    def resistance_scale(self, day) -> np.ndarray:
        """The factor on ``r_ohm`` and ``r1_ohm`` on each day: growth with age, fall with
        the day's mean temperature. Constant within a day; exactly 1 with the defaults."""
        day = np.asarray(day, dtype=float)
        growth = 1.0 + self.r_growth_per_year * day / 365.25
        return growth * np.exp(-self.r_temp_coeff_per_c * (self.day_temperature_c(day) - 25.0))

    def _profile(self):
        return sim.thermal.Profile(self.temp_mean_c, self.temp_season_amp_c, self.temp_daily_amp_c,
                                   tuple(tuple(p) for p in self.planted_temps))

    def case(self, step_s: int = 10) -> dict:
        """This pack as a ``besslib.sim`` case."""
        n = self.cells_in_series
        r0, r1 = self.r_ohm / n, self.r1_ohm / n
        if r0 * n != self.r_ohm or r1 * n != self.r1_ohm:
            raise ValueError("the pack resistances do not divide exactly by the cells in series")
        if step_s % _GRID_S:
            raise ValueError(f"step_s must be a multiple of {_GRID_S} s")
        return {
            "case": {"name": "synth", "start": self.start, "step_s": _GRID_S, "sample_s": step_s,
                     "chemistry": "synthetic", "pack_name": "besslib synthetic pack", "cell_every_s": 1e12},
            "cell": {"preset": "synth", "capacity_ah": self.capacity_ah, "r0_ohm": r0, "r1_ohm": r1,
                     "tau1_s": self.tau1_s},
            "pack": {"groups": 1, "series": n, "fade_points_per_year": self.fade_points_per_year,
                     "resistance_law": "synth-day", "resistance_growth_per_year": self.r_growth_per_year,
                     "r_temp_coeff_per_c": self.r_temp_coeff_per_c},
            "thermal": {"mode": "prescribed"},
            "temperature": {
                "pack": {"mean_c": self.temp_mean_c, "season_amp_c": self.temp_season_amp_c,
                         "daily_amp_c": self.temp_daily_amp_c, "planted": [list(p) for p in self.planted_temps]},
                "ambient": {"mean_c": 20.0},
            },
            "bms": {"v_cell_max": self.v_max_cell, "v_cell_min": self.v_min_cell, "stop_sigma_v": self.stop_sigma_v,
                    "stop_rule": "synth", "supply_a": -self.hidden_current_a},
            "duty": {"kind": "home", "capacity_ah": self.capacity_ah, "deep_days": list(self.planted_deep_days),
                     "deep_peak_c": _DEEP_PEAK, "deep_load_c": _DEEP_LOAD,
                     "deep_v_min_offset_cell": self.v_min_cell - self.deep_v_min_cell},
            "sensors": {"current_gain_max": self.gain_error_max, "current_noise_a": self.current_noise_a,
                        "voltage_noise_v": self.voltage_noise_v, "voltage_gain_max": self.voltage_gain_error_max},
        }


def _days(pack: SynthPack, months: int) -> int:
    t0 = pd.Timestamp(pack.start)
    return int(((t0 + pd.DateOffset(months=months)) - t0).days)


def frames(pack: SynthPack, months: int, step_s: int = 10, seed: int = 0, true_signals: bool = False):
    """Yield the record month by month as besslib frames; ``frames(...)`` is a generator.

    With ``true_signals`` the frames hold the pack's own current, voltage and power for
    the same operation: no sensor gain error and no noise.
    """
    for month in sim.run(pack.case(step_s), months, seed, measured=not true_signals, log_blocks=False):
        yield month.frame


@lru_cache(maxsize=8)
def _truth(pack: SynthPack, months: int, seed: int):
    stops, blocks = [], []
    for month in sim.run(pack.case(), months, seed, measured=False):
        stops.append(month.truth.stops)
        blocks.append(month.truth.blocks)
    return pd.concat(stops, ignore_index=True), pd.concat(blocks, ignore_index=True)


def true_stops(pack: SynthPack, months: int, seed: int) -> pd.DataFrame:
    """Every BMS stop of ``frames(pack, months, seed=seed)``: ``time``, ``kind`` (``full``
    or ``empty``), the true state of charge ``soc`` and the true ``capacity_ah`` then.

    A stop that follows another of the same kind (a short top-up, or a load that finds
    the pack already at its limit) is listed too; ``first`` is False for it. The state of
    charge and the capacity are those of the BMS's own copy of the pack, on which it
    decides its stops (as in besslib 0.2.0).
    """
    stops = _truth(pack, months, seed)[0]
    stops = stops[stops["kind"].isin(["full", "empty"])]
    table = pd.DataFrame({"seconds": stops["seconds"].to_numpy(), "kind": stops["kind"].to_numpy(),
                          "soc": stops["model_soc"].to_numpy(), "capacity_ah": stops["model_capacity_ah"].to_numpy()})
    table.insert(0, "time", pd.Timestamp(pack.start) + pd.to_timedelta(table["seconds"], unit="s"))
    table["first"] = table["kind"] != table["kind"].shift()
    return table


def true_segments(pack: SynthPack, months: int, seed: int) -> pd.DataFrame:
    """The current program of ``frames(pack, months, seed=seed)`` after the BMS stops:
    ``start``, ``seconds`` (since the pack's start), ``duration_s`` and the true
    ``current_a``, constant within a segment (positive = charging)."""
    blocks = _truth(pack, months, seed)[1]
    table = pd.DataFrame({"seconds": blocks["seconds"].to_numpy(), "duration_s": blocks["duration_s"].to_numpy(),
                          "current_a": blocks["current_a"].to_numpy()})
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
    stops = true_stops(pack, months, seed)
    kept = stops[stops["kind"] != stops["kind"].shift()]
    t = kept["seconds"].to_numpy() / (365.25 * 86400.0)
    soc = kept["soc"].to_numpy()
    cap = kept["capacity_ah"].to_numpy()
    usable = np.abs(np.diff(soc)) * 0.5 * (cap[1:] + cap[:-1])
    years = 0.5 * (t[1:] + t[:-1])
    slope = np.polyfit(years, usable, 1)[0]
    return float(slope / pack.capacity_ah * 100.0)
