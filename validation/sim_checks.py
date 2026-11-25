"""The simulator's own checks S0-S8: writes ``validation/results/sim_checks.csv``.

One row per check, with what it ran, its result, its pass number and whether it passed.
Rows of checks not named on the command line are kept from the file on disk, so a long
check can run on its own.

* S0a, S0c (``sim_s0.py``): the ``synth`` case against ``tests/synth.py`` of 0.2.0.
* S0b: V6, X3 and W7 rebuilt on the simulator (``S0b-rebuild``, about an hour) and compared
  with the published files byte for byte (``S0b`` compares files already rebuilt).
* S1, S2, S3, S4 (``sim_plant_checks.py``): charge, energy, closed forms, BMS limits.
* S5, S6, S8 (``sim_run_checks.py``): reproducible files, the data contract, speed.
* S10 (``sim_s10.py``): the LFP preset against measured HPPC currents at 20 and 30 degrees C.
S1-S6 and S8 run on every shipped case (``besslib.sim.case_names()``); the 396-group string runs 2 months in S1, S2
and S4, and S6 runs 3 packs of 14 months of every case, the string included (about 10 s per pack-month).

Usage:
    python validation/sim_checks.py                 # every check but S0b
    python validation/sim_checks.py S0b-rebuild     # only the named checks
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))
RESULTS = ROOT / "validation" / "results" / "sim_checks.csv"
COLUMNS = ["check", "what", "result", "pass_if", "passed"]
ORDER = ["S0a", "S0b V6", "S0b X3", "S0b W7", "S0c", "S1", "S2", "S3", "S4", "S5", "S6", "S8", "S10"]


def s0a() -> list[dict]:
    import sim_s0

    return [sim_s0.samples(36, (1, 2, 3))]


def s0b(mode: str = "compare") -> list[dict]:
    # A process of its own: the checks it rebuilds import ``synth`` at import time.
    out = subprocess.run([sys.executable, str(ROOT / "validation" / "sim_s0.py"), mode, "--json"],
                         cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def s0b_rebuild() -> list[dict]:
    return s0b("published")


def s0c() -> list[dict]:
    import sim_s0

    return [sim_s0.copy_drift(36, (1, 2, 3))]


def s5() -> list[dict]:
    import sim_run_checks

    r = sim_run_checks.check_s5()
    cases = ", ".join(f"{x['case']} ({x['files']} files)" for x in r["rows"])
    return [{
        "check": "S5",
        "what": f"each case written twice with seed 1 and once with seed 2, {r['rows'][0]['months']} months: {cases}",
        "result": f"same seed: {r['worst_same_seed_differences']} files differ; "
                  f"other seed: {r['worst_other_seed_equal_frames']} frame files equal",
        "pass_if": "0 files differ with the same seed; 0 frame files equal with another seed",
        "passed": bool(r["pass"]),
    }]


def s6() -> list[dict]:
    import sim_run_checks

    months = 14
    r = sim_run_checks.check_s6(packs=3, months=months)
    rule = r["import_rule"]
    def a1_text(x: dict) -> str:
        true = x["true_capacity_ah_last_stop"]
        true_text = f"{true:.2f} Ah" if true is not None else "no full or empty stop"
        if x["a1_status"] != "estimated":
            return f"{x['case']} seed {x['seed']}: A1 {x['a1_status']}, true capacity at the last stop {true_text}"
        return (f"{x['case']} seed {x['seed']}: A1 {x['a1_value_ah']:.2f} Ah ({x['a1_low_ah']:.2f}-{x['a1_high_ah']:.2f}), "
                f"true capacity at the last stop {true_text}")

    a1 = "; ".join(a1_text(x) for x in r["rows"])
    estimated = sum(x["a1_status"] == "estimated" for x in r["rows"])
    return [{
        "check": "S6",
        "what": f"{r['packs_per_case']} packs of each case ({', '.join(r['cases'])}), {months} months: every frame "
                f"through check_frame, besslib 0.2 annex_vii under warnings as errors; the import rule "
                f"({rule['files_scanned']['sim']} sim and {rule['files_scanned']['soh']} soh modules, read with ast)",
        "result": f"{r['n_failures']} failures; A1 estimated in {estimated} of {len(r['rows'])} packs"
                  f". Information, A1 as read (the 0.2 quantity, between rest voltages): {a1}",
        "pass_if": "0 failures",
        "passed": bool(r["pass"]),
    }]


def s10() -> list[dict]:
    """The LFP preset against the measured HPPC current at 20 and 30 degrees C (``sim_s10.check_s10``)."""
    import sim_s10

    out = sim_s10.check_s10()
    rows = [out] if isinstance(out, dict) else list(out)
    return [{c: row[c] for c in COLUMNS} for row in rows]


def s8() -> list[dict]:
    import sim_run_checks

    r = sim_run_checks.check_s8()
    text = "; ".join(
        f"{x['case']} ({x['months']} months): {x['seconds_per_pack_month']:.2f} s per pack-month, "
        f"200 packs x 36 months about {x['packs_200_months_36_hours_extrapolated']:.1f} h on one core (extrapolated)"
        for x in r["rows"]
    )
    return [{
        "check": "S8",
        "what": "information: speed at 10 s steps, best of 2 runs of 3 months (the 396-group string 2 months), one core",
        "result": text,
        "pass_if": "information",
        "passed": "",
    }]


PLANT_SEEDS = (1, 2, 3)
STRING_CASE = "grid-string"


def plant_runs() -> tuple:
    """(case, months): synth 36 and demo 12 as before, every other shipped case 12, the string 2."""
    from besslib import sim

    first = (("synth", 36), ("demo", 12))
    rest = tuple((c, 2 if c == STRING_CASE else 12) for c in sim.case_names() if c not in ("synth", "demo"))
    return first + rest


def plant() -> list[dict]:
    """S1, S2 and S4 on 3 seeds of each case; S3 on the closed forms."""
    import sim_plant_checks as pc

    from besslib import sim

    res = {"S1": [], "S2": [], "S4": []}
    runs_list = plant_runs()
    for case, months in runs_list:
        data = sim.load_case(case)
        for seed in PLANT_SEEDS:
            pack, settings = sim.build(data, seed)
            out = [m.truth for m in sim.run(data, months, seed, measured=False, log_blocks=False)]
            res["S1"].append((case, seed, pc.s1_charge(out)))
            res["S2"].append((case, seed, pc.s2_energy(out, pack)))
            res["S4"].append((case, seed, pc.s4_bms(out, pack, settings.get("update_s"))))
        # The same case without fade: S2's stored energy then comes from the month-end states of
        # charge, a path independent of the engine's block sums.
        flat = sim.load_case(case, {"pack.fade_points_per_year": 0.0})
        for seed in PLANT_SEEDS:
            pack, _ = sim.build(flat, seed)
            out = [m.truth for m in sim.run(flat, min(months, 12), seed, measured=False, log_blocks=False)]
            res["S2"].append((case + " no fade", seed, pc.s2_energy(out, pack)))
    runs = ", ".join(f"{c} {m} months" for c, m in runs_list)
    what = f"3 seeds each of {runs}, true signals"
    what_s2 = f"{what}; and each case without fade, up to 12 months"
    rows = []
    for key, label, pass_if in (("S1", "charge per group and month", "<= 1e-09 relative"),
                                ("S2", "energy per group and month", "<= 1e-06 relative")):
        worst = max(r["worst"] for _, _, r in res[key])
        paths = sorted({r["path"] for _, _, r in res[key] if "path" in r})
        unit = res[key][0][2]["unit"]
        by_path = {}
        for _, _, r in res[key]:
            k = r.get("path", "")
            by_path[k] = max(by_path.get(k, 0.0), r["worst"])
        detail = "; ".join(f"{k}: worst {v:.2g}" for k, v in sorted(by_path.items()) if k)
        rows.append({"check": key, "what": f"{label}: {what_s2 if key == 'S2' else what}; {unit}",
                     "result": f"worst {worst:.2g} over {sum(r['n'] for _, _, r in res[key])} group-months"
                               + (f" ({detail})" if detail else ""),
                     "pass_if": pass_if, "passed": all(r["passed"] for _, _, r in res[key])})
    parts = []
    for case, _ in runs_list:
        rr = [r for c, _, r in res["S4"] if c == case]
        parts.append(f"{case} (rule {rr[0]['rule']}): {sum(r['bad_stops'] for r in rr)} stops outside their step "
                     f"of {sum(r['n_stops'] for r in rr)}, {sum(r['bad_samples'] for r in rr)} samples with current "
                     f"beyond the limit of {sum(r['n_samples'] for r in rr)}")
    rows.append({"check": "S4", "what": f"BMS limits and stops: {what}. The synth rule checks the BMS's own copy "
                                        "of the pack, so for it only the stops count and its samples are information",
                 "result": "; ".join(parts), "pass_if": "0", "passed": all(r["passed"] for _, _, r in res["S4"])})
    rows.insert(2, s3())
    return rows


def s3() -> dict:
    """Closed forms: the R0 step and the RC relaxation; one thermal node's steady state and time constant."""
    import math

    import numpy as np

    from besslib.sim.cell import GroupParams, GroupState, OcvCurve, advance, trajectory
    from besslib.sim.thermal import Network, ThermalModel

    soc_pts = [0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0, 1.1]
    v_pts = [3.0, 3.40, 3.50, 3.60, 3.75, 3.95, 4.05, 4.15, 4.30]
    ocv = OcvCurve(soc_pts, v_pts)
    one = lambda x: np.array([float(x)])  # noqa: E731
    p = GroupParams(series=one(14), capacity_ah=one(100), r0=one(0.02), r1=one(0.01), tau1=one(600), r2=one(0),
                    tau2=one(1), h_max=one(0), kappa=one(0))
    s0 = GroupState(one(0.5), one(0), one(0), one(0))
    elec = 0.0
    for i in (50.0, -30.0):
        dur = 1800.0
        _, rest = trajectory(s0, p, ocv, one(0), one(0), [0.0])
        _, on = trajectory(s0, p, ocv, one(i), one(i), [0.0])
        elec = max(elec, abs((on[0, 0] - rest[0, 0]) - i * 0.02))
        s1, _ = advance(s0, p, ocv, one(i), one(i), dur)
        soc1 = 0.5 + i * dur / (3600.0 * 100.0)
        times = np.array([0.0, 10.0, 60.0, 600.0, 3600.0])
        _, relax = trajectory(s1, p, ocv, one(0), one(0), times)
        want = 14.0 * float(np.interp(soc1, soc_pts, v_pts)) + i * 0.01 * (1 - math.exp(-dur / 600.0)) * np.exp(-times / 600.0)
        elec = max(elec, float(np.abs(relax[:, 0] - want).max()))
    therm = 0.0
    for fan in (1.0, 0.25):
        m = ThermalModel(mode="network", c_group_j_k=8000.0, c_housing_j_k=1000.0, g_group_housing_w_k=0.0,
                         g_neighbour_w_k=0.0, g_group_air_w_k=2.5, g_housing_air_w_k=4.0)
        net = Network(m, groups=1, fan=fan)
        q, t_air, t0 = 12.0, 20.0, 35.0
        ha, mc = 2.5 * fan, 8000.0
        ss = net.steady(np.array([q, 0.0]), t_air)[0]
        therm = max(therm, abs(ss - (t_air + q / ha)) / (t_air + q / ha))
        tau = 1.0 / float(np.min(net.lam))
        therm = max(therm, abs(tau - mc / ha) / (mc / ha))
    return {"check": "S3", "what": "closed forms: R0 step and RC relaxation (charge and discharge, 1RC, 14 cells, "
                                   "0.02 and 0.01 ohm, 600 s); one thermal node (fan 1 and 0.25): steady state "
                                   "T_air + q/(hA) and time constant m c/(hA)",
            "result": f"electrical worst {elec:.2g} V; thermal worst {therm:.2g} relative",
            "pass_if": "<= 1e-12 V; <= 1e-06 relative", "passed": bool(elec <= 1e-12 and therm <= 1e-6)}


