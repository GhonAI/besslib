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
    (0.5 %, Figgener et al. 2024, Supplementary Table 2). An estimate needs at least ``min_cross``
    full-to-empty or empty-to-full pairs and ``min_same`` full-to-full or empty-to-empty
    pairs in memory (decayed counts). The fade rate needs ``fade_min_span_years``.
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

    def with_(self, **changes) -> "CapacityConfig":
        """A copy with some settings changed."""
        return replace(self, **changes)


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

    def with_(self, **changes) -> "RecordConfig":
        """A copy with some settings changed."""
        return replace(self, **changes)

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


def config_hash(config: CapacityConfig | RecordConfig) -> str:
    """The first 12 hex digits of the SHA-256 of the settings, as sorted JSON."""
    text = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]
