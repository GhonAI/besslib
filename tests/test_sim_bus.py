"""The SunSpec bus of besslib.sim: byte vectors, set-points, no truth on the bus, the import rule, the missing extra.

The register map is checked three ways, so the encoder cannot confirm itself: (1) stored byte vectors rebuilt by
``tests/vectors/sim_bus/make_vectors.py``; (2) literal offsets and values written here from the SunSpec model files;
(3) a small decoder in this file that reads the registers back with the model files and compares them with
``sunspec.expected`` (written from the state, not from the registers). Tests that need pymodbus or pysunspec2 are
skipped when those are missing.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from besslib.sim.bus import sunspec

VECTORS = Path(__file__).parent / "vectors" / "sim_bus"
BUS = Path(sunspec.__file__).parent
sys.path.insert(0, str(VECTORS))
import make_vectors  # noqa: E402

MODELS = (1, 802, 803, 804)
FIELDS = {
    "manufacturer", "model", "version", "serial", "device_address", "ah_rating", "wh_rating", "charge_w_max",
    "discharge_w_max", "soc_pct", "soh_pct", "soc_reserve_min_pct", "soc_reserve_max_pct", "heartbeat",
    "control_heartbeat", "alarm_reset", "battery_type", "state", "charge_status", "events", "pack_v", "v_max", "v_min",
    "cell_v_max", "cell_v_min", "cell_v_avg", "cell_v_max_index", "cell_v_min_index", "current_a", "power_w",
    "charge_a_max", "discharge_a_max", "temp_max_c", "temp_min_c", "temp_avg_c", "cells_balancing", "cells",
    "connected", "enabled", "set_op", "set_inv_state",
    # Added by the live pack's interface, with defaults: the last string enable command (803 StrSetEna reads it) and
    # the 804 SetCon / SetEna requests that read back until the step, then 0. They are commands the client wrote,
    # not anything of the plant.
    "set_ena", "con_request", "ena_request",
}
TRUTH_WORDS = ("true", "truth", "leak", "resistance", "r0", "fan", "capacity_ah", "fade", "self_discharge", "event_list")


@pytest.fixture(scope="module")
def states():
    return make_vectors.load_states()


def regs_of(name, states):
    state = states[name]
    return sunspec.registers(state, make_vectors.scales_of(state))


def hex_regs(name):
    return [int(x, 16) for x in (VECTORS / f"{name}.hex").read_text().split()]


def s16(x):
    return x - 0x10000 if x >= 0x8000 else x


def u32(regs, i):
    return (regs[i] << 16) | regs[i + 1]


# 1. Byte vectors -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ordinary", "no_limits_alarms"])
def test_stored_vectors_equal_the_encoder(name, states):
    regs = regs_of(name, states)
    assert regs == hex_regs(name)
    assert sunspec.to_bytes(regs) == b"".join(r.to_bytes(2, "big") for r in hex_regs(name))


def test_stored_write_results_equal_the_encoder_and_the_hand_written_expectations(states):
    spec = json.loads((VECTORS / "writes.json").read_text())
    stored = json.loads((VECTORS / "writes_result.json").read_text())
    state = states[spec["state"]]
    scales = make_vectors.scales_of(state)
    assert len(stored) == len(spec["requests"])
    for request, saved in zip(spec["requests"], stored):
        now = make_vectors.write_result(request, state, scales)
        assert json.loads(json.dumps(now, default=str)) == {k: v for k, v in saved.items() if k in now}, request["what"]
        if request["expect"] == "ok":
            assert "setpoints" in now and now["setpoints"], request["what"]
        else:
            assert now == {"error": request["expect"]}, request["what"]


def test_header_and_model_lengths_at_literal_offsets(states):
    regs = hex_regs("ordinary")
    assert len(regs) == 260
    assert regs[0:2] == [0x5375, 0x6E53]  # "SunS"
    assert (regs[2], regs[3]) == (1, 66)
    assert (regs[70], regs[71]) == (802, 62)
    assert (regs[134], regs[135]) == (803, 58)  # one string
    assert (regs[194], regs[195]) == (804, 62)  # one module
    assert regs[258:260] == [0xFFFF, 0]
    assert (regs[136], regs[137]) == (1, 1)  # NStr, NStrCon
    assert (regs[196], regs[197]) == (1, 1)  # Idx, NMod


def test_values_at_literal_offsets_and_scale_factors():
    regs = hex_regs("ordinary")
    # model 802 starts at register 70 (address 40070); offsets from model_802.json
    assert s16(regs[70 + 56]) == -2 and s16(regs[70 + 59]) == -1 and s16(regs[70 + 60]) == -3  # SoC_SF V_SF CellV_SF
    assert regs[70 + 11] == 6250          # SoC 62.5 %
    assert regs[70 + 13] == 9725          # SoH 97.25 %
    assert regs[70 + 34] == 504           # V 50.4 V
    assert regs[70 + 37] == 3612          # CellVMax 3.612 V
    assert regs[70 + 40] == 3598          # CellVMin
    assert regs[70 + 43] == 3600          # CellVAvg
    assert regs[70 + 21] == 4 and regs[70 + 22] == 3 and regs[70 + 16] == 4  # Typ, State, ChaSt
    assert regs[70 + 50] == 1 and regs[70 + 51] == 3                          # SetOp, SetInvState
    assert regs[70 + 10] == 500 and regs[70 + 9] == 9500                      # SoCRsvMin, SocRsvMax
    # charging at the bus: W is negative (positive = discharging); its scale factor comes from the file itself
    w_sf = s16(regs[70 + 63])
    assert s16(regs[70 + 47]) * 10.0 ** w_sf == pytest.approx(-1008.0, abs=0.5 * 10.0 ** w_sf)
    a_sf = s16(regs[70 + 61])
    assert s16(regs[70 + 44]) * 10.0 ** a_sf == pytest.approx(-20.0, abs=0.5 * 10.0 ** a_sf)  # charging: negative
    assert regs[70 + 47] >= 0x8000  # negative as int16
    # strings: "Mn" 16 registers from offset 2 of model 1, big-endian, zero padded
    assert b"".join(r.to_bytes(2, "big") for r in regs[4:20]) == b"besslib".ljust(32, b"\0")
    assert regs[68] == 1  # DA
    assert regs[242] == 14 and regs[194 + 48] == 14  # ModNCell of 804's module group


def test_second_vector_signs_events_and_missing_limits():
    regs = hex_regs("no_limits_alarms")
    assert (regs[70 + 26], regs[70 + 27]) == (0x0001, 0x0002)  # Evt1: OVER_TEMP_ALARM bit 1, OVER_SOC_MAX_WARNING bit 16
    assert regs[70 + 4] == 0xFFFF and regs[70 + 5] == 0xFFFF   # no charge / discharge power limit
    assert regs[70 + 45] == 0xFFFF and regs[70 + 46] == 0xFFFF  # no charge / discharge current limit
    assert 0 < regs[70 + 47] < 0x8000 and 0 < regs[70 + 44] < 0x8000  # discharging: W and A positive
    sf = s16(regs[134 + 23])  # ModTmp_SF of model 803
    assert s16(regs[134 + 4]) * 10.0 ** sf == pytest.approx(-3.5, abs=0.5 * 10.0 ** sf)
    assert regs[70 + 22] == 5 and regs[70 + 16] == 5
    assert regs[70 + 18] == 40000
    assert regs[70 + 19] == 7
    assert regs[70 + 50] == 2 and regs[70 + 51] == 1
    assert (regs[136], regs[137]) == (1, 0)  # one string, none connected
    assert regs[194 + 48] == 396


def test_not_implemented_values_at_literal_offsets():
    for name in ("ordinary", "no_limits_alarms"):
        regs = hex_regs(name)
        assert regs[69] == 0x8000                       # Pad of model 1
        assert (regs[70 + 24], regs[70 + 25]) == (0xFFFF, 0xFFFF)  # WarrDt uint32
        assert regs[70 + 48] == 0xFFFF                  # ReqInvState enum16
        assert regs[70 + 49] == 0x8000                  # ReqW int16


# 2. A decoder of the test's own, from the model files -----------------------------------------------------------------

NOT_IMPLEMENTED = {"uint16": 0xFFFF, "enum16": 0xFFFF, "int16": 0x8000, "sunssf": 0x8000, "uint32": 0xFFFFFFFF,
                   "bitfield32": 0xFFFFFFFF, "bitfield16": 0xFFFF, "count": 0xFFFF, "int32": 0x80000000}


def model_json(model):
    return json.loads((BUS / "models" / f"model_{model}.json").read_text())


def decode(regs):
    """{(model, path, point): value or None} read from the registers with the model files only."""
    out, at = {}, 2
    sf_of = {}

    def read(point, i):
        kind, size = point["type"], point["size"]
        raw = regs[i] if size == 1 else u32(regs, i)
        if kind == "string":
            text = b"".join(r.to_bytes(2, "big") for r in regs[i:i + size]).rstrip(b"\0").decode()
            return text or None  # an empty string is "not implemented"
        if kind == "pad":
            return "pad"
        if raw == NOT_IMPLEMENTED.get(kind):
            return None
        return s16(raw) if kind in ("int16", "sunssf") else raw

    for model in MODELS:
        assert regs[at] == model
        length = regs[at + 1]
        top = model_json(model)["group"]
        base = at

        def walk(group, off, path):
            for point in group.get("points", []):
                if point["name"] in ("ID", "L") and not path:
                    off += point["size"]
                    continue
                value = read(point, base + off)
                if value != "pad":
                    out[(model, path, point["name"])] = (value, point)
                    if point["type"] == "sunssf":
                        sf_of[(model, point["name"])] = value
                off += point["size"]
            for sub in group.get("groups", []):
                count = out[(model, "", sub["count"])][0]
                for k in range(count):
                    off = walk(sub, off, f"{sub['name']}/{k}")
            return off

        # ID and L are real points of the map: read them too
        out[(model, "", "ID")] = (regs[at], top["points"][0])
        out[(model, "", "L")] = (regs[at + 1], top["points"][1])
        walk(top, 0, "")
        at += 2 + length
    assert regs[at:at + 2] == [0xFFFF, 0]
    scaled = {}
    for (model, path, name), (value, point) in out.items():
        sf = point.get("sf")
        if value is not None and sf:
            e = sf_of[(model, sf)]
            value = (value * 10.0 ** e, 0.5 * 10.0 ** e)
        scaled[(model, path, name)] = (value, point)
    return scaled


def mismatches(regs, state, scales):
    """What differs between the registers (read back with the model files) and ``expected``; every point is compared."""
    want = sunspec.expected(state, scales)
    got = decode(regs)
    bad = []
    if set(got) != set(want):
        bad.append(f"points differ: {sorted(set(got) ^ set(want), key=str)[:5]}")
    for key in sorted(set(got) & set(want), key=str):
        value, point = got[key]
        w = want[key]
        if w is None or value is None:
            if not (w is None and value is None):
                bad.append(f"{key}: read {value!r}, expected {w!r}")
        elif isinstance(value, tuple):
            if abs(value[0] - w) > value[1] * (1 + 1e-9) + 1e-12:
                bad.append(f"{key}: read {value[0]}, expected {w}")
        elif value != w:
            bad.append(f"{key}: read {value!r}, expected {w!r}")
    return bad


@pytest.mark.parametrize("name", ["ordinary", "no_limits_alarms"])
def test_registers_decode_to_expected_within_half_a_step(name, states):
    state = states[name]
    assert mismatches(hex_regs(name), state, make_vectors.scales_of(state)) == []
    assert len(decode(hex_regs(name))) == len([p for p in sunspec.points() if p.type != "pad"])


@pytest.mark.parametrize("index", [11 + 70, 107, 2 + 4, 69 + 0 + 50, 242])
def test_a_corrupted_register_is_found_as_a_mismatch(index, states):
    """The negative self-check: the same comparison code must see a wrong register."""
    state = states["ordinary"]
    scales = make_vectors.scales_of(state)
    regs = hex_regs("ordinary")
    regs[index] ^= 0x0101
    assert mismatches(regs, state, scales)


# 3. Interface, truth, imports -----------------------------------------------------------------------------------------

def test_busstate_is_the_listed_fields_and_has_no_truth():
    names = {f.name for f in dataclasses.fields(sunspec.BusState)}
    assert names == FIELDS
    assert not [n for n in names if any(w in n for w in TRUTH_WORDS)]
    assert dataclasses.is_dataclass(sunspec.BusState) and sunspec.BusState.__dataclass_params__.frozen


def test_import_rule_nothing_from_soh_and_sunspec_is_standard_library_only():
    files = sorted(BUS.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "sunspec.py", "live.py", "server.py"}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")] + [("." * node.level) + (node.module or "") + "." + a.name for a in node.names]
            for n in names:
                assert not re.search(r"(^|\.)soh($|\.)", n), f"{path.name} imports {n}"
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(ast.parse((BUS / "sunspec.py").read_text())):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] in stdlib for a in node.names), ast.dump(node)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert node.module.split(".")[0] in stdlib, node.module


def test_pymodbus_is_imported_inside_functions_only():
    tree = ast.parse((BUS / "server.py").read_text())
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ",".join(a.name for a in node.names)
            assert "pymodbus" not in mod, "pymodbus imported at the top of server.py"


def test_default_bind_is_loopback():
    from besslib.sim.bus import server

    assert inspect.signature(server.serve).parameters["host"].default == "127.0.0.1"
    running = inspect.signature(server.running).parameters.get("host")
    assert running is None or running.default in ("127.0.0.1", inspect.Parameter.empty)
    out = subprocess.run([sys.executable, "-m", "besslib.sim", "serve", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "127.0.0.1" in out.stdout


def test_import_and_serve_message_without_pymodbus():
    code = (
        "import sys\n"
        "sys.modules['pymodbus'] = None\n"
        "import besslib.sim\n"
        "import besslib.sim.bus.sunspec\n"
        "import runpy\n"
        "sys.argv = ['besslib.sim', 'serve', '--case', 'demo']\n"
        "runpy.run_module('besslib.sim', run_name='__main__')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    text = out.stdout + out.stderr
    assert out.returncode != 0
    assert 'pip install "besslib[modbus]"' in text, text
    assert "Traceback" not in text, text


# 4. The live pack ----------------------------------------------------------------------------------------------------

def make_live(case="demo", **kw):
    from besslib.sim.bus.live import LivePack

    return LivePack(case, seed=1, **kw)


def run_until(live, ok, hours=24):
    """Step an hour at a time until ``ok(state)``; the state it stopped at."""
    for _ in range(hours):
        live.step(360)
        if ok(live.state()):
            return live.state()
    raise AssertionError(f"the condition never held in {hours} simulated hours")


def write_to(live, address, values):
    live.write(sunspec.decode_write(address, values, live.state(), live.scales))


def flowing(direction):
    """direction 'charge' (bus current negative) or 'discharge' (bus current positive)."""
    return lambda s: s.current_a < -1.0 if direction == "charge" else s.current_a > 1.0


def test_a_contactor_command_echoes_at_once_and_acts_at_the_next_step():
    live = make_live()
    s = run_until(live, lambda s: abs(s.current_a) > 1.0)
    assert s.connected and s.state == sunspec.STATE_CONNECTED
    write_to(live, 40120, [2])  # SetOp = DISCONNECT
    now = live.state()
    assert now.set_op == 2                                  # the register echoes the accepted value at once
    assert now.connected and now.current_a == s.current_a   # the current still flows before the step
    live.step(1)
    after = live.state()
    assert not after.connected and after.current_a == 0.0 and after.state == sunspec.STATE_DISCONNECTED
    write_to(live, 40120, [1])
    assert not live.state().connected and live.state().set_op == 1
    live.step(1)
    assert live.state().connected


def test_inverter_not_running_stops_the_current_at_the_next_step():
    live = make_live()
    s = run_until(live, lambda s: abs(s.current_a) > 1.0)
    write_to(live, 40121, [1])  # SetInvState = stopped
    assert live.state().set_inv_state == 1 and live.state().current_a == s.current_a
    live.step(1)
    assert live.state().current_a == 0.0 and live.state().state == sunspec.STATE_STANDBY


def test_a_reserve_stops_the_flow_in_its_direction_at_the_next_step():
    live = make_live()
    s = run_until(live, flowing("charge"))
    write_to(live, 40079, [int(round((s.soc_pct - 0.5) * 100))])  # SocRsvMax just below the state of charge
    assert live.state().soc_reserve_max_pct == pytest.approx(s.soc_pct - 0.5, abs=0.005)
    assert live.state().current_a == s.current_a
    live.step(1)
    assert live.state().current_a == 0.0
    live = make_live()
    s = run_until(live, flowing("discharge"))
    write_to(live, 40080, [int(round((s.soc_pct + 0.5) * 100))])  # SoCRsvMin just above the state of charge
    assert live.state().current_a == s.current_a
    live.step(1)
    assert live.state().current_a == 0.0


def test_alarm_reset_and_heartbeat_echo_at_once_and_alarm_reset_reads_0_after_the_step():
    live = make_live()
    live.step(2)
    write_to(live, 40089, [3])
    write_to(live, 40090, [1])
    s = live.state()
    assert (s.control_heartbeat, s.alarm_reset) == (3, 1)
    live.step(1)
    s = live.state()
    assert (s.control_heartbeat, s.alarm_reset) == (3, 0)


def test_reserve_echo_and_string_requests_reset_after_the_step():
    live = make_live()
    live.step(2)
    write_to(live, 40080, [3000])
    write_to(live, 40079, [8000])
    s = live.state()
    assert (s.soc_reserve_min_pct, s.soc_reserve_max_pct) == (pytest.approx(30.0), pytest.approx(80.0))
    write_to(live, 40231, [2])  # 804 SetCon
    write_to(live, 40230, [2])  # 804 SetEna
    s = live.state()
    assert (s.con_request, s.ena_request, s.set_op, s.set_ena) == (2, 2, 2, 2)
    assert s.connected and s.enabled
    live.step(1)
    s = live.state()
    assert (s.con_request, s.ena_request, s.set_op, s.set_ena) == (0, 0, 2, 2)  # requests read 0, commands stay
    assert not s.connected and not s.enabled and s.current_a == 0.0


def test_bad_values_and_read_only_addresses_raise_the_modbus_codes():
    live = make_live()
    s, sc = live.state(), live.scales
    for address, values, code in ((40120, [3], 3), (40080, [10001], 3), (40079, [0], 3), (40081, [5000], 2),
                                  (40070, [802], 2), (39999, [1], 2), (40080, [3000, 5000], 2), (40090, [2], 3),
                                  (40230, [0], 3), (40068, [2], 3)):
        with pytest.raises(sunspec.BusError) as e:
            sunspec.decode_write(address, values, s, sc)
        assert e.value.code == code, (address, values)
    with pytest.raises(sunspec.BusError):  # all or nothing: min not below max
        sunspec.decode_write(40079, [9000, 9500], s, sc)


def test_soc_on_the_bus_is_the_bms_count_not_the_true_state_of_charge():
    live = make_live()
    engine = live.engine
    differ = False
    for _ in range(10):
        live.step(360)
        bms = min(max(engine.bms_soc * 100.0, 0.0), 100.0)
        assert live.state().soc_pct == pytest.approx(bms, abs=1e-9)
        true = float(sum(engine.state.soc) / len(engine.state.soc)) * 100.0
        differ = differ or abs(true - bms) > 1e-6
    assert differ, "the BMS count never differed from the true state of charge: the test proved nothing"


def test_bus_state_matches_the_engines_last_sample_and_signs_are_exact():
    live = make_live()
    s = run_until(live, flowing("charge"))
    last, engine, g = live.last, live.engine, live.pack.groups
    assert last.current_a > 0                                  # the sample: positive = charging
    assert s.current_a == -last.current_a and s.current_a < 0  # the bus: positive = discharging
    assert s.power_w == -last.current_a * last.pack_v and s.power_w < 0
    assert s.pack_v == last.pack_v
    assert s.cell_v_max == last.cell_v.max() and s.cell_v_min == last.cell_v.min()
    assert s.cell_v_avg == pytest.approx(last.cell_v.mean(), abs=1e-12)
    temps = engine.temps[:g]
    assert (s.temp_max_c, s.temp_min_c) == (temps.max(), temps.min())
    assert s.soc_pct == pytest.approx(min(max(engine.bms_soc, 0.0), 1.0) * 100.0, abs=1e-9)
    s = run_until(live, flowing("discharge"))
    assert live.last.current_a < 0 and s.current_a == -live.last.current_a > 0 and s.power_w > 0


# 5. Over Modbus TCP (pymodbus) and with pysunspec2 -------------------------------------------------------------------

@pytest.fixture
def bus():
    pytest.importorskip("pymodbus")
    from besslib.sim.bus import server

    live = make_live()
    with server.running(live, "127.0.0.1", 0, speed=None) as handle:
        yield handle, live


def client_of(port):
    from pymodbus.client import ModbusTcpClient

    c = ModbusTcpClient("127.0.0.1", port=port)
    assert c.connect()
    return c


def read_map(c, n):
    out = []
    for at in range(0, n, 100):
        r = c.read_holding_registers(40000 + at, count=min(100, n - at), device_id=1)
        assert not r.isError(), r
        out += list(r.registers)
    return out


def test_server_serves_the_register_map_of_the_state(bus):
    handle, live = bus
    handle.step(5)
    c = client_of(handle.port)
    try:
        assert read_map(c, sunspec.length()) == sunspec.registers(live.state(), live.scales)
    finally:
        c.close()


def test_server_exceptions_echo_at_once_and_effect_at_the_next_step(bus):
    handle, live = bus
    handle.step(3)
    c = client_of(handle.port)
    try:
        r = c.write_register(40120, 3, device_id=1)
        assert r.isError() and r.exception_code == 3
        r = c.write_register(40081, 5000, device_id=1)
        assert r.isError() and r.exception_code == 2
        assert not c.write_register(40120, 2, device_id=1).isError()
        assert read_map(c, sunspec.length())[70 + 50] == 2  # echoed at once
        assert live.state().connected                          # but not yet in effect
        handle.step(1)
        assert not live.state().connected
        r = c.read_holding_registers(40000, count=2, device_id=2)
        assert r.isError() and r.exception_code == 0x0B       # another unit id
    finally:
        c.close()


def test_pysunspec2_finds_the_four_models(bus):
    pytest.importorskip("sunspec2")
    from sunspec2.modbus.client import SunSpecModbusClientDeviceTCP

    handle, live = bus
    handle.step(3)
    d = SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr="127.0.0.1", ipport=handle.port, timeout=10)
    d.base_addr_list = [40000]
    try:
        d.scan()
        assert set(MODELS) <= set(d.models)
        assert d.models[802][0].points["SoC"].cvalue == pytest.approx(live.state().soc_pct, abs=0.005)
    finally:
        d.close()


def test_s7_comparison_finds_a_wrong_expected_value(bus, monkeypatch):
    """The negative self-check of S7's own comparison code: a corrupted expectation must show as a mismatch."""
    pytest.importorskip("sunspec2")
    from sunspec2.modbus.client import SunSpecModbusClientDeviceTCP

    sys.path.insert(0, str(Path(__file__).parents[1] / "validation"))
    import sim_s7

    handle, live = bus
    handle.step(3)
    d = SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr="127.0.0.1", ipport=handle.port, timeout=10)
    d.base_addr_list = [40000]
    d.scan()
    try:
        clean = []
        n = sim_s7.compare(d, live, sunspec, "clean", clean)
        assert clean == [] and n == len([p for p in sunspec.points() if p.type != "pad"])
        real = sunspec.expected

        def wrong(state, scales):
            out = real(state, scales)
            out[(802, "", "V")] += 0.2
            out[(803, "string/0", "StrSetCon")] = None
            return out

        monkeypatch.setattr(sunspec, "expected", wrong)
        found = []
        sim_s7.compare(d, live, sunspec, "wrong", found)
        assert len(found) == 2
    finally:
        d.close()


