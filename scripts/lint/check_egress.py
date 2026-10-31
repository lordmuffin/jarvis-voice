#!/usr/bin/env python3
"""Fail on network connections opened outside the files that honour the local-only gate.

While a local-only session records, nothing may leave the device. The Mac app enforces that in
one place (`EgressGate`): `LiveAPIClient`, `LiveConnection`, `NetworkPlaceDetector` and
`ServerInfo` ask it before they send. A new feature that builds its own `URLSession` would bypass
it, so this check lists the files allowed to open connections and fails on any other.

  Swift    URLSession(...), URLSession.shared, NWConnection(...)
  Kotlin   OkHttpClient, HttpURLConnection, openConnection(...)

Add a file to the allow-list only after it checks the gate. Android's gate is not built yet, so
its Live files are listed as pending, and the files that carry no Live session content (chat,
model downloads, notifications) are listed as outside the toggle. Escape hatch for one line:
`egress-lint:ignore <reason>`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

IGNORE = "egress-lint:ignore"

SWIFT_RULES = (
    ("url-session", re.compile(r"\bURLSession\s*\(|\bURLSession\.shared\b")),
    ("raw-connection", re.compile(r"\bNWConnection\s*\(")),
)
KOTLIN_RULES = (
    ("http-client", re.compile(r"\bOkHttpClient\b|\bHttpURLConnection\b|\.openConnection\s*\(")),
)

ALLOWED = {
    # Mac: these ask EgressGate before they send.
    "macos/JarvisLiveKit/Sources/JarvisLiveKit/Transport/LiveConnection.swift",
    "macos/JarvisLiveKit/Sources/JarvisLiveKit/Transport/LiveAPIClient.swift",
    "macos/JarvisLiveKit/Sources/JarvisLiveKit/Network/NetworkPlaceDetector.swift",
    "macos/JarvisLiveKit/Sources/JarvisLiveKit/Network/ServerChannel.swift",
    # Android Live path: gate pending (docs/plans/local-only-audit.md).
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/live/LiveConnection.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/live/LiveApiClient.kt",
    "android/wearcommon/src/main/java/com/lordmuffin/jarvisvoice/wearcommon/RecordingUploader.kt",
    # Android, outside the Live local-only toggle (audit finding F10).
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/ModelDownloadWorker.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/chat/AgentTaskRepository.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/chat/LlmRepository.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/chat/NetworkTtsRepository.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/notify/NotificationRepository.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/speech/ModelDownloader.kt",
    "android/app/src/main/java/com/lordmuffin/jarvisvoice/speech/SttDownloadWorker.kt",
}
SKIP_PARTS = {"build", ".build", "Tests", "test", "androidTest", "Instrument", "instrument"}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    rule: str
    text: str


def check_file(path: Path, root: Path) -> list[Violation]:
    rel = path.relative_to(root).as_posix()
    if rel in ALLOWED or SKIP_PARTS & set(path.relative_to(root).parts):
        return []
    rules = SWIFT_RULES if path.suffix == ".swift" else KOTLIN_RULES
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = []
    for n, line in enumerate(lines, 1):
        if IGNORE in line or (n > 1 and IGNORE in lines[n - 2]):
            continue
        found += [Violation(path, n, name, line.strip()) for name, rx in rules if rx.search(line)]
    return found


def check_tree(root: Path) -> list[Violation]:
    files = [
        *(root / "macos").rglob("*.swift"),
        *(root / "android").rglob("*.kt"),
    ]
    return [v for f in sorted(files) for v in check_file(f, root)]


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    bad = check_tree(root)
    for v in bad:
        print(f"{v.path.relative_to(root)}:{v.line}: {v.rule}: {v.text}")
    if bad:
        print(
            f"\n{len(bad)} network connection(s) opened outside the local-only gate. Go through "
            "EgressGate (see scripts/lint/check_egress.py), or add a justified "
            f"`{IGNORE} <reason>`.",
            file=sys.stderr,
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
