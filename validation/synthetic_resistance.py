"""Check W2 on one synthetic pack: the 1-second resistance and its growth.

Called by ``synthetic_phase3.py`` with one simulated seed (``check(run) -> dict``). The
estimate reads pack A (``run.reduced``: current gain error within 0.5 %, voltage gain
error within 0.25 %, sensor noise) with the current settings (``RecordConfig()``), no
mask, in the headline band (22.5 to 27.5 degrees C).

What the truth is. The estimator measures the pack's own response over one sampling
interval: the voltage step that follows a current step between two consecutive samples.
In the simulator (``tests/synth.py``) that step is the ohmic drop ``r_ohm x scale`` of the
day, plus what the open-circuit voltage and the RC element did over the sample before
the step. The second part is not the ohmic resistance, but it is what any 1-sample
measurement of this pack sees, so it belongs to the truth (on these packs it makes the
response about 2 % lower than the ohmic resistance; ``w2_value_ohmic`` reports the ohmic
value next to it). So the truth is the same estimator on the pack's own signals
(``run.reduced_true``: the same operation, no gain error, no noise): its qualifying steps,
grouped into the same day-band events, over the same days the estimate uses, with the same
weights ``exp(-age / tau_days)``. Every day's step sizes and voltage steps are then those
of the pack itself, so the truth carries the day-to-day variation of the resistance with
the pack temperature within the band and with age. What differs between estimate and
truth is only what the sensors add: gain errors, noise, and the steps that noise moves
across the step filter's thresholds. This is the check the band must pass: its
systematic term stands for the gain errors and its statistical part for the rest. The
check is not circular: the estimate never sees the true signals, and the truth uses
neither the measured signals nor the band.

The truth so defined is the pack's response at the weighted mean age of the days in
memory; with ``tau_days`` of memory the estimate lags a growing resistance by about
``tau_days``. ``w2_value_truth_at_date`` moves the truth to the date by the known growth
factor ``1 + r_growth_per_year x age`` (the ratio of that factor at the date to its
weighted mean over the same days and weights); its coverage is information, not the
check. ``w2_value_truth_25c`` is the same truth with each day's sums divided by that day's
temperature factor (below): the pack's response at 25 degrees C over the same days, also
information.

The growth. The fit ``R = k0 + k1 y`` runs over every day of the headline band, with
``y`` in years from the first such day; the reported growth is ``100 k1 / k0`` per year.
The simulator's resistance falls with the day's mean temperature (1 % per degree), and
the days in the band are not spread evenly over the seasons, so part of a fitted slope
can be temperature, which is not growth. The truth therefore is the same growth fit on
the pack's own signals with each day's sums ``b`` divided by that day's temperature
factor ``exp(-r_temp_coeff_per_c (T_day - 25))``: the pack's 1-sample response at a
fixed temperature, as it ages. (This divides the small open-circuit part of the response
by the factor too; that part is about 2 % of the response, and the factor stays within a
few per cent of 1 in the band, so the error is far below the band.) The simulator's own
rate at the fit's origin, ``100 r_growth_per_year / (1 + r_growth_per_year x age0)``, is
reported next to it (``w2_growth_truth_param``, with ``w2_growth_held_param``; it shares
no day with the estimate, unlike the truth above). The estimate does not correct for
temperature: if the temperature within the band moves a slope, the band has to hold it.

``*_held`` is True when the 90 % band holds the truth; an estimate without a band is not
held. The value is taken at the end of the last day of the record.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.soh import _core, _ratio
from besslib.soh.capacity import days
from besslib.soh.config import CapacityConfig, RecordConfig
from besslib.soh.resistance import day_band_events, estimate_resistance, resistance_growth

OK = _core.STATUS_TEXT[_core.OK]
TEMP_REF_C = 25.0


def end_of_record(reduced) -> pd.Timestamp:
    """Midnight after the record's last day."""
    return pd.Timestamp(reduced.daily["day"].max()) + pd.Timedelta(days=1)


def pack_day(run, day) -> np.ndarray:
    """Days since the pack's start (0 = its first day), from midnight timestamps."""
    return ((pd.DatetimeIndex(day) - pd.Timestamp(run.pack.start)).total_seconds() / 86400.0).to_numpy()


def headline_events(run, reduced, config: RecordConfig) -> pd.DataFrame:
    events, _ = day_band_events(reduced.steps, run.spec, config, TEMP_REF_C)
    return events[events["temp_band_c"] == TEMP_REF_C].set_index("day")


