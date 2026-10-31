"""The actions and decisions a finished note lists, read back from its Markdown.

The live copilot may never have run for a session (an upload, a short or solo recording), so
its snapshot can be empty while the note lists actions. Reading the note itself, rather than
what the finalizer once produced, also picks up edits made in the dashboard."""

import re

from jarvis_live.protocol import Action, Copilot, Note

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_ITEM = re.compile(r"^[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
_EXTRA = re.compile(r"^(.*?)\s*\(((?:owner|due):[^()]*)\)$")


def _section(markdown: str, title: str) -> list[str]:
    """The top-level bullet texts under ``## title``, up to the next heading."""
    out: list[str] = []
    inside = False
    for line in markdown.splitlines():
        if m := _HEADING.match(line):
            inside = m[1].lower() == title.lower()
            continue
        if inside and (item := _ITEM.match(line)):
            out.append(item[1])
    return out


def _action(text: str) -> tuple[str, str | None, str | None]:
    """``text (owner: X, due: Y)`` as the finalizer renders it -> (text, owner, due)."""
    owner = due = None
    if m := _EXTRA.match(text):
        text = m[1]
        for part in m[2].split(","):
            key, _, value = part.partition(":")
            if key.strip() == "owner":
                owner = value.strip() or None
            elif key.strip() == "due":
                due = value.strip() or None
    return text, owner, due


def note_items(markdown: str) -> tuple[list[Action], list[str]]:
    actions = [
        Action(id=f"note-action-{i}", text=t, owner=o, due=d)
        for i, (t, o, d) in enumerate(_action(x) for x in _section(markdown, "Actions"))
    ]
    return actions, _section(markdown, "Decisions")


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())


def merge_note_items(copilot: Copilot, markdown: str | None) -> Copilot:
    """``copilot`` plus the note's actions and decisions it does not already have."""
    if not markdown:
        return copilot
    actions, decisions = note_items(markdown)
    have = {_key(a.text) for a in copilot.actions}
    new_actions = [a for a in actions if _key(a.text) not in have]
    have = {_key(d.text) for d in copilot.decisions}
    new_decisions = [
        Note(id=f"note-decision-{i}", text=d)
        for i, d in enumerate(decisions)
        if _key(d) not in have
    ]
    if not new_actions and not new_decisions:
        return copilot
    return copilot.model_copy(
        update={
            "actions": [*copilot.actions, *new_actions],
            "decisions": [*copilot.decisions, *new_decisions],
        }
    )
