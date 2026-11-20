"""besslib.soh: state-of-health figures from operating data."""

from besslib.soh.capacity import estimate_capacity, fade_rate
from besslib.soh.capacity_test import CapacityTest, measure_capacity_test
from besslib.soh.charge_loss import charge_loss, charge_loss_trend
from besslib.soh.config import CapacityConfig, RecordConfig, config_hash
from besslib.soh.efficiency import efficiency_loops, estimate_efficiency
from besslib.soh.record import ANNEX_VII_FIELDS, AnnexVII, annex_vii
from besslib.soh.reduce import Reduced, read_reduced, reduce_frame, reduce_parts, write_reduced
from besslib.soh.report import report
from besslib.soh.resistance import estimate_resistance, resistance_growth
from besslib.soh.rests import find_rests
from besslib.soh.usage import Limits, Usage, count_usage, usage_by_year

__all__ = [
    "ANNEX_VII_FIELDS",
    "AnnexVII",
    "CapacityConfig",
    "CapacityTest",
    "Limits",
    "RecordConfig",
    "Reduced",
    "Usage",
    "annex_vii",
    "charge_loss",
    "charge_loss_trend",
    "config_hash",
    "count_usage",
    "efficiency_loops",
    "estimate_capacity",
    "estimate_efficiency",
    "estimate_resistance",
    "fade_rate",
    "find_rests",
    "measure_capacity_test",
    "read_reduced",
    "reduce_frame",
    "reduce_parts",
    "report",
    "resistance_growth",
    "usage_by_year",
    "write_reduced",
]
