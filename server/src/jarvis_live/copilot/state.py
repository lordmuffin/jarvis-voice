"""Server-owned copilot state. The LLM proposes deltas; this class owns ids and versions."""

import json
import re
from dataclasses import dataclass, field

from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.protocol import Action, Copilot, Note, Related, Suggestion

MAX_SUGGESTIONS = 5  # oldest are dropped first so a chatty model cannot flood the UI
_ID = re.compile(r"^([nads])(\d+)$")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass
class CopilotState:
    version: int = 0
    notes: list[Note] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    decisions: list[Note] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    related: list[Related] = field(default_factory=list)
    shown: set[str] = field(default_factory=set)  # vault paths already surfaced this session
    _next: dict[str, int] = field(default_factory=lambda: {"n": 1, "a": 1, "d": 1, "s": 1})

    @classmethod
    def from_snapshot(cls, snap: Copilot, shown: set[str] | None = None) -> "CopilotState":
        st = cls(
            version=snap.version,
            notes=list(snap.notes),
            actions=list(snap.actions),
            decisions=list(snap.decisions),
            suggestions=list(snap.suggestions),
            related=list(snap.related),
            shown=set(shown or ()) | {r.path for r in snap.related},
        )
        ids = [x.id for x in (*st.notes, *st.decisions)]
        ids += [x.id for x in st.actions] + [x.id for x in st.suggestions]
        for id_ in ids:
            if m := _ID.match(id_):
                st._next[m.group(1)] = max(st._next[m.group(1)], int(m.group(2)) + 1)
        return st

    def _new_id(self, prefix: str) -> str:
        n = self._next[prefix]
        self._next[prefix] = n + 1
        return f"{prefix}{n}"

    def snapshot(self) -> Copilot:
        return Copilot(
            version=self.version,
            notes=list(self.notes),
            actions=list(self.actions),
            decisions=list(self.decisions),
            suggestions=list(self.suggestions),
            related=list(self.related),
        )

    # --- mutation ---------------------------------------------------------------------------

    def expire(self, now_ms: int) -> bool:
        """Drop suggestions past their expiry. Returns True if any were dropped."""
        keep = [s for s in self.suggestions if s.expires_at_ms > now_ms]
        changed = len(keep) != len(self.suggestions)
        self.suggestions = keep
        if changed:
            self.version += 1
        return changed

    def apply(self, delta: CopilotDelta, now_ms: int, default_ttl_s: int = 120) -> bool:
        """Apply ``delta``; bump ``version`` and return True iff anything changed.

        An id the state knows updates that item in place. A missing id, or one the state has
        never issued, creates a new item with a server-assigned id. An upsert whose text equals
        an existing item (and carries no other change) is a no-op, not a duplicate."""
        before = self.snapshot().model_dump()

        for n in delta.notes_upsert:
            self._upsert_note(self.notes, "n", n.id, n.text)
        for d in delta.decisions_upsert:
            self._upsert_note(self.decisions, "d", d.id, d.text)
        for a in delta.actions_upsert:
            self._upsert_action(a.id, a.text, a.owner, a.due)

        if delta.remove_ids:
            gone = set(delta.remove_ids)
            self.notes = [x for x in self.notes if x.id not in gone]
            self.decisions = [x for x in self.decisions if x.id not in gone]
            self.actions = [x for x in self.actions if x.id not in gone]
            self.suggestions = [x for x in self.suggestions if x.id not in gone]

        live = {_norm(s.text) for s in self.suggestions}
        for s in delta.suggestions:
            if _norm(s.text) in live:
                continue
            live.add(_norm(s.text))
            ttl = s.ttl_s or default_ttl_s
            self.suggestions.append(
                Suggestion(
                    id=self._new_id("s"),
                    kind=s.kind,
                    text=s.text.strip(),
                    expires_at_ms=now_ms + ttl * 1000,
                )
            )
        self.suggestions = self.suggestions[-MAX_SUGGESTIONS:]

        changed = self.snapshot().model_dump() != before
        if changed:
            self.version += 1
        return changed

    def set_related(self, hits: list[Related]) -> bool:
        if not hits:
            return False
        self.related = list(hits)
        self.shown.update(h.path for h in hits)
        self.version += 1
        return True

    def _upsert_note(self, items: list[Note], prefix: str, id_: str | None, text: str) -> None:
        text = text.strip()
        for i, existing in enumerate(items):
            if id_ is not None and existing.id == id_:
                items[i] = Note(id=existing.id, text=text)
                return
        if any(_norm(x.text) == _norm(text) for x in items):
            return
        items.append(Note(id=self._new_id(prefix), text=text))

    def _upsert_action(
        self, id_: str | None, text: str, owner: str | None, due: str | None
    ) -> None:
        text, owner, due = text.strip(), (owner or "").strip() or None, (due or "").strip() or None
        for i, existing in enumerate(self.actions):
            if id_ is not None and existing.id == id_:
                self.actions[i] = Action(
                    id=existing.id,
                    text=text,
                    owner=owner or existing.owner,
                    due=due or existing.due,
                )
                return
        for i, existing in enumerate(self.actions):
            if _norm(existing.text) == _norm(text):  # same action restated: refresh owner/due
                self.actions[i] = Action(
                    id=existing.id,
                    text=existing.text,
                    owner=owner or existing.owner,
                    due=due or existing.due,
                )
                return
        self.actions.append(Action(id=self._new_id("a"), text=text, owner=owner, due=due))

    # --- prompt view ------------------------------------------------------------------------

    def prompt_json(self) -> str:
        def item(x: Note | Action) -> dict[str, str]:
            return x.model_dump(exclude_none=True)

        return json.dumps(
            {
                "notes": [item(x) for x in self.notes],
                "decisions": [item(x) for x in self.decisions],
                "actions": [item(x) for x in self.actions],
                "active_suggestions": [s.text for s in self.suggestions],
            },
            ensure_ascii=False,
        )
