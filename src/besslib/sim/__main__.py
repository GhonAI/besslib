"""``python -m besslib.sim``: ``run`` a case into a folder, or list the ``cases``."""

from __future__ import annotations

import argparse
import tomllib

from .run import _read_toml, case_names
from .truth import run_to_dir


def _value(text: str):
    """A ``--set`` value as TOML reads it (number, bool, list); otherwise the text."""
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m besslib.sim", description="besslib pack simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run a case and write frames and truth to a folder")
    p.add_argument("--case", required=True, help="a case name (see `cases`) or the path of a TOML file")
    p.add_argument("--months", type=int, default=None, help="months to run (default: the case's)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True, help="the folder to write to")
    p.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                   help="change one setting, e.g. bms.stop_sigma_v=0.002 (repeatable)")
    p.add_argument("--true-frames", action="store_true", help="also write the frames without sensor errors")
    sub.add_parser("cases", help="list the shipped cases")
    args = parser.parse_args(argv)
    if args.command == "cases":
        for name in case_names():
            print(f"{name}: {_read_toml(name, 'cases').get('case', {}).get('description', '')}")
        return 0
    overrides = {}
    for item in args.sets:
        key, sep, text = item.partition("=")
        if not sep:
            parser.error(f"--set needs key=value, got {item!r}")
        overrides[key.strip()] = _value(text.strip())
    manifest = run_to_dir(args.case, args.months, args.seed, args.out, overrides, true_frames=args.true_frames)
    print(f"wrote {len(manifest['files'])} files, {manifest['months']} months, to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
