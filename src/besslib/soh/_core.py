"""The capacity estimator core: rest-anchored coulomb counting, one update per pair.

Each *pair* runs from one full or empty rest to the next linked one. Its measured
charge ``q`` (Ah, positive = charging) is modelled as

    q = C * x + beta * dt

``x`` is the change of state of charge: +1 from empty to full, -1 from full to empty,
0 from full to full or empty to empty. ``dt`` is the time between the rests in days.
``C`` is the usable capacity (Ah). ``beta`` (Ah per day) is the charge the meter does
not see: BMS supply, balancing, self-discharge and sensor offset. The *offset current*
is ``-beta / 24`` A, negative when the battery is drained, as in Figgener et al. (2024,
Supplementary Methods). The *fade model* adds a linear trend: ``C = c0 + r * y``, with
``y`` in years since ``t0``.

Weights: ``1 / (sigma_q**2 + c_weight**2 * sigma_x**2)``, the effective variance of
Orear, Am. J. Phys. 50, 912 (1982), doi:10.1119/1.12972, with a fixed prior
``c_weight`` (the nominal capacity), so the weights do not depend on the estimate.

Band: the weights fix only the relative size of the pair errors, and not even that
well: the scatter differs between kinds of pair (a full-to-full pair and an
empty-to-empty pair rest on different edges of the charge). And consecutive pairs share
a rest, so their errors are correlated at lag 1 and at no longer lag. The covariance is
therefore the sandwich ``V = A^-1 M A^-1 * m / (m - p)`` with the residuals' own
scores ``g_k = w_k a_k e_k``:

    M = sum g_k g_k' + sum over linked pairs (g_k g_(k-1)' + g_(k-1) g_k')

the lag-1 heteroskedasticity- and autocorrelation-consistent form of Newey and West,
Econometrica 55, 703 (1987), doi:10.2307/1913610, with full weight at lag 1 instead of
their Bartlett weight 1/2, because the pair errors are known to be correlated at lag 1
only. ``m / (m - p)`` is the usual degrees-of-freedom factor, and the band uses a
Student-t quantile with ``m - p`` degrees of freedom. This form is not positive by
construction: a negative variance of the main quantity is reported as not possible,
never replaced by a floor. The current sensor's gain error is added to the capacity's
band as a systematic term. The Birge ratio (reduced chi-square) and the lag-1
correlation ``rho`` of the weighted residuals are reported as diagnostics; they no
longer scale the band.

The residuals are not known until the fit is solved, so ``M`` is kept as fixed-size
sums of fourth-order products of ``z = (a, q - c_weight * x)``; ``solve`` contracts them
with ``(-theta', 1)``. Subtracting ``c_weight * x`` keeps these sums small, which
matters in 32-bit arithmetic.

Old pairs fade by ``exp(-age / tau_days)``: a pair of age ``s`` counts as if its
variance were ``exp(s / tau_days)`` times larger (the convention of least squares with
forgetting). A lag-1 link counts with ``sqrt(lambda_k lambda_(k-1))``, which keeps the
two pairs of a link on the same footing. Related work: Plett, J. Power Sources 196, 2319
(2011), doi:10.1016/j.jpowsour.2010.09.048 (recursive total least squares for capacity;
not read, cited as related work).

Portability rule: plain float arithmetic, a fixed-size state (arrays of length 3, 9 and
144), no numpy, no pandas, no growing containers, no recursion, no exceptions for
control flow. Each class maps to a C struct, each function to a C function. The test
vectors live in ``tests/vectors/``.
"""

from __future__ import annotations

import math

#: Status codes of an estimate. A C port uses the same integers.
OK = 0
TOO_FEW_FULL_EMPTY = 1
TOO_FEW_SAME_KIND = 2
SINGULAR = 3
STALE = 4
NO_BAND = 5
NO_LEVELS = 6  # set by the caller: the core sees pairs, not the rests that set the levels
SHORT_SPAN = 7

