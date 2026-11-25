"""SunSpec encoders: BMS state to registers, and write requests to set-points.

The register map holds the SunSpec models 1 (common), 802 (battery bank), 803 (lithium-ion
bank) and 804 (lithium-ion string), in this order after the "SunS" marker at register
40000, and the end marker. The models are read from the SunSpec Alliance's own definition
files (``models/*.json``). The bank has one string and the string one module: the cases
have no finer structure.

* ``registers(state, scales)``: the whole map as 16-bit numbers; ``to_bytes`` gives the
  big-endian bytes.
* ``expected(state, scales)``: what a client should read for every point, in physical
  units. It is written a second time, from the state alone, so that a check of the
  registers against it is not a check of the registers against themselves.
* ``decode_write(address, values, state, scales)``: the set-points in a write request.

A point the BMS does not know reads as SunSpec's "not implemented" value of its type. No
value is made up.

**Sign of current and power on the bus:** positive while discharging, the opposite of
besslib's frames. The SunSpec model files do not state the sign of ``A`` and ``W``.

This module uses the standard library only and no state of its own, so that it can be
ported to C.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

BASE = 40000  # the register of the "SunS" marker
SUNS = (0x5375, 0x6E53)
END = (0xFFFF, 0)
MODELS = (1, 802, 803, 804)

# SunSpec's "not implemented" value of each type used here, as an unsigned number.
NOT_IMPLEMENTED = {
    "uint16": 0xFFFF, "int16": 0x8000, "uint32": 0xFFFFFFFF, "enum16": 0xFFFF, "bitfield32": 0xFFFFFFFF,
    "sunssf": 0x8000, "pad": 0x8000,
}
# Lowest and highest value of each type that is not its "not implemented" value.
_RANGE = {
    "uint16": (0, 0xFFFE), "int16": (-0x7FFF, 0x7FFF), "uint32": (0, 0xFFFFFFFE), "enum16": (0, 0xFFFE),
    "bitfield32": (0, 0x7FFFFFFF), "sunssf": (-10, 10),
}

ILLEGAL_ADDRESS = 2  # Modbus exception: not mapped, or not writable
ILLEGAL_VALUE = 3  # Modbus exception: a value the point does not take

# Enumerations, as in the model files.
TYPE_UNKNOWN, TYPE_LITHIUM_ION = 0, 4
STATE_DISCONNECTED, STATE_CONNECTED, STATE_STANDBY = 1, 3, 4
CHARGE_OFF, CHARGE_EMPTY, CHARGE_DISCHARGING, CHARGE_CHARGING, CHARGE_FULL, CHARGE_HOLDING = 1, 2, 3, 4, 5, 6
REMOTE = 0
ON, OFF = 1, 2  # SetOp, StrSetCon, SetCon: connect, disconnect; StrSetEna, SetEna: enable, disable
INVERTER_STOPPED, INVERTER_STANDBY, INVERTER_STARTED = 1, 2, 3
DISABLED_NONE, DISABLED_EXTERNAL = 0, 3


class BusError(Exception):
    """A request the device refuses; ``code`` is the Modbus exception code."""

    def __init__(self, code: int, text: str = "") -> None:
        super().__init__(text or f"Modbus exception {code}")
        self.code = code


@dataclass(frozen=True)
class BusState:
    """What the BMS acts on and shows. Plain numbers, texts and one set of names.

    Voltages, current and temperatures are the simulator's own values, without sensor errors. It has no field
    for true capacity, true state of charge, resistances, leaks, the fan or planted events."""

    manufacturer: str
    model: str
    version: str
    serial: str
    device_address: int
    ah_rating: float
    wh_rating: float
    charge_w_max: float | None  # None: the BMS sets no limit
    discharge_w_max: float | None
    soc_pct: float  # the BMS's own count
    soh_pct: float  # the BMS's setting
    soc_reserve_min_pct: float  # set-point
    soc_reserve_max_pct: float  # set-point
    heartbeat: int
    control_heartbeat: int  # set-point, echoed
    alarm_reset: int  # set-point; back to 0 when done
    battery_type: int
    state: int
    charge_status: int
    events: frozenset  # SunSpec event names
    pack_v: float
    v_max: float  # the BMS's voltage limits, for the pack
    v_min: float
    cell_v_max: float
    cell_v_min: float
    cell_v_avg: float
    cell_v_max_index: int  # 1-based
    cell_v_min_index: int
    current_a: float  # positive = discharging
    power_w: float  # positive = discharging
    charge_a_max: float | None  # None: the BMS sets no limit now
    discharge_a_max: float | None
    temp_max_c: float
    temp_min_c: float
    temp_avg_c: float
    cells_balancing: int
    cells: int  # cells in series
    connected: bool
    enabled: bool
    set_op: int  # the last contactor command (1 connect, 2 disconnect)
    set_inv_state: int  # set-point
    set_ena: int = ON  # the last string enable command
    con_request: int = 0  # a contactor command on the string model not yet carried out; 0 = none
    ena_request: int = 0  # an enable command on the string model not yet carried out; 0 = none


@dataclass(frozen=True)
class Scales:
    """Scale factors (powers of ten) of the map, chosen once per pack and then constant."""

    ah: int = 0
    wh: int = 0
    w_rate: int = 0
    soc: int = -2
    soh: int = -2
    v: int = -1
    cell_v: int = -3
    a: int = -1
    a_max: int = -1
    w: int = 0
    temp: int = -1


# The scale factor points of the models, and the field of ``Scales`` behind each (None: not implemented).
_SF = {
    "AHRtg_SF": "ah", "WHRtg_SF": "wh", "WChaDisChaMax_SF": "w_rate", "DisChaRte_SF": None, "SoC_SF": "soc",
    "DoD_SF": None, "SoH_SF": "soh", "V_SF": "v", "CellV_SF": "cell_v", "A_SF": "a", "AMax_SF": "a_max", "W_SF": "w",
    "ModTmp_SF": "temp",
}


def _fit(largest: float, highest: int, finest: int) -> int:
    """The finest scale factor, from ``finest`` up, at which ``largest`` stays within ``highest``."""
    sf = finest
    while _raw(largest, sf) > highest:
        sf += 1
    return sf


def scales_for(state: BusState, current_max_a: float | None = None, power_max_w: float | None = None) -> Scales:
    """Scale factors for a pack with this state's ratings.

    Current and power have room for the rated capacity per hour (1 C), or for ``current_max_a``
    and ``power_max_w`` where these are larger; voltage for 1.25 times the pack's upper limit."""
    v_top = 1.25 * state.v_max
    amps = max(state.ah_rating, abs(state.current_a), current_max_a or 0.0, state.charge_a_max or 0.0,
               state.discharge_a_max or 0.0)
    watts = max(v_top * amps, abs(state.power_w), power_max_w or 0.0)
    rate = max(state.charge_w_max or 0.0, state.discharge_w_max or 0.0)
    return Scales(
        ah=_fit(state.ah_rating, 0xFFFE, -2),
        wh=_fit(state.wh_rating, 0xFFFE, 0),
        w_rate=_fit(rate, 0xFFFE, 0),
        v=_fit(v_top, 0xFFFE, -1),
        a=_fit(amps, 0x7FFF, -2),
        a_max=_fit(amps, 0xFFFE, -2),
        w=_fit(watts, 0x7FFF, 0),
    )


