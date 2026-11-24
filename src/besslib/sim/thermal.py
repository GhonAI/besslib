"""Temperatures: a prescribed profile, or a lumped thermal network with exact steps.

**Profile.** ``Profile`` gives a temperature as a function of time: a yearly mean, a
seasonal swing (warmest around day 200), a daily swing (warmest at 15:00) and planted
intervals, in the arithmetic of ``tests/synth.py``. It serves as the ambient air of a
network, or, with ``ThermalModel.mode = "prescribed"``, as the temperature of every cell
group and of the sensor (no heat balance, as ``tests/synth.py``).

**Network.** One node per cell group and one for the pack housing. Each group exchanges
heat with its neighbours in a chain, with the housing and with the air; the housing
exchanges heat with the air. Heat leaves to the air as ``q = -hA (T - T_air)``
(Bernardi et al. 1985, Eq. 18); a fan factor scales every conductance to the air (a fan
failure sets it below 1). With ``M`` the heat capacities and ``K`` the conductance
matrix, ``M dT/dt = -K T + b`` with ``b`` the heat sources plus the air terms. Within a
block ``b`` is constant (the block's mean heat), so

    T(t) = T_ss + exp(-M^-1 K t) (T0 - T_ss),   T_ss = K^-1 b,

computed exactly through the eigenvectors of the symmetric ``M^-1/2 K M^-1/2``. One node
with heat ``q`` settles at ``T_air + q / (hA)`` with the time constant ``m c / (hA)``.

Every value of ``ThermalModel`` is an example value, not from a source, unless a preset
names one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Profile:
    mean_c: float = 25.0
    season_amp_c: float = 0.0
    daily_amp_c: float = 0.0
    planted: tuple = ()  # (day, start_hour, hours, temp_c): the temperature is temp_c in that interval

    def day_mean(self, day):
        day = np.asarray(day, dtype=float)
        return self.mean_c + self.season_amp_c * np.cos(2.0 * np.pi * (day - 200.0) / 365.25)

    def __call__(self, seconds):
        seconds = np.asarray(seconds, dtype=float)
        hour = (seconds % 86400.0) / 3600.0
        temp = self.day_mean(np.floor(seconds / 86400.0)) + self.daily_amp_c * np.sin(2.0 * np.pi * (hour - 9.0) / 24.0)
        for day, start_hour, hours, temp_c in self.planted:
            begin = day * 86400.0 + start_hour * 3600.0
            temp = np.where((seconds >= begin) & (seconds < begin + hours * 3600.0), float(temp_c), temp)
        return temp


@dataclass(frozen=True)
class ThermalModel:
    """``mode``: ``"prescribed"`` (every node follows the ambient profile) or ``"network"``.
    ``sensor``: ``"housing"`` or the index of a cell group."""

    mode: str = "prescribed"
    c_group_j_k: float = 1.0
    c_housing_j_k: float = 1.0
    g_group_housing_w_k: float = 0.0
    g_neighbour_w_k: float = 0.0
    g_group_air_w_k: float = 0.0
    g_housing_air_w_k: float = 0.0
    sensor: object = "housing"
    bleed_heat_to: str = "group"  # where a balancing resistor's heat goes: "group" or "housing"

    def __post_init__(self) -> None:
        if self.mode not in ("prescribed", "network"):
            raise ValueError("thermal mode is 'prescribed' or 'network'")
        if self.mode == "network":
            if self.c_group_j_k <= 0 or self.c_housing_j_k <= 0:
                raise ValueError("heat capacities must be positive")
            if self.g_group_air_w_k <= 0 and self.g_housing_air_w_k <= 0:
                raise ValueError("the network needs a path to the air")


class Network:
    """The network of ``groups`` cell groups plus the housing, at one fan factor."""

    def __init__(self, model: ThermalModel, groups: int, fan: float = 1.0) -> None:
        n = groups + 1
        self.n, self.groups = n, groups
        cap = np.r_[np.full(groups, model.c_group_j_k), model.c_housing_j_k]
        k = np.zeros((n, n))
        h = groups  # the housing node

        def link(a, b, g):
            k[a, a] += g
            k[b, b] += g
            k[a, b] -= g
            k[b, a] -= g

        for g in range(groups):
            link(g, h, model.g_group_housing_w_k)
            if g + 1 < groups:
                link(g, g + 1, model.g_neighbour_w_k)
        air = np.r_[np.full(groups, model.g_group_air_w_k), model.g_housing_air_w_k] * fan
        if not np.any(air > 0):
            raise ValueError("the network has no path to the air at this fan factor")
        k[np.diag_indices(n)] += air
        self.air = air
        self.k = k
        self.cap = cap
        s = 1.0 / np.sqrt(cap)
        lam, q = np.linalg.eigh(s[:, None] * k * s[None, :])
        self.lam = lam
        self._left = s[:, None] * q  # M^-1/2 Q
        self._right = q.T * np.sqrt(cap)[None, :]  # Q^T M^1/2
        self._kinv = np.linalg.inv(k)

    def steady(self, heat, t_air):
        """The steady state for constant node heats (W, shape ``(n,)``) and air temperature."""
        return self._kinv @ (heat + self.air * t_air)

    def at(self, t0, heat, t_air, times):
        """Node temperatures at offsets ``times`` (shape ``(K,)``): ``(K, n)``."""
        ss = self.steady(heat, t_air)
        z = self._right @ (t0 - ss)
        decay = np.exp(-np.outer(np.asarray(times, dtype=float), self.lam))
        return ss[None, :] + (decay * z[None, :]) @ self._left.T

    def end(self, t0, heat, t_air, duration: float):
        return self.at(t0, heat, t_air, np.array([float(duration)]))[0]

    def time_constant_s(self) -> float:
        """The slowest time constant of the network."""
        return float(1.0 / self.lam.min()) if self.lam.min() > 0 else math.inf
