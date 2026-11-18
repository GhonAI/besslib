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

The same file holds a second, smaller fit, the *through-origin ratio* (``Ratio``), for
quantities that are one sum divided by another: an event is two sums ``(a, b)`` with
``a > 0``, and ``b = k a + e`` with an error whose variance grows with ``a`` (the event
is itself a sum of many small terms). Weighted least squares with weight ``1 / a`` gives
``k = sum lambda b / sum lambda a``, and every event's score is ``g = b - k a``. The
growth form ``b = (k0 + k1 y) a`` uses the regressors ``(1, y) a`` with the same weight:
its normal matrix is ``sum lambda a (1, y)(1, y)'`` and its scores are ``(1, y) g``. The
lag-1 sandwich, the ``m / (m - p)`` factor with ``m = sum lambda``, the Student-t
quantile, ``ROUNDING`` and ``NO_BAND`` follow the capacity fit. Four choices are this
fit's own.

First, the forgetting weight enters the band's sums twice, as the weight of a weighted
least-squares fit does: the score of event k is ``lambda_k (1, y) g_k``, so ``M = sum
lambda_k^2 g g' + sum over links lambda_k lambda_(k-1) (g_k g_(k-1)' + g_(k-1) g_k')``.
For ``k = sum lambda b / sum lambda a`` with independent errors this is ``sum lambda^2
Var(b) / (sum lambda a)^2``, the variance of the estimate as computed. The capacity fit
keeps the weight once, which is exact only if an event's variance grew as
``exp(age / tau_days)``; for events whose errors do not grow with age that band is about
``sqrt(2)`` too wide at ``tau_days`` of memory (``(sum lambda)^2 / sum lambda^2`` is
close to ``2 sum lambda``). The capacity fit is left as it is (its settings and results
are frozen); this fit uses the weight twice. Without forgetting both are the same.
``m = sum lambda`` stays the count for ``m / (m - p)``, the degrees of freedom and the
minimum number of events. It is half the effective number of events of the estimate,
``(sum lambda)^2 / sum lambda^2``, so as a minimum count it errs on the cautious side;
and it is close to the effective number of the band's own sums, ``(sum lambda^2)^2 /
sum lambda^4``, which sets how well the variance itself is known (for evenly spaced
events the two differ by the factor ``(1 + r^2) / (1 + r)``, ``r`` the weight ratio of
neighbouring events: within 0.3 % for daily events at 180 days of memory).

Second, the degrees of freedom of the band are those of its variance estimate. Without
links that estimate is a sum of squared scores, and the band uses ``m - p`` degrees of
freedom, as the capacity fit does. A lag-1 term adds products of neighbouring scores,
which scatter more than they add: when a shared end point dominates the errors, the
squares and the products nearly cancel and their sum is poorly known. The count used
here is derived here, as follows. Take scores of equal variance 1, normal, correlated
by ``rho`` across a link and independent otherwise, and the meat ``S = sum g_k^2 + 2
sum over links g_k g_(k-1)`` over ``n`` events. The fourth moments of normal variables
(``E[g_a g_b g_c g_d]`` is the sum of the three products of pair covariances) give
``E[S] = n (1 + 2 rho q)`` and ``Var[S] = 2 n (1 + 2 q + 8 rho q + 4 rho^2 (q + q2))``,
where ``q`` is the share of events linked to the one before and ``q2`` the share whose
link follows another link: ``2 n + 4 rho^2 N_L`` from the squares, ``4 (1 + rho^2) N_L +
8 rho^2 N_LL`` from the products, ``16 rho N_L`` from their covariance (``N_L = q n``
links, ``N_LL = q2 n`` pairs of consecutive links). A chi-square variable with the same
mean and variance has ``2 E[S]^2 / Var[S] = n (1 + 2 rho q)^2 / (1 + 2 q + 8 rho q + 4
rho^2 (q + q2))`` degrees of freedom: ``n`` without links, ``n / 3`` for a fully linked
chain without correlation, and towards zero as ``rho q`` nears ``-1/2`` (a chain fully
linked at ``rho = -0.45`` keeps about 1 % of ``n``). The band uses ``m - p`` times this
share, with ``rho``, ``q`` and ``q2`` from the weighted sums (``rho`` is the reported
diagnostic). Without links it is exactly the capacity fit's ``m - p``. At very few
degrees of freedom ``t_quantile`` (four terms) runs low: 5.57 against 6.31 at one degree
of freedom and 90 %; below one it is held at one.

