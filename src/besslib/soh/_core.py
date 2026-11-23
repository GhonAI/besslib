"""The capacity estimator core: rest-anchored coulomb counting, one update per pair.

Each *pair* runs from one full or empty rest to the next linked one. Its measured
charge ``q`` (Ah, positive = charging) is modelled as

    q = C * x + beta * dt + gamma_f * du_f + gamma_e * du_e

``x`` is the change of state of charge: +1 from empty to full, -1 from full to empty,
0 from full to full or empty to empty. ``dt`` is the time between the rests in days.
``C`` is the usable capacity (Ah). ``beta`` (Ah per day) is the charge the meter does
not see: BMS supply, balancing, self-discharge and sensor offset. The *offset current*
is ``-beta / 24`` A, negative when the battery is drained, as in Figgener et al. (2024,
Supplementary Methods).

The *voltage terms* (version 0.2, ``voltage``): two stops of one kind are not at the same
state of charge. A BMS that stops at a voltage limit gives almost the same edge voltage
at every stop, and the difference shows in the voltage after relaxation. So every rest
carries its relaxed open-circuit voltage ``u`` minus a reference of its own kind, ``r0f``
for a full rest and ``r0e`` for an empty one: the first rest of that kind the state sees
(fixed for the life of the state, so it is causal and the sums never need rebuilding).
A pair from rest ``a`` to rest ``b`` gets ``du_f = [b full] (u_b - r0f) - [a full] (u_a -
r0f)`` and ``du_e = [b empty] (u_b - r0e) - [a empty] (u_a - r0e)``: on every kind of
pair, each end gives its own kind's term. ``gamma_f`` and ``gamma_e`` (Ah per volt near
full and near empty) turn the voltage into charge. They and the offset do not depend on
the reference; ``C`` does. Since ``[b full] - [a full] = x`` and ``[b empty] - [a empty] =
-x``, moving the references to ``rf`` and ``re`` is a change of parameters only:

    C(rf, re) = C(r0f, r0e) + gamma_f (rf - r0f) - gamma_e (re - r0e)

the capacity between a full rest at ``rf`` and an empty rest at ``re``. The estimate is
read at the forgetting-weighted mean relaxed voltage of each kind at the date: the rests
the state has seen, each weighted ``exp(-age / tau_days)`` (``Estimate.u_full``,
``u_empty``; four floats of the state hold the decayed sums). This is the capacity
between the full and the empty stops of recent use. A rest enters these sums once: as
the end of a pair, or as the start of a pair that does not continue the one before. A
rest whose voltage is not finite gives no voltage term (it is taken at the reference)
and does not enter the sums. A voltage term whose kind of pair (full-to-full or
empty-to-empty) has fewer than ``min_same`` pairs in memory (decayed count) is left out
of that solve: the solve then uses the sub-system of the same sums, and
``Estimate.terms`` says which terms it used.

The *trend* (version 0.2, ``trend``): ``C = c0 + r * y``, with ``y`` in years since
``t0``, the time of the first pair the state sees, so ``q = (c0 + r y) x + ...``. The
estimate is read at the date: ``C(t) = c0 + r y_t``. Under a straight-line fade a
weighted line fit has no lag, whatever its weights; without the trend the estimate lags
a steady fade by about ``tau_days``. The trend is part of a solve only while the decayed
count of full-to-empty and empty-to-full pairs is at least ``min_cross_trend``: with few
of them a line through them is poorly fixed, and the trend's error then dominates the
estimate. There is no minimum span: a short span gives a wide band, shown as it is, and
``SINGULAR`` when the normal matrix is singular. The *fade model* is the same fit
without forgetting; its main quantity is ``r``, and it needs ``min_span_years``.

The capacity at the date is the linear combination ``g' theta`` with the gradient ``g =
(1, y_t, 0, rf - r0f, -(re - r0e))`` over the terms used, and its band is from ``g' V
g``, the same sandwich, with no further term for the references: they define the
quantity reported (the capacity between those two voltages), and are reported with it.
The fade fit's start capacity ``c0`` is read the same way, at the record's mean
voltages (no forgetting).

The state's regressors, in this order: ``x``, ``x * y`` (trend), ``dt``, ``du_f`` and
``du_e`` (voltage terms), at most five. ``Estimate`` gives every parameter at a fixed
position (``I_C``, ``I_BETA``, ``I_RATE``, ``I_GF``, ``I_GE``) whatever its index.

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
Student-t quantile. ``p`` counts the parameters of the solve (a term left out does not
count). This form is not positive by construction: a negative variance of the main
quantity is reported as not possible, never replaced by a floor. The current sensor's
gain error is added to the capacity's band as a systematic term. The Birge ratio
(reduced chi-square) and the lag-1 correlation ``rho`` of the weighted residuals are
reported as diagnostics; they do not scale the band.

The residuals are not known until the fit is solved, so ``M`` is kept as fixed-size
sums of fourth-order products of ``z = (a, q - c_weight * x)`` (at most six long);
``solve`` contracts them with ``(-theta', 1)``, with 0 for a term left out. Subtracting
``c_weight * x`` keeps these sums small, which matters in 32-bit arithmetic.

Old pairs fade by ``exp(-age / tau_days)``: a pair of age ``s`` counts as if its
variance were ``exp(s / tau_days)`` times larger (the convention of least squares with
forgetting). How often the forgetting weight enters the band's sums is
``band_forgetting``. With 2 (version 0.2) it enters twice, as the weight of a weighted
least-squares fit does: the score of pair k is ``lambda_k w_k a_k e_k``, so squares
carry ``lambda_k^2`` and links ``lambda_k lambda_(k-1)``, the form of the ratio fit
below, whose first choice says why. With 1 (release 0.1.0) it enters once, and a link
counts with ``sqrt(lambda_k lambda_(k-1))``, which keeps the two pairs of a link on the
same footing. Without forgetting both are the same. ``m = sum lambda`` stays the count
for ``m / (m - p)`` and for the minimum numbers of pairs.

Degrees of freedom of the band: with ``band_forgetting = 1``, ``m - p``. With 2, the
ratio fit's rule (its second choice, below): ``m - p`` times the share lost to the
lag-1 term, with ``q`` and ``q2`` the shares of links and of links that follow a link
(in the weights of the band's sums), and ``rho`` the lag-1 correlation of the main
quantity's scores ``c' g_k``, where ``c' = g' A^-1`` is its contrast, read from the
band's own sums. The residuals' ``rho`` is not the one that matters here: the pair
errors of a shared rest are correlated at about -1/2, but the capacity's scores carry
the sign of ``x``, so a full-to-empty pair followed by an empty-to-full pair gives
scores correlated at about +1/2, while a same-kind pair hardly enters the capacity's
score. Below one degree of freedom ``t_quantile`` holds at one. The fade fit follows the
same setting.

Plausibility (version 0.2, ``plausibility``): a full-to-empty pair with ``q >= 0``, or
an empty-to-full pair with ``q <= 0``, that ended within ``tau_days`` before the date
(at any time, without forgetting) gives ``WRONG_SIGN``; one float of the state holds the
time of the last such pair. A capacity at the date, or the low end of its band, at or
below zero gives ``OUT_OF_RANGE``. The kinds of pair are counted from the kind passed to
``update``, not from ``x == 0``. ``LEVELS_CROSSED`` is set by the caller, which sees the
levels.

Inverse: for ``p = 2`` and 3 the closed forms of release 0.1.0; for ``p = 4`` and 5 a
Cholesky factorisation in fixed loops. Years count from the first pair, so late in a
long record ``c0`` and ``r`` are strongly correlated and ``C(t) = c0 + r y_t`` loses
digits to cancellation; the 32-bit test vectors measure it.

``capacity_state`` and ``fade_state`` default to the fit of release 0.1.0 (no trend in
the capacity fit, no voltage terms, ``band_forgetting = 1``, no plausibility); with it
every number is that of release 0.1.0 to the last bit. ``besslib.soh.capacity`` passes
the settings of ``CapacityConfig``.

Related work: Plett, J. Power Sources 196, 2319
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
Var(b) / (sum lambda a)^2``, the variance of the estimate as computed. Keeping the
weight once (the capacity fit of release 0.1.0, ``band_forgetting = 1``) is exact only
if an event's variance grew as ``exp(age / tau_days)``; for events whose errors do not
grow with age that band is about ``sqrt(2)`` too wide at ``tau_days`` of memory (``(sum
lambda)^2 / sum lambda^2`` is close to ``2 sum lambda``). This fit uses the weight
twice, as the capacity fit does with ``band_forgetting = 2``. Without forgetting both
are the same.
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
diagnostic). Without links it is exactly ``m - p``. At very few
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

Portability rule: plain float arithmetic, a fixed-size state (arrays of length 5, 6, 25
and 900, and eight more floats for the voltage references; for the ratio fit 2, 3, 4, 9 and 36), no numpy, no pandas, no growing containers, no
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
LEVELS_CROSSED = 12  # set by the caller: the core sees pairs, not the levels (version 0.2)
WRONG_SIGN = 13  # version 0.2

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
    LEVELS_CROSSED: (
        "not possible from this data: the empty level has reached the full level, so full and empty no longer "
        "mark the two ends of the charge window"
    ),
    WRONG_SIGN: (
        "not possible from this data: a full-to-empty or empty-to-full pair within the memory time carries "
        "charge of the wrong sign"
    ),
}

#: Relative rounding tolerance of the band's sums, for 64-bit arithmetic. A 32-bit port
#: needs a larger one, set from its own test vectors.
ROUNDING = 1.0e-9

P_MAX = 5  # most regressors of the capacity fit: x, x * y, dt, du_f, du_e
_NZ = 6  # length of z = (regressors, centred charge); index of s4 is ((r * 5 + s) * 6 + i) * 6 + j

#: Positions in ``Estimate.theta`` and ``Estimate.sigma``, the same for every fit.
I_C = 0  # capacity (Ah): at the date for the capacity fit, at t0 for the fade fit
I_BETA = 1  # offset term (Ah per day)
I_RATE = 2  # trend of the capacity (Ah per year)
I_GF = 3  # full-to-full voltage term (Ah per V)
I_GE = 4  # empty-to-empty voltage term (Ah per V)

#: Kinds of pair, as ``update`` takes them. ``PAIR_FROM_X`` takes the kind from the sign
#: of ``x`` (release 0.1.0): full-to-empty below 0, empty-to-full above 0, else a
#: same-kind pair of unknown end.
PAIR_FROM_X = 0
PAIR_FE = 1
PAIR_EF = 2
PAIR_FF = 3
PAIR_EE = 4

#: Bits of ``Estimate.terms``: which optional terms the solve used.
TERM_TREND = 1
TERM_FULL = 2
TERM_EMPTY = 4

_NEVER = -1.0e30


class Fit:
    """Fixed-size state of a weighted least-squares fit with forgetting and lag-1 sums.

    Regressors, in this order: ``x``; ``x * y`` when ``trend`` is 1 (y in years since
    ``t0_days``); ``dt``; ``du_f`` and ``du_e`` when ``voltage`` is 1. ``p`` is their
    number (2 to 5). ``a`` = sum lambda w a a' (row-major 5 x 5), ``h`` = sum lambda w a q,
    ``c`` = sum lambda w q q; ``l`` = sum v a_k a_(k-1)', ``ly`` = sum v q_k a_(k-1),
    ``yl`` = sum v a_k q_(k-1), ``yy1`` = sum v q_k q_(k-1), with v = g sqrt(w_k w_(k-1)),
    over pairs that share a rest with the pair before them; ``g`` =
    exp(-(t_k - t_(k-1)) / (2 tau_days)) makes a link decay as sqrt(lambda_k lambda_(k-1)).
    ``s4`` = sum lambda^b w^2 a_r a_s z_i z_j and ``s4l`` = sum (lambda_k lambda_(k-1))^(b/2)
    w_k w_(k-1) (a_k,r a_(k-1),s + a_(k-1),r a_k,s) z_k,i z_(k-1),j, with z = (a, q -
    c_weight x) and ``b = band_forgetting``: the sums of the band's sandwich (5 x 5 x 6 x 6
    each). ``m``, ``m_lag``, ``m_cross``, ``m_same``, ``m_ff``, ``m_ee`` are decayed counts
    (sum lambda) of pairs, links and kinds of pair; ``m2`` = sum lambda^2, ``m2_lag`` = sum
    lambda_k lambda_(k-1) over links and ``m2_ll`` the same over links whose previous pair
    is linked too (the degrees of freedom with ``band_forgetting = 2``). ``t_wrong`` is the
    time of the last full-to-empty or empty-to-full pair whose charge has the wrong sign.
    ``r0_f`` and ``r0_e`` are the reference voltages of the voltage terms (the first full
    and the first empty rest; ``has_r0_f``, ``has_r0_e`` say whether they are set);
    ``wu_f``, ``su_f``, ``wu_e``, ``su_e`` are the decayed sums ``sum lambda`` and ``sum
    lambda (u - r0)`` over the full and the empty rests seen.
    """

    __slots__ = (
        "p", "fade", "trend", "voltage", "band", "plausibility", "c_weight", "tau_days",
        "t_last", "t_last_cross", "t_wrong", "t0_days", "has_t0",
        "a", "h", "c", "l", "ly", "yl", "yy1", "s4", "s4l",
        "m", "m_lag", "m_cross", "m_same", "m_ff", "m_ee", "m2", "m2_lag", "m2_ll", "n", "y_min", "y_max",
        "prev_ok", "prev_linked", "prev_a", "prev_q", "prev_w", "prev_t", "prev_z",
        "r0_f", "r0_e", "has_r0_f", "has_r0_e", "wu_f", "su_f", "wu_e", "su_e",
    )

    def __init__(
        self,
        c_weight: float,
        tau_days: float,
        t0_days: float = 0.0,
        fade: int = 0,
        trend: int = 0,
        voltage: int = 0,
        band_forgetting: int = 1,
        plausibility: int = 0,
    ) -> None:
        self.fade = 1 if fade != 0 else 0  # 1: the fade fit (main quantity r, minimum span, no gain term)
        self.trend = 1 if trend != 0 or fade != 0 else 0
        self.voltage = 1 if voltage != 0 else 0
        self.band = 2 if band_forgetting == 2 else 1
        self.plausibility = 1 if plausibility != 0 else 0
        self.p = 2 + self.trend + 2 * self.voltage
        self.c_weight = float(c_weight)
        self.tau_days = float(tau_days)  # memory time; 0 or less means no forgetting
        self.t0_days = float(t0_days)  # origin of the trend's years
        self.has_t0 = 1 if fade != 0 else 0  # the capacity fit takes the time of its first pair
        self.t_last = _NEVER
        self.t_last_cross = _NEVER
        self.t_wrong = _NEVER
        self.a = [0.0] * 25
        self.h = [0.0] * 5
        self.c = 0.0
        self.l = [0.0] * 25
        self.ly = [0.0] * 5
        self.yl = [0.0] * 5
        self.yy1 = 0.0
        self.s4 = [0.0] * 900
        self.s4l = [0.0] * 900
        self.m = 0.0  # decayed number of pairs
        self.m_lag = 0.0  # decayed number of lag-1 links
        self.m_cross = 0.0  # decayed number of full-to-empty and empty-to-full pairs
        self.m_same = 0.0  # decayed number of full-to-full and empty-to-empty pairs
        self.m_ff = 0.0  # decayed number of full-to-full pairs
        self.m_ee = 0.0  # decayed number of empty-to-empty pairs
        self.m2 = 0.0  # sum of lambda^2
        self.m2_lag = 0.0  # sum of lambda_k lambda_(k-1) over links
        self.m2_ll = 0.0  # the same over links that follow a link
        self.n = 0  # pairs ever added
        self.y_min = 1.0e30  # first and last year of a full-to-empty or empty-to-full pair
        self.y_max = -1.0e30
        self.prev_ok = 0
        self.prev_linked = 0
        self.prev_a = [0.0] * P_MAX
        self.prev_q = 0.0
        self.prev_w = 0.0
        self.prev_t = _NEVER
        self.prev_z = [0.0] * _NZ
        self.r0_f = 0.0  # reference voltages of the voltage terms: the first rest of each kind
        self.r0_e = 0.0
        self.has_r0_f = 0
        self.has_r0_e = 0
        self.wu_f = 0.0  # decayed sum of lambda over the full rests seen
        self.su_f = 0.0  # decayed sum of lambda (u - r0_f) over the full rests seen
        self.wu_e = 0.0
        self.su_e = 0.0


def capacity_state(
    c_weight: float,
    tau_days: float,
    local_trend: int = 0,
    voltage_terms: int = 0,
    band_forgetting: int = 1,
    plausibility: int = 0,
) -> Fit:
    """The capacity estimator: regressors (x, dt), plus x * y with ``local_trend`` and
    (du_f, du_e) with ``voltage_terms``. The defaults are the fit of release 0.1.0."""
    return Fit(c_weight, tau_days, 0.0, 0, local_trend, voltage_terms, band_forgetting, plausibility)


def fade_state(
    c_weight: float,
    t0_days: float,
    voltage_terms: int = 0,
    band_forgetting: int = 1,
    plausibility: int = 0,
) -> Fit:
    """The fade model: regressors (x, x * y, dt), plus (du_f, du_e) with ``voltage_terms``;
    no forgetting. The defaults are the fit of release 0.1.0."""
    return Fit(c_weight, 0.0, t0_days, 1, 1, voltage_terms, band_forgetting, plausibility)


def decay(state: Fit, t_days: float) -> None:
    """Move the state forward to ``t_days``; old pairs lose weight: lambda in the normal
    matrix and the counts, lambda^b in the band's sums (b = ``band_forgetting``)."""
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
    f2 = f * f
    fb = f
    if state.band == 2:
        fb = f2
    for k in range(25):
        state.a[k] *= f
        state.l[k] *= f
    for k in range(900):
        state.s4[k] *= fb
        state.s4l[k] *= fb
    for k in range(5):
        state.h[k] *= f
        state.ly[k] *= f
        state.yl[k] *= f
    state.c *= f
    state.yy1 *= f
    state.m *= f
    state.m_lag *= f
    state.m_cross *= f
    state.m_same *= f
    state.m_ff *= f
    state.m_ee *= f
    state.m2 *= f2
    state.m2_lag *= f2
    state.m2_ll *= f2
    state.wu_f *= f
    state.su_f *= f
    state.wu_e *= f
    state.su_e *= f


