"""Round-trip efficiency over closed loops between rests at the same state of charge.

Annex VII of Regulation (EU) 2023/1542 asks, "where possible", for "the remaining round
trip efficiency"; Annex IV (6) defines it by the energy delivered in a discharge against
the energy needed to restore the initial state of charge. Operating data hold no planned
cycle, so besslib closes the cycle between two rests at the same edge of the charge: a
*loop* runs from one ``full`` rest to the next ``full`` rest of the same linked run of the
rest chain (``rests.rest_chain``; a loop never crosses a break of the chain). Its energy
in and out are the differences of the running energy counters at the two rests. The
estimate is ``eta = sum(lambda * energy out) / sum(lambda * energy in)`` over the loops in
memory, with ``lambda = exp(-age / tau_days)``.

Band: the loop band of ``besslib.soh._core.loop_solve``. A loop does not close exactly:
the energy stored at a full rest differs a little from one rest to the next, because a
voltage stop does not land at exactly the same state of charge. That error of rest ``j``
enters the loop that ends there with one sign and the linked loop that starts there with
the other. The band holds the ratio over the loops in memory had each of them closed
exactly (the loops' own variation from one to the next is part of that value, not of its
error), and its width comes from the rests' errors only: ``sigma = sqrt(s2 C2) / A``,
where ``A`` is the weighted energy in, ``C2`` the sum of the rests' squared weights in
the estimate (the first and the last rest of each linked run without forgetting; the
latest rest, mostly, with forgetting), and ``s2`` the variance of a rest's error. With
the scores ``energy out - eta * energy in``, ``s2`` is the mean product of the scores of
two loops with one loop between them (loops ``k`` and ``k - 2`` of three in a row, which share no rest)
minus the mean product of two linked loops (which share one): a slow drift of the loops'
own efficiency adds to both and cancels in the difference. Its degrees of freedom are
those of that difference (``_core``); below one there is no band. No
systematic sensor term is added: a gain error of the current or the voltage sensor
scales energy in and energy out by the same factor and cancels in their ratio. A zero
offset of the current sensor does not cancel; it is not in the band.

Reported next to it: the *coulombic part* ``sum(lambda * Ah out) / sum(lambda * Ah in)``
over the same loops with the same weights, and the *voltage part* ``eta`` divided by
it. ``kind="empty"`` gives the same from loops between ``empty`` rests, a cross-check
with other loops of the same record.

Limits. It is the DC efficiency at the battery terminals, not the AC efficiency of the
plant with its inverter. It depends on the use (current, depth and time at rest;
Schimpe et al., Applied Energy 210, 211 (2018), doi:10.1016/j.apenergy.2017.10.129), so it
describes this battery under its own operation over the loops in memory, not a rated
value and not the efficiency under other use. The energy in also restores the charge the
meter does not see (BMS supply, balancing, self-discharge), which lowers the value. The
band takes the rests' errors as independent and of one size; they are not quite, since
the state of charge at a voltage stop depends on the current before it. A change of the
loops' own efficiency that is fast against two loops is not cancelled: when it makes
neighbouring loops more alike than loops two apart, the rest error and the band come out
too small. The difference of two means is noisier than one mean, so the band rests on
fewer degrees of freedom than the number of loops suggests, and more records have none.
The degrees of freedom assume normal scores and no correlation of the scores beyond two
loops. With forgetting the latest rest
carries most of the band, so the band rests on the error of one rest whose size is
learnt from the others. A zero offset of the current sensor is not in the band. No full
or empty rest exists before the levels are set (``CapacityConfig.level_warmup_days``), so
the first year of a record has no value.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core, _ratio
from besslib.soh.capacity import days
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.rests import detect_rests, levels_from, operating_rests, rest_chain

#: Columns of the loop table.
LOOP_COLUMNS = ["t_start", "t_end", "energy_in_wh", "energy_out_wh", "ah_in", "ah_out", "linked"]

#: Columns of the estimate table.
EFFICIENCY_COLUMNS = [
    "round_trip_efficiency", "round_trip_efficiency_low", "round_trip_efficiency_high", "round_trip_efficiency_sigma",
    "coulombic_efficiency", "voltage_efficiency", "n_loops", "dof", "rho", "rest_error_wh", "status",
]

TOO_FEW_LOOPS = "not possible from this data: too few closed loops"
OUT_EXCEEDS_IN = "not possible from this data: energy out exceeds energy in over closed loops"
NO_ENERGY_OUT = "not possible from this data: no energy out over closed loops"
NO_ENERGY_IN = "not possible from this data: no energy in over closed loops"
NO_SHARED_REST = (
    "not possible from this data: no three loops in a row share their rests, so the rest error cannot be told from "
    "a slow drift"
)
NO_REST_ERROR = (
    "not possible from this data: the loop scores show no state-of-charge error at the shared rests (their lag-1 "
    "products are not below their lag-2 products)"
)
REST_ERROR_UNKNOWN = "not possible from this data: the rest error is not known well enough"


def efficiency_loops(rests: pd.DataFrame, chain: pd.DataFrame, kind: str = "full") -> pd.DataFrame:
    """The closed loops from one ``kind`` rest to the next one in the same run of ``chain``.

    ``rests`` is the rest table the chain was made from (it carries the running counters
    ``energy_in_wh`` and ``energy_out_wh``, joined to the chain by the rest's start
    time); ``chain`` the output of ``rests.rest_chain``. A loop never crosses a row with
    ``new_segment``. ``ah_in`` and ``ah_out`` are (throughput +/- net charge) / 2 between
    the rests. ``linked`` is 1 when the loop starts at the rest where the previous loop
    ended.
    """
    if kind not in ("full", "empty"):
        raise ValueError("kind must be 'full' or 'empty'")
    missing = [c for c in ("start", "energy_in_wh", "energy_out_wh") if c not in rests.columns]
    if missing:
        raise ValueError(f"the rest table has no column {', '.join(missing)}")
    energy = rests[["start", "energy_in_wh", "energy_out_wh"]].drop_duplicates("start").set_index("start")
    joined = energy.reindex(pd.DatetimeIndex(chain["time"]))
    if len(chain) and joined.isna().any().any():
        raise ValueError("a rest of the chain has no energy counters in the rest table")
    e_in = joined["energy_in_wh"].to_numpy(float)
    e_out = joined["energy_out_wh"].to_numpy(float)
    seg = np.cumsum(chain["new_segment"].to_numpy(int))
    idx = np.flatnonzero((chain["kind"] == kind).to_numpy())
    a, b = idx[:-1], idx[1:]
    keep = seg[a] == seg[b]  # no break of the chain at or before the loop's end
    linked = np.r_[False, keep[:-1]] & keep  # the loop before it ended at its start rest
    a, b, linked = a[keep], b[keep], linked[keep]
    time = pd.DatetimeIndex(chain["time"])
    q = chain["q_ah"].to_numpy(float)
    thr = chain["throughput_ah"].to_numpy(float)
    dq, dthr = q[b] - q[a], thr[b] - thr[a]
    return pd.DataFrame(
        {
            "t_start": time[a],
            "t_end": time[b],
            "energy_in_wh": e_in[b] - e_in[a],
            "energy_out_wh": e_out[b] - e_out[a],
            "ah_in": 0.5 * (dthr + dq),
            "ah_out": 0.5 * (dthr - dq),
            "linked": linked.astype(int),
        },
        columns=LOOP_COLUMNS,
    )


def _decayed_ratio(t: np.ndarray, num: np.ndarray, den: np.ndarray, at: float, tau_days: float) -> float:
    use = t <= at
    lam = np.exp(-(at - t[use]) / tau_days) if tau_days > 0 else np.ones(int(use.sum()))
    d = float((lam * den[use]).sum())
    return float((lam * num[use]).sum()) / d if d > 0 else math.nan


def estimate_efficiency(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    rests: pd.DataFrame | None = None,
    level: float = 0.90,
    *,
    config: RecordConfig | None = None,
    capacity_config: CapacityConfig | None = None,
    reduced=None,
    at=None,
    mask=None,
    kind: str = "full",
) -> pd.DataFrame:
    """Round-trip efficiency with its band at each time in ``at`` (module docstring).

    Parameters
    ----------
    frame
        Operating data in the besslib frame format; may be ``None`` when ``rests`` or
        ``reduced`` is given.
    rests
        Rest candidates with the energy counters (``reduce.Reduced.rests`` or
        ``rests.detect_rests``), or rests already labelled by ``rests.operating_rests``
        (a table with a ``kind`` column).
    level
        Band level.
    config, capacity_config
        Settings of the record and of the rest detection; defaults are the frozen ones.
    reduced
        A ``reduce.Reduced``; its rest table is used when ``rests`` is not given.
    at
        Times of the estimate. Default: the end of every calendar month in the data.
    mask
        Intervals ``(start, end)`` whose rests are left out; no loop spans one.
    kind
        ``"full"`` (loops from full to full) or ``"empty"`` (the cross-check).

    Returns
    -------
    DataFrame indexed by time: ``round_trip_efficiency`` with ``_low``, ``_high`` and
    ``_sigma`` (its standard error), ``coulombic_efficiency``, ``voltage_efficiency``,
    ``n_loops`` (loops in memory, decayed), ``dof``, ``rho`` (lag-1 correlation of the
    scores, a diagnostic), ``rest_error_wh`` (the standard deviation of the energy
    stored at a rest, from which the band is formed), ``status``. A value is given only
    when the status is ``estimated``; it is never above 1 or at or below 0. ``attrs`` hold the loops and
    both configuration hashes.
    """
    config = config or RecordConfig()
    capacity_config = capacity_config or CapacityConfig()
    if rests is None:
        if reduced is not None:
            rests = reduced.rests
        elif frame is not None:
            rests = detect_rests(frame, spec, capacity_config)
        else:
            raise ValueError("give a frame, rests or reduced tables")
    if "kind" not in rests.columns:
        rests = operating_rests(rests, spec, capacity_config, mask=mask)
    chain = rest_chain(rests, spec, capacity_config, mask=mask)
    loops = efficiency_loops(rests, chain, kind)
    ready = levels_from(rests, capacity_config)
    ready_days = float(days([ready])[0]) if pd.notna(ready) else math.inf
    if at is None:
        times = loops["t_end"] if len(loops) else rests["start"] if len(rests) else frame.index if frame is not None else []
        if len(times) == 0:
            raise ValueError("no data to estimate from")
        at = pd.date_range(pd.Timestamp(min(times)).normalize(), pd.Timestamp(max(times)), freq="ME")
    at = pd.DatetimeIndex(at)
    at_days = days(at)
    t = days(loops["t_end"])
    a = loops["energy_in_wh"].to_numpy(float)
    b = loops["energy_out_wh"].to_numpy(float)
    ah_in = loops["ah_in"].to_numpy(float)
    ah_out = loops["ah_out"].to_numpy(float)
    # The loops the core accepts (energy in > 0, both sums finite); the coulombic part and
    # the reason of a refused estimate use the same ones.
    kept = (a > 0.0) & np.isfinite(a) & np.isfinite(b)
    est = _ratio.estimate_loops(
        t, a, b, loops["linked"].to_numpy(int), at_days, config.tau_days, level,
        config.efficiency_min_loops, 0.0, 1.0,
    )
    rows = []
    for k, tq in enumerate(at_days):
        e = est.iloc[k]
        row = dict.fromkeys(EFFICIENCY_COLUMNS[:6] + ["rest_error_wh"], math.nan)
        row.update(n_loops=float(e["n_events"]), dof=float(e["dof"]), rho=float(e["rho"]))
        code = int(e["status"])
        if tq < ready_days:
            row["status"] = _core.STATUS_TEXT[_core.NO_LEVELS]
        elif code == _core.OK:
            eta = float(e["value"])
            coulombic = _decayed_ratio(t[kept], ah_out[kept], ah_in[kept], float(tq), config.tau_days)
            row.update(
                round_trip_efficiency=eta,
                round_trip_efficiency_low=float(e["low"]),
                round_trip_efficiency_high=float(e["high"]),
                round_trip_efficiency_sigma=float(e["sigma"]),
                rest_error_wh=float(e["rest_error"]),
                coulombic_efficiency=coulombic,
                voltage_efficiency=eta / coulombic,
                status=_core.STATUS_TEXT[_core.OK],
            )
        elif code == _core.TOO_FEW_EVENTS:
            row["status"] = TOO_FEW_LOOPS
        elif code == _core.OUT_OF_RANGE:
            ratio = _decayed_ratio(t[kept], b[kept], a[kept], float(tq), config.tau_days)
            row["status"] = OUT_EXCEEDS_IN if ratio > 1.0 else NO_ENERGY_OUT
        elif code == _core.SINGULAR:
            row["status"] = NO_ENERGY_IN
        elif code == _core.NO_LINKS:
            row["status"] = NO_SHARED_REST
        elif code == _core.NO_BAND:
            row["status"] = NO_REST_ERROR
        elif code == _core.LOW_DOF:
            row["status"] = REST_ERROR_UNKNOWN
        else:
            row["status"] = _core.STATUS_TEXT.get(code, f"not possible from this data: status {code}")
        rows.append(row)
    out = pd.DataFrame(rows, index=at, columns=EFFICIENCY_COLUMNS)
    out.index.name = "time"
    out.attrs["loops"] = loops
    out.attrs["kind"] = kind
    out.attrs["config_hash"] = config_hash(config)
    out.attrs["capacity_config_hash"] = config_hash(capacity_config)
    return out