STATUS_TEXT = {
    OK: "estimated",
    TOO_FEW_FULL_EMPTY: "not possible from this data: too few full-to-empty or empty-to-full pairs",
    TOO_FEW_SAME_KIND: "not possible from this data: too few full-to-full or empty-to-empty pairs to fix the offset",
    SINGULAR: "not possible from this data: the pairs do not separate capacity from offset",
    STALE: "not possible from this data: no full-to-empty or empty-to-full pair within the memory time",
    NO_BAND: "not possible from this data: the residuals give no positive variance for the band",
    NO_LEVELS: "not possible from this data: not enough history yet to set the full and empty levels",
    SHORT_SPAN: "not possible from this data: the full-to-empty and empty-to-full pairs span too short a time",
}

#: Relative rounding tolerance of the band's sums, for 64-bit arithmetic. A 32-bit port
#: needs a larger one, set from its own test vectors.
ROUNDING = 1.0e-9

_NZ = 4  # length of z = (regressors, centred charge); index of s4 is ((r * 3 + s) * 4 + i) * 4 + j

_NEVER = -1.0e30


class Fit:
    """Fixed-size state of a weighted least-squares fit with forgetting and lag-1 sums.

    ``p`` regressors (2 or 3). ``a`` = sum w a a' (row-major 3 x 3), ``h`` = sum w a q,
    ``c`` = sum w q q; ``l`` = sum v a_k a_(k-1)', ``ly`` = sum v q_k a_(k-1),
    ``yl`` = sum v a_k q_(k-1), ``yy1`` = sum v q_k q_(k-1), with v = g sqrt(w_k w_(k-1)),
    over pairs that share a rest with the pair before them; ``g`` =
    exp(-(t_k - t_(k-1)) / (2 tau_days)) makes a link decay as sqrt(lambda_k lambda_(k-1)).
    ``s4`` = sum w^2 a_r a_s z_i z_j and ``s4l`` = sum g w_k w_(k-1) (a_k,r a_(k-1),s +
    a_(k-1),r a_k,s) z_k,i z_(k-1),j, with z = (a, q - c_weight x): the sums of the band's
    sandwich (3 x 3 x 4 x 4 each).
    """

    __slots__ = (
        "p", "c_weight", "tau_days", "t_last", "t_last_cross", "t0_days",
        "a", "h", "c", "l", "ly", "yl", "yy1", "s4", "s4l",
        "m", "m_lag", "m_cross", "m_same", "n", "y_min", "y_max",
        "prev_ok", "prev_a", "prev_q", "prev_w", "prev_t", "prev_z",
    )

    def __init__(self, p: int, c_weight: float, tau_days: float, t0_days: float = 0.0) -> None:
        self.p = int(p)
        self.c_weight = float(c_weight)
        self.tau_days = float(tau_days)  # memory time; 0 or less means no forgetting
        self.t0_days = float(t0_days)  # origin of the fade model's years
        self.t_last = _NEVER
        self.t_last_cross = _NEVER
        self.a = [0.0] * 9
        self.h = [0.0] * 3
        self.c = 0.0
        self.l = [0.0] * 9
        self.ly = [0.0] * 3
        self.yl = [0.0] * 3
        self.yy1 = 0.0
        self.s4 = [0.0] * 144
        self.s4l = [0.0] * 144
        self.m = 0.0  # decayed number of pairs
        self.m_lag = 0.0  # decayed number of lag-1 links
        self.m_cross = 0.0  # decayed number of pairs with x != 0
        self.m_same = 0.0  # decayed number of pairs with x == 0
        self.n = 0  # pairs ever added
        self.y_min = 1.0e30  # first and last year of a full-to-empty or empty-to-full pair
        self.y_max = -1.0e30
        self.prev_ok = 0
        self.prev_a = [0.0] * 3
        self.prev_q = 0.0
        self.prev_w = 0.0
        self.prev_t = _NEVER
        self.prev_z = [0.0] * _NZ