def _rest_voltage(state: Fit, full: int, u: float, weight: float, new: int) -> float:
    """The voltage term of one end of a pair: ``u`` minus its kind's reference (0 when
    ``u`` is not finite). A rest the state sees for the first time (``new``) sets the
    reference when its kind has none yet, and enters the decayed sums with ``weight``."""
    if not math.isfinite(u):
        return 0.0
    if full != 0:
        if state.has_r0_f == 0:
            state.r0_f = u
            state.has_r0_f = 1
        d = u - state.r0_f
        if new != 0:
            state.wu_f += weight
            state.su_f += weight * d
        return d
    if state.has_r0_e == 0:
        state.r0_e = u
        state.has_r0_e = 1
    d = u - state.r0_e
    if new != 0:
        state.wu_e += weight
        state.su_e += weight * d
    return d


def update(
    state: Fit,
    t_days: float,
    x: float,
    dt_days: float,
    q_ah: float,
    sigma_q_ah: float,
    sigma_x: float,
    linked: int,
    u_start: float = 0.0,
    u_end: float = 0.0,
    kind: int = PAIR_FROM_X,
) -> None:
    """Add one pair that ends at ``t_days`` (pairs in time order).

    ``linked`` is 1 when the pair starts at the rest where the previous pair ended.
    ``u_start`` and ``u_end`` are the relaxed open-circuit voltages of its two rests
    (volts); they are used only by a state with voltage terms, which also needs the
    ``kind`` (one of the ``PAIR_`` codes) to know each end's kind. With ``PAIR_FROM_X``
    and ``x = 0`` the ends' kinds are not known and the pair gets no voltage term.
    """
    decay(state, t_days)
    if state.has_t0 == 0:
        state.t0_days = t_days
        state.has_t0 = 1
    pk = kind
    if pk == PAIR_FROM_X:
        if x > 0.0:
            pk = PAIR_EF
        elif x < 0.0:
            pk = PAIR_FE
    du_f = 0.0
    du_e = 0.0
    if state.voltage != 0 and pk != PAIR_FROM_X:
        fa = 0  # 1 when the rest at the start (a) or the end (b) is full
        fb = 0
        if pk == PAIR_FE or pk == PAIR_FF:
            fa = 1
        if pk == PAIR_EF or pk == PAIR_FF:
            fb = 1
        new_a = 1  # rest a enters the reference sums unless it ended the pair before
        if linked != 0 and state.prev_ok != 0:
            new_a = 0
        wa = 1.0
        if state.tau_days > 0.0:
            wa = math.exp(-dt_days / state.tau_days)
        da = _rest_voltage(state, fa, u_start, wa, new_a)
        db = _rest_voltage(state, fb, u_end, 1.0, 1)
        if fb != 0:
            du_f += db
        else:
            du_e += db
        if fa != 0:
            du_f -= da
        else:
            du_e -= da
    w = 1.0 / (sigma_q_ah * sigma_q_ah + state.c_weight * state.c_weight * sigma_x * sigma_x)
    y = (t_days - state.t0_days) / 365.25
    p = state.p
    row = [0.0] * P_MAX
    row[0] = x
    nr = 1
    if state.trend != 0:
        row[1] = x * y
        nr = 2
    row[nr] = dt_days
    if state.voltage != 0:
        row[nr + 1] = du_f
        row[nr + 2] = du_e
    z = [0.0] * _NZ
    for r in range(p):
        z[r] = row[r]
    z[p] = q_ah - state.c_weight * x
    for r in range(p):
        for k in range(p):
            state.a[5 * r + k] += w * row[r] * row[k]
        state.h[r] += w * row[r] * q_ah
    state.c += w * q_ah * q_ah
    for r in range(p):
        for s in range(p):
            ww = w * w * row[r] * row[s]
            for i in range(p + 1):
                for j in range(p + 1):
                    state.s4[((r * 5 + s) * 6 + i) * 6 + j] += ww * z[i] * z[j]
    link = 0
    if linked != 0 and state.prev_ok != 0:
        link = 1
        g = 1.0  # sqrt(lambda_k lambda_(k-1)) now, with lambda_k = 1
        g2 = 1.0  # lambda_k lambda_(k-1) now
        if state.tau_days > 0.0:
            g = math.exp(-(t_days - state.prev_t) / (2.0 * state.tau_days))
            g2 = math.exp(-(t_days - state.prev_t) / state.tau_days)
        v = g * math.sqrt(w * state.prev_w)
        for r in range(p):
            for k in range(p):
                state.l[5 * r + k] += v * row[r] * state.prev_a[k]
            state.ly[r] += v * q_ah * state.prev_a[r]
            state.yl[r] += v * row[r] * state.prev_q
        state.yy1 += v * q_ah * state.prev_q
        state.m_lag += g
        state.m2_lag += g2
        if state.prev_linked != 0:
            state.m2_ll += g2
        gb = g
        if state.band == 2:
            gb = g2
        u = gb * w * state.prev_w
        for r in range(p):
            for s in range(p):
                aa = u * (row[r] * state.prev_a[s] + state.prev_a[r] * row[s])
                for i in range(p + 1):
                    for j in range(p + 1):
                        state.s4l[((r * 5 + s) * 6 + i) * 6 + j] += aa * z[i] * state.prev_z[j]
    state.prev_ok = 1
    state.prev_linked = link
    for r in range(P_MAX):
        state.prev_a[r] = row[r]
    for r in range(_NZ):
        state.prev_z[r] = z[r]
    state.prev_q = q_ah
    state.prev_w = w
    state.prev_t = t_days
    state.m += 1.0
    state.m2 += 1.0
    if pk == PAIR_FE or pk == PAIR_EF:
        state.m_cross += 1.0
        state.t_last_cross = t_days
        if y < state.y_min:
            state.y_min = y
        if y > state.y_max:
            state.y_max = y
        if (pk == PAIR_FE and q_ah >= 0.0) or (pk == PAIR_EF and q_ah <= 0.0):
            state.t_wrong = t_days
    else:
        state.m_same += 1.0
        if pk == PAIR_FF:
            state.m_ff += 1.0
        elif pk == PAIR_EE:
            state.m_ee += 1.0
    state.n += 1


