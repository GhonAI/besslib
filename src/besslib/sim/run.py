"""Cases and runs: a case file in, frames and truth out.

A case is a TOML file (``besslib/sim/cases/*.toml``) with the sections ``[case]``,
``[cell]``, ``[pack]``, ``[thermal]``, ``[temperature.pack]``, ``[temperature.ambient]``,
``[bms]``, ``[duty]`` and ``[sensors]``; ``[cell] preset = "<name>"`` takes a cell from
``besslib/sim/presets/<name>.toml``, and the case's own ``[cell]`` values replace the
preset's. Settings can be changed at the call (``overrides={"bms.stop_sigma_v": 0.0}``).

Random numbers: each pack has one seed. Its main stream gives, in this order, the duty's
plan and the BMS's daily limit errors, day by day; then the sensors' constants; then the
sensors' noise, month by month (``sensors.py``). A second stream (``[seed, 1]``) gives the
sensors' other noise; a third (``[seed, 2]``) the spreads between cell groups (capacity,
resistance, then self-discharge); a fourth (``[seed, 3]``) the sensors' gaps; a fifth
(``[seed, 4]``) the events; a sixth (``[seed, 5]``) a drawn temperature offset. So the spreads,
the events and the sensors never change the operation. Duty overlays (``duty.py``) draw no
random numbers.

Events (``[[event]]`` in a case file) plant one fault on a random day, drawn per seed, in file
order, each always drawing the same count of numbers: ``fan_failure`` (the fan's factor falls),
``leak`` (a leak starts in one group; ``leak_a = 0`` draws the same numbers and plants none),
``stop_steps`` (the discharge stop falls in equal steps). ``events(case, seed)`` lists them.
"""

from __future__ import annotations

import copy
import math
import tomllib
from dataclasses import dataclass, field, fields, replace
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


def _nested(x):
    """Lists (also nested ones) as tuples."""
    return tuple(_nested(v) for v in x) if isinstance(x, (list, tuple)) else x


def _scaled(x, k: float):
    return tuple(_scaled(v, k) for v in x) if isinstance(x, (list, tuple)) else x / k


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
    base.update(cell)
    for note in ("source", "notes", "credit"):
        base.pop(note, None)
    to_ah = base.pop("scale_to_ah", None)
    if to_ah is not None:  # as if cells were in parallel: capacity times k, resistances divided by k
        k = float(to_ah) / float(base["capacity_ah"])
        base["capacity_ah"] = float(to_ah)
        for name in ("r0_ohm", "r1_ohm", "r2_ohm", "r0_table", "r1_table"):
            if name in base:
                base[name] = _scaled(base[name], k)
    base = {key: _nested(value) for key, value in base.items()}
    return CellModel(**_only(CellModel, base, "cell"))


EVENT_KINDS = ("fan_failure", "leak", "stop_steps")
LEAK_CELL_V = 3.7  # the cell voltage at which a leak's current is stated


def _month_days(start: pd.Timestamp, month: int) -> int:
    """Days from the run's start to the first day of month ``month`` (1-based; month 1 starts at 0)."""
    return int(((start + pd.DateOffset(months=month - 1)) - start).days)


def _day_range(event: dict, start: pd.Timestamp) -> tuple[int, int]:
    lo, hi = int(event["from_month"]), int(event["to_month"])
    if not 1 <= lo <= hi:
        raise ValueError(f"event {event.get('kind')}: from_month and to_month are 1-based, from <= to")
    return _month_days(start, lo), _month_days(start, hi + 1)


def events(case, seed: int) -> list[dict]:
    """The case's ``[[event]]`` entries as drawn for ``seed``: each with its ``kind``, the settings
    and what was drawn (``day`` counted from the run's start, ``group``, ``leak_ohm``, ``steps``)."""
    data = case if isinstance(case, dict) else load_case(case)
    return _draw_events(data, seed)[0]


