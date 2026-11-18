"""Checks W5 and W7 on one synthetic pack: round-trip efficiency and apparent charge loss.

Called by ``synthetic_phase3.py`` with one simulated seed (``check(run) -> dict``). Both
checks read pack A (``run.reduced``: current gain error within 0.5 %, voltage gain error
within 0.25 %, sensor noise) and the frozen settings.

Check W5, round-trip efficiency. The estimate is ``efficiency.estimate_efficiency`` at
the last sample of the record, from full-to-full loops between rests that the library
detected and classified on the measured signals, with capacity-test-free operation (no
mask). The truth is the simulator's own energy out divided by its energy in over its own
full-to-full loops, without sensor errors: the loops run between consecutive BMS stops at
the upper voltage limit (``run.stops``, the first stop of each run of equal kinds, so a
top-up after a stop does not start a new loop), and their energies are the differences of
the running energy counters of the pack's own signals (``run.reduced_true``: no gain
error, no noise) at those stops. A BMS stop is where the current goes to zero, so in the
pack's own signals a rest candidate starts at its time; a stop is used when a rest
candidate of ``run.reduced_true`` starts within one sample of it. A stop without one
(the rest after it is shorter than the minimum rest duration, because the charge
resumes) is dropped: the loop then runs from the stop before it to the stop after it,
which is still a loop from the upper limit back to it. Their count is reported
(``w5_true_full_stops``, ``w5_true_stops_matched``). The truth is weighted as the
estimate: ``exp(-age / tau_days)`` at the same time, over the loops that end by then.

The check is not circular: the estimate rests on rests found by the library's current
filter and labelled by its edge-voltage levels on measured, noisy, gain-distorted
signals, and on its own choice of loops; the truth uses neither the classifier nor the
measured signals, only the simulator's stops and its own current and voltage. The hidden
drain is in neither signal, but it is in both values the same way: the energy in of a
loop also restores the charge the drain took.

The band is the loop band (``besslib.soh._core.loop_solve``): it holds the ratio over
the estimate's own loops had each closed exactly, with its width from the error of the
energy stored at their end rests, estimated as the mean lag-2 product of the loop scores
minus the mean lag-1 product (a slow drift of the loops' own efficiency cancels in the
difference; one that changes within two loops does not). Below one degree of freedom
there is no band. The truth runs over the simulator's loops, which are close to but not
the same loops.
Reported with it: the band's degrees of freedom (``w5_dof``) and the rest error
(``w5_rest_error_wh``, one standard deviation).

Check W7, apparent charge loss. The truth is the pack's hidden drain
(``pack.hidden_current_a``, -0.05 A, so 50 mA of loss). The estimate is
``charge_loss.charge_loss`` at the last month end of the record with status
``estimated``, from ``capacity.estimate_capacity`` on the rests of ``run.reduced``
(``rests.operating_rests``, ``rest_chain``, ``rest_pairs``; frozen ``CapacityConfig()``).

Both: ``*_held`` is True when the 90 % band holds the truth; a pack without a band is
not held.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from besslib.soh import _core
from besslib.soh.capacity import days, estimate_capacity
from besslib.soh.charge_loss import charge_loss
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.efficiency import estimate_efficiency
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs

OK = _core.STATUS_TEXT[_core.OK]


def last_time(reduced) -> pd.Timestamp:
    """The last sample time of a reduced record."""
    return pd.Timestamp(reduced.daily["t_last"].dropna().max())


def true_loops(run) -> tuple[pd.DataFrame, int, int]:
    """The simulator's full-to-full loops with their energies from the pack's own signals.

    Returns the loops (``t_start``, ``t_end``, ``energy_in_wh``, ``energy_out_wh``), the
    number of first full stops and how many of them start a rest candidate of
    ``run.reduced_true`` within one sample.
    """
    stops = run.stops[run.stops["first"] & (run.stops["kind"] == "full")].sort_values("time")
    rests = run.reduced_true.rests.sort_values("start")
    found = pd.merge_asof(
        stops[["time"]].astype({"time": "datetime64[ns]"}),
        rests[["start", "energy_in_wh", "energy_out_wh"]].astype({"start": "datetime64[ns]"}),
        left_on="time", right_on="start", direction="nearest", tolerance=pd.Timedelta(seconds=run.step_s),
    )
    found = found.dropna(subset=["start"]).reset_index(drop=True)
    loops = pd.DataFrame(
        {
            "t_start": found["time"].iloc[:-1].to_numpy(),
            "t_end": found["time"].iloc[1:].to_numpy(),
            "energy_in_wh": np.diff(found["energy_in_wh"].to_numpy(float)),
            "energy_out_wh": np.diff(found["energy_out_wh"].to_numpy(float)),
        }
    )
    return loops, int(len(stops)), int(len(found))


def check_w5(run, config: RecordConfig, capacity_config: CapacityConfig) -> dict:
    at = last_time(run.reduced)
    out = {}
    for kind, prefix in (("full", "w5_"), ("empty", "w5_empty_")):
        est = estimate_efficiency(
            None, run.spec, reduced=run.reduced, config=config, capacity_config=capacity_config, at=[at], kind=kind
        ).iloc[0]
        out.update(
            {
                f"{prefix}value": est["round_trip_efficiency"],
                f"{prefix}low": est["round_trip_efficiency_low"],
                f"{prefix}high": est["round_trip_efficiency_high"],
                f"{prefix}sigma": est["round_trip_efficiency_sigma"],
                f"{prefix}n_loops": est["n_loops"],
                f"{prefix}dof": est["dof"],
                f"{prefix}rho": est["rho"],
                f"{prefix}rest_error_wh": est["rest_error_wh"],
                f"{prefix}status": est["status"],
            }
        )
        if kind == "full":
            out.update(w5_coulombic=est["coulombic_efficiency"], w5_voltage=est["voltage_efficiency"])
    loops, n_stops, n_matched = true_loops(run)
    t = days(loops["t_end"])
    tq = float(days([at])[0])
    use = t <= tq
    lam = np.exp(-(tq - t[use]) / config.tau_days)
    truth = float((lam * loops["energy_out_wh"].to_numpy(float)[use]).sum() / (lam * loops["energy_in_wh"].to_numpy(float)[use]).sum())
    held = bool(out["w5_status"] == OK and out["w5_low"] <= truth <= out["w5_high"])
    out.update(
        w5_truth=truth,
        w5_held=held,
        w5_error=out["w5_value"] - truth,
        w5_true_loops=int(use.sum()),
        w5_true_full_stops=n_stops,
        w5_true_stops_matched=n_matched,
        w5_time=str(at),
    )
    return out


def check_w7(run, config: RecordConfig, capacity_config: CapacityConfig) -> dict:
    rests = operating_rests(run.reduced.rests, run.spec, capacity_config)
    pairs = rest_pairs(rest_chain(rests, run.spec, capacity_config), capacity_config)
    cap = estimate_capacity(None, run.spec, config=capacity_config, pairs=pairs, levels_from=levels_from(rests, capacity_config))
    loss = charge_loss(cap, run.spec, level=config.level, config=config)
    ok = loss[loss["status"] == OK]
    truth = -1000.0 * run.pack.hidden_current_a
    if len(ok) == 0:
        last = loss.iloc[-1] if len(loss) else None
        return {
            "w7_value_ma": math.nan, "w7_low_ma": math.nan, "w7_high_ma": math.nan, "w7_sigma_ma": math.nan,
            "w7_truth_ma": truth, "w7_held": False, "w7_error_ma": math.nan, "w7_n_pairs": math.nan,
            "w7_status": last["status"] if last is not None else "not possible from this data: no month end",
            "w7_time": "",
        }
    row = ok.iloc[-1]
    return {
        "w7_value_ma": row["charge_loss_ma"],
        "w7_low_ma": row["charge_loss_ma_low"],
        "w7_high_ma": row["charge_loss_ma_high"],
        "w7_sigma_ma": row["charge_loss_sigma_ma"],
        "w7_truth_ma": truth,
        "w7_held": bool(row["charge_loss_ma_low"] <= truth <= row["charge_loss_ma_high"]),
        "w7_error_ma": row["charge_loss_ma"] - truth,
        "w7_n_pairs": row["n_pairs"],
        "w7_status": row["status"],
        "w7_time": str(ok.index[-1]),
    }


def check(run) -> dict:
    config = RecordConfig()
    capacity_config = CapacityConfig()
    out = check_w5(run, config, capacity_config)
    out.update(check_w7(run, config, capacity_config))
    return out
