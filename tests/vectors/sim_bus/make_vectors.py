"""Rebuild the byte vectors of the SunSpec bus: ``python tests/vectors/sim_bus/make_vectors.py``.

Inputs (written by hand, kept): ``states.json`` (BusStates, fields of ``besslib.sim.bus.sunspec.BusState``) and
``writes.json`` (write requests with the exception code or "ok" expected by hand).
Outputs: ``<state>.hex`` (the whole register map as hex, 4 digits per register, one register per line, "SunS" first)
and ``writes_result.json`` (what the encoder makes of each request). ``tests/test_sim_bus.py`` reads them and also
checks a few registers against literal offsets of the SunSpec model files, so a wrong encoder cannot confirm itself.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from besslib.sim.bus import sunspec

HERE = Path(__file__).resolve().parent


def load_states() -> dict:
    out = {}
    for name, raw in json.loads((HERE / "states.json").read_text()).items():
        raw["events"] = frozenset(raw["events"])
        out[name] = sunspec.BusState(**raw)
    return out


def scales_of(state):
    """The scale factors chosen from the state's ratings (``sunspec.scales_for``)."""
    return sunspec.scales_for(state)


def hex_lines(registers) -> str:
    return "\n".join(f"{r:04x}" for r in registers) + "\n"


def setpoint_dict(sp) -> dict:
    return dataclasses.asdict(sp) if dataclasses.is_dataclass(sp) else dict(vars(sp))


def write_result(request: dict, state, scales) -> dict:
    try:
        points = sunspec.decode_write(request["address"], request["values"], state, scales)
    except sunspec.BusError as exc:
        return {"error": exc.code}
    return {"setpoints": [setpoint_dict(p) for p in points]}


def main() -> None:
    states = load_states()
    for name, state in states.items():
        (HERE / f"{name}.hex").write_text(hex_lines(sunspec.registers(state, scales_of(state))))
    spec = json.loads((HERE / "writes.json").read_text())
    state = states[spec["state"]]
    results = [{"address": r["address"], "values": r["values"], **write_result(r, state, scales_of(state))}
               for r in spec["requests"]]
    (HERE / "writes_result.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    print(f"wrote {len(states)} register maps and {len(results)} write results")


if __name__ == "__main__":
    main()
