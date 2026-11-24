"""besslib.sim: a battery pack simulator with a known truth, the test bench of besslib.soh.

An equivalent-circuit pack of cell groups, with a thermal model, a BMS, sensors and a
duty, runs a *case* (a TOML file) and gives, month by month, the frames a meter at the
pack terminals would report (``besslib.frames`` format) and the truth behind them::

    from besslib import sim
    for month in sim.run("synth", months=3, seed=1):
        month.frame        # what besslib.soh reads
        month.truth.stops  # every BMS stop, with the true state of charge

``besslib.sim`` imports ``besslib.frames`` and nothing from ``besslib.soh``.
It is a test bench, not a model of any real product.
"""

from .run import MonthOut, build, case_names, load_case, run, spec

__all__ = ["MonthOut", "build", "case_names", "load_case", "run", "spec"]
