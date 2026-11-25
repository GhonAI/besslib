"""Tests of the apparent charge loss and its trend."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from besslib.soh import _core
from besslib.soh.capacity import _z, estimate_capacity
from besslib.soh.charge_loss import HOURS_PER_MONTH, NO_OFFSET_BAND, charge_loss, charge_loss_trend
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.rests import detect_rests_in_parts, levels_from, operating_rests, rest_chain, rest_pairs
from synth import SynthPack, frames

PACK = SynthPack()
SPEC = PACK.spec()
OK = _core.STATUS_TEXT[_core.OK]
# Four months of a pack without seasons: a 60-day level window and no warm-up.
SHORT = CapacityConfig(level_window_days=60.0, level_warmup_days=0.0)


def capacity_table(rows) -> pd.DataFrame:
    index = pd.DatetimeIndex([r[0] for r in rows], name="time")
    return pd.DataFrame(
        {
            "offset_current_a": [r[1] for r in rows],
            "offset_current_sigma_a": [r[2] for r in rows],
            "n_pairs": [r[3] for r in rows],
            "status": [r[4] for r in rows],
        },
        index=index,
    )


def test_sign_band_and_units():
    cap = capacity_table([("2021-01-31", -0.05, 0.002, 40.0, OK), ("2021-02-28", 0.01, 0.002, 40.0, OK)])
    out = charge_loss(cap, SPEC)
    drained, offset = out.iloc[0], out.iloc[1]
    assert drained["charge_loss_ma"] == pytest.approx(50.0)
    # A sensor offset can have either sign: a negative loss stays estimated.
    assert offset["charge_loss_ma"] == pytest.approx(-10.0) and offset["status"] == OK
    z = _z(0.90)
    k = _core.t_quantile(z, 38.0)
    half = math.hypot(k * 2.0, z * RecordConfig().charge_sigma * 50.0)
    assert drained["charge_loss_ma_high"] - drained["charge_loss_ma"] == pytest.approx(half)
    assert drained["charge_loss_ma"] - drained["charge_loss_ma_low"] == pytest.approx(half)
    per_month = 0.05 * HOURS_PER_MONTH / SPEC.reference_capacity_ah * 100.0
    assert drained["charge_loss_percent_per_month"] == pytest.approx(per_month)
    assert drained["charge_loss_percent_per_month_low"] == pytest.approx((50.0 - half) * per_month / 50.0)
    assert out.attrs["config_hash"] == config_hash(RecordConfig())


def test_statuses_pass_through_and_nan_sigma():
    warm = _core.STATUS_TEXT[_core.NO_LEVELS]
    cap = capacity_table(
        [("2021-01-31", np.nan, np.nan, 0.0, warm), ("2021-02-28", -0.05, np.nan, 40.0, OK), ("2021-03-31", -0.05, 0.001, 40.0, OK)]
    )
    out = charge_loss(cap, SPEC)
    assert list(out["status"]) == [warm, NO_OFFSET_BAND, OK]
    assert out.iloc[:2][["charge_loss_ma", "charge_loss_ma_low", "charge_loss_ma_high"]].isna().all().all()


def loss_table(values, sigmas, times) -> pd.DataFrame:
    cap = capacity_table([(t, -v / 1000.0, s / 1000.0, 50.0, OK) for t, v, s in zip(times, values, sigmas)])
    return charge_loss(cap, SPEC)


def test_trend_through_year_ends():
    months = pd.date_range("2020-01-31", "2023-06-30", freq="ME")
    values = 30.0 + 2.0 * (months - months[0]).days / 365.25  # 2 mA more each year
    loss = loss_table(values, np.full(len(months), 1.0), months)
    loss.loc[pd.Timestamp("2021-12-31"), "status"] = _core.STATUS_TEXT[_core.STALE]  # the year end is not estimated
    out = charge_loss_trend(loss)
    ends = out["year_end"]
    assert list(ends["year"]) == [2020, 2021, 2022, 2023]
    assert list(ends["time"]) == [pd.Timestamp(t) for t in ("2020-12-31", "2021-11-30", "2022-12-31", "2023-06-30")]
    assert out["status"] == OK and out["n_years"] == 4
    assert out["slope_ma_per_year"] == pytest.approx(2.0, rel=1e-9)
    # No scatter about the line: the band is the gain term alone.
    z = _z(0.90)
    assert out["slope_ma_per_year_high"] - 2.0 == pytest.approx(z * RecordConfig().charge_sigma * 2.0, rel=1e-6)


def test_trend_band_from_scatter():
    times = pd.DatetimeIndex(["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"])
    values = np.array([30.0, 33.0, 31.0, 36.0, 35.0])
    out = charge_loss_trend(loss_table(values, np.full(5, 2.0), times))
    years = (times - times[0]).days.to_numpy() / 365.25
    slope, intercept = np.polyfit(years, values, 1)
    resid = values - (intercept + slope * years)
    se = math.sqrt((resid**2).sum() / 3 / ((years - years.mean()) ** 2).sum())
    assert out["slope_ma_per_year"] == pytest.approx(slope)
    assert out["slope_sigma_ma_per_year"] == pytest.approx(se)
    assert out["dof"] == 3.0
    assert out["slope_ma_per_year_low"] < slope < out["slope_ma_per_year_high"]


def test_trend_needs_year_ends():
    times = pd.DatetimeIndex(["2020-12-31", "2021-12-31"])
    out = charge_loss_trend(loss_table([30.0, 31.0], [1.0, 1.0], times))
    assert out["status"] == "not possible from this data: fewer than 3 year-end values"
    assert math.isnan(out["slope_ma_per_year"])


def test_synthetic_pack_finds_the_hidden_drain():
    # Four months with a short level window: the estimate is near the 50 mA drain. Whether
    # the band holds it often enough is check W7, on 36-month packs with the frozen settings.
    candidates = detect_rests_in_parts(frames(PACK, 4, step_s=10, seed=3), SPEC, SHORT)
    rests = operating_rests(candidates, SPEC, SHORT)
    pairs = rest_pairs(rest_chain(rests, SPEC, SHORT), SHORT)
    cap = estimate_capacity(None, SPEC, config=SHORT, pairs=pairs, levels_from=levels_from(rests, SHORT))
    out = charge_loss(cap, SPEC)
    last = out.iloc[-1]
    truth = -1000.0 * PACK.hidden_current_a
    assert last["status"] == OK
    assert abs(last["charge_loss_ma"] - truth) < 10.0
    assert 0.0 < last["charge_loss_ma_high"] - last["charge_loss_ma_low"] < 20.0
    ok = (out["status"] == OK).to_numpy()  # with few months the sandwich can leave the offset without a band
    np.testing.assert_allclose(out["charge_loss_ma"][ok], -1000.0 * cap["offset_current_a"][ok])
    assert (out["charge_loss_ma"][~ok].isna()).all()


def test_band_uses_the_fits_own_degrees_of_freedom():
    rows = [("2021-01-31", -0.05, 0.002, 40.0, OK)]
    cap = capacity_table(rows)
    without = charge_loss(cap, SPEC).iloc[0]  # no dof column: n - 2
    cap["dof"] = [5.0]
    with_dof = charge_loss(cap, SPEC).iloc[0]
    z = _z(0.90)
    half = math.hypot(_core.t_quantile(z, 5.0) * 2.0, z * RecordConfig().charge_sigma * 50.0)
    assert with_dof["charge_loss_ma_high"] - with_dof["charge_loss_ma"] == pytest.approx(half)
    assert with_dof["charge_loss_ma_high"] > without["charge_loss_ma_high"]
