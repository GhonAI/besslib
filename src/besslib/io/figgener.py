"""Reader for the open home-storage field dataset of Figgener et al. (2024).

Data: Figgener, J. et al., "Data for: Multi-year field measurements of home storage
systems and their use in capacity estimation", Zenodo, doi:10.5281/zenodo.12091223,
licence CC BY 4.0. Paper: Nature Energy 9, 1438-1447 (2024),
doi:10.1038/s41560-024-01620-9.

The dataset has one archive per system (``Data_ID_01.zip`` to ``Data_ID_21.zip``),
each with one CSV file per month at one-second resolution. This reader takes the
months straight out of the archive, so nothing has to be unpacked.

The source's current and power are already positive for charging, which is the
besslib convention (checked on system 17: the current is positive around midday,
while the voltage rises).
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from importlib import resources
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pc

from besslib.frames import PackSpec
from besslib.soh.reduce import Reduced

SYSTEM_IDS = tuple(range(1, 22))

_RENAME = {
    "P_in_W": "power_w",
    "V_in_V": "voltage_v",
    "I_in_A": "current_a",
    "T_Bat_in_C": "temp_pack_c",
    "T_Room_in_C": "temp_ambient_c",
    "Interpolated": "interpolated",
}
_COLUMNS = ["voltage_v", "current_a", "power_w", "temp_pack_c", "temp_ambient_c", "interpolated"]
_MONTH_FILE = re.compile(r"(\d{4})_(\d{2})_System_ID_(\d{2})\.csv$")
_TIME_FORMAT = "%d-%b-%Y %H:%M:%S"

#: The credit line of the dataset (CC BY 4.0); it travels with every pack read from it.
CREDIT = (
    "Data: Figgener, J. et al., Data for: Multi-year field measurements of home storage systems and their use "
    "in capacity estimation, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0."
)
PAPER = (
    "Figgener, J. et al., Multi-year field measurements of home storage systems and their use in capacity "
    "estimation, Nature Energy 9, 1438-1447 (2024), https://doi.org/10.1038/s41560-024-01620-9, licence "
    "CC BY-NC-ND 4.0: cited, nothing copied."
)


def _meta(name: str) -> pd.DataFrame:
    with resources.files("besslib.io").joinpath("figgener_meta", name).open() as f:
        return pd.read_csv(f)


def systems() -> pd.DataFrame:
    """The metadata of the 21 systems, one row each, with the source's column names."""
    return _meta("systems.csv").set_index("ID")


def capacity_tests() -> pd.DataFrame:
    """The 60 field capacity tests: ``system_id`` and ``start_time`` (as in the source)."""
    return _meta("capacity_tests.csv").assign(start_time=lambda t: pd.to_datetime(t["start_time"]))


def pack_spec(system_id: int) -> PackSpec:
    """The ``PackSpec`` of one system, from the dataset's metadata.

    The dataset gives the date the storage system was installed, not the date of
    manufacture; it is used as the date of putting into service.
    """
    row = systems().loc[_check_id(system_id)]
    return PackSpec(
        nominal_capacity_ah=float(row["Capacity_nominal_in_Ah"]),
        nominal_energy_kwh=float(row["Energy_nominal_in_kWh"]),
        cells_in_series=int(row["Cell_number_in_series"]),
        cells_in_parallel=int(row["Cell_number_in_parallel"]),
        chemistry=str(row["Chemistry_detail"]),
        nominal_voltage_v=float(row["Voltage_nominal_in_V"]),
        date_put_into_service=date.fromisoformat(row["Date_storage_system_installation"]),
        name=f"Figgener 2024, system {system_id:02d}",
        extra={
            "manufacturer": str(row["Manufacturer"]),
            "energy_usable_datasheet_kwh": float(row["Energy_usable_datasheet_in_kWh"]),
            "inverter_nominal_power_kw": float(row["Inverter_nominal_power"]),
            "measurement_start": date.fromisoformat(row["Date_measurement_system_installation"]),
            "data_credit": CREDIT,
        },
    )


def read_sample() -> tuple[Reduced, PackSpec]:
    """The sample shipped with besslib: system 05 of this dataset, as reduced tables.

    The three tables (one row per day, per current step and per rest candidate) were
    made from the system's measurements by ``besslib.soh.reduce_parts``; the samples
    themselves are not shipped. The data are licensed under CC BY 4.0, not under the
    licence of the code; the credit is in ``besslib/data/sample_05/NOTICE.md``.

    Returns
    -------
    (reduced, spec)
        A ``Reduced`` for ``besslib.soh.annex_vii(None, spec, reduced=reduced)``, and the
        system's ``PackSpec``.
    """
    folder = resources.files("besslib.data").joinpath("sample_05")
    tables = {}
    for name in ("daily", "steps", "rests"):
        with resources.as_file(folder.joinpath(f"{name}.parquet")) as path:
            tables[name] = pd.read_parquet(path)
    daily = tables["daily"]
    sample_s = float(daily["sample_s"].iloc[0]) if len(daily) else float("nan")
    reduced = Reduced(daily=daily, steps=tables["steps"], rests=tables["rests"], sample_s=sample_s)
    return reduced, pack_spec(5)