Third, the growth fit needs both of its variances (callers form ratios of its
parameters), so a negative variance of either gives ``NO_BAND``. Fourth, the numerator
is centred on the ratio of the first event, ``b - k_ref a``: the sums of the band then
hold the residuals' size, not the size of ``b``, so a 32-bit port loses about two
fewer digits to cancellation. No other scaling is needed: the largest sum is of order
``m a^2``, far below the 32-bit limit for any plausible event.

The *loop band* (``loop_solve``) is a second band on the same state, for closed loops
between rests: an event is a loop from one rest to the next rest of the same kind, ``a``
its energy in and ``b`` its energy out. The energy stored at rest ``j`` differs from the
reference by an error ``e_j``: the state of charge at a voltage stop is not exactly the
same each time. A loop from rest ``j - 1`` to rest ``j`` gives ``b_k = eta_k a_k +
e_(j-1) - e_j``, where ``eta_k a_k`` is what the loop would deliver had it closed
exactly. The target is ``T = sum lambda eta a / sum lambda a``, the ratio over the loops
in memory had each of them closed; the loops' own variation (``eta_k``) is part of the
target, not of the error. The estimate is the same ``R = sum lambda b / sum lambda a``,
and ``R - T = sum_j c_j e_j / A`` with ``A = sum lambda a`` and ``c_j`` the coefficient
of rest ``j``: ``+lambda_k`` as the start of loop ``k``, ``-lambda_k`` as its end,
``lambda_k - lambda_(k-1)`` for a rest that ends loop ``k - 1`` and starts the linked
loop ``k``. With independent rest errors of one variance ``s2``, ``Var(R) = s2 C2 /
A^2`` with ``C2 = sum_j c_j^2``. Without forgetting ``C2`` is 2 per linked run of loops
(its first and last rest); with forgetting the latest rest, at weight 1, dominates it.
``C2`` is kept recursively: on each accepted event, after decaying the state to its
time, a linked event adds ``2 - 2 g`` (the old end rest ``-g`` becomes the shared rest
``1 - g``, and a new end rest ``-1`` comes in), an unlinked one adds 2, with ``g`` the
previous event's weight now; decay multiplies ``C2`` by ``f^2``.