def capacity_state(c_weight: float, tau_days: float) -> Fit:
    """The capacity estimator: regressors (x, dt)."""
    return Fit(2, c_weight, tau_days)


def fade_state(c_weight: float, t0_days: float) -> Fit:
    """The fade model: regressors (x, x * y, dt), no forgetting."""
    return Fit(3, c_weight, 0.0, t0_days)


def decay(state: Fit, t_days: float) -> None:
    """Move the state forward to ``t_days``; old pairs lose weight."""
    if state.n == 0:
        state.t_last = t_days
        return
    age = t_days - state.t_last
    if age <= 0.0:
        return
    state.t_last = t_days
    if state.tau_days <= 0.0:
        return
    f = math.exp(-age / state.tau_days)
    for k in range(9):
        state.a[k] *= f
        state.l[k] *= f
    for k in range(144):
        state.s4[k] *= f
        state.s4l[k] *= f
    for k in range(3):
        state.h[k] *= f
        state.ly[k] *= f
        state.yl[k] *= f
    state.c *= f
    state.yy1 *= f
    state.m *= f
    state.m_lag *= f
    state.m_cross *= f
    state.m_same *= f


def update(
    state: Fit,
    t_days: float,
    x: float,
    dt_days: float,
    q_ah: float,
    sigma_q_ah: float,
    sigma_x: float,
    linked: int,
) -> None:
    """Add one pair that ends at ``t_days`` (pairs in time order).

    ``linked`` is 1 when the pair starts at the rest where the previous pair ended.
    """
    decay(state, t_days)
    w = 1.0 / (sigma_q_ah * sigma_q_ah + state.c_weight * state.c_weight * sigma_x * sigma_x)
    y = (t_days - state.t0_days) / 365.25
    row = [x, dt_days, 0.0]
    if state.p == 3:
        row = [x, x * y, dt_days]
    p = state.p
    z = [0.0] * _NZ
    for r in range(p):
        z[r] = row[r]
    z[p] = q_ah - state.c_weight * x
    for r in range(p):
        for k in range(p):
            state.a[3 * r + k] += w * row[r] * row[k]
        state.h[r] += w * row[r] * q_ah
    state.c += w * q_ah * q_ah
    for r in range(p):
        for s in range(p):
            ww = w * w * row[r] * row[s]
            for i in range(p + 1):
                for j in range(p + 1):
                    state.s4[((r * 3 + s) * 4 + i) * 4 + j] += ww * z[i] * z[j]
    if linked != 0 and state.prev_ok != 0:
        g = 1.0
        if state.tau_days > 0.0:
            g = math.exp(-(t_days - state.prev_t) / (2.0 * state.tau_days))
        v = g * math.sqrt(w * state.prev_w)
        for r in range(p):
            for k in range(p):
                state.l[3 * r + k] += v * row[r] * state.prev_a[k]
            state.ly[r] += v * q_ah * state.prev_a[r]
            state.yl[r] += v * row[r] * state.prev_q
        state.yy1 += v * q_ah * state.prev_q
        state.m_lag += g
        u = g * w * state.prev_w
        for r in range(p):
            for s in range(p):
                aa = u * (row[r] * state.prev_a[s] + state.prev_a[r] * row[s])
                for i in range(p + 1):
                    for j in range(p + 1):
                        state.s4l[((r * 3 + s) * 4 + i) * 4 + j] += aa * z[i] * state.prev_z[j]
    state.prev_ok = 1
    for r in range(3):
        state.prev_a[r] = row[r]
    for r in range(_NZ):
        state.prev_z[r] = z[r]
    state.prev_q = q_ah
    state.prev_w = w
    state.prev_t = t_days
    state.m += 1.0
    if x != 0.0:
        state.m_cross += 1.0
        state.t_last_cross = t_days
        if y < state.y_min:
            state.y_min = y
        if y > state.y_max:
            state.y_max = y
    else:
        state.m_same += 1.0
    state.n += 1


