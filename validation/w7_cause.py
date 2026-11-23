"""Version 0.2, check X1: the cause of W7's bias in the apparent charge loss.

W7 (``synthetic_efficiency.check_w7``) finds the apparent charge loss biased on the 200
synthetic packs (+4.43 mA on a 50 mA drain at release 0.1.0). The suspected cause: the
capacity fit gives every pair of same-kind rests (full to full, empty to empty) a change
of state of charge ``x = 0``, but the BMS stops at a voltage, so two stops of one kind are
not at the same state of charge.

This script fits each pack twice, as W7 does (same pairs, weights, forgetting and date):

1. with the labels ``x`` (+1, -1, 0), which must give W7's numbers again;
2. with the simulator's true change of state of charge between the two rests of each pair
   in place of ``x`` (no pair then has ``x = 0``, so the kinds of pair are counted from
   ``kind``, not from ``x``). Its value is the fit's point value; its band sigma is given
   where the core can compute one (``true_status``).

X1 passes when the mean error of the second fit lies within 2 x sd / sqrt(200) of zero.
The date of each pack is the last ``estimated`` month end of the first fit (W7's date).

A third fit, for information, also puts the exact charge through the current sensor (the
pack's own current, without gain error, noise or 10 s sampling) in place of the measured
charge: what remains of the error after the second fit and not after the third comes from
the measured charge, not from the labels. All three run at the settings of release 0.1.0.

Usage::

    python validation/w7_cause.py                  # 200 packs, 3 processes
    python validation/w7_cause.py --seeds 2        # a quick look

Writes ``validation/results/w7_cause.csv`` (one row per pack) and prints the summary.
"""

from __future__ import annotations

import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tests"))

from synthetic_efficiency import check_w7  # noqa: E402
from synthetic_phase3 import MONTHS, N_PACKS, simulate  # noqa: E402
from synth import _days, _segments  # noqa: E402

from besslib.soh import _core  # noqa: E402
from besslib.soh.capacity import _solve, _z, capacity_state_at, days, estimate_row  # noqa: E402
from besslib.soh.charge_loss import charge_loss  # noqa: E402
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash  # noqa: E402
from besslib.soh.rests import levels_from, operating_rests, rest_chain, rest_pairs  # noqa: E402

OUT = HERE / "results" / "w7_cause.csv"
CROSS = ("F->E", "E->F")


def true_soc(pack, months: int, seed: int, times, charge: bool = False) -> np.ndarray:
    """The simulator's own state of charge at ``times``, from the same draws as ``frames``.

    With ``charge`` it is instead the exact charge (Ah) through the current sensor since
    the start: the pack's own current, without the hidden drain, gain error, noise or
    sampling.
    """
    rng = np.random.default_rng(seed)
    segs = _segments(pack, _days(pack, months), rng)
    starts = np.array([s[0] for s in segs])
    lengths = np.array([s[1] for s in segs])
    currents = np.array([s[2] for s in segs])
    t0 = pd.Timestamp(pack.start)
    caps = pack.true_capacity_ah(t0 + pd.to_timedelta(starts, unit="s"))
    s = (pd.DatetimeIndex(times) - t0).total_seconds().to_numpy()
    k = np.searchsorted(starts, s, side="right") - 1
    if charge:
        q0 = np.concatenate([[0.0], np.cumsum(currents * lengths)[:-1]]) / 3600.0
        return q0[k] + currents[k] * (s - starts[k]) / 3600.0
    soc0 = np.empty(len(segs))
    soc = 0.5
    for j in range(len(segs)):  # as in ``synth.frames``
        soc0[j] = soc
        soc += (currents[j] + pack.hidden_current_a) * lengths[j] / (3600.0 * caps[j])
    return soc0[k] + (currents[k] + pack.hidden_current_a) * (s - starts[k]) / (3600.0 * caps[k])


def estimate_by_kind(pairs: pd.DataFrame, spec, config: CapacityConfig, at, ready: float) -> pd.DataFrame:
    """``estimate_capacity`` with the kinds of pair counted from ``kind`` instead of ``x != 0``.

    Adds ``beta_point_ah_per_day``, the fitted offset term from the normal equations, also
    where the core gives no band (its status then says why).
    """
    z = _z(config.level)
    t_end = days(pairs["t_end"])
    cross = pairs["kind"].isin(CROSS).to_numpy()
    rows, points = [], []
    for t in days(at):
        if t < ready:
            est = _core.Estimate()
            est.status = _core.NO_LEVELS
        else:
            state = capacity_state_at(pairs, spec, config, float(t))
            keep = t_end <= t
            lam = np.exp(-(t - t_end[keep]) / config.tau_days)
            c = cross[keep]
            state.m_cross = float(lam[c].sum())
            state.m_same = float(lam[~c].sum())
            state.t_last_cross = float(t_end[keep][c].max()) if c.any() else _core._NEVER
            est = _solve(state, float(t), z, config)
            inv = [0.0] * 25  # 5 x 5 layout of the 0.2 core
            if _core._invert(2, state.a, inv):  # the point value, also where the band cannot be computed
                points.append(inv[5] * state.h[0] + inv[6] * state.h[1])
        rows.append(estimate_row(est, spec))
        if len(points) < len(rows):
            points.append(math.nan)
    out = pd.DataFrame(rows, index=pd.DatetimeIndex(at))
    out.index.name = "time"
    out["beta_point_ah_per_day"] = points
    return out


