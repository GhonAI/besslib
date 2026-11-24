"""Cases and runs: a case file in, frames and truth out.

A case is a TOML file (``besslib/sim/cases/*.toml``) with the sections ``[case]``,
``[cell]``, ``[pack]``, ``[thermal]``, ``[temperature.pack]``, ``[temperature.ambient]``,
``[bms]``, ``[duty]`` and ``[sensors]``; ``[cell] preset = "<name>"`` takes a cell from
``besslib/sim/presets/<name>.toml``, and the case's own ``[cell]`` values replace the
preset's. Settings can be changed at the call (``overrides={"bms.stop_sigma_v": 0.0}``).

Random numbers: each pack has one seed. Its main stream gives, in this order, the duty's
plan and the BMS's daily limit errors, day by day; then the sensors' constants; then the
sensors' noise, month by month (``sensors.py``). A second stream (``[seed, 1]``) gives the
sensors' other noise; a third (``[seed, 2]``) the spreads between cell groups; a fourth
(``[seed, 3]``) the sensors' gaps. So the spreads and the sensors never change the
operation. Duty overlays (``duty.py``) draw no random numbers.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from besslib.frames import PackSpec

from .bms import BmsSettings
from .cell import CellModel
from .duty import make_duty
from .pack import Engine, Month, PackModel
from .sensors import SensorSettings, Sensors, apply_gaps, frame
from .thermal import Profile, ThermalModel


def _read_toml(name: str, folder: str) -> dict:
    path = Path(name)
    if path.suffix == ".toml" and path.exists():
        return tomllib.loads(path.read_text())
    text = resources.files("besslib.sim").joinpath(folder, f"{name}.toml").read_text()
    return tomllib.loads(text)


def case_names() -> list[str]:
    folder = resources.files("besslib.sim").joinpath("cases")
    return sorted(p.name[:-5] for p in folder.iterdir() if p.name.endswith(".toml"))


def load_case(case, overrides: dict | None = None) -> dict:
    """A case as a dictionary: a name (``"synth"``), a path to a TOML file, or a dictionary."""
    data = copy.deepcopy(case) if isinstance(case, dict) else _read_toml(str(case), "cases")
    for key, value in (overrides or {}).items():
        node = data
        *path, last = key.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = value
    return data


def _only(cls, settings: dict, section: str) -> dict:
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ValueError(f"[{section}] does not know: {', '.join(unknown)}")
    return settings


def _tuples(d: dict, *names) -> dict:
    out = dict(d)
    for n in names:
        if n in out:
            out[n] = tuple(tuple(x) if isinstance(x, list) else x for x in out[n])
    return out


def cell_model(data: dict) -> CellModel:
    cell = dict(data.get("cell", {}))
    preset = cell.pop("preset", None)
    base = {}
    if preset:
        base = _read_toml(preset, "presets")
        base.pop("source", None)
        base.pop("notes", None)
    base.update(cell)
    base["ocv_soc"] = tuple(base["ocv_soc"])
    base["ocv_v"] = tuple(base["ocv_v"])
    return CellModel(**_only(CellModel, base, "cell"))


def build(data: dict, seed: int) -> tuple[PackModel, dict]:
    """The pack of one seed, and the case's run settings."""
    run = dict(data.get("case", {}))
    pk = dict(data.get("pack", {}))
    rng = np.random.default_rng([seed, 2])
    groups = int(pk.pop("groups", 1))
    sd_cap = float(pk.pop("capacity_spread_sd", 0.0))
    sd_r = float(pk.pop("resistance_spread_sd", 0.0))
    if sd_cap > 0 and "capacity_factor" not in pk:
        pk["capacity_factor"] = tuple(1.0 + sd_cap * rng.standard_normal(groups))
    if sd_r > 0 and "resistance_factor" not in pk:
        pk["resistance_factor"] = tuple(1.0 + sd_r * rng.standard_normal(groups))
    for name in ("capacity_factor", "resistance_factor", "soc0", "fade_points_per_year_groups",
                 "self_discharge_a", "leak_ohm", "leak_from_s", "fan_points"):
        if name in pk:
            v = pk[name]
            pk[name] = tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v
    temp = data.get("temperature", {})
    thermal = ThermalModel(**_only(ThermalModel, dict(data.get("thermal", {})), "thermal"))
    bms = BmsSettings(**_only(BmsSettings, _tuples(data.get("bms", {}), "v_min_points", "v_max_points", "derate"), "bms"))
    pack = PackModel(
        cell=cell_model(data),
        groups=groups,
        thermal=thermal,
        pack_profile=Profile(**_tuples(temp.get("pack", {}), "planted")),
        ambient=Profile(**_tuples(temp.get("ambient", {"mean_c": 20.0}), "planted")),
        bms=bms,
        **_only(PackModel, pk, "pack"),
    )
    return pack, run


