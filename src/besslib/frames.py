"""The one data contract of besslib: column names, units and the sign convention.

Every function in ``besslib.soh`` reads a *frame*: a pandas DataFrame with a sorted,
timezone-naive ``DatetimeIndex`` and the columns below. ``besslib.sim`` (A2) writes
the same format.

Sign convention
---------------
Positive ``current_a`` and positive ``power_w`` mean **charging**: energy flows into
the battery and its state of charge rises. Discharging is negative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

#: Required columns and their units.
REQUIRED = {
    "voltage_v": "V, at the battery terminals",
    "current_a": "A, positive = charging",
    "power_w": "W, positive = charging",
    "temp_pack_c": "degrees C, battery pack",
}

#: Optional columns and their units.
OPTIONAL = {
    "temp_ambient_c": "degrees C, room or ambient",
    "interpolated": "bool, True where the value was filled in, not measured",
}


class FrameError(ValueError):
    """The DataFrame does not follow the besslib frame format."""


def check_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return ``frame`` unchanged if it follows the format; raise ``FrameError`` if not."""
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise FrameError("the index must be a DatetimeIndex")
    if frame.index.tz is not None:
        raise FrameError("the index must be timezone-naive")
    missing = [name for name in REQUIRED if name not in frame.columns]
    if missing:
        raise FrameError(f"missing columns: {', '.join(missing)}")
    if not frame.index.is_monotonic_increasing:
        raise FrameError("the index must be sorted by time")
    if frame.index.has_duplicates:
        raise FrameError("the index has duplicate timestamps")
    return frame


@dataclass(frozen=True)
class PackSpec:
    """What is known about a battery pack from its documents, not from its data.

    ``reference_capacity_ah`` is the value every state-of-health share is divided by.
    besslib prints it with every share, because the same pack can read 100 % or 65 %
    depending on the reference chosen.

    ``voltage_max_v`` and ``voltage_min_v`` are the documented limits of the whole pack (V, at
    its terminals). With both, the capacity estimate checks that the full and empty levels it
    finds lie near them (``CapacityConfig.limits_share``); without them it cannot. A limit that
    is not a positive number, limits in the wrong order, or a limit on the wrong side of the
    nominal voltage (``1000 * nominal_energy_kwh / nominal_capacity_ah``) raise ``ValueError``.
    """

    nominal_capacity_ah: float
    nominal_energy_kwh: float
    cells_in_series: int
    cells_in_parallel: int = 1
    chemistry: str = ""
    nominal_voltage_v: float | None = None
    voltage_max_v: float | None = None
    voltage_min_v: float | None = None
    reference_capacity_ah: float | None = None
    date_of_manufacture: date | None = None
    date_put_into_service: date | None = None
    name: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.nominal_capacity_ah <= 0 or self.nominal_energy_kwh <= 0:
            raise ValueError("nominal capacity and energy must be positive")
        if self.cells_in_series < 1 or self.cells_in_parallel < 1:
            raise ValueError("cell counts must be at least 1")
        if self.reference_capacity_ah is None:
            object.__setattr__(self, "reference_capacity_ah", self.nominal_capacity_ah)
        self._check_limits()

    def _check_limits(self) -> None:
        """The documented voltage limits: finite, positive, in order, and of the whole pack.

        The pack's nominal voltage, ``1000 * nominal_energy_kwh / nominal_capacity_ah``, lies
        inside its voltage window; a limit on the wrong side of it is not the pack's (a limit of
        one cell given for the pack is the usual slip)."""
        limits = {"voltage_max_v": self.voltage_max_v, "voltage_min_v": self.voltage_min_v}
        for name, value in limits.items():
            if value is not None and not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a positive number of volts, not {value!r}")
        nominal = 1000.0 * self.nominal_energy_kwh / self.nominal_capacity_ah
        if self.voltage_max_v is not None and self.voltage_min_v is not None and not self.voltage_max_v > self.voltage_min_v:
            raise ValueError(f"voltage_max_v ({self.voltage_max_v} V) must lie above voltage_min_v ({self.voltage_min_v} V)")
        if self.voltage_max_v is not None and not self.voltage_max_v > nominal:
            raise ValueError(
                f"voltage_max_v ({self.voltage_max_v} V) lies at or below the pack's nominal voltage ({nominal:.4g} V, "
                "nominal energy over nominal capacity): the limits are of the whole pack, at its terminals, not of one cell"
            )
        if self.voltage_min_v is not None and not self.voltage_min_v < nominal:
            raise ValueError(
                f"voltage_min_v ({self.voltage_min_v} V) lies at or above the pack's nominal voltage ({nominal:.4g} V, "
                "nominal energy over nominal capacity)"
            )
