"""The pack and the engine that runs it.

A pack is a series string of cell groups (``cell.py``) with a thermal model
(``thermal.py``), a BMS (``bms.py``) and an ageing schedule. ``Engine.run`` takes the
duty's pieces (``duty.py``) and steps through them in *blocks*: stretches in which the
terminal current and every parameter are constant, so the circuit is solved exactly
(closed forms, no integration error). A block ends at the end of a piece, at midnight,
at a month's end, at a change of the fan, at the start of a leak (its first step on the
grid), every ``update_s`` seconds (when set; the parameters that depend on temperature,
state of charge and age are evaluated at each block's start), every ``hold_s`` for a power piece (its current is set from the power and
the voltage at the block's start), and at a stop or at ``until_soc``, found in the step
in which it happens.

Ageing is set, not predicted: each group's capacity falls by ``fade_points_per_year``
points of its starting capacity per year, its resistances grow by
``resistance_growth_per_year`` of their starting values per year; the truth is known by
construction.

The engine yields one ``Month`` at a time: the pack's true terminal signals at every step,
the cell groups at every ``cell_every_s``, the stops, the blocks, and per group the
month's sums of charge and energy (for the simulator's own balance checks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .bms import BmsSettings
from .cell import CellModel, GroupParams, GroupState, OcvCurve, advance, temperature_factor, trajectory
from .duty import DAY_S, Piece
from .thermal import Network, Profile, ThermalModel

YEAR_S = 365.25 * 86400.0


@dataclass(frozen=True)
class PackModel:
    cell: CellModel
    groups: int = 1
    series: int = 1  # cells in series per group
    parallel: int = 1
    capacity_factor: tuple = ()  # per group, times the cell capacity (spread); default all 1
    resistance_factor: tuple = ()  # per group, times the cell resistances; default all 1
    soc0: tuple = ()  # per group; default 0.5
    fade_points_per_year: float = 0.0
    fade_points_per_year_groups: tuple = ()  # per group, replaces the pack value when given
    resistance_growth_per_year: float = 0.0
    resistance_law: str = "arrhenius"  # or "synth-day", the law of tests/synth.py
    r_temp_coeff_per_c: float = 0.0  # "synth-day" only
    self_discharge_a: tuple = ()  # per group, A, constant
    leak_ohm: tuple = ()  # per group, a resistor across each cell (inf = none)
    leak_from_s: tuple = ()  # per group, when the leak starts
    thermal: ThermalModel = field(default_factory=ThermalModel)
    pack_profile: Profile = field(default_factory=Profile)  # the pack's temperature when prescribed
    ambient: Profile = field(default_factory=lambda: Profile(mean_c=20.0))
    fan_points: tuple = ()  # (time_s, factor): the fan factor from that time on; 1 before the first
    bms: BmsSettings = field(default_factory=BmsSettings)

    def __post_init__(self) -> None:
        if self.resistance_law not in ("arrhenius", "synth-day"):
            raise ValueError("resistance_law is 'arrhenius' or 'synth-day'")
        if self.bms.stop_rule == "synth" and self.groups != 1:
            raise ValueError("the 'synth' stop rule models a one-group pack")
        if self.cell.tables is not None and (self.resistance_law == "synth-day" or self.bms.stop_rule == "synth"):
            raise ValueError("the 'synth' rules take a cell without resistance tables")

    # --- per group values ------------------------------------------------------------
    def _per_group(self, values: tuple, default: float) -> np.ndarray:
        if not values:
            return np.full(self.groups, float(default))
        out = np.asarray(values, dtype=float)
        if out.shape != (self.groups,):
            raise ValueError(f"a per-group setting has {len(out)} values for {self.groups} groups")
        return out

    @property
    def cells_in_series(self) -> int:
        return self.groups * self.series

    @property
    def nominal_ah(self) -> float:
        return self.cell.capacity_ah * self.parallel

    def capacity0(self) -> np.ndarray:
        return self.cell.capacity_ah * self.parallel * self._per_group(self.capacity_factor, 1.0)


@dataclass
class Month:
    start: pd.Timestamp
    seconds: np.ndarray  # since the run's start, at each step
    current_a: np.ndarray  # true terminal current
    voltage_v: np.ndarray  # true terminal voltage
    temp_sensor_c: np.ndarray  # true temperature at the sensor's point
    temp_ambient_c: np.ndarray
    v_cell_max: np.ndarray  # highest and lowest mean cell voltage of any group
    v_cell_min: np.ndarray
    cells: pd.DataFrame
    stops: pd.DataFrame
    blocks: pd.DataFrame
    balance: pd.DataFrame
    # The BMS's voltage limits at each step, V per cell (the "synth" rule: its pack limits
    # divided by the cells in series), and the true state of charge at the month's ends.
    stop_rule: str = ""
    limit_max_v: np.ndarray | None = None
    limit_min_v: np.ndarray | None = None
    soc_start: np.ndarray | None = None
    soc_end: np.ndarray | None = None


_NONE = np.zeros(0)
_STOP_COLUMNS = [
    "seconds", "kind", "rule", "group", "v_before", "v_after", "limit_v", "soc", "capacity_ah",
    "bms_soc", "model_soc", "model_capacity_ah", "tag", "current_a",
]
_BLOCK_COLUMNS = ["seconds", "duration_s", "current_a"]
_BAL = ["charge_ah", "cap_dsoc_ah", "stored_wh", "heat_wh", "terminal_wh", "supply_ah", "supply_wh",
        "self_ah", "self_wh", "leak_ah", "leak_wh", "bleed_ah", "bleed_wh"]


class Engine:
    def __init__(self, pack: PackModel, step_s: float = 10.0, update_s: float | None = None,
                 cell_every_s: float = 600.0, log_blocks: bool = True) -> None:
        self.pack = pack
        self.dt = float(step_s)
        self.update_s = float(update_s) if update_s else None
        self.cell_every_s = float(cell_every_s)
        self.log_blocks = log_blocks
        cell = pack.cell
        g = pack.groups
        self.ocv = OcvCurve(cell.ocv_soc, cell.ocv_v)
        self.series = np.full(g, float(pack.series))
        self.c0 = pack.capacity0()
        rf = pack._per_group(pack.resistance_factor, 1.0)
        self.r_scale = pack.series / pack.parallel * rf  # cell ohms to group ohms
        self.tables = cell.tables
        self._hyst_table = len(cell.hyst_table) > 0
        self._needs_soc = self.tables is not None or self._hyst_table
        self.r0_ref = cell.r0_ohm * pack.series / pack.parallel * rf
        self.r1_ref = cell.r1_ohm * pack.series / pack.parallel * rf
        self.r2_ref = cell.r2_ohm * pack.series / pack.parallel * rf
        self.fade = pack._per_group(pack.fade_points_per_year_groups, pack.fade_points_per_year)
        self.self_a = pack._per_group(pack.self_discharge_a, 0.0)
        self.leak_ohm = pack._per_group(pack.leak_ohm, math.inf)
        self.leak_from = pack._per_group(pack.leak_from_s, 0.0)
        # A leak starts at the first step at or after its time; that step starts a block.
        self.leak_on = np.ceil(self.leak_from / self.dt - 1e-9) * self.dt
        on = self.leak_on[np.isfinite(self.leak_ohm) & (self.leak_on > 0)]
        self._leak_t = np.unique(on)
        self._leaks = bool(np.isfinite(self.leak_ohm).any())
        self._networks: dict = {}
        self._fan_t = np.array([p[0] for p in pack.fan_points], dtype=float)
        self._fan_f = np.array([p[1] for p in pack.fan_points], dtype=float)
        if pack.thermal.mode == "network":
            s = pack.thermal.sensor
            self.sensor = g if s == "housing" else int(s)
        else:
            self.sensor = None

    # --- parameters at a time ----------------------------------------------------------
    def capacity(self, t: float) -> np.ndarray:
        years = t / YEAR_S
        return self.c0 * (1.0 - self.fade / 100.0 * years)

    def _synth_scale(self, day: float) -> float:
        p = self.pack
        growth = 1.0 + p.resistance_growth_per_year * day / 365.25
        return growth * np.exp(-p.r_temp_coeff_per_c * (p.pack_profile.day_mean(day) - 25.0))

    def params(self, t: float, temps: np.ndarray, soc: np.ndarray | None = None) -> GroupParams:
        """The groups' parameters at time ``t`` and these temperatures; ``soc`` (the present states
        when left out) matters only to a cell with tables."""
        p, cell = self.pack, self.pack.cell
        if self._needs_soc and soc is None:
            soc = self.state.soc
        h_max = cell.hyst_bound(soc) * p.series if self._hyst_table else np.full(p.groups, cell.hyst_v * p.series)
        if self.tables is not None:
            age = 1.0 + p.resistance_growth_per_year * (t / YEAR_S)
            r0, r1, tau1 = self.tables(soc, temps)
            hold = cell.rc_hold == "tau"
            two = cell.r2_ohm > 0  # the second RC element has no table: its own value and t_ref_c
            s1 = age * temperature_factor(cell.ea_r1_j_mol, temps, cell.t_ref_c) if two else 1.0
            return GroupParams(
                series=self.series,
                capacity_ah=self.capacity(t),
                r0=r0 * (self.r_scale * age),
                r1=r1 * (self.r_scale * age),
                tau1=tau1 if hold else tau1 * age,
                r2=self.r2_ref * s1,
                tau2=np.full(p.groups, cell.tau2_s if cell.tau2_s > 0 else 1.0) * (1.0 if hold or not two else s1),
                h_max=h_max,
                kappa=np.full(p.groups, cell.hyst_rate),
            )
        if p.resistance_law == "synth-day":
            s0 = s1 = self._synth_scale(math.floor(t / DAY_S))
        else:
            age = 1.0 + p.resistance_growth_per_year * (t / YEAR_S)
            s0 = age * temperature_factor(cell.ea_r0_j_mol, temps, cell.t_ref_c)
            s1 = age * temperature_factor(cell.ea_r1_j_mol, temps, cell.t_ref_c)
        r1 = self.r1_ref * s1
        if cell.rc_hold == "tau":
            tau1 = np.full(p.groups, cell.tau1_s)
            tau2 = np.full(p.groups, cell.tau2_s if cell.tau2_s > 0 else 1.0)
        else:
            tau1 = cell.tau1_s * (r1 / np.where(self.r1_ref > 0, self.r1_ref, 1.0))
            tau1 = np.where(self.r1_ref > 0, tau1, cell.tau1_s)
            tau2 = np.full(p.groups, cell.tau2_s if cell.tau2_s > 0 else 1.0) * s1
        return GroupParams(
            series=self.series,
            capacity_ah=self.capacity(t),
            r0=self.r0_ref * s0,
            r1=r1,
            tau1=tau1,
            r2=self.r2_ref * s1,
            tau2=tau2,
            h_max=h_max,
            kappa=np.full(p.groups, cell.hyst_rate),
        )

    def fan(self, t: float) -> float:
        if len(self._fan_t) == 0:
            return 1.0
        k = np.searchsorted(self._fan_t, t, side="right") - 1
        return 1.0 if k < 0 else float(self._fan_f[k])

    def network(self, fan: float) -> Network:
        if fan not in self._networks:
            self._networks[fan] = Network(self.pack.thermal, self.pack.groups, fan)
        return self._networks[fan]

    def _next_fan_change(self, t: float) -> float:
        if len(self._fan_t) == 0:
            return math.inf
        later = self._fan_t[self._fan_t > t]
        return float(later[0]) if len(later) else math.inf

    # --- the run -------------------------------------------------------------------------
    def _start(self) -> None:
        """The pack's states at the start of a run."""
        p = self.pack
        g = p.groups
        self.state = GroupState(soc=p._per_group(p.soc0, 0.5), p1=np.zeros(g), p2=np.zeros(g), h=np.zeros(g))
        t_first = float(p.ambient(0.0)) if p.thermal.mode == "network" else float(p.pack_profile(0.0))
        self.temps = np.full(g + 1, t_first)
        self.bms_soc = float(np.mean(self.state.soc))
        self.bms_cap = p.nominal_ah * p.bms.soh_percent / 100.0
        self.model = [float(self.state.soc[0]), 0.0] if p.bms.stop_rule == "synth" else None

    def run(self, pieces: list[Piece], day_limits: np.ndarray, start: pd.Timestamp, months: int):
        """Yield ``Month`` objects. ``day_limits[d]`` = the day's random errors (upper, lower)."""
        g = self.pack.groups
        self._start()
        bounds = pd.date_range(start, start + pd.DateOffset(months=months), freq="MS")
        if len(bounds) != months + 1 or bounds[0] != start:
            raise ValueError("a run starts at midnight on the first day of a month")
        month_s = [(b - start).total_seconds() for b in bounds]
        k_piece, latched = 0, False
        tb = pieces[0].start_s if pieces else 0.0
        for mi in range(months):
            m0, m1 = month_s[mi], month_s[mi + 1]
            acc = _MonthAcc(m0, int(round((m1 - m0) / self.dt)), g)
            acc.soc_start = self.state.soc.copy()
            while k_piece < len(pieces) and tb < m1:
                piece = pieces[k_piece]
                tb, latched = self._block(piece, tb, latched, m1, day_limits, acc)
                if tb >= piece.end_s:
                    k_piece += 1
                    latched = False
                    if k_piece < len(pieces):
                        tb = pieces[k_piece].start_s
            yield self._month(start, acc)

    def _block(self, piece: Piece, tb: float, latched: bool, m1: float, day_limits, acc) -> tuple[float, bool]:
        """Run one block from ``tb`` within ``piece``; return the next block's start and the latch."""
        p, bms = self.pack, self.pack.bms
        g, dt = p.groups, self.dt
        n_series = p.cells_in_series
        state, temps = self.state, self.temps
        te_max = min(piece.end_s, m1, (math.floor(tb / DAY_S) + 1) * DAY_S, self._next_fan_change(tb))
        if len(self._leak_t):
            later = self._leak_t[self._leak_t > tb]
            if len(later):
                te_max = min(te_max, float(later[0]))
        if self.update_s:
            te_max = min(te_max, (math.floor(tb / self.update_s + 1e-9) + 1) * self.update_s)
        if piece.mode == "power" and not latched:
            te_max = min(te_max, tb + (piece.hold_s or dt))
        kmax = int(round((te_max - tb) / dt))
        if kmax <= 0:
            raise ValueError(f"a block boundary at {te_max} s is off the {dt} s step grid")
        prm = self.params(tb, temps[:g])
        day = int(math.floor(tb / DAY_S))
        current = 0.0
        if piece.active and not latched:
            current = self._ask(piece, state, prm)
            charge_max, discharge_max = bms.current_limits(float(temps[:g].max()), float(temps[:g].min()), p.nominal_ah)
            current = min(max(current, -discharge_max), charge_max)
        # Drains at the block start.
        v_cell0 = self.ocv(state.soc)
        if self._leaks:
            finite = np.isfinite(self.leak_ohm)
            leak = np.where((tb >= self.leak_on) & finite, v_cell0 / np.where(finite, self.leak_ohm, 1.0), 0.0)
        else:
            leak = np.zeros(g)
        bleed = self._bleed(state, prm, current, v_cell0)
        drains = bms.supply_a + self.self_a + leak + bleed
        # The limits of this block.
        e_max, e_min = day_limits[day]
        if bms.stop_rule == "synth":
            lim_max = n_series * (bms.v_cell_max + e_max)
            v_min = n_series * (bms.v_cell_min + e_min)
            lim_min = v_min - n_series * piece.v_min_offset_cell if piece.v_min_offset_cell else v_min
        else:
            lim_max = bms.v_cell_max + e_max + bms.offset(bms.v_max_points, tb / DAY_S)
            lim_min = bms.v_cell_min + e_min + bms.offset(bms.v_min_points, tb / DAY_S) - piece.v_min_offset_cell
        k_end, stop, v = kmax, None, None
        if current != 0.0:
            k_end, stop, v = self._search(state, prm, current, drains, kmax, lim_max, lim_min, tb, piece)
            if k_end == 0:  # the limit is reached at the block's start: no current in this block
                self._record_stop(stop, tb, tb, piece, acc)
                latched, current, k_end, stop, v = True, 0.0, kmax, None, None
                if bms.balance:  # balancing decides again, with no current
                    bleed = self._bleed(state, prm, current, v_cell0)
                    drains = bms.supply_a + self.self_a + leak + bleed
        i_soc = current - drains
        cur_g = np.full(g, current)
        te = tb + k_end * dt
        dur = k_end * dt
        # The block's samples.
        i0 = int(round((tb - acc.m0) / dt))
        sl = slice(i0, i0 + k_end)
        offs = np.arange(k_end) * dt
        if v is None:
            _, v = trajectory(state, prm, self.ocv, cur_g, i_soc, offs)
        else:  # the search has run through these steps with this current
            v = v[:k_end]
        acc.current_a[sl] = current
        acc.voltage_v[sl] = v.sum(axis=1) if g > 1 else v[:, 0]
        vc = v / self.series
        acc.v_cell_max[sl] = vc.max(axis=1)
        acc.v_cell_min[sl] = vc.min(axis=1)
        acc.temp_ambient_c[sl] = p.ambient(tb + offs)
        scale = n_series if bms.stop_rule == "synth" else 1.0
        acc.limit_max[sl] = lim_max / scale
        acc.limit_min[sl] = lim_min / scale
        # Advance the cells; then the temperatures, with the block's mean heat.
        new_state, ints = advance(state, prm, self.ocv, cur_g, i_soc, dur)
        cell_t = self._cell_times(tb, te)
        cell_temps = None
        if p.thermal.mode == "network":
            t_air = float(p.ambient(tb))
            heat = np.zeros(g + 1)
            leak_w = leak * ints.ocv_dt / dur
            bleed_w = bleed * ints.ocv_dt / dur
            self_w = self.self_a * ints.ocv_dt / dur  # self-discharge heats its own group
            heat[:g] = ints.heat_wh * 3600.0 / dur + leak_w + self_w
            if p.thermal.bleed_heat_to == "housing":
                heat[g] += bleed_w.sum()
            else:
                heat[:g] += bleed_w
            net = self.network(self.fan(tb))
            acc.temp_sensor_c[sl] = net.at(temps, heat, t_air, offs)[:, self.sensor]
            if len(cell_t):
                cell_temps = net.at(temps, heat, t_air, cell_t - tb)[:, :g]
            temps_end = net.end(temps, heat, t_air, dur)
        else:
            acc.temp_sensor_c[sl] = p.pack_profile(tb + offs)
            if len(cell_t):
                cell_temps = np.repeat(np.asarray(p.pack_profile(cell_t), dtype=float)[:, None], g, axis=1)
            temps_end = np.full(g + 1, float(p.pack_profile(te)))
        if len(cell_t):
            soc_c, v_c = trajectory(state, prm, self.ocv, cur_g, i_soc, cell_t - tb)
            for j, tc in enumerate(cell_t):
                acc.cells.append((tc, soc_c[j], v_c[j], cell_temps[j], prm.capacity_ah, prm.r0, leak, bleed))
        # The month's sums.
        b = acc.bal
        b["charge_ah"] += ints.charge_ah
        b["cap_dsoc_ah"] += prm.capacity_ah * (new_state.soc - state.soc)
        b["stored_wh"] += ints.stored_wh
        b["heat_wh"] += ints.heat_wh
        b["terminal_wh"] += ints.terminal_wh
        for name, a in (("supply", bms.supply_a), ("self", self.self_a), ("leak", leak), ("bleed", bleed)):
            b[name + "_ah"] += a * dur / 3600.0
            b[name + "_wh"] += a * ints.ocv_dt / 3600.0
        self.bms_soc += current * dur / (3600.0 * self.bms_cap)
        if self.model is not None:
            self._model_advance(current, dur, tb)
        if self.log_blocks:
            acc.blocks.append((tb, dur, current))
        self.state, self.temps = new_state, temps_end
        if stop is not None:
            self._record_stop(stop, te, tb, piece, acc)
            latched = True
            if te == piece.end_s:
                if self.log_blocks:
                    acc.blocks.append((te, 0.0, 0.0))  # the rest after a stop at the piece's end is empty
                if self.model is not None:
                    self._model_advance(0.0, 0.0, tb)
        return te, latched

    # --- helpers ---------------------------------------------------------------------------
    def _bleed(self, state: GroupState, prm: GroupParams, current: float, v_cell0: np.ndarray) -> np.ndarray:
        """The balancing bleed per group (A), decided at the block start with this current."""
        bms, g = self.pack.bms, self.pack.groups
        if not bms.balance:
            return np.zeros(g)
        _, v_now = trajectory(state, prm, self.ocv, np.full(g, current), np.full(g, current), [0.0])
        return np.where(bms.bleeding(v_now[0] / self.series), v_cell0 / bms.bleed_ohm, 0.0)

    def _ask(self, piece: Piece, state: GroupState, prm: GroupParams) -> float:
        if piece.mode == "current":
            return float(piece.value)
        # Power: P = I (E + R I) at the block start.
        e = float((self.ocv(state.soc) * prm.series + state.h + state.p1 + state.p2).sum())
        r = float(prm.r0.sum())
        pw = float(piece.value)
        if r <= 0:
            return pw / e
        disc = e * e + 4.0 * r * pw
        if disc < 0:  # beyond the largest power the pack can give: its largest-power point
            return -e / (2.0 * r)
        return (-e + math.sqrt(disc)) / (2.0 * r)

    def _search(self, state, prm, current, drains, kmax, lim_max, lim_min, tb, piece):
        """The first step end (0..kmax) at which a limit is reached (0 = at the block's start), the stop,
        and the groups' voltages at the steps' ends where the rule reads the plant's."""
        g = self.pack.groups
        taus = np.arange(kmax + 1) * self.dt
        charging = current > 0
        limit = lim_max if charging else lim_min
        vc = v = None
        if self.model is not None:
            v_series = self._model_volts(current, taus, tb)
            over = v_series >= lim_max if charging else v_series <= lim_min
            group = -1
        else:
            _, v = trajectory(state, prm, self.ocv, np.full(g, current), current - drains, taus)
            if self.pack.bms.stop_rule == "pack":
                v_series = v.sum(axis=1) / self.pack.cells_in_series
                over = v_series >= lim_max if charging else v_series <= lim_min
                group = -1
            else:
                vc = v / self.series
                over = (vc >= lim_max if charging else vc <= lim_min).any(axis=1)
                group = None
        k_stop = int(np.argmax(over)) if over.any() else None
        k_until = None
        if piece.until_soc is not None:
            soc_b = self.bms_soc + current * taus / (3600.0 * self.bms_cap)
            hit = soc_b >= piece.until_soc if charging else soc_b <= piece.until_soc
            if hit.any():
                k_until = int(np.argmax(hit))
        if k_stop is None and k_until is None:
            return kmax, None, v
        if k_stop is not None and (k_until is None or k_stop <= k_until):
            if group is None:
                group = int(np.argmax(vc[k_stop])) if charging else int(np.argmin(vc[k_stop]))
                trace = vc[:, group]
            else:
                trace = v_series
            v_before = float(trace[k_stop - 1]) if k_stop > 0 else math.nan
            return k_stop, dict(kind="full" if charging else "empty", group=group, v_before=v_before,
                                v_after=float(trace[k_stop]), limit=limit, current=current), v
        return k_until, dict(kind="until", group=-1, v_before=math.nan, v_after=math.nan, limit=piece.until_soc,
                             current=current), v

    def _model_params(self, t: float):
        """The synth BMS's copy on the day of ``t``: capacity at midnight, resistances of the day."""
        day = math.floor(t / DAY_S)
        cap = float(self.capacity(day * DAY_S)[0])
        scale = self._synth_scale(day)
        return cap, float(self.r0_ref[0]) * scale, float(self.r1_ref[0]) * scale, self.pack.cell.tau1_s

    def _model_volts(self, current, taus, tb):
        cap, r0, r1, tau1 = self._model_params(tb)
        soc, pv = self.model
        rate = (current - self.pack.bms.supply_a - float(self.self_a[0])) / (3600.0 * cap)
        target = current * r1
        n = self.pack.cells_in_series
        return n * self.ocv(soc + rate * taus) + current * r0 + target + (pv - target) * np.exp(-taus / tau1)

    def _model_advance(self, current, length, tb):
        cap, r0, r1, tau1 = self._model_params(tb)
        rate = (current - self.pack.bms.supply_a - float(self.self_a[0])) / (3600.0 * cap)
        target = current * r1
        self.model[0] = self.model[0] + rate * length
        self.model[1] = target + (self.model[1] - target) * np.exp(-length / tau1)

    def _record_stop(self, stop, t, t_block, piece, acc) -> None:
        grp = stop["group"]
        cap = self.capacity(t)
        if grp < 0:
            soc, c = float(np.mean(self.state.soc)), float(np.mean(cap))
        else:
            soc, c = float(self.state.soc[grp]), float(cap[grp])
        model_soc = model_cap = math.nan
        if self.model is not None:
            model_soc = self.model[0]
            model_cap = self._model_params(t_block)[0]
        rule = "until" if stop["kind"] == "until" else self.pack.bms.stop_rule
        acc.stops.append((t, stop["kind"], rule, grp, stop["v_before"], stop["v_after"], stop["limit"],
                          soc, c, self.bms_soc, model_soc, model_cap, piece.tag, stop["current"]))
        if stop["kind"] == "full":
            self.bms_soc = 1.0

    def _cell_times(self, tb: float, te: float) -> np.ndarray:
        e = self.cell_every_s
        first = math.ceil(tb / e) * e
        return np.arange(first, te, e) if first < te else _NONE

    def _month(self, start, acc) -> Month:
        g = self.pack.groups
        cells = acc.cells
        seconds = acc.m0 + np.arange(len(acc.current_a)) * self.dt
        names = ["seconds", "group", "soc", "voltage_v", "temp_c", "capacity_ah", "r0_ohm", "leak_a", "bleed_a"]
        if cells:
            cell_frame = pd.DataFrame({
                "seconds": np.repeat([c[0] for c in cells], g),
                "group": np.tile(np.arange(g), len(cells)),
                **{name: np.concatenate([c[k] for c in cells]) for k, name in enumerate(names[2:], start=1)},
            })
        else:
            cell_frame = pd.DataFrame({name: pd.Series(dtype=float) for name in names})
        return Month(
            start=start + pd.to_timedelta(acc.m0, unit="s"),
            seconds=seconds,
            current_a=acc.current_a, voltage_v=acc.voltage_v, temp_sensor_c=acc.temp_sensor_c,
            temp_ambient_c=acc.temp_ambient_c, v_cell_max=acc.v_cell_max, v_cell_min=acc.v_cell_min,
            cells=cell_frame,
            stops=pd.DataFrame(acc.stops, columns=_STOP_COLUMNS),
            blocks=pd.DataFrame(acc.blocks, columns=_BLOCK_COLUMNS),
            balance=pd.DataFrame({"group": np.arange(g), **acc.bal}),
            stop_rule=self.pack.bms.stop_rule,
            limit_max_v=acc.limit_max, limit_min_v=acc.limit_min,
            soc_start=acc.soc_start, soc_end=self.state.soc.copy(),
        )


class _MonthAcc:
    def __init__(self, m0: float, n: int, g: int) -> None:
        self.m0 = m0
        self.current_a, self.voltage_v = np.zeros(n), np.zeros(n)
        self.temp_sensor_c, self.temp_ambient_c = np.zeros(n), np.zeros(n)
        self.v_cell_max, self.v_cell_min = np.zeros(n), np.zeros(n)
        self.limit_max, self.limit_min = np.full(n, np.nan), np.full(n, np.nan)
        self.soc_start = None
        self.cells, self.stops, self.blocks = [], [], []
        self.bal = {k: np.zeros(g) for k in _BAL}
