"""Version 0.2, check X7: does the capacity band hold the true capacity on simulated pair chains?

300 chains of pairs of rests, built here with a fixed seed, without fade: a 100 Ah home
storage pack whose usable capacity between its stops is 95 Ah, read at its full and empty
rests for two years. The rests come about once a day (0.3 to 1.2 days apart); the next
rest is of the other kind with probability 0.6, so the chain holds full-to-empty,
empty-to-full, full-to-full and empty-to-empty pairs. About one pair in a hundred breaks
the chain (a data gap): the next pair starts at a fresh rest and is not linked.

Each rest misses its nominal state of charge (1 at full, 0 at empty) by an error of
1.5 % (normal), shared by the pair that ends there and the pair that starts there. Its
relaxed open-circuit voltage moves with that error, by 7 V per unit of state of charge
at full and 10 V at empty (a 14-cell pack), plus 10 mV of noise. The meter adds a random
error of 0.2 % of the charge moved and a drain of 50 mA (``beta`` = -1.2 Ah per day).
The pairs carry the weights of ``rests.rest_pairs`` (``sigma_q_rel`` x charge moved, and
``sigma_soc_edge`` per rest).

The band at the date of the last pair (plus half a day) is read with the 0.2 settings,
with 180 days of memory and without memory (``tau_days = 0``, the whole record). The
chains have no gain error, so the check is on the band's statistical part: the gain
error's systematic term is set to zero (``gain_error = 0``). For comparison, the same is
reported with ``band_forgetting = 1`` (the forgetting weight once in the band's sums, as
in release 0.1.0) and with the frozen gain term.

Pass (X7): the band holds 95 Ah in 85-95 % of the chains with an estimate, with 180 days
of memory and without, at the 0.2 settings and ``gain_error = 0``.

Information, chosen after the first results: with the voltage terms the estimate is the
capacity between the full and the empty reference voltage at the date (the weighted mean
rest voltage of each kind), not between the nominal states of charge 1 and 0. On these
chains that capacity is ``95 x (1 + (u_full - 57.4) / 7 - (u_empty - 44.8) / 10)``; the
table also says whether the band holds it (``held_reference``).

Writes ``validation/results/capacity_chains.csv`` (one row per chain and setting) and
prints the summary and the configuration hashes.

Usage::

    python validation/capacity_chains.py           # 300 chains
    python validation/capacity_chains.py 30        # fewer chains
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

from besslib.frames import PackSpec  # noqa: E402
from besslib.soh import _core  # noqa: E402
from besslib.soh.capacity import _solve, _z, capacity_state_at, days, run_core  # noqa: E402
from besslib.soh.config import CapacityConfig, config_hash  # noqa: E402

RESULTS = ROOT / "validation" / "results"
PASS_RANGE = (0.85, 0.95)
SEED = 20261123

NOMINAL_AH = 100.0
USABLE_AH = 95.0
YEARS = 2.0
P_CROSS = 0.6
P_BREAK = 0.01
SOC_ERROR = 0.015
SLOPE_FULL_V = 7.0
SLOPE_EMPTY_V = 10.0
OCV_NOISE_V = 0.010
METER_REL = 0.002
BETA_AH_PER_DAY = -0.05 * 24.0
SPEC = PackSpec(nominal_capacity_ah=NOMINAL_AH, nominal_energy_kwh=5.0, cells_in_series=14)
EPOCH = pd.Timestamp("2000-01-01")


def chain(rng: np.random.Generator, config: CapacityConfig) -> pd.DataFrame:
    """One chain of pairs, as ``rests.rest_pairs`` gives them."""
    n_rest = int(YEARS * 365.25 / 0.75)
    t = 8000.0 + np.cumsum(rng.uniform(0.3, 1.2, n_rest))
    full = np.empty(n_rest, dtype=int)
    full[0] = rng.integers(0, 2)
    switch = rng.uniform(size=n_rest) < P_CROSS
    for k in range(1, n_rest):
        full[k] = 1 - full[k - 1] if switch[k] else full[k - 1]
    eps_end = rng.normal(0.0, SOC_ERROR, n_rest)  # the error of each rest
    eps_fresh = rng.normal(0.0, SOC_ERROR, n_rest)  # a fresh start rest after a break
    u_noise = rng.normal(0.0, OCV_NOISE_V, (2, n_rest))
    broken = rng.uniform(size=n_rest) < P_BREAK
    broken[0] = True
    a, b = np.arange(n_rest - 1), np.arange(1, n_rest)
    eps_a = np.where(broken[b], eps_fresh[b], eps_end[a])  # the start rest of the pair
    eps_b = eps_end[b]
    ocv = lambda f, e, noise: np.where(f == 1, 57.4 + SLOPE_FULL_V * e, 44.8 + SLOPE_EMPTY_V * e) + noise  # noqa: E731
    u_a = np.where(broken[b], ocv(full[a], eps_a, u_noise[1, b]), ocv(full[a], eps_a, u_noise[0, a]))
    u_b = ocv(full[b], eps_b, u_noise[0, b])
    x = (full[b] - full[a]).astype(float)
    dt = t[b] - t[a]
    depth = rng.uniform(0.2, 0.8, len(a))  # cycling between the rests, in units of the capacity
    throughput = USABLE_AH * (np.abs(x) + 2.0 * depth)
    q = USABLE_AH * (full[b] + eps_b - full[a] - eps_a) + BETA_AH_PER_DAY * dt
    q = q + rng.normal(0.0, 1.0, len(a)) * METER_REL * throughput
    kind = np.array(["E", "F"])[full[a]].astype(object) + "->" + np.array(["E", "F"])[full[b]].astype(object)
    return pd.DataFrame(
        {
            "t_end": EPOCH + pd.to_timedelta(t[b], unit="D"),
            "kind": kind,
            "x": x,
            "dt_days": dt,
            "q_ah": q,
            "throughput_ah": throughput,
            "sigma_q_ah": config.sigma_q_rel * throughput,
            "sigma_x": np.sqrt(2.0) * config.sigma_soc_edge,
            "linked": (~broken[b]).astype(int),
            "u_start": u_a,
            "u_end": u_b,
        }
    )


SETTINGS = {
    "0.2, gain 0": CapacityConfig(gain_error=0.0),
    "0.2 with band_forgetting 1, gain 0": CapacityConfig(gain_error=0.0, band_forgetting=1),
    "0.2, frozen gain term": CapacityConfig(),
}
MEMORIES = (180.0, 0.0)


def main(argv: list[str]) -> None:
    n = int(argv[0]) if argv else 300
    rng = np.random.default_rng(SEED)
    chains = [chain(rng, CapacityConfig()) for _ in range(n)]
    rows = []
    worst_v7 = 0.0
    for name, base in SETTINGS.items():
        for tau in MEMORIES:
            config = base.with_(tau_days=tau)
            z = _z(config.level)
            for i, pairs in enumerate(chains):
                at = float(days(pairs["t_end"].iloc[[-1]])[0]) + 0.5
                est = _solve(capacity_state_at(pairs, SPEC, config, at), at, z, config)
                if i < 5 and name == "0.2, gain 0":  # V7 on chains: the step-by-step core gives the same
                    c = run_core(pairs, SPEC, config, [EPOCH + pd.Timedelta(days=at)])[0]
                    if c.status == est.status == _core.OK:
                        worst_v7 = max(worst_v7, abs(c.theta[0] - est.theta[0]) / abs(est.theta[0]),
                                       abs(c.high - est.high) / abs(est.high))
                ok = est.status == _core.OK
                ref = USABLE_AH
                if ok and np.isfinite(est.u_full) and np.isfinite(est.u_empty):
                    ref = USABLE_AH * (1.0 + (est.u_full - 57.4) / SLOPE_FULL_V - (est.u_empty - 44.8) / SLOPE_EMPTY_V)
                rows.append({
                    "chain": i,
                    "setting": name,
                    "tau_days": tau,
                    "band_forgetting": config.band_forgetting,
                    "gain_error": config.gain_error,
                    "status": _core.STATUS_TEXT[est.status],
                    "true_capacity_ah": USABLE_AH,
                    "capacity_ah": est.theta[_core.I_C] if ok else np.nan,
                    "low": est.low if ok else np.nan,
                    "high": est.high if ok else np.nan,
                    "held": bool(ok and est.low <= USABLE_AH <= est.high),
                    "reference_capacity_ah": ref,
                    "held_reference": bool(ok and est.low <= ref <= est.high),
                    "sigma_ah": est.sigma[_core.I_C] if ok else np.nan,
                    "dof": est.dof if ok else np.nan,
                    "n_pairs": est.m,
                    "terms": est.terms,
                    "config_hash": config_hash(config),
                })
    out = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.to_csv(RESULTS / "capacity_chains.csv", index=False, float_format="%.6f")

    print(f"X7: {n} chains, true capacity {USABLE_AH:g} Ah, no fade, seed {SEED}")
    print(f"0.2 settings: config hash {config_hash(CapacityConfig())}")
    verdict = []
    for (name, tau), g in out.groupby(["setting", "tau_days"], sort=False):
        ok = g[g["status"] == _core.STATUS_TEXT[_core.OK]]
        cover = ok["held"].mean()
        err = ok["capacity_ah"] - USABLE_AH
        memory = f"{tau:g} days" if tau > 0 else "none"
        err_ref = ok["capacity_ah"] - ok["reference_capacity_ah"]
        line = (f"  {name:36s} memory {memory:9s}: coverage {cover:.3f} ({int(ok['held'].sum())} of {len(ok)}), "
                f"error mean {err.mean():+.3f} Ah, sd {err.std():.3f} Ah, median sigma {ok['sigma_ah'].median():.3f} Ah, "
                f"median dof {ok['dof'].median():.1f}, hash {g['config_hash'].iloc[0]}; information: against the "
                f"capacity at the reference voltages coverage {ok['held_reference'].mean():.3f}, error mean "
                f"{err_ref.mean():+.3f} Ah, sd {err_ref.std():.3f} Ah")
        print(line)
        if name == "0.2, gain 0":
            verdict.append(PASS_RANGE[0] <= cover <= PASS_RANGE[1])
    print(f"  core against batch on 5 chains: max relative difference {worst_v7:.2e}")
    print(f"X7 {'pass' if all(verdict) else 'fail'}: coverage in {PASS_RANGE[0]:.0%}-{PASS_RANGE[1]:.0%} with 180 days "
          "of memory and without, 0.2 settings, gain 0")


if __name__ == "__main__":
    main(sys.argv[1:])
