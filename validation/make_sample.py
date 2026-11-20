"""The sample shipped with besslib: the reduced tables of system 05 of the Figgener dataset.

Reads ``data/reduced/phase3/ID_05_{daily,steps,rests}.parquet`` (written by
``validation/reduce_phase3.py``) and writes ``src/besslib/data/sample_05/`` with the same
tables, unchanged, compressed with zstd at level 19. Each file carries the credit, the
licence, the disclaimer and the note that the data were modified, as schema metadata;
``NOTICE.md`` beside them says the same in plain words. Both are made from the texts in
``notice_texts``, so they cannot disagree.

Two runs give byte-identical files; the script checks it by making every file twice in
memory and comparing the bytes with the file written.

Usage:
    python validation/make_sample.py
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from besslib.io import figgener
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "reduced" / "phase3"
OUT = ROOT / "src" / "besslib" / "data" / "sample_05"
SYSTEM_ID = 5
TABLES = ("daily", "steps", "rests")
ZSTD_LEVEL = 19

#: The creators of the Zenodo record 12091223, in its order.
CREATORS = (
    "Figgener, Jan",
    "Haberschusz, David",
    "Wessels, Oliver",
    "Kairies, Kai-Philipp",
    "Bors, Jakob",
    "van Ouwerkerk, Jonas",
    "Sauer, Dirk Uwe",
)
TITLE = "Data for: Multi-year field measurements of home storage systems and their use in capacity estimation"
DOI = "https://doi.org/10.5281/zenodo.12091223"
LICENCE = "CC BY 4.0, https://creativecommons.org/licenses/by/4.0/"
DISCLAIMER = (
    "The licensed material is provided as-is and as-available, without representations or warranties of any "
    "kind, as stated in Section 5 of the CC BY 4.0 licence (disclaimer of warranties and limitation of liability)."
)


def notice_texts() -> dict[str, str]:
    """The metadata texts of the sample: one place for the parquet metadata and NOTICE.md."""
    version = metadata.version("besslib")
    return {
        "creator": "; ".join(CREATORS),
        "title": TITLE,
        "source": f"{DOI}, file Data_ID_{SYSTEM_ID:02d}.zip",
        "licence": LICENCE,
        "disclaimer": DISCLAIMER,
        "modified": (
            f"reduced by besslib {version} with validation/reduce_phase3.py: daily sums, current steps and rest "
            "candidates; not the original samples"
        ),
        "capacity_config_hash": config_hash(CapacityConfig()),
        "record_config_hash": config_hash(RecordConfig()),
    }


def notice_md(texts: dict[str, str]) -> str:
    """NOTICE.md of the sample, from the same texts as the parquet metadata."""
    return "\n".join(
        [
            f"# Sample data: system {SYSTEM_ID:02d} of the Figgener home-storage dataset",
            "",
            "## What the files are",
            "",
            f"`daily.parquet`, `steps.parquet` and `rests.parquet` are the reduced tables of system {SYSTEM_ID:02d}: "
            "one row per day, per current step and per rest candidate. besslib reads them with "
            "`besslib.io.read_sample()`.",
            "",
            f"Modified: {texts['modified']}.",
            "",
            f"Made with the settings of configuration hashes {texts['capacity_config_hash']} (capacity) and "
            f"{texts['record_config_hash']} (record).",
            "",
            "## Credit",
            "",
            "Creators, as in the Zenodo record:",
            "",
            *[f"- {c}" for c in CREATORS],
            "",
            f"Title: {texts['title']}.",
            "",
            f"Source: Zenodo, {texts['source']}.",
            "",
            f"Licence: {texts['licence']}.",
            "",
            "This sample is licensed under CC BY 4.0, not under the BSD-3-Clause licence of the besslib code.",
            "",
            "## Disclaimer",
            "",
            texts["disclaimer"],
            "",
            "## The paper",
            "",
            "The dataset belongs to an article in Nature Energy. Cite it when you use the data:",
            "",
            figgener.PAPER,
            "",
        ]
    )


def table_bytes(name: str, texts: dict[str, str]) -> bytes:
    """One table of the sample as parquet bytes: the source table unchanged, the texts added as metadata."""
    table = pq.read_table(SOURCE / f"ID_{SYSTEM_ID:02d}_{name}.parquet")
    extra = {k.encode(): v.encode() for k, v in texts.items()}
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), **extra})
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", compression_level=ZSTD_LEVEL)
    return sink.getvalue().to_pybytes()


def sample_files() -> dict[str, bytes]:
    """The file names of the sample and their bytes."""
    texts = notice_texts()
    files = {f"{name}.parquet": table_bytes(name, texts) for name in TABLES}
    files["NOTICE.md"] = notice_md(texts).encode("utf-8")
    return files


def _check_same(path: Path, name: str) -> None:
    """Content and column types of one written table against its source."""
    source = pq.read_table(SOURCE / f"ID_{SYSTEM_ID:02d}_{name}.parquet")
    sample = pq.read_table(path)
    if not sample.schema.remove_metadata().equals(source.schema.remove_metadata()):
        raise SystemExit(f"{name}: the schema differs from the source")
    if not sample.equals(source.replace_schema_metadata(sample.schema.metadata)):
        raise SystemExit(f"{name}: the table differs from the source")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for file_name, data in sample_files().items():
        (OUT / file_name).write_bytes(data)
    for name in TABLES:
        _check_same(OUT / f"{name}.parquet", name)
    for file_name, data in sample_files().items():
        if (OUT / file_name).read_bytes() != data:
            raise SystemExit(f"{file_name}: two runs differ")
    print("the tables equal their sources; two runs give byte-identical files")
    total = 0
    for name in TABLES:
        size = (OUT / f"{name}.parquet").stat().st_size
        total += size
        print(f"{name}.parquet: {size / 1e6:.2f} MB")
    print(f"total: {total / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