def _invert(p: int, a: list, out: list) -> int:
    """Invert the symmetric p x p matrix ``a`` (row-major in 5 x 5) into ``out``.

    p = 2 and 3: the closed forms of release 0.1.0 (kept, so its settings give its
    numbers to the last bit); p = 4 and 5: ``_cholesky_inverse``. Returns 0 when the
    matrix is singular.
    """
    for k in range(25):
        out[k] = 0.0
    if p == 2:
        det = a[0] * a[6] - a[1] * a[5]
        if a[0] <= 0.0 or a[6] <= 0.0 or det <= 1.0e-9 * a[0] * a[6]:
            return 0
        out[0] = a[6] / det
        out[1] = -a[1] / det
        out[5] = -a[5] / det
        out[6] = a[0] / det
        return 1
    if p == 3:
        a11, a12, a13, a22, a23, a33 = a[0], a[1], a[2], a[6], a[7], a[12]
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
        for r in range(3):
            for k in range(3):
                out[5 * r + k] = vals[3 * r + k] / det
        return 1
    return _cholesky_inverse(p, a, out)


def _cholesky_inverse(p: int, a: list, out: list) -> int:
    """Invert the symmetric positive definite p x p matrix ``a`` (p <= 5, row-major in
    5 x 5) into ``out`` by Cholesky, ``a = L L'`` and ``a^-1 = L^-T L^-1``, in fixed
    loops. Returns 0 when a pivot is not above 1e-12 times its diagonal element (a
    singular matrix)."""
    low = [0.0] * 25
    for j in range(p):
        d = a[5 * j + j]
        for k in range(j):
            d -= low[5 * j + k] * low[5 * j + k]
        if a[5 * j + j] <= 0.0 or d <= 1.0e-12 * a[5 * j + j]:
            return 0
        low[5 * j + j] = math.sqrt(d)
        for i in range(j + 1, p):
            s = a[5 * i + j]
            for k in range(j):
                s -= low[5 * i + k] * low[5 * j + k]
            low[5 * i + j] = s / low[5 * j + j]
    li = [0.0] * 25  # L^-1, lower triangular
    for i in range(p):
        li[5 * i + i] = 1.0 / low[5 * i + i]
        for j in range(i):
            s = 0.0
            for k in range(j, i):
                s += low[5 * i + k] * li[5 * k + j]
            li[5 * i + j] = -s / low[5 * i + i]
    for i in range(p):
        for j in range(p):
            s = 0.0
            k0 = i
            if j > i:
                k0 = j
            for k in range(k0, p):
                s += li[5 * k + i] * li[5 * k + j]
            out[5 * i + j] = s
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
    """The result of ``solve`` (the C port's output struct).

    ``theta`` and ``sigma`` (standard errors) at the fixed positions ``I_C`` (capacity:
    at the date for the capacity fit, at t0 for the fade fit), ``I_BETA`` (offset term),
    ``I_RATE`` (trend), ``I_GF`` and ``I_GE`` (voltage terms); NaN where the fit has no
    such term or left it out. ``sigma[I_C]`` of the capacity fit includes the gain
    error's systematic term. ``low`` and ``high`` are the band of the main quantity: the
    capacity at the date, or the rate for the fade fit. ``terms`` holds the ``TERM_``
    bits of the optional terms used, ``p`` the number of parameters solved for, ``dof``
    the band's degrees of freedom. ``theta``, ``low`` and ``high`` are also filled when
    the status is ``OUT_OF_RANGE``, so the caller can say what came out; it is not an
    estimate then. ``u_full`` and ``u_empty`` are the reference voltages at which the
    capacity is read (volts; NaN without voltage terms in the solve, or without a rest
    of that kind). ``dof_beta`` is the degrees of freedom of the offset's band, by the
    same rule as ``dof`` on the offset's own scores.
    """

    __slots__ = (
        "status", "theta", "sigma", "low", "high", "chi2", "dof", "birge", "rho", "m", "m_cross", "m_same",
        "span_years", "terms", "p", "u_full", "u_empty", "dof_beta",
    )

    def __init__(self) -> None:
        self.status = SINGULAR
        self.theta = [math.nan] * P_MAX
        self.sigma = [math.nan] * P_MAX
        self.low = math.nan  # band of the main quantity: C at the date, or r for the fade fit
        self.high = math.nan
        self.chi2 = math.nan
        self.dof = 0.0
        self.birge = math.nan
        self.rho = math.nan
        self.m = 0.0
        self.m_cross = 0.0
        self.m_same = 0.0
        self.span_years = 0.0
        self.terms = 0
        self.p = 0
        self.u_full = math.nan
        self.u_empty = math.nan
        self.dof_beta = 0.0


