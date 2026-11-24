"""Check S0: the ``synth`` case of ``besslib.sim`` reproduces ``tests/synth.py`` of besslib 0.2.0.

Two parts, with their pass numbers:

1. **Samples.** ``tests/synth.py`` as tagged ``v0.2.0`` (read with ``git show``) and today's
   ``tests/synth.py``, a thin wrapper around ``besslib.sim``, give the same frames, measured and true,
   at 10 and 20 s, for every pack variant the checks use (the default pack, the resistance
   growth pack of ``test_resistance.py``, the temperature pack, and packs A, B and B with
   noise of ``synthetic_phase3.py``), on 3 seeds x 36 months; and the same
   ``true_stops``, ``true_segments`` and ``true_usable_fade``. Pass: the largest
   difference in voltage, current and power is at most 1e-9 (V, A, W); the truth tables
   are equal.
2. **Published numbers.** V6 (``synthetic_coverage.py``), X3 (``synthetic_lag.py``) and
   W7 (``synthetic_phase3.py --only w7``) run on ``besslib.sim`` with their published
   settings, 200 packs each, into ``data/sim_s0/``; each results file must equal the
   published one in ``validation/results/`` byte for byte, or, for a file that also records
   the run's wall-clock time (W7's ``w7_seconds``), in every other column.

Usage:
    python validation/sim_s0.py samples [months] [seeds]    # part 1 (default 36 months, seeds 1,2,3)
    python validation/sim_s0.py published                   # part 2 (about an hour on 3 cores)
    python validation/sim_s0.py compare                     # part 2's comparison only, on files already rebuilt
    python validation/sim_s0.py drift                       # information row S0c
"""

from __future__ import annotations

import hashlib
import json
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "validation"))

TAG = "v0.2.0"
OUT = ROOT / "data" / "sim_s0"
PASS_SAMPLES = 1e-9
PUBLISHED = {"V6": "synthetic_coverage.csv", "X3": "synthetic_lag.csv", "W7": "synthetic_phase3.csv"}