# --- the layout ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    address: int
    model: int
    path: str  # "" in the model's fixed part, else "<group>/<index from 0>"
    index: int | None  # the group's index, None in the fixed part
    name: str
    type: str
    size: int  # registers
    access: str  # "R" or "RW"
    sf: str | None  # the scale factor's point
    symbols: tuple = ()  # (name, value) pairs


@lru_cache(maxsize=None)
def _definition(model: int) -> dict:
    text = resources.files("besslib.sim.bus").joinpath("models", f"model_{model}.json").read_text(encoding="utf-8")
    return json.loads(text)["group"]


@lru_cache(maxsize=None)
def points() -> tuple[Point, ...]:
    """Every point of the map, in address order: one string in model 803, one module in model 804."""
    out = []
    address = BASE + len(SUNS)
    for model in MODELS:
        group = _definition(model)
        parts = [("", None, group)] + [(f"{g['name']}/0", 0, g) for g in group.get("groups", [])]
        for path, index, part in parts:
            for p in part["points"]:
                symbols = tuple((s["name"], int(s["value"])) for s in p.get("symbols", []))
                out.append(Point(address, model, path, index, p["name"], p["type"], int(p["size"]),
                                 p.get("access", "R"), p.get("sf"), symbols))
                address += int(p["size"])
    return tuple(out)


