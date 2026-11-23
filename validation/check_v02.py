"""Version 0.2 checks X0, X2, X4, X8, X9 and X10.

* X0: the 0.2 code with the 0.1 settings against release 0.1.0 (tag ``v0.1.0``). The run
  scripts write the 0.1 results to ``data/x0/phase2`` and ``data/x0/phase3``
  (``run_capacity.py`` and ``run_annex_vii.py``, each with ``--settings 0.1 --out DIR``).
  Every phase 2 file (monthly, tests, summary) and phase 3 file (record, yearly) of the
  systems found there is compared with ``git show v0.1.0:<path>``, at the precision they
  are printed at (the files are text: the text must be equal). The one allowed difference
  is the A3 method text in a record (item 9). Columns of a table and keys of a summary
  that 0.2 adds are not compared (0.1 has no value for them); every column and key of the
  tag's file must be there with the same text, and the note names the added ones. The W4 and W6 rows of ``checks_phase3.csv``
  (item 5) are a check table, not a result file, and are not part of this comparison. The
  0.1 core vectors (``tests/vectors``) are compared byte for byte with the tag's.
* X2: plausibility, over the 0.2 capacity tables and records present (a file counts as 0.2
  when its hash is the hash of today's settings): month ends that are ``estimated`` with a
  value or low end at or below zero, with crossed levels, or with a wrong-sign pair in
  memory; plus the synthetic crossing case, which must give status 12.
* X4: records whose A2 is ``estimated`` while A5 is not.
* X8, X9, X10: pytest runs (and, for X9, the text in the README and in a report page).
  X10 runs ``test_report.py`` under ``-W error::FutureWarning`` with the interpreter that
  runs this script; the run at the lowest direct versions (numpy 2.0, pandas 2.2.2,
  pyarrow 16) is made by starting this script from that environment.

A check whose inputs are missing is written as ``not run`` with the reason. Writes
``validation/results/checks_v02.csv``.

Usage:
    python validation/check_v02.py                   # the development systems
    python validation/check_v02.py --systems 1 4 7   # some systems
    python validation/check_v02.py --systems all     # all 21 (X2 and X4 on held-out 0.2 results need the owner's word)
    python validation/check_v02.py --x0 DIR          # where the 0.1 results are (default data/x0)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.io import figgener
from besslib.soh import _core
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash

from tune_capacity import DEVELOPMENT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
TAG = "v0.1.0"
NOT_RUN = "not run"
COLUMNS = ["check", "pass_if", "value", "result", "note"]

#: The approved wording of the A3 method (version 0.2): typed here on purpose, since X9 checks the library against it.
A3_TEXT = (
    "energy out / energy in, summed over closed loops from one full rest to the next (Regulation (EU) "
    "2023/1542, Annex IV (6)); band: how far that ratio can be off because a loop does not end at exactly the "
    "state of charge it started from; the size of that error is learnt from the loops themselves (mean lag-2 "
    "product of the loop scores minus the mean lag-1 product)"
)
VECTORS = ("capacity_core_expected.csv", "capacity_core_pairs.csv", "loop_core_events.csv", "loop_core_expected.csv",
           "ratio_core_events.csv", "ratio_core_expected.csv")


def row(check: str, pass_if: str, value: str, result, note: str = "") -> dict:
    result = {True: "pass", False: "fail", None: NOT_RUN}.get(result, result)
    return dict(zip(COLUMNS, [check, pass_if, value, result, note]))


def tag_file(path: str) -> str | None:
    """A file of the tag as text; ``None`` when the tag does not have it."""
    done = subprocess.run(["git", "show", f"{TAG}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def tag_bytes(path: str) -> bytes | None:
    done = subprocess.run(["git", "show", f"{TAG}:{path}"], cwd=ROOT, capture_output=True)
    return done.stdout if done.returncode == 0 else None


# --- X0 ---------------------------------------------------------------------------------------


def _record_without_a3_method(text: str) -> dict:
    data = json.loads(text)
    for f in data["fields"]:
        if f["code"] == "A3":
            f["method"] = None
    return data


def _tag_keys_only(mine, tag, path: str, added: set):
    """``mine`` cut to the keys the tag has, at every depth (D53 b); the keys 0.2 adds are put in ``added``."""
    if isinstance(mine, dict) and isinstance(tag, dict):
        added.update(f"{path}{k}" for k in mine if k not in tag)
        return {k: _tag_keys_only(mine[k], tag[k], f"{path}{k}.", added) if k in mine else _MISSING for k in tag}
    if isinstance(mine, list) and isinstance(tag, list) and len(mine) == len(tag):
        return [_tag_keys_only(m, t, path, added) for m, t in zip(mine, tag)]
    return mine


_MISSING = object()


def _record_same_on_tag_keys(text: str, theirs: str) -> tuple[bool, set]:
    """A record against the tag's: equal at every key the tag has (fields in order, by position), the A3
    method text left out (item 9); the keys 0.2 adds (``A1.details.read_at_full_rest_v``, ...) are returned."""
    mine, tag = _record_without_a3_method(text), _record_without_a3_method(theirs)
    added: set = set()
    fields = []
    if len(mine["fields"]) == len(tag["fields"]):
        for m, t in zip(mine["fields"], tag["fields"]):
            fields.append(_tag_keys_only(m, t, f"{t.get('code', '')}.", added))
    cut = _tag_keys_only({k: v for k, v in mine.items() if k != "fields"}, {k: v for k, v in tag.items() if k != "fields"},
                         "", added)
    same = len(fields) == len(tag["fields"]) and cut == {k: v for k, v in tag.items() if k != "fields"} \
        and fields == tag["fields"]
    return same, added


def _csv_same_on_tag_columns(text: str, theirs: str) -> tuple[bool, set]:
    """Equal cell text in every column the tag's file has, row for row; the columns 0.2 adds are returned."""
    import csv
    import io

    mine_rows = list(csv.reader(io.StringIO(text)))
    tag_rows = list(csv.reader(io.StringIO(theirs)))
    if not mine_rows or not tag_rows or len(mine_rows) != len(tag_rows):
        return False, set()
    head = mine_rows[0]
    if any(c not in head for c in tag_rows[0]):
        return False, set()
    where = [head.index(c) for c in tag_rows[0]]
    same = all([r[k] for k in where] == t for r, t in zip(mine_rows[1:], tag_rows[1:]))
    return same, set(head) - set(tag_rows[0])


