"""Live mode: a case's pack, run step by step, with set-points from the bus.

``LivePack`` runs the same engine as ``besslib.sim.run``, one step per block. The parameters
that depend on temperature, state of charge and age, and the current of a power piece, are
set again at every step, so a live run follows a batch run closely but not bit for bit.
With ``start`` the batch engine runs up to the first day of a month and live mode goes on
from there.

**Set-points** stay outside the plant: they change the piece the engine is asked for (a
rest, or a tighter state of charge to end at), never the engine's state. They act at the
next step:

* contactor open (``SetOp``, ``StrSetCon``, ``SetCon``: one string, one contactor) or string
  disabled (``StrSetEna``, ``SetEna``): no current;
* inverter stopped or in standby (``SetInvState``): the duty asks for no current. This is
  besslib's reading: the model file only says "information needed by battery";
* ``SoCRsvMin``, ``SocRsvMax``: no discharge at or below, no charge at or above, by the BMS's
  own state of charge. 0 and 100 set no reserve. A reserve that stopped a piece lets it go on when the reserve changes.
* ``AlmRst``: clears the latched alarms and returns to 0.

**What the bus shows** (``state``) is what the BMS acts on: the voltages and the current of the
last step (its start, as in the frames), without sensor errors, the group temperatures, its own state of charge, its
settings and limits, and alarms from its own thresholds:

* ``OVER_TEMP_ALARM`` at ``stop_temp_c``, latched until a reset; ``OVER_TEMP_WARNING`` while the
  derate table lowers a limit because of heat; ``UNDER_TEMP_ALARM`` while cold blocks charging;
* ``OVER_VOLT_WARNING``, ``UNDER_VOLT_WARNING`` while a full or an empty stop holds;
* ``OVER_SOC_MAX_WARNING``, ``UNDER_SOC_MIN_WARNING`` while a reserve holds;
* ``VOLTAGE_IMBALANCE_WARNING`` while a cell bleeds.

Voltages, current and temperatures are the simulator's own values. Not shown: true capacity,
true state of charge, resistances, leaks, the fan and planted events. The voltage limits are
the BMS's settings, without the day's random error and without a planted drift. No text
carries the case's name or the seed, from which a run could be repeated.

``Hb`` counts simulated seconds since the case's start, so it jumps by the step (10 in the
shipped cases) and turns from 65534 to 0; it stands still between steps.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

import besslib

from ..cell import trajectory
from ..duty import Piece
from ..pack import Month, _MonthAcc
from ..run import _prepare, load_case, spec
from ..truth import _jsonable, tables, write_parquet
from .sunspec import (
    CHARGE_CHARGING, CHARGE_DISCHARGING, CHARGE_EMPTY, CHARGE_FULL, CHARGE_HOLDING, CHARGE_OFF, INVERTER_STARTED, ON,
    STATE_CONNECTED, STATE_DISCONNECTED, STATE_STANDBY, TYPE_LITHIUM_ION, TYPE_UNKNOWN, BusState, SetPoint, scales_for,
)

DEVICE_ADDRESS = 1
HEARTBEAT_TURN = 65535  # the heartbeat counts 0 .. 65534; 65535 would read as "not implemented"
_LITHIUM = {"NMC", "LFP", "LMO", "LMO/NMC", "NCA"}
_STEP_FIELDS = ("current_a", "voltage_v", "temp_sensor_c", "temp_ambient_c", "v_cell_max", "v_cell_min",
                "limit_max_v", "limit_min_v", "seconds")


class EndOfRun(Exception):
    """The case's planned months are over."""


@dataclass(frozen=True)
class Sample:
    """The BMS's measurements in one step: at its start, with the step's current."""

    seconds: float
    current_a: float  # positive = charging, as in the frames
    cell_v: np.ndarray  # mean cell voltage per group
    pack_v: float
    bleeding: np.ndarray  # which groups' cells bleed