def spec(data: dict, pack: PackModel) -> PackSpec:
    """What a user of the frames would know from the pack's documents."""
    run = data.get("case", {})
    nominal_v = float(run.get("nominal_cell_v", 3.7))
    return PackSpec(
        nominal_capacity_ah=pack.nominal_ah,
        nominal_energy_kwh=pack.nominal_ah * nominal_v * pack.cells_in_series / 1000.0,
        cells_in_series=pack.cells_in_series,
        cells_in_parallel=pack.parallel,
        chemistry=str(run.get("chemistry", pack.cell.name)),
        name=str(run.get("pack_name", f"besslib.sim {run.get('name', '')}".strip())),
    )


@dataclass
class MonthOut:
    frame: pd.DataFrame  # what the meter reports (besslib frame)
    true_frame: pd.DataFrame  # the same operation, true signals
    truth: Month


def run(case, months: int | None = None, seed: int = 0, overrides: dict | None = None,
        measured: bool = True, log_blocks: bool = True):
    """Run a case: yield one ``MonthOut`` per month.

    ``measured=False`` skips the sensors' noise (``frame`` is then the true signals too) and
    draws none of its random numbers."""
    data = load_case(case, overrides)
    pack, settings = build(data, seed)
    months = int(months if months is not None else settings.get("months", 12))
    start = pd.Timestamp(settings.get("start", "2020-01-01"))
    step_s = float(settings.get("step_s", 10.0))
    engine = Engine(pack, step_s=step_s, update_s=settings.get("update_s") or None,
                    cell_every_s=float(settings.get("cell_every_s", 600.0)), log_blocks=log_blocks)
    duty = make_duty(dict(data.get("duty", {})), start, step_s)
    rng = np.random.default_rng(seed)
    days = int(((start + pd.DateOffset(months=months)) - start).days)
    pieces, limits = [], np.zeros((days, 2))
    for d in range(days):
        pieces.extend(duty.day(rng, d))
        limits[d] = pack.bms.draw_day(rng)
    sensor_settings = SensorSettings(**_only(SensorSettings, dict(data.get("sensors", {})), "sensors"))
    sensors = Sensors(sensor_settings, rng, np.random.default_rng([seed, 1]))
    rng_gaps = np.random.default_rng([seed, 3])
    every = float(settings.get("sample_s", step_s)) / step_s
    if every < 1 or every != int(every):
        raise ValueError("sample_s must be a whole multiple of step_s")
    every = int(every)
    for month in engine.run(pieces, limits, start, months):
        sec, volt, amp = month.seconds[::every], month.voltage_v[::every], month.current_a[::every]
        tpack, tamb = month.temp_sensor_c[::every], month.temp_ambient_c[::every]
        true = frame(start, sec, volt, amp, tpack, tamb)
        if measured:
            v, i, t = sensors.measure(volt, amp, tpack)
            meas = frame(start, sec, v, i, t, tamb)
            if sensor_settings.gaps_per_month > 0:
                meas = apply_gaps(meas, sensor_settings, rng_gaps, float(settings.get("sample_s", step_s)))
        else:
            meas = true
        yield MonthOut(meas, true, month)