def _json_same_on_tag_keys(text: str, theirs: str) -> tuple[bool, set]:
    """Equal values at every top-level key the tag's file has; the keys 0.2 adds are returned."""
    mine, tag = json.loads(text), json.loads(theirs)
    same = all(k in mine and mine[k] == v for k, v in tag.items())
    return same, set(mine) - set(tag)


def x0(ids, folder: Path) -> list[dict]:
    pass_if = "0 differences, except the A3 text and the W4/W6 rows"
    files = []
    for sid in ids:
        files += [("phase2", f"system_{sid:02d}_{n}") for n in ("monthly.csv", "tests.csv", "summary.json")]
        files += [("phase3", f"system_{sid:02d}_{n}") for n in ("record.json", "yearly.csv")]
    compared, different, allowed, missing_tag = 0, [], 0, []
    systems, added = set(), set()
    for phase, name in files:
        mine = folder / phase / name
        if not mine.exists():
            continue
        theirs = tag_file(f"validation/results/{phase}/{name}")
        if theirs is None:
            missing_tag.append(f"{phase}/{name}")
            continue
        compared += 1
        systems.add(name[:9])
        text = mine.read_text()
        if name.endswith("record.json"):
            same, extra = _record_same_on_tag_keys(text, theirs)
            added.update(extra)
            allowed += int(same and text != theirs)
        elif name.endswith(".csv"):
            same, extra = _csv_same_on_tag_columns(text, theirs)
            added.update(extra)
        else:
            same, extra = _json_same_on_tag_keys(text, theirs)
            added.update(extra)
        if not same:
            different.append(f"{phase}/{name}")
    out = []
    if compared == 0:
        out.append(row("X0 files", pass_if, "no 0.1 result files", None,
                       f"run run_capacity.py and run_annex_vii.py with --settings 0.1 --out into {folder}/phase2 and /phase3"))
    else:
        note = f"{len(systems)} systems compared; {allowed} records differ only in the A3 text and in keys 0.2 adds"
        if added:
            note += f"; columns and keys added in 0.2, not compared: {sorted(added)}"
        if missing_tag:
            note += f"; the tag has no {missing_tag}"
        out.append(row("X0 files", pass_if, f"{len(different)} of {compared} files differ" + (f": {different}" if different else ""),
                       not different and not missing_tag, note))
    # the 0.1 core vectors
    bad, seen = [], 0
    for name in VECTORS:
        old = tag_bytes(f"tests/vectors/{name}")
        path = ROOT / "tests" / "vectors" / name
        if old is None or not path.exists():
            bad.append(f"{name} (missing)")
            continue
        seen += 1
        if path.read_bytes() != old:
            bad.append(name)
    out.append(row("X0 vectors", "0 differences", f"{len(bad)} of {len(VECTORS)} 0.1 vector files differ" + (f": {bad}" if bad else ""),
                   not bad, "byte for byte against the tag; the core's agreement with them is test_core.py"))
    return out


