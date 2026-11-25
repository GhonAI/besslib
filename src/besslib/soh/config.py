"""The settings of the estimators, in frozen records with a printable hash.

``CapacityConfig`` holds the settings of the capacity estimator, ``RecordConfig`` those
of the other fields of the Annex VII record. The defaults are the values frozen after
tuning on the development systems. Every result besslib writes carries
``config_hash(config)``, so a number can always be traced to the exact settings that
made it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace


#: The forms of ``CapacityConfig.voltage_terms``.
VOLTAGE_TERMS = ("every_pair_quadratic", "every_pair", "none")

#: The forms of ``CapacityConfig.rest_time``.
REST_TIMES = ("reading", "start")


@dataclass(frozen=True)
class CapacityConfig:
    """Settings for rest detection, pairing and the estimator.

    Rest detection (Figgener et al. 2024, Supplementary Methods, "Relaxation phase
    detection"): a rest is a stretch with ``|current| < current_c_rate * C`` that lasts
    at least ``min_duration_min``; current excursions up to ``peak_s`` seconds are
    tolerated, longer ones end the rest; at least ``min_throughput`` x C of charge must
    have moved since the last accepted rest.

    Rest kinds: the *edge voltage* of a rest is the median voltage in the
    ``pre_window_s`` seconds before it, while current still flows. ``full`` is a rest
    after a charge whose edge voltage lies within ``edge_tolerance`` of the voltage span
    below the system's end-of-charge level; ``empty`` likewise above the
    end-of-discharge level. The levels are the ``edge_quantile`` and
    ``1 - edge_quantile`` quantiles of the edge voltages in the trailing
    ``level_window_days``, from at least ``min_level_rests`` rests of each direction.

    The end-of-charge and end-of-discharge levels are properties of the system, not of
    the season. A home storage system may not reach one of them for months: in summer it
    is rarely emptied, in winter rarely filled. A shorter window would then move its
    level into the normal operating range and call a half-empty stop empty. So the
    window spans one operating year, and no level exists, and no rest is full or empty,
    before ``level_warmup_days`` of history after the first accepted rest of the record.

    Pairs: two consecutive full or empty rests form a pair while they are at most
    ``max_link_days`` apart and have at most ``max_missing_s`` seconds of missing data
    between them; a masked interval between them also breaks the link.

    Estimator: memory time ``tau_days``; band level ``level``; ``sigma_q_rel`` x charge
    moved is the charge error of a pair and ``sigma_soc_edge`` the state-of-charge error
    of one rest (both set the relative weights only; the band is taken from the scatter
    of the pairs themselves); ``gain_error`` is the current sensor's systematic error
    (0.5 %, Figgener et al. 2024, Supplementary Table 2). With ``gain_rectangular`` (version
    0.2) it is read as the half-width ``a`` of a rectangular distribution, so the capacity's
    band adds ``a / sqrt(3)`` as its standard uncertainty (JCGM 100:2008, 4.3.7, Eq. 7), as
    ``RecordConfig`` does for the other fields; release 0.1.0 added ``a`` itself. An estimate needs at least ``min_cross``
    full-to-empty or empty-to-full pairs and ``min_same`` full-to-full or empty-to-empty
    pairs in memory (decayed counts). The fade rate needs ``fade_min_span_years``.

    Version 0.2 adds six settings; each defaults to the 0.2 behaviour, and ``v0_1()``
    gives the settings of release 0.1.0. ``voltage_terms``: ``"every_pair"`` gives every
    pair two terms in the relaxed open-circuit voltage of its rests, each end its own
    kind's voltage minus a reference, because two stops of one kind are not at the same
    state of charge; the capacity is then read at the forgetting-weighted mean rest
    voltage of each kind at the date (``besslib.soh._core``). ``"none"``: release 0.1.0.
    ``local_trend``: the fit with forgetting carries a linear trend in the capacity and
    reads the value at the date, so a fading capacity does not lag; ``trend_min_cross``
    is the decayed count of full-to-empty and empty-to-full pairs it needs in memory
    (below it the solve leaves the trend out; frozen: ``validation/tune_trend.py``).
    ``band_forgetting``: how often the forgetting weight enters the band's sums (2: as
    the weight of a weighted least-squares fit; 1: release 0.1.0). ``plausibility``: an
    estimate whose levels have crossed, whose value or band reaches zero, or whose memory
    holds a full-to-empty or empty-to-full pair of the wrong sign is ``not possible``.

    Version 0.3 adds six settings and one form; each defaults to the 0.3 behaviour, and
    ``v0_2()`` gives the settings of release 0.2.0. ``voltage_terms =
    "every_pair_quadratic"`` adds a quadratic term at each end (``besslib.soh._core``), so
    the reading at the mean rest voltage follows the curve of charge over voltage; each is
    used while its kind has at least ``quad_min_rests`` rests in memory (decayed count).
    ``rest_time``: ``"reading"`` takes the time of a pair between the moments its rest
    voltages are read (``min(duration, ocv_fit_max_min)`` after the start of each rest),
    ``"start"`` between the starts of its rests (release 0.2.0). Two rules say when full
    and empty are not the pack's: with at least ``above_full_min_rests`` accepted rests
    after a charge, in the trailing ``level_window_days``, whose edge voltage lies more
    than ``above_full_share`` of the span between the levels above the full level, the
    full level is not the pack's full (0 rests: the rule is off). With
    ``PackSpec.voltage_max_v`` and ``voltage_min_v`` given, a full level more than
    ``limits_share`` of the documented window below the maximum, or an empty level more
    than that above the minimum, is not possible either (0: the rule is off).
    """

    current_c_rate: float = 0.005  # frozen: validation/tune_capacity.py
    min_duration_min: float = 20.0  # frozen: validation/tune_capacity.py
    peak_s: float = 10.0
    min_throughput: float = 0.2
    pre_window_s: float = 60.0
    break_gap_s: float = 60.0
    edge_quantile: float = 0.9
    edge_tolerance: float = 0.03  # validation/edge_tolerance.py
    level_window_days: float = 365.0  # one operating year: every season once
    level_warmup_days: float = 365.0  # no level before one year of history
    min_level_rests: int = 10
    ocv_fit_max_min: float = 120.0
    max_link_days: float = 10.0
    max_missing_s: float = 600.0
    tau_days: float = 180.0  # frozen: validation/tune_capacity.py
    level: float = 0.90
    sigma_q_rel: float = 0.005
    sigma_soc_edge: float = 0.02
    gain_error: float = 0.005
    min_cross: float = 3.0
    min_same: float = 2.0
    fade_min_span_years: float = 1.0
    voltage_terms: str = "every_pair_quadratic"  # version 0.3; 0.2: "every_pair", 0.1: "none"
    local_trend: bool = True  # version 0.2
    band_forgetting: int = 2  # version 0.2
    plausibility: bool = True  # version 0.2
    trend_min_cross: float = 0.0  # version 0.2; frozen: validation/tune_trend.py (development systems)
    gain_rectangular: bool = True  # version 0.2: gain_error / sqrt(3) in the capacity's band
    quad_min_rests: float = 2.0  # version 0.3; frozen: validation/tune_quadratic.py (development systems)
    rest_time: str = "reading"  # version 0.3
    above_full_min_rests: int = 5  # version 0.3
    above_full_share: float = 0.10  # version 0.3
    limits_share: float = 0.25  # version 0.3; chosen on the simulator's development packs

    def __post_init__(self) -> None:
        if self.voltage_terms not in VOLTAGE_TERMS:
            raise ValueError(f"voltage_terms must be one of {VOLTAGE_TERMS}, not {self.voltage_terms!r}")
        if self.rest_time not in REST_TIMES:
            raise ValueError(f"rest_time must be one of {REST_TIMES}, not {self.rest_time!r}")

    def with_(self, **changes) -> "CapacityConfig":
        """A copy with some settings changed."""
        return replace(self, **changes)

    def v0_1(self) -> "CapacityConfig":
        """A copy with the version 0.2 and 0.3 settings at their release 0.1.0 values."""
        return replace(self, **{**_V0_2["CapacityConfig"], **_V0_1["CapacityConfig"]})

    def v0_2(self) -> "CapacityConfig":
        """A copy with the version 0.3 settings at their release 0.2.0 values."""
        return replace(self, **_V0_2["CapacityConfig"])


@dataclass(frozen=True)
class RecordConfig:
    """Settings for resistance, round-trip efficiency and the Annex VII record.

    They are kept apart from ``CapacityConfig``, so the capacity estimator's settings
    and hash do not change when these do.

    Resistance: a *current step* is a change of at least ``min_step_c_rate`` x C between
    two consecutive samples, after ``steady_s`` seconds in which the current stayed
    within ``steady_tolerance`` x the step of its value before the step. Steps are
    grouped by day and by pack-temperature band of ``temp_band_c`` degrees. A day in a
    band is left out when more than ``unrefreshed_max_share`` of its steps show no voltage
    change at all: the voltage was then not refreshed together with the current (a
    current step moves the voltage of a pack by far more than the voltage sensor's
    resolution, so a synchronous logger shows almost no exact zeros). An estimate
    needs ``resistance_min_days`` days in memory in its band (decayed count); the growth
    per year needs ``growth_min_span_years`` between its first and last day.

    Round-trip efficiency: an estimate needs ``efficiency_min_loops`` closed loops in
    memory (decayed count).

    Shared: memory time ``tau_days`` (the frozen value of the capacity estimator, not
    tuned again); band level ``level``. ``current_accuracy`` and ``voltage_accuracy``
    are the sensors' stated accuracies (0.5 % and 0.25 %, Figgener et al. 2024,
    Supplementary Table 2). Each accuracy is read as the half-width ``a`` of a
    rectangular distribution, so its standard uncertainty is ``a / sqrt(3)`` (JCGM
    100:2008, "Guide to the expression of uncertainty in measurement", 4.3.7, Eq. 7).
    A gain error moves every reading of a sensor the same way, so averaging does not
    reduce it: it enters a band as a systematic term. The trend of the apparent charge
    loss needs ``charge_loss_min_years`` year-end values.

    Version 0.2 adds ``a2_needs_a5``: the remaining power capability (A2) is
    ``estimated`` only when the resistance (A5) is ``estimated`` at the same date.
    ``v0_1()`` gives the settings of release 0.1.0.
    """

    min_step_c_rate: float = 0.05  # frozen after validation/tune_resistance.py (development systems only)
    steady_s: float = 2.0  # frozen after validation/tune_resistance.py (development systems only)
    steady_tolerance: float = 0.2
    unrefreshed_max_share: float = 0.01
    temp_band_c: float = 5.0
    resistance_min_days: float = 10.0
    growth_min_span_years: float = 1.0
    efficiency_min_loops: float = 5.0
    tau_days: float = 180.0
    level: float = 0.90
    current_accuracy: float = 0.005
    voltage_accuracy: float = 0.0025
    charge_loss_min_years: int = 3
    a2_needs_a5: bool = True  # version 0.2

    def with_(self, **changes) -> "RecordConfig":
        """A copy with some settings changed."""
        return replace(self, **changes)

    def v0_1(self) -> "RecordConfig":
        """A copy with the version 0.2 settings at their release 0.1.0 values."""
        return replace(self, **_V0_1["RecordConfig"])

    def v0_2(self) -> "RecordConfig":
        """A copy with the settings of release 0.2.0 (version 0.3 adds none here)."""
        return replace(self, **_V0_2["RecordConfig"])

    @property
    def charge_sigma(self) -> float:
        """Relative standard uncertainty of a charge (Ah) from the current sensor's gain."""
        return self.current_accuracy / math.sqrt(3.0)

    @property
    def energy_sigma(self) -> float:
        """Relative standard uncertainty of an energy (Wh): current and voltage gain combined."""
        return math.hypot(self.current_accuracy, self.voltage_accuracy) / math.sqrt(3.0)

    @property
    def resistance_sigma(self) -> float:
        """Relative standard uncertainty of a voltage step divided by a current step."""
        return math.hypot(self.current_accuracy, self.voltage_accuracy) / math.sqrt(3.0)


