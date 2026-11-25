"""The SunSpec bus of besslib.sim: what the pack's BMS acts on, as SunSpec models over Modbus TCP.

* ``sunspec``: the encoders. Pure functions, standard library only: a ``BusState`` in,
  registers and bytes out, write requests in, set-points out.
* ``live``: a pack that runs step by step and takes set-points (``LivePack``).
* ``server``: the Modbus TCP server. It needs pymodbus (``pip install "besslib[modbus]"``),
  imported only when a server starts.

The bus shows what the BMS acts on: the simulator's own voltages, current and temperatures, without
sensor errors, and the BMS's own state of charge, settings, limits and alarms. True capacity, true state
of charge, resistances, leaks, the fan and planted events are not on it.
"""
