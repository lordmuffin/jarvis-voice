#!/usr/bin/env python3
"""Fail on hard-coded design values in the Android app (and watch app).

The design system (lordmuffin/instrument) owns colours, sizes and type. App code references its
tokens: @color/inst_*, @dimen/inst_*, @style/Inst.*. This check flags the literals that bypass them:

  layouts      colour literals; non-zero dp/sp/px values
  drawables,   colour literals (white/black icon-mask fills in vector paths are allowed)
  values, ...
  Kotlin       Color(0x..), Color.Red etc.; non-zero N.dp / N.sp

Not checked: dp/sp inside values/ styles and drawable shapes, and dimens*.xml, where app-specific
sizes with no token legitimately live.

Escape hatch, for the rare justified exception: put `design-lint:ignore <reason>` in a comment on
the same line or the line above. The vendored `src/instrument/` trees are skipped.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

IGNORE = "design-lint:ignore"
MODULES = ("android/app", "android/wear")

HEX = re.compile(r'(?:="|>)(#[0-9a-fA-F]{3,8})(?:"|<)')
MASK = re.compile(r'fillColor="#(?:[fF]{3}|[fF]{6}|[fF]{8}|0{3}|0{6}|[fF]{2}0{6})"')
DIMEN = re.compile(r'="(-?\d*\.?\d+)(dp|sp|dip|px|pt)"')
KT_COLOUR = re.compile(
    r"\bColor\(\s*0x|\bColor\.(?:Red|Green|Blue|Black|White|Gray|Yellow|Cyan|Magenta)\b"
)
KT_DIMEN = re.compile(r"(?<![\w.])(\d*\.?\d+)\.(?:dp|sp)\b")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    text: str


def _is_zero(num: str) -> bool:
    return float(num) == 0


def _kinds(path: Path, root: Path) -> set[str]:
    rel = path.relative_to(root).parts
    if "instrument" in rel:  # vendored, checked by the Instrument workflow
        return set()
    if path.suffix == ".kt":
        return {"kt"}
    if path.suffix != ".xml" or "res" not in rel:
        return set()
    res_dir = rel[rel.index("res") + 1]
    if res_dir.startswith("layout"):
        return {"colour", "dimen"}
    if res_dir.startswith("values") and path.name.startswith("dimens"):
        return set()
    return {"colour"}


def check_file(path: Path, root: Path) -> list[Violation]:
    kinds = _kinds(path, root)
    if not kinds:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    found: list[Violation] = []
    for i, line in enumerate(lines):
        if IGNORE in line or (i > 0 and IGNORE in lines[i - 1]):
            continue
        hits: list[str] = []
        if "kt" in kinds:
            hits += ["colour"] * len(KT_COLOUR.findall(line))
            hits += ["dimen" for m in KT_DIMEN.finditer(line) if not _is_zero(m.group(1))]
        if "colour" in kinds:
            hits += ["colour" for m in HEX.finditer(line) if not MASK.search(line)]
        if "dimen" in kinds:
            hits += ["dimen" for m in DIMEN.finditer(line) if not _is_zero(m.group(1))]
        found += [Violation(path, i + 1, rule, line.strip()) for rule in hits]
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else ".").resolve()
    violations: list[Violation] = []
    for module in MODULES:
        base = root / module / "src"
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.is_file():
                violations += check_file(path, root)
    for v in violations:
        print(f"{v.path.relative_to(root)}:{v.line}: hard-coded {v.rule}: {v.text}")
    if violations:
        print(
            f"\n{len(violations)} hard-coded design value(s). Use Instrument tokens "
            "(@color/inst_*, @dimen/inst_*); an app-only size goes in values/dimens.xml. "
            "Missing component or token? Propose it for lordmuffin/instrument.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
