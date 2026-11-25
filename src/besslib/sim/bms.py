"""The BMS: stops at voltage limits, balancing, current limits by temperature, its own
state of charge.

**Stops.** The BMS ends a charge when a voltage reaches its upper limit and a discharge
when it reaches its lower one. It checks at the start of every block with the current
asked for, and at the end of every step with the step's current; a stop therefore lies in
the step in which its voltage crossed, and no limit is passed by more than one step's
change. Which voltage it checks (``stop_rule``):

* ``"cells"``: every cell group's mean cell voltage (``v_group / series``);
* ``"pack"``: the pack voltage divided by the cells in series;
* ``"synth"``: the rule of ``tests/synth.py``. The BMS runs its own copy of a one-group
  pack whose capacity and resistances it updates at midnight, and checks that copy's
  voltage, not the plant's. Its copy and the plant drift apart slowly (the plant updates
  its capacity at every block); the rule exists to reproduce ``tests/synth.py``.

Each day's two limits carry a random error (``stop_sigma_v``, drawn day by day). A limit
can move with time (``v_min_points``, ``v_max_points``: ``(day, offset_v)`` points,
linear between them, flat outside), as a firmware update or a recalibration would move it.

**Balancing** (``balance``): passive; at each block start, every group above ``balance_v``
whose cell voltage lies more than ``balance_spread_v`` above the lowest group's bleeds through
``bleed_ohm`` per cell. The lowest group never bleeds, so the others come down to it.

**Current limits** by the hottest and the coldest group temperature: ``derate`` points
``(temp_c, charge_c, discharge_c)`` (largest C-rates, linear between points, flat
outside); no current above ``stop_temp_c``; no charging below ``charge_block_below_c``.

**Its own state of charge**: a coulomb counter of the terminal current, reset to 1 at
every full stop. Its state of health is a setting (``soh_percent``) for the bus.

Every default is an example value, not from a source.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BmsSettings:
    v_cell_max: float = 4.20
    v_cell_min: float = 3.30
    stop_sigma_v: float = 0.0
    stop_rule: str = "cells"
    v_min_points: tuple = ()
    v_max_points: tuple = ()
    supply_a: float = 0.0  # the BMS's own supply, drawn from the cells and not metered
    balance: bool = False
    balance_v: float = 4.0
    balance_spread_v: float = 0.010
    bleed_ohm: float = 50.0
    derate: tuple = ()
    stop_temp_c: float | None = None
    charge_block_below_c: float | None = None
    soh_percent: float = 100.0

    def __post_init__(self) -> None:
        if self.stop_rule not in ("cells", "pack", "synth"):
            raise ValueError("stop_rule is 'cells', 'pack' or 'synth'")
        if self.v_cell_min >= self.v_cell_max:
            raise ValueError("v_cell_min must lie below v_cell_max")

    def draw_day(self, rng: np.random.Generator) -> tuple[float, float]:
        """The day's random errors of the upper and the lower limit (always drawn, in this order)."""
        return rng.normal(0.0, self.stop_sigma_v), rng.normal(0.0, self.stop_sigma_v)

    @staticmethod
    def offset(points: tuple, day: float) -> float:
        if not points:
            return 0.0
        days = np.array([p[0] for p in points], dtype=float)
        values = np.array([p[1] for p in points], dtype=float)
        return float(np.interp(day, days, values))

    def current_limits(self, t_max_c: float, t_min_c: float, nominal_ah: float) -> tuple[float, float]:
        """The largest charge and discharge currents (A, both positive) at these group temperatures."""
        charge = discharge = np.inf
        if self.derate:
            temps = np.array([p[0] for p in self.derate], dtype=float)
            c = np.array([p[1] for p in self.derate], dtype=float)
            dc = np.array([p[2] for p in self.derate], dtype=float)
            # The hottest group limits above the table's middle, the coldest below it.
            for t in (t_max_c, t_min_c):
                charge = min(charge, float(np.interp(t, temps, c)) * nominal_ah)
                discharge = min(discharge, float(np.interp(t, temps, dc)) * nominal_ah)
        if self.stop_temp_c is not None and t_max_c >= self.stop_temp_c:
            charge = discharge = 0.0
        if self.charge_block_below_c is not None and t_min_c < self.charge_block_below_c:
            charge = 0.0
        return charge, discharge

    def bleeding(self, v_cell: np.ndarray) -> np.ndarray:
        """Which groups bleed, from their mean cell voltages at the block start."""
        if not self.balance or len(v_cell) < 2:
            return np.zeros(len(v_cell), dtype=bool)
        return (v_cell > self.balance_v) & (v_cell - float(v_cell.min()) > self.balance_spread_v)
