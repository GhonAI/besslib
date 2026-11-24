"""The simulator's own checks of the plant and the BMS: S1 (charge), S2 (energy), S4 (BMS).

Each function takes the months of one run (``besslib.sim.run`` yields ``MonthOut``
objects; ``Engine.run`` yields ``Month`` objects; both are accepted) and returns a dict
with the worst value, the pass number and whether it passed. The pass numbers are those of
the A2 plan, fixed before any result: S1 1e-9 relative, S2 1e-6 relative, S4 zero.

S1, charge, per group and month. The engine's ``cap_dsoc_ah`` (capacity times the change
of state of charge, summed over its blocks) against the terminal current of the per-step
truth times the step, less the drains (supply, self-discharge, leak, bleed). Denominator:
the group's charge throughput in the month, the sum of |terminal current| times the step
plus the drains' charge.

S2, energy, per group and month. Terminal energy (the engine's exact block integrals) =
change of stored energy + polarization heat + drain losses. The stored energy comes from
an independent path when the capacity is constant in the month: the true state of charge
at the month's start and end, and this file's own integral of the open-circuit curve
(trapezoids on the merged breakpoints, exact for a curve linear between points and flat
beyond them). When the capacity changes within the month, the engine's per-block stored
energy is used and the result says so. Denominator: the month's terminal energy
throughput from the per-step truth (the sum of |current x voltage| times the step) plus the
group's drain losses, so a month of rest is judged against its drains.

S4, BMS. (a) No sample with current flowing beyond the limit that applies: while charging
the checked voltage stays below the upper limit, while discharging above the lower. The
checked voltage is the highest or lowest group's mean cell voltage (rule "cells"), or the
pack voltage per cell (rules "pack" and "synth"; the "synth" BMS checks its own copy of the
pack, not the plant, so for it the count is information). (b) Every stop lies in the step
where its voltage crossed: ``v_before`` on the safe side, ``v_after`` beyond, and the step
before the stop carries current in the stop's direction. A stop at a block start (no
``v_before``) is allowed when the current is first applied there, or the current, the
limit or the day changed there (the stop's ``current_a`` is the current asked), or it lies on
the engine's update grid, or a stop of the same kind closed the step ending at that time (the
next piece is refused at once).
"""

from __future__ import annotations

import numpy as np

S1_PASS = 1e-9
S2_PASS = 1e-6
S4_PASS = 0


def _truth(m):
    return getattr(m, "truth", m)


def _step(t) -> float:
    return float(t.seconds[1] - t.seconds[0])


def ocv_integral(soc_pts, v_pts, a: float, b: float) -> float:
    """The integral of the open-circuit curve (per cell, V) over state of charge from ``a``
    to ``b``: linear between the points, flat beyond them. Trapezoids on the merged grid."""
    soc_pts = np.asarray(soc_pts, dtype=float)
    lo, hi = min(a, b), max(a, b)
    grid = np.unique(np.r_[lo, hi, soc_pts[(soc_pts > lo) & (soc_pts < hi)]])
    v = np.interp(grid, soc_pts, np.asarray(v_pts, dtype=float))
    total = float(np.sum(np.diff(grid) * 0.5 * (v[1:] + v[:-1])))
    return total if b >= a else -total


def s1_charge(months) -> dict:
    worst, where, n = 0.0, None, 0
    for mi, m in enumerate(months):
        t = _truth(m)
        dt = _step(t)
        bal = t.balance
        terminal_ah = float(np.sum(t.current_a)) * dt / 3600.0
        through_ah = float(np.sum(np.abs(t.current_a))) * dt / 3600.0
        drains = (bal["supply_ah"] + bal["self_ah"] + bal["leak_ah"] + bal["bleed_ah"]).to_numpy()
        expected = terminal_ah - drains
        den = np.maximum(through_ah + np.abs(drains), 1e-12)
        rel = np.abs(bal["cap_dsoc_ah"].to_numpy() - expected) / den
        n += len(rel)
        k = int(np.argmax(rel))
        if rel[k] > worst or where is None:
            worst, where = max(worst, float(rel[k])), (mi, k)
    return dict(check="S1", worst=worst, pass_at=S1_PASS, passed=bool(worst <= S1_PASS), n=n, where=where,
                unit="relative to the group's charge throughput")


