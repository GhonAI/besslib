"""Check S3 for the cell group (``besslib.sim.cell``): closed forms against analytic values,
block integrals against a fine numerical integration, and the saved test vectors.

The numerical integration is Gauss-Legendre with 16 nodes on each of 64 equal pieces of a
block, with the pieces also split where the state of charge crosses a point of the
open-circuit curve (there the integrand has a kink). The reference voltage is written out
here in scalar arithmetic, apart from ``cell.py``.

Every parameter value here is an example value, not from a source. The curve is the
``synth`` preset's (example values, not from a source).

The vectors ``tests/vectors/sim_cell_steps.csv`` hold one group's blocks: inputs (start
state, parameters, duration, terminal current, drain) and outputs (end state, voltage at
the block's start and end, the block integrals). Rewrite them with
``python tests/test_sim_cell.py --write-vectors``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from besslib.sim.cell import CellModel, GroupParams, GroupState, OcvCurve, advance, temperature_factor, trajectory

VECTORS = Path(__file__).parent / "vectors"
SOC = (0.0, 0.05, 0.1, 0.2, 0.5, 0.8, 0.9, 1.0, 1.1)  # the synth preset's curve
OCV_V = (3.0, 3.40, 3.50, 3.60, 3.75, 3.95, 4.05, 4.15, 4.30)
OCV = OcvCurve(SOC, OCV_V)
PRM_NAMES = ("series", "capacity_ah", "r0", "r1", "tau1", "r2", "tau2", "h_max", "kappa")


def params(**kw) -> GroupParams:
    base = dict(series=14.0, capacity_ah=100.0, r0=0.02, r1=0.01, tau1=600.0, r2=0.0, tau2=1.0, h_max=0.0, kappa=0.0)
    base.update(kw)
    return GroupParams(**{k: np.atleast_1d(np.asarray(v, dtype=float)) for k, v in base.items()})


def state(soc=0.5, p1=0.0, p2=0.0, h=0.0) -> GroupState:
    return GroupState(*(np.atleast_1d(np.asarray(x, dtype=float)) for x in (soc, p1, p2, h)))


def ref(s: GroupState, p: GroupParams, g: int, i: float, i_soc: float, t: float):
    """The group's state of charge, terminal voltage and open-circuit voltage at ``t``,
    written out in scalars."""
    c = float(p.capacity_ah[g])
    soc = float(s.soc[g]) + i_soc * t / (3600.0 * c)
    p1 = i * float(p.r1[g]) + (float(s.p1[g]) - i * float(p.r1[g])) * math.exp(-t / float(p.tau1[g]))
    p2 = 0.0
    if p.r2[g] > 0:
        p2 = i * float(p.r2[g]) + (float(s.p2[g]) - i * float(p.r2[g])) * math.exp(-t / float(p.tau2[g]))
    h = float(s.h[g])
    if p.h_max[g] > 0:
        beta = float(p.kappa[g]) * abs(i_soc) / (3600.0 * c)
        h_inf = math.copysign(float(p.h_max[g]), i_soc) if i_soc != 0 else 0.0
        h = h_inf + (h - h_inf) * math.exp(-beta * t)
    e = float(p.series[g]) * float(np.interp(soc, SOC, OCV_V))
    return soc, e + h + i * float(p.r0[g]) + p1 + p2, e


def quad(f, a: float, b: float, breaks=()) -> float:
    """Gauss-Legendre, 16 nodes on each of 64 pieces of [a, b], split also at ``breaks``."""
    x, w = np.polynomial.legendre.leggauss(16)
    edges = np.unique(np.r_[np.linspace(a, b, 65), [t for t in breaks if a < t < b]])
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        total += half * sum(wk * f(mid + half * xk) for xk, wk in zip(x, w))
    return total


def soc_breaks(s0: float, rate: float, dur: float):
    """The times in [0, dur] at which the state of charge crosses a curve point."""
    if rate == 0:
        return ()
    return tuple((pt - s0) / rate for pt in SOC if 0 < (pt - s0) / rate < dur)


# --- S3: R0 step and RC relaxation --------------------------------------------------------
@pytest.mark.parametrize("r2", [0.0, 0.004])
def test_r0_step_and_rc_relaxation(r2):
    p = params(r2=r2, tau2=60.0)
    s0 = state(0.5)
    i, dur = 50.0, 1800.0
    _, v_rest = trajectory(s0, p, OCV, np.array([0.0]), np.array([0.0]), [0.0])
    _, v_on = trajectory(s0, p, OCV, np.array([i]), np.array([i]), [0.0])
    assert abs((v_on[0, 0] - v_rest[0, 0]) - i * 0.02) <= 1e-12  # the R0 step
    s1, _ = advance(s0, p, OCV, np.array([i]), np.array([i]), dur)
    soc1 = 0.5 + i * dur / (3600.0 * 100.0)
    assert abs(s1.soc[0] - soc1) <= 1e-15
    p1_end = i * 0.01 * (1.0 - math.exp(-dur / 600.0))
    p2_end = i * r2 * (1.0 - math.exp(-dur / 60.0))
    _, v_end_on = trajectory(s0, p, OCV, np.array([i]), np.array([i]), [dur])
    times = np.array([0.0, 10.0, 60.0, 600.0, 3600.0])
    _, v_relax = trajectory(s1, p, OCV, np.array([0.0]), np.array([0.0]), times)
    e_end = 14.0 * float(np.interp(soc1, SOC, OCV_V))
    expected = e_end + p1_end * np.exp(-times / 600.0) + p2_end * np.exp(-times / 60.0)
    assert np.abs(v_relax[:, 0] - expected).max() <= 1e-12  # the RC relaxation
    assert abs((v_end_on[0, 0] - v_relax[0, 0]) - i * 0.02) <= 1e-12  # the R0 step down


def test_hysteresis_closed_form():
    p = params(h_max=0.28, kappa=30.0)
    s0 = state(0.4, h=-0.1)
    i = 30.0
    t = np.array([0.0, 100.0, 1000.0, 5000.0])
    _, v = trajectory(s0, p, OCV, np.array([i]), np.array([i]), t)
    beta = 30.0 * i / (3600.0 * 100.0)
    for k, tk in enumerate(t):
        soc = 0.4 + i * tk / 360000.0
        h = 0.28 + (-0.1 - 0.28) * math.exp(-beta * tk)
        p1 = i * 0.01 * (1 - math.exp(-tk / 600.0))
        exp_v = 14.0 * float(np.interp(soc, SOC, OCV_V)) + h + i * 0.02 + p1
        assert abs(v[k, 0] - exp_v) <= 1e-12


def test_hysteresis_decays_where_its_bound_is_zero():
    p = params(h_max=0.0, kappa=30.0)
    i, beta = 30.0, 30.0 * 30.0 / 360000.0
    s1, _ = advance(state(0.4, h=-0.1), p, OCV, np.array([i]), np.array([i]), 500.0)
    assert abs(s1.h[0] + 0.1 * math.exp(-beta * 500.0)) <= 1e-15


# --- tables -----------------------------------------------------------------------------------
T_SOC, T_TEMP = (0.1, 0.5, 0.9), (10.0, 30.0)
T_R0 = ((3e-3, 2e-3, 1e-3), (2e-3, 1.5e-3, 1e-3))
T_R1 = ((6e-3, 4e-3, 2e-3), (4e-3, 3e-3, 2e-3))
T_TAU = ((40.0, 50.0, 60.0), (30.0, 30.0, 30.0))


def table_cell(**kw) -> CellModel:
    base = dict(ocv_soc=SOC, ocv_v=OCV_V, capacity_ah=100.0, r0_ohm=1e-3, r1_ohm=1e-3, tau1_s=30.0, ea_r0_j_mol=20000.0,
                ea_r1_j_mol=40000.0, r_soc=T_SOC, r_temp_c=T_TEMP, r0_table=T_R0, r1_table=T_R1, tau1_table=T_TAU)
    return CellModel(**{**base, **kw})


def test_tables_are_bilinear_inside_and_flat_beyond_the_states_of_charge():
    c = table_cell()
    r0, r1, tau = c.resistances(np.array([0.3, 0.9, 0.95, -0.2, 0.5]), np.array([15.0, 30.0, 10.0, 20.0, 10.0]))
    np.testing.assert_allclose(r0, [0.75 * 2.5e-3 + 0.25 * 1.75e-3, 1e-3, 1e-3, 2.5e-3, 2e-3], rtol=1e-14)
    np.testing.assert_allclose(r1, [0.75 * 5e-3 + 0.25 * 3.5e-3, 2e-3, 2e-3, 5e-3, 4e-3], rtol=1e-14)
    np.testing.assert_allclose(tau, [0.75 * 45 + 0.25 * 30, 30.0, 60.0, 35.0, 50.0], rtol=1e-14)


@pytest.mark.parametrize("hold", ["tau", "capacitance"])
def test_tables_beyond_their_temperatures_follow_the_arrhenius_form_of_the_nearest_row(hold):
    c = table_cell(rc_hold=hold)
    for temp, row, end in ((-5.0, 0, 10.0), (45.0, 1, 30.0)):
        r0, r1, tau = c.resistances(np.array([0.5]), np.array([temp]))
        f0, f1 = temperature_factor(20000.0, temp, end), temperature_factor(40000.0, temp, end)
        assert r0[0] == pytest.approx(T_R0[row][1] * f0, rel=1e-14)
        assert r1[0] == pytest.approx(T_R1[row][1] * f1, rel=1e-14)
        assert tau[0] == pytest.approx(T_TAU[row][1] * (1.0 if hold == "tau" else f1), rel=1e-14)
    one = table_cell(r_temp_c=(23.0,), r0_table=T_R0[:1], r1_table=T_R1[:1], tau1_table=T_TAU[:1], rc_hold=hold)
    r0, r1, _ = one.resistances(np.array([0.1, 0.1]), np.array([23.0, 10.0]))
    np.testing.assert_allclose(r0, 3e-3 * np.array([1.0, temperature_factor(20000.0, 10.0, 23.0)]), rtol=1e-14)
    np.testing.assert_allclose(r1, 6e-3 * np.array([1.0, temperature_factor(40000.0, 10.0, 23.0)]), rtol=1e-14)


def test_hysteresis_bound_table_and_constant():
    c = table_cell(hyst_soc=(0.0, 0.5, 1.0), hyst_table=(0.06, 0.02, 0.1), hyst_v=9.0)
    np.testing.assert_allclose(c.hyst_bound(np.array([-0.1, 0.25, 0.5, 1.2])), [0.06, 0.04, 0.02, 0.1], rtol=1e-14)
    np.testing.assert_allclose(table_cell(hyst_v=0.03).hyst_bound(np.array([0.1, 0.9])), 0.03)


def test_tables_are_checked():
    with pytest.raises(ValueError, match="given together"):
        table_cell(tau1_table=())
    with pytest.raises(ValueError, match="one row per temperature"):
        table_cell(r0_table=T_R0[:1])
    with pytest.raises(ValueError, match="increase"):
        table_cell(r_soc=(0.1, 0.1, 0.9))
    with pytest.raises(ValueError, match="hyst_table"):
        table_cell(hyst_soc=(0.0, 1.0), hyst_table=(0.1,))


def test_without_tables_the_resistances_are_the_constants_with_the_temperature_factor():
    c = CellModel(ocv_soc=SOC, ocv_v=OCV_V, capacity_ah=100.0, r0_ohm=1e-3, r1_ohm=2e-3, tau1_s=30.0, ea_r1_j_mol=3e4)
    r0, r1, tau = c.resistances(np.array([0.2, 0.8]), np.array([5.0, 25.0]))
    f = temperature_factor(3e4, np.array([5.0, 25.0]))
    np.testing.assert_allclose([r0, r1, tau], [[1e-3, 1e-3], 2e-3 * f, 30.0 * f], rtol=1e-14)


# --- trajectory against the scalar reference ------------------------------------------------
BLOCKS = [
    # (state, params, current, drain, duration): example values, not from a source
    (dict(soc=0.15, p1=0.1, h=-0.05), dict(r2=0.003, tau2=45.0, h_max=0.28, kappa=25.0), 40.0, 0.05, 3000.0),
    (dict(soc=0.12, p1=-0.2, p2=0.05, h=0.2), dict(r2=0.003, tau2=45.0, h_max=0.28, kappa=25.0), -45.0, 0.3, 2400.0),
    (dict(soc=1.05, p1=0.3), dict(), 30.0, 0.0, 1800.0),  # into the flat top
    (dict(soc=0.3, p1=0.2, h=0.1), dict(h_max=0.28, kappa=25.0), 0.0, 0.05, 7200.0),  # rest, drains only
    (dict(soc=0.6, p1=0.1, h=0.1), dict(h_max=0.28, kappa=25.0), 0.05, 0.05, 600.0),  # current = drain
    (dict(soc=0.55), dict(series=1.0, capacity_ah=280.0, r0=3e-4, r1=2e-4, tau1=30.0), 1.0, 0.0, 10.0),
]


@pytest.mark.parametrize("blk", BLOCKS)
def test_trajectory_matches_reference(blk):
    st, pk, i, drain, dur = blk
    s, p = state(**st), params(**pk)
    t = np.linspace(0.0, dur, 7)
    soc, v = trajectory(s, p, OCV, np.array([i]), np.array([i - drain]), t)
    for k, tk in enumerate(t):
        r_soc, r_v, _ = ref(s, p, 0, i, i - drain, tk)
        assert abs(soc[k, 0] - r_soc) <= 1e-14
        assert abs(v[k, 0] - r_v) <= 1e-12


@pytest.mark.parametrize("blk", BLOCKS)
def test_block_integrals_against_quadrature(blk):
    st, pk, i, drain, dur = blk
    s, p = state(**st), params(**pk)
    i_soc = i - drain
    end, ints = advance(s, p, OCV, np.array([i]), np.array([i_soc]), dur)
    _, v_end = trajectory(s, p, OCV, np.array([i]), np.array([i_soc]), [dur])
    r_soc, r_v, _ = ref(s, p, 0, i, i_soc, dur)
    assert abs(end.soc[0] - r_soc) <= 1e-14 and abs(v_end[0, 0] - r_v) <= 1e-12
    br = soc_breaks(float(s.soc[0]), i_soc / (3600.0 * float(p.capacity_ah[0])), dur)

    def q(fn):
        return quad(lambda t: fn(*ref(s, p, 0, i, i_soc, t)), 0.0, dur, br)

    v_dt = q(lambda soc, v, e: v)
    ocv_dt = q(lambda soc, v, e: e)
    want = dict(
        v_dt=v_dt, ocv_dt=ocv_dt,
        terminal_wh=i * v_dt / 3600.0,
        stored_wh=i_soc * ocv_dt / 3600.0,
        heat_wh=i * (v_dt - ocv_dt) / 3600.0,
        drain_wh=drain * ocv_dt / 3600.0,
        charge_ah=i_soc * dur / 3600.0,
    )
    for name, w in want.items():
        got = float(getattr(ints, name)[0])
        scale = max(abs(w), 1e-9 * dur, 1e-12)
        assert abs(got - w) <= 1e-10 * scale, (name, got, w)


def test_two_groups_are_independent():
    s = GroupState(np.array([0.3, 0.7]), np.array([0.1, -0.1]), np.array([0.0, 0.02]), np.array([0.05, -0.05]))
    p = GroupParams(*(np.array(x, dtype=float) for x in (
        [14, 14], [100, 95], [0.02, 0.025], [0.01, 0.012], [600, 700], [0.0, 0.003], [1.0, 50.0],
        [0.28, 0.28], [25.0, 25.0])))
    i_soc = np.array([20.0 - 0.05, 20.0 - 0.3])
    soc, v = trajectory(s, p, OCV, np.array([20.0, 20.0]), i_soc, [0.0, 500.0])
    for g in range(2):
        for k, tk in enumerate([0.0, 500.0]):
            r_soc, r_v, _ = ref(s, p, g, 20.0, float(i_soc[g]), tk)
            assert abs(soc[k, g] - r_soc) <= 1e-14 and abs(v[k, g] - r_v) <= 1e-12


def test_ocv_integral_with_flat_ends():
    for a, b in [(0.0, 1.1), (-0.2, 0.03), (0.95, 1.4), (-0.1, 1.3), (0.33, 0.34), (0.5, 0.2)]:
        brk = [x for x in SOC if min(a, b) < x < max(a, b)]
        lo, hi = min(a, b), max(a, b)
        num = quad(lambda x: float(np.interp(x, SOC, OCV_V)), lo, hi, brk) * (1 if b >= a else -1)
        got = float(OCV.integral(b) - OCV.integral(a))
        assert abs(got - num) <= 1e-12 * max(1.0, abs(num))
    assert float(OCV.integral(0.0)) == 0.0


def test_temperature_factor():
    assert float(temperature_factor(30000.0, 25.0)) == pytest.approx(1.0, abs=1e-15)
    assert float(temperature_factor(0.0, -10.0)) == 1.0
    f = float(temperature_factor(30000.0, 0.0))
    t, tr = 273.15, 298.15
    assert f == pytest.approx((t / tr) * math.exp(30000.0 / 8.314462618 * (1 / t - 1 / tr)), rel=1e-14)
    assert f > 1.0


# --- the saved vectors ---------------------------------------------------------------------
def _vector_inputs() -> pd.DataFrame:
    """One group's blocks: charge, rest, discharge, rest, a 2RC stretch, hysteresis both ways."""
    rows = []
    s = state(0.2)
    prm = dict(series=14.0, capacity_ah=100.0, r0=0.02, r1=0.01, tau1=600.0, r2=0.004, tau2=60.0, h_max=0.28,
               kappa=25.0)
    program = [(10.0, 0.0, 0.05), (1800.0, 40.0, 0.05), (10.0, 40.0, 0.05), (3600.0, 0.0, 0.05),
               (2700.0, -35.0, 0.05), (900.0, 0.0, 0.3), (600.0, 60.0, 0.05), (20.0, -60.0, 0.05),
               (7200.0, -50.0, 0.05), (1200.0, 0.0, 0.05), (300.0, 0.05, 0.05)]
    for k, (dur, cur, drain) in enumerate(program):
        p = dict(prm, r0=prm["r0"] * (1.0 + 0.1 * (k >= 6)))  # a resistance change halfway
        rows.append(dict(step=k, soc0=float(s.soc[0]), p1_0=float(s.p1[0]), p2_0=float(s.p2[0]), h0=float(s.h[0]),
                         **p, duration_s=dur, current_a=cur, drain_a=drain))
        s, _ = advance(s, params(**p), OCV, np.array([cur]), np.array([cur - drain]), dur)
    return pd.DataFrame(rows)


