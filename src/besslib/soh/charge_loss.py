"""Apparent charge loss: the charge the battery loses that its meter does not see.

Annex VII of Regulation (EU) 2023/1542 asks for "the evolution of self-discharging
rates". Operating data cannot separate cell self-discharge from the other currents that
bypass the meter, so besslib reports what it can measure and names it for what it is.

Method: the capacity fit (``besslib.soh.capacity``, ``besslib.soh._core``) models the
measured charge of each pair of rests as ``q = C x + beta dt``, and reports the *offset
current* ``-beta / 24`` A. The apparent charge loss is minus that current, in mA:
positive when the battery loses charge the meter does not see. It is therefore an upper
bound for cell self-discharge that also holds the BMS supply, balancing currents and the
current sensor's zero offset, and in a home storage system it is mostly not cell
self-discharge. A negative value is possible (a sensor offset can have either sign) and
is reported as it is.

Band: a Student-t interval on the offset's lag-1 sandwich standard error, with the
degrees of freedom of the capacity fit (decayed pair count minus 2), and the current
sensor's gain error as a systematic term (``RecordConfig.charge_sigma``; JCGM 100:2008,
4.3.7): a gain error scales every measured charge, and so the fitted offset, by the same
factor.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core
from besslib.soh.config import RecordConfig, config_hash

#: Hours in an average month (365.25 x 24 / 12).
HOURS_PER_MONTH = 730.5

NO_OFFSET_BAND = "not possible from this data: the band of the offset current could not be computed"

#: Columns of the ``charge_loss`` table.
LOSS_COLUMNS = [
    "charge_loss_ma", "charge_loss_ma_low", "charge_loss_ma_high", "charge_loss_sigma_ma",
    "charge_loss_percent_per_month", "charge_loss_percent_per_month_low", "charge_loss_percent_per_month_high",
    "n_pairs", "status",
]


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def charge_loss(
    capacity: pd.DataFrame,
    spec: PackSpec,
    level: float = 0.90,
    config: RecordConfig | None = None,
) -> pd.DataFrame:
    """The apparent charge loss at each time of a capacity table.

    ``capacity`` is the output of ``capacity.estimate_capacity`` (index: time). Returns,
    with the same index: ``charge_loss_ma`` (= -1000 x ``offset_current_a``; positive =
    the battery loses charge the meter does not see) with ``_low`` and ``_high``;
    ``charge_loss_sigma_ma`` (its statistical standard error, without the gain term);
    the same loss in % of ``spec.reference_capacity_ah`` per month of 730.5 hours
    (``charge_loss_percent_per_month``, ``_low``, ``_high``); ``n_pairs`` (decayed pair
    count of the fit) and ``status``.

    The value is an upper bound for cell self-discharge: it also holds the BMS supply,
    balancing and the current sensor's zero offset (module docstring). A row is
    ``estimated`` only when the capacity row is, and the offset's sandwich variance is
    positive; otherwise all values are NaN and the status gives the reason.
    """
    config = config or RecordConfig()
    z = _z(level)
    rel = config.charge_sigma
    per_month = HOURS_PER_MONTH / spec.reference_capacity_ah * 100.0 / 1000.0  # mA -> % of reference per month
    rows = []
    for status, off, sig, n in zip(
        capacity["status"], capacity["offset_current_a"], capacity["offset_current_sigma_a"], capacity["n_pairs"]
    ):
        row = dict.fromkeys(LOSS_COLUMNS[:-2], math.nan)
        row["n_pairs"] = float(n)
        if status != _core.STATUS_TEXT[_core.OK]:
            row["status"] = status
        elif not (math.isfinite(sig) and math.isfinite(off)):
            row["status"] = NO_OFFSET_BAND
        else:
            value = -1000.0 * float(off)
            stat = 1000.0 * float(sig)
            k = _core.t_quantile(z, float(n) - 2.0)
            half = math.sqrt((k * stat) ** 2 + (z * rel * value) ** 2)
            row.update(
                charge_loss_ma=value,
                charge_loss_ma_low=value - half,
                charge_loss_ma_high=value + half,
                charge_loss_sigma_ma=stat,
                charge_loss_percent_per_month=value * per_month,
                charge_loss_percent_per_month_low=(value - half) * per_month,
                charge_loss_percent_per_month_high=(value + half) * per_month,
                status=_core.STATUS_TEXT[_core.OK],
            )
        rows.append(row)
    out = pd.DataFrame(rows, index=capacity.index, columns=LOSS_COLUMNS)
    out.index.name = "time"
    out.attrs["config_hash"] = config_hash(config)
    out.attrs["capacity_config_hash"] = capacity.attrs.get("config_hash")
    return out


def year_end_values(loss: pd.DataFrame) -> pd.DataFrame:
    """The value at the end of each calendar year: the last ``estimated`` row of each year.

    For the last year of a record that does not reach 31 December it is the last
    estimated row of that year; the ``time`` column says when.
    """
    ok = loss[loss["status"] == _core.STATUS_TEXT[_core.OK]]
    if len(ok) == 0:
        return pd.DataFrame(columns=["year", "time", "charge_loss_ma", "charge_loss_ma_low", "charge_loss_ma_high",
                                     "charge_loss_sigma_ma", "n_pairs"])
    ok = ok.sort_index()
    last = ok.groupby(ok.index.year).tail(1)
    out = last[["charge_loss_ma", "charge_loss_ma_low", "charge_loss_ma_high", "charge_loss_sigma_ma", "n_pairs"]].copy()
    out.insert(0, "time", last.index)
    out.insert(0, "year", last.index.year)
    return out.reset_index(drop=True)


def charge_loss_trend(loss: pd.DataFrame, config: RecordConfig | None = None, level: float = 0.90) -> dict:
    """The evolution of the apparent charge loss: a weighted straight line through its year-end values.

    ``loss`` is the output of ``charge_loss`` (at month ends, or at any times that
    include the year ends). Weights: ``1 / charge_loss_sigma_ma**2``, the statistical
    part of each value's band. The gain error is left out of the weights because it moves
    every value by the same factor; for the same reason it is added to the slope's band
    as a systematic term. The weights set only the relative size of the errors; the
    slope's standard error is scaled by the scatter of the values about the line (the
    reduced chi-square), and its band uses a Student-t quantile with ``n - 2`` degrees of
    freedom. The year-end values are treated as independent; with a memory time of half a
    year, neighbouring values share about ``exp(-365 / tau_days)`` of their weight, which
    this band does not include.

    Needs ``config.charge_loss_min_years`` year-end values. Returns ``year_end`` (the
    table of ``year_end_values``), ``slope_ma_per_year`` with ``_low``, ``_high`` and
    ``slope_sigma_ma_per_year``, ``intercept_ma`` (the line's value at the first year-end
    time ``t0``), ``n_years``, ``dof``, ``span_years``, ``status`` and ``config_hash``.
    """
    config = config or RecordConfig()
    table = year_end_values(loss)
    n = len(table)
    out = {
        "year_end": table,
        "slope_ma_per_year": math.nan,
        "slope_ma_per_year_low": math.nan,
        "slope_ma_per_year_high": math.nan,
        "slope_sigma_ma_per_year": math.nan,
        "intercept_ma": math.nan,
        "t0": table["time"].iloc[0] if n else pd.NaT,
        "n_years": n,
        "dof": float(max(n - 2, 0)),
        "span_years": 0.0,
        "status": _core.STATUS_TEXT[_core.OK],
        "config_hash": config_hash(config),
    }
    need = max(int(config.charge_loss_min_years), 3)  # a line with a band needs three points
    if n < need:
        out["status"] = f"not possible from this data: fewer than {need} year-end values"
        return out
    sig = table["charge_loss_sigma_ma"].to_numpy(float)
    if not (np.all(np.isfinite(sig)) and np.all(sig > 0.0)):
        out["status"] = "not possible from this data: a year-end value has no positive standard error"
        return out
    years = (pd.DatetimeIndex(table["time"]) - table["time"].iloc[0]).total_seconds().to_numpy() / (365.25 * 86400.0)
    y = table["charge_loss_ma"].to_numpy(float)
    w = 1.0 / sig**2
    sw = w.sum()
    tm = (w * years).sum() / sw
    ym = (w * y).sum() / sw
    stt = (w * (years - tm) ** 2).sum()
    out["span_years"] = float(years[-1] - years[0])
    if stt <= 0.0:
        out["status"] = "not possible from this data: the year-end values do not span time"
        return out
    slope = float((w * (years - tm) * (y - ym)).sum() / stt)
    intercept = float(ym - slope * tm)
    dof = n - 2
    chi2 = float((w * (y - intercept - slope * years) ** 2).sum())
    stat = math.sqrt(chi2 / dof / stt)
    z = _z(level)
    k = _core.t_quantile(z, float(dof))
    half = math.sqrt((k * stat) ** 2 + (z * config.charge_sigma * slope) ** 2)
    out.update(
        slope_ma_per_year=slope,
        slope_ma_per_year_low=slope - half,
        slope_ma_per_year_high=slope + half,
        slope_sigma_ma_per_year=stat,
        intercept_ma=intercept,
        dof=float(dof),
    )
    return out
