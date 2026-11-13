"""The reader, tested on a small archive built in the test (no download needed)."""

import zipfile

import numpy as np
import pandas as pd
import pytest

from besslib.frames import FrameError, PackSpec, check_frame
from besslib.io import figgener


def month_csv(start: str, periods: int) -> str:
    index = pd.date_range(start, periods=periods, freq="s")
    lines = ["Time,P_in_W,V_in_V,I_in_A,T_Bat_in_C,T_Room_in_C,Interpolated"]
    for i, stamp in enumerate(index):
        current = 10.0 if i % 2 == 0 else -4.0
        lines.append(f"{stamp.strftime('%d-%b-%Y %H:%M:%S')},{52.0 * current:g},52,{current:g},21.5,19,{int(i == 3)}")
    return "\n".join(lines) + "\n"


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "Data_ID_17"
    folder.mkdir()
    with zipfile.ZipFile(folder / "Data_ID_17.zip", "w") as z:
        z.writestr("17/", "")
        z.writestr("17/2019_03_System_ID_17.csv", month_csv("2019-03-31 23:59:50", 10))
        z.writestr("17/2019_04_System_ID_17.csv", month_csv("2019-04-01 00:00:00", 20))
    return tmp_path


def test_reads_months_from_the_archive_in_frame_format(root):
    frame, spec = figgener.read_figgener(root, 17)
    check_frame(frame)
    assert len(frame) == 30
    assert frame.index[0] == pd.Timestamp("2019-03-31 23:59:50")
    assert list(frame.columns) == ["voltage_v", "current_a", "power_w", "temp_pack_c", "temp_ambient_c", "interpolated"]
    assert frame["interpolated"].dtype == bool and frame["interpolated"].sum() == 2
    assert frame["current_a"].iloc[0] == 10.0 and frame["power_w"].iloc[0] == 520.0
    assert isinstance(spec, PackSpec) and spec.nominal_capacity_ah == 158.0 and spec.cells_in_series == 16


def test_start_and_end_select_time_and_skip_other_months(root):
    frame, _ = figgener.read_figgener(root, 17, "2019-04-01 00:00:05", "2019-04-01 00:00:09")
    assert len(frame) == 5
    march, _ = figgener.read_figgener(root, 17, end="2019-03-31")
    assert len(march) == 10
    with pytest.raises(ValueError):
        figgener.read_figgener(root, 17, "2020-01-01", "2020-01-02")


def test_archive_path_and_unpacked_folder_also_work(root, tmp_path):
    archive = root / "Data_ID_17" / "Data_ID_17.zip"
    assert len(figgener.read_figgener(archive, 17)[0]) == 30
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp_path / "unpacked" / "Data_ID_17")
    assert len(figgener.read_figgener(tmp_path / "unpacked", 17)[0]) == 30
    months = figgener.list_months(tmp_path / "unpacked", 17)
    assert list(months["month"]) == [pd.Timestamp("2019-03-01"), pd.Timestamp("2019-04-01")]


def test_missing_system_is_a_clear_error(root):
    with pytest.raises(FileNotFoundError):
        figgener.read_figgener(root, 5)
    with pytest.raises(ValueError):
        figgener.read_figgener(root, 22)


def test_shipped_metadata_matches_the_source_counts():
    systems = figgener.systems()
    tests = figgener.capacity_tests()
    assert len(systems) == 21 and len(tests) == 60
    assert systems["Chemistry"].value_counts().to_dict() == {"LFP": 8, "NMC": 7, "LMO": 6}
    assert tests["system_id"].nunique() == 18
    assert not {13, 16, 18} & set(tests["system_id"])
    assert tests["start_time"].min().year == 2017 and tests["start_time"].max().year == 2022


def test_check_frame_rejects_what_breaks_the_contract():
    index = pd.date_range("2022-01-01", periods=3, freq="s")
    good = pd.DataFrame({"voltage_v": 50.0, "current_a": 1.0, "power_w": 50.0, "temp_pack_c": 20.0}, index=index)
    assert check_frame(good) is good
    with pytest.raises(FrameError, match="missing"):
        check_frame(good.drop(columns="power_w"))
    with pytest.raises(FrameError, match="sorted"):
        check_frame(good.iloc[::-1])
    with pytest.raises(FrameError, match="DatetimeIndex"):
        check_frame(good.reset_index(drop=True))
    with pytest.raises(FrameError, match="naive"):
        check_frame(good.tz_localize("UTC"))
    with pytest.raises(FrameError, match="duplicate"):
        check_frame(good.set_index(pd.DatetimeIndex([index[0], index[0], index[1]])))
    assert np.isclose(PackSpec(100.0, 5.0, 16).reference_capacity_ah, 100.0)