RUNS = {"S0a": s0a, "S0b": s0b, "S0b-rebuild": s0b_rebuild, "S0c": s0c, "plant": plant, "S5": s5, "S6": s6, "S8": s8,
        "S10": s10}
DEFAULT = ["S0a", "S0c", "plant", "S5", "S6", "S8", "S10"]


def main(argv: list[str]) -> None:
    names = argv or DEFAULT
    unknown = [n for n in names if n not in RUNS]
    if unknown:
        raise SystemExit(f"unknown checks {unknown}; known: {list(RUNS)}")
    new = []
    for name in names:
        new.extend(machine(RUNS[name]()))
    write(new)


def machine(rows: list[dict]) -> list[dict]:
    """Name the processor the rows were computed on (results agree across processors to about
    1e-13; bit for bit only on one)."""
    import platform

    for row in rows:
        row["what"] = f"{row['what']} [computed on {platform.machine()}]"
    return rows


def write(new: list[dict]) -> None:
    for row in new:
        print(f"{row['check']}: {row['result']}  [{row['pass_if']}] -> {row['passed']}", flush=True)
    old = pd.read_csv(RESULTS, keep_default_na=False) if RESULTS.exists() else pd.DataFrame(columns=COLUMNS)
    keep = old[~old["check"].isin([r["check"] for r in new])]
    table = pd.concat([keep, pd.DataFrame(new, columns=COLUMNS)], ignore_index=True)
    table["_order"] = table["check"].map({c: k for k, c in enumerate(ORDER)}).fillna(len(ORDER))
    table = table.sort_values(["_order", "check"]).drop(columns="_order")
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS, index=False)
    print(f"written: {RESULTS.relative_to(ROOT)} ({len(table)} rows)")


if __name__ == "__main__":
    main(sys.argv[1:])
