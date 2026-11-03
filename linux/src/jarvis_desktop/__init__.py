"""Jarvis Voice for the Linux desktop."""

from importlib.metadata import version

# pyproject.toml is the one place the version lives (release-please bumps it).
__version__ = version("jarvis-desktop")