# 6. The state against the case data and the BMS rules (never against live.py's own formulas) ------------------------------

import threading  # noqa: E402
import time  # noqa: E402
from dataclasses import replace  # noqa: E402

import numpy as np  # noqa: E402

from besslib import sim  # noqa: E402
from besslib.sim.bus import live as live_module  # noqa: E402
from besslib.sim.bus import server as server_module  # noqa: E402
from besslib.sim.bus.live import LivePack  # noqa: E402

HOT = {"temperature.ambient.mean_c": 40.0, "temperature.ambient.season_amp_c": 0.0, "temperature.ambient.daily_amp_c": 0.0}
COLD = {"temperature.ambient.mean_c": 0.0, "temperature.ambient.season_amp_c": 0.0, "temperature.ambient.daily_amp_c": 0.0}
DERATE = [[0, 0.2, 0.5], [25, 0.6, 1.0], [60, 0.1, 0.3]]  # temperature, charge C-rate, discharge C-rate: different columns


def small(months=2, case="demo", **overrides):
    return LivePack(case, seed=1, overrides={"pack.groups": 4, **overrides}, months=months)


def strings_of(s):
    return [s.manufacturer, s.model, s.version, s.serial]


def test_ratings_voltage_limits_and_texts_from_the_case_file():
    # demo.toml: 4 groups of the synth cell, 100 Ah, 3.7 V nominal -> 4 x 3.7 V x 100 Ah = 1480 Wh; bms limits 4.20 / 3.30 V per cell
    live = small(**{"bms.soh_percent": 91.5, "bms.derate": DERATE})
    live.step(30)
    s = live.state()
    assert (s.manufacturer, s.model, s.serial) == ("besslib", "4S 100 Ah synthetic", "SIMULATED")
    assert (s.ah_rating, s.wh_rating) == (100.0, 1480.0)            # Wh, not kWh
    assert (s.v_max, s.v_min) == (pytest.approx(4 * 4.20), pytest.approx(4 * 3.30))
    assert s.soh_pct == 91.5                                          # the BMS setting, not the true capacity
    assert s.cells == 4
    # derate table: largest charge column 0.6 C and discharge column 1.0 C of the rated 1480 Wh
    assert (s.charge_w_max, s.discharge_w_max) == (pytest.approx(0.6 * 1480.0), pytest.approx(1.0 * 1480.0))
    t = live.engine.temps[:4]
    low_c, low_d = live.pack.bms.current_limits(float(t.max()), float(t.min()), 100.0)
    assert (s.charge_a_max, s.discharge_a_max) == (low_c, low_d)
    assert s.charge_a_max != s.discharge_a_max and s.charge_a_max < s.discharge_a_max  # a swap would fail
    # all groups at one temperature T: interpolate the table by hand
    if float(t.max()) == float(t.min()):
        x = float(t.max())
        assert s.charge_a_max == pytest.approx(100 * np.interp(x, [0, 25, 60], [0.2, 0.6, 0.1]))
        assert s.discharge_a_max == pytest.approx(100 * np.interp(x, [0, 25, 60], [0.5, 1.0, 0.3]))