def _invert(p: int, a: list, out: list) -> int:
    """Invert the symmetric p x p matrix ``a`` (p = 2 or 3, row-major in 3 x 3) into ``out``."""
    for k in range(9):
        out[k] = 0.0
    if p == 2:
        det = a[0] * a[4] - a[1] * a[3]
        if a[0] <= 0.0 or a[4] <= 0.0 or det <= 1.0e-9 * a[0] * a[4]:
            return 0
        out[0] = a[4] / det
        out[1] = -a[1] / det
        out[3] = -a[3] / det
        out[4] = a[0] / det
        return 1
    a11, a12, a13, a22, a23, a33 = a[0], a[1], a[2], a[4], a[5], a[8]
    c11 = a22 * a33 - a23 * a23
    c12 = a13 * a23 - a12 * a33
    c13 = a12 * a23 - a13 * a22
    c22 = a11 * a33 - a13 * a13
    c23 = a12 * a13 - a11 * a23
    c33 = a11 * a22 - a12 * a12
    det = a11 * c11 + a12 * c12 + a13 * c13
    if a11 <= 0.0 or a22 <= 0.0 or a33 <= 0.0 or det <= 1.0e-12 * a11 * a22 * a33:
        return 0
    vals = (c11, c12, c13, c12, c22, c23, c13, c23, c33)
    for k in range(9):
        out[k] = vals[k] / det
    return 1


def t_quantile(z: float, nu: float) -> float:
    """Student-t quantile from the normal quantile ``z`` (Cornish-Fisher, four terms).

    Abramowitz and Stegun, Handbook of Mathematical Functions (1964), 26.7.5.
    ``nu`` below 1 is treated as 1.
    """
    if nu < 1.0:
        nu = 1.0
    z2 = z * z
    g1 = (z2 + 1.0) * z / 4.0
    g2 = ((5.0 * z2 + 16.0) * z2 + 3.0) * z / 96.0
    g3 = (((3.0 * z2 + 19.0) * z2 + 17.0) * z2 - 15.0) * z / 384.0
    return z + g1 / nu + g2 / (nu * nu) + g3 / (nu * nu * nu)


class Estimate:
    """The result of ``solve`` (the C port's output struct). ``theta`` = (C, beta) for the
    capacity fit, (c0, r, beta) for the fade fit; ``sigma`` their standard errors."""

    __slots__ = ("status", "theta", "sigma", "low", "high", "chi2", "dof", "birge", "rho", "m", "m_cross", "m_same", "span_years")

    def __init__(self) -> None:
        self.status = SINGULAR
        self.theta = [math.nan] * 3
        self.sigma = [math.nan] * 3
        self.low = math.nan  # band of the main quantity: C, or r for the fade fit
        self.high = math.nan
        self.chi2 = math.nan
        self.dof = 0.0
        self.birge = math.nan
        self.rho = math.nan
        self.m = 0.0
        self.m_cross = 0.0
        self.m_same = 0.0
        self.span_years = 0.0