def _position(state: Fit, r: int) -> int:
    """The ``Estimate`` position of regressor ``r`` of the state."""
    if r == 0:
        return I_C
    if state.trend != 0 and r == 1:
        return I_RATE
    if r == 1 + state.trend:
        return I_BETA
    if r == 2 + state.trend:
        return I_GF
    return I_GE


def _quad(pe: int, con: list, meat: list, size: list) -> float:
    """``con' meat con``; a negative value within rounding of the sums is zero."""
    acc = 0.0
    mag = 0.0
    for i in range(pe):
        for j in range(pe):
            acc += con[i] * meat[5 * i + j] * con[j]
            mag += abs(con[i] * con[j]) * size[5 * i + j]
    if acc < 0.0 and -acc <= ROUNDING * mag:
        acc = 0.0
    return acc


def _dof(state: Fit, pe: int, con: list, meat_s: list, meat_l: list, nu: float) -> float:
    """Degrees of freedom of the band of the contrast ``con`` (module docstring): ``m - p``,
    or with ``band_forgetting = 2`` the ratio fit's rule on the contrast's scores."""
    dof = nu
    if state.band == 2 and state.m2 > 0.0:
        e2 = 0.0
        lag2 = 0.0
        for i in range(pe):
            for j in range(pe):
                e2 += con[i] * meat_s[5 * i + j] * con[j]
                lag2 += con[i] * meat_l[5 * i + j] * con[j]
        rho = 0.0
        if state.m2_lag > 0.0 and e2 > 0.0:
            rho = (0.5 * lag2 / state.m2_lag) / (e2 / state.m2)
        q = state.m2_lag / state.m2
        qq = state.m2_ll / state.m2
        den = 1.0 + 2.0 * q + 8.0 * rho * q + 4.0 * rho * rho * (q + qq)
        if den > 0.0:
            dof = nu * (1.0 + 2.0 * rho * q) * (1.0 + 2.0 * rho * q) / den
    return dof


