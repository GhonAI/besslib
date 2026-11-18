"""Reduce a long record to three small tables: one row per day, per current step and per rest.

The record is read once, piece by piece (for example month by month), and never held
whole. The three tables hold everything the usage counts, the resistance and the
round-trip efficiency need, so those can be computed again and again without reading the
raw data:

``daily``
    One row per calendar day, none left out: seconds measured and interpolated, charge
    and energy in and out, the lowest and highest voltage, and the measured seconds
    spent charging, discharging and idle in each 1-degree pack-temperature bin.
``steps``
    One row per current step at the loosest setting of the tuning grid: the two
    currents and voltages around the step, the temperature and the seconds for which
    the current stayed steady before the step. A step qualifies for a setting
    ``(min_step_c_rate, steady_s)`` when its size and its steady time reach it.
``rests``
    The rest candidates of ``rests.detect_rests_in_parts``, with the running energy
    counters, and five sums of the daily path (``*_today``) over the steps of the rest's
    own day up to its start. The sum of the daily rows of the days before a rest plus
    these ``*_today`` values reproduces the running counters of the rest detector by a
    second, independent path; the two agree to rounding.

Definitions. A *step* is the interval between two consecutive samples. A step belongs to
the calendar day of its end sample. ``sample_s`` is the median sampling interval. A step
is *valid* when the current is finite at both ends. Charge and energy use the trapezoid
rule over the steps that are valid (energy: have a finite power at both ends) and not
longer than the break gap of the configuration; a step with a longer gap is not bridged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.frames import PackSpec, check_frame
from besslib.soh.config import CapacityConfig
from besslib.soh.rests import REST_COLUMNS, _arrays, _power, detect_rests_in_parts

#: Lower edge of the first temperature bin (degrees C) and the number of 1-degree bins.
TEMP_BIN_LOW_C = -20
TEMP_BINS = 90
#: The three operating modes of a step, as the daily table names them.
MODES = ("charge", "discharge", "idle")
#: The loosest step setting of the tuning grid: every step table row passes it.
LOOSEST_MIN_STEP_C_RATE = 0.05
LOOSEST_STEADY_S = 2.0
#: Longest steady look-back (s) and the tolerance of "steady" (share of the step size).
STEADY_CAP_S = 10.0
STEADY_TOLERANCE = 0.2

_TAIL = 24  # samples kept from the previous piece (a step and its steady look-back reach back at most this far)
_NBIN = TEMP_BINS + 1  # the last bin is "no temperature reading"
_SUM_COLUMNS = ["measured_s", "interpolated_s", "ah_in", "ah_out", "wh_in", "wh_out"]
_TODAY_COLUMNS = ["ah_in_today", "ah_out_today", "wh_in_today", "wh_out_today", "measured_s_today"]
_TODAY_FROM = [2, 3, 4, 5, 0]  # their columns in _SUM_COLUMNS
STEP_COLUMNS = [
    "time", "current_before_a", "current_after_a", "voltage_before_v", "voltage_after_v", "temp_pack_c", "steady_s",
]


def _bin_names() -> list[str]:
    names = []
    for mode in MODES:
        names += [f"s_{mode}_{k:02d}" for k in range(TEMP_BINS)] + [f"s_{mode}_nan"]
    return names


_BIN_COLUMNS = _bin_names()
DAILY_COLUMNS = (
    ["day", "t_first", "t_last", "sample_s", *_SUM_COLUMNS, "voltage_min_v", "voltage_max_v"] + _BIN_COLUMNS
)


@dataclass
class Reduced:
    """The three tables of a reduced record and its usual sampling interval (s)."""

    daily: pd.DataFrame
    steps: pd.DataFrame
    rests: pd.DataFrame
    sample_s: float


def _time(seconds) -> pd.DatetimeIndex:
    return pd.to_datetime(np.asarray(seconds, dtype=np.int64), unit="s")


class _Tap:
    """Sums one piece of the record into daily rows, step rows and per-rest day sums.

    A piece is read together with the last samples of the pieces before it, so the step
    that ends at its first sample and the steady look-back of its first steps see their
    neighbours. The day sums are made from per-step arrays, not from the running
    counters of the rest detector.
    """

    def __init__(self, spec: PackSpec, config: CapacityConfig) -> None:
        self.config = config
        self.idle_a = config.current_c_rate * spec.nominal_capacity_ah
        self.step_a = LOOSEST_MIN_STEP_C_RATE * spec.nominal_capacity_ah
        self.tail = None
        self.sample_s: list[float] = []
        self.carry_day = None
        self.carry = np.zeros(len(_TODAY_COLUMNS))
        # per piece: day numbers, sums, bin seconds, vmin, vmax, first and last sample time
        self.days, self.sums, self.bins, self.vmin, self.vmax, self.tfirst, self.tlast = [], [], [], [], [], [], []
        self.steps: list[pd.DataFrame] = []
        self.today: list[np.ndarray] = []

    def __call__(self, prev, cur: pd.DataFrame, tables) -> None:
        check_frame(cur)
        cfg = self.config
        t_c, i_c, v_c, temp_c, interp_c = _arrays(cur)
        p_c = _power(cur)
        n = len(t_c)
        # sample interval of this piece: the median step, as the rest detector's counters
        dt_c = np.diff(t_c).astype(float)
        if len(dt_c):
            s = float(np.median(dt_c))
        else:
            s = self.sample_s[-1] if self.sample_s else 1.0
        self.sample_s.append(s)

        if self.tail is None:
            m = 0
            t, i, v, temp, interp, p = t_c, i_c, v_c, temp_c, interp_c, p_c
        else:
            m = len(self.tail[0])
            t, i, v, temp, interp, p = (np.concatenate([a, b]) for a, b in zip(self.tail, (t_c, i_c, v_c, temp_c, interp_c, p_c)))
        self.tail = tuple(a[-_TAIL:] for a in (t, i, v, temp, interp, p))

        # --- per-step arrays, one value per sample of this piece (0 where the sample has no step) ---
        lo = 1 if m == 0 else 0  # the first sample of the record has no step
        J = np.arange(m + lo, m + n)  # end samples of the steps in this piece
        dt = (t[J] - t[J - 1]).astype(float)
        fi = np.isfinite(i)
        valid = fi[J] & fi[J - 1]
        bridged = valid & (dt <= cfg.break_gap_s)
        ii = np.where(fi, i, 0.0)
        pf = np.isfinite(p)
        pbridged = pf[J] & pf[J - 1] & (dt <= cfg.break_gap_s)
        pp = np.where(pf, p, 0.0)

        def trapezoid(x, ok):
            return np.where(ok, 0.5 * (x[J] + x[J - 1]) * dt, 0.0)

        rows = np.zeros((len(_SUM_COLUMNS), n))
        sl = slice(lo, n)
        measured = np.where(valid, np.where(dt <= 1.5 * s, dt, s), 0.0)
        rows[0, sl] = measured
        rows[1, sl] = np.where(interp[J], np.minimum(dt, cfg.break_gap_s), 0.0)
        rows[2, sl] = trapezoid(np.maximum(ii, 0.0), bridged) / 3600.0
        rows[3, sl] = trapezoid(np.maximum(-ii, 0.0), bridged) / 3600.0
        rows[4, sl] = trapezoid(np.maximum(pp, 0.0), pbridged) / 3600.0
        rows[5, sl] = trapezoid(np.maximum(-pp, 0.0), pbridged) / 3600.0

        # mode and temperature bin of the end sample
        mode = np.where(i_c >= self.idle_a, 0, np.where(i_c <= -self.idle_a, 1, 2))
        okt = np.isfinite(temp_c)
        with np.errstate(invalid="ignore"):
            k = np.clip(np.floor(np.where(okt, temp_c, 0.0)) - TEMP_BIN_LOW_C, 0, TEMP_BINS - 1)
        col = mode * _NBIN + np.where(okt, k, TEMP_BINS).astype(np.int64)

        # --- daily sums: the samples of each calendar day are contiguous ---
        day = t_c // 86400
        starts = np.concatenate([[0], np.flatnonzero(np.diff(day)) + 1])
        seg = np.zeros(n, dtype=np.int64)
        seg[starts[1:]] = 1
        seg = np.cumsum(seg)
        nseg = len(starts)
        sums = np.add.reduceat(rows, starts, axis=1).T
        bins = np.bincount(seg * (3 * _NBIN) + col, weights=rows[0], minlength=nseg * 3 * _NBIN).reshape(nseg, 3 * _NBIN)
        self.days.append(day[starts])
        self.sums.append(sums)
        self.bins.append(bins)
        self.vmin.append(np.fmin.reduceat(v_c, starts))
        self.vmax.append(np.fmax.reduceat(v_c, starts))
        self.tfirst.append(t_c[starts])
        self.tlast.append(t_c[np.concatenate([starts[1:] - 1, [n - 1]])])

        # --- the sums of the rest's own day up to its start ---
        rests = tables[0]
        if len(rests):
            r = np.searchsorted(t_c, rests["start"].astype("datetime64[s]").astype(np.int64))
            if not np.array_equal(t_c[r], rests["start"].astype("datetime64[s]").astype(np.int64)):
                raise ValueError("a rest starts at a time that is not a sample of its piece")
            cs = np.cumsum(rows[_TODAY_FROM], axis=1)
            before = np.where(starts[seg[r]] > 0, cs[:, np.maximum(starts[seg[r]] - 1, 0)], 0.0)
            today = (cs[:, r] - before).T
            if self.carry_day is not None:
                first = seg[r] == 0
                if day[0] == self.carry_day:
                    today[first] += self.carry
            self.today.append(today)
        else:
            self.today.append(np.zeros((0, len(_TODAY_COLUMNS))))
        last = sums[-1, _TODAY_FROM].copy()
        if self.carry_day is not None and day[-1] == self.carry_day:
            last += self.carry
        self.carry, self.carry_day = last, int(day[-1])

        # --- the step rows ---
        fv = np.isfinite(v)
        di = np.abs(i[J] - i[J - 1])
        ok = valid & fv[J] & fv[J - 1] & ~interp[J] & ~interp[J - 1] & (dt <= 1.5 * s)
        with np.errstate(invalid="ignore"):
            ok &= di >= self.step_a
        c = J[ok]
        if len(c):
            dic = di[ok]
            ref = i[c - 1]
            reach = max(1, int(round(STEADY_CAP_S / s)))
            alive = np.ones(len(c), dtype=bool)
            n_ok = np.zeros(len(c))
            for back in range(1, reach + 1):
                kk = c - 1 - back
                inside = kk >= 0
                kc = np.maximum(kk, 0)
                with np.errstate(invalid="ignore"):
                    good = (
                        inside & fi[kc] & ~interp[kc] & ((t[kc + 1] - t[kc]) <= 1.5 * s)
                        & (np.abs(i[kc] - ref) <= STEADY_TOLERANCE * dic)
                    )
                alive &= good
                n_ok += alive
            steady = n_ok * s
            keep = steady >= LOOSEST_STEADY_S
            c, steady = c[keep], steady[keep]
            self.steps.append(
                pd.DataFrame(
                    {
                        "time": _time(t[c]),
                        "current_before_a": i[c - 1],
                        "current_after_a": i[c],
                        "voltage_before_v": v[c - 1],
                        "voltage_after_v": v[c],
                        "temp_pack_c": temp[c],
                        "steady_s": steady,
                    }
                )
            )

    # --- the finished tables ---
    def daily(self, sample_s: float) -> pd.DataFrame:
        if not self.days:
            return pd.DataFrame(columns=DAILY_COLUMNS)
        day = np.concatenate(self.days)
        full = np.arange(day.min(), day.max() + 1)
        pos = np.searchsorted(full, day)
        out_sums = np.zeros((len(full), len(_SUM_COLUMNS)))
        out_bins = np.zeros((len(full), 3 * _NBIN))
        np.add.at(out_sums, pos, np.concatenate(self.sums))
        np.add.at(out_bins, pos, np.concatenate(self.bins))
        vmin = np.full(len(full), np.nan)
        vmax = np.full(len(full), np.nan)
        np.fmin.at(vmin, pos, np.concatenate(self.vmin))
        np.fmax.at(vmax, pos, np.concatenate(self.vmax))
        big = np.iinfo(np.int64).max
        tfirst = np.full(len(full), big, dtype=np.int64)
        tlast = np.full(len(full), -big, dtype=np.int64)
        np.minimum.at(tfirst, pos, np.concatenate(self.tfirst))
        np.maximum.at(tlast, pos, np.concatenate(self.tlast))
        none = tfirst == big
        data = {
            "day": pd.to_datetime(full * 86400, unit="s"),
            "t_first": pd.to_datetime(np.where(none, 0, tfirst), unit="s").where(~none),
            "t_last": pd.to_datetime(np.where(none, 0, tlast), unit="s").where(~none),
            "sample_s": np.full(len(full), sample_s),
        }
        for k, name in enumerate(_SUM_COLUMNS):
            data[name] = out_sums[:, k]
        data["voltage_min_v"] = vmin
        data["voltage_max_v"] = vmax
        for k, name in enumerate(_BIN_COLUMNS):
            data[name] = out_bins[:, k]
        return pd.DataFrame(data)[DAILY_COLUMNS]


def reduce_parts(parts, spec: PackSpec, config: CapacityConfig | None = None, log=None) -> Reduced:
    """Reduce a record that comes in consecutive pieces (for example months) to its three tables.

    ``parts`` is any iterable of frames in time order; it is read once, and at most three
    pieces are in memory at a time. The rest candidates are those of
    ``rests.detect_rests_in_parts`` for ``config`` (default ``CapacityConfig()``), with the
    energy counters and the day sums added. ``log`` is called with the number of pieces
    finished.
    """
    config = config or CapacityConfig()
    tap = _Tap(spec, config)
    table = detect_rests_in_parts(parts, spec, config, log=log, each=tap)
    sample_s = float(np.median(tap.sample_s)) if tap.sample_s else float("nan")
    daily = tap.daily(sample_s)
    steps = (
        pd.concat(tap.steps, ignore_index=True) if tap.steps else pd.DataFrame({c: [] for c in STEP_COLUMNS})
    )[STEP_COLUMNS]
    table = table.reset_index(drop=True)
    today = np.concatenate(tap.today) if tap.today else np.zeros((0, len(_TODAY_COLUMNS)))
    for k, name in enumerate(_TODAY_COLUMNS):
        table[name] = today[:, k]
    # a step table keeps its dtypes even when empty
    steps["time"] = pd.to_datetime(steps["time"])
    for name in STEP_COLUMNS[1:]:
        steps[name] = steps[name].astype("float64")
    return Reduced(daily=daily, steps=steps, rests=table[REST_COLUMNS + _TODAY_COLUMNS], sample_s=sample_s)


def reduce_frame(frame: pd.DataFrame, spec: PackSpec, config: CapacityConfig | None = None) -> Reduced:
    """Reduce one frame held in memory; the same as ``reduce_parts([frame], ...)``."""
    return reduce_parts([frame], spec, config)


def _paths(folder, name) -> dict[str, Path]:
    return {k: Path(folder) / f"{name}_{k}.parquet" for k in ("daily", "steps", "rests")}


def write_reduced(reduced: Reduced, folder, name: str) -> None:
    """Write ``<folder>/<name>_daily.parquet``, ``_steps.parquet`` and ``_rests.parquet``."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    paths = _paths(folder, name)
    reduced.daily.to_parquet(paths["daily"], index=False, compression="zstd")
    reduced.steps.to_parquet(paths["steps"], index=False, compression="zstd")
    reduced.rests.to_parquet(paths["rests"], index=False, compression="zstd")


