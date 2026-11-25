"""Rests at full and empty, and the pairs between them.

Method: Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9,
Supplementary Methods ("Relaxation phase detection"): a current filter, a peak filter, a
duration filter and a charge-throughput filter. The thresholds are besslib's own, tuned on
the development systems (see ``CapacityConfig``).

Each rest is anchored at its *start*, the moment the current stops. Its *edge voltage*
is the median voltage in the last minute before that, while current still flows. The
edge voltage decides whether the rest is at full or at empty: under load the voltage
rises or falls steeply at both ends of the charge window, also for LFP, whose resting
voltage is too flat to tell full from half-full.

Every rest also gets an open-circuit voltage from a two-RC relaxation fit (Suppl.
Eq. 2-5), with bounded time constants found on a grid. It is the level near the end of
the fitted part of the rest, at most ``ocv_fit_max_min`` after its start; the capacity
fit's voltage terms read it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from besslib.frames import PackSpec, check_frame
from besslib.soh.config import CapacityConfig

#: Columns of the rest table.
REST_COLUMNS = [
    "start", "end", "duration_min", "truncated", "edge_v", "edge_current_a", "v_end", "ocv_v", "ocv_sigma_v",
    "temp_pack_c", "charge_ah", "throughput_ah", "throughput_end_ah", "missing_s", "interpolated_s",
    "energy_in_wh", "energy_out_wh",
]

_TAU_FAST_S = (20.0, 60.0, 180.0)
_TAU_SLOW_S = (300.0, 900.0, 2700.0)


def _arrays(frame: pd.DataFrame):
    t = frame.index.as_unit("s").asi8.astype(np.int64)
    i = frame["current_a"].to_numpy(dtype=float)
    v = frame["voltage_v"].to_numpy(dtype=float)
    temp = frame["temp_pack_c"].to_numpy(dtype=float)
    if "interpolated" in frame:
        interp = frame["interpolated"].to_numpy(dtype=bool)
    else:
        interp = np.zeros(len(frame), dtype=bool)
    return t, i, v, temp, interp


def _power(frame: pd.DataFrame) -> np.ndarray:
    return frame["power_w"].to_numpy(dtype=float)


def _counters(t, i, interp, break_gap_s, p):
    """Running charge (Ah), throughput (Ah), missing and interpolated seconds, energy in and
    energy out (Wh) at each sample.

    Steps longer than ``break_gap_s`` are not bridged: they add no charge. Any step longer
    than 1.5 times the usual step (the median) counts as missing time beyond that step. A
    step with a missing (NaN) current at either end adds no charge and counts as missing.
    Energy in and out are the trapezoids of ``max(P, 0)`` and ``max(-P, 0)`` over the steps
    that are not longer than ``break_gap_s`` and have a finite power at both ends.
    """
    dt = np.diff(t).astype(float)
    step = float(np.median(dt)) if len(dt) else 1.0
    finite = np.isfinite(i)
    valid = finite[1:] & finite[:-1]  # a step with a missing current value adds no charge
    bridged = (dt <= break_gap_s) & valid
    ii = np.where(finite, i, 0.0)
    step_q = np.where(bridged, 0.5 * (ii[1:] + ii[:-1]) * dt, 0.0) / 3600.0
    step_a = np.where(bridged, 0.5 * (np.abs(ii[1:]) + np.abs(ii[:-1])) * dt, 0.0) / 3600.0
    step_m = np.where(valid, np.where(dt > 1.5 * step, dt - step, 0.0), dt)
    step_i = np.where(interp[1:], np.minimum(dt, break_gap_s), 0.0)
    pfinite = np.isfinite(p)
    pbridged = (dt <= break_gap_s) & pfinite[1:] & pfinite[:-1]
    pp = np.where(pfinite, p, 0.0)
    step_ein = np.where(pbridged, 0.5 * (np.maximum(pp[1:], 0.0) + np.maximum(pp[:-1], 0.0)) * dt, 0.0) / 3600.0
    step_eout = np.where(pbridged, 0.5 * (np.maximum(-pp[1:], 0.0) + np.maximum(-pp[:-1], 0.0)) * dt, 0.0) / 3600.0
    zero = np.zeros(1)
    return (
        np.concatenate([zero, np.cumsum(step_q)]),
        np.concatenate([zero, np.cumsum(step_a)]),
        np.concatenate([zero, np.cumsum(step_m)]),
        np.concatenate([zero, np.cumsum(step_i)]),
        np.concatenate([zero, np.cumsum(step_ein)]),
        np.concatenate([zero, np.cumsum(step_eout)]),
    )


def _runs(mask: np.ndarray):
    """Start and end (exclusive) indices of the True runs of ``mask``."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return np.flatnonzero(d == 1), np.flatnonzero(d == -1)


