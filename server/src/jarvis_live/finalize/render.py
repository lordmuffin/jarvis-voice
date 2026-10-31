"""Markdown rendering for finished notes."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.copilot.transcript import Line, hhmmss, mmss

_PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+/-]*$")
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


@dataclass(frozen=True)
class NoteMeta:
    created: datetime  # timezone-aware, in the display timezone
    session_id: str
    device: str
    mode: str
    duration_minutes: int
    user: str | None = None
    source: str | None = None  # where an uploaded recording was made, e.g. "watch"


@dataclass(frozen=True)
class NoteWorkItem:
    """A work item as the note shows it. Changes after the note was written live on the
    dashboard (``review_url``); regenerating the note picks them up."""

    title: str
    repo: str | None
    state: str  # draft | approved
    intent: str
    criteria: list[str]
    sources: list[tuple[int, str]]  # (start_ms, link; "" if there is no public URL)
    review_url: str
    prompt: str | None = None  # the frozen prompt of an approved item


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


def _work_item(w: NoteWorkItem) -> list[str]:
    head = [f"`{w.repo}`" if w.repo else "no repository yet", w.state]
    if w.review_url:
        head.append(f"[review]({w.review_url})")
    out = [f"### {_one_line(w.title)}", "", " · ".join(head), ""]
    if w.intent.strip():
        out += [w.intent.strip(), ""]
    if w.criteria:
        out += [*(f"- [ ] {_one_line(c)}" for c in w.criteria), ""]
    if w.sources:
        links = [f"[{mmss(t)}]({url})" if url else mmss(t) for t, url in w.sources]
        out += ["Heard at " + ", ".join(links), ""]
    if w.prompt:
        out.append("> [!abstract]- Claude Code prompt")
        out += [f"> {line}".rstrip() for line in w.prompt.rstrip().splitlines()]
        out.append("")
    return out


def render_note(
    note: FinalNote,
    meta: NoteMeta,
    transcript: list[Line],
    work_items: list[NoteWorkItem] | None = None,
    timing: list[str] | None = None,
) -> str:
    # The vault's AI Output template (Notes: 30 Resources/Templates/AI Output Template.md),
    # then this session's details.
    out = [
        "---",
        "type: ai-output",
        f"date: {meta.created.date().isoformat()}",
        f"created: {meta.created.isoformat(timespec='seconds')}",
        "status: draft",
        "generated-by: jarvis-live",
        f"session_id: {meta.session_id}",
        f"device: {_yaml(meta.device)}",
        *([f"user: {_yaml(meta.user)}"] if meta.user else []),
        f"mode: {meta.mode}",
        *([f"source: {_yaml(meta.source)}"] if meta.source else []),
        f"duration_minutes: {meta.duration_minutes}",
        "tags: [ai-output, jarvis-live, inbox]",
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

    section("Actions", [_action(a) for a in note.actions])
    section("Decisions", [f"- {_one_line(d)}" for d in note.decisions])
    section("Open questions", [f"- {_one_line(q)}" for q in note.open_questions])
    section("Follow-ups", [f"- {_one_line(f)}" for f in note.follow_ups])
    if work_items:
        out.extend(["## Work items", ""])
        for w in work_items:
            out.extend(_work_item(w))
    section("Related", [f"- [[{_one_line(r)}]]" for r in note.related])
    if timing:
        out.extend([*timing])
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
