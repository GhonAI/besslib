"""Runs the cases of ``besslib.sim`` on many packs and writes their checks: ``run`` and ``summary``.

A case module ``sim_case_<name>.py`` holds ``CASE``, ``SETTINGS``, ``run_pack(setting, seed)`` (the result rows of
one pack, no file written), ``RESULT`` and ``summary(table)`` (the check rows). This script runs the packs of a
case, setting by setting and seed by seed, in a few processes, and writes ``RESULT``; ``summary`` reads every
result file and writes ``validation/results/checks_sim_v03.csv``.

Seeds: ``development`` (0-199), ``held-out`` (2000-2199, run once, on frozen code) or ``a-b`` (both ends included).
A results file holds one seed set only (column ``seed_set``). Development runs write to ``data/sim_dev/``
(git-ignored), never to ``validation/results/``; a held-out run writes to ``validation/results/sim_v03/``. The run
of version 0.2 (seeds 1000-1199) stays in ``validation/results/sim/`` and ``checks_sim.csv``.
``summary`` refuses a file whose configuration hashes differ from today's or whose seeds are not the 200
held-out ones (``--development`` reads ``data/sim_dev/`` and allows any seeds).

Usage:
    python validation/sim_cases.py run heat leak --seeds 0-9 --processes 2
    python validation/sim_cases.py run grid --seeds development --settings nmc-7 lfp-7
    python validation/sim_cases.py summary [--out FILE] [--development]
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))

import sim_truth  # noqa: E402

CASES = {"heat": "sim_case_heat", "imbalance": "sim_case_imbalance", "leak": "sim_case_leak", "cold": "sim_case_cold",
         "grid": "sim_case_grid", "drift": "sim_case_drift"}
RESULTS = ROOT / "validation" / "results" / "sim_v03"
#: Version 0.2's held-out results (seeds 1000-1199): published, never written again.
RESULTS_02 = ROOT / "validation" / "results" / "sim"
DEVELOPMENT = ROOT / "data" / "sim_dev"
CHECKS = ROOT / "validation" / "results" / "checks_sim_v03.csv"
COLUMNS = ["check", "what", "result", "pass_if", "passed"]


def parse_seeds(text: str) -> list[int]:
    if text == "development":
        return list(sim_truth.DEVELOPMENT)
    if text == "held-out":
        return list(sim_truth.HELD_OUT)
    a, sep, b = text.partition("-")
    if not sep or not a.isdigit() or not b.isdigit() or int(b) < int(a):
        raise SystemExit(f"--seeds is development, held-out or a-b, not {text!r}")
    return list(range(int(a), int(b) + 1))


def seed_set(seeds) -> str:
    s = set(seeds)
    if s <= set(sim_truth.DEVELOPMENT):
        return "development"
    if s <= set(sim_truth.HELD_OUT):
        return "held-out"
    return "mixed"


def load(name: str):
    if name not in CASES:
        raise SystemExit(f"unknown case {name!r}; known: {', '.join(CASES)}")
    return importlib.import_module(CASES[name])


def run_task(task: tuple[str, str, int]) -> list[dict]:
    name, setting, seed = task
    return load(name).run_pack(setting, seed)


def result_dir(seeds, out: str | None) -> Path:
    if out:
        return Path(out)
    return RESULTS if seed_set(seeds) == "held-out" else DEVELOPMENT


def write_rows(module, rows: list[dict], label: str, folder: Path) -> Path:
    """The rows of one run into the case's results file, replacing the same settings and seeds in it."""
    if folder.resolve() in {RESULTS_02.resolve(), CHECKS.parent.resolve()}:
        raise SystemExit(f"{folder} holds published results of version 0.2 or the checks files: no run writes there")
    if label == "held-out" and folder.resolve() != RESULTS.resolve():
        raise SystemExit(f"held-out rows go to {RESULTS}, not {folder}")
    new = pd.DataFrame(rows)
    new.insert(len(new.columns), "seed_set", label)
    path = folder / module.RESULT
    folder.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path, keep_default_na=False, na_values=[""])
        if len(old) and str(old["seed_set"].iloc[0]) == label:
            key = set(zip(new["setting"], new["seed"]))
            keep = old[[(s, k) not in key for s, k in zip(old["setting"], old["seed"])]]
            new = pd.concat([keep, new], ignore_index=True)
    order = {s: k for k, s in enumerate(module.SETTINGS)}
    new = new.assign(_o=new["setting"].map(order)).sort_values(["_o", "seed"], kind="stable").drop(columns="_o")
    new.to_csv(path, index=False, float_format="%.6g")
    print(f"written: {path} ({len(new)} rows, {path.stat().st_size / 1e6:.2f} MB)")
    return path


