"""The capacity-test measurement, checked on synthetic tests with a known answer."""

import numpy as np
import pandas as pd
import pytest

from besslib.frames import PackSpec
from besslib.soh import measure_capacity_test

SPEC = PackSpec(nominal_capacity_ah=100.0, nominal_energy_kwh=5.0, cells_in_series=16)
START = pd.Timestamp("2022-06-01 14:00:00")


def synthetic_test(discharge_a=-40.0, discharge_s=7200, taper_s=600, pre_load_a=-1.0, noise=0.0, seed=1):
    """One day: a charge, a rest with a small load, a full-power discharge with a taper, a rest.

    Returns the frame and the true charge delivered from the documented start, in Ah.
    """
    index = pd.date_range(START - pd.Timedelta(hours=8), START + pd.Timedelta(hours=8), freq="s")
    seconds = (index - START).total_seconds().to_numpy()
    current = np.zeros(len(index))
    current[(seconds >= -6 * 3600) & (seconds < -3 * 3600)] = 30.0  # the charge before the test
    current[(seconds >= -1800) & (seconds < 0)] = pre_load_a  # house load before the start
    current[(seconds >= 0) & (seconds < discharge_s)] = discharge_a
    taper = (seconds >= discharge_s) & (seconds < discharge_s + taper_s)
    current[taper] = discharge_a * (1 - (seconds[taper] - discharge_s) / taper_s)
    true_ah = -(discharge_a * discharge_s + 0.5 * discharge_a * taper_s) / 3600.0
    if noise:
        current = current + np.random.default_rng(seed).normal(0, noise, len(index))
    voltage = 52.0 + 0.02 * current
    frame = pd.DataFrame(
        {"voltage_v": voltage, "current_a": current, "power_w": voltage * current, "temp_pack_c": 22.0, "interpolated": False},
        index=index,
    )
    return frame, true_ah


def test_recovers_the_known_capacity_and_energy():
    frame, true_ah = synthetic_test()
    result = measure_capacity_test(frame, SPEC, START)
    assert result.status == "ok"
    assert result.capacity_ah == pytest.approx(true_ah, rel=2e-3)
    assert result.soh_capacity == pytest.approx(true_ah / 100.0, rel=2e-3)
    assert abs((result.onset - START).total_seconds()) <= 10
    assert result.c_rate == pytest.approx(0.4)
    assert result.energy_kwh == pytest.approx(-(frame["power_w"].loc[result.onset : result.end].sum()) / 3.6e6, rel=1e-3)
    assert result.pre_start_ah == pytest.approx(0.5, abs=0.05)  # 30 min at 1 A, reported, not added
    assert result.missing_seconds == 0 and result.interpolated_share == 0.0


def test_noise_and_a_late_documented_start_do_not_move_the_result():
    frame, true_ah = synthetic_test(noise=0.5)
    late = measure_capacity_test(frame, SPEC, START + pd.Timedelta(minutes=7))
    assert late.status == "ok"
    assert late.capacity_ah == pytest.approx(true_ah, rel=5e-3)


def test_says_so_when_there_is_no_discharge_at_the_documented_start():
    frame, _ = synthetic_test()
    wrong = measure_capacity_test(frame, SPEC, START - pd.Timedelta(hours=2, minutes=30))
    assert wrong.status == "no full-power discharge at the documented start"
    assert np.isnan(wrong.capacity_ah)


def test_says_so_when_the_data_does_not_cover_the_start():
    frame, _ = synthetic_test()
    assert measure_capacity_test(frame, SPEC, START + pd.Timedelta(days=3)).status == "no data at the documented start"


def test_counts_missing_seconds_inside_the_discharge():
    frame, true_ah = synthetic_test()
    hole = (frame.index >= START + pd.Timedelta(minutes=30)) & (frame.index < START + pd.Timedelta(minutes=31))
    result = measure_capacity_test(frame[~hole], SPEC, START)
    assert result.status == "ok"
    assert result.missing_seconds == 60
    assert result.capacity_ah == pytest.approx(true_ah, rel=2e-3)  # the gap is bridged by the trapezoid rule