@lru_cache(maxsize=None)
def _lengths() -> dict:
    """Registers of each model after its ID and L."""
    return {m: sum(p.size for p in points() if p.model == m) - 2 for m in MODELS}


@lru_cache(maxsize=None)
def _by_address() -> dict:
    return {p.address: p for p in points()}


def length() -> int:
    """Registers in the whole map, the markers included."""
    return len(SUNS) + sum(p.size for p in points()) + len(END)


# --- state to registers ----------------------------------------------------------------------


def _raw(value: float, sf: int) -> int:
    return round(value * 10 ** (-sf)) if sf < 0 else round(value / 10**sf)


def _contactor_bits(s: BusState) -> int:
    return (1 if s.enabled else 0) | (2 if s.connected else 0)  # STRING_ENABLED, CONTACTOR_STATUS


def _values(s: BusState) -> dict:
    """Per (model, path), the value of every point the BMS knows: physical units, names of events, texts."""
    string = "string/0"
    module = "lithium_ion_string_module/0"
    one = 1  # one string, one module
    return {
        (1, ""): {
            "Mn": s.manufacturer, "Md": s.model, "Vr": s.version, "SN": s.serial, "DA": s.device_address,
        },
        (802, ""): {
            "AHRtg": s.ah_rating, "WHRtg": s.wh_rating,
            "WChaRteMax": s.charge_w_max, "WDisChaRteMax": s.discharge_w_max,
            "SocRsvMax": s.soc_reserve_max_pct, "SoCRsvMin": s.soc_reserve_min_pct,
            "SoC": s.soc_pct, "SoH": s.soh_pct, "ChaSt": s.charge_status, "LocRemCtl": REMOTE,
            "Hb": s.heartbeat, "CtrlHb": s.control_heartbeat, "AlmRst": s.alarm_reset,
            "Typ": s.battery_type, "State": s.state,
            "Evt1": s.events, "Evt2": 0, "EvtVnd1": 0, "EvtVnd2": 0,
            "V": s.pack_v, "VMax": s.v_max, "VMin": s.v_min,
            "CellVMax": s.cell_v_max, "CellVMaxStr": one, "CellVMaxMod": one,
            "CellVMin": s.cell_v_min, "CellVMinStr": one, "CellVMinMod": one, "CellVAvg": s.cell_v_avg,
            "A": s.current_a, "AChaMax": s.charge_a_max, "ADisChaMax": s.discharge_a_max, "W": s.power_w,
            "SetOp": s.set_op, "SetInvState": s.set_inv_state,
        },
        (803, ""): {
            "NStr": one, "NStrCon": 1 if s.connected else 0,
            "ModTmpMax": s.temp_max_c, "ModTmpMaxStr": one, "ModTmpMaxMod": one,
            "ModTmpMin": s.temp_min_c, "ModTmpMinStr": one, "ModTmpMinMod": one, "ModTmpAvg": s.temp_avg_c,
            "StrVMax": s.pack_v, "StrVMaxStr": one, "StrVMin": s.pack_v, "StrVMinStr": one, "StrVAvg": s.pack_v,
            "StrAMax": s.current_a, "StrAMaxStr": one, "StrAMin": s.current_a, "StrAMinStr": one,
            "StrAAvg": s.current_a, "NCellBal": s.cells_balancing,
        },
        (803, string): {
            "StrNMod": one, "StrSt": _contactor_bits(s), "StrSoC": s.soc_pct, "StrSoH": s.soh_pct, "StrA": s.current_a,
            "StrCellVMax": s.cell_v_max, "StrCellVMaxMod": one, "StrCellVMin": s.cell_v_min, "StrCellVMinMod": one,
            "StrCellVAvg": s.cell_v_avg,
            "StrModTmpMax": s.temp_max_c, "StrModTmpMaxMod": one, "StrModTmpMin": s.temp_min_c,
            "StrModTmpMinMod": one, "StrModTmpAvg": s.temp_avg_c,
            "StrDisRsn": DISABLED_NONE if s.enabled else DISABLED_EXTERNAL,
            "StrConSt": 1 if s.connected else 0, "StrEvt1": s.events,
            "StrSetEna": s.set_ena, "StrSetCon": s.set_op,
        },
        (804, ""): {
            "Idx": one, "NMod": one, "St": _contactor_bits(s), "NCellBal": s.cells_balancing,
            "SoC": s.soc_pct, "SoH": s.soh_pct, "A": s.current_a, "V": s.pack_v,
            "CellVMax": s.cell_v_max, "CellVMaxMod": one, "CellVMin": s.cell_v_min, "CellVMinMod": one,
            "CellVAvg": s.cell_v_avg,
            "ModTmpMax": s.temp_max_c, "ModTmpMaxMod": one, "ModTmpMin": s.temp_min_c, "ModTmpMinMod": one,
            "ModTmpAvg": s.temp_avg_c,
            "ConSt": 1 if s.connected else 0, "Evt1": s.events,
            "SetEna": s.ena_request, "SetCon": s.con_request,
        },
        (804, module): {
            "ModNCell": s.cells, "ModSoC": s.soc_pct, "ModSoH": s.soh_pct,
            "ModCellVMax": s.cell_v_max, "ModCellVMaxCell": s.cell_v_max_index,
            "ModCellVMin": s.cell_v_min, "ModCellVMinCell": s.cell_v_min_index, "ModCellVAvg": s.cell_v_avg,
            "ModCellTmpMax": s.temp_max_c, "ModCellTmpMin": s.temp_min_c, "ModCellTmpAvg": s.temp_avg_c,
        },
    }


