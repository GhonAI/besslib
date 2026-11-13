"""Convert the dataset's two metadata spreadsheets to the CSV files shipped with besslib.

Source: Metadata_and_Code.zip from Zenodo record 12091223 (Figgener et al. 2024, CC BY 4.0).
Reads the .xlsx files with the standard library only, so the result can be rebuilt anywhere.

Usage:
    python validation/make_metadata.py data/raw/Metadata_and_Code/Metadata_and_Code.zip
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OUT = Path(__file__).resolve().parents[1] / "src" / "besslib" / "io" / "figgener_meta"
EXCEL_EPOCH = datetime(1899, 12, 30)


def read_sheet(xlsx_bytes: bytes) -> list[list[str]]:
    """Return the first sheet of an .xlsx file as rows of strings."""
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for item in root.findall(f"{NS}si"):
            strings.append("".join(t.text or "" for t in item.iter(f"{NS}t")))
    root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in root.iter(f"{NS}row"):
        cells = []
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v")
            if v is None:
                cells.append("")
            elif c.get("t") == "s":
                cells.append(strings[int(v.text)])
            else:
                cells.append(v.text)
        rows.append(cells)
    return rows


def unix_to_date(seconds: str) -> str:
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc).strftime("%Y-%m-%d")


def excel_to_timestamp(serial: str) -> str:
    stamp = EXCEL_EPOCH + timedelta(days=float(serial))
    return (stamp + timedelta(microseconds=500000)).strftime("%Y-%m-%d %H:%M:%S")


def main(archive: str) -> None:
    z = zipfile.ZipFile(archive)
    OUT.mkdir(parents=True, exist_ok=True)

    systems = read_sheet(z.read("00_Data/00_Metadata/Metadata_Systems.xlsx"))
    header, body = systems[0], systems[1:]
    date_cols = [i for i, name in enumerate(header) if name.startswith("Date_")]
    with open(OUT / "systems.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in body:
            row = list(row)
            for i in date_cols:
                row[i] = unix_to_date(row[i])
            row = [f"{float(x):g}" if _is_number(x) and i not in date_cols else x for i, x in enumerate(row)]
            w.writerow(row)

    tests = read_sheet(z.read("00_Data/00_Metadata/Capacity_Tests.xlsx"))
    with open(OUT / "capacity_tests.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system_id", "start_time"])
        for start, system_id in tests[1:]:
            w.writerow([int(float(system_id)), excel_to_timestamp(start)])

    print(f"systems: {len(body)} rows, capacity tests: {len(tests) - 1} rows -> {OUT}")


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    main(sys.argv[1])
