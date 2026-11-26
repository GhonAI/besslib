"""Version 0.3 checks F1, R2 and W7.

F1: the settings of releases 0.1.0 and 0.2.0 give those releases' numbers.

The run scripts write the results of the two settings to ``data/f1/v0.1`` and ``data/f1/v0.2``
(``run_capacity.py`` and ``run_annex_vii.py``, each with ``--settings 0.1`` or ``0.2`` and
``--out DIR/phase2`` or ``DIR/phase3``). Every phase 2 file (monthly, tests, summary) and phase 3 file
(record, yearly) found there is compared with ``git show v0.1.0:<path>`` or ``v0.2.0``, as check X0
of version 0.2 does (``check_v02.py``: equal text in every column and at every key the release
has; what a later version adds is not compared and is named). The core vectors
(``tests/vectors/``) are compared byte for byte with the tag's; that the core still gives them is
``tests/test_core.py``. The configuration hashes must be those of the releases.

R2: on the development field systems, rule A (``ABOVE_FULL``, charges end above the full
level) removes no month end that release 0.2.0 estimated. Read from the monthly files of the
capacity run at today's settings (``validation/results/phase2``, or ``--phase2 DIR``) against
the same files at ``v0.2.0``; the held-out systems are not read.

W7 (version 0.3's rule): on the 200 synthetic packs of ``synthetic_phase3.py`` (today's settings), A4's
band holds the hidden drain in 85-95 % of the packs and the mean error lies within 2 sd / sqrt(n) of
zero (release 0.2.0's W7 judged the share only: 188 of 200).

Writes ``validation/results/checks_v03.csv``.

Usage:
    python validation/check_v03.py
    python validation/check_v03.py --phase2 data/dev_s20/phase2   # R2 from another folder
    python validation/check_v03.py --f1 data/f1_s20                # F1 from another folder (default data/f1)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import io
import sys

import check_v02
from besslib.io import figgener
from besslib.soh import _core
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from tune_capacity import DEVELOPMENT

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
        # release 0.1.0 is compared with check X0's exception (the A3 text and the W4/W6 rows changed in 0.2 on purpose)
        r = dict(r, check=r["check"].replace("X0", f"F1 {tag}"), pass_if=r["pass_if"] if tag == "v0.1.0" else "0 differences")
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


def r2(folder: Path) -> list[dict]:
    """R2: month ends of the development systems that 0.2.0 estimated and rule A now refuses."""
    ok, above = _core.STATUS_TEXT[_core.OK], _core.STATUS_TEXT[_core.ABOVE_FULL]
    check_v02.TAG = "v0.2.0"
    base = removed = 0
    hashes = set()
    for sid in DEVELOPMENT:
        name = f"system_{sid:02d}_monthly.csv"
        now = pd.read_csv(folder / name, dtype={"config_hash": str})
        then = pd.read_csv(io.StringIO(check_v02.tag_file(f"validation/results/phase2/{name}")), dtype={"config_hash": str})
        hashes |= set(now["config_hash"])
        both = then.merge(now, on="time", suffixes=("_02", ""), validate="one_to_one")
        was = both["status_02"] == ok
        base += int(was.sum())
        removed += int((was & (both["status"] == above)).sum())
    digest = config_hash(CapacityConfig())
    if hashes != {digest}:
        raise SystemExit(f"{folder} holds results of {sorted(hashes)}, not of today's settings {digest}")
    return [check_v02.row("R2", "0 removed", f"{removed} of {base} month ends that 0.2.0 estimated on the "
                          f"{len(DEVELOPMENT)} development systems are refused by rule A", removed == 0,
                          f"settings {digest}; held-out systems not read")]


def w7() -> list[dict]:
    """W7 with the mean clause, from ``synthetic_phase3.csv`` at today's settings."""
    t = pd.read_csv(ROOT / "validation" / "results" / "synthetic_phase3.csv", dtype={"capacity_config_hash": str})
    digest = config_hash(CapacityConfig())
    if set(t["capacity_config_hash"]) != {digest}:
        raise SystemExit(f"synthetic_phase3.csv holds results of {sorted(set(t['capacity_config_hash']))}, not {digest}")
    held = t["w7_held"].fillna(False).astype(bool)
    error = t["w7_error_ma"].dropna()
    mean, limit = float(error.mean()), float(2.0 * error.std(ddof=1) / len(error) ** 0.5)
    share = float(held.mean())
    passed = 0.85 <= share <= 0.95 and abs(mean) <= limit
    return [check_v02.row("W7", "85-95 % and mean error within 2 sd / sqrt(n) of zero",
                          f"band held the hidden drain in {int(held.sum())} of {len(t)} packs ({share:.1%}); mean error "
                          f"{mean:+.3f} mA, limit +-{limit:.3f} ({len(error)} packs with an estimate)", passed,
                          f"settings {digest}; release 0.2.0 judged the share only")]


W7_V03 = "W7 (0.3 rule)"  # the name of version 0.3's W7 row on the pages


def with_w7_v03(checks: pd.DataFrame) -> pd.DataFrame:
    """``checks_phase3.csv`` with version 0.3's W7 row (``checks_v03.csv``) right after release 0.2.0's W7 row.

    Release 0.2.0's W7 judged the share only; the pages show that row and, under it, version 0.3's rule with the
    mean clause, so a pass by the old rule is never shown alone.
    """
    v03 = pd.read_csv(ROOT / "validation" / "results" / "checks_v03.csv", keep_default_na=False)
    new, at = v03[v03["check"] == "W7"], checks.index[checks["check"] == "W7"]
    if len(new) != 1 or len(at) != 1:
        raise SystemExit("checks_v03.csv and checks_phase3.csv must each hold one W7 row")
    old = checks.loc[at[0]]
    row = {c: "" for c in checks.columns}
    row.update(check=W7_V03, scope=old["scope"], value=new["value"].iloc[0], pass_if=new["pass_if"].iloc[0],
               result=new["result"].iloc[0])
    k = checks.index.get_loc(at[0]) + 1
    return pd.concat([checks.iloc[:k], pd.DataFrame([row]), checks.iloc[k:]], ignore_index=True)


def main(argv: list[str]) -> None:
    global FOLDER
    if "--f1" in argv:
        FOLDER = Path(argv[argv.index("--f1") + 1])
    folder = Path(argv[argv.index("--phase2") + 1]) if "--phase2" in argv else ROOT / "validation" / "results" / "phase2"
    rows = f1("v0.1.0") + f1("v0.2.0") + r2(folder) + w7()
    path = ROOT / "validation" / "results" / "checks_v03.csv"
    pd.DataFrame(rows, columns=check_v02.COLUMNS).to_csv(path, index=False)
    for r in rows:
        print(f"{r['check']}: {r['value']} -> {r['result']}  {r['note']}")
    print(f"written: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
