"""Markdown rendering for finished notes."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.copilot.transcript import Line, hhmmss

_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+/-]*$")
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


@dataclass(frozen=True)
class NoteMeta:
    created: datetime  # timezone-aware, in the display timezone
    session_id: str
    device: str
    mode: str
    duration_minutes: int


def _yaml(value: str) -> str:
    # JSON strings are valid YAML double-quoted scalars.
    return (
        value
        if _PLAIN.match(value) and value.lower() not in {"true", "false", "null", "yes", "no"}
        else json.dumps(value)
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _action(a: FinalAction) -> str:
    extra = ", ".join(
        p
        for p in (
            f"owner: {_one_line(a.owner)}" if a.owner else "",
            f"due: {_one_line(a.due)}" if a.due else "",
        )
        if p
    )
    return f"- [ ] {_one_line(a.text)}" + (f" ({extra})" if extra else "")


def render_note(note: FinalNote, meta: NoteMeta, transcript: list[Line]) -> str:
    out = [
        "---",
        f"created: {meta.created.isoformat(timespec='seconds')}",
        "type: live-session",
        "source: jarvis-live",
        f"session_id: {meta.session_id}",
        f"device: {_yaml(meta.device)}",
        f"mode: {meta.mode}",
        f"duration_minutes: {meta.duration_minutes}",
        "tags: [live-session, inbox]",
        "---",
        "",
        f"# {_one_line(note.title)}",
        "",
        "## Summary",
        "",
        note.summary.strip(),
        "",
    ]

    def section(title: str, items: list[str]) -> None:
        if items:
            out.extend([f"## {title}", "", *items, ""])

    section("Decisions", [f"- {_one_line(d)}" for d in note.decisions])
    section("Actions", [_action(a) for a in note.actions])
    section("Open questions", [f"- {_one_line(q)}" for q in note.open_questions])
    section("Follow-ups", [f"- {_one_line(f)}" for f in note.follow_ups])
    section("Related", [f"- [[{_one_line(r)}]]" for r in note.related])
    out.extend(["## Transcript", ""])
    out.extend(
        f"[{hhmmss(x.start_ms)}] **{'Me' if x.speaker == 'me' else 'Them'}:** {_one_line(x.text)}"
        for x in transcript
    )
    return "\n".join(out) + "\n"


def slugify(title: str, max_len: int = 60) -> str:
    slug = _one_line(_UNSAFE_FILENAME.sub(" ", title)).strip(" .")
    return slug[:max_len].rstrip(" .") or "untitled"


def render_filename(template: str, created: datetime, title: str, session_id: str) -> str:
    name = template.format(
        date=created.strftime("%Y-%m-%d"),
        time=created.strftime("%H%M"),
        slug=slugify(title),
        session_id=session_id,
    )
    return _UNSAFE_FILENAME.sub("-", name.replace("/", "-"))