def cmd_run(args) -> None:
    seeds = parse_seeds(args.seeds)
    label = seed_set(seeds)
    if label == "mixed":
        raise SystemExit("a run holds one seed set only: development seeds or held-out seeds, not both")
    folder = result_dir(seeds, args.out)
    if label == "development" and folder.resolve() == RESULTS.resolve():
        raise SystemExit("development runs write to data/sim_dev/, never to validation/results/")
    folder.mkdir(parents=True, exist_ok=True)
    for name in args.cases:
        module = load(name)
        wanted = args.settings or list(module.SETTINGS)
        unknown = [s for s in wanted if s not in module.SETTINGS]
        if unknown:
            if args.settings and len(args.cases) > 1:
                wanted = [s for s in wanted if s in module.SETTINGS]
            else:
                raise SystemExit(f"{name}: unknown settings {unknown}; known: {list(module.SETTINGS)}")
        tasks = []
        for setting in wanted:
            use = seeds[:1] if module.SETTINGS[setting].get("first_seed_only") else seeds
            tasks += [(name, setting, s) for s in use]
        t0 = time.time()
        rows = []
        with Pool(args.processes, maxtasksperchild=1) as pool:
            for k, part in enumerate(pool.imap_unordered(run_task, tasks), start=1):
                rows.extend(part)
                print(f"{name}: {k}/{len(tasks)} packs, {time.time() - t0:.0f} s", flush=True)
        write_rows(module, rows, label, folder)


def read_results(folder: Path, development: bool) -> list[tuple[str, pd.DataFrame]]:
    out = []
    for name in CASES:
        try:
            module = load(name)
        except ModuleNotFoundError:
            print(f"{name}: no case module yet, skipped")
            continue
        path = folder / module.RESULT
        if not path.exists():
            print(f"{name}: no results file {path.name}, skipped")
            continue
        table = pd.read_csv(path, keep_default_na=False, na_values=[""])
        for column, value in sim_truth.HASHES.items():
            if set(table[column]) != {value}:
                raise SystemExit(f"{path.name}: {column} is {sorted(set(table[column]))}, today's is {value}: run it again")
        if not development:
            if set(table["seed_set"]) != {"held-out"}:
                raise SystemExit(f"{path.name}: its seeds are {sorted(set(table['seed_set']))}, not held-out")
            for setting, t in table.groupby("setting"):
                if module.SETTINGS[setting].get("first_seed_only"):
                    continue
                if set(t["seed"]) != set(sim_truth.HELD_OUT) or len(t) < len(sim_truth.HELD_OUT):
                    raise SystemExit(f"{path.name}: setting {setting} does not hold the 200 held-out seeds")
        out.append((name, table))
    return out


def cmd_summary(args) -> None:
    folder = DEVELOPMENT if args.development else RESULTS
    rows = []
    for name, table in read_results(folder, args.development):
        module = load(name)
        machines = ", ".join(sorted(set(table["machine"].astype(str))))
        for row in module.summary(table):
            rows.append({**row, "what": f"{row['what']} [{name}; computed on {machines}]"})
    out = Path(args.out) if args.out else (DEVELOPMENT / "checks_sim.csv" if args.development else CHECKS)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=COLUMNS).to_csv(out, index=False)
    for r in rows:
        print(f"{r['check']}: {r['result']}  [{r['pass_if']}] -> {r['passed']}")
    print(f"written: {out} ({len(rows)} rows)")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the packs of some cases")
    r.add_argument("cases", nargs="+", help=f"one or more of {', '.join(CASES)}")
    r.add_argument("--seeds", default="development", help="development, held-out or a-b")
    r.add_argument("--processes", type=int, default=2)
    r.add_argument("--settings", nargs="*", help="only these settings")
    r.add_argument("--out", help="the folder to write to (default by seed set)")
    s = sub.add_parser("summary", help="the checks of every results file")
    s.add_argument("--out", help="the checks file (default validation/results/checks_sim_v03.csv)")
    s.add_argument("--development", action="store_true", help="read data/sim_dev/ and allow any seeds")
    args = p.parse_args(argv)
    {"run": cmd_run, "summary": cmd_summary}[args.command](args)


if __name__ == "__main__":
    main()
