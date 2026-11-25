"""Shared by the case checks T1-T6: one simulated pack with its truth, and besslib 0.2 on its frames.

``Recorder`` runs one pack of a case, passes its frames on to besslib month by month, and
keeps what the checks need of the truth: every BMS stop with the running terminal charge,
the cell states at each month's end, and the month's sums of charge per cell group.

Two capacity truths (both from the simulator's own states, no sensor error):

* ``t_rest``: the charge between full and empty at two relaxed pack voltages (those the
  0.2 estimate reads at, ``ref_full_v`` and ``ref_empty_v``), computed on the cell states
  at the date: the charge moved through the string that takes the sum of the open-circuit
  voltages (with the hysteresis bound of the side, when the chemistry has one) from one
  voltage to the other. The estimate reads its voltages from a meter with a gain error, so
  the voltage of the truth is the meter's divided by one plus that gain: the state in
  which this pack's own meter reads that voltage.
* ``t_stop``: the charge between the BMS stops: the mean, over the full-to-empty and
  empty-to-full stretches whose middle lies in the ``window_days`` before the date, of the
  terminal charge between two stops of different kinds, with the BMS's supply current
  added (it leaves the cells without passing the terminals).

A month end is one the estimator reports (``estimate_capacity``'s rows). A month end
without a band counts as not held only where the check's own rule says so.
"""

from __future__ import annotations

import math
import platform

import numpy as np
import pandas as pd

from besslib import sim
from besslib.sim.cell import OcvCurve
from besslib.soh import _core
from besslib.soh.capacity import estimate_capacity
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.reduce import reduce_parts
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs

OK = _core.STATUS_TEXT[_core.OK]
PASS_RANGE = (0.85, 0.95)
HELD_OUT = range(1000, 1200)  # fixed with the plan, before any result
DEVELOPMENT = range(0, 200)
HASHES = {
    "capacity_config_hash": config_hash(CapacityConfig()),
    "record_config_hash": config_hash(RecordConfig()),
}


def tags() -> dict:
    """What every result row carries: the two configuration hashes and the processor."""
    return {**HASHES, "machine": platform.machine()}


class Recorder:
    """One pack of one case; ``frames()`` yields besslib frames and records the truth on the way.

    ``signals``: ``"measured"`` (the meter's) or ``"true"`` (no sensor error). ``on_month`` is
    called with every ``MonthOut`` for anything else a check needs.
    """

    def __init__(self, case, months: int, seed: int, overrides: dict | None = None, signals: str = "measured",
                 on_month=None) -> None:
        self.data = sim.load_case(case, overrides)
        self.months, self.seed, self.signals, self.on_month = int(months), int(seed), signals, on_month
        self.pack, self.settings = sim.build(self.data, seed)
        self.spec = sim.spec(self.data, self.pack)
        self.start = pd.Timestamp(self.settings.get("start", "2020-01-01"))
        self.ocv = OcvCurve(self.pack.cell.ocv_soc, self.pack.cell.ocv_v)
        self.voltage_gain = 0.0  # of the pack's meter; set when the run starts
        self._stops, self._ends, self._balance = [], [], []

    def frames(self):
        self._stops, self._ends, self._balance = [], [], []
        charge = 0.0  # Ah through the terminals since the start
        for out in sim.run(self.data, self.months, self.seed, measured=self.signals == "measured", log_blocks=False):
            t = out.truth
            dt = float(t.seconds[1] - t.seconds[0])
            running = np.concatenate([[0.0], np.cumsum(t.current_a) * dt / 3600.0]) + charge
            st = t.stops[t.stops["kind"].isin(["full", "empty"])]
            if len(st):
                k = np.round((st["seconds"].to_numpy(float) - t.seconds[0]) / dt).astype(int)
                self._stops.append(st.assign(q_ah=running[np.clip(k, 0, len(running) - 1)]))
            charge = float(running[-1])
            end = float(t.seconds[-1]) + dt
            self._ends.append((end, t.soc_end.copy(), self._capacity(end)))
            hours = len(t.current_a) * dt / 3600.0
            self._balance.append(t.balance.assign(t_start=t.start, hours=hours))
            if out.voltage_gain is not None:
                self.voltage_gain = float(out.voltage_gain)
            if self.on_month is not None:
                self.on_month(out)
            yield out.frame

    def _capacity(self, seconds: float) -> np.ndarray:
        p = self.pack
        fade = p._per_group(p.fade_points_per_year_groups, p.fade_points_per_year)
        return p.capacity0() * (1.0 - fade / 100.0 * seconds / (365.25 * 86400.0))

    # --- the truth ---------------------------------------------------------------------
    @property
    def stops(self) -> pd.DataFrame:
        """Every full and empty stop: ``time``, ``kind``, ``q_ah`` (running terminal charge), ``first``
        (False for a repeat of the same kind), and the engine's own columns."""
        if not self._stops:
            return pd.DataFrame(columns=["time", "seconds", "kind", "q_ah", "first"])
        s = pd.concat(self._stops, ignore_index=True)
        s["time"] = self.start + pd.to_timedelta(s["seconds"], unit="s")
        s["first"] = s["kind"] != s["kind"].shift()
        return s

    @property
    def balance(self) -> pd.DataFrame:
        return pd.concat(self._balance, ignore_index=True)

    def cells_at(self, time) -> tuple[np.ndarray, np.ndarray]:
        """The groups' states of charge and capacities at the month end nearest ``time``."""
        sec = (pd.Timestamp(time) - self.start).total_seconds()
        k = int(np.argmin([abs(e[0] - sec) for e in self._ends]))
        if abs(self._ends[k][0] - sec) > 1.5 * 86400.0:
            raise ValueError(f"no month end near {time}")
        return self._ends[k][1], self._ends[k][2]

    def true_volts(self, measured_v: float) -> float:
        """The true pack voltage at which this pack's meter reads ``measured_v``."""
        return measured_v / (1.0 + self.voltage_gain)

    def t_rest(self, time, full_v: float, empty_v: float) -> float:
        """Ah between two relaxed pack voltages, as the meter reads them, on the cell states at ``time``."""
        soc, cap = self.cells_at(time)
        return (self.charge_at(soc, cap, self.true_volts(full_v), +1.0)
                - self.charge_at(soc, cap, self.true_volts(empty_v), -1.0))

    def charge_at(self, soc, cap, volts: float, side: float) -> float:
        """The charge (Ah, from the present states) that brings the relaxed string to ``volts``."""
        p = self.pack

        def relaxed(q: float) -> float:
            s = soc + q / cap
            v = self.ocv(s)
            if p.cell.hyst_v > 0 or getattr(p.cell, "hyst_table", ()):
                v = v + side * p.cell.hyst_bound(s)
            return float(v.sum()) * p.series

        span = 2.0 * float(cap.max())
        lo, hi = -span, span
        if not (relaxed(lo) <= volts <= relaxed(hi)):
            return math.nan
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if relaxed(mid) < volts:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-9:
                break
        return 0.5 * (lo + hi)

    def stop_charges(self) -> pd.DataFrame:
        """The charge between consecutive stops of different kinds: ``time`` (middle), ``ah``."""
        s = self.stops
        s = s[s["first"]] if len(s) else s
        if len(s) < 2:
            return pd.DataFrame(columns=["time", "ah"])
        sec, q = s["seconds"].to_numpy(float), s["q_ah"].to_numpy(float)
        ah = np.abs(np.diff(q) - self.pack.bms.supply_a * np.diff(sec) / 3600.0)
        mid = self.start + pd.to_timedelta(0.5 * (sec[1:] + sec[:-1]), unit="s")
        return pd.DataFrame({"time": mid, "ah": ah})

    def t_stop(self, time, window_days: float = 60.0) -> float:
        c = self.stop_charges()
        if not len(c):
            return math.nan
        age = (pd.Timestamp(time) - c["time"]).dt.total_seconds() / 86400.0
        use = (age >= 0) & (age <= window_days)
        return float(c.loc[use, "ah"].mean()) if use.any() else math.nan