def _number(point: Point, value, scales: Scales) -> int | None:
    """The whole number a point carries, None when it is not implemented."""
    if point.type == "sunssf":
        field = _SF[point.name]
        return None if field is None else getattr(scales, field)
    if point.name == "ID" and point.path == "":
        return point.model
    if point.name == "L" and point.path == "":
        return _lengths()[point.model]
    if value is None:
        return None
    if isinstance(value, frozenset):
        bits = dict(point.symbols)
        return sum(1 << bits[name] for name in value)
    if point.sf is not None:
        return _raw(value, getattr(scales, _SF[point.sf]))
    if value != int(value):
        raise ValueError(f"model {point.model} {point.name}: {value!r} is not a whole number")
    return int(value)


def _encode(point: Point, value, scales: Scales) -> list[int]:
    if point.type == "string":
        data = (value or "").encode("utf-8")
        if len(data) > 2 * point.size:
            raise ValueError(f"model {point.model} {point.name}: {value!r} is longer than {2 * point.size} bytes")
        return list(struct.unpack(f">{point.size}H", data.ljust(2 * point.size, b"\0")))
    number = None if point.type == "pad" else _number(point, value, scales)
    if number is None:
        number = NOT_IMPLEMENTED[point.type]
    else:
        low, high = _RANGE[point.type]
        if not low <= number <= high:
            raise ValueError(f"model {point.model} {point.name}: {value!r} does not fit {point.type}")
    number &= (1 << (16 * point.size)) - 1
    return [(number >> (16 * k)) & 0xFFFF for k in range(point.size - 1, -1, -1)]


def registers(state: BusState, scales: Scales) -> list[int]:
    """The whole map from register 40000: "SunS", the models, the end marker."""
    values = _values(state)
    out = list(SUNS)
    for p in points():
        out += _encode(p, values[(p.model, p.path)].get(p.name), scales)
    return out + list(END)


def to_bytes(regs) -> bytes:
    """The registers as Modbus sends them: each 16-bit number big-endian."""
    return struct.pack(f">{len(regs)}H", *regs)


# --- what a client should read -----------------------------------------------------------------