def read_reduced(folder, name: str) -> Reduced:
    """Read the three tables written by ``write_reduced``."""
    paths = _paths(folder, name)
    daily = pd.read_parquet(paths["daily"])
    sample_s = float(daily["sample_s"].iloc[0]) if len(daily) else float("nan")
    return Reduced(
        daily=daily, steps=pd.read_parquet(paths["steps"]), rests=pd.read_parquet(paths["rests"]), sample_s=sample_s
    )


def temp_hours(daily: pd.DataFrame) -> pd.DataFrame:
    """Hours spent in each mode and 1-degree temperature bin, over all the days of ``daily``.

    Long form with the columns ``mode``, ``temp_c`` (lower edge of the bin in degrees C;
    NaN for the rows of no temperature reading) and ``hours``. Every mode has
    ``TEMP_BINS + 1`` rows.
    """
    rows = []
    edges = list(range(TEMP_BIN_LOW_C, TEMP_BIN_LOW_C + TEMP_BINS)) + [np.nan]
    for mode in MODES:
        names = [f"s_{mode}_{k:02d}" for k in range(TEMP_BINS)] + [f"s_{mode}_nan"]
        seconds = daily[names].sum(axis=0).to_numpy(dtype=float)
        rows.append(pd.DataFrame({"mode": mode, "temp_c": np.array(edges, dtype=float), "hours": seconds / 3600.0}))
    return pd.concat(rows, ignore_index=True)