def list_months(root: str | Path, system_id: int) -> pd.DataFrame:
    """The monthly files of one system: ``month`` (first day), ``member``, ``bytes``."""
    source = _Source(root, _check_id(system_id))
    rows = [{"month": month, "member": member, "bytes": size} for month, member, size in source.months()]
    return pd.DataFrame(rows, columns=["month", "member", "bytes"])


def read_figgener(
    root: str | Path,
    system_id: int,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, PackSpec]:
    """Read one system's measurements between ``start`` and ``end`` (both included).

    Parameters
    ----------
    root
        A folder that holds ``Data_ID_XX.zip`` or the unpacked ``Data_ID_XX`` folder
        (directly, or one level below), or the path of the archive itself.
    system_id
        1 to 21.
    start, end
        Anything ``pandas.Timestamp`` accepts. ``None`` means the first or last
        measurement. Only the months needed are read.

    Returns
    -------
    (frame, spec)
        The measurements in the besslib frame format, and the system's ``PackSpec``.
    """
    source = _Source(root, _check_id(system_id))
    start = pd.Timestamp(start) if start is not None else None
    end = pd.Timestamp(end) if end is not None else None
    if end is not None and end == end.normalize() and not _has_time(end):
        end = end + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    parts = []
    for month, member, _ in source.months():
        month_end = month + pd.offsets.MonthBegin(1)
        if (end is not None and month > end) or (start is not None and month_end <= start):
            continue
        parts.append(source.read(member))
    if not parts:
        raise ValueError(f"system {system_id}: no data between {start} and {end}")

    frame = pd.concat(parts) if len(parts) > 1 else parts[0]
    if not frame.index.is_monotonic_increasing:
        frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="first")]
    return frame.loc[start:end], pack_spec(system_id)


def _has_time(stamp: pd.Timestamp) -> bool:
    return bool(stamp.hour or stamp.minute or stamp.second)


def _check_id(system_id: int) -> int:
    if int(system_id) not in SYSTEM_IDS:
        raise ValueError(f"system_id must be 1 to 21, got {system_id!r}")
    return int(system_id)


class _Source:
    """One system's files, in an archive or in an unpacked folder."""

    def __init__(self, root: str | Path, system_id: int) -> None:
        root = Path(root)
        tag = f"Data_ID_{system_id:02d}"
        self.system_id = system_id
        self.archive: Path | None = None
        self.folder: Path | None = None
        if root.is_file() and root.suffix == ".zip":
            self.archive = root
            return
        for candidate in (root / f"{tag}.zip", root / tag / f"{tag}.zip"):
            if candidate.is_file():
                self.archive = candidate
                return
        for candidate in (root / tag / f"{system_id:02d}", root / tag, root):
            if candidate.is_dir() and any(candidate.glob(f"*_System_ID_{system_id:02d}.csv")):
                self.folder = candidate
                return
        raise FileNotFoundError(f"no {tag}.zip and no unpacked {tag} folder under {root}")

    def months(self) -> list[tuple[pd.Timestamp, str, int]]:
        if self.archive is not None:
            with zipfile.ZipFile(self.archive) as z:
                entries = [(info.filename, info.file_size) for info in z.infolist()]
        else:
            entries = [(str(p), p.stat().st_size) for p in self.folder.glob("*.csv")]
        found = []
        for member, size in entries:
            match = _MONTH_FILE.search(member)
            if match and int(match.group(3)) == self.system_id:
                month = pd.Timestamp(year=int(match.group(1)), month=int(match.group(2)), day=1)
                found.append((month, member, size))
        return sorted(found)

    def read(self, member: str) -> pd.DataFrame:
        options = pc.ConvertOptions(
            timestamp_parsers=[_TIME_FORMAT],
            column_types={"Time": pa.timestamp("s"), "Interpolated": pa.int8()},
        )
        if self.archive is not None:
            with zipfile.ZipFile(self.archive) as z, z.open(member) as f:
                table = pc.read_csv(f, convert_options=options)
        else:
            table = pc.read_csv(member, convert_options=options)
        frame = table.to_pandas().set_index("Time").rename(columns=_RENAME)
        frame.index.name = "time"
        frame["interpolated"] = frame["interpolated"].astype(bool)
        return frame[_COLUMNS]