def _draw_events(data: dict, seed: int) -> tuple[list[dict], dict, dict]:
    """The drawn events, and the changes they make to the pack (``pack`` keywords) and the BMS (``bms``)."""
    start = pd.Timestamp(data.get("case", {}).get("start", "2020-01-01"))
    pk = data.get("pack", {})
    groups, series = int(pk.get("groups", 1)), int(pk.get("series", 1))
    rng = np.random.default_rng([seed, 4])
    out, pack, bms = [], {}, {}
    leak_ohm = {}
    leak_from = {}
    for raw in data.get("event", []):
        e = dict(raw)
        kind = e.get("kind")
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event {kind!r}; known: {', '.join(EVENT_KINDS)}")
        lo, hi = _day_range(e, start)
        if kind == "fan_failure":
            e["day"] = int(rng.integers(lo, hi))
            if "fan_points" in pack:
                raise ValueError("two fan_failure events")
            pack["fan_points"] = ((e["day"] * 86400.0, float(e["factor"])),)
        elif kind == "leak":
            e["day"] = int(rng.integers(lo, hi))
            e["group"] = int(rng.integers(0, groups))
            amps = float(e["leak_a"])
            e["leak_ohm"] = LEAK_CELL_V * series / amps if amps > 0 else None
            if amps > 0:
                leak_ohm[e["group"]] = e["leak_ohm"]
                leak_from[e["group"]] = e["day"] * 86400.0
        else:
            n = int(e["steps"])
            days = np.sort(rng.integers(lo, hi, n))
            e["days"] = [int(d) for d in days]
            if "v_min_points" in bms:
                raise ValueError("two stop_steps events")
            points, t_prev = [(0.0, 0.0)], 0.0
            for k, d in enumerate(days, start=1):
                t = max(float(d) * 86400.0, t_prev + 2.0)  # the steps stay apart, one second each
                points += [(t / 86400.0, -float(e["total_v"]) * (k - 1) / n),
                           ((t + 1.0) / 86400.0, -float(e["total_v"]) * k / n)]
                t_prev = t
            bms["v_min_points"] = tuple(points)
        out.append(e)
    if leak_ohm:
        pack["leak_ohm"] = tuple(leak_ohm.get(j, math.inf) for j in range(groups))
        pack["leak_from_s"] = tuple(leak_from.get(j, 0.0) for j in range(groups))
    return out, pack, bms


def _build(data: dict, seed: int) -> tuple[PackModel, dict, list[dict]]:
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
    mean_a = float(pk.pop("self_discharge_mean_a", 0.0))
    sd_a = float(pk.pop("self_discharge_sd_a", 0.0))
    if "self_discharge_a" not in pk and (mean_a > 0 or sd_a > 0):
        draws = mean_a + sd_a * rng.standard_normal(groups) if sd_a > 0 else np.full(groups, mean_a)
        pk["self_discharge_a"] = tuple(np.maximum(draws, 0.0))
    drawn, pack_set, bms_set = _draw_events(data, seed)
    for name in ("capacity_factor", "resistance_factor", "soc0", "fade_points_per_year_groups",
                 "self_discharge_a", "leak_ohm", "leak_from_s", "fan_points"):
        if name in pk:
            pk[name] = _nested(pk[name])
    for name, value in pack_set.items():
        if pk.get(name):
            raise ValueError(f"an event sets pack.{name}, which the case sets too")
        pk[name] = value
    temp = data.get("temperature", {})
    thermal = ThermalModel(**_only(ThermalModel, dict(data.get("thermal", {})), "thermal"))
    bms_data = _tuples(data.get("bms", {}), "v_min_points", "v_max_points", "derate")
    for name, value in bms_set.items():
        if bms_data.get(name):
            raise ValueError(f"an event sets bms.{name}, which the case sets too")
        bms_data[name] = value
    bms = BmsSettings(**_only(BmsSettings, bms_data, "bms"))
    pack = PackModel(
        cell=cell_model(data),
        groups=groups,
        thermal=thermal,
        pack_profile=Profile(**_tuples(temp.get("pack", {}), "planted")),
        ambient=Profile(**_tuples(temp.get("ambient", {"mean_c": 20.0}), "planted")),
        bms=bms,
        **_only(PackModel, pk, "pack"),
    )
    return pack, run, drawn


def build(data: dict, seed: int) -> tuple[PackModel, dict]:
    """The pack of one seed (its events applied), and the case's run settings."""
    pack, run, _ = _build(data, seed)
    return pack, run