_OUT = ["soc", "p1", "p2", "h", "v_start", "v_end", "charge_ah", "stored_wh", "heat_wh", "drain_wh", "terminal_wh"]


def _vector_outputs(inputs: pd.DataFrame) -> pd.DataFrame:
    out = []
    for r in inputs.itertuples():
        s = state(r.soc0, r.p1_0, r.p2_0, r.h0)
        p = params(**{n: getattr(r, n) for n in PRM_NAMES})
        cur, i_soc = np.array([r.current_a]), np.array([r.current_a - r.drain_a])
        end, ints = advance(s, p, OCV, cur, i_soc, r.duration_s)
        _, v = trajectory(s, p, OCV, cur, i_soc, [0.0, r.duration_s])
        out.append(dict(soc=end.soc[0], p1=end.p1[0], p2=end.p2[0], h=end.h[0], v_start=v[0, 0], v_end=v[1, 0],
                        charge_ah=ints.charge_ah[0], stored_wh=ints.stored_wh[0], heat_wh=ints.heat_wh[0],
                        drain_wh=ints.drain_wh[0], terminal_wh=ints.terminal_wh[0]))
    return pd.DataFrame(out, columns=_OUT)


def write_vectors() -> None:
    inputs = _vector_inputs()
    table = pd.concat([inputs, _vector_outputs(inputs)], axis=1)
    VECTORS.mkdir(exist_ok=True)
    table.to_csv(VECTORS / "sim_cell_steps.csv", index=False, float_format="%.17g")