def solve(
    state: Fit,
    t_days: float,
    z: float,
    gain_error: float,
    min_cross: float,
    min_same: float,
    min_span_years: float,
) -> Estimate:
    """The estimate at ``t_days`` (the state is decayed to it first).

    ``z`` is the two-sided normal quantile of the band (1.6449 for 90 %). The band of the
    main quantity is the Student-t quantile times its sandwich standard error (module
    docstring). A parameter other than the main one whose sandwich variance comes out
    negative gets ``sigma`` NaN. For the
    capacity fit, ``gain_error`` (the current sensor's relative accuracy) is added to the
    band of C as a systematic term: it moves every pair the same way, so averaging does
    not reduce it. For the fade fit it is left out (it scales the rate by at most that
    fraction). ``min_span_years`` applies to the fade fit only.
    """
    decay(state, t_days)
    out = Estimate()
    out.m = state.m
    out.m_cross = state.m_cross
    out.m_same = state.m_same
    if state.m_cross > 0.0:
        out.span_years = state.y_max - state.y_min
    if state.m_cross < min_cross:
        out.status = TOO_FEW_FULL_EMPTY
        return out
    if state.m_same < min_same:
        out.status = TOO_FEW_SAME_KIND
        return out
    p = state.p
    if p == 2 and state.tau_days > 0.0 and t_days - state.t_last_cross > state.tau_days:
        out.status = STALE
        return out
    if p == 3 and out.span_years < min_span_years:
        out.status = SHORT_SPAN
        return out
    inv = [0.0] * 9
    if _invert(p, state.a, inv) == 0:
        out.status = SINGULAR
        return out
    th = [0.0, 0.0, 0.0]
    for r in range(p):
        acc = 0.0
        for k in range(p):
            acc += inv[3 * r + k] * state.h[k]
        th[r] = acc
    chi2 = state.c
    for r in range(p):
        chi2 -= th[r] * state.h[r]
    if chi2 < 0.0:
        chi2 = 0.0
    nu = state.m - p
    out.chi2 = chi2
    out.dof = nu
    if nu <= 0.0:
        out.status = NO_BAND
        return out
    out.birge = chi2 / nu  # diagnostic: 1 when the weights' error sizes match the scatter
    # Lag-1 correlation of the weighted residuals (diagnostic): sum e_k e_(k-1) from the lag sums.
    out.rho = 0.0
    if state.m_lag > 1.0 and chi2 > 0.0:
        cross = state.yy1
        for r in range(p):
            cross -= th[r] * (state.ly[r] + state.yl[r])
            for k in range(p):
                cross += th[r] * state.l[3 * r + k] * th[k]
        out.rho = (cross / state.m_lag) / (chi2 / state.m)
    # The sandwich's meat M_rs = phi' (S_rs + SL_rs) phi with phi = (-theta + c_weight e_0, 1).
    phi = [0.0] * _NZ
    for r in range(p):
        phi[r] = -th[r]
    phi[0] += state.c_weight
    phi[p] = 1.0
    meat = [0.0] * 9
    size = [0.0] * 9  # the same sums in absolute values: the scale of their rounding error
    for r in range(p):
        for s in range(p):
            acc = 0.0
            mag = 0.0
            for i in range(p + 1):
                for j in range(p + 1):
                    idx = ((r * 3 + s) * 4 + i) * 4 + j
                    acc += phi[i] * (state.s4[idx] + state.s4l[idx]) * phi[j]
                    mag += abs(phi[i] * phi[j]) * (abs(state.s4[idx]) + abs(state.s4l[idx]))
            meat[3 * r + s] = acc
            size[3 * r + s] = mag
    scale = state.m / nu
    main = 0 if p == 2 else 1
    for r in range(p):
        acc = 0.0
        mag = 0.0
        for i in range(p):
            for j in range(p):
                acc += inv[3 * r + i] * meat[3 * i + j] * inv[3 * j + r]
                mag += abs(inv[3 * r + i] * inv[3 * j + r]) * size[3 * i + j]
        # A negative value within rounding of the sums is zero (a fit with no scatter).
        if acc < 0.0 and -acc <= ROUNDING * mag:
            acc = 0.0
        var = scale * acc
        if var >= 0.0:
            out.sigma[r] = math.sqrt(var)
        elif r == main:
            out.status = NO_BAND
            return out
    for r in range(p):
        out.theta[r] = th[r]
    k = t_quantile(z, nu)
    sys_part = gain_error * th[0] if p == 2 else 0.0
    half = math.sqrt(k * k * out.sigma[main] * out.sigma[main] + z * z * sys_part * sys_part)
    if p == 2:
        out.sigma[0] = math.sqrt(out.sigma[0] * out.sigma[0] + sys_part * sys_part)
    out.low = th[main] - half
    out.high = th[main] + half
    out.status = OK
    return out
