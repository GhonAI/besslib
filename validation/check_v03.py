"""Version 0.3 check F1: the settings of releases 0.1.0 and 0.2.0 give those releases' numbers.

The run scripts write the results of the two settings to ``data/f1/v0.1`` and ``data/f1/v0.2``
(``run_capacity.py`` and ``run_annex_vii.py``, each with ``--settings 0.1`` or ``0.2`` and
``--out DIR/phase2`` or ``DIR/phase3``). Every phase 2 file (monthly, tests, summary) and phase 3 file
(record, yearly) found there is compared with ``git show v0.1.0:<path>`` or ``v0.2.0``, as check X0
of version 0.2 does (``check_v02.py``: equal text in every column and at every key the release
has; what a later version adds is not compared and is named). The core vectors
(``tests/vectors/``) are compared byte for byte with the tag's; that the core still gives them is
``tests/test_core.py``. The configuration hashes must be those of the releases.

Writes ``validation/results/checks_v03.csv``.

Usage:
    python validation/check_v03.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import check_v02
from besslib.io import figgener
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "data" / "f1"
#: The hashes the releases printed (their result files carry them).
HASHES = {"v0.1.0": ("109e9eb1efa5", "c2f7f545cebb"), "v0.2.0": ("b3959941c19b", "d49cbfdcd5b6")}
VECTORS = {"v0.1.0": check_v02.VECTORS,
           "v0.2.0": check_v02.VECTORS + ("capacity_core_v02_expected.csv", "capacity_core_v02_pairs.csv")}


def f1(tag: str) -> list[dict]:
    short = tag[: tag.rindex(".")]
    check_v02.TAG, check_v02.VECTORS = tag, VECTORS[tag]
    rows = check_v02.x0(figgener.SYSTEM_IDS, FOLDER / short)
    out = []
    for r in rows:
        r = dict(r, check=r["check"].replace("X0", f"F1 {tag}"), pass_if="0 differences")
        out.append({k: v.replace("release 0.1.0 ", "") if isinstance(v, str) else v for k, v in r.items()})
    for r in out:
        for old, new in (("keys 0.2 adds", "keys later versions add"), ("columns and keys added in 0.2", "columns and keys added later")):
            r["note"] = r["note"].replace(old, new)
        if "records differ only" in r["note"] and tag == "v0.2.0":
            r["note"] = r["note"].replace("in the A3 text and in keys", "in keys")
    old = tag == "v0.1.0"
    got = (config_hash(CapacityConfig().v0_1() if old else CapacityConfig().v0_2()),
           config_hash(RecordConfig().v0_1() if old else RecordConfig().v0_2()))
    out.append(check_v02.row(f"F1 {tag} hashes", "the release's", f"{got[0]}, {got[1]}", got == HASHES[tag]))
    return out


def main() -> None:
    rows = f1("v0.1.0") + f1("v0.2.0")
    path = ROOT / "validation" / "results" / "checks_v03.csv"
    pd.DataFrame(rows, columns=check_v02.COLUMNS).to_csv(path, index=False)
    for r in rows:
        print(f"{r['check']}: {r['value']} -> {r['result']}  {r['note']}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
