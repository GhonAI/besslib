"""besslib: state of health from battery storage operating data."""

from importlib import metadata as _metadata

from besslib import frames, io, soh
from besslib.frames import PackSpec

try:
    __version__ = _metadata.version("besslib")
except _metadata.PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "unknown"
__all__ = ["PackSpec", "frames", "io", "soh", "__version__"]