def test_no_derate_table_means_no_limits_and_a_true_capacity_does_not_reach_soh():
    live = small(**{"bms.derate": [], "bms.soh_percent": 88.0, "pack.fade_points_per_year": 3000.0})
    live.step(2000)
    s = live.state()
    assert (s.charge_w_max, s.discharge_w_max, s.charge_a_max, s.discharge_a_max) == (None, None, None, None)
    assert s.soh_pct == 88.0
    assert float(live.engine.capacity(live.seconds).mean()) < 100.0 * 0.999  # the plant did fade; the BMS setting did not follow


@pytest.mark.parametrize("case", sim.case_names())
def test_no_case_name_and_no_seed_in_any_string_of_any_case(case):
    live = LivePack(case, seed=9137, months=1)
    s = live.state()
    for text in strings_of(s):
        assert not re.search(rf"\b{re.escape(case)}\b", text, re.I), (case, text)
        assert "9137" not in text and "seed" not in text.lower(), text
    assert s.serial == "SIMULATED"


def test_cell_indexes_average_temperatures_balancing_and_heartbeat():
    live = small(**{"pack.series": 2, "pack.capacity_spread_sd": 0.08, "bms.balance": True, "bms.balance_v": 0.0,
                    "bms.balance_spread_v": 1e-4})
    live.step(60)
    s, last, g = live.state(), live.last, 4
    v = last.cell_v  # mean cell voltage of each group; two cells in series per group
    assert v.max() > v.min()
    assert s.cell_v_max == v.max() and s.cell_v_min == v.min() and s.cell_v_avg == pytest.approx(v.mean())
    assert s.cell_v_max_index == int(v.argmax()) * 2 + 1 and s.cell_v_min_index == int(v.argmin()) * 2 + 1
    assert s.cell_v_max_index != s.cell_v_min_index and s.cells == 8
    t = live.engine.temps[:g]
    assert (s.temp_max_c, s.temp_min_c, s.temp_avg_c) == (t.max(), t.min(), pytest.approx(t.mean()))
    bleeding = live.pack.bms.bleeding(v)
    assert bleeding.any() and s.cells_balancing == int(bleeding.sum()) * 2
    assert "VOLTAGE_IMBALANCE_WARNING" in s.events
    assert s.heartbeat == int(live.seconds) % 65535


