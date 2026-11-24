"""Sensors: what a meter at the pack terminals reports.

Each signal gets a constant gain error (drawn once per pack, uniform within
``+-gain_max``), an offset, noise, and a resolution (quantisation). Temperature comes
from the true temperature at the sensor's point (``thermal.py``).

Random numbers, in a fixed order (so that changing one setting does not change the
operation or another sensor): from the pack's main stream, after the duty's plan, the
current gain (always drawn), then the voltage gain (drawn only when ``voltage_gain_max``
is above 0), then month by month the voltage noise and the current noise (always drawn,
zeros when the level is 0). This is the order of ``tests/synth.py``. Temperature noise
comes from a second stream. The current noise is added only while current flows unless
``current_noise_at_rest``, as ``tests/synth.py``.

Gaps (``gaps_per_month`` above 0) come from their own stream, ``[seed, 3]``, so they
change no other number. A month draws the number of gaps, their start rows, then their
lengths. A gap of up to ``interpolate_max_s`` is filled by linear interpolation and
flagged ``interpolated`` (the field data does the same up to 5 min); a longer gap leaves
its rows out of the frame.

Every default is an example value, not from a source.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SensorSettings:
    current_gain_max: float = 0.0
    current_offset_a: float = 0.0
    current_noise_a: float = 0.0
    current_noise_at_rest: bool = False
    current_resolution_a: float = 0.0
    voltage_gain_max: float = 0.0
    voltage_offset_v: float = 0.0
    voltage_noise_v: float = 0.0
    voltage_resolution_v: float = 0.0
    temp_offset_c: float = 0.0
    temp_noise_c: float = 0.0
    temp_resolution_c: float = 0.0
    gaps_per_month: float = 0.0  # mean number of gaps a month (Poisson)
    gap_median_s: float = 120.0  # lengths are log-normal
    gap_sigma: float = 1.0  # standard deviation of the log of the length
    gap_max_s: float = 3600.0  # longest gap
    interpolate_max_s: float = 300.0  # gaps up to this are filled and flagged


def _quantise(x: np.ndarray, step: float) -> np.ndarray:
    return np.round(x / step) * step if step > 0 else x


class Sensors:
    def __init__(self, settings: SensorSettings, rng: np.random.Generator, rng_extra: np.random.Generator) -> None:
        self.s = settings
        self.rng, self.rng_extra = rng, rng_extra
        self.current_gain = rng.uniform(-settings.current_gain_max, settings.current_gain_max)
        self.voltage_gain = 0.0
        if settings.voltage_gain_max > 0.0:
            self.voltage_gain = rng.uniform(-settings.voltage_gain_max, settings.voltage_gain_max)

    def measure(self, voltage: np.ndarray, current: np.ndarray, temp: np.ndarray):
        """Measured voltage, current and temperature for one month of true signals."""
        s, n = self.s, len(voltage)
        volts = voltage * (1.0 + self.voltage_gain) + self.rng.normal(0.0, s.voltage_noise_v, n)
        noise = self.rng.normal(0.0, s.current_noise_a, n)
        if not s.current_noise_at_rest:
            noise = noise * (current != 0)
        amps = current * (1.0 + self.current_gain) + noise
        if s.voltage_offset_v:
            volts = volts + s.voltage_offset_v
        if s.current_offset_a:
            amps = amps + s.current_offset_a
        temps = temp
        if s.temp_offset_c:
            temps = temps + s.temp_offset_c
        if s.temp_noise_c > 0:
            temps = temps + self.rng_extra.normal(0.0, s.temp_noise_c, n)
        return (_quantise(volts, s.voltage_resolution_v), _quantise(amps, s.current_resolution_a),
                _quantise(temps, s.temp_resolution_c))


def frame(start: pd.Timestamp, seconds: np.ndarray, voltage, current, temp_pack, temp_ambient) -> pd.DataFrame:
    """A besslib frame (``besslib.frames``) from one month of signals."""
    index = pd.DatetimeIndex(start + pd.to_timedelta(seconds, unit="s"), name="time").as_unit("s")
    return pd.DataFrame(
        {
            "voltage_v": voltage,
            "current_a": current,
            "power_w": voltage * current,
            "temp_pack_c": temp_pack,
            "temp_ambient_c": temp_ambient,
            "interpolated": np.zeros(len(seconds), dtype=bool),
        },
        index=index,
    )


def apply_gaps(meas: pd.DataFrame, settings: SensorSettings, rng: np.random.Generator, sample_s: float) -> pd.DataFrame:
    """One month's measured frame with its gaps: short ones interpolated, long ones left out."""
    n = len(meas)
    count = int(rng.poisson(settings.gaps_per_month))
    if count == 0 or n < 4:
        return meas
    first = rng.integers(1, n - 1, count)
    length = np.minimum(rng.lognormal(np.log(settings.gap_median_s), settings.gap_sigma, count), settings.gap_max_s)
    miss = np.zeros(n, dtype=bool)
    for a, seconds in zip(first, length):
        rows = int(max(1, np.ceil(seconds / sample_s)))
        miss[a : min(a + rows, n - 1)] = True  # the last row always stays: a gap has a far end
    if not miss.any():
        return meas
    edges = np.flatnonzero(np.diff(np.r_[False, miss, False].astype(np.int8)))
    out = meas.copy()
    keep = np.ones(n, dtype=bool)
    good = np.flatnonzero(~miss)
    flag = out["interpolated"].to_numpy().copy()
    for a, b in zip(edges[0::2], edges[1::2]):
        if (b - a) * sample_s <= settings.interpolate_max_s:
            flag[a:b] = True
        else:
            keep[a:b] = False
    fill = miss & keep
    for name in ("voltage_v", "current_a", "power_w", "temp_pack_c", "temp_ambient_c"):
        values = out[name].to_numpy().copy()
        values[fill] = np.interp(np.flatnonzero(fill), good, values[good])
        out[name] = values
    out["interpolated"] = flag
    return out[keep]