def _ocv_fit(seconds: np.ndarray, volts: np.ndarray) -> tuple[float, float]:
    """Open-circuit voltage from ``V = ocv + a exp(-t/tf) + b exp(-t/ts)`` on a tau grid."""
    good = np.isfinite(volts)
    seconds, volts = seconds[good], volts[good]
    if len(volts) < 30:
        return (float(volts[-1]) if len(volts) else float("nan")), float("nan")
    best = (np.inf, float(volts[-1]))
    ones = np.ones_like(seconds)
    for tf in _TAU_FAST_S:
        ef = np.exp(-seconds / tf)
        for ts in _TAU_SLOW_S:
            design = np.column_stack([ones, ef, np.exp(-seconds / ts)])
            coef, *_ = np.linalg.lstsq(design, volts, rcond=None)
            sse = float(np.sum((design @ coef - volts) ** 2))
            if sse < best[0]:
                best = (sse, float(coef[0]))
    rmse = np.sqrt(best[0] / len(volts))
    ocv = best[1]
    return ocv, float(0.5 * abs(ocv - volts[-1]) + rmse)


def _nanstat(func, values) -> float:
    """``func`` (``np.nanmedian`` or ``np.nanmean``) of the non-NaN values; NaN, without a
    warning, when there are none (an empty slice too)."""
    values = np.asarray(values, dtype=float)
    if np.isnan(values).all():
        return math.nan
    return float(func(values))