def spec(data: dict, pack: PackModel) -> PackSpec:
    """What a user of the frames would know from the pack's documents: among it the BMS's
    voltage limits, without a planted drift of them."""
    run = data.get("case", {})
    nominal_v = float(run.get("nominal_cell_v", 3.7))
    return PackSpec(
        nominal_capacity_ah=pack.nominal_ah,
        nominal_energy_kwh=pack.nominal_ah * nominal_v * pack.cells_in_series / 1000.0,
        cells_in_series=pack.cells_in_series,
        cells_in_parallel=pack.parallel,
        chemistry=str(run.get("chemistry", pack.cell.name)),
        voltage_max_v=pack.bms.v_cell_max * pack.cells_in_series,
        voltage_min_v=pack.bms.v_cell_min * pack.cells_in_series,
        name=str(run.get("pack_name", f"besslib.sim {run.get('name', '')}".strip())),
    )


@dataclass
class MonthOut:
    frame: pd.DataFrame  # what the meter reports (besslib frame)
    true_frame: pd.DataFrame  # the same operation, true signals
    truth: Month
    voltage_gain: float = 0.0  # the pack's drawn gain errors (0.0 when ``measured=False``)
    current_gain: float = 0.0
    temp_offset_c: float = 0.0  # the pack's drawn temperature offset (``sensors.temp_offset_max_c``)
    events: list = field(default_factory=list)  # the case's events as drawn (``events``)


@dataclass
class _Prepared:
    """What a run needs before its first step, batch or live."""

    pack: PackModel
    settings: dict
    drawn: list
    months: int
    start: pd.Timestamp
    step_s: float
    engine: Engine
    pieces: list
    limits: np.ndarray  # per day, the BMS's random errors (upper, lower)
    rng: np.random.Generator  # the main stream, after the duty's plan and the limits


def _prepare(data: dict, months: int | None, seed: int, log_blocks: bool = True) -> _Prepared:
    """The pack, the engine, the duty's plan and the daily limit errors, drawn in the fixed order."""
    pack, settings, drawn = _build(data, seed)
    months = int(months if months is not None else settings.get("months", 12))
    start = pd.Timestamp(settings.get("start", "2020-01-01"))
    step_s = float(settings.get("step_s", 10.0))
    engine = Engine(pack, step_s=step_s, update_s=settings.get("update_s") or None,
                    cell_every_s=float(settings.get("cell_every_s", 600.0)), log_blocks=log_blocks)
    duty_settings = dict(data.get("duty", {}))
    if duty_settings.get("kind") == "grid" and "nominal_energy_kwh" not in duty_settings:
        duty_settings["nominal_energy_kwh"] = spec(data, pack).nominal_energy_kwh
    duty = make_duty(duty_settings, start, step_s)
    rng = np.random.default_rng(seed)
    days = int(((start + pd.DateOffset(months=months)) - start).days)
    pieces, limits = [], np.zeros((days, 2))
    for d in range(days):
        pieces.extend(duty.day(rng, d))
        limits[d] = pack.bms.draw_day(rng)
    return _Prepared(pack, settings, drawn, months, start, step_s, engine, pieces, limits, rng)


def run(case, months: int | None = None, seed: int = 0, overrides: dict | None = None,
        measured: bool = True, log_blocks: bool = True):
    """Run a case: yield one ``MonthOut`` per month.

    ``measured=False`` skips the sensors' noise (``frame`` is then the true signals too) and
    draws none of its random numbers."""
    data = load_case(case, overrides)
    plan = _prepare(data, months, seed, log_blocks)
    settings, drawn = plan.settings, plan.drawn
    months, start, step_s, engine, rng = plan.months, plan.start, plan.step_s, plan.engine, plan.rng
    pieces, limits = plan.pieces, plan.limits
    sensor_dict = dict(data.get("sensors", {}))
    offset_max = float(sensor_dict.pop("temp_offset_max_c", 0.0))
    sensor_settings = SensorSettings(**_only(SensorSettings, sensor_dict, "sensors"))
    temp_offset = 0.0
    if offset_max > 0:  # one constant error per pack, from a stream of its own
        temp_offset = float(np.random.default_rng([seed, 5]).uniform(-offset_max, offset_max))
        sensor_settings = replace(sensor_settings, temp_offset_c=sensor_settings.temp_offset_c + temp_offset)
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
        yield MonthOut(meas, true, month,
                       voltage_gain=sensors.voltage_gain if measured else 0.0,
                       current_gain=sensors.current_gain if measured else 0.0,
                       temp_offset_c=temp_offset if measured else 0.0, events=drawn)