def expected(state: BusState, scales: Scales) -> dict:
    """``{(model, path, point): value}``: what a client should read for every point but the pads.

    Values are in physical units after scaling, enumerations and bit fields as whole numbers,
    and None where the point must read as "not implemented". Written from the state, not from
    the registers or from what makes them."""
    s = state
    out = {(p.model, p.path, p.name): None for p in points() if p.type != "pad"}

    def put(model: int, path: str, values: dict) -> None:
        for name, value in values.items():
            if (model, path, name) not in out:
                raise KeyError((model, path, name))
            out[(model, path, name)] = value

    on = 1 if s.connected else 0
    status = (1 if s.enabled else 0) + 2 * on
    alarm_bits = {
        "OVER_TEMP_ALARM": 1 << 1, "OVER_TEMP_WARNING": 1 << 2, "UNDER_TEMP_ALARM": 1 << 3,
        "OVER_VOLT_WARNING": 1 << 10, "UNDER_VOLT_WARNING": 1 << 12, "UNDER_SOC_MIN_WARNING": 1 << 14,
        "OVER_SOC_MAX_WARNING": 1 << 16, "VOLTAGE_IMBALANCE_WARNING": 1 << 17,
    }
    alarms = sum(alarm_bits[name] for name in s.events)

    put(1, "", {"ID": 1, "L": 66, "Mn": s.manufacturer, "Md": s.model, "Vr": s.version, "SN": s.serial,
                "DA": s.device_address})
    put(802, "", {
        "ID": 802, "L": 62, "AHRtg": s.ah_rating, "WHRtg": s.wh_rating, "WChaRteMax": s.charge_w_max,
        "WDisChaRteMax": s.discharge_w_max, "SocRsvMax": s.soc_reserve_max_pct, "SoCRsvMin": s.soc_reserve_min_pct,
        "SoC": s.soc_pct, "SoH": s.soh_pct, "ChaSt": s.charge_status, "LocRemCtl": 0, "Hb": s.heartbeat,
        "CtrlHb": s.control_heartbeat, "AlmRst": s.alarm_reset, "Typ": s.battery_type, "State": s.state,
        "Evt1": alarms, "Evt2": 0, "EvtVnd1": 0, "EvtVnd2": 0, "V": s.pack_v, "VMax": s.v_max, "VMin": s.v_min,
        "CellVMax": s.cell_v_max, "CellVMaxStr": 1, "CellVMaxMod": 1, "CellVMin": s.cell_v_min, "CellVMinStr": 1,
        "CellVMinMod": 1, "CellVAvg": s.cell_v_avg, "A": s.current_a, "AChaMax": s.charge_a_max,
        "ADisChaMax": s.discharge_a_max, "W": s.power_w, "SetOp": s.set_op, "SetInvState": s.set_inv_state,
        "AHRtg_SF": scales.ah, "WHRtg_SF": scales.wh, "WChaDisChaMax_SF": scales.w_rate, "SoC_SF": scales.soc,
        "SoH_SF": scales.soh, "V_SF": scales.v, "CellV_SF": scales.cell_v, "A_SF": scales.a,
        "AMax_SF": scales.a_max, "W_SF": scales.w,
    })
    put(803, "", {
        "ID": 803, "L": 58, "NStr": 1, "NStrCon": on, "ModTmpMax": s.temp_max_c, "ModTmpMaxStr": 1, "ModTmpMaxMod": 1,
        "ModTmpMin": s.temp_min_c, "ModTmpMinStr": 1, "ModTmpMinMod": 1, "ModTmpAvg": s.temp_avg_c,
        "StrVMax": s.pack_v, "StrVMaxStr": 1, "StrVMin": s.pack_v, "StrVMinStr": 1, "StrVAvg": s.pack_v,
        "StrAMax": s.current_a, "StrAMaxStr": 1, "StrAMin": s.current_a, "StrAMinStr": 1, "StrAAvg": s.current_a,
        "NCellBal": s.cells_balancing, "CellV_SF": scales.cell_v, "ModTmp_SF": scales.temp, "A_SF": scales.a,
        "SoH_SF": scales.soh, "SoC_SF": scales.soc, "V_SF": scales.v,
    })
    put(803, "string/0", {
        "StrNMod": 1, "StrSt": status, "StrSoC": s.soc_pct, "StrSoH": s.soh_pct, "StrA": s.current_a,
        "StrCellVMax": s.cell_v_max, "StrCellVMaxMod": 1, "StrCellVMin": s.cell_v_min, "StrCellVMinMod": 1,
        "StrCellVAvg": s.cell_v_avg, "StrModTmpMax": s.temp_max_c, "StrModTmpMaxMod": 1, "StrModTmpMin": s.temp_min_c,
        "StrModTmpMinMod": 1, "StrModTmpAvg": s.temp_avg_c, "StrDisRsn": 0 if s.enabled else 3, "StrConSt": on,
        "StrEvt1": alarms, "StrSetEna": s.set_ena, "StrSetCon": s.set_op,
    })
    put(804, "", {
        "ID": 804, "L": 62, "Idx": 1, "NMod": 1, "St": status, "NCellBal": s.cells_balancing, "SoC": s.soc_pct,
        "SoH": s.soh_pct, "A": s.current_a, "V": s.pack_v, "CellVMax": s.cell_v_max, "CellVMaxMod": 1,
        "CellVMin": s.cell_v_min, "CellVMinMod": 1, "CellVAvg": s.cell_v_avg, "ModTmpMax": s.temp_max_c,
        "ModTmpMaxMod": 1, "ModTmpMin": s.temp_min_c, "ModTmpMinMod": 1, "ModTmpAvg": s.temp_avg_c, "ConSt": on,
        "Evt1": alarms, "SetEna": s.ena_request, "SetCon": s.con_request, "SoC_SF": scales.soc, "SoH_SF": scales.soh,
        "A_SF": scales.a, "V_SF": scales.v, "CellV_SF": scales.cell_v, "ModTmp_SF": scales.temp,
    })
    put(804, "lithium_ion_string_module/0", {
        "ModNCell": s.cells, "ModSoC": s.soc_pct, "ModSoH": s.soh_pct, "ModCellVMax": s.cell_v_max,
        "ModCellVMaxCell": s.cell_v_max_index, "ModCellVMin": s.cell_v_min, "ModCellVMinCell": s.cell_v_min_index,
        "ModCellVAvg": s.cell_v_avg, "ModCellTmpMax": s.temp_max_c, "ModCellTmpMin": s.temp_min_c,
        "ModCellTmpAvg": s.temp_avg_c,
    })
    return out