def test_the_heartbeat_wraps_at_65535_and_never_reads_not_implemented():
    live = small(months=1, **{"case.step_s": 600})
    seen = []
    for _ in range(115):
        live.step(1)
        seen.append(live.state().heartbeat)
    assert max(seen) <= 65534
    assert seen[108] == 65400  # step 109 is at 65400 s
    assert seen[109] == 66000 % 65535 == 465 and seen[110] == 66600 % 65535


def test_every_event_and_state_with_the_reason_that_raises_it():
    derate = [[20, 1.0, 1.0], [30, 0.5, 0.5]]
    hot = small(**HOT, **{"bms.derate": derate})
    assert hot.state().events == {"OVER_TEMP_WARNING"}              # derating because of heat, no stop temperature
    assert "OVER_TEMP_WARNING" not in small(**{"bms.derate": derate, **COLD}).state().events
    cold = small(**COLD, **{"bms.charge_block_below_c": 10.0})
    assert "UNDER_TEMP_ALARM" in cold.state().events
    assert "UNDER_TEMP_ALARM" not in small(**HOT, **{"bms.charge_block_below_c": 10.0}).state().events
    for live in (hot, cold):
        live.step(20)
        assert "FAN_ERROR" not in live.state().events


def test_over_temp_alarm_latches_alarm_reset_while_hot_keeps_it_and_clears_when_cool():
    live = small(**HOT, **{"bms.stop_temp_c": 35.0})
    assert "OVER_TEMP_ALARM" in live.state().events
    write_to(live, 40090, [1])
    live.step(2)
    assert "OVER_TEMP_ALARM" in live.state().events                  # reset while hot: it comes back
    live.engine.temps[:] = 20.0                                      # the pack cools
    live.step(2)
    assert "OVER_TEMP_ALARM" in live.state().events                  # latched until a reset
    write_to(live, 40090, [1])
    live.step(1)
    assert "OVER_TEMP_ALARM" not in live.state().events


