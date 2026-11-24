"""Check S3 for the thermal network (``besslib.sim.thermal``): one node's steady state and
time constant, and for several nodes the heat balance and the solution against a fine
numerical integration of the equations.

Every value here is an example value, not from a source.
"""

from __future__ import annotations

import numpy as np
import pytest

from besslib.sim.thermal import Network, Profile, ThermalModel


def one_node(fan: float = 1.0) -> Network:
    # The group node has its own path to the air and no link to the housing or a neighbour,
    # so it is a single node; the housing node is separate.
    m = ThermalModel(mode="network", c_group_j_k=8000.0, c_housing_j_k=50000.0, g_group_housing_w_k=0.0,
                     g_neighbour_w_k=0.0, g_group_air_w_k=2.5, g_housing_air_w_k=4.0)
    return Network(m, groups=1, fan=fan)


@pytest.mark.parametrize("fan", [1.0, 0.3])
def test_one_node_steady_state_and_time_constant(fan):
    net = one_node(fan)
    q, t_air, t0 = 12.0, 20.0, 35.0
    ha, mc = 2.5 * fan, 8000.0
    heat = np.array([q, 0.0])
    ss = net.steady(heat, t_air)
    assert abs(ss[0] - (t_air + q / ha)) <= 1e-6 * abs(t_air + q / ha)
    tau = mc / ha
    temps = net.at(np.array([t0, t_air]), heat, t_air, np.array([0.0, tau, 3 * tau, 50 * tau]))[:, 0]
    want = ss[0] + (t0 - ss[0]) * np.exp(-np.array([0.0, 1.0, 3.0, 50.0]))
    assert np.all(np.abs(temps - want) <= 1e-6 * np.abs(want))
    assert np.min(np.abs(net.lam - ha / mc)) <= 1e-6 * ha / mc
    # The housing on its own: T_air + 0 / hA, so it stays at the air temperature.
    assert abs(net.steady(heat, t_air)[1] - t_air) <= 1e-9


def chain(groups: int = 4, fan: float = 1.0) -> Network:
    m = ThermalModel(mode="network", c_group_j_k=9000.0, c_housing_j_k=40000.0, g_group_housing_w_k=0.8,
                     g_neighbour_w_k=1.5, g_group_air_w_k=0.6, g_housing_air_w_k=5.0)
    return Network(m, groups=groups, fan=fan)


def test_network_heat_balance():
    net = chain()
    heat = np.array([3.0, 8.0, 1.0, 5.0, 2.0])
    t0 = np.array([25.0, 30.0, 22.0, 27.0, 24.0])
    t_air, dur = 18.0, 7200.0
    t_end = net.end(t0, heat, t_air, dur)
    stored = float(np.sum(net.cap * (t_end - t0)))
    # Heat to the air: the closed form integrated with Gauss-Legendre (16 nodes x 64 pieces).
    x, w = np.polynomial.legendre.leggauss(16)
    edges = np.linspace(0.0, dur, 65)
    to_air = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        tt = 0.5 * (lo + hi) + 0.5 * (hi - lo) * x
        temps = net.at(t0, heat, t_air, tt)
        to_air += 0.5 * (hi - lo) * float(w @ ((temps - t_air) @ net.air))
    into = float(heat.sum()) * dur
    assert abs(into - (stored + to_air)) <= 1e-6 * into


def test_network_against_numerical_integration():
    net = chain(groups=3, fan=0.4)
    heat = np.array([4.0, 9.0, 2.0, 1.0])
    t0 = np.array([20.0, 21.0, 19.0, 20.0])
    t_air, dur, h = 15.0, 3600.0, 1.0
    # Classic fourth-order Runge-Kutta on M dT/dt = -K T + heat + air T_air, 1 s steps.
    b = heat + net.air * t_air

    def f(t):
        return (b - net.k @ t) / net.cap

    t = t0.copy()
    for _ in range(int(dur / h)):
        k1 = f(t)
        k2 = f(t + 0.5 * h * k1)
        k3 = f(t + 0.5 * h * k2)
        k4 = f(t + h * k3)
        t = t + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
    got = net.end(t0, heat, t_air, dur)
    assert np.all(np.abs(got - t) <= 1e-6 * np.abs(t))


def test_fan_factor_and_no_path():
    assert chain(fan=0.2).time_constant_s() > chain(fan=1.0).time_constant_s()
    with pytest.raises(ValueError):
        chain(fan=0.0)


def test_profile_shape():
    p = Profile(mean_c=20.0, season_amp_c=5.0, daily_amp_c=2.0, planted=((3, 10.0, 2.0, -4.0),))
    assert float(p(200 * 86400.0 + 15 * 3600.0)) == pytest.approx(27.0, abs=1e-12)  # warmest day, 15:00
    assert float(p(3 * 86400.0 + 11 * 3600.0)) == -4.0
    assert float(p(3 * 86400.0 + 12 * 3600.0)) != -4.0