def detect_rests(
    frame: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    origin: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Rest candidates in ``frame``: the current, peak and duration filters only.

    The running counters (``charge_ah``, ``throughput_ah``, ``missing_s``,
    ``interpolated_s``) are zero at ``origin`` (default: the first sample), so tables from
    consecutive pieces of a long record can be joined by adding offsets.
    """
    config = config or CapacityConfig()
    check_frame(frame)
    t, i, v, temp, interp = _arrays(frame)
    if len(t) < 2:
        return pd.DataFrame(columns=REST_COLUMNS)
    q, qa, miss, ins, ein, eout = _counters(t, i, interp, config.break_gap_s, _power(frame))
    if origin is not None:
        k = int(np.searchsorted(t, pd.Timestamp(origin).value // 1_000_000_000))
        k = min(max(k, 0), len(t) - 1)
        q, qa, miss, ins = q - q[k], qa - qa[k], miss - miss[k], ins - ins[k]
        ein, eout = ein - ein[k], eout - eout[k]

    idle = np.abs(i) < config.current_c_rate * spec.nominal_capacity_ah
    # Peak filter: short excursions are tolerated, longer ones end the rest.
    starts, ends = _runs(~idle)
    inner = (starts > 0) & (ends < len(t))
    starts, ends = starts[inner], ends[inner]
    short = t[ends] - t[starts] <= config.peak_s
    fill = np.zeros(len(t) + 1, dtype=np.int32)
    np.add.at(fill, starts[short], 1)
    np.add.at(fill, ends[short], -1)
    idle |= np.cumsum(fill[:-1]) > 0
    # A data gap ends a rest.
    gap_after = np.flatnonzero(np.diff(t) > config.break_gap_s)

    rows = []
    starts, ends = _runs(idle)
    for a, b in zip(starts, ends):
        cuts = gap_after[(gap_after >= a) & (gap_after < b - 1)]
        pieces = []
        lo = a
        for g in cuts:
            pieces.append((lo, g + 1))
            lo = g + 1
        pieces.append((lo, b))
        for a2, b2 in pieces:
            if a2 == 0:
                continue  # no current flow seen before it: no edge voltage
            duration = t[b2 - 1] - t[a2]
            if duration < config.min_duration_min * 60.0:
                continue
            first = int(np.searchsorted(t, t[a2] - config.pre_window_s))
            pre_idx = np.arange(first, a2)
            if len(pre_idx) == 0:
                continue
            fit_end = int(np.searchsorted(t, t[a2] + config.ocv_fit_max_min * 60.0, side="right"))
            fit_end = min(fit_end, b2)
            ocv, ocv_sigma = _ocv_fit((t[a2:fit_end] - t[a2]).astype(float), v[a2:fit_end])
            rows.append(
                (
                    t[a2], t[b2 - 1], duration / 60.0, bool(b2 == len(t)),
                    _nanstat(np.nanmedian, v[pre_idx]), _nanstat(np.nanmedian, i[pre_idx]),
                    _nanstat(np.nanmean, v[max(b2 - 10, a2) : b2]), ocv, ocv_sigma,
                    _nanstat(np.nanmean, temp[a2:b2]), q[a2], qa[a2], qa[b2 - 1], miss[a2], ins[a2], ein[a2], eout[a2],
                )
            )
    out = pd.DataFrame(rows, columns=REST_COLUMNS)
    for name in ("start", "end"):
        out[name] = pd.to_datetime(out[name].astype("int64"), unit="s")
    return out


def counters_at(
    frame: pd.DataFrame,
    at: pd.Timestamp,
    config: CapacityConfig | None = None,
    origin: pd.Timestamp | None = None,
) -> tuple[pd.Timestamp, float, float, float, float, float, float]:
    """The running counters of ``detect_rests`` at the first sample at or after ``at``.

    Returns ``(time, charge_ah, throughput_ah, missing_s, interpolated_s, energy_in_wh, energy_out_wh)``. If no sample
    is at or after ``at``, the last sample is used and its time returned, so the caller
    can count the gap that follows as missing.
    """
    config = config or CapacityConfig()
    t, i, _, _, interp = _arrays(frame)
    q, qa, miss, ins, ein, eout = _counters(t, i, interp, config.break_gap_s, _power(frame))
    k0 = 0
    if origin is not None:
        k0 = min(int(np.searchsorted(t, pd.Timestamp(origin).value // 1_000_000_000)), len(t) - 1)
    k = min(int(np.searchsorted(t, pd.Timestamp(at).value // 1_000_000_000)), len(t) - 1)
    return (
        pd.Timestamp(int(t[k]), unit="s"), q[k] - q[k0], qa[k] - qa[k0], miss[k] - miss[k0], ins[k] - ins[k0],
        ein[k] - ein[k0], eout[k] - eout[k0],
    )


def detect_rests_in_parts(parts, spec: PackSpec, configs, margin: str = "1D", log=None, each=None):
    """Rest candidates over a long record that comes in consecutive pieces (e.g. months).

    Each piece is searched with ``margin`` of its neighbours on both sides, so a rest
    across a boundary is found whole, and it keeps the rests that start inside it. The
    running counters are joined across pieces, including the step between them.

    ``configs`` is one ``CapacityConfig`` or a sequence of them (each piece is read once
    for all). Returns one table, or a list of tables in the order of ``configs``.

    ``each``, if given, is called as ``each(prev, cur, tables)`` once per piece, after that
    piece's rests are final: ``prev`` is the previous non-empty piece (or None), ``cur`` the
    piece itself, ``tables`` the list of its rest tables in the order of ``configs``.
    """
    single = isinstance(configs, CapacityConfig)
    configs = [configs] if single else list(configs)
    margin = pd.Timedelta(margin)
    tables = [[] for _ in configs]
    totals = [[0.0] * 6 for _ in configs]
    columns = ("charge_ah", "throughput_ah", "throughput_end_ah", "missing_s", "interpolated_s", "energy_in_wh",
               "energy_out_wh")
    slots = (0, 1, 1, 2, 3, 4, 5)
    prev = cur = None
    count = 0

    def flush(prev, cur, nxt):
        first = cur.index[0]
        bound = nxt.index[0] if nxt is not None else cur.index[-1] + pd.Timedelta(seconds=1)
        pieces = []
        if prev is not None:
            pieces.append(prev.loc[first - margin :])
        pieces.append(cur)
        if nxt is not None:
            pieces.append(nxt.loc[: bound + margin])
        frame = pd.concat(pieces) if len(pieces) > 1 else cur
        frame = frame[~frame.index.duplicated(keep="first")]
        mine = []
        for k, cfg in enumerate(configs):
            rests = detect_rests(frame, spec, cfg, origin=first)
            rests = rests[(rests["start"] >= first) & (rests["start"] < bound)].copy()
            off = totals[k]
            for col, slot in zip(columns, slots):
                rests[col] = rests[col] + off[slot]
            tables[k].append(rests)
            mine.append(rests)
            if nxt is not None:
                _, dq, da, dm, di, dei, deo = counters_at(frame, bound, cfg, origin=first)
                off[0] += dq
                off[1] += da
                off[2] += dm
                off[3] += di
                off[4] += dei
                off[5] += deo
        if each is not None:
            each(prev, cur, mine)

    for part in parts:
        if part is None or len(part) == 0:
            continue
        if cur is not None:
            flush(prev, cur, part)
            count += 1
            if log is not None:
                log(count)
        prev, cur = cur, part
    if cur is not None:
        flush(prev, cur, None)
    out = [pd.concat(t, ignore_index=True) if t else pd.DataFrame(columns=REST_COLUMNS) for t in tables]
    return out[0] if single else out


def classify_rests(candidates: pd.DataFrame, spec: PackSpec, config: CapacityConfig | None = None) -> pd.DataFrame:
    """Apply the throughput filter and label each rest ``full``, ``empty`` or ``other``.

    ``candidates`` is the output of ``detect_rests`` for a whole record, in time order.
    Adds ``accepted``, ``direction`` (``charge`` or ``discharge``: what came before the
    rest), ``level_full_v``, ``level_empty_v`` and ``kind``.

    The levels are the system's end-of-charge and end-of-discharge edge voltages: high
    and low quantiles of the edge voltages of accepted rests in the trailing
    ``config.level_window_days`` (one operating year by default, so that both a summer
    and a winter are in it). Before ``config.level_warmup_days`` have passed since the
    first accepted rest of the record, the levels are NaN and every rest is ``other``.
    """
    config = config or CapacityConfig()
    rests = candidates.sort_values("start").reset_index(drop=True).copy()
    need = config.min_throughput * spec.nominal_capacity_ah
    accepted = np.zeros(len(rests), dtype=bool)
    last_end = -np.inf
    for k, (start_ah, end_ah) in enumerate(zip(rests["throughput_ah"], rests["throughput_end_ah"])):
        if start_ah - last_end >= need:
            accepted[k] = True
            last_end = end_ah
    rests["accepted"] = accepted
    rests["direction"] = np.where(rests["edge_current_a"] > 0, "charge", "discharge")

    window = f"{config.level_window_days:g}D"
    rests["level_full_v"] = np.nan
    rests["level_empty_v"] = np.nan
    for direction, column, quantile in (
        ("charge", "level_full_v", config.edge_quantile),
        ("discharge", "level_empty_v", 1.0 - config.edge_quantile),
    ):
        sel = rests[rests["accepted"] & (rests["direction"] == direction)]
        if sel.empty:
            continue
        series = sel.set_index("start")["edge_v"]
        rolling = series.rolling(window, min_periods=config.min_level_rests)
        level = rolling.quantile(quantile)
        level = level.groupby(level.index).last()
        rests[column] = level.reindex(rests["start"], method="ffill").to_numpy()

    # No level before the warm-up has passed: until then the window has not seen every
    # season, and its quantiles need not be the system's limits.
    ready = levels_from(rests, config)
    if pd.notna(ready):
        early = (rests["start"] < ready).to_numpy()
        rests.loc[early, ["level_full_v", "level_empty_v"]] = np.nan

    span = rests["level_full_v"] - rests["level_empty_v"]
    tol = config.edge_tolerance * span
    full = (
        rests["accepted"]
        & (rests["direction"] == "charge")
        & (rests["edge_v"] >= rests["level_full_v"] - tol)
    )
    empty = (
        rests["accepted"]
        & (rests["direction"] == "discharge")
        & (rests["edge_v"] <= rests["level_empty_v"] + tol)
    )
    rests["kind"] = np.where(full, "full", np.where(empty, "empty", "other"))
    return rests


def levels_from(rests: pd.DataFrame, config: CapacityConfig | None = None) -> pd.Timestamp:
    """The first time at which the full and empty levels exist: ``config.level_warmup_days``
    after the first accepted rest of ``rests`` (a table with an ``accepted`` column, as
    ``classify_rests`` writes it). ``NaT`` when no rest is accepted."""
    config = config or CapacityConfig()
    first = rests.loc[rests["accepted"].to_numpy(dtype=bool), "start"].min() if len(rests) else pd.NaT
    if pd.isna(first):
        return pd.NaT
    return pd.Timestamp(first) + pd.Timedelta(days=config.level_warmup_days)


def find_rests(frame: pd.DataFrame, spec: PackSpec, config: CapacityConfig | None = None) -> pd.DataFrame:
    """All rests in ``frame``, labelled ``full``, ``empty`` or ``other``.

    Returns one row per rest: ``start`` (the anchor), ``end``, ``duration_min``,
    ``edge_v`` (the voltage just before the rest), ``ocv_v`` and ``ocv_sigma_v`` (from the
    relaxation fit), ``temp_pack_c``, running ``charge_ah`` and ``throughput_ah`` at the
    anchor, ``accepted`` (throughput filter), ``direction``, ``level_full_v``,
    ``level_empty_v`` and ``kind``. In the first ``config.level_warmup_days`` of the
    record (one year by default) no level exists yet and every rest is ``other``.
    """
    return classify_rests(detect_rests(frame, spec, config), spec, config)


def operating_rests(
    candidates: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    mask: list[tuple[pd.Timestamp, pd.Timestamp]] | None = None,
) -> pd.DataFrame:
    """The labelled rests of normal operation, from a table of rest candidates.

    Keeps the candidates that last at least ``config.min_duration_min``, removes those
    that start inside a ``mask`` interval (for example a capacity test), and labels the
    others with ``classify_rests``. The masked rests are removed before the full and
    empty levels are set, so they do not move the levels either.
    """
    config = config or CapacityConfig()
    cands = candidates[candidates["duration_min"] >= config.min_duration_min]
    inside = np.zeros(len(cands), dtype=bool)
    for a, b in mask or []:
        inside |= ((cands["start"] >= pd.Timestamp(a)) & (cands["start"] <= pd.Timestamp(b))).to_numpy()
    return classify_rests(cands[~inside], spec, config)


#: Columns of the rest chain.
CHAIN_COLUMNS = [
    "time", "kind", "full", "q_ah", "throughput_ah", "missing_s", "edge_v", "temp_pack_c", "new_segment", "reason",
    "ocv_v", "duration_min",
]


def rest_chain(
    rests: pd.DataFrame,
    spec: PackSpec,
    config: CapacityConfig | None = None,
    mask: list[tuple[pd.Timestamp, pd.Timestamp]] | None = None,
) -> pd.DataFrame:
    """The full and empty rests in time order, split into linked runs (segments).

    Rests inside a ``mask`` interval are removed. A new segment starts at the first rest,
    after a masked interval (``reason = "mask"``), after more than ``max_missing_s``
    missing seconds (``"gap"``) and after more than ``max_link_days`` without a full or
    empty rest (``"long"``). No pair links two rests across a new segment, so no charge
    measured inside a masked interval reaches the estimator.
    """
    config = config or CapacityConfig()
    ends = rests[rests["kind"].isin(["full", "empty"])].sort_values("start").reset_index(drop=True)
    intervals = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in (mask or [])]
    if intervals and len(ends):
        inside = np.zeros(len(ends), dtype=bool)
        for a, b in intervals:
            inside |= ((ends["start"] >= a) & (ends["start"] <= b)).to_numpy()
        ends = ends[~inside].reset_index(drop=True)
    chain = pd.DataFrame(
        {
            "time": ends["start"],
            "kind": ends["kind"],
            "full": (ends["kind"] == "full").astype(int),
            "q_ah": ends["charge_ah"].astype(float),
            "throughput_ah": ends["throughput_ah"].astype(float),
            "missing_s": ends["missing_s"].astype(float),
            "edge_v": ends["edge_v"].astype(float),
            "temp_pack_c": ends["temp_pack_c"].astype(float),
            "ocv_v": ends["ocv_v"].astype(float),
            "duration_min": ends["duration_min"].astype(float) if "duration_min" in ends else np.nan,
        }
    )
    reason = np.full(len(chain), "", dtype=object)
    if len(chain):
        gap_days = chain["time"].diff().dt.total_seconds().to_numpy() / 86400.0
        missing = chain["missing_s"].diff().to_numpy()
        reason[gap_days > config.max_link_days] = "long"
        reason[missing > config.max_missing_s] = "gap"
        times = chain["time"].to_numpy()
        for a, b in intervals:
            after = np.searchsorted(times, np.datetime64(b), side="right")
            before = np.searchsorted(times, np.datetime64(a), side="left")
            if 0 < after < len(chain) and before == after:
                reason[after] = "mask"
        reason[0] = "start"
    chain["reason"] = reason
    chain["new_segment"] = (chain["reason"] != "").astype(int)
    return chain[CHAIN_COLUMNS]


#: Columns of the pair table.
PAIR_COLUMNS = [
    "t_start", "t_end", "kind", "x", "dt_days", "q_ah", "throughput_ah", "dv", "temp_pack_c",
    "sigma_q_ah", "sigma_x", "linked", "u_start", "u_end",
]


def rest_pairs(chain: pd.DataFrame, config: CapacityConfig | None = None) -> pd.DataFrame:
    """The pairs of linked consecutive rests: ``F->E``, ``E->F``, ``F->F``, ``E->E``.

    ``x`` is the change of state of charge (+1, -1 or 0), ``q_ah`` the charge measured
    between the rests, ``dv`` the change of edge voltage. ``linked`` is 1 when the pair
    starts at the rest where the previous pair ended (their errors share that rest).
    ``u_start`` and ``u_end`` are the relaxed open-circuit voltages (``ocv_v``) of the
    two rests: the capacity fit's voltage terms are formed from them
    (``besslib.soh._core``; ``capacity.voltage_terms``). ``dt_days`` is the time between
    the moments the rest voltages are read, ``min(duration_min, ocv_fit_max_min)`` after
    each start (``config.rest_time = "reading"``, version 0.3), or between the starts
    (``"start"``). The charge is the same in both: no current flows through the meter in a
    rest, but the battery's own supply drains it while its voltage relaxes.
    """
    config = config or CapacityConfig()
    a = chain.iloc[:-1].reset_index(drop=True)
    b = chain.iloc[1:].reset_index(drop=True)
    pairs = pd.DataFrame(
        {
            "t_start": a["time"],
            "t_end": b["time"],
            "kind": a["kind"].str[0].str.upper() + "->" + b["kind"].str[0].str.upper(),
            "x": (b["full"] - a["full"]).astype(float),
            "dt_days": (b["time"] - a["time"]).dt.total_seconds() / 86400.0,
            "q_ah": b["q_ah"] - a["q_ah"],
            "throughput_ah": b["throughput_ah"] - a["throughput_ah"],
            "dv": b["edge_v"] - a["edge_v"],
            "temp_pack_c": 0.5 * (a["temp_pack_c"] + b["temp_pack_c"]),
            "linked": (a["new_segment"] == 0).astype(int),
        }
    )
    if config.rest_time == "reading":
        if len(chain) and chain["duration_min"].isna().any():
            raise ValueError('rest_time = "reading" needs the duration of every rest (duration_min)')
        read_a = a["duration_min"].clip(upper=config.ocv_fit_max_min).to_numpy(float)
        read_b = b["duration_min"].clip(upper=config.ocv_fit_max_min).to_numpy(float)
        pairs["dt_days"] += (read_b - read_a) / 1440.0
    pairs["u_start"] = a["ocv_v"].to_numpy(float)
    pairs["u_end"] = b["ocv_v"].to_numpy(float)
    pairs = pairs[(b["new_segment"] == 0).to_numpy()].reset_index(drop=True)
    pairs["sigma_q_ah"] = config.sigma_q_rel * pairs["throughput_ah"].abs()
    pairs["sigma_x"] = np.sqrt(2.0) * config.sigma_soc_edge
    return pairs[PAIR_COLUMNS]