#: The settings added in version 0.2, at their release 0.1.0 values.
_V0_1 = {
    "CapacityConfig": {
        "voltage_terms": "none", "local_trend": False, "band_forgetting": 1, "plausibility": False,
        "trend_min_cross": 0.0, "gain_rectangular": False,
    },
    "RecordConfig": {"a2_needs_a5": False},
}

#: The settings added or given a new form in version 0.3, at their release 0.2.0 values.
_V0_2 = {
    "CapacityConfig": {
        "voltage_terms": "every_pair", "quad_min_rests": 0.0, "rest_time": "start", "above_full_min_rests": 0,
        "above_full_share": 0.0, "limits_share": 0.0,
    },
    "RecordConfig": {},
}


def config_hash(config: CapacityConfig | RecordConfig) -> str:
    """The first 12 hex digits of the SHA-256 of the settings, as sorted JSON.

    A setting added in version 0.2 is left out while it holds its release 0.1.0 value, and
    one added in 0.3 while it holds its release 0.2.0 value, so the settings of a release
    keep that release's hash.
    """
    settings = asdict(config)
    name = type(config).__name__
    added = {k: v for k, v in _V0_2[name].items() if k not in _V0_1[name]}
    for key, old in {**_V0_1[name], **added}.items():
        if settings[key] == old:
            del settings[key]
    text = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]