def s2_energy(months, pack) -> dict:
    """``pack``: the run's ``PackModel`` (its curve, cells in series per group, capacity)."""
    worst, where, n, paths = 0.0, None, 0, set()
    soc_pts, v_pts = pack.cell.ocv_soc, pack.cell.ocv_v
    for mi, m in enumerate(months):
        t = _truth(m)
        dt = _step(t)
        bal = t.balance
        through_wh = float(np.sum(np.abs(t.current_a * t.voltage_v))) * dt / 3600.0
        cap = t.cells.groupby("group")["capacity_ah"] if len(t.cells) else None
        drain_wh = (bal["supply_wh"] + bal["self_wh"] + bal["leak_wh"] + bal["bleed_wh"]).to_numpy()
        for g in range(len(bal)):
            constant = (cap is not None and t.soc_start is not None and pack.fade_points_per_year == 0.0
                        and not pack.fade_points_per_year_groups)
            if constant:
                c = float(cap.get_group(g).iloc[0])
                stored = c * pack.series * ocv_integral(soc_pts, v_pts, float(t.soc_start[g]), float(t.soc_end[g]))
                paths.add("stored energy from the month-end states of charge")
            else:
                stored = float(bal["stored_wh"].iloc[g])
                paths.add("stored energy from the engine's block sums (capacity not constant)")
            resid = float(bal["terminal_wh"].iloc[g]) - stored - float(bal["heat_wh"].iloc[g]) - float(drain_wh[g])
            rel = abs(resid) / max(through_wh + abs(float(drain_wh[g])), 1e-12)
            n += 1
            if rel > worst or where is None:
                worst, where = max(worst, rel), (mi, g)
    return dict(check="S2", worst=worst, pass_at=S2_PASS, passed=bool(worst <= S2_PASS), n=n, where=where,
                path="; ".join(sorted(paths)),
                unit="relative to the month's terminal energy throughput plus drain losses")


def s4_bms(months, pack, update_s: float | None = None) -> dict:
    """``pack``: the run's ``PackModel``; ``update_s``: the engine's update interval, if any."""
    n_series = pack.cells_in_series
    bad_samples = bad_stops = n_samples = n_stops = n_start = 0
    rule = ""
    first_bad = None
    for mi, m in enumerate(months):
        t = _truth(m)
        dt = _step(t)
        rule = t.stop_rule
        cur = t.current_a
        if rule == "cells":
            v_hi, v_lo = t.v_cell_max, t.v_cell_min
        else:
            v_hi = v_lo = t.voltage_v / n_series
        over = ((cur > 0) & (v_hi >= t.limit_max_v)) | ((cur < 0) & (v_lo <= t.limit_min_v))
        n_samples += len(cur)
        if over.any():
            bad_samples += int(over.sum())
            first_bad = first_bad or ("sample", mi, float(t.seconds[int(np.argmax(over))]))
        lim = np.where(np.isfinite(t.limit_max_v), t.limit_max_v, 0.0)
        for s in t.stops.itertuples():
            if s.kind not in ("full", "empty"):
                continue
            n_stops += 1
            full = s.kind == "full"
            k = int(round((s.seconds - t.seconds[0]) / dt))  # the sample at the stop
            if np.isfinite(s.v_before):
                ok = (s.v_before < s.limit_v <= s.v_after) if full else (s.v_after <= s.limit_v < s.v_before)
                if k >= 1:
                    ok = ok and (cur[k - 1] > 0 if full else cur[k - 1] < 0)
            else:
                n_start += 1
                if k < 1:
                    ok = True  # at the month's start: the step before lies in the month before
                else:
                    prev = cur[k - 1]
                    first = not (prev > 0 if full else prev < 0)
                    changed = (k < len(cur) and (lim[k] != lim[k - 1]
                                                 or t.limit_min_v[k] != t.limit_min_v[k - 1]))
                    day = s.seconds % 86400.0 == 0.0
                    grid = bool(update_s) and abs(s.seconds / update_s - round(s.seconds / update_s)) < 1e-9
                    # A new piece refused at the very time of a stop of the same kind: the crossing
                    # lies in the step that the earlier stop closed.
                    repeat = bool(((t.stops["kind"] == s.kind) & (t.stops["seconds"] == s.seconds)
                                   & np.isfinite(t.stops["v_before"])).any())
                    ok = first or prev != s.current_a or changed or day or grid or repeat
            if not ok:
                bad_stops += 1
                first_bad = first_bad or ("stop", mi, float(s.seconds))
    worst = bad_samples + bad_stops
    passed = worst <= S4_PASS if rule != "synth" else bad_stops == 0
    return dict(check="S4", worst=worst, pass_at=S4_PASS, passed=bool(passed), bad_samples=bad_samples,
                bad_stops=bad_stops, n_samples=n_samples, n_stops=n_stops, n_stops_at_block_start=n_start,
                rule=rule, first_bad=first_bad)
