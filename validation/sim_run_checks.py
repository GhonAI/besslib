"""Checks S5, S6 and S8 of ``besslib.sim`` as functions, with their pass numbers.

``validation/sim_checks.py`` imports them and writes the results file; this file writes none.

* ``check_s5``: the same case and seed written twice give identical file hashes; another seed
  gives different frame files. Pass: 0 differing files in the first, 0 equal frame files in the second.
  Every shipped case (``sim.case_names()``).
* ``check_s6``: every frame passes ``check_frame``; besslib 0.2 reads 3 packs of each shipped
  case with no warning under ``warnings.simplefilter("error")``; a case in which besslib has nothing
  to estimate (a grid pack without a calibration charge) gives ``not possible from this data``, which is no failure; ``besslib.sim`` does not import
  ``besslib.soh`` and ``besslib.soh`` does not import ``besslib.sim`` (read with ``ast``; a
  dynamic ``importlib`` call would not be seen). Pass: 0 failures.
* ``check_s8`` (information): seconds per pack-month at the case's step, and the 200 packs x 36
  months figure extrapolated from it (marked as such); the 396-group string too, from 2 months.

Usage:  python validation/sim_run_checks.py [s5|s6|s8]     (prints the result as JSON)
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import besslib  # noqa: E402
from besslib import frames, sim  # noqa: E402
from besslib.sim import truth  # noqa: E402


# --- S5 -----------------------------------------------------------------------------------------
def check_s5(cases=None, months: int = 2, seed: int = 1, other_seed: int = 2) -> dict:
    """Same case and seed twice: identical hashes. Another seed: different frame files."""
    cases = cases or sim.case_names()
    rows = []
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
        tmp = Path(tmp)
        for case in cases:
            a = truth.run_to_dir(case, months, seed, tmp / f"{case}_a")
            b = truth.run_to_dir(case, months, seed, tmp / f"{case}_b")
            c = truth.run_to_dir(case, months, other_seed, tmp / f"{case}_c")
            differ = sorted(k for k in a["files"] if a["files"][k] != b["files"].get(k))
            manifest_same = (tmp / f"{case}_a/manifest.json").read_bytes() == (tmp / f"{case}_b/manifest.json").read_bytes()
            frame_files = [k for k in a["files"] if k.startswith("frames/")]
            same_frames = sorted(k for k in frame_files if a["files"][k] == c["files"][k])
            rows.append({"case": case, "months": months, "files": len(a["files"]), "differ_same_seed": differ,
                         "manifest_same": manifest_same, "frame_files_equal_across_seeds": same_frames})
    worst_same = max(len(r["differ_same_seed"]) + (0 if r["manifest_same"] else 1) for r in rows)
    worst_other = max(len(r["frame_files_equal_across_seeds"]) for r in rows)
    return {"check": "S5", "rows": rows, "worst_same_seed_differences": worst_same,
            "worst_other_seed_equal_frames": worst_other, "pass": worst_same == 0 and worst_other == 0}


# --- S6 -----------------------------------------------------------------------------------------
def _imports(path: Path, root: Path) -> set[str]:
    """The dotted names a module imports (relative imports resolved), as candidates."""
    parts = list(path.relative_to(root.parent).with_suffix("").parts)
    package = parts[:-1]  # the package the module sits in
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            module = ".".join(base + (node.module.split(".") if node.module else []))
            found.add(module)
            found.update(f"{module}.{a.name}" for a in node.names)
    return found


def import_rule() -> dict:
    root = Path(besslib.__file__).parent
    bad = []
    scanned = {"sim": 0, "soh": 0}
    for package, forbidden in (("sim", "besslib.soh"), ("soh", "besslib.sim")):
        for path in sorted((root / package).rglob("*.py")):
            scanned[package] += 1
            for name in _imports(path, root):
                if name == forbidden or name.startswith(forbidden + "."):
                    bad.append(f"{path.relative_to(root.parent)} imports {name}")
    return {"files_scanned": scanned, "violations": bad}


def _f(x) -> float:
    return float("nan") if x is None else float(x)


def _read_case(case: str, seed: int, months: int) -> dict:
    """One pack: its frames pass ``check_frame``, and besslib 0.2 reads them under -W error."""
    data = sim.load_case(case)
    pack, _ = sim.build(data, seed)
    spec = sim.spec(data, pack)
    seen = {"frames": 0, "rows": 0}
    last = {}

    def parts():
        for out in sim.run(data, months, seed):
            frames.check_frame(out.frame)
            seen["frames"] += 1
            seen["rows"] += len(out.frame)
            stops = out.truth.stops
            full = stops[stops.kind.isin(["full", "empty"])]
            if len(full):
                last["true_capacity_ah"] = float(full.capacity_ah.iloc[-1])
            yield out.frame

    from besslib import soh  # the checks script may use A1; the library's sim may not

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        reduced = soh.reduce_parts(parts(), spec)
        record = soh.annex_vii(None, spec, reduced=reduced)
    table = record.to_frame()
    a1 = record.field("A1")
    return {
        "case": case, "seed": seed, "months": months, "frames_checked": seen["frames"], "rows": seen["rows"],
        "statuses": {k: int(v) for k, v in table["status"].value_counts().items()},
        "record": {r.code: r.status for r in table.itertuples()},
        "a1_status": str(a1.status), "a1_value_ah": _f(a1.value), "a1_low_ah": _f(a1.low), "a1_high_ah": _f(a1.high),
        "a1_events": _f(a1.events), "true_capacity_ah_last_stop": last.get("true_capacity_ah"),
        "nominal_capacity_ah": float(spec.nominal_capacity_ah),
    }


def check_s6(packs: int = 3, months: int = 3) -> dict:
    rows, failures = [], []
    for case in sim.case_names():
        for seed in range(1, packs + 1):
            try:
                rows.append(_read_case(case, seed, months))
            except Exception as err:  # a warning raised as an error, or a frame that fails
                failures.append(f"{case} seed {seed}: {type(err).__name__}: {err}")
    rule = import_rule()
    failures += rule["violations"]
    return {"check": "S6", "cases": sim.case_names(), "packs_per_case": packs, "months": months, "rows": rows,
            "import_rule": rule, "failures": failures, "n_failures": len(failures), "pass": not failures}


# --- S8 -----------------------------------------------------------------------------------------
STRING_CASE = "grid-string"


def check_s8(cases=("synth", "demo", STRING_CASE), months: int = 3, string_months: int = 2, repeats: int = 2) -> dict:
    """Seconds per pack-month (best of ``repeats``; one core, other jobs may share the machine)."""
    rows = []
    for case in cases:
        n = string_months if case == STRING_CASE else months
        best = min(_time(case, n) for _ in range(repeats))
        per = best / n
        rows.append({"case": case, "months": n, "seconds_per_pack_month": per,
                     "packs_200_months_36_hours_extrapolated": per * 200 * 36 / 3600.0,
                     "note": "extrapolated from a short run on one core, not measured at 200 x 36"})
    return {"check": "S8", "information": True, "rows": rows, "pass": None}


def _time(case: str, months: int) -> float:
    t0 = time.perf_counter()
    for _ in sim.run(case, months, 1):
        pass
    return time.perf_counter() - t0


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = {}
    for name, fn in (("s5", check_s5), ("s6", check_s6), ("s8", check_s8)):
        if which in ("all", name):
            out[name] = fn()
    print(json.dumps(out, indent=2, default=str))
