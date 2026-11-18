"""The 1-second resistance of the pack from the current steps of normal operation, and its growth.

"1-second" names the field data's sampling interval: the value is the resistance over one
sampling interval of the record, whatever that interval is, and every output carries it.

Annex VII of Regulation (EU) 2023/1542 asks, "where possible", for the ohmic resistance
and its increase. besslib measures a DC resistance the way a current-step test does
(Barai et al., Sci. Rep. 8, 21 (2018), doi:10.1038/s41598-017-18424-5; Schweiger et al.,
Sensors 10, 5604 (2010), doi:10.3390/s100605604): the change of the terminal voltage
divided by the change of the current, over one sampling interval. Normal operation
holds many such steps (the inverter follows the load and the sun), so no test is needed.

A *current step* is a row of the step table (``besslib.soh.reduce``): two consecutive
samples whose current differs by at least ``min_step_c_rate`` x the nominal capacity,
after ``steady_s`` seconds in which the current stayed within ``steady_tolerance`` x the
step of its value before the step. ``dI`` and ``dV`` are the value after minus the value
before. Steps inside a ``mask`` interval (for example a capacity test) are left out.

Event: one calendar day in one pack-temperature band, ``a = sum dI^2``, ``b = sum dI dV``
over its steps, at the end of that day. Bands are ``temp_band_c`` wide and centred on
``temp_ref_c + n temp_band_c``; a step's band is that of its pack temperature (lower
edge included). Steps without a temperature reading are counted and left out.

Estimate in each band: ``R = sum lambda b / sum lambda a`` over the days in memory,
``lambda = exp(-age / tau_days)``: the least-squares slope of dV on dI through the
origin, over all steps of those days. Its statistical band is the sandwich of the
through-origin ratio fit (``besslib.soh._core``) on the day scores ``b - R a``; days are
not linked (no two days share a measurement), so it is the heteroskedasticity-consistent
form. The sensors' gain errors are added as a systematic term
(``RecordConfig.resistance_sigma``): a gain error moves every step of the record the same
way, so averaging does not reduce it. Half-width:
``sqrt((t sigma_stat)^2 + (z resistance_sigma R)^2)``. No model of temperature is
applied; each band stands for itself, and ``headline`` marks the band centred on
``temp_ref_c``.

Limits. At a sampling interval of about one second the value holds the ohmic part and
a part of the charge-transfer resistance: Barai et al. (2018) measured 1.35 mOhm at
0.1 s and 1.61 mOhm at 1 s on the same 20 Ah LFP cell. It is therefore not the ohmic
resistance of an impedance spectrum, and values from records with different sampling
intervals are not comparable; every output carries the interval (``sample_s``). It is
an average over the states of charge and the currents the system visits in its band
(the resistance depends on both, Schweiger et al. 2010), not a value at a fixed state
of charge. With memory time ``tau_days`` it lags a growing resistance by about
``tau_days``.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from besslib.frames import PackSpec
from besslib.soh import _core, _ratio
from besslib.soh.capacity import days
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.reduce import LOOSEST_MIN_STEP_C_RATE, LOOSEST_STEADY_S, STEADY_CAP_S, STEADY_TOLERANCE, reduce_frame
from besslib.soh.rests import operating_rests

#: Columns of the day-band event table.
EVENT_COLUMNS = ["day", "temp_band_c", "t_days", "a", "b", "n_steps", "n_unrefreshed", "resistance_ohm"]

#: Columns of the estimate table.
RESISTANCE_COLUMNS = [
    "time", "temp_band_c", "headline", "resistance_ohm", "resistance_ohm_low", "resistance_ohm_high",
    "n_days", "n_steps", "status",
]

def power_label(sample_s: float) -> str:
    """The label of every power capability value, with the record's sampling interval."""
    return f"derived from the {sample_s:g}-second resistance, not measured"

_OK = _core.STATUS_TEXT[_core.OK]
_NOT = "not possible from this data: "