def step_until(live, ok, steps=8640 * 2, block=10):
    """Step ``block`` steps at a time (the hourly pieces would clear a hold between hourly samples)."""
    for _ in range(steps // block):
        live.step(block)
        if ok(live.state()):
            return live.state()
    raise AssertionError(f"the condition never held in {steps} steps")


def test_full_and_empty_stops_raise_the_voltage_warnings_and_the_charge_status():
    live = small(**{"bms.v_cell_max": 3.9})                  # the charge ends at the voltage limit, not at 100 %
    s = step_until(live, lambda s: "OVER_VOLT_WARNING" in s.events)
    assert "UNDER_VOLT_WARNING" not in s.events
    live.step(1)                                             # ChaSt follows one step later (the last sample's current)
    assert live.state().charge_status == 5                   # FULL
    live = small(**{"pack.soc0": [0.02] * 4})
    s = step_until(live, lambda s: "UNDER_VOLT_WARNING" in s.events)
    assert "OVER_VOLT_WARNING" not in s.events
    live.step(1)
    assert live.state().charge_status == 2                   # EMPTY


def test_charge_status_state_and_status_bits_with_enabled_not_equal_connected():
    live = small()
    s = live.state()
    assert (s.state, s.charge_status) == (3, 6)                      # CONNECTED, HOLDING at rest
    s = run_until(live, flowing("charge"))
    assert s.charge_status == 4 and s.state == 3
    s = run_until(live, flowing("discharge"))
    assert s.charge_status == 3
    write_to(live, 40230, [2])                                        # 804 SetEna = disable: connected but not enabled
    live.step(1)
    regs = sunspec.registers(live.state(), live.scales)
    assert live.state().connected and not live.state().enabled
    assert regs[194 + 4:194 + 6] == [0, 2] and regs[134 + 29 + 0:134 + 29 + 2] == [0, 2]  # St, StrSt: CONTACTOR_STATUS only
    assert live.state().charge_status == 1                            # OFF
    write_to(live, 40230, [1])
    write_to(live, 40120, [2])                                        # disconnect, still enabled
    live.step(1)
    regs = sunspec.registers(live.state(), live.scales)
    assert regs[194 + 4:194 + 6] == [0, 1] and regs[134 + 29:134 + 31] == [0, 1]           # STRING_ENABLED only
    assert live.state().state == 1 and live.state().charge_status == 1 and regs[134 + 3] == 0  # NStrCon 0
    write_to(live, 40120, [1])
    write_to(live, 40121, [2])
    live.step(1)
    assert live.state().state == 4                                    # STANDBY


@pytest.mark.parametrize("address,value", [(40120, 2), (40191, 2), (40231, 2), (40190, 2), (40230, 2), (40121, 1), (40121, 2)])
def test_each_contactor_enable_and_inverter_set_point_stops_the_current(address, value):
    live = small()
    s = run_until(live, lambda s: abs(s.current_a) > 1.0)
    write_to(live, address, [value])
    assert live.state().current_a == s.current_a
    live.step(1)
    assert live.state().current_a == 0.0


def test_a_reserve_never_loosens_a_tighter_until_soc():
    live = small()
    low = live_module.Piece(0.0, 100.0, "current", -10.0, 0.30)   # discharge ending at 30 %
    high = live_module.Piece(0.0, 100.0, "current", 10.0, 0.70)   # charge ending at 70 %
    live._acting.update(rsv_min=10.0, rsv_max=90.0)               # both looser than the pieces' own ends
    assert live._asked(low)[0].until_soc == 0.30 and live._asked(high)[0].until_soc == 0.70
    live._acting.update(rsv_min=50.0, rsv_max=60.0)               # tighter: they win
    assert live._asked(low)[0].until_soc == 0.50 and live._asked(high)[0].until_soc == 0.60


def test_changing_a_reserve_releases_its_own_latch_and_the_other_reserve_does_not():
    live = small()
    s = run_until(live, flowing("charge"))
    write_to(live, 40079, [int(round((s.soc_pct - 0.5) * 100))])
    live.step(1)
    assert live.state().current_a == 0.0 and "OVER_SOC_MAX_WARNING" in live.state().events
    assert "UNDER_SOC_MIN_WARNING" not in live.state().events        # not swapped
    write_to(live, 40080, [100])                                       # the other reserve: no release
    live.step(5)
    assert live.state().current_a == 0.0
    write_to(live, 40079, [10000])                                     # its own reserve: released
    live.step(1)
    assert live.state().current_a < 0.0 and "OVER_SOC_MAX_WARNING" not in live.state().events
    s = run_until(live, lambda s: flowing("discharge")(s) and s.soc_pct < 95.0)
    write_to(live, 40080, [int(round((s.soc_pct + 0.5) * 100))])
    live.step(1)
    assert live.state().current_a == 0.0 and "UNDER_SOC_MIN_WARNING" in live.state().events
    assert "OVER_SOC_MAX_WARNING" not in live.state().events


def test_a_reserve_acts_by_the_bms_count_not_by_the_true_state_of_charge():
    for offset, flows in ((-10.0, True), (+3.0, False)):
        live = small()
        s = run_until(live, flowing("charge"))
        assert s.soc_pct < 85.0
        true = float(live.engine.state.soc.mean()) * 100.0
        live.engine.bms_soc = (true + offset) / 100.0                  # the BMS count differs from the plant
        reserve = true - 5.0 if flows else true + 1.5                  # between the two counts
        write_to(live, 40079, [int(round(reserve * 100))])
        live.step(1)
        assert (live.state().current_a != 0.0) is flows, (offset, reserve)


# 7. Time -------------------------------------------------------------------------------------------------------------

def test_a_short_plan_with_a_large_step_runs_to_the_end_and_raises_again():
    live = small(months=1, **{"case.step_s": 600})
    live.step(31 * 144 - 1)
    assert live.seconds == 31 * 86400 - 600
    live.step(1)                                                       # across the month's end
    assert live.seconds == 31 * 86400 and str(live.time) == "2020-02-01 00:00:00"
    for _ in range(2):
        with pytest.raises(live_module.EndOfRun):
            live.step(1)
    assert list(live.truth_tables()) == ["2020-01"] and len(live.truth_tables()["2020-01"]["steps"]) == 31 * 144


def test_a_run_goes_on_across_a_month_end_and_keeps_every_step():
    live = small(months=2, **{"case.step_s": 600})
    live.step(31 * 144 + 20)
    t = live.truth_tables()
    assert list(t) == ["2020-01", "2020-02"]
    assert [len(t[m]["steps"]) for m in t] == [31 * 144, 20]
    assert str(t["2020-02"]["steps"]["time"].iloc[0]) == "2020-02-01 00:00:00"
    feb = t["2020-02"]["steps"]
    assert (feb["voltage_v"] > 12.0).all()  # the month's own samples, not an empty month
    assert feb["current_a"].iloc[-1] == live.last.current_a


def test_the_shown_sample_is_the_steps_start_as_in_the_truth():
    live = small()
    for _ in range(3):
        run_until(live, lambda s: abs(s.current_a) > 1.0)
        row = live.truth_tables()["2020-01"]["steps"].iloc[-1]
        assert live.state().pack_v == row["voltage_v"] and live.state().current_a == -row["current_a"]
        assert (live.state().cell_v_max, live.state().cell_v_min) == (row["v_cell_max"], row["v_cell_min"])
        live.step(1)


def test_the_scales_have_room_for_a_duty_far_above_one_c():
    live = small(**{"duty.capacity_ah": 2000.0})
    amps = max(abs(p.value) for p in live._plan.pieces if p.mode == "current")
    assert amps > 5.0 * live.state().ah_rating  # the pieces ask for far more than 1 C
    top = live.pack.cells_in_series * live.pack.bms.v_cell_max
    sunspec.registers(replace(live.state(), current_a=-amps, power_w=-amps * top), live.scales)


def test_a_current_rating_that_needs_a_coarser_scale_fits_both_signs(states):
    s = replace(next(iter(states.values())), ah_rating=400.0, current_a=-400.0, charge_a_max=None, discharge_a_max=None)
    sc = sunspec.scales_for(s)
    assert sc.a == -1  # 400 A at 0.01 A would not fit a signed register
    sunspec.registers(s, sc)
    sunspec.registers(replace(s, current_a=400.0), sc)


def test_start_lands_on_the_right_piece_and_the_month():
    from besslib.sim.run import _prepare

    ov = {"pack.groups": 4}
    plan = _prepare(sim.load_case("demo", ov), 3, 1)
    live = LivePack("demo", seed=1, overrides=ov, start="2020-02-01", months=3)
    first = next(p for p in plan.pieces if p.start_s >= 31 * 86400)
    assert live._plan.pieces[live._k] == first
    assert live.seconds == 31 * 86400 and str(live.time) == "2020-02-01 00:00:00"
    assert not [p for p in plan.pieces[: live._k] if p.start_s >= 31 * 86400]
    with pytest.raises(ValueError):
        LivePack("demo", seed=1, overrides=ov, start="2020-02-02", months=3)


def test_truth_tables_partial_month_manifest_times_and_keep_truth(tmp_path):
    live = LivePack("demo", seed=1, overrides={"pack.groups": 4}, start="2020-02-01", months=3)
    live.step(100)
    write_to(live, 40120, [2])
    at = live.seconds
    live.step(1)
    write_to(live, 40089, [5])                                         # a heartbeat is not logged
    tables = live.truth_tables()
    assert list(tables) == ["2020-02"] and len(tables["2020-02"]["steps"]) == 101
    manifest = live.write_truth(tmp_path)
    assert manifest["live_from"] == "2020-02-01T00:00:00" and manifest["live_to"] == live.time.isoformat()
    origin = pd.Timestamp("2020-01-01")
    assert manifest["set_points"] == [{"time": (origin + pd.Timedelta(seconds=at)).isoformat(), "model": 802,
                                       "point": "SetOp", "value": 2}]
    assert (tmp_path / "manifest.json").exists() and all((tmp_path / f).exists() for f in manifest["files"])
    short = LivePack("demo", seed=1, overrides={"pack.groups": 4, "case.step_s": 600}, months=1, keep_truth=False)
    short.step(31 * 144)
    assert short._months == []                                         # nothing kept
    for call in (short.truth_tables, lambda: short.write_truth(tmp_path / "none")):
        with pytest.raises(ValueError):
            call()


def test_serve_with_out_writes_the_truth_files_of_a_short_plan(tmp_path):
    pytest.importorskip("pymodbus")
    live = LivePack("demo", seed=1, overrides={"pack.groups": 2, "case.step_s": 1800}, months=1)
    seen = []
    server_module.serve(live, port=0, speed=1e9, out=tmp_path / "out", started=lambda bus: seen.append(bus.port))
    assert seen and seen[0] > 0
    files = sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert "manifest.json" in files and "2020-01.parquet" in files
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["mode"] == "live"


# 8. Server and command line ------------------------------------------------------------------------------------------

def test_serve_defaults_are_read_from_the_argument_parser(monkeypatch):
    pytest.importorskip("pymodbus")
    from besslib.sim import __main__ as cli

    got = {}
    monkeypatch.setattr(server_module, "serve", lambda live, **kw: got.update(kw))
    assert cli.main(["serve", "--case", "demo"]) == 0
    assert (got["host"], got["port"], got["speed"], got["out"]) == ("127.0.0.1", 5020, 1.0, None)
    assert server_module.HOST == "127.0.0.1"


def test_other_function_codes_are_refused(bus):
    handle, _ = bus
    c = client_of(handle.port)
    try:
        for call in (lambda: c.read_coils(0, count=1, device_id=1), lambda: c.read_discrete_inputs(0, count=1, device_id=1),
                     lambda: c.read_input_registers(40000, count=1, device_id=1), lambda: c.write_coil(0, True, device_id=1)):
            r = call()
            assert r.isError() and r.exception_code == 1
        assert not c.read_holding_registers(40000, count=2, device_id=1).isError()
    finally:
        c.close()


def test_a_write_waits_for_a_step_in_progress():
    live = small()
    bus = server_module.Bus(live)
    log, real_step, real_write = [], live.step, live.write

    def slow_step(n=1):
        log.append("step start")
        time.sleep(0.3)
        real_step(n)
        log.append("step end")

    live.step = slow_step
    live.write = lambda sps: (log.append("write"), real_write(sps))[1]
    t = threading.Thread(target=bus.step)
    t.start()
    time.sleep(0.1)
    bus.write(40120, [2])
    t.join()
    assert log == ["step start", "step end", "write"]


def test_a_read_never_mixes_two_steps(bus):
    handle, live = bus
    c = client_of(handle.port)                                         # connected before the stepper starts
    history = [list(handle._registers)]
    real = handle.refresh

    def refresh():
        real()
        history.append(list(handle._registers))

    handle.refresh = refresh
    done = threading.Event()
    stepper = threading.Thread(target=lambda: [handle.step(1) for _ in iter(done.is_set, True)])
    stepper.start()
    try:
        deadline = time.monotonic() + 20
        while len(history) < 20 and time.monotonic() < deadline:
            time.sleep(0.005)
        blocks = []
        for _ in range(25):
            r = c.read_holding_registers(40080, count=100, device_id=1)  # Hb at 40088 and the voltages in one request
            assert not r.isError()
            blocks.append(list(r.registers))
    finally:
        done.set()
        stepper.join()
        c.close()
    known = {tuple(h[80:180]) for h in history}
    assert len(blocks) == 25 and all(tuple(b) in known for b in blocks)
    assert len({tuple(b) for b in blocks}) > 1                         # the reads did see the steps move


def test_the_pacer_steps_at_the_wall_clock_times_speed(monkeypatch):
    for speed in (5.0, 40.0):
        live = small()
        bus = server_module.Bus(live)
        clock, waits = [100.0], []

        class Stop:
            def wait(self, seconds):
                waits.append(seconds)
                clock[0] += seconds
                return len(waits) > 3

        monkeypatch.setattr(server_module.time, "monotonic", lambda: clock[0])
        server_module._pace(bus, speed, Stop())
        assert waits[:3] == [pytest.approx(10.0 / speed)] * 3
        assert live.seconds == 30.0                                    # three steps of 10 s
        waits.clear()


# 9. The encoder -----------------------------------------------------------------------------------------------------

def test_values_out_of_range_raise_and_nothing_is_made_up(states):
    base = states["ordinary"]
    scales = make_vectors.scales_of(base)
    for change in ({"soc_pct": 700.0}, {"soc_pct": -1.0}, {"heartbeat": 65535}, {"power_w": 1e9}, {"pack_v": 1e6},
                   {"version": "x" * 17}, {"cells": 70000}, {"temp_max_c": -4000.0}):
        with pytest.raises(ValueError):
            sunspec.registers(replace(base, **change), scales)


@pytest.mark.parametrize("case", ["demo", "grid-string", "overheating"])
def test_scale_factors_leave_room_for_the_largest_current_and_power_of_the_duty(case):
    live = LivePack(case, seed=1, months=1)
    pieces = live._plan.pieces
    amps = max((abs(p.value) for p in pieces if p.mode == "current"), default=0.0)
    watts = max((abs(p.value) for p in pieces if p.mode == "power"), default=0.0)
    sc = live.scales
    assert round(amps / 10.0 ** sc.a) <= 0x7FFF and round(watts / 10.0 ** sc.w) <= 0x7FFF
    assert round(amps / 10.0 ** sc.a_max) <= 0xFFFE
    top = live.pack.cells_in_series * live.pack.bms.v_cell_max * 1.25
    assert round(top / 10.0 ** sc.v) <= 0xFFFE
    # the whole map encodes at the duty's largest current and power
    s = replace(live.state(), current_a=amps, power_w=watts, charge_a_max=amps, discharge_a_max=amps)
    sunspec.registers(s, sc)


# 10. More of the server and the command line ------------------------------------------------------------------------

def raw_request(port, code):
    """One Modbus TCP frame with this function code and 4 bytes of data; the reply's PDU."""
    import socket as _socket

    frame = (1).to_bytes(2, "big") + b"\0\0" + (6).to_bytes(2, "big") + bytes([1, code]) + b"\0\0\0\1"
    with _socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(frame)
        head = sock.recv(7)
        return sock.recv(head[5] - 1)


def test_every_function_code_but_3_6_and_16_gets_exception_1(bus):
    handle, _ = bus
    for code in (1, 2, 4, 5, 7, 8, 11, 12, 15, 17, 20, 21, 22, 23, 24, 43, 100, 127):
        reply = raw_request(handle.port, code)
        assert reply == bytes([code | 0x80, 1]), code


def test_bad_months_and_a_non_empty_out_exit_with_2_and_one_line(tmp_path, capsys):
    pytest.importorskip("pymodbus")
    from besslib.sim import __main__ as cli

    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "x").write_text("x")
    for args in (["--months", "0"], ["--out", str(tmp_path / "taken")]):
        assert cli.main(["serve", "--case", "demo", *args]) == 2
        err = capsys.readouterr().err
        assert len(err.strip().splitlines()) == 1 and err.strip(), err


def test_a_failing_refresh_inside_a_write_leaves_nothing_accepted():
    live = small()
    bus = server_module.Bus(live)
    before = live.state()

    def broken():
        raise RuntimeError("refresh failed")

    bus.refresh = broken
    with pytest.raises(RuntimeError):
        bus.write(40120, [2])
    assert live.state() == before
    live.step(1)
    assert live.state().connected and live.state().set_op == 1


def test_fc6_on_setcon_echoes_2_even_when_a_step_falls_in_between(bus):
    handle, live = bus
    real = handle.write

    def write_then_step(address, values):
        real(address, values)
        handle.step(1)                                                 # the step that resets SetCon to 0

    handle.write = write_then_step
    c = client_of(handle.port)
    try:
        r = c.write_register(40231, 2, device_id=1)
        assert not r.isError() and list(r.registers) == [2]
        assert read_map(c, sunspec.length())[194 + 37] == 0            # and the register itself reads 0 after the step
    finally:
        c.close()
