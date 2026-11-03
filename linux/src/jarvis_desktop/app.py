"""Entry point for ``jarvis-voice``."""

import argparse
from collections.abc import Sequence

from jarvis_desktop import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-voice")
    parser.add_argument("--version", action="version", version=f"jarvis-voice {__version__}")
    parser.parse_args(argv)
    return 0