# --- X2 ---------------------------------------------------------------------------------------


def crossing_case() -> dict:
    """The synthetic crossing case: levels crossed from the middle of the record on give status 12."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from synth import SynthPack, frames
    finally:
        sys.path.pop(0)
    from besslib.soh.capacity import estimate_capacity
    from besslib.soh.rests import classify_rests, detect_rests_in_parts, levels_from, rest_chain, rest_pairs

    pack = SynthPack()
    spec = pack.spec()
    config = CapacityConfig(level_window_days=90.0, level_warmup_days=0.0)
    rests = classify_rests(detect_rests_in_parts(frames(pack, 18, step_s=20, seed=4), spec, CapacityConfig()), spec, config)
    late = rests["start"] >= rests["start"].iloc[len(rests) // 2]
    rests.loc[late, "level_empty_v"] = rests.loc[late, "level_full_v"] + 0.1
    pairs = rest_pairs(rest_chain(rests, spec, config), config)
    est = estimate_capacity(None, spec, rests=rests, pairs=pairs, config=config, at=[rests["start"].iloc[-1]],
                            levels_from=levels_from(rests, config))
    return {"status": est["status"].iloc[0], "ok": est["status"].iloc[0] == _core.STATUS_TEXT[_core.LEVELS_CROSSED]}


def month_end_flags(sid: int, monthly: pd.DataFrame, config: CapacityConfig) -> dict:
    """For the ``estimated`` month ends of a table: value or low at or below zero, crossed levels, wrong-sign pair in memory."""
    from besslib.soh.capacity import _levels_crossed, days
    from besslib.soh.rests import classify_rests, rest_chain, rest_pairs

    from reduce_events import rest_file
    from run_capacity import all_tests
    from tune_capacity import masks_for

    ok = monthly["status"] == _core.STATUS_TEXT[_core.OK]
    nonpositive = ok & ((monthly["capacity_ah"] <= 0) | (monthly["capacity_ah_low"] <= 0))
    spec = figgener.pack_spec(sid)
    mask = masks_for(all_tests(), sid)
    cands = pd.read_parquet(rest_file(sid, config.current_c_rate))
    cands = cands[cands["duration_min"] >= config.min_duration_min]
    inside = np.zeros(len(cands), dtype=bool)
    for a, b in mask:
        inside |= ((cands["start"] >= a) & (cands["start"] <= b)).to_numpy()
    rests = classify_rests(cands[~inside], spec, config)
    pairs = rest_pairs(rest_chain(rests, spec, config, mask=mask), config)
    t = days(monthly.index)
    crossed = _levels_crossed(rests, t)
    end = days(pairs["t_end"])
    wrong = ((pairs["kind"] == "F->E") & (pairs["q_ah"] >= 0)) | ((pairs["kind"] == "E->F") & (pairs["q_ah"] <= 0))
    wrong_end = end[wrong.to_numpy()]
    in_memory = np.array([bool(((wrong_end <= tq) & (wrong_end > tq - config.tau_days)).any()) for tq in t])
    return {"month_ends": int(ok.sum()), "nonpositive": int(nonpositive.sum()), "crossed": int((ok.to_numpy() & crossed).sum()),
            "wrong_sign": int((ok.to_numpy() & in_memory).sum())}


def x2(ids) -> list[dict]:
    pass_if = "0 estimated month ends with value or low <= 0, crossed levels or a wrong-sign pair"
    config = CapacityConfig()
    digest = config_hash(config)
    totals = dict(month_ends=0, nonpositive=0, crossed=0, wrong_sign=0)
    used, skipped = [], []
    for sid in ids:
        path = RESULTS / "phase2" / f"system_{sid:02d}_monthly.csv"
        if not path.exists():
            skipped.append(f"{sid:02d} (no table)")
            continue
        monthly = pd.read_csv(path, parse_dates=["time"], index_col="time")
        if set(monthly["config_hash"]) != {digest}:
            skipped.append(f"{sid:02d} (hash {sorted(set(monthly['config_hash']))[0]}, not today's {digest})")
            continue
        try:
            flags = month_end_flags(sid, monthly, config)
        except FileNotFoundError as err:
            skipped.append(f"{sid:02d} ({err.filename} missing)")
            continue
        used.append(sid)
        for k in totals:
            totals[k] += flags[k]
    out = []
    if not used:
        out.append(row("X2 month ends", pass_if, "no 0.2 capacity tables", None, "skipped: " + "; ".join(skipped)))
    else:
        bad = totals["nonpositive"] + totals["crossed"] + totals["wrong_sign"]
        value = (f"{bad} of {totals['month_ends']} estimated month ends (value or low <= 0: {totals['nonpositive']}; "
                 f"crossed levels: {totals['crossed']}; wrong-sign pair: {totals['wrong_sign']}); systems {used}")
        out.append(row("X2 month ends", pass_if, value, bad == 0, ("skipped: " + "; ".join(skipped)) if skipped else ""))
    # records and yearly tables
    n, bad_rows, found = 0, [], []
    for sid in ids:
        path = RESULTS / "phase3" / f"system_{sid:02d}_yearly.csv"
        if not path.exists():
            continue
        yearly = pd.read_csv(path)
        if set(yearly["capacity_config_hash"]) != {digest}:
            continue
        found.append(sid)
        a1 = yearly[(yearly["quantity"] == "capacity_ah") & (yearly["status"] == "estimated")]
        n += len(a1)
        bad_rows += [f"{sid:02d} {d}" for d in a1.loc[(a1["value"] <= 0) | (a1["low"] <= 0), "date"]]
    out.append(row("X2 records", "0 estimated A1 values with value or low <= 0 in the yearly tables",
                   f"{len(bad_rows)} of {n}" + (f": {bad_rows}" if bad_rows else "") if found else "no 0.2 yearly tables",
                   (not bad_rows) if found else None, f"systems {found}" if found else ""))
    case = crossing_case()
    out.append(row("X2 synthetic case", "levels crossed gives status 12", case["status"], case["ok"]))
    return out


# --- X4 ---------------------------------------------------------------------------------------


def x4(ids) -> dict:
    pass_if = "0 records with A2 estimated while A5 is not"
    digest = config_hash(RecordConfig())
    bad, n = [], 0
    for sid in ids:
        path = RESULTS / "phase3" / f"system_{sid:02d}_record.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if data["record_config_hash"] != digest:
            continue
        n += 1
        status = {f["code"]: f["status"] for f in data["fields"]}
        if status["A2"] == "estimated" and status["A5"] != "estimated":
            bad.append(f"{sid:02d}")
    if n == 0:
        return row("X4", pass_if, "no 0.2 records", None, f"records must carry record hash {digest}")
    return row("X4", pass_if, f"{len(bad)} of {n} records" + (f": {bad}" if bad else ""), not bad)


# --- X8, X9, X10 -------------------------------------------------------------------------------


def pytest_run(args: list[str]) -> tuple[bool, str]:
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=ROOT,
                          capture_output=True, text=True)
    tail = (done.stdout.strip().splitlines() or [""])[-1]
    return done.returncode == 0, tail


def x8() -> dict:
    ok, tail = pytest_run(["tests/test_rests.py", "-W", "error::RuntimeWarning"])
    return row("X8", "test_rests.py passes under -W error::RuntimeWarning", tail, ok,
               "the second half of X8, no RuntimeWarning from rests.py in the full pytest, is read from the full run")


def x9() -> list[dict]:
    from besslib.soh import record as record_module

    out = []
    ok, tail = pytest_run(["tests/test_record.py", "-k", "a3_method"])
    out.append(row("X9 record code", "the A3 method equals the approved wording", tail,
                   ok and record_module._METHODS["A3"] == A3_TEXT))
    # the 0.2 record files, the README, a report page
    digest = config_hash(RecordConfig())
    files = [p for p in sorted((RESULTS / "phase3").glob("system_*_record.json"))
             if json.loads(p.read_text())["record_config_hash"] == digest]
    wrong = [p.name for p in files if next(f for f in json.loads(p.read_text())["fields"] if f["code"] == "A3")["method"] != A3_TEXT]
    out.append(row("X9 record files", "exact", f"{len(wrong)} of {len(files)} 0.2 records differ" + (f": {wrong}" if wrong else ""),
                   (not wrong) if files else None, "" if files else "no 0.2 records yet"))
    readme = (ROOT / "README.md").read_text()
    out.append(row("X9 README", "exact", "wording found" if A3_TEXT in readme else "wording not found", A3_TEXT in readme,
                   "README.md is generated by make_readme.py from the records; it fails until that script is rerun on the 0.2 records"))
    try:
        import besslib
        from besslib.soh import annex_vii, report

        reduced, spec = besslib.io.read_sample()
        record = annex_vii(None, spec, reduced=reduced)
        with tempfile.TemporaryDirectory() as tmp:
            page = report(record, Path(tmp) / "record.html").read_text(encoding="utf-8")
        out.append(row("X9 report", "exact", "wording found" if escape(A3_TEXT) in page else "wording not found",
                       escape(A3_TEXT) in page, "page of the shipped sample"))
    except Exception as err:  # noqa: BLE001
        out.append(row("X9 report", "exact", f"{type(err).__name__}: {err}", None, "the report could not be built"))
    return out


def x10() -> dict:
    ok, tail = pytest_run(["tests/test_report.py", "-W", "error::FutureWarning"])
    return row("X10", "test_report.py passes under -W error::FutureWarning", tail, ok,
               "with the interpreter and library versions of this environment; run it from the environment with numpy 2.0, "
               "pandas 2.2.2, pyarrow 16 for the check as planned")


def main(argv: list[str]) -> None:
    ids = list(DEVELOPMENT)
    folder = ROOT / "data" / "x0"
    if "--systems" in argv:
        k = argv.index("--systems")
        rest = [a for a in argv[k + 1 :] if not a.startswith("--")]
        ids = list(figgener.SYSTEM_IDS) if rest == ["all"] else [int(a) for a in rest]
    if "--x0" in argv:
        folder = Path(argv[argv.index("--x0") + 1])
    rows = []
    for make in (lambda: x0(ids, folder), lambda: x2(ids), lambda: [x4(ids)], lambda: [x8()], x9, lambda: [x10()]):
        try:
            rows += make()
        except Exception as err:  # noqa: BLE001  a missing input never crashes the table
            rows.append(row("error", "", f"{type(err).__name__}: {err}", None, "the check could not run"))
    table = pd.DataFrame(rows, columns=COLUMNS)
    table["capacity_config_hash"] = config_hash(CapacityConfig())
    table["record_config_hash"] = config_hash(RecordConfig())
    table.to_csv(RESULTS / "checks_v02.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 110):
        print(table[["check", "value", "result"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
