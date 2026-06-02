"""Markdown emitter for voice-capture notes. Caller supplies the `source` label."""
from __future__ import annotations

import datetime as _dt
import os
from typing import Mapping

VAULT_INBOX = os.environ.get(
    "VAULT_INBOX",
    "/home/lordmuffin/Documents/Notes/00 Inbox/Voice Notes",
)


def voice_note_basename(timestamp: _dt.datetime, source: str) -> str:
    """Filename stem (without extension) used for a voice note + its audio."""
    ts = timestamp
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    return ts.strftime(f"%Y-%m-%d - %H-%M - {source}")


def write_voice_note(
    transcript: str,
    duration_seconds: float,
    source: str,
    *,
    timestamp: _dt.datetime | None = None,
    model: str | None = "whisper-small.en",
    extra_frontmatter: Mapping[str, object] | None = None,
    audio_file: str | None = None,
    transcribe_seconds: float | None = None,
) -> str:
    """Write a voice-capture markdown note to the vault and return its path."""
    ts = timestamp or _dt.datetime.now()
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)

    fname = voice_note_basename(ts, source) + ".md"

    frontmatter: dict[str, object] = {
        "created": ts.strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "tags": "[voice-capture, inbox]",
        "duration_seconds": int(duration_seconds),
    }
    if transcribe_seconds is not None:
        frontmatter["transcribe_seconds"] = round(float(transcribe_seconds), 1)
    if audio_file:
        frontmatter["audio_file"] = audio_file
    if model:
        frontmatter["model"] = model
    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            if v is not None:
                frontmatter[k] = v

    lines: list[str] = ["---"]
    lines += [f"{k}: {v}" for k, v in frontmatter.items()]
    lines += ["---", ""]
    lines += [f"# Voice Note — {ts.strftime('%Y-%m-%d %H:%M')}", ""]

    meta_parts = [f"**Source:** {source}", f"**Duration:** {int(duration_seconds)}s"]
    if model:
        meta_parts.append(f"**Model:** {model}")
    if transcribe_seconds is not None:
        meta_parts.append(f"**Transcribe Time:** {round(float(transcribe_seconds), 1)}s")
    lines += ["  ".join(meta_parts), ""]
    if audio_file:
        lines += [f"![[{audio_file}]]", ""]
    lines += ["---", "", transcript.strip(), "", "---", "#voice-capture #inbox", ""]

    os.makedirs(VAULT_INBOX, exist_ok=True)
    path = os.path.join(VAULT_INBOX, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