def _z(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def _check_setting(config: RecordConfig) -> None:
    """The step table holds the steps of the loosest setting only, with a fixed steady tolerance."""
    if config.min_step_c_rate < LOOSEST_MIN_STEP_C_RATE:
        raise ValueError(f"min_step_c_rate below {LOOSEST_MIN_STEP_C_RATE}: the step table does not hold those steps")
    if not LOOSEST_STEADY_S <= config.steady_s <= STEADY_CAP_S:
        raise ValueError(f"steady_s must lie within {LOOSEST_STEADY_S} to {STEADY_CAP_S} s for the step table")
    if config.steady_tolerance != STEADY_TOLERANCE:
        raise ValueError(f"the step table was made with steady_tolerance {STEADY_TOLERANCE}")


def qualifying_steps(
    steps: pd.DataFrame, spec: PackSpec, config: RecordConfig | None = None, mask=None
) -> pd.DataFrame:
    """The rows of a step table that qualify for ``(config.min_step_c_rate, config.steady_s)``,
    outside every ``mask`` interval, with ``d_current_a`` and ``d_voltage_v`` (after - before)."""
    config = config or RecordConfig()
    _check_setting(config)
    d_i = (steps["current_after_a"] - steps["current_before_a"]).to_numpy(float)
    d_v = (steps["voltage_after_v"] - steps["voltage_before_v"]).to_numpy(float)
    keep = (
        (np.abs(d_i) >= config.min_step_c_rate * spec.nominal_capacity_ah)
        & (steps["steady_s"].to_numpy(float) >= config.steady_s)
        & np.isfinite(d_i)
        & np.isfinite(d_v)
    )
    time = pd.DatetimeIndex(steps["time"])
    for begin, end in mask or []:
        keep &= ~((time >= pd.Timestamp(begin)) & (time <= pd.Timestamp(end)))
    out = steps.loc[keep].copy()
    out["d_current_a"] = d_i[keep]
    out["d_voltage_v"] = d_v[keep]
    return out


def band_centre(temp_c, temp_ref_c: float = 25.0, width_c: float = 5.0) -> np.ndarray:
    """The centre of the temperature band of each temperature: ``temp_ref_c + n width_c``,
    with the band ``[centre - width_c / 2, centre + width_c / 2)``. NaN stays NaN."""
    t = np.asarray(temp_c, dtype=float)
    return temp_ref_c + width_c * np.floor((t - temp_ref_c + 0.5 * width_c) / width_c)


def day_band_events(
    steps: pd.DataFrame, spec: PackSpec, config: RecordConfig | None = None, temp_ref_c: float = 25.0, mask=None
) -> tuple[pd.DataFrame, dict]:
    """The events of the estimator: one row per calendar day and temperature band.

    Columns ``day``, ``temp_band_c`` (band centre), ``t_days`` (the end of the day, days
    since 2000-01-01), ``a = sum dI^2``, ``b = sum dI dV``, ``n_steps``,
    ``n_unrefreshed`` (steps whose voltage did not change at all) and the day's own
    ``resistance_ohm = b / a``.

    A band-day is kept only when at most ``config.unrefreshed_max_share`` of its steps
    show no voltage change: a current step of at least ``min_step_c_rate`` x C moves the
    terminal voltage by far more than the sensor's resolution, so an exact zero means the
    voltage was not refreshed together with the current, and the day's ratio would mix
    real steps with steps that measure nothing.

    Also returns a dict of counts: ``n_steps`` (qualifying, with a temperature),
    ``n_steps_no_temperature`` and ``n_steps_masked`` (left out), ``n_days_unrefreshed``
    and ``n_steps_unrefreshed_days`` (band-days left out by the rule and their steps),
    ``unrefreshed_share_by_band`` (band centre -> share of its qualifying steps without
    voltage change, over the record) and ``dropped`` (the band-days left out, same columns).
    """
    config = config or RecordConfig()
    every = qualifying_steps(steps, spec, config)
    use = qualifying_steps(steps, spec, config, mask)
    temp = use["temp_pack_c"].to_numpy(float)
    has_temp = np.isfinite(temp)
    counts = {
        "n_steps": int(has_temp.sum()),
        "n_steps_no_temperature": int((~has_temp).sum()),
        "n_steps_masked": int(len(every) - len(use)),
    }
    use = use.loc[has_temp]
    frame = pd.DataFrame(
        {
            "day": pd.DatetimeIndex(use["time"]).normalize(),
            "temp_band_c": band_centre(use["temp_pack_c"].to_numpy(float), temp_ref_c, config.temp_band_c),
            "a": use["d_current_a"].to_numpy(float) ** 2,
            "b": (use["d_current_a"] * use["d_voltage_v"]).to_numpy(float),
            "n_steps": 1,
            "n_unrefreshed": (use["d_voltage_v"].to_numpy(float) == 0.0).astype(int),
        }
    )
    events = frame.groupby(["day", "temp_band_c"], sort=True, as_index=False).sum()
    events["t_days"] = days(events["day"]) + 1.0
    events["resistance_ohm"] = events["b"] / events["a"]
    events = events[EVENT_COLUMNS]
    kept = (events["n_unrefreshed"] <= config.unrefreshed_max_share * events["n_steps"]).to_numpy()
    by_band = events.groupby("temp_band_c")[["n_unrefreshed", "n_steps"]].sum()
    counts.update(
        n_days_unrefreshed=int((~kept).sum()),
        n_steps_unrefreshed_days=int(events.loc[~kept, "n_steps"].sum()),
        unrefreshed_share_by_band={float(c): float(r["n_unrefreshed"] / r["n_steps"]) for c, r in by_band.iterrows()},
        dropped=events.loc[~kept].reset_index(drop=True),
    )
    return events.loc[kept].reset_index(drop=True), counts


def _refresh_status(kept: pd.DataFrame, dropped: pd.DataFrame, tq: float, tau_days: float, min_days: float):
    """The refresh status of a band whose kept days in memory are too few, or None.

    The rule is the reason when the band-days it left out would have brought the band to
    ``min_days`` (decayed counts with memory ``tau_days``; no forgetting for 0). X is the
    share of steps without voltage change among all qualifying steps of the band in
    memory, with the same weights.
    """
    def weighted(table):
        t = table["t_days"].to_numpy(float)
        use = t <= tq
        lam = np.exp(-(tq - t[use]) / tau_days) if tau_days > 0 else np.ones(int(use.sum()))
        return (float(lam.sum()), float((lam * table["n_steps"].to_numpy(float)[use]).sum()),
                float((lam * table["n_unrefreshed"].to_numpy(float)[use]).sum()))

    k_days, k_steps, k_unref = weighted(kept)
    d_days, d_steps, d_unref = weighted(dropped)
    if d_days <= 0.0 or k_days >= min_days or k_days + d_days < min_days:
        return None
    return unrefreshed_text((k_unref + d_unref) / (k_steps + d_steps))


def unrefreshed_text(share: float) -> str:
    """The status of an estimate the voltage-refresh rule made impossible."""
    share_text = f"{100.0 * share:.1f} % of steps show no voltage change"
    return f"{_NOT}the voltage is not refreshed with the current ({share_text})"


def _status(code: int, centre: float) -> str:
    if code == _core.OK:
        return _OK
    if code == _core.TOO_FEW_EVENTS:
        return f"{_NOT}too few current steps near {centre:g} °C"
    if code == _core.OUT_OF_RANGE:
        return f"{_NOT}the current steps near {centre:g} °C give a resistance at or below zero"
    if code == _core.SHORT_SPAN:
        return f"{_NOT}the days with current steps near {centre:g} °C span too short a time for a growth rate"
    return _core.STATUS_TEXT.get(code, f"{_NOT}status {code}")


def _systematic_half(stat_half: float, value: float, level: float, config: RecordConfig) -> float:
    """``sqrt(stat_half^2 + (z resistance_sigma value)^2)``: the statistical half-width plus
    the sensors' gain errors as a systematic term."""
    return math.hypot(stat_half, _z(level) * config.resistance_sigma * value)


def _reduced(frame, spec, reduced, capacity_config):
    if reduced is not None:
        return reduced
    if frame is None:
        raise ValueError("give a frame or reduced tables")
    return reduce_frame(frame, spec, capacity_config)


def estimate_resistance(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    temp_ref_c: float = 25.0,
    level: float = 0.90,
    *,
    config: RecordConfig | None = None,
    capacity_config: CapacityConfig | None = None,
    reduced=None,
    at=None,
    mask=None,
) -> pd.DataFrame:
    """The 1-second resistance with its band, per temperature band, at each time in ``at`` (module docstring).

    Parameters
    ----------
    frame
        Operating data in the besslib frame format; may be ``None`` when ``reduced`` is given.
    spec
        The pack; its nominal capacity sets the smallest current step.
    temp_ref_c
        Centre of the headline band.
    level
        Band level.
    config, capacity_config
        Settings of the record and of the reduction; defaults are the frozen ones.
    reduced
        A ``reduce.Reduced`` of the record (its step table is used).
    at
        Times of the estimate. Default: the end of every calendar month in the data.
    mask
        Intervals ``(start, end)`` whose steps are left out.

    Returns
    -------
    DataFrame, one row per time and temperature band (every band with at least one day,
    and always the headline band): ``time``, ``temp_band_c`` (band centre), ``headline``,
    ``resistance_ohm`` with ``_low`` and ``_high`` (statistical and systematic part),
    ``n_days`` and ``n_steps`` (in memory, decayed), ``status``. A value is given only
    when the status is ``estimated``. When a band has too few days in memory because the
    voltage-refresh rule left band-days out (they would have brought it to
    ``resistance_min_days``), the status says so, with the share of steps without voltage
    change among the band's qualifying steps in memory (decayed weights). ``attrs``:
    ``config_hash``, ``capacity_config_hash``, ``sample_s``, ``events`` (the kept
    day-band table) and the counts of ``day_band_events`` (``dropped``,
    ``n_days_unrefreshed``, ``unrefreshed_share_by_band``, ...).
    """
    config = config or RecordConfig()
    capacity_config = capacity_config or CapacityConfig()
    reduced = _reduced(frame, spec, reduced, capacity_config)
    events, counts = day_band_events(reduced.steps, spec, config, temp_ref_c, mask)
    if at is None:
        known = pd.DatetimeIndex(reduced.daily["day"]) if len(reduced.daily) else pd.DatetimeIndex([])
        if len(known) == 0:
            raise ValueError("no data to estimate from")
        at = pd.date_range(known.min(), known.max() + pd.Timedelta(days=1), freq="ME")
    at = pd.DatetimeIndex(at)
    at_days = days(at)
    dropped = counts["dropped"]
    bands = sorted(set(events["temp_band_c"].tolist()) | set(dropped["temp_band_c"].tolist()) | {float(temp_ref_c)})
    rows = []
    for centre in bands:
        sel = events[events["temp_band_c"] == centre]
        sel_dropped = dropped[dropped["temp_band_c"] == centre]
        t = sel["t_days"].to_numpy(float)
        n_steps = sel["n_steps"].to_numpy(float)
        est = _ratio.estimate_ratio(
            t, sel["a"].to_numpy(float), sel["b"].to_numpy(float), np.zeros(len(sel), dtype=int), at_days,
            config.tau_days, level, config.resistance_min_days, 0.0, math.inf,
        )
        for k, (when, tq) in enumerate(zip(at, at_days)):
            e = est.iloc[k]
            use = t <= tq
            lam = np.exp(-(tq - t[use]) / config.tau_days) if config.tau_days > 0 else np.ones(int(use.sum()))
            row = {
                "time": when, "temp_band_c": float(centre), "headline": bool(centre == float(temp_ref_c)),
                "resistance_ohm": math.nan, "resistance_ohm_low": math.nan, "resistance_ohm_high": math.nan,
                "n_days": float(e["n_events"]), "n_steps": float((lam * n_steps[use]).sum()),
                "status": _status(int(e["status"]), centre),
            }
            if int(e["status"]) == _core.TOO_FEW_EVENTS:
                refresh = _refresh_status(sel, sel_dropped, tq, config.tau_days, config.resistance_min_days)
                row["status"] = refresh or row["status"]
            if int(e["status"]) == _core.OK:
                r = float(e["value"])
                half = _systematic_half(float(e["high"]) - r, r, level, config)
                row.update(resistance_ohm=r, resistance_ohm_low=r - half, resistance_ohm_high=r + half)
            rows.append(row)
    out = pd.DataFrame(rows, columns=RESISTANCE_COLUMNS)
    out.attrs.update(
        config_hash=config_hash(config),
        capacity_config_hash=config_hash(capacity_config),
        sample_s=float(reduced.sample_s),
        events=events,
        **counts,
    )
    return out


def _delta_half(grad, cov, k_t: float) -> float:
    """``k_t sqrt(grad' cov grad)``; NaN when that variance is negative (the sandwich is not
    positive by construction)."""
    var = 0.0
    for r in range(2):
        for s in range(2):
            var += grad[r] * cov[r][s] * grad[s]
    return k_t * math.sqrt(var) if var >= 0.0 else math.nan


def resistance_growth(
    frame: pd.DataFrame | None,
    spec: PackSpec,
    temp_ref_c: float = 25.0,
    level: float = 0.90,
    *,
    config: RecordConfig | None = None,
    capacity_config: CapacityConfig | None = None,
    reduced=None,
    at=None,
    mask=None,
) -> dict:
    """The growth of the 1-second resistance over the whole record, and the remaining power capability.

    Fit: ``R = k0 + k1 y`` over every kept day of the headline band (``day_band_events``:
    band-days whose voltage was not refreshed with the current are left out) up to ``at`` (default: the
    end of the record's last day), no forgetting, ``y`` in years since the first day with
    a step in that band (the through-origin growth fit of ``besslib.soh._core``: ``b =
    (k0 + k1 y) a``, the lag-1 sandwich without links, Student-t quantile). It needs days
    spanning ``growth_min_span_years``.

    ``growth_percent_per_year = 100 k1 / k0``, the growth as a share of the start value.
    ``power_capability_share = k0 / (k0 + k1 y_at)``: the share of the start power
    capability that remains at ``at``, *derived from the resistance over one sampling
    interval, not measured* (``power_capability_label`` names the interval). Reason:
    with an ohmic model the power a pack can give at a fixed voltage limit is ``V_lim
    (OCV - V_lim) / R``, proportional to ``1 / R`` (Regulation (EU) 2023/1542, Annex IV
    (4), defines the power fade by that power). Both bands are by
    the delta method from the fit's covariance, with the t quantile; a negative
    delta-method variance gives no band (status). A constant gain error of either sensor
    scales ``k0`` and ``k1`` by the same factor and cancels in both ratios, so no
    systematic term is added to them.

    Both ratios are linearised around their denominators, ``k0`` and ``R_at = k0 + k1
    y_at``. When the band of either reaches zero or below, the ratio has no finite
    variance and its linear band means nothing (a share band below zero, a growth band of
    thousands of per cent); the status is then *not possible*, with the reason, and no
    ratio is given. This happens when a straight line is a poor model of the record, for
    example when the resistance triples, or when the voltage signal changes its timing
    within the record.

    ``power_capability_w = V_min (OCV - V_min) / R_at`` with ``V_min = spec.voltage_min_v``
    (the pack's lower voltage limit), ``R_at = k0 + k1 y_at``, and the open-circuit
    voltage of the last ``full`` rest before ``at`` (``rests.operating_rests``): the
    discharge power at full charge at which the terminal voltage would reach the limit.
    It has its own status; without ``spec.voltage_min_v`` it is not computed. Its band
    holds the statistical part of ``R_at`` (t quantile), the open-circuit voltage's own
    uncertainty (``ocv_sigma_v``, normal quantile) and the gain errors as a systematic
    term: relative ``sqrt(sigma_I^2 + (sigma_V V_min / (OCV - V_min))^2)``, because a
    voltage gain error scales the open-circuit voltage and the resistance alike.

    Returns a dict: ``resistance_start_ohm`` (``k0``), ``resistance_at_ohm``,
    ``growth_percent_per_year`` with ``_low`` and ``_high``, ``power_capability_share``
    with ``_low`` and ``_high``, ``power_capability_w`` with ``_low``, ``_high`` and
    ``power_capability_w_status``, ``power_capability_label``, ``temp_band_c``,
    ``first_day``, ``at``, ``n_days``, ``n_days_unrefreshed`` (band-days left out by the
    voltage-refresh rule), ``span_years``, ``dof``, ``status``, ``sample_s``,
    ``config_hash``, ``capacity_config_hash``.
    """
    config = config or RecordConfig()
    capacity_config = capacity_config or CapacityConfig()
    reduced = _reduced(frame, spec, reduced, capacity_config)
    events, counts = day_band_events(reduced.steps, spec, config, temp_ref_c, mask)
    sel = events[events["temp_band_c"] == float(temp_ref_c)]
    sel_dropped = counts["dropped"][counts["dropped"]["temp_band_c"] == float(temp_ref_c)]
    nan = math.nan
    out = {
        "resistance_start_ohm": nan, "resistance_at_ohm": nan,
        "growth_percent_per_year": nan, "growth_percent_per_year_low": nan, "growth_percent_per_year_high": nan,
        "power_capability_share": nan, "power_capability_share_low": nan, "power_capability_share_high": nan,
        "power_capability_w": nan, "power_capability_w_low": nan, "power_capability_w_high": nan,
        "power_capability_w_status": f"{_NOT}no growth fit",
        "power_capability_label": power_label(float(reduced.sample_s)),
        "temp_band_c": float(temp_ref_c), "first_day": pd.NaT, "at": pd.NaT,
        "n_days": 0.0, "n_days_unrefreshed": 0, "span_years": 0.0, "dof": 0.0,
        "status": _status(_core.TOO_FEW_EVENTS, float(temp_ref_c)),
        "sample_s": float(reduced.sample_s),
        "config_hash": config_hash(config), "capacity_config_hash": config_hash(capacity_config),
    }
    if at is None:
        known = pd.DatetimeIndex(reduced.daily["day"]) if len(reduced.daily) else pd.DatetimeIndex([])
        if len(known) == 0:
            raise ValueError("no data to estimate from")
        at = known.max() + pd.Timedelta(days=1)
    at = pd.Timestamp(at)
    out["at"] = at
    tq = float(days([at])[0])
    sel = sel[sel["t_days"] <= tq]
    sel_dropped = sel_dropped[sel_dropped["t_days"] <= tq]
    out["n_days_unrefreshed"] = int(len(sel_dropped))
    refresh = _refresh_status(sel, sel_dropped, tq, 0.0, config.resistance_min_days)
    if len(sel) == 0:
        out["status"] = refresh or out["status"]
        return out
    t = sel["t_days"].to_numpy(float)
    t0 = float(t[0])
    out["first_day"] = pd.Timestamp(sel["day"].iloc[0])
    fit = _ratio.estimate_growth(
        t, sel["a"].to_numpy(float), sel["b"].to_numpy(float), np.zeros(len(sel), dtype=int), t0, tq, level,
        config.resistance_min_days, config.growth_min_span_years, 0.0, math.inf,
    )
    code = int(fit["status"])
    out.update(n_days=float(fit["n_events"]), span_years=float(fit["span_years"]), dof=float(fit["dof"]),
               status=_status(code, float(temp_ref_c)))
    if code == _core.TOO_FEW_EVENTS:
        out["status"] = refresh or out["status"]
    if code != _core.OK:
        return out
    k0, k1, cov = float(fit["k0"]), float(fit["k1"]), fit["cov"]
    y = (tq - t0) / 365.25
    r_at = k0 + k1 * y
    k_t = _core.t_quantile(_z(level), float(fit["dof"]))
    growth = k1 / k0
    growth_half = _delta_half((-k1 / k0**2, 1.0 / k0), cov, k_t)
    share = k0 / r_at
    share_half = _delta_half((k1 * y / r_at**2, -k0 * y / r_at**2), cov, k_t)
    k0_half = _delta_half((1.0, 0.0), cov, k_t)
    r_at_half = _delta_half((1.0, y), cov, k_t)
    if not (k0 - k0_half > 0.0 and r_at - r_at_half > 0.0):
        out["status"] = (f"{_NOT}the growth fit near {float(temp_ref_c):g} °C does not separate the resistance "
                         "at the start or at the date from zero")
        return out
    if math.isnan(growth_half) or math.isnan(share_half):
        out["status"] = _core.STATUS_TEXT[_core.NO_BAND]
        return out
    out.update(
        resistance_start_ohm=k0,
        resistance_at_ohm=r_at,
        growth_percent_per_year=100.0 * growth,
        growth_percent_per_year_low=100.0 * (growth - growth_half),
        growth_percent_per_year_high=100.0 * (growth + growth_half),
        power_capability_share=share,
        power_capability_share_low=share - share_half,
        power_capability_share_high=share + share_half,
    )
    out.update(_power_w(reduced, spec, capacity_config, config, mask, at, r_at, (1.0, y), cov, k_t, level))
    return out


def _power_w(reduced, spec, capacity_config, config, mask, at, r_at, grad, cov, k_t, level) -> dict:
    """``power_capability_w`` with its band and status (see ``resistance_growth``)."""
    nan = math.nan
    out = {"power_capability_w": nan, "power_capability_w_low": nan, "power_capability_w_high": nan}
    v_min = spec.voltage_min_v
    if v_min is None:
        out["power_capability_w_status"] = f"{_NOT}voltage limits not given"
        return out
    rests = operating_rests(reduced.rests, spec, capacity_config, mask)
    full = rests[(rests["kind"] == "full") & (rests["start"] <= at) & np.isfinite(rests["ocv_v"].to_numpy(float))]
    if len(full) == 0:
        out["power_capability_w_status"] = f"{_NOT}no full rest before the date"
        return out
    last = full.sort_values("start").iloc[-1]
    ocv = float(last["ocv_v"])
    ocv_sigma = float(last["ocv_sigma_v"]) if np.isfinite(last["ocv_sigma_v"]) else 0.0
    if not ocv > v_min:
        out["power_capability_w_status"] = f"{_NOT}the open-circuit voltage at full charge is not above the lower limit"
        return out
    power = v_min * (ocv - v_min) / r_at
    r_half = _delta_half(grad, cov, k_t)
    if math.isnan(r_half):
        out["power_capability_w_status"] = _core.STATUS_TEXT[_core.NO_BAND]
        return out
    z = _z(level)
    sigma_i = config.current_accuracy / math.sqrt(3.0)
    sigma_v = config.voltage_accuracy / math.sqrt(3.0)
    rel_sys = math.hypot(sigma_i, sigma_v * v_min / (ocv - v_min))
    half = math.sqrt((power / r_at * r_half) ** 2 + (z * v_min / r_at * ocv_sigma) ** 2 + (z * rel_sys * power) ** 2)
    out.update(
        power_capability_w=power,
        power_capability_w_low=power - half,
        power_capability_w_high=power + half,
        power_capability_w_status=_OK,
    )
    return out


def resistance_of_steps(steps: pd.DataFrame, spec: PackSpec, config: RecordConfig | None = None) -> dict:
    """The same estimator on any short window: ``R = sum dI dV / sum dI^2`` over the qualifying rows of ``steps``.

    For a window too short for days, such as the first minute of a capacity test. Each
    step is one event of the through-origin fit, so with two steps or more the
    statistical band is the heteroskedasticity-consistent sandwich over the steps, with
    the t quantile on ``n_steps - 1`` degrees of freedom; one step gives no scatter, and
    the band then holds the systematic term only (``band`` says which). The
    voltage-refresh rule of ``day_band_events`` applies to the window as to a day: when
    more than ``config.unrefreshed_max_share`` of its steps show no voltage change, no
    value is given. Returns ``resistance_ohm`` with ``_low`` and ``_high``, ``n_steps``,
    ``n_unrefreshed``, ``band`` (``"statistical and systematic"`` or ``"systematic only:
    one step"``) and ``status``.
    """
    config = config or RecordConfig()
    use = qualifying_steps(steps, spec, config)
    d_i = use["d_current_a"].to_numpy(float)
    d_v = use["d_voltage_v"].to_numpy(float)
    n = int(len(use))
    nan = math.nan
    n_unref = int((d_v == 0.0).sum())
    out = {"resistance_ohm": nan, "resistance_ohm_low": nan, "resistance_ohm_high": nan, "n_steps": n,
           "n_unrefreshed": n_unref, "band": ""}
    if n == 0:
        out["status"] = f"{_NOT}no qualifying current step"
        return out
    if n_unref > config.unrefreshed_max_share * n:
        out["status"] = unrefreshed_text(n_unref / n)
        return out
    a = d_i * d_i
    b = d_i * d_v
    r = float(b.sum() / a.sum())
    if not r > 0.0:
        out["status"] = f"{_NOT}the current steps give a resistance at or below zero"
        return out
    stat_half = 0.0
    out["band"] = "systematic only: one step"
    if n >= 2:
        # Ordered placeholder times: the fit has no forgetting here, so only the order matters.
        e = _ratio.estimate_ratio(np.arange(n, dtype=float), a, b, np.zeros(n, dtype=int), [float(n)], 0.0,
                                  config.level, 2.0, 0.0, math.inf).iloc[0]
        if int(e["status"]) != _core.OK:
            out["status"] = _status(int(e["status"]), nan)
            return out
        stat_half = float(e["high"]) - r
        out["band"] = "statistical and systematic"
    half = _systematic_half(stat_half, r, config.level, config)
    out.update(resistance_ohm=r, resistance_ohm_low=r - half, resistance_ohm_high=r + half, status=_OK)
    return out