``s2`` is estimated from the lag-1 and the lag-2 products of the scores ``g_k = b_k - R
a_k``. Write ``g_k = nu_k + e_(j-1) - e_j``, with ``nu`` the loop's own part, which may
drift slowly (season, temperature). Two linked loops share a rest, which enters them with
opposite signs: ``E[g_k g_(k-1)] = gamma_nu(1) - s2``. Loops ``k`` and ``k - 2`` of three
in a row share no rest: ``E[g_k g_(k-2)] = gamma_nu(2)``. Hence ``s2 = M2 - M1`` with
``M1 = sum w1 g_k g_(k-1) / sum w1`` over links (``w1 = lambda_k lambda_(k-1)``) and ``M2
= sum w2 g_k g_(k-2) / sum w2`` over *2-links*, where loop ``k`` is linked to ``k - 1``
and ``k - 1`` to ``k - 2`` (``w2 = lambda_k lambda_(k-2)``). It is unbiased when the own
part's autocovariance is the same at lag 1 and lag 2: no drift, or one slow against two
loops. No 2-link in memory gives ``NO_LINKS``; a negative ``s2`` beyond ``ROUNDING`` (of
the two means' sums in absolute values) gives ``NO_BAND``.

Degrees of freedom of ``s2``: take the scores normal, of one variance ``gamma0``, with
autocovariances ``gamma1`` and ``gamma2`` within a linked run and none at longer lags or
across a break, estimated by the weighted mean square, ``M1`` and ``M2``. The fourth
moments of normal variables (``Cov(g_a g_b, g_c g_d) = gamma(a-c) gamma(b-d) + gamma(a-d)
gamma(b-c)``) give ``Var(s2_hat) = V11 + V22 - 2 V12`` with

    V11 W1^2 = (gamma0^2 + gamma1^2) S11_0 + 2 (gamma1^2 + gamma0 gamma2) S11_1 + 2 gamma2^2 S11_2
    V22 W2^2 = (gamma0^2 + gamma2^2) S22_0 + 2 gamma1^2 S22_1 + 2 gamma2^2 S22_2
    V12 W1 W2 = gamma1 (gamma0 + gamma2) (X_0 + X_-1) + gamma1 gamma2 (X_+1 + X_-2)

where ``W1 = sum w1``, ``W2 = sum w2``, ``S11_d`` is the sum of ``w1_k w1_(k-d)`` over
links ``d`` apart in one run, ``S22_d`` the same for 2-links, and ``X_d`` the sum of
``w1_k w2_(k-d)`` over a link and a 2-link ``d`` apart in one run (``X_-1``: the link
just before the 2-link's last loop, ``X_-2``: the one before that). All are fixed-size
sums kept on each event, with the weights of the last two links and 2-links in the
state (set to 0 at a break). A chi-square variable with the same mean and variance has
``2 s2^2 / Var(s2_hat)`` degrees of freedom. A pure chain of ``N`` equal links of rest
errors has ``2 N / 21`` (against ``2 N / 7`` for lag-1 products alone): the difference
of two means is noisier than one. Below one degree of freedom, or with a variance of
``s2`` that comes out at or below zero, the status is ``LOW_DOF``. The band is ``R +- t
sqrt(s2 C2) / A`` with the Student-t quantile at these degrees of freedom; nothing else
multiplies it.

Its limits: it holds the ratio over these loops, not the efficiency under other use; the
rest errors are taken as independent and of one size, which they are not quite (the
state of charge at a voltage stop depends on the current before it); a drift of the
loops' own efficiency that changes within two loops still biases ``s2`` (a drift whose
lag-1 autocovariance exceeds its lag-2 one makes ``s2`` and the band too small); the
degrees of freedom assume normal scores and no autocovariance beyond lag 2; the latest
rest dominates ``C2``, so the band rests on the error of one rest whose size is learnt
from the others; a zero offset of the current sensor does not cancel and is not in the
band (a gain error cancels in the ratio).

Portability rule: plain float arithmetic, a fixed-size state (arrays of length 3, 9 and
144; for the ratio fit 2, 3, 4, 9 and 36), no numpy, no pandas, no growing containers, no
recursion, no exceptions for control flow. Each class maps to a C struct, each function
to a C function. The test vectors live in ``tests/vectors/``.
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
TOO_FEW_EVENTS = 8
OUT_OF_RANGE = 9
NO_LINKS = 10
LOW_DOF = 11

STATUS_TEXT = {
    OK: "estimated",
    TOO_FEW_FULL_EMPTY: "not possible from this data: too few full-to-empty or empty-to-full pairs",
    TOO_FEW_SAME_KIND: "not possible from this data: too few full-to-full or empty-to-empty pairs to fix the offset",
    SINGULAR: "not possible from this data: the pairs do not separate capacity from offset",
    STALE: "not possible from this data: no full-to-empty or empty-to-full pair within the memory time",
    NO_BAND: "not possible from this data: the residuals give no positive variance for the band",
    NO_LEVELS: "not possible from this data: not enough history yet to set the full and empty levels",
    SHORT_SPAN: "not possible from this data: the full-to-empty and empty-to-full pairs span too short a time",
    TOO_FEW_EVENTS: "not possible from this data: too few events in memory",
    OUT_OF_RANGE: "not possible from this data: the estimate lies outside the physically possible range",
    NO_LINKS: "not possible from this data: no three events in a row in memory share their end points",
    LOW_DOF: "not possible from this data: the band rests on less than one degree of freedom",
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


# --- the through-origin ratio fit ------------------------------------------------------

_NR = 3  # length of z = (regressors, centred numerator); index of s4 is ((r * 2 + s) * 3 + i) * 3 + j


class Ratio:
    """Fixed-size state of the through-origin fit ``b = (theta . u) a`` with weight ``1 / a``.

    ``p = 1``: ``u = (1,)``, ``theta = (k,)``. ``p = 2``: ``u = (1, y)`` with ``y`` in years
    since ``t0_days``, ``theta = (k0, k1)``. ``a`` = sum lambda a u u' (row-major 2 x 2),
    ``h`` = sum lambda u (b - k_ref a); ``s4`` = sum lambda^2 u_r u_s z_i z_j and ``s4l`` =
    sum lambda_k lambda_(k-1) (u_k,r u_(k-1),s + u_(k-1),r u_k,s) z_k,i z_(k-1),j over
    linked events, with z = (a u, b - k_ref a) (2 x 2 x 3 x 3 each). ``k_ref`` is b / a of
    the first event the state accepted. ``m`` = sum lambda and ``m_lag`` = sum
    sqrt(lambda_k lambda_(k-1)) are the decayed numbers of events and of links (as in the
    capacity fit); ``m2`` = sum lambda^2 and ``m2_lag`` = sum lambda_k lambda_(k-1) are the
    same counts with the weights of the band's sums, and ``m2_ll`` the same over links
    whose previous event is linked too; they set ``rho`` and the degrees of freedom.
    The loop band (``loop_solve``) adds ``c2`` = sum over rests of their squared
    coefficients, ``w4`` = sum (lambda_k lambda_(k-1))^2 over links, ``w4l`` = sum of
    the products of consecutive link weights, and ``prev_lw``, the weight of the link
    that ended at the previous event now (0 when that event was not linked). For the
    lag-2 products: ``s4l2`` = sum w2 z_k,i z_(k-2),j (index i * 3 + j) and ``m2_l2`` =
    sum w2 over 2-links (``w2 = lambda_k lambda_(k-2)``, loop k linked to k - 1 and k - 1
    to k - 2); ``s11_2``, ``s22_0``, ``s22_1``, ``s22_2``, ``x0``, ``xm1``, ``xp1``,
    ``xm2``, the sums of weight products that set the degrees of freedom (module
    docstring); ``prev_lw2``, ``prev_w2`` and ``prev_w2b``, the weights now of the link
    two events back and of the 2-links one and two events back (0 across a break); and
    ``prev2_t``, ``prev2_z``, the time and ``z`` of the event two back.
    """

    __slots__ = (
        "p", "tau_days", "t0_days", "t_last", "k_ref", "has_ref", "a", "h", "s4", "s4l",
        "m", "m_lag", "m2", "m2_lag", "m2_ll", "n", "n_skipped", "y_min", "y_max",
        "prev_ok", "prev_linked", "prev_t", "prev_u", "prev_z", "c2", "w4", "w4l", "prev_lw",
        "s4l2", "m2_l2", "s11_2", "s22_0", "s22_1", "s22_2", "x0", "xm1", "xp1", "xm2",
        "prev_lw2", "prev_w2", "prev_w2b", "prev2_t", "prev2_z",
    )

    def __init__(self, p: int, tau_days: float, t0_days: float = 0.0) -> None:
        self.p = int(p)
        self.tau_days = float(tau_days)  # memory time; 0 or less means no forgetting
        self.t0_days = float(t0_days)  # origin of the growth fit's years
        self.t_last = _NEVER
        self.k_ref = 0.0
        self.has_ref = 0
        self.a = [0.0] * 4
        self.h = [0.0] * 2
        self.s4 = [0.0] * 36
        self.s4l = [0.0] * 36
        self.m = 0.0  # decayed number of events
        self.m_lag = 0.0  # decayed number of lag-1 links
        self.m2 = 0.0  # sum of lambda^2
        self.m2_lag = 0.0  # sum of lambda_k lambda_(k-1) over links
        self.m2_ll = 0.0  # the same over links that follow a link
        self.n = 0  # events ever accepted
        self.n_skipped = 0  # events refused: a <= 0, or a or b not finite
        self.y_min = 1.0e30  # first and last year of an accepted event
        self.y_max = -1.0e30
        self.prev_ok = 0
        self.prev_linked = 0
        self.prev_t = _NEVER
        self.prev_u = [0.0] * 2
        self.prev_z = [0.0] * _NR
        self.c2 = 0.0  # sum of the rests' squared coefficients (loop band)
        self.w4 = 0.0  # sum of (lambda_k lambda_(k-1))^2 over links
        self.w4l = 0.0  # sum of w_l w_(l-1) over consecutive links
        self.prev_lw = 0.0  # weight of the link that ended at the previous event, now
        self.s4l2 = [0.0] * 9  # sum w2 z_k,i z_(k-2),j over 2-links
        self.m2_l2 = 0.0  # sum w2 over 2-links
        self.s11_2 = 0.0  # sum w1_k w1_(k-2) over links two apart in one run
        self.s22_0 = 0.0  # sum w2^2
        self.s22_1 = 0.0  # sum w2_k w2_(k-1)
        self.s22_2 = 0.0  # sum w2_k w2_(k-2)
        self.x0 = 0.0  # sum w1_k w2_k
        self.xm1 = 0.0  # sum w1_(k-1) w2_k
        self.xp1 = 0.0  # sum w1_k w2_(k-1)
        self.xm2 = 0.0  # sum w1_(k-2) w2_k
        self.prev_lw2 = 0.0  # weight now of the link two events back (0 across a break)
        self.prev_w2 = 0.0  # weight now of the 2-link that ended at the previous event
        self.prev_w2b = 0.0  # the same two events back
        self.prev2_t = _NEVER
        self.prev2_z = [0.0] * _NR


def ratio_state(tau_days: float) -> Ratio:
    """The ratio ``k`` of ``b = k a``, with memory time ``tau_days``."""
    return Ratio(1, tau_days)


def growth_state(t0_days: float) -> Ratio:
    """The growth fit ``b = (k0 + k1 y) a``, ``y`` in years since ``t0_days``, no forgetting."""
    return Ratio(2, 0.0, t0_days)


def _ratio_decay(state: Ratio, t_days: float) -> None:
    """Move the state forward to ``t_days``; old events lose weight: lambda in the normal
    matrix and the counts, lambda^2 in the band's sums."""
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
    f2 = f * f  # the band's sums carry the weight twice
    for k in range(4):
        state.a[k] *= f
    for k in range(36):
        state.s4[k] *= f2
        state.s4l[k] *= f2
    for k in range(2):
        state.h[k] *= f
    state.m *= f
    state.m_lag *= f
    state.m2 *= f2
    state.m2_lag *= f2
    state.m2_ll *= f2
    f4 = f2 * f2
    state.c2 *= f2
    state.w4 *= f4
    state.w4l *= f4
    state.prev_lw *= f2
    for k in range(9):
        state.s4l2[k] *= f2
    state.m2_l2 *= f2
    state.prev_lw2 *= f2
    state.prev_w2 *= f2
    state.prev_w2b *= f2
    state.s11_2 *= f4
    state.s22_0 *= f4
    state.s22_1 *= f4
    state.s22_2 *= f4
    state.x0 *= f4
    state.xm1 *= f4
    state.xp1 *= f4
    state.xm2 *= f4


def _break_history(state: Ratio) -> None:
    """A break of the chain: no link or 2-link before it counts with one after it."""
    state.prev_lw = 0.0
    state.prev_lw2 = 0.0
    state.prev_w2 = 0.0
    state.prev_w2b = 0.0


def ratio_update(state: Ratio, t_days: float, a: float, b: float, linked: int) -> None:
    """Add one event ``(a, b)`` that ends at ``t_days`` (events in time order).

    ``linked`` is 1 when the event shares its start point with the end of the previous
    event (their errors share that point). An event with ``a <= 0`` or a non-finite sum
    is refused and counted in ``n_skipped``; the next event is then not linked to it.
    """
    if not (a > 0.0) or not math.isfinite(a) or not math.isfinite(b):
        state.n_skipped += 1
        state.prev_ok = 0
        state.prev_linked = 0
        _break_history(state)
        return
    _ratio_decay(state, t_days)
    if state.has_ref == 0:
        state.k_ref = b / a
        state.has_ref = 1
    p = state.p
    y = (t_days - state.t0_days) / 365.25
    u = [1.0, y]
    z = [0.0] * _NR
    for r in range(p):
        z[r] = u[r] * a
    z[p] = b - state.k_ref * a
    for r in range(p):
        for s in range(p):
            state.a[2 * r + s] += a * u[r] * u[s]
        state.h[r] += u[r] * z[p]
    for r in range(p):
        for s in range(p):
            uu = u[r] * u[s]
            for i in range(p + 1):
                for j in range(p + 1):
                    state.s4[((r * 2 + s) * 3 + i) * 3 + j] += uu * z[i] * z[j]
    link = 1 if linked != 0 and state.prev_ok != 0 else 0
    if link != 0:
        g = 1.0  # lambda_k lambda_(k-1) now, with lambda_k = 1
        if state.tau_days > 0.0:
            g = math.exp(-(t_days - state.prev_t) / state.tau_days)
        for r in range(p):
            for s in range(p):
                uu = g * (u[r] * state.prev_u[s] + state.prev_u[r] * u[s])
                for i in range(p + 1):
                    for j in range(p + 1):
                        state.s4l[((r * 2 + s) * 3 + i) * 3 + j] += uu * z[i] * state.prev_z[j]
        state.m_lag += math.sqrt(g)
        state.m2_lag += g
        if state.prev_linked != 0:
            state.m2_ll += g
        # The loop band: the previous end rest (-g) becomes the shared rest (1 - g), and a
        # new end rest (-1) comes in.
        state.c2 += 2.0 - 2.0 * g
        state.w4 += g * g
        state.w4l += g * state.prev_lw
        state.s11_2 += g * state.prev_lw2  # 0 unless the link before the previous one is in this run
        w2 = 0.0
        if state.prev_linked != 0:
            # A 2-link: this loop, the previous one and the one before it in a row.
            w2 = 1.0  # lambda_k lambda_(k-2) now
            if state.tau_days > 0.0:
                w2 = math.exp(-(t_days - state.prev2_t) / state.tau_days)
            for i in range(p + 1):
                for j in range(p + 1):
                    state.s4l2[i * 3 + j] += w2 * z[i] * state.prev2_z[j]
            state.m2_l2 += w2
            state.s22_0 += w2 * w2
            state.s22_1 += w2 * state.prev_w2
            state.s22_2 += w2 * state.prev_w2b
            state.x0 += g * w2
            state.xm1 += state.prev_lw * w2
            state.xm2 += state.prev_lw2 * w2
        state.xp1 += g * state.prev_w2
        state.prev_lw2 = state.prev_lw
        state.prev_lw = g
        state.prev_w2b = state.prev_w2
        state.prev_w2 = w2
    else:
        state.c2 += 2.0  # a new start rest (+1) and a new end rest (-1)
        _break_history(state)
    state.prev_linked = link
    state.prev_ok = 1
    state.prev2_t = state.prev_t
    for r in range(_NR):
        state.prev2_z[r] = state.prev_z[r]
    state.prev_t = t_days
    for r in range(2):
        state.prev_u[r] = u[r]
    for r in range(_NR):
        state.prev_z[r] = z[r]
    state.m += 1.0
    state.m2 += 1.0
    state.n += 1
    if y < state.y_min:
        state.y_min = y
    if y > state.y_max:
        state.y_max = y


class RatioEstimate:
    """The result of ``ratio_solve`` (the C port's output struct).

    ``theta`` = (k, NaN) for the ratio fit, (k0, k1) for the growth fit; ``sigma`` their
    standard errors; ``cov`` the row-major 2 x 2 covariance (NaN outside p x p). ``low``
    and ``high`` are the statistical band of k, or of k1 for the growth fit. ``theta``
    is also filled when the status is ``OUT_OF_RANGE``, so the caller can say what came
    out; it is not an estimate then. ``dof`` is the band's degrees of freedom (``m - p``
    times the share lost to the lag-1 term; for ``loop_solve`` those of the rest error's
    variance; module docstring); callers that form a band from ``cov`` use the same.
    ``rest_error`` is filled by ``loop_solve`` only.
    """

    __slots__ = (
        "status", "theta", "sigma", "cov", "low", "high", "dof", "m", "m_lag", "rho", "span_years", "rest_error",
    )

    def __init__(self) -> None:
        self.status = SINGULAR
        self.theta = [math.nan] * 2
        self.sigma = [math.nan] * 2
        self.cov = [math.nan] * 4
        self.low = math.nan
        self.high = math.nan
        self.dof = 0.0
        self.m = 0.0
        self.m_lag = 0.0
        self.rho = math.nan
        self.span_years = 0.0
        self.rest_error = math.nan  # loop band only: sqrt(s2), the rest error in the units of b


def ratio_solve(
    state: Ratio,
    t_days: float,
    z: float,
    min_events: float,
    min_span_years: float,
    lower: float,
    upper: float,
) -> RatioEstimate:
    """The estimate at ``t_days`` (the state is decayed to it first).

    ``z`` is the two-sided normal quantile of the band. Status, in this order:
    ``TOO_FEW_EVENTS`` when the decayed event count is below ``min_events``;
    ``SHORT_SPAN`` (growth fit only) when the events span less than ``min_span_years``;
    ``SINGULAR``; ``OUT_OF_RANGE`` when k (growth fit: k0, or the fitted value at
    ``t_days``) is ``<= lower`` or ``> upper``; ``NO_BAND`` when ``m <= p`` or a variance
    comes out negative beyond rounding (module docstring); else ``OK``. The band is the
    statistical band only: the caller adds any systematic term.
    """
    _ratio_decay(state, t_days)
    out = RatioEstimate()
    out.m = state.m
    out.m_lag = state.m_lag
    if state.n > 0:
        out.span_years = state.y_max - state.y_min
    if state.m < min_events:
        out.status = TOO_FEW_EVENTS
        return out
    p = state.p
    if p == 2 and out.span_years < min_span_years:
        out.status = SHORT_SPAN
        return out
    inv = [0.0] * 9  # row-major in 3 x 3, as _invert writes it
    if p == 1:
        if not (state.a[0] > 0.0):
            out.status = SINGULAR
            return out
        inv[0] = 1.0 / state.a[0]
    else:
        mat = [state.a[0], state.a[1], 0.0, state.a[2], state.a[3], 0.0, 0.0, 0.0, 0.0]
        if _invert(2, mat, inv) == 0:
            out.status = SINGULAR
            return out
    th = [0.0, 0.0]
    for r in range(p):
        acc = 0.0
        for k in range(p):
            acc += inv[3 * r + k] * state.h[k]
        th[r] = acc
    th[0] += state.k_ref
    value = th[0]
    fitted = th[0]
    if p == 2:
        fitted = th[0] + th[1] * (t_days - state.t0_days) / 365.25
    if value <= lower or value > upper or fitted <= lower or fitted > upper:
        for r in range(p):
            out.theta[r] = th[r]
        out.status = OUT_OF_RANGE
        return out
    nu = state.m - p
    out.dof = nu
    if nu <= 0.0:
        out.status = NO_BAND
        return out
    # phi contracts the sums: phi' z = b - theta' u a, the residual.
    phi = [0.0] * _NR
    for r in range(p):
        phi[r] = -th[r]
    phi[0] += state.k_ref
    phi[p] = 1.0
    meat = [0.0] * 4
    size = [0.0] * 4  # the same sums in absolute values: the scale of their rounding error
    for r in range(p):
        for s in range(p):
            acc = 0.0
            mag = 0.0
            for i in range(p + 1):
                for j in range(p + 1):
                    idx = ((r * 2 + s) * 3 + i) * 3 + j
                    acc += phi[i] * (state.s4[idx] + state.s4l[idx]) * phi[j]
                    mag += abs(phi[i] * phi[j]) * (abs(state.s4[idx]) + abs(state.s4l[idx]))
            meat[2 * r + s] = acc
            size[2 * r + s] = mag
    # Lag-1 correlation of the residuals across links (diagnostic): from the u_0 = 1 sums,
    # each normalised by its own sum of weights.
    out.rho = 0.0
    e2 = 0.0
    lag2 = 0.0
    for i in range(p + 1):
        for j in range(p + 1):
            e2 += phi[i] * state.s4[i * 3 + j] * phi[j]
            lag2 += phi[i] * state.s4l[i * 3 + j] * phi[j]
    rho = 0.0
    if state.m2_lag > 0.0 and e2 > 0.0:
        rho = (0.5 * lag2 / state.m2_lag) / (e2 / state.m2)
    if state.m_lag > 1.0:
        out.rho = rho
    # Degrees of freedom of the band (module docstring): those of the variance estimate,
    # (m - p) times its loss from the lag-1 term; 1 without links.
    q = state.m2_lag / state.m2
    qq = state.m2_ll / state.m2
    den = 1.0 + 2.0 * q + 8.0 * rho * q + 4.0 * rho * rho * (q + qq)
    if den > 0.0:
        out.dof = nu * (1.0 + 2.0 * rho * q) * (1.0 + 2.0 * rho * q) / den
    scale = state.m / nu
    for r in range(p):
        for s in range(p):
            acc = 0.0
            mag = 0.0
            for i in range(p):
                for j in range(p):
                    acc += inv[3 * r + i] * meat[2 * i + j] * inv[3 * j + s]
                    mag += abs(inv[3 * r + i] * inv[3 * j + s]) * size[2 * i + j]
            if r == s:
                # A negative value within rounding of the sums is zero (a fit with no scatter).
                if acc < 0.0 and -acc <= ROUNDING * mag:
                    acc = 0.0
                if acc < 0.0:
                    out.status = NO_BAND
                    return out
            out.cov[2 * r + s] = scale * acc
    for r in range(p):
        out.theta[r] = th[r]
        out.sigma[r] = math.sqrt(out.cov[3 * r])
    main = p - 1
    half = t_quantile(z, out.dof) * out.sigma[main]
    out.low = th[main] - half
    out.high = th[main] + half
    out.status = OK
    return out


def loop_solve(
    state: Ratio,
    t_days: float,
    z: float,
    min_events: float,
    lower: float,
    upper: float,
) -> RatioEstimate:
    """The loop band at ``t_days`` (the state is decayed to it first; ``p = 1`` only).

    The value is ``k = sum lambda b / sum lambda a``, as ``ratio_solve`` gives it; the band
    is ``k +- t sqrt(s2 C2) / A`` from the rest error ``s2``, the mean lag-2 product of the
    scores minus the mean lag-1 product (module docstring). Status, in this order:
    ``TOO_FEW_EVENTS``; ``SINGULAR`` (also for a growth state); ``OUT_OF_RANGE`` (as
    ``ratio_solve``, theta filled); ``NO_LINKS`` when no 2-link is in memory; ``NO_BAND``
    when ``s2`` is negative beyond rounding (rho filled); ``LOW_DOF`` below one degree of
    freedom (rho, dof and rest_error filled); else ``OK``. ``rest_error`` is ``sqrt(s2)``;
    ``rho`` the lag-1 correlation of the scores.
    """
    _ratio_decay(state, t_days)
    out = RatioEstimate()
    out.m = state.m
    out.m_lag = state.m_lag
    if state.n > 0:
        out.span_years = state.y_max - state.y_min
    if state.m < min_events:
        out.status = TOO_FEW_EVENTS
        return out
    if state.p != 1 or not (state.a[0] > 0.0):
        out.status = SINGULAR
        return out
    inv = 1.0 / state.a[0]
    k = state.h[0] * inv + state.k_ref
    if k <= lower or k > upper:
        out.theta[0] = k
        out.status = OUT_OF_RANGE
        return out
    if not (state.m2_l2 > 0.0):
        out.status = NO_LINKS
        return out
    # phi contracts the sums: phi' z = b - k a, the score.
    phi = [state.k_ref - k, 1.0]
    e2 = 0.0
    lag1 = 0.0
    lag2 = 0.0
    mag1 = 0.0  # the lag sums in absolute values: the scale of their rounding error
    mag2 = 0.0
    for i in range(2):
        for j in range(2):
            e2 += phi[i] * state.s4[i * 3 + j] * phi[j]
            lag1 += phi[i] * state.s4l[i * 3 + j] * phi[j]
            lag2 += phi[i] * state.s4l2[i * 3 + j] * phi[j]
            mag1 += abs(phi[i] * phi[j]) * abs(state.s4l[i * 3 + j])
            mag2 += abs(phi[i] * phi[j]) * abs(state.s4l2[i * 3 + j])
    w1 = state.m2_lag
    w2 = state.m2_l2
    g0 = e2 / state.m2  # the weighted mean square
    g1 = 0.5 * lag1 / w1  # M1, the weighted mean of g_k g_(k-1) over links (s4l holds it twice)
    g2 = lag2 / w2  # M2, the same of g_k g_(k-2) over 2-links
    rho = 0.0
    if e2 > 0.0:
        rho = g1 / g0
    out.rho = rho
    s2 = g2 - g1
    if s2 < 0.0:
        # Within rounding of the two means' sums it is zero; beyond it the rest error's
        # variance is negative and there is no band.
        if -s2 > ROUNDING * (0.5 * mag1 / w1 + mag2 / w2):
            out.status = NO_BAND
            return out
        s2 = 0.0
    # Degrees of freedom of s2 (module docstring).
    v11 = ((g0 * g0 + g1 * g1) * state.w4 + 2.0 * (g1 * g1 + g0 * g2) * state.w4l + 2.0 * g2 * g2 * state.s11_2) / (w1 * w1)
    v22 = ((g0 * g0 + g2 * g2) * state.s22_0 + 2.0 * g1 * g1 * state.s22_1 + 2.0 * g2 * g2 * state.s22_2) / (w2 * w2)
    v12 = (g1 * (g0 + g2) * (state.x0 + state.xm1) + g1 * g2 * (state.xp1 + state.xm2)) / (w1 * w2)
    var_s2 = v11 + v22 - 2.0 * v12
    out.dof = 0.0
    if var_s2 > 0.0:
        out.dof = 2.0 * s2 * s2 / var_s2
    out.rest_error = math.sqrt(s2)
    if out.dof < 1.0:
        out.status = LOW_DOF
        return out
    var = s2 * state.c2 * inv * inv
    out.theta[0] = k
    out.cov[0] = var
    out.sigma[0] = math.sqrt(var)
    half = t_quantile(z, out.dof) * out.sigma[0]
    out.low = k - half
    out.high = k + half
    out.status = OK
    return out