def reduce_pack(rec: Recorder):
    """besslib's reduced tables of one pack (the record is kept by ``rec``)."""
    return reduce_parts(rec.frames(), rec.spec)


def capacity_rows(reduced, spec, mask=None, config: CapacityConfig | None = None) -> pd.DataFrame:
    """besslib 0.2's monthly capacity estimates from reduced tables (``mask`` as in ``operating_rests``)."""
    config = config or CapacityConfig()
    rests = operating_rests(reduced.rests, spec, config, **({"mask": mask} if mask is not None else {}))
    pairs = rest_pairs(rest_chain(rests, spec, config, **({"mask": mask} if mask is not None else {})), config)
    return estimate_capacity(None, spec, config=config, pairs=pairs, levels_from=levels_from(rests, config))


def has_band(row) -> bool:
    return bool(row["status"] == OK and np.isfinite(row["capacity_ah_low"]) and np.isfinite(row["capacity_ah_high"]))


def share(held: int, n: int) -> float:
    return held / n if n else math.nan


def coverage_row(check: str, what: str, held: int, n: int, errors, unit: str, extra: str = "") -> dict:
    """A check row of the form "the band holds the truth in 85-95 % and the mean error is within 2 sd / sqrt(n)".

    ``errors``: one mean error per pack (NaN for a pack without a band)."""
    e = np.asarray(errors, dtype=float)
    e = e[np.isfinite(e)]
    s = share(held, n)
    mean = float(e.mean()) if len(e) else math.nan
    limit = 2.0 * float(e.std(ddof=1)) / math.sqrt(len(e)) if len(e) > 1 else math.nan
    ok = bool(n and PASS_RANGE[0] <= s <= PASS_RANGE[1] and abs(mean) <= limit)
    return {
        "check": check, "what": what,
        "result": f"{held} of {n} ({s:.1%}); mean error {mean:+.3f} {unit}, limit +-{limit:.3f} ({len(e)} packs)"
                  + (f"; {extra}" if extra else "") if n else "no month end with a band",
        "pass_if": "85-95 % and mean error within 2 sd / sqrt(n) of zero", "passed": ok,
    }
