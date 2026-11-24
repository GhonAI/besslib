"""The cell group: an equivalent circuit, solved exactly at constant current.

A *cell group* is ``series`` cells in series, each of ``parallel`` cells lumped into one.
Its terminal voltage is

    v = series * ocv(soc) + h + r0 * i + p1 + p2

with ``i`` the current at the group's terminals (positive = charging, as
``besslib.frames``), ``p1`` and ``p2`` the voltages of one or two RC elements and ``h`` a
one-state hysteresis voltage (zero unless the chemistry has one). Drains (the BMS supply,
a balancing bleed, an internal leak) take charge from the open-circuit source behind the
resistances: the state of charge moves with ``i_soc = i - drains``, and the drains do not
pass ``r0`` or the RC elements. For a leak inside the cell this is its place in the
circuit; for the supply and the bleed it leaves out their drop over ``r0`` (below 1 mV at
the values used), as ``tests/synth.py`` does for its hidden drain. The open-circuit voltage curve is linear
between its points and flat beyond them, as ``numpy.interp``.

Within a *block* the current and the parameters are constant, so every state has a
closed form (``trajectory``) and every energy has an exact integral (``advance``):

* state of charge: ``soc + i t / (3600 c)``;
* an RC voltage: ``i r + (p0 - i r) exp(-t / tau)``;
* hysteresis: ``h_inf + (h0 - h_inf) exp(-beta t)``, ``h_inf = sign(i) h_max``,
  ``beta = kappa |i| / (3600 c)``: the voltage moves towards its bound at a rate set by the
  charge passed (one-state hysteresis, as in Tran et al. 2021, doi:10.3390/batteries7030051);
* the energy stored: the open-circuit voltage integrated over charge;
* the polarization heat: current times (terminal voltage - open-circuit voltage), the
  irreversible heat of Bernardi, Pawlikowski and Newman 1985 (doi:10.1149/1.2113792).

Temperature (``temperature_factor``): both resistances follow the Arrhenius form of
Ecker et al. 2015 (doi:10.1149/2.0551509jes, Eq. 2 for the electrolyte conductivity,
``sigma = sigma0 exp(-Ea / (R T)) / T``; Eq. 11 makes the charge-transfer resistance
proportional to ``T / j0`` with an Arrhenius ``j0``), so ``r(T) = r_ref (T / T_ref)
exp(Ea / R (1 / T - 1 / T_ref))``. Which resistance follows which process is this
library's reading, not Ecker's.

All arrays are per group, shape ``(G,)``, or per time and group, shape ``(K, G)``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GAS_CONSTANT = 8.314462618  # J/(mol K), the SI value
KELVIN = 273.15


@dataclass(frozen=True)
class CellModel:
    """One cell at its reference temperature. Values are per cell, before lumping.

    ``rc_hold`` says what stays fixed when a resistance changes with temperature or age:
    ``"capacitance"`` (the RC capacitance; ``tau`` scales with ``r``) or ``"tau"`` (the
    time constant, as ``tests/synth.py``).
    """

    ocv_soc: tuple
    ocv_v: tuple
    capacity_ah: float
    r0_ohm: float
    r1_ohm: float
    tau1_s: float
    r2_ohm: float = 0.0
    tau2_s: float = 0.0
    hyst_v: float = 0.0  # largest hysteresis voltage per cell
    hyst_rate: float = 0.0  # kappa: the hysteresis moves 1 - 1/e of the way per 1/kappa of state of charge
    t_ref_c: float = 25.0
    ea_r0_j_mol: float = 0.0  # 0 = the resistance does not depend on temperature
    ea_r1_j_mol: float = 0.0
    rc_hold: str = "capacitance"
    name: str = ""

    def __post_init__(self) -> None:
        soc = np.asarray(self.ocv_soc, dtype=float)
        if soc.ndim != 1 or len(soc) < 2 or len(soc) != len(self.ocv_v) or np.any(np.diff(soc) <= 0):
            raise ValueError("the OCV curve needs at least two points with increasing state of charge")
        if self.capacity_ah <= 0 or self.r0_ohm < 0 or self.r1_ohm < 0 or self.tau1_s <= 0:
            raise ValueError("capacity and tau1 must be positive, resistances not negative")
        if self.r2_ohm > 0 and self.tau2_s <= 0:
            raise ValueError("a second RC element needs a positive tau2")
        if self.rc_hold not in ("capacitance", "tau"):
            raise ValueError("rc_hold is 'capacitance' or 'tau'")


class OcvCurve:
    """The open-circuit voltage of one cell and its integral over state of charge."""

    def __init__(self, soc, volts) -> None:
        self.soc = np.asarray(soc, dtype=float)
        self.v = np.asarray(volts, dtype=float)
        seg = np.diff(self.soc) * 0.5 * (self.v[1:] + self.v[:-1])
        self._f = np.r_[0.0, np.cumsum(seg)]  # integral from soc[0] to each point
        self._slope = np.diff(self.v) / np.diff(self.soc)

    def __call__(self, soc):
        return np.interp(soc, self.soc, self.v)

    def integral(self, soc):
        """``F(soc)``: the integral of the curve from its first point, in V per unit state of
        charge; flat beyond the ends, as ``numpy.interp``."""
        soc = np.asarray(soc, dtype=float)
        k = np.clip(np.searchsorted(self.soc, soc, side="right") - 1, 0, len(self.soc) - 2)
        s0 = self.soc[k]
        inside = np.clip(soc, self.soc[0], self.soc[-1])
        d = inside - s0
        f = self._f[k] + d * (self.v[k] + 0.5 * self._slope[k] * d)
        # Beyond the ends the curve is flat.
        f = f + np.where(soc < self.soc[0], (soc - self.soc[0]) * self.v[0], 0.0)
        f = f + np.where(soc > self.soc[-1], (soc - self.soc[-1]) * self.v[-1], 0.0)
        return f


def temperature_factor(ea_j_mol, temp_c, t_ref_c: float = 25.0):
    """``(T / T_ref) exp(Ea / R (1 / T - 1 / T_ref))``: 1 at the reference temperature,
    above 1 when colder (Ecker et al. 2015, Eq. 2 inverted). ``Ea = 0`` gives exactly 1."""
    ea = np.asarray(ea_j_mol, dtype=float)
    t = np.asarray(temp_c, dtype=float) + KELVIN
    t_ref = t_ref_c + KELVIN
    out = (t / t_ref) * np.exp(ea / GAS_CONSTANT * (1.0 / t - 1.0 / t_ref))
    return np.where(ea == 0.0, 1.0, out)


@dataclass
class GroupState:
    """The electrical state of every group: state of charge, RC voltages, hysteresis."""

    soc: np.ndarray
    p1: np.ndarray
    p2: np.ndarray
    h: np.ndarray

    def copy(self) -> "GroupState":
        return GroupState(self.soc.copy(), self.p1.copy(), self.p2.copy(), self.h.copy())


@dataclass
class GroupParams:
    """The parameters of every group during one block (already lumped, aged and at temperature)."""

    series: np.ndarray  # cells in series in the group
    capacity_ah: np.ndarray
    r0: np.ndarray
    r1: np.ndarray
    tau1: np.ndarray
    r2: np.ndarray
    tau2: np.ndarray
    h_max: np.ndarray  # group volts
    kappa: np.ndarray


def _rc(p0, target, tau, t):
    return target + (p0 - target) * np.exp(-t / tau)


def _rc_integral(p0, target, tau, t):
    """The integral of an RC voltage over ``[0, t]``."""
    return target * t + (p0 - target) * tau * -np.expm1(-t / tau)


def _hyst(h0, h_inf, beta, t):
    return h_inf + (h0 - h_inf) * np.exp(-beta * t)


def _hyst_integral(h0, h_inf, beta, t):
    bt = beta * t
    # (1 - exp(-bt)) / beta, with its limit t when beta is 0
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(bt > 1e-12, -np.expm1(-bt) / np.where(beta > 0, beta, 1.0), t * (1.0 - 0.5 * bt))
    return h_inf * t + (h0 - h_inf) * frac


def _targets(params: GroupParams, current, i_soc):
    has2 = params.r2 > 0
    t2 = np.where(has2, params.tau2, 1.0)
    beta = params.kappa * np.abs(i_soc) / (3600.0 * params.capacity_ah)
    h_inf = np.sign(i_soc) * params.h_max
    return current * params.r1, current * params.r2, t2, has2, beta, h_inf


def trajectory(state: GroupState, params: GroupParams, ocv: OcvCurve, current, i_soc, t):
    """The state of charge and terminal voltage of every group at offsets ``t`` (seconds,
    shape ``(K,)``) into a block with a constant terminal ``current`` and open-circuit
    current ``i_soc`` per group. Returns ``(soc, v)``, each ``(K, G)``.

    The arithmetic follows ``tests/synth.py`` term by term, so the ``synth`` preset gives
    the same numbers."""
    t = np.asarray(t, dtype=float)[:, None]
    target1, target2, t2, has2, beta, h_inf = _targets(params, current, i_soc)
    soc = state.soc + i_soc * t / (3600.0 * params.capacity_ah)
    p1 = _rc(state.p1, target1, params.tau1, t)
    p2 = np.where(has2, _rc(state.p2, target2, t2, t), 0.0)
    h = np.where(params.h_max > 0, _hyst(state.h, h_inf, beta, t), state.h)
    v = ocv(soc) * params.series + h + current * params.r0 + p1 + p2
    return soc, v


@dataclass
class BlockIntegrals:
    """Exact integrals over one block, per group."""

    charge_ah: np.ndarray  # integral of the open-circuit current i_soc, Ah
    stored_wh: np.ndarray  # open-circuit voltage integrated over that charge
    heat_wh: np.ndarray  # polarization heat: i (v - ocv)
    drain_wh: np.ndarray  # open-circuit voltage times the drains
    terminal_wh: np.ndarray  # v i: the energy into the group at its terminals
    v_dt: np.ndarray  # integral of the group terminal voltage, V s
    ocv_dt: np.ndarray  # integral of the group open-circuit voltage, V s


def advance(state: GroupState, params: GroupParams, ocv: OcvCurve, current, i_soc, duration: float):
    """The state at the end of a block of ``duration`` seconds, and the block's integrals."""
    target1, target2, t2, has2, beta, h_inf = _targets(params, current, i_soc)
    dur = float(duration)
    soc1 = state.soc + i_soc * dur / (3600.0 * params.capacity_ah)
    p1 = _rc(state.p1, target1, params.tau1, dur)
    p2 = np.where(has2, _rc(state.p2, target2, t2, dur), 0.0)
    has_h = params.h_max > 0
    h1 = np.where(has_h, _hyst(state.h, h_inf, beta, dur), state.h)

    ip1 = _rc_integral(state.p1, target1, params.tau1, dur)
    ip2 = np.where(has2, _rc_integral(state.p2, target2, t2, dur), 0.0)
    ih = np.where(has_h, _hyst_integral(state.h, h_inf, beta, dur), state.h * dur)
    # The open-circuit part: the integral over charge, and over time.
    df = ocv.integral(soc1) - ocv.integral(state.soc)  # V per unit state of charge, per cell
    stored_vs = 3600.0 * params.capacity_ah * df * params.series  # V A s
    dsoc = soc1 - state.soc
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_ocv = np.where(np.abs(dsoc) > 1e-13, df / np.where(dsoc != 0, dsoc, 1.0), ocv(state.soc))
    ocv_dt = mean_ocv * params.series * dur
    over_dt = ih + current * params.r0 * dur + ip1 + ip2  # integral of (v - ocv)
    v_dt = ocv_dt + over_dt
    out = BlockIntegrals(
        charge_ah=i_soc * dur / 3600.0,
        stored_wh=stored_vs / 3600.0,
        heat_wh=current * over_dt / 3600.0,
        drain_wh=(current - i_soc) * ocv_dt / 3600.0,
        terminal_wh=current * v_dt / 3600.0,
        v_dt=v_dt,
        ocv_dt=ocv_dt,
    )
    return GroupState(soc1, p1, p2, h1), out