# --- write requests ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SetPoint:
    model: int
    path: str
    point: str
    value: float  # physical units; a whole number where the point has no scale factor


_CHOICES = {"AlmRst": (0, 1), "SetEna": (ON, OFF)}  # SetEna: the model file gives no symbols; as StrSetEna


def _set_point(point: Point, raw: int, state: BusState, scales: Scales) -> SetPoint:
    def bad(why: str) -> BusError:
        return BusError(ILLEGAL_VALUE, f"model {point.model} {point.name} = {raw}: {why}")

    if point.name in ("SocRsvMax", "SoCRsvMin"):
        sf = getattr(scales, _SF[point.sf])
        value = raw / 10 ** (-sf) if sf < 0 else float(raw * 10**sf)
        if not 0.0 <= value <= 100.0:
            raise bad("outside 0 to 100 %")
        return SetPoint(point.model, point.path, point.name, value)
    if point.name == "DA":
        if raw != state.device_address:
            raise bad("the device address is fixed")
    elif point.name == "CtrlHb":
        if raw == NOT_IMPLEMENTED["uint16"]:
            raise bad("not a value")
    else:
        choices = _CHOICES.get(point.name) or tuple(v for _, v in point.symbols)
        if raw not in choices:
            raise bad(f"one of {', '.join(map(str, choices))}")
    return SetPoint(point.model, point.path, point.name, raw)


def decode_write(address: int, values, state: BusState, scales: Scales) -> list[SetPoint]:
    """The set-points in a request that writes ``values`` from register ``address`` on: all of them or none.

    Raises ``BusError`` with code 2 when a register is not mapped or not writable, and with
    code 3 when a value is not one the point takes."""
    values = [int(v) for v in values]
    if not values:
        raise BusError(ILLEGAL_VALUE, "an empty write")
    targets = []
    for k in range(len(values)):
        point = _by_address().get(address + k)  # every writable point is one register long
        if point is None or point.access != "RW" or point.size != 1:
            raise BusError(ILLEGAL_ADDRESS, f"register {address + k} is not writable")
        targets.append(point)
    out = [_set_point(p, v, state, scales) for p, v in zip(targets, values)]
    low = high = None
    for sp in out:
        low = sp.value if sp.point == "SoCRsvMin" else low
        high = sp.value if sp.point == "SocRsvMax" else high
    if low is not None or high is not None:
        low = state.soc_reserve_min_pct if low is None else low
        high = state.soc_reserve_max_pct if high is None else high
        if not low < high:
            raise BusError(ILLEGAL_VALUE, f"the reserves need min < max, not {low} and {high}")
    return out