def reference_text() -> str:
    """The text of ``tests/synth.py`` as tagged ``v0.2.0``."""
    return subprocess.run(["git", "show", f"{TAG}:tests/synth.py"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def reference_synth(text: str | None = None):
    """``tests/synth.py`` as tagged ``v0.2.0`` (or the given copy of it), loaded as a module."""
    text = reference_text() if text is None else text
    folder = Path(tempfile.mkdtemp(prefix="besslib_s0_"))
    path = folder / "synth_v020.py"
    path.write_text(text)
    spec = importlib.util.spec_from_file_location("synth_v020", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def variants(seed: int) -> list[tuple[str, dict]]:
    """The pack settings of every check that runs on ``tests/synth.py``."""
    import synthetic_phase3

    a, b, noisy = synthetic_phase3.packs_for(seed)  # built on whichever SynthPack is imported; only fields are read
    fields = [f for f in a.__dataclass_fields__]
    def kw(p):
        return {f: getattr(p, f) for f in fields}
    return [
        ("default", {}),
        ("growth", dict(r_growth_per_year=0.40, voltage_gain_error_max=0.0025)),
        ("temperature", dict(temp_mean_c=15.0, temp_season_amp_c=8.0, temp_daily_amp_c=3.0)),
        ("phase3 A", kw(a)),
        ("phase3 B", kw(b)),
        ("phase3 B noisy", kw(noisy)),
    ]


def samples(months: int = 36, seeds=(1, 2, 3)) -> dict:
    return samples_row([samples_raw(months, seeds)], months, seeds)


def samples_raw(months: int, seeds, reference: str | None = None) -> dict:
    """The largest differences and the failures, for some seeds (``samples_row`` joins several)."""
    import synth as new

    old = reference_synth(reference)
    worst = {"voltage_v": 0.0, "current_a": 0.0, "power_w": 0.0, "temp_pack_c": 0.0}
    failures = []
    n_frames = 0
    for seed in seeds:
        for name, kw in variants(seed):
            po, pn = old.SynthPack(**kw), new.SynthPack(**kw)
            for true_signals in (False, True):
                for step in (10, 20):
                    a = old.frames(po, months, step_s=step, seed=seed, true_signals=true_signals)
                    b = new.frames(pn, months, step_s=step, seed=seed, true_signals=true_signals)
                    for x, y in zip(a, b, strict=True):
                        n_frames += 1
                        if not x.index.equals(y.index) or list(x.columns) != list(y.columns):
                            failures.append(f"seed {seed} {name}: index or columns differ")
                        for col in worst:
                            worst[col] = max(worst[col], float(np.abs(x[col].to_numpy() - y[col].to_numpy()).max()))
                        for col in ("temp_ambient_c", "interpolated"):
                            if not x[col].equals(y[col]):
                                failures.append(f"seed {seed} {name}: {col} differs")
            if not old.true_stops(po, months, seed).equals(new.true_stops(pn, months, seed)):
                failures.append(f"seed {seed} {name}: true_stops differ")
            if not old.true_segments(po, months, seed).equals(new.true_segments(pn, months, seed)):
                failures.append(f"seed {seed} {name}: true_segments differ")
            if months >= 13 and old.true_usable_fade(po, months, seed) != new.true_usable_fade(pn, months, seed):
                failures.append(f"seed {seed} {name}: true_usable_fade differs")
            print(f"S0 samples: seed {seed}, {name}: done", flush=True)
    return {"worst": worst, "failures": failures, "n_frames": n_frames, "n_variants": len(variants(seeds[0]))}


def samples_row(raws: list[dict], months: int, seeds) -> dict:
    worst = {k: max(r["worst"][k] for r in raws) for k in raws[0]["worst"]}
    failures = [f for r in raws for f in r["failures"]]
    n_frames = sum(r["n_frames"] for r in raws)
    largest = max(worst["voltage_v"], worst["current_a"], worst["power_w"])
    return {
        "check": "S0a",
        "what": f"synth case against tests/synth.py at {TAG}: {len(seeds)} seeds x {months} months, "
                f"{raws[0]['n_variants']} pack variants, measured and true, 10 and 20 s ({n_frames} monthly frames)",
        "result": f"largest difference {largest:.3g} (V {worst['voltage_v']:.3g}, A {worst['current_a']:.3g}, "
                  f"W {worst['power_w']:.3g}; temperature {worst['temp_pack_c']:.3g} degC); "
                  f"truth tables: {'equal' if not failures else '; '.join(failures)}",
        "pass_if": f"<= {PASS_SAMPLES:g} V, A and W; stops, segments and usable fade equal",
        "passed": bool(largest <= PASS_SAMPLES and not failures),
    }


def copy_drift(months: int = 36, seeds=(1, 2, 3)) -> dict:
    """Information: how far the plant of ``tests/synth.py`` drifts from the BMS's copy.

    ``tests/synth.py`` decides its stops on a copy of the pack whose capacity is that of
    midnight, while its frames come from a pack whose capacity is that of each segment's
    start. The two states of charge drift apart. This row gives, per year of the run, the
    mean difference (plant - copy) of the cell open-circuit voltage at the full and at the
    empty stops, over the seeds.
    """
    import pandas as pd

    from besslib import sim
    from besslib.sim.cell import OcvCurve
    from besslib.sim.run import cell_model

    cell = cell_model(sim.load_case("synth"))
    ocv = OcvCurve(cell.ocv_soc, cell.ocv_v)
    years = months // 12
    diff = {"full": np.zeros((len(seeds), years)), "empty": np.zeros((len(seeds), years))}
    soc_diff = {"full": np.zeros((len(seeds), years)), "empty": np.zeros((len(seeds), years))}
    for i, seed in enumerate(seeds):
        stops = pd.concat([m.truth.stops for m in sim.run("synth", months, seed, measured=False, log_blocks=False)])
        year = (stops["seconds"].to_numpy() // (365.25 * 86400.0)).astype(int)
        dv = ocv(stops["soc"].to_numpy()) - ocv(stops["model_soc"].to_numpy())
        ds = stops["soc"].to_numpy() - stops["model_soc"].to_numpy()
        for kind in diff:
            for y in range(years):
                k = (stops["kind"].to_numpy() == kind) & (year == y)
                diff[kind][i, y] = 1000.0 * dv[k].mean()
                soc_diff[kind][i, y] = 100.0 * ds[k].mean()
    text = "; ".join(
        f"{kind}: " + ", ".join(f"year {y + 1} {diff[kind][:, y].mean():+.1f} mV ({soc_diff[kind][:, y].mean():+.2f} points)"
                                for y in range(years))
        for kind in diff
    )
    return {
        "check": "S0c",
        "what": f"information: tests/synth.py's plant against its BMS copy at the stops, cell OCV (plant - copy), "
                f"mean over {len(seeds)} seeds x {months} months",
        "result": text,
        "pass_if": "information",
        "passed": "",
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def published() -> list[dict]:
    """Rebuild V6, X3 and W7 on ``besslib.sim`` into ``data/sim_s0/`` and compare bytes."""
    import synth

    if not hasattr(synth.SynthPack, "case"):
        raise SystemExit("tests/synth.py is not the besslib.sim wrapper")
    import synthetic_coverage
    import synthetic_lag
    import synthetic_phase3

    for module in (synthetic_coverage, synthetic_lag, synthetic_phase3):
        if module.frames is not synth.frames:
            raise SystemExit(f"{module.__name__} did not pick up the simulator's frames: run this part on its own")
    OUT.mkdir(parents=True, exist_ok=True)
    synthetic_coverage.RESULTS = OUT
    synthetic_lag.RESULTS = OUT
    synthetic_phase3.RESULTS = OUT
    synthetic_phase3.OUT = OUT / PUBLISHED["W7"]  # merged() still reads the published file
    synthetic_coverage.main([])
    synthetic_lag.main([])
    synthetic_phase3.main(["--only", "w7"])
    return compare()


RUN_TIME = {"W7": "w7_seconds"}  # wall-clock seconds of the run that wrote the part: not a result


def compare() -> list[dict]:
    """The files rebuilt by ``published`` against the published ones: byte for byte, and, where a
    file also holds a run time, every result column equal with the run-time column left out."""
    import pandas as pd

    rows = []
    for check, name in PUBLISHED.items():
        mine, theirs = OUT / name, ROOT / "validation" / "results" / name
        same_bytes = mine.read_bytes() == theirs.read_bytes()
        result = f"byte-identical: sha256 {_sha(mine)[:12]}" if same_bytes else (
            f"bytes differ: sha256 {_sha(mine)[:12]} (published {_sha(theirs)[:12]})")
        passed = same_bytes
        if not same_bytes and check in RUN_TIME:
            a, b = pd.read_csv(mine), pd.read_csv(theirs)
            col = RUN_TIME[check]
            differ = [c for c in a.columns if c != col and not a[c].equals(b[c])] if list(a.columns) == list(b.columns) else ["columns"]
            passed = not differ
            result += (f"; every result column equal ({a.shape[1] - 1} of {a.shape[1]} columns, {len(a)} packs); "
                       f"only {col} differs, the run time (mean {a[col].mean():.1f} s here, {b[col].mean():.1f} s published)"
                       if passed else f"; result columns that differ: {', '.join(differ)}")
        rows.append({
            "check": f"S0b {check}",
            "what": f"{check} rebuilt on besslib.sim by sim_s0.py published, 200 packs "
                    f"(data/sim_s0/{name} against validation/results/{name})",
            "result": result,
            "pass_if": "the published numbers exactly: byte-identical, or every result column equal (run time left out)",
            "passed": passed,
        })
    return rows

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("published", "compare"):
        rows = published() if args[0] == "published" else compare()
        if "--json" in args:
            print(json.dumps(rows))
        else:
            for row in rows:
                print(row)
    elif args and args[0] == "drift":
        print(copy_drift())
    else:
        months = int(args[1]) if len(args) > 1 else 36
        seeds = tuple(int(s) for s in args[2].split(",")) if len(args) > 2 else (1, 2, 3)
        print(samples(months, seeds))