def one_pack(seed: int) -> dict:
    config, capacity_config = RecordConfig().v0_1(), CapacityConfig().v0_1()  # W7 as published in 0.1.0
    run = simulate(seed, parts=["w7"])
    labels = check_w7(run, config, capacity_config)
    rests = operating_rests(run.reduced.rests, run.spec, capacity_config)
    pairs = rest_pairs(rest_chain(rests, run.spec, capacity_config), capacity_config)
    soc_a = true_soc(run.pack, MONTHS, seed, pairs["t_start"])
    soc_b = true_soc(run.pack, MONTHS, seed, pairs["t_end"])
    true_pairs = pairs.assign(x=soc_b - soc_a)
    truth = -1000.0 * run.pack.hidden_current_a
    row = {
        "seed": seed,
        "date": labels["w7_time"],
        "truth_ma": truth,
        "labels_ma": labels["w7_value_ma"],
        "labels_error_ma": labels["w7_error_ma"],
        "labels_sigma_ma": labels["w7_sigma_ma"],
        "labels_status": labels["w7_status"],
        "n_pairs": len(pairs),
        "n_same_kind": int((~pairs["kind"].isin(CROSS)).sum()),
        "x_true_same_mean": float(true_pairs.loc[~pairs["kind"].isin(CROSS), "x"].mean()),
        "x_true_same_sd": float(true_pairs.loc[~pairs["kind"].isin(CROSS), "x"].std()),
    }
    if not labels["w7_time"]:
        row.update(true_ma=math.nan, true_error_ma=math.nan, true_sigma_ma=math.nan, true_status="no date")
        row.update(exact_ma=math.nan, exact_error_ma=math.nan, exact_sigma_ma=math.nan, exact_status="no date")
        return row
    date = pd.Timestamp(labels["w7_time"])
    ready = float(days([levels_from(rests, capacity_config)])[0])
    cap = estimate_by_kind(true_pairs, run.spec, capacity_config, [date], ready)
    loss = charge_loss(cap, run.spec, level=config.level, config=config).iloc[0]
    value = 1000.0 * float(cap["beta_point_ah_per_day"].iloc[0]) / 24.0  # = -1000 x offset current
    row.update(
        true_ma=value,
        true_error_ma=value - truth,
        true_sigma_ma=loss["charge_loss_sigma_ma"],
        true_status=loss["status"],
    )
    # Third fit: the true change and the exact charge through the sensor (no gain, noise or sampling).
    q_a = true_soc(run.pack, MONTHS, seed, pairs["t_start"], charge=True)
    q_b = true_soc(run.pack, MONTHS, seed, pairs["t_end"], charge=True)
    exact_pairs = true_pairs.assign(q_ah=q_b - q_a)
    cap = estimate_by_kind(exact_pairs, run.spec, capacity_config, [date], ready)
    loss = charge_loss(cap, run.spec, level=config.level, config=config).iloc[0]
    value = 1000.0 * float(cap["beta_point_ah_per_day"].iloc[0]) / 24.0
    row.update(
        exact_ma=value,
        exact_error_ma=value - truth,
        exact_sigma_ma=loss["charge_loss_sigma_ma"],
        exact_status=loss["status"],
    )
    return row


def main(argv: list[str]) -> None:
    n = N_PACKS
    if "--seeds" in argv:
        n = int(argv[argv.index("--seeds") + 1])
    processes = int(argv[argv.index("--processes") + 1]) if "--processes" in argv else 3
    with Pool(processes) as pool:
        rows = pool.map(one_pack, range(1, n + 1), chunksize=1)  # seeds 1..200, as synthetic_phase3
    table = pd.DataFrame(rows).sort_values("seed")
    table["capacity_config_hash"] = config_hash(CapacityConfig().v0_1())
    table["record_config_hash"] = config_hash(RecordConfig().v0_1())
    if n == N_PACKS:
        table.to_csv(OUT, index=False)
    for name in ("labels", "true", "exact"):
        e = table[f"{name}_error_ma"].dropna()
        limit = 2.0 * e.std() / math.sqrt(len(e))
        print(
            f"{name:6s}: n {len(e)}, mean error {e.mean():+.2f} mA, sd {e.std():.2f} mA, "
            f"limit +-{limit:.2f} mA, median band sigma {table[f'{name}_sigma_ma'].median():.2f} mA "
            f"({table[f'{name}_sigma_ma'].notna().sum()} packs with a band), "
            f"within limit: {abs(e.mean()) <= limit}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
