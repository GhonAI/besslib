"""``python -m besslib.sim``: ``run`` a case into a folder, ``serve`` one on the SunSpec bus, or list the ``cases``."""

from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from pathlib import Path

from .run import _read_toml, case_names
from .truth import run_to_dir


def _value(text: str):
    """A ``--set`` value as TOML reads it (number, bool, list); otherwise the text."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def _overrides(parser: argparse.ArgumentParser, sets: list[str]) -> dict:
    out = {}
    for item in sets:
        key, sep, text = item.partition("=")
        if not sep:
            parser.error(f"--set needs key=value, got {item!r}")
        out[key.strip()] = _value(text.strip())
    return out


def _check_out(out: str) -> str | None:
    """Create the folder to write to; the reason when it cannot be used."""
    folder = Path(out)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if any(folder.iterdir()):
            return f"--out {out}: the folder is not empty"
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError as exc:
        return f"--out {out}: {exc.strerror or exc}"
    return None


def _serve(args, overrides: dict) -> int:
    from .bus import server
    from .bus.live import LivePack

    try:
        server.check_pymodbus()
    except server.MissingPymodbus as exc:
        print(exc, file=sys.stderr)
        return 1
    wrong = "--months must be at least 1" if args.months is not None and args.months < 1 else None
    if wrong is None and args.out is not None:
        wrong = _check_out(args.out)
    if wrong is not None:
        print(wrong, file=sys.stderr)
        return 2
    try:
        live = LivePack(args.case, seed=args.seed, overrides=overrides, start=args.start, months=args.months,
                        keep_truth=args.out is not None)
    except (ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1

    def started(bus) -> None:
        print(f"case {live.name}, seed {args.seed}, from {live.time}, {args.speed:g} times the wall clock")
        print(f"SunSpec models 1, 802, 803, 804 on Modbus TCP {bus.host}:{bus.port}, unit id 1")
        print("the bus shows what the BMS acts on, without sensor errors; true capacity, true state of charge and "
              "planted faults are not on it. Ctrl-C stops.", flush=True)
        if bus.host not in ("127.0.0.1", "::1"):
            print("Modbus has no login: whoever reaches this address can command the pack", file=sys.stderr, flush=True)

    try:
        server.serve(live, host=args.host, port=args.port, speed=args.speed, out=args.out, started=started)
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.out is not None:
        print(f"wrote the truth of the live stretch to {args.out}")
    return 0


def _positive(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be above 0")
    return value


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--case", required=True, help="a case name (see `cases`) or the path of a TOML file")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                   help="change one setting, e.g. bms.stop_sigma_v=0.002 (repeatable)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m besslib.sim", description="besslib pack simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run a case and write frames and truth to a folder")
    _common(p)
    p.add_argument("--months", type=int, default=None, help="months to run (default: the case's)")
    p.add_argument("--out", required=True, help="the folder to write to")
    p.add_argument("--true-frames", action="store_true", help="also write the frames without sensor errors")
    p = sub.add_parser("serve", help="run a case live and show its BMS as SunSpec models over Modbus TCP")
    _common(p)
    p.add_argument("--start", default=None, metavar="YYYY-MM-01",
                   help="the first day of a month; the batch engine runs up to it, then the case goes on live")
    p.add_argument("--months", type=int, default=None, help="months of the duty's plan (default: the case's)")
    p.add_argument("--host", default="127.0.0.1",
                   help="the address to listen on (default: %(default)s, this machine only)")
    p.add_argument("--port", type=int, default=5020, help="default: %(default)s")
    p.add_argument("--speed", type=_positive, default=1.0, help="simulated seconds per second (default: 1)")
    p.add_argument("--out", default=None,
                   help="write the truth of the live stretch to this new or empty folder on a clean stop")
    sub.add_parser("cases", help="list the shipped cases")
    args = parser.parse_args(argv)
    if args.command == "cases":
        for name in case_names():
            print(f"{name}: {_read_toml(name, 'cases').get('case', {}).get('description', '')}")
        return 0
    overrides = _overrides(parser, args.sets)
    if args.command == "serve":
        return _serve(args, overrides)
    manifest = run_to_dir(args.case, args.months, args.seed, args.out, overrides, true_frames=args.true_frames)
    print(f"wrote {len(manifest['files'])} files, {manifest['months']} months, to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