def _read(path: Path) -> pd.DataFrame:
    if int(pd.__version__.split(".")[0]) < 3:
        return pd.read_csv(path, float_precision="round_trip")
    return pd.read_csv(path)


def test_saved_vectors():
    table = _read(VECTORS / "sim_cell_steps.csv")
    inputs = table.drop(columns=_OUT)
    # The chain: each block starts from the previous block's end.
    for col, src in (("soc0", "soc"), ("p1_0", "p1"), ("p2_0", "p2"), ("h0", "h")):
        assert (table[col].to_numpy()[1:] == table[src].to_numpy()[:-1]).all()
    got = _vector_outputs(inputs)
    for col in _OUT:
        np.testing.assert_allclose(got[col].to_numpy(), table[col].to_numpy(), rtol=1e-14, atol=1e-15, err_msg=col)
    # And the outputs against the scalar reference.
    for r, o in zip(inputs.itertuples(), table.itertuples()):
        s = state(r.soc0, r.p1_0, r.p2_0, r.h0)
        p = params(**{n: getattr(r, n) for n in PRM_NAMES})
        r_soc, r_v, _ = ref(s, p, 0, r.current_a, r.current_a - r.drain_a, r.duration_s)
        assert abs(o.soc - r_soc) <= 1e-14 and abs(o.v_end - r_v) <= 1e-12


if __name__ == "__main__" and "--write-vectors" in sys.argv:
    write_vectors()
    print(f"wrote {VECTORS / 'sim_cell_steps.csv'}")
