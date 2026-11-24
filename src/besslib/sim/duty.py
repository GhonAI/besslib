"""Duties: what the energy-management system asks of the pack, as a list of pieces.

A *piece* asks for a constant current, a constant power, or a rest from ``start_s`` to
``end_s`` (seconds since the run's start). The BMS may end its current early: at a stop
(a voltage limit), or when its own state of charge reaches ``until_soc``. A stopped
piece rests until its end; the next piece tries again.

Every duty draws its random numbers day by day, in a fixed order, before the plant runs
(``plan_days``); a day with a planted event still draws the numbers of an ordinary day,
so planting a day does not change the days after it.

``home`` is the day of ``tests/synth.py``, kept: a night load, a solar charge in eight
hourly steps of a half sine, an evening load, each at a random rate.

``grid`` keeps the pack around mid state of charge: a random current each hour, ended by
the BMS's own state of charge at the edge of a window, one arbitrage cycle a day, and
now and then a calibration full charge.

An *overlay* plants a field procedure on chosen days of any base duty (``capacity_test``,
``cp_discharge``). The base duty draws its numbers first, as on any day, then the overlay
replaces a stretch of the day with its own pieces; overlays draw no random numbers.

Pieces cover time without gaps, lie on the step grid and stay inside one day
(``check_pieces``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

import math

import numpy as np
import pandas as pd

DAY_S = 86400.0


@dataclass(frozen=True)
class Piece:
    start_s: float
    end_s: float
    mode: str = "rest"  # "current" (A), "power" (W) or "rest"; positive = charging
    value: float = 0.0
    until_soc: float | None = None  # end the current when the BMS state of charge reaches this
    v_min_offset_cell: float = 0.0  # this piece's discharge stop lies lower by this, V per cell
    hold_s: float | None = None  # power pieces: the current is set again at this interval
    tag: str = ""

    @property
    def active(self) -> bool:
        return self.mode != "rest" and self.value != 0.0


@dataclass(frozen=True)
class HomeDuty:
    """The ``tests/synth.py`` day. Rates are shares of ``capacity_ah`` per hour (C-rates)."""

    capacity_ah: float = 100.0
    night_c: tuple = (0.03, 0.10)
    peak_c: tuple = (0.0, 0.45)
    evening_c: tuple = (0.05, 0.25)
    deep_days: tuple = ()  # days with a full charge and an evening discharge to a lower stop
    deep_peak_c: float = 0.45
    deep_load_c: float = 0.30
    deep_v_min_offset_cell: float = 0.0  # how much lower the deep day's evening stop lies, V per cell
    kind: str = field(default="home", init=False)

    def day(self, rng: np.random.Generator, d: int) -> list[Piece]:
        c0 = self.capacity_ah
        base = d * DAY_S
        deep = d in self._deep
        peak = rng.uniform(self.peak_c[0], self.peak_c[1]) * c0
        if deep:
            peak = self.deep_peak_c * c0
        solar = [(8.0 + h, 9.0 + h, peak * np.sin(np.pi * (h + 0.5) / 8.0)) for h in range(8)]
        program = [
            (0.0, 7.0, -rng.uniform(self.night_c[0], self.night_c[1]) * c0),
            (7.0, 8.0, 0.0),
            *solar,
            (16.0, 18.0, 0.0),
            (18.0, 24.0, -rng.uniform(self.evening_c[0], self.evening_c[1]) * c0),
        ]
        offset = 0.0
        if deep:
            program[-1] = (18.0, 24.0, -self.deep_load_c * c0)
            offset = self.deep_v_min_offset_cell
        out = []
        for h0, h1, current in program:
            out.append(
                Piece(
                    base + h0 * 3600.0,
                    base + h1 * 3600.0,
                    "current" if current != 0.0 else "rest",
                    float(current),
                    v_min_offset_cell=offset if h0 == 18.0 else 0.0,
                    tag="deep" if (deep and h0 == 18.0) else "",
                )
            )
        return out

    @property
    def _deep(self) -> frozenset:
        return frozenset(int(d) for d in self.deep_days)


def on_grid(seconds: float, step_s: float) -> float:
    """``seconds`` rounded up to the step grid."""
    return math.ceil(seconds / step_s - 1e-9) * step_s


def replace_window(pieces: list[Piece], a: float, b: float, new: list[Piece]) -> list[Piece]:
    """``pieces`` with the stretch ``[a, b)`` replaced by ``new`` (which must cover it)."""
    out = []
    for p in pieces:
        if p.end_s <= a or p.start_s >= b:
            out.append(p)
            continue
        if p.start_s < a:
            out.append(_cut(p, p.start_s, a))
        if p.end_s > b:
            out.append(_cut(p, b, p.end_s))
    out.extend(new)
    return sorted(out, key=lambda p: p.start_s)


def _cut(p: Piece, start_s: float, end_s: float) -> Piece:
    return Piece(start_s, end_s, p.mode, p.value, p.until_soc, p.v_min_offset_cell, p.hold_s, p.tag)


def check_pieces(pieces: list[Piece], step_s: float) -> None:
    """Raise ``ValueError`` unless the pieces are in order, without gaps, on the step grid, within days."""
    for k, p in enumerate(pieces):
        if p.end_s <= p.start_s:
            raise ValueError(f"piece {k} is empty or reversed")
        if k and p.start_s != pieces[k - 1].end_s:
            raise ValueError(f"piece {k} does not start where piece {k - 1} ends")
        for t in (p.start_s, p.end_s):
            if abs(t / step_s - round(t / step_s)) > 1e-9:
                raise ValueError(f"piece {k} has an edge at {t} s, off the {step_s} s step grid")
        if math.floor(p.start_s / DAY_S) != math.floor((p.end_s - 1e-9) / DAY_S):
            raise ValueError(f"piece {k} crosses midnight")


@dataclass(frozen=True)
class GridDuty:
    """Grid duty: random currents around mid state of charge, an arbitrage cycle, a calibration.

    Currents are C-rates of ``capacity_ah``. Every day draws one uniform number per slot, in
    slot order (``24 / slot_h`` of them), whatever else the day holds. A charge piece ends
    when the BMS's state of charge reaches ``soc_high``, a discharge piece at ``soc_low``.
    The arbitrage cycle charges to ``cycle_high`` in the first window and discharges to
    ``cycle_low`` in the second; a calibration day (every ``calibration_days`` days, 0 =
    never) replaces the first ``calibration_hours`` with a charge to the BMS's full stop.
    Every default is an example value, not from a source.
    """

    capacity_ah: float = 100.0
    soc_low: float = 0.30
    soc_high: float = 0.70
    mid_c: float = 0.25
    slot_h: float = 1.0
    cycle: bool = True
    cycle_low: float = 0.25
    cycle_high: float = 0.75
    cycle_c: float = 0.25
    cycle_charge_h: tuple = (2.0, 6.0)
    cycle_discharge_h: tuple = (17.0, 21.0)
    calibration_days: int = 0
    calibration_c: float = 0.20
    calibration_hours: float = 6.0
    kind: str = field(default="grid", init=False)

    def day(self, rng: np.random.Generator, d: int) -> list[Piece]:
        c0 = self.capacity_ah
        base = d * DAY_S
        n = int(round(24.0 / self.slot_h))
        rates = rng.uniform(-self.mid_c, self.mid_c, n)
        pieces = []
        for k in range(n):
            a, b = base + k * self.slot_h * 3600.0, base + (k + 1) * self.slot_h * 3600.0
            r = float(rates[k])
            pieces.append(Piece(a, b, "current" if r != 0.0 else "rest", r * c0,
                                until_soc=(self.soc_high if r > 0 else self.soc_low) if r != 0.0 else None))
        if self.cycle:
            for (h0, h1), sign, target in ((self.cycle_charge_h, 1.0, self.cycle_high),
                                           (self.cycle_discharge_h, -1.0, self.cycle_low)):
                a, b = base + h0 * 3600.0, base + h1 * 3600.0
                pieces = replace_window(pieces, a, b, [Piece(a, b, "current", sign * self.cycle_c * c0,
                                                             until_soc=target, tag="cycle")])
        if self.calibration_days > 0 and (d + 1) % self.calibration_days == 0:
            b = base + self.calibration_hours * 3600.0
            pieces = replace_window(pieces, base, b, [Piece(base, b, "current", self.calibration_c * c0,
                                                            tag="calibration")])
        return pieces


DUTIES = {"home": HomeDuty, "grid": GridDuty}

OVERLAYS = ("capacity_test", "cp_discharge")


@dataclass(frozen=True)
class Overlay:
    """A field procedure planted on some days (``days``: day numbers) or one day a month
    (``day_of_month``, 1 to 28; 0 = none), starting ``start_h`` hours into the day.

    ``capacity_test``: charge at ``c_charge`` to the BMS's full stop, rest ``rest_h``, discharge
    at ``c_discharge`` to the BMS's stop, rest ``rest_h``. The charge and the discharge may take
    ``margin`` times the hours a full pack needs (Figgener et al., SI Note 6: "Full charge, then
    discharge at the system's maximum power (0.25 C to 1 C) until it stops").

    ``cp_discharge``: the same full charge, a rest, then a constant-power discharge of
    ``power_w`` watts to the BMS's stop, for at most ``discharge_h`` hours; the current is
    set again every step.

    Every default is an example value, not from a source.
    """

    kind: str
    capacity_ah: float = 100.0
    days: tuple = ()
    day_of_month: int = 0
    start_h: float = 0.0
    c_charge: float = 0.25
    c_discharge: float = 0.25
    power_w: float = 0.0
    discharge_h: float = 8.0
    rest_h: float = 1.0
    margin: float = 1.15

    def __post_init__(self) -> None:
        if self.kind not in OVERLAYS:
            raise ValueError(f"unknown overlay {self.kind!r}; known: {', '.join(OVERLAYS)}")
        if self.kind == "cp_discharge" and self.power_w <= 0:
            raise ValueError("a cp_discharge overlay needs power_w > 0 (discharge power in W)")
        if self.c_charge <= 0 or self.c_discharge <= 0:
            raise ValueError("C-rates must be positive")
        if not 0 <= self.day_of_month <= 28:
            raise ValueError("day_of_month is 0 (none) or 1 to 28")

    def planted(self, d: int, date: pd.Timestamp) -> bool:
        return d in self.days or (self.day_of_month > 0 and date.day == self.day_of_month)

    def pieces(self, d: int, step_s: float) -> tuple[float, float, list[Piece]]:
        """The window ``[a, b)`` of day ``d`` and the pieces that fill it."""
        rest = on_grid(self.rest_h * 3600.0, step_s)
        charge = on_grid(self.margin * 3600.0 / self.c_charge, step_s)
        if self.kind == "capacity_test":
            discharge = on_grid(self.margin * 3600.0 / self.c_discharge, step_s)
            tag, mode, value = "capacity-test", "current", -self.c_discharge * self.capacity_ah
            hold = None
        else:
            discharge = on_grid(self.discharge_h * 3600.0, step_s)
            tag, mode, value = "cp-discharge", "power", -self.power_w
            hold = step_s
        a = d * DAY_S + on_grid(self.start_h * 3600.0, step_s)
        edges = np.cumsum([0.0, charge, rest, discharge, rest]) + a
        if edges[-1] > (d + 1) * DAY_S:
            raise ValueError(f"the {self.kind} overlay does not fit in one day ({(edges[-1] - d * DAY_S) / 3600.0:.1f} h)")
        out = [
            Piece(edges[0], edges[1], "current", self.c_charge * self.capacity_ah, tag=tag),
            Piece(edges[1], edges[2]),
            Piece(edges[2], edges[3], mode, value, hold_s=hold, tag=tag),
            Piece(edges[3], edges[4]),
        ]
        return float(edges[0]), float(edges[-1]), out


class Overlaid:
    """A base duty with overlays planted on their days."""

    def __init__(self, base, overlays: list[Overlay], start: pd.Timestamp, step_s: float) -> None:
        self.base, self.overlays, self.start, self.step_s = base, overlays, pd.Timestamp(start), float(step_s)
        self.kind = base.kind

    def day(self, rng: np.random.Generator, d: int) -> list[Piece]:
        pieces = self.base.day(rng, d)  # the ordinary day's numbers, always
        date = self.start + pd.Timedelta(days=d)
        for o in self.overlays:
            if o.planted(d, date):
                a, b, new = o.pieces(d, self.step_s)
                pieces = replace_window(pieces, a, b, new)
        return pieces


def _checked(cls, settings: dict, what: str) -> dict:
    known = {f.name for f in fields(cls) if f.init}
    unknown = sorted(set(settings) - known)
    if unknown:
        raise ValueError(f"[{what}] does not know: {', '.join(unknown)}")
    return settings


def make_duty(settings: dict, start=None, step_s: float = 10.0):
    """The duty of a case's ``[duty]`` section. ``start`` and ``step_s`` serve the overlays."""
    settings = dict(settings)
    kind = settings.pop("kind", "home")
    overlays = settings.pop("overlay", [])
    if kind not in DUTIES:
        raise ValueError(f"unknown duty {kind!r}; known: {', '.join(DUTIES)}")
    for key in ("night_c", "peak_c", "evening_c", "deep_days", "cycle_charge_h", "cycle_discharge_h"):
        if key in settings:
            settings[key] = tuple(settings[key])
    base = DUTIES[kind](**_checked(DUTIES[kind], settings, "duty"))
    if not overlays:
        return base
    built = []
    for o in overlays:
        o = dict(o)
        o.setdefault("capacity_ah", getattr(base, "capacity_ah", 100.0))
        if "days" in o:
            o["days"] = tuple(int(x) for x in o["days"])
        built.append(Overlay(**_checked(Overlay, o, "duty.overlay")))
    return Overlaid(base, built, start if start is not None else pd.Timestamp("2020-01-01"), step_s)
