#!/usr/bin/env python3
"""Ratchet check (TDD.md §7 Phase 4): fail if VALUE drops below the number in BASELINE_FILE.

    ratchet.py BASELINE_FILE VALUE            # check
    ratchet.py BASELINE_FILE VALUE --update   # check, then raise the baseline if VALUE is higher

VALUE is a number, or FILE.json:a.b.c to read a number out of a JSON report.

The baseline only ever goes up here; lowering it by hand needs an approval.
"""

import json
import math
import sys
from pathlib import Path


def read_value(raw: str) -> float:
    if ".json:" not in raw:
        return float(raw)
    file, _, keys = raw.partition(".json:")
    node = json.loads(Path(file + ".json").read_text())
    for key in keys.split("."):
        node = node[key]
    return float(node)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    path, value = Path(argv[1]), math.floor(read_value(argv[2]) * 10) / 10
    baseline = float(path.read_text().strip())
    if value < baseline:
        print(f"RATCHET FAIL {path}: {value} < baseline {baseline}")
        return 1
    print(f"ratchet ok {path}: {value} >= baseline {baseline}")
    if "--update" in argv[3:] and value > baseline:
        path.write_text(f"{value}\n")
        print(f"baseline raised to {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
