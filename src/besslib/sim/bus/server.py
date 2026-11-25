"""The Modbus TCP server of a live pack.

It needs pymodbus, imported only when a server starts. Modbus has no login: whoever reaches
the address can command the pack, so the default address is this machine only (127.0.0.1).

Reads are answered from one register snapshot per step, so a read never mixes two steps.
A write is decoded at once (all of it or none) and acts at the next step; its registers
show the accepted values at once; a request that fails leaves every set-point as it was.

Function codes 3, 6 and 16, unit id 1. Every other function code gets exception 1, also the
ones pymodbus would answer by itself (7, 8, 17, 43, ...). The heartbeat ``Hb`` counts
simulated seconds, so it jumps by the step and stands still between steps.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
import time
from contextlib import contextmanager

from . import sunspec
from .live import DEVICE_ADDRESS, EndOfRun, LivePack

MISSING = 'the bus needs pymodbus: pip install "besslib[modbus]"'
HOST, PORT = "127.0.0.1", 5020
LATE_S = 2.0  # behind the wall clock by more than this: said once
FUNCTIONS = (3, 6, 16)  # read holding registers, write one, write several


class MissingPymodbus(RuntimeError):
    """pymodbus cannot be imported."""

    def __init__(self) -> None:
        super().__init__(MISSING)


def check_pymodbus() -> None:
    """Raise ``MissingPymodbus`` when pymodbus cannot be imported."""
    try:
        import pymodbus.server  # noqa: F401
        from pymodbus.simulator import DataType, SimData, SimDevice  # noqa: F401
    except ImportError as exc:
        raise MissingPymodbus from exc


class Bus:
    """A live pack behind its registers. ``lock`` guards the pack; the registers are swapped whole."""

    def __init__(self, live: LivePack) -> None:
        self.live = live
        self.lock = threading.RLock()
        self.host, self.port = HOST, 0
        self.stepping = False
        self._registers: list[int] = []
        self._echo: tuple[int, int] | None = None  # the last single register written, for its reply
        self.refresh()

    def refresh(self) -> None:
        with self.lock:
            self._registers = sunspec.registers(self.live.state(), self.live.scales)

    def step(self, n: int = 1) -> None:
        """Run ``n`` steps and refresh the registers after each."""
        self.stepping = True
        try:
            for _ in range(n):
                with self.lock:
                    self.live.step()
                    self.refresh()
        finally:
            self.stepping = False

    def write(self, address: int, values) -> None:
        """Take a write request, all of it or none; raises ``sunspec.BusError``."""
        with self.lock:
            setpoints = sunspec.decode_write(address, values, self.live.state(), self.live.scales)
            before = self.live._set_points()
            try:
                self.live.write(setpoints)
                self.refresh()
            except Exception:
                self.live._set_points_back(before)
                raise

    def devices(self) -> list:
        """The pymodbus devices: the pack at its address, and a refusal at every other."""
        from pymodbus.constants import ExcCodes
        from pymodbus.simulator import DataType, SimData, SimDevice

        async def action(function, start, address, _count, current, values):
            if function not in FUNCTIONS:
                return ExcCodes.ILLEGAL_FUNCTION
            if values is not None:
                self._echo = None
                try:
                    self.write(address, values)
                except sunspec.BusError as exc:
                    return ExcCodes(exc.code)
                if function == 6:
                    self._echo = (address, values[0])
            snapshot = self._registers
            current[: len(snapshot)] = snapshot
            # After a write with code 6 pymodbus reads the register back for its reply, with the same code and before
            # any other request. A step in between may have reset the register: the reply is the accepted value.
            if function == 6 and values is None and self._echo is not None and self._echo[0] == address:
                current[address - start] = self._echo[1]
            return None

        async def nobody(*_args):
            return ExcCodes.GATEWAY_NO_RESPONSE

        def block():
            return [SimData(sunspec.BASE, values=list(self._registers), datatype=DataType.REGISTERS)]

        return [SimDevice(id=DEVICE_ADDRESS, simdata=block(), action=action),
                SimDevice(id=0, simdata=block(), action=nobody)]


def _refusals() -> list:
    """A pymodbus request class for every function code but ours: refused, its data not read."""
    from pymodbus.constants import ExcCodes
    from pymodbus.pdu import ExceptionResponse, ModbusPDU

    class Refused(ModbusPDU):
        def x_decode(self, data: bytes) -> None:
            pass

        def x_encode(self) -> bytes:
            return b""

        async def datastore_update(self, _context, device_id):
            return ExceptionResponse(self.function_code, ExcCodes.ILLEGAL_FUNCTION, device_id=device_id)

    return [type(f"Refused{code}", (Refused,), {"function_code": code}) for code in range(1, 128)
            if code not in FUNCTIONS]


async def _run_server(bus: Bus, host: str, port: int, ready: threading.Event, stop: list) -> None:
    from pymodbus.server import ModbusTcpServer

    server = ModbusTcpServer(bus.devices(), address=(host, port), custom_pdu=_refusals())
    done = asyncio.Event()
    stop[:] = [asyncio.get_running_loop(), done]
    await server.serve_forever(background=True)
    try:
        bus.host, bus.port = server.transport.sockets[0].getsockname()[:2]
        ready.set()
        await done.wait()
    finally:
        await server.shutdown()


@contextmanager
def _served(live: LivePack, host: str, port: int):
    check_pymodbus()
    bus = Bus(live)
    ready, stop, failure = threading.Event(), [], []

    def main() -> None:
        try:
            asyncio.run(_run_server(bus, host, port, ready, stop))
        except Exception as exc:  # handed to the caller
            failure.append(exc)
        finally:
            ready.set()

    thread = threading.Thread(target=main, name="besslib-bus", daemon=True)
    thread.start()
    ready.wait()
    if failure:
        raise OSError(f"the server cannot listen on {host}:{port}") from failure[0]
    try:
        yield bus
    finally:
        loop, done = stop
        if thread.is_alive():
            loop.call_soon_threadsafe(done.set)
        thread.join(timeout=10.0)


class _Signals:
    """Ctrl-C and SIGTERM as a request to stop that never cuts a step in two (main thread only)."""

    def __init__(self, bus: Bus) -> None:
        self.bus, self.asked, self._before = bus, False, {}

    def __enter__(self) -> "_Signals":
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                self._before[number] = signal.signal(number, self._on)
        return self

    def __exit__(self, *_exc) -> None:
        for number, handler in self._before.items():
            signal.signal(number, handler)

    def _on(self, *_args) -> None:
        self.asked = True
        if not self.bus.stepping:
            raise KeyboardInterrupt

    def wait(self, seconds: float) -> bool:
        if not self.asked:
            time.sleep(seconds)
        return self.asked


def _pace(bus: Bus, speed: float, stop) -> None:
    """Step at the wall clock times ``speed`` until ``stop.wait`` gives True; raises ``EndOfRun`` at the plan's end.

    When the machine cannot keep up, it says so once and runs every step as fast as it can."""
    if speed <= 0:
        raise ValueError("speed must be above 0")
    wait = bus.live.step_s / speed
    due = time.monotonic() + wait
    said = False
    while not stop.wait(max(due - time.monotonic(), 1e-4)):  # the short wait lets a write in
        bus.step()
        due += wait
        late = time.monotonic() - due
        if late > max(LATE_S, 2.0 * wait) and not said:
            said = True
            print(f"besslib.sim: this machine cannot keep {speed:g} times the wall clock; every step still runs",
                  file=sys.stderr, flush=True)


@contextmanager
def running(live: LivePack, host: str = HOST, port: int = 0, speed: float | None = None):
    """A server in its own thread, for tests and checks: yields the ``Bus`` (``host``, ``port``, ``lock``, ``step(n)``).

    ``port=0`` takes a free port. ``speed=None``: time stands still until ``step`` is called."""
    with _served(live, host, port) as bus:
        stop = threading.Event()
        pacer = None
        if speed is not None:

            def pace() -> None:
                try:
                    _pace(bus, speed, stop)
                except EndOfRun:
                    pass

            pacer = threading.Thread(target=pace, name="besslib-pace", daemon=True)
            pacer.start()
        try:
            yield bus
        finally:
            stop.set()
            if pacer is not None:
                pacer.join(timeout=10.0)


def serve(live: LivePack, host: str = HOST, port: int = PORT, speed: float = 1.0, out=None, started=None) -> None:
    """Serve until Ctrl-C (or SIGTERM) or the end of the plan; then write the live stretch's truth to ``out`` if given.

    ``started(bus)`` is called once the server listens. ``out`` needs a pack that keeps its truth."""
    if speed <= 0:
        raise ValueError("speed must be above 0")
    if out is not None:
        live.need_truth()
    with _served(live, host, port) as bus:
        if started is not None:
            started(bus)
        try:
            with _Signals(bus) as signals:
                _pace(bus, speed, signals)
        except KeyboardInterrupt:
            pass
        except EndOfRun as exc:
            print(f"besslib.sim: {exc}", file=sys.stderr, flush=True)
    if out is not None:
        live.write_truth(out)