def check_value(run, config: RecordConfig, capacity_config: CapacityConfig) -> dict:
    at = end_of_record(run.reduced)
    est = estimate_resistance(None, run.spec, TEMP_REF_C, config.level, config=config, capacity_config=capacity_config,
                              reduced=run.reduced, at=[at])
    row = est[est["headline"]].iloc[0]
    measured = headline_events(run, run.reduced, config)
    own = headline_events(run, run.reduced_true, config)
    same = measured.index.intersection(own.index)
    tq = float(days([at])[0])
    lam = np.exp(-(tq - own.loc[same, "t_days"].to_numpy(float)) / config.tau_days)
    a_true = own.loc[same, "a"].to_numpy(float)
    b_true = own.loc[same, "b"].to_numpy(float)
    truth = float(np.sum(lam * b_true) / np.sum(lam * a_true))
    age = pack_day(run, same)
    scale = run.pack.resistance_scale(age)
    ohmic = float(run.pack.r_ohm * np.sum(lam * a_true * scale) / np.sum(lam * a_true))
    growth = 1.0 + run.pack.r_growth_per_year * age / 365.25
    mean_growth = float(np.sum(lam * a_true * growth) / np.sum(lam * a_true))
    at_growth = 1.0 + run.pack.r_growth_per_year * float(pack_day(run, [at - pd.Timedelta(days=1)])[0]) / 365.25
    truth_at_date = truth * at_growth / mean_growth
    temp_factor = np.exp(-run.pack.r_temp_coeff_per_c * (run.pack.day_temperature_c(age) - 25.0))
    truth_25c = float(np.sum(lam * b_true / temp_factor) / np.sum(lam * a_true))
    ok = row["status"] == OK
    value, low, high = float(row["resistance_ohm"]), float(row["resistance_ohm_low"]), float(row["resistance_ohm_high"])
    # The two parts of the band, for the report: the statistical half-width alone and the systematic one.
    stat = _ratio.estimate_ratio(
        measured["t_days"].to_numpy(float), measured["a"].to_numpy(float), measured["b"].to_numpy(float),
        np.zeros(len(measured), dtype=int), [tq], config.tau_days, config.level, config.resistance_min_days, 0.0,
        math.inf,
    ).iloc[0]
    z = NormalDist().inv_cdf(0.5 + config.level / 2.0)
    return {
        "w2_value": value,
        "w2_value_low": low,
        "w2_value_high": high,
        "w2_value_stat_half": float(stat["high"] - stat["value"]) if ok else math.nan,
        "w2_value_sys_half": z * config.resistance_sigma * value if ok else math.nan,
        "w2_value_truth": truth,
        "w2_value_held": bool(ok and low <= truth <= high),
        "w2_value_error_rel": value / truth - 1.0 if ok else math.nan,
        "w2_value_ohmic": ohmic,
        "w2_value_truth_at_date": truth_at_date,
        "w2_value_held_at_date": bool(ok and low <= truth_at_date <= high),
        "w2_value_truth_25c": truth_25c,
        "w2_value_held_25c": bool(ok and low <= truth_25c <= high),
        "w2_status": row["status"],
        "w2_n_days": float(row["n_days"]),
        "w2_n_steps": float(row["n_steps"]),
        "w2_days_matched": int(len(same)),
        "w2_days_measured_only": int(len(measured.index.difference(own.index))),
        "w2_days_true_only": int(len(own.index.difference(measured.index))),
        "w2_time": str(at),
    }


def check_growth(run, config: RecordConfig, capacity_config: CapacityConfig) -> dict:
    at = end_of_record(run.reduced)
    est = resistance_growth(None, run.spec, TEMP_REF_C, config.level, config=config, capacity_config=capacity_config,
                            reduced=run.reduced, at=at)
    own = headline_events(run, run.reduced_true, config)
    tq = float(days([at])[0])
    own = own[own["t_days"] <= tq]
    t = own["t_days"].to_numpy(float)
    temp_factor = np.exp(-run.pack.r_temp_coeff_per_c * (run.pack.day_temperature_c(pack_day(run, own.index)) - 25.0))
    fit = _ratio.estimate_growth(
        t, own["a"].to_numpy(float), own["b"].to_numpy(float) / temp_factor, np.zeros(len(own), dtype=int), float(t[0]),
        tq, config.level, config.resistance_min_days, config.growth_min_span_years, 0.0, math.inf,
    ) if len(own) else {"status": _core.TOO_FEW_EVENTS}
    truth = 100.0 * fit["k1"] / fit["k0"] if fit["status"] == _core.OK else math.nan
    age0 = float(pack_day(run, [own.index[0]])[0]) if len(own) else math.nan
    g = run.pack.r_growth_per_year
    ok = est["status"] == OK
    value = est["growth_percent_per_year"]
    low, high = est["growth_percent_per_year_low"], est["growth_percent_per_year_high"]
    return {
        "w2_growth": value,
        "w2_growth_low": low,
        "w2_growth_high": high,
        "w2_growth_truth": truth,
        "w2_growth_truth_param": 100.0 * g / (1.0 + g * age0 / 365.25),
        "w2_growth_held_param": bool(ok and low <= 100.0 * g / (1.0 + g * age0 / 365.25) <= high),
        "w2_growth_held": bool(ok and np.isfinite(truth) and low <= truth <= high),
        "w2_growth_status": est["status"],
        "w2_growth_n_days": est["n_days"],
        "w2_growth_span_years": est["span_years"],
        "w2_power_share": est["power_capability_share"],
        "w2_power_share_low": est["power_capability_share_low"],
        "w2_power_share_high": est["power_capability_share_high"],
    }


def check(run) -> dict:
    config = RecordConfig()
    capacity_config = CapacityConfig()
    out = check_value(run, config, capacity_config)
    out.update(check_growth(run, config, capacity_config))
    return out
