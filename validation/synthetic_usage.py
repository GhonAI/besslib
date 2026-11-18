"""Check W8 on one synthetic pack: throughput, cycles and planted events.

Called by ``synthetic_phase3.py`` with one simulated seed (``check(run) -> dict``). It
reads pack B (``run.reduced_plain``: the measured signals, current gain error within
0.5 %, no voltage gain error) and counts with ``usage.count_usage``.

Truth. Charge in and out: the sums of ``max(+-I, 0) x duration`` over the simulator's own
current program (``run.segments``), in Ah. Energy in and out: the sums of the running
daily energy of the pack's own signals (``run.reduced_true``: no gain error, no noise).
Cycles: the true charge in plus out over twice the reference capacity.

Planted events (``run.pack_plain.planted_temps`` and ``planted_deep_days``). Hours at or
above 45 degrees C and at or below 0 degrees C: the sum of the lengths of the planted
intervals at those temperatures. Hours charging inside them: the seconds of the true
current program with a current at or above the idle threshold of the rest detector
(``CapacityConfig.current_c_rate`` x nominal capacity; a smaller current is idle, not
charging) that fall inside such an interval. Deep discharges: one per planted deep day,
counted against the limit ``3.2 V x cells in series`` (the BMS stop of an ordinary
discharge is at 3.3 V per cell, a planted one runs down to 3.1 V).

Events are exact when every hours value is within one sample step per interval edge of
its truth and the counts are equal. Pass: the relative error of Ah, kWh and cycles is at
most 0.006 and the events are exact.
"""

from __future__ import annotations

import numpy as np

from besslib.soh.config import CapacityConfig
from besslib.soh.usage import Limits, count_usage

TEMP_HIGH_C = 45.0
TEMP_LOW_C = 0.0
LIMIT_REL = 0.006


def _rel(value: float, truth: float) -> float:
    return float((value - truth) / truth) if truth else float("nan")


def _overlap_s(start: np.ndarray, length: np.ndarray, begin: float, end: float) -> float:
    """Seconds of the segments [start, start + length) inside [begin, end)."""
    lo = np.maximum(start, begin)
    hi = np.minimum(start + length, end)
    return float(np.maximum(hi - lo, 0.0).sum())


def check(run) -> dict:
    spec, pack = run.spec, run.pack_plain
    limits = Limits(temp_low_c=TEMP_LOW_C, temp_high_c=TEMP_HIGH_C, charge_temp_low_c=TEMP_LOW_C,
                    charge_temp_high_c=TEMP_HIGH_C, voltage_min_v=3.2 * spec.cells_in_series)
    usage = count_usage(None, spec, limits, reduced=run.reduced_plain)

    seg = run.segments
    start = seg["seconds"].to_numpy(dtype=float)
    length = seg["duration_s"].to_numpy(dtype=float)
    current = seg["current_a"].to_numpy(dtype=float)
    truth_ah_in = float((np.maximum(current, 0.0) * length).sum()) / 3600.0
    truth_ah_out = float((np.maximum(-current, 0.0) * length).sum()) / 3600.0
    truth_kwh_in = float(run.reduced_true.daily["wh_in"].sum()) / 1000.0
    truth_kwh_out = float(run.reduced_true.daily["wh_out"].sum()) / 1000.0
    truth_cycles = (truth_ah_in + truth_ah_out) / (2.0 * spec.reference_capacity_ah)

    idle_a = CapacityConfig().current_c_rate * spec.nominal_capacity_ah
    charging = current >= idle_a
    hot = [(d * 86400.0 + h * 3600.0, n * 3600.0) for d, h, n, c in pack.planted_temps if c >= TEMP_HIGH_C]
    cold = [(d * 86400.0 + h * 3600.0, n * 3600.0) for d, h, n, c in pack.planted_temps if c <= TEMP_LOW_C]
    truth = {
        "hours_above_temp_high": sum(n for _, n in hot) / 3600.0,
        "hours_below_temp_low": sum(n for _, n in cold) / 3600.0,
        "hours_charging_above_temp_high": sum(_overlap_s(start[charging], length[charging], b, b + n) for b, n in hot)
        / 3600.0,
        "hours_charging_below_temp_low": sum(_overlap_s(start[charging], length[charging], b, b + n) for b, n in cold)
        / 3600.0,
    }
    tolerance_h = {
        "hours_above_temp_high": 2 * len(hot) * run.step_s / 3600.0,
        "hours_below_temp_low": 2 * len(cold) * run.step_s / 3600.0,
        # inside the intervals the charging seconds also start and stop at segment edges: one step per edge
        "hours_charging_above_temp_high": 2 * len(hot) * run.step_s / 3600.0,
        "hours_charging_below_temp_low": 2 * len(cold) * run.step_s / 3600.0,
    }
    truth_deep = len(pack.planted_deep_days)

    out: dict = {}
    for name, value, true in (
        ("ah_in", usage.charge_in_ah, truth_ah_in),
        ("ah_out", usage.charge_out_ah, truth_ah_out),
        ("kwh_in", usage.energy_in_kwh, truth_kwh_in),
        ("kwh_out", usage.energy_out_kwh, truth_kwh_out),
        ("cycles", usage.full_equivalent_cycles, truth_cycles),
    ):
        out[f"w8_{name}"] = value
        out[f"w8_{name}_truth"] = true
        out[f"w8_{name}_rel_error"] = _rel(value, true)
    exact = True
    for name, true in truth.items():
        value = getattr(usage, name)
        out[f"w8_{name}"] = value
        out[f"w8_{name}_truth"] = true
        out[f"w8_{name}_diff_h"] = value - true
        exact &= value is not None and abs(value - true) <= tolerance_h[name] + 1e-9
    out["w8_n_deep_discharge"] = usage.n_deep_discharge
    out["w8_n_deep_discharge_truth"] = truth_deep
    exact &= usage.n_deep_discharge == truth_deep
    out["w8_n_hot_intervals"] = len(hot)
    out["w8_n_cold_intervals"] = len(cold)
    out["w8_events_exact"] = bool(exact)
    errors = [out[f"w8_{n}_rel_error"] for n in ("ah_in", "ah_out", "kwh_in", "kwh_out", "cycles")]
    out["w8_pass"] = bool(all(abs(e) <= LIMIT_REL for e in errors) and exact)
    return out