def solve(
    state: Fit,
    t_days: float,
    z: float,
    gain_error: float,
    min_cross: float,
    min_same: float,
    min_span_years: float,
    min_cross_trend: float = 0.0,
) -> Estimate:
    """The estimate at ``t_days`` (the state is decayed to it first).

    ``z`` is the two-sided normal quantile of the band (1.6449 for 90 %). The band of the
    main quantity is the Student-t quantile times its sandwich standard error (module
    docstring). A parameter other than the main one whose sandwich variance comes out
    negative gets ``sigma`` NaN. For the capacity fit, ``gain_error`` (the relative
    standard uncertainty of the current sensor's gain) is added to the band of C as a systematic term: it moves
    every pair the same way, so averaging does not reduce it. For the fade fit it is left
    out (it scales the rate by at most that fraction). ``min_span_years`` applies to the
    fade fit only. A voltage term whose kind of pair has a decayed count below
    ``min_same`` is left out of the solve, and so is the trend of the capacity fit while
    the decayed count of full-to-empty and empty-to-full pairs is below
    ``min_cross_trend``.

    The capacity fit's main quantity is the capacity at the date, read at the reference
    voltages at the date (``u_full``, ``u_empty``), with the gradient of the module
    docstring; without trend and voltage terms in the solve it is the parameter ``C``
    itself, computed as in release 0.1.0.

    Status, in this order: ``TOO_FEW_FULL_EMPTY``, ``TOO_FEW_SAME_KIND``; ``STALE``
    (capacity fit) or ``SHORT_SPAN`` (fade fit); ``WRONG_SIGN`` (with plausibility);
    ``SINGULAR``; ``NO_BAND``; ``OUT_OF_RANGE`` (capacity fit with plausibility: the
    capacity or the low end of its band at or below zero); else ``OK``.
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
    tau = state.tau_days
    if state.fade == 0 and tau > 0.0 and t_days - state.t_last_cross > tau:
        out.status = STALE
        return out
    if state.fade != 0 and out.span_years < min_span_years:
        out.status = SHORT_SPAN
        return out
    if state.plausibility != 0 and state.t_wrong > _NEVER and (tau <= 0.0 or t_days - state.t_wrong <= tau):
        out.status = WRONG_SIGN
        return out
    # The terms of this solve: a voltage term needs min_same pairs of its kind in memory,
    # the capacity fit's trend min_cross_trend full-to-empty and empty-to-full pairs.
    act = [0] * P_MAX
    pe = 0
    for r in range(p):
        use = 1
        if state.trend != 0 and state.fade == 0 and r == 1 and state.m_cross < min_cross_trend:
            use = 0
        if state.voltage != 0 and r == p - 2 and state.m_ff < min_same:
            use = 0
        if state.voltage != 0 and r == p - 1 and state.m_ee < min_same:
            use = 0
        if use != 0:
            act[pe] = r
            pe += 1
    out.p = pe
    i_beta = 0  # the offset's index among the terms used
    for i in range(pe):
        if state.trend != 0 and act[i] == 1:
            out.terms |= TERM_TREND
        if act[i] == 1 + state.trend:
            i_beta = i
        if state.voltage != 0 and act[i] == p - 2:
            out.terms |= TERM_FULL
        if state.voltage != 0 and act[i] == p - 1:
            out.terms |= TERM_EMPTY
    sub = [0.0] * 25
    for i in range(pe):
        for k in range(pe):
            sub[5 * i + k] = state.a[5 * act[i] + act[k]]
    inv = [0.0] * 25
    if _invert(pe, sub, inv) == 0:
        out.status = SINGULAR
        return out
    th = [0.0] * P_MAX
    for r in range(pe):
        acc = 0.0
        for k in range(pe):
            acc += inv[5 * r + k] * state.h[act[k]]
        th[r] = acc
    chi2 = state.c
    for r in range(pe):
        chi2 -= th[r] * state.h[act[r]]
    if chi2 < 0.0:
        chi2 = 0.0
    nu = state.m - pe
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
        for r in range(pe):
            cross -= th[r] * (state.ly[act[r]] + state.yl[act[r]])
            for k in range(pe):
                cross += th[r] * state.l[5 * act[r] + act[k]] * th[k]
        out.rho = (cross / state.m_lag) / (chi2 / state.m)
    # The sandwich's meat M_rs = phi' (S_rs + SL_rs) phi with phi = (-theta + c_weight e_0, 1);
    # a term left out has phi 0.
    phi = [0.0] * _NZ
    for r in range(pe):
        phi[act[r]] = -th[r]
    phi[0] += state.c_weight
    phi[p] = 1.0
    meat = [0.0] * 25
    size = [0.0] * 25  # the same sums in absolute values: the scale of their rounding error
    meat_s = [0.0] * 25  # the squares' part and the links' part, apart (band_forgetting = 2)
    meat_l = [0.0] * 25
    for r in range(pe):
        for s in range(pe):
            acc = 0.0
            mag = 0.0
            acc_s = 0.0
            acc_l = 0.0
            for i in range(p + 1):
                for j in range(p + 1):
                    idx = ((act[r] * 5 + act[s]) * 6 + i) * 6 + j
                    acc += phi[i] * (state.s4[idx] + state.s4l[idx]) * phi[j]
                    mag += abs(phi[i] * phi[j]) * (abs(state.s4[idx]) + abs(state.s4l[idx]))
                    if state.band == 2:
                        acc_s += phi[i] * state.s4[idx] * phi[j]
                        acc_l += phi[i] * state.s4l[idx] * phi[j]
            meat[5 * r + s] = acc
            size[5 * r + s] = mag
            meat_s[5 * r + s] = acc_s
            meat_l[5 * r + s] = acc_l
    scale = state.m / nu
    # The reference voltages at the date, as shifts from the first rest of each kind.
    d_f = 0.0
    d_e = 0.0
    if out.terms & TERM_FULL and state.wu_f > 0.0:
        d_f = state.su_f / state.wu_f
        out.u_full = state.r0_f + d_f
    if out.terms & TERM_EMPTY and state.wu_e > 0.0:
        d_e = state.su_e / state.wu_e
        out.u_empty = state.r0_e + d_e
    # The capacity at the date and at the reference voltages: the gradient over the terms
    # used (1 for C, y_t for the trend, 0 for the offset, d_f and -d_e for the voltage
    # terms). ``main`` is the index of the main parameter, -1 for this combination: the
    # capacity fit with trend or voltage terms in the solve.
    y_t = (t_days - state.t0_days) / 365.25
    grad = [0.0] * P_MAX  # the capacity's gradient (capacity fit), or c0's (fade fit)
    grad[0] = 1.0
    combo = 0
    for i in range(pe):
        if state.fade == 0 and state.trend != 0 and act[i] == 1:
            grad[i] = y_t
            combo = 1
        if state.voltage != 0 and act[i] == p - 2:
            grad[i] = d_f
            combo = 1
        if state.voltage != 0 and act[i] == p - 1:
            grad[i] = -d_e
            combo = 1
    main = 0
    if state.fade != 0:
        main = 1
    elif combo != 0:
        main = -1
    sig = [math.nan] * P_MAX
    for r in range(pe):
        acc = 0.0
        mag = 0.0
        for i in range(pe):
            for j in range(pe):
                acc += inv[5 * r + i] * meat[5 * i + j] * inv[5 * j + r]
                mag += abs(inv[5 * r + i] * inv[5 * j + r]) * size[5 * i + j]
        # A negative value within rounding of the sums is zero (a fit with no scatter).
        if acc < 0.0 and -acc <= ROUNDING * mag:
            acc = 0.0
        var = scale * acc
        if var >= 0.0:
            sig[r] = math.sqrt(var)
        elif r == main:
            out.status = NO_BAND
            return out
    # The contrasts: the gradient times the inverse, g' A^-1, of the main quantity and of
    # the capacity (the fade fit's c0 at the reference voltages).
    con = [0.0] * P_MAX
    con_c = [0.0] * P_MAX
    for j in range(pe):
        acc = 0.0
        for i in range(pe):
            acc += grad[i] * inv[5 * i + j]
        con_c[j] = acc
        if main >= 0:
            con[j] = inv[5 * main + j]
        else:
            con[j] = acc
    value = th[0]
    s_main = 0.0
    if main >= 0:
        value = th[main]
        s_main = sig[main]
    else:
        value = 0.0
        for i in range(pe):
            value += grad[i] * th[i]
        var = scale * _quad(pe, con, meat, size)
        if var < 0.0:
            out.status = NO_BAND
            return out
        s_main = math.sqrt(var)
    # Degrees of freedom (module docstring): of the main quantity's band, and of the
    # offset's on its own scores.
    dof = _dof(state, pe, con, meat_s, meat_l, nu)
    con_b = [0.0] * P_MAX
    for j in range(pe):
        con_b[j] = inv[5 * i_beta + j]
    out.dof = dof
    out.dof_beta = _dof(state, pe, con_b, meat_s, meat_l, nu)
    for r in range(pe):
        out.theta[_position(state, act[r])] = th[r]
        out.sigma[_position(state, act[r])] = sig[r]
    if state.fade != 0 and combo != 0:
        c_ref = 0.0
        for i in range(pe):
            c_ref += grad[i] * th[i]
        out.theta[I_C] = c_ref
        var = scale * _quad(pe, con_c, meat, size)
        out.sigma[I_C] = math.nan
        if var >= 0.0:
            out.sigma[I_C] = math.sqrt(var)
    k = t_quantile(z, dof)
    sys_part = 0.0
    if state.fade == 0:
        sys_part = gain_error * value
    half = math.sqrt(k * k * s_main * s_main + z * z * sys_part * sys_part)
    if state.fade == 0:
        out.theta[I_C] = value
        out.sigma[I_C] = math.sqrt(s_main * s_main + sys_part * sys_part)
    out.low = value - half
    out.high = value + half
    if state.fade == 0 and state.plausibility != 0 and (value <= 0.0 or out.low <= 0.0):
        out.status = OUT_OF_RANGE
        return out
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
    inv = [0.0] * 25  # row-major in 5 x 5, as _invert writes it
    if p == 1:
        if not (state.a[0] > 0.0):
            out.status = SINGULAR
            return out
        inv[0] = 1.0 / state.a[0]
    else:
        mat = [0.0] * 25
        mat[0] = state.a[0]
        mat[1] = state.a[1]
        mat[5] = state.a[2]
        mat[6] = state.a[3]
        if _invert(2, mat, inv) == 0:
            out.status = SINGULAR
            return out
    th = [0.0, 0.0]
    for r in range(p):
        acc = 0.0
        for k in range(p):
            acc += inv[5 * r + k] * state.h[k]
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
                    acc += inv[5 * r + i] * meat[2 * i + j] * inv[5 * j + s]
                    mag += abs(inv[5 * r + i] * inv[5 * j + s]) * size[2 * i + j]
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
