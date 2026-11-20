"""Readers that turn measurement files into besslib frames."""

from besslib.io import figgener
from besslib.io.figgener import read_figgener, read_sample

__all__ = ["figgener", "read_figgener", "read_sample"]