class LivePack:
    """A case's pack in live mode. ``start``: the first day of a month, up to which the batch engine
    runs first; ``months``: the length of the duty's plan from the case's start (default: the case's).

    ``keep_truth=False`` keeps no truth tables and no set-point log, so memory stays constant in a
    long run; ``truth_tables`` and ``write_truth`` then raise ``ValueError``."""

    def __init__(self, case, seed: int = 0, overrides: dict | None = None, start=None, months: int | None = None,
                 keep_truth: bool = True) -> None:
        self.seed, self.overrides = int(seed), dict(overrides or {})
        if months is not None and months < 1:
            raise ValueError("months must be at least 1")
        self._data = load_case(case, overrides)
        self.name = str(self._data.get("case", {}).get("name", case if isinstance(case, str) else ""))
        plan = _prepare(self._data, months, self.seed)
        self.pack, self.engine = plan.pack, plan.engine
        self.spec = spec(self._data, plan.pack)
        self._plan = plan
        self._bounds = [(b - plan.start).total_seconds()
                        for b in pd.date_range(plan.start, plan.start + pd.DateOffset(months=plan.months), freq="MS")]
        if len(self._bounds) != plan.months + 1:
            raise ValueError("a run starts at midnight on the first day of a month")
        self._month_index = self._month_of(start)
        if self._month_index:
            for _ in self.engine.run(plan.pieces, plan.limits, plan.start, self._month_index):
                pass
        else:
            self.engine._start()
        self.keep_truth = keep_truth
        self.engine.log_blocks = keep_truth
        self._tb = self._bounds[self._month_index]
        self._live_from = self._tb
        # Pieces stay inside one day, so one of them starts at the month's start.
        self._k = next((k for k, p in enumerate(plan.pieces) if p.start_s >= self._tb), len(plan.pieces))
        self._latched = False
        self._hold = ""  # why the piece is latched: "full", "empty", "until", "reserve-low" or "reserve-high"
        self._acc = self._new_acc()
        self._months: list[Month] = []
        # Set-points as written, and as they act since the last step.
        self._set = {"op": ON, "ena": ON, "inv": INVERTER_STARTED, "rsv_min": 0.0, "rsv_max": 100.0}
        self._acting = dict(self._set)
        self._con_request = self._ena_request = self._alarm_reset = self._control_heartbeat = 0
        self.set_point_log: list[tuple] = []
        self._over_temp = False
        self._check_over_temp()
        self.last = self._sample(self.engine.state, self._params(), 0.0)
        self.scales = self._scales()

    def _month_of(self, start) -> int:
        if start is None:
            return 0
        seconds = (pd.Timestamp(start) - self._plan.start).total_seconds()
        if seconds not in self._bounds[:-1]:
            last = self._plan.start + pd.Timedelta(seconds=self._bounds[-2])
            raise ValueError(f"start must be the first day of a month from {self._plan.start:%Y-%m-%d} to "
                             f"{last:%Y-%m-%d} (a longer plan: months)")
        return self._bounds.index(seconds)

    def _new_acc(self) -> _MonthAcc:
        m0, m1 = self._bounds[self._month_index], self._bounds[self._month_index + 1]
        acc = _MonthAcc(m0, int(round((m1 - m0) / self.engine.dt)), self.pack.groups)
        acc.soc_start = self.engine.state.soc.copy()
        return acc

    # --- time ----------------------------------------------------------------------------
    @property
    def seconds(self) -> float:
        """Seconds since the case's start."""
        return self._tb

    @property
    def time(self) -> pd.Timestamp:
        return self._plan.start + pd.Timedelta(seconds=self._tb)

    @property
    def step_s(self) -> float:
        return self.engine.dt

    # --- stepping --------------------------------------------------------------------------
    def step(self, n: int = 1) -> None:
        """Run ``n`` steps; set-points written before act from the first one."""
        for _ in range(n):
            self._one_step()

    def _one_step(self) -> None:
        engine, pieces = self.engine, self._plan.pieces
        if self._tb >= self._bounds[-1]:
            raise EndOfRun(f"the plan of {self._plan.months} months is over ({self.time:%Y-%m-%d})")
        self._apply_set_points()
        piece = pieces[self._k]
        asked, reserve = self._asked(piece)
        before, params = engine.state, self._params()
        stops = len(self._acc.stops)
        row = int(round((self._tb - self._acc.m0) / engine.dt))
        self._tb, latched = engine._block(asked, self._tb, self._latched, self._tb + engine.dt, self._plan.limits,
                                          self._acc)
        if len(self._acc.stops) > stops:
            kind, current = self._acc.stops[-1][1], self._acc.stops[-1][-1]
            self._hold = kind
            if kind == "until" and reserve:
                self._hold = "reserve-high" if current > 0 else "reserve-low"
        self._latched = latched
        if not self.keep_truth:
            self._acc.cells.clear()
            self._acc.stops.clear()
        if self._tb >= piece.end_s:
            self._k += 1
            self._latched, self._hold = False, ""
        self.last = self._sample(before, params, float(self._acc.current_a[row]), self._acc.m0 + row * engine.dt)
        self._check_over_temp()
        if self._alarm_reset:
            self._alarm_reset = 0
        if self._tb >= self._bounds[self._month_index + 1]:
            self._end_month()

    def _params(self):
        e = self.engine
        return e.params(self._tb, e.temps[: self.pack.groups])

    def _apply_set_points(self) -> None:
        if self._set != self._acting:
            edge = {"reserve-low": "rsv_min", "reserve-high": "rsv_max"}.get(self._hold)
            if edge and self._set[edge] != self._acting[edge]:
                self._latched, self._hold = False, ""
            self._acting = dict(self._set)
        self._con_request = self._ena_request = 0
        if self._alarm_reset:
            self._over_temp = False

    def _asked(self, piece: Piece) -> tuple[Piece, bool]:
        """The piece as the set-points leave it, and whether a reserve sets its end."""
        on = self._acting["op"] == ON and self._acting["ena"] == ON and self._acting["inv"] == INVERTER_STARTED
        if not piece.active:
            return piece, False
        if not on:
            return replace(piece, mode="rest", value=0.0), False
        low, high = self._acting["rsv_min"] / 100.0, self._acting["rsv_max"] / 100.0
        until = piece.until_soc
        if piece.value < 0 and low > 0.0 and (until is None or low > until):
            return replace(piece, until_soc=low), True
        if piece.value > 0 and high < 1.0 and (until is None or high < until):
            return replace(piece, until_soc=high), True
        return piece, False

    def _end_month(self) -> None:
        if self.keep_truth:
            self._months.append(self.engine._month(self._plan.start, self._acc))
        self._month_index += 1
        if self._month_index < self._plan.months:
            self._acc = self._new_acc()

    # --- what the BMS sees -------------------------------------------------------------------
    def _sample(self, state, params, current: float, seconds: float | None = None) -> Sample:
        engine, g = self.engine, self.pack.groups
        amps = np.full(g, current)
        _, v = trajectory(state, params, engine.ocv, amps, amps, [0.0])
        cell_v = v[0] / engine.series
        return Sample(self._tb if seconds is None else seconds, current, cell_v, float(v[0].sum()),
                      self.pack.bms.bleeding(cell_v))

    def _limits(self) -> tuple[float | None, float | None]:
        t = self.engine.temps[: self.pack.groups]
        out = self.pack.bms.current_limits(float(t.max()), float(t.min()), self.pack.nominal_ah)
        return tuple(float(x) if math.isfinite(x) else None for x in out)

    def _check_over_temp(self) -> None:
        stop = self.pack.bms.stop_temp_c
        if stop is not None and float(self.engine.temps[: self.pack.groups].max()) >= stop:
            self._over_temp = True

    def _heat_derates(self, t_max: float) -> bool:
        derate = self.pack.bms.derate
        temps = np.array([p[0] for p in derate], dtype=float)
        for column in (1, 2):
            rates = np.array([p[column] for p in derate], dtype=float)
            cooler = rates[temps <= t_max]
            if len(cooler) and float(np.interp(t_max, temps, rates)) < float(cooler.max()):
                return True
        return False

    def _events(self, t_max: float, t_min: float) -> frozenset:
        bms = self.pack.bms
        out = set()
        if self._over_temp:
            out.add("OVER_TEMP_ALARM")
        if bms.derate and self._heat_derates(t_max):
            out.add("OVER_TEMP_WARNING")
        if bms.charge_block_below_c is not None and t_min < bms.charge_block_below_c:
            out.add("UNDER_TEMP_ALARM")
        held = {"full": "OVER_VOLT_WARNING", "empty": "UNDER_VOLT_WARNING", "reserve-high": "OVER_SOC_MAX_WARNING",
                "reserve-low": "UNDER_SOC_MIN_WARNING"}.get(self._hold)
        if held:
            out.add(held)
        if self.last.bleeding.any():
            out.add("VOLTAGE_IMBALANCE_WARNING")
        return frozenset(out)

    def state(self) -> BusState:
        """What the BMS acts on now."""
        pack, bms, last = self.pack, self.pack.bms, self.last
        n, series = pack.cells_in_series, pack.series
        t = self.engine.temps[: pack.groups]
        t_max, t_min = float(t.max()), float(t.min())
        charge_a, discharge_a = self._limits()
        ah, wh = float(self.spec.nominal_capacity_ah), float(self.spec.nominal_energy_kwh) * 1000.0
        connected, enabled = self._acting["op"] == ON, self._acting["ena"] == ON
        running = self._acting["inv"] == INVERTER_STARTED
        if not connected:
            status = STATE_DISCONNECTED
        else:
            status = STATE_CONNECTED if enabled and running else STATE_STANDBY
        if not (connected and enabled):
            charge = CHARGE_OFF
        elif last.current_a != 0.0:
            charge = CHARGE_CHARGING if last.current_a > 0 else CHARGE_DISCHARGING
        else:
            charge = {"full": CHARGE_FULL, "empty": CHARGE_EMPTY}.get(self._hold, CHARGE_HOLDING)
        return BusState(
            manufacturer="besslib",
            model=f"{n}S {ah:g} Ah {self.spec.chemistry}"[:32],
            version=str(besslib.__version__)[:16],
            serial="SIMULATED",  # no seed: it would give the whole run away
            device_address=DEVICE_ADDRESS,
            ah_rating=ah,
            wh_rating=wh,
            charge_w_max=max(float(p[1]) for p in bms.derate) * wh if bms.derate else None,
            discharge_w_max=max(float(p[2]) for p in bms.derate) * wh if bms.derate else None,
            soc_pct=100.0 * min(max(float(self.engine.bms_soc), 0.0), 1.0),
            soh_pct=float(bms.soh_percent),
            soc_reserve_min_pct=self._set["rsv_min"],
            soc_reserve_max_pct=self._set["rsv_max"],
            heartbeat=int(self._tb) % HEARTBEAT_TURN,
            control_heartbeat=self._control_heartbeat,
            alarm_reset=self._alarm_reset,
            battery_type=TYPE_LITHIUM_ION if self.spec.chemistry in _LITHIUM else TYPE_UNKNOWN,
            state=status,
            charge_status=charge,
            events=self._events(t_max, t_min),
            pack_v=last.pack_v,
            v_max=n * float(bms.v_cell_max),
            v_min=n * float(bms.v_cell_min),
            cell_v_max=float(last.cell_v.max()),
            cell_v_min=float(last.cell_v.min()),
            cell_v_avg=float(last.cell_v.mean()),
            cell_v_max_index=int(last.cell_v.argmax()) * series + 1,
            cell_v_min_index=int(last.cell_v.argmin()) * series + 1,
            current_a=0.0 - last.current_a,  # the bus is positive when discharging
            power_w=0.0 - last.current_a * last.pack_v,
            charge_a_max=charge_a,
            discharge_a_max=discharge_a,
            temp_max_c=t_max,
            temp_min_c=t_min,
            temp_avg_c=float(t.mean()),
            cells_balancing=int(last.bleeding.sum()) * series,
            cells=n,
            connected=connected,
            enabled=enabled,
            set_op=self._set["op"],
            set_inv_state=self._set["inv"],
            set_ena=self._set["ena"],
            con_request=self._con_request,
            ena_request=self._ena_request,
        )

    def _scales(self):
        """Scale factors with room for the largest current and power the duty asks for."""
        pack, bms = self.pack, self.pack.bms
        low_v = pack.cells_in_series * bms.v_cell_min
        amps = max((abs(p.value) for p in self._plan.pieces if p.mode == "current"), default=0.0)
        watts = max((abs(p.value) for p in self._plan.pieces if p.mode == "power"), default=0.0)
        amps = max([amps, watts / low_v] + [float(r) * pack.nominal_ah for p in bms.derate for r in p[1:]])
        return scales_for(self.state(), current_max_a=1.25 * amps, power_max_w=1.25 * watts)

    # --- set-points -----------------------------------------------------------------------------
    def write(self, setpoints: list[SetPoint]) -> None:
        """Take set-points (``sunspec.decode_write``), all or none. They act at the next step."""
        before = self._set_points()
        try:
            self._take(setpoints)
        except Exception:
            self._set_points_back(before)
            raise

    def _set_points(self) -> tuple:
        """The set-points as they are, for ``_set_points_back``."""
        return (dict(self._set), self._con_request, self._ena_request, self._alarm_reset, self._control_heartbeat,
                len(self.set_point_log))

    def _set_points_back(self, before: tuple) -> None:
        values, self._con_request, self._ena_request, self._alarm_reset, self._control_heartbeat, rows = before
        self._set = dict(values)
        del self.set_point_log[rows:]

    def _take(self, setpoints: list[SetPoint]) -> None:
        for sp in setpoints:
            name, value = sp.point, sp.value
            if name in ("SetOp", "StrSetCon", "SetCon"):
                self._set["op"] = int(value)
                if name == "SetCon":
                    self._con_request = int(value)
            elif name in ("StrSetEna", "SetEna"):
                self._set["ena"] = int(value)
                if name == "SetEna":
                    self._ena_request = int(value)
            elif name == "SetInvState":
                self._set["inv"] = int(value)
            elif name == "SoCRsvMin":
                self._set["rsv_min"] = float(value)
            elif name == "SocRsvMax":
                self._set["rsv_max"] = float(value)
            elif name == "AlmRst":
                self._alarm_reset = int(value)
            elif name == "CtrlHb":
                self._control_heartbeat = int(value)
            elif name != "DA":
                raise ValueError(f"unknown set-point {name}")
            if self.keep_truth and name not in ("CtrlHb", "DA"):
                self.set_point_log.append((self._tb, sp.model, name, value))

    # --- the truth (never on the bus) ----------------------------------------------------------
    def _all_months(self) -> list[Month]:
        out = list(self._months)
        n = int(round((self._tb - self._acc.m0) / self.engine.dt))
        if self._month_index < self._plan.months and n > 0:
            month = self.engine._month(self._plan.start, self._acc)
            out.append(replace(month, **{name: getattr(month, name)[:n] for name in _STEP_FIELDS}))
        return out

    def need_truth(self) -> None:
        """Raise ``ValueError`` when this pack keeps no truth."""
        if not self.keep_truth:
            raise ValueError("this live pack keeps no truth (keep_truth=False)")

    def truth_tables(self) -> dict[str, dict[str, pd.DataFrame]]:
        """``{"YYYY-MM": {table: frame}}`` of the live stretch, in the format of ``besslib.sim.truth``."""
        self.need_truth()
        per_cell = None
        if self._data.get("case", {}).get("per_cell_truth"):
            per_cell = (self.pack.cells_in_series, self.pack.nominal_ah)
        return {m.start.strftime("%Y-%m"): tables(m, per_cell) for m in self._all_months()}

    def write_truth(self, out_dir) -> dict:
        """Write the live stretch's truth and a manifest to a folder; return the manifest."""
        self.need_truth()
        out = Path(out_dir)
        hashes = {}
        for label, frames in self.truth_tables().items():
            for name, table in frames.items():
                path = f"truth/{name}/{label}.parquet"
                hashes[path] = write_parquet(table, out / path)
        origin = self._plan.start
        manifest = {
            "mode": "live",
            "case": _jsonable(self._data),
            "seed": self.seed,
            "overrides": _jsonable(self.overrides),
            "events": _jsonable(self._plan.drawn),
            "besslib_version": besslib.__version__,
            "spec": _jsonable({k: v for k, v in asdict(self.spec).items() if k != "extra"}),
            "live_from": (origin + pd.Timedelta(seconds=self._live_from)).isoformat(),
            "live_to": self.time.isoformat(),
            "set_points": [
                {"time": (origin + pd.Timedelta(seconds=t)).isoformat(), "model": model, "point": name, "value": value}
                for t, model, name, value in self.set_point_log
            ],
            "files": dict(sorted(hashes.items())),
        }
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
        return manifest
