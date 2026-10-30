"""Server-owned merging of the ideas pass. The LLM proposes; this module decides.

Pure (no I/O) so the rules are unit-tested directly: refs are server-issued, edited fields are
locked, closed items stay closed, every item cites the transcript, and caps stop a chatty model
from flooding the review queue."""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from jarvis_live.copilot.transcript import Line, mmss
from jarvis_live.workitems.schema import IdeasDelta, ItemUpsert

OPEN = "draft"
CLOSED = ("approved", "discarded", "merged", "done")
EDITABLE_FIELDS = ("title", "repo", "intent", "acceptance_criteria", "done_when")

MAX_OPEN_ITEMS = 20
MAX_CRITERIA = 8
MAX_SOURCES = 40
MAX_TITLE = 120
MAX_INTENT = 2000
MAX_CRITERION = 300
MAX_DONE_WHEN = 300

_REF = re.compile(r"^w(\d+)$")
_LINE = re.compile(r"^\s*l?\s*(\d+)\s*$", re.IGNORECASE)


def norm_title(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def clip(text: str | None, n: int) -> str:
    return " ".join((text or "").split())[:n].rstrip()


def clip_block(text: str | None, n: int) -> str:
    """Keep paragraph breaks (intent is prose), trim the rest."""
    paras = [" ".join(p.split()) for p in (text or "").split("\n\n")]
    return "\n\n".join(p for p in paras if p)[:n].rstrip()


def clean_criteria(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in items:
        c = clip(c, MAX_CRITERION).lstrip("-*[] ").strip()
        if c and norm_title(c) not in seen:
            seen.add(norm_title(c))
            out.append(c)
    return out[:MAX_CRITERIA]


@dataclass
class Source:
    segment_id: str
    start_ms: int
    end_ms: int
    text: str

    @classmethod
    def of(cls, line: Line) -> "Source":
        return cls(str(line.segment_id), line.start_ms, line.end_ms, line.text)

    def dump(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }


@dataclass
class Item:
    """A work item as the merge sees it. ``id`` is None until it is first stored."""

    ref: str
    title: str
    state: str = OPEN
    repo: str | None = None
    intent: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    done_when: str | None = None
    sources: list[Source] = field(default_factory=list)
    locked_fields: list[str] = field(default_factory=list)
    id: uuid.UUID | None = None


@dataclass
class Change:
    item: Item
    created: bool
    fields: list[str]  # what changed ("sources" included)


def number_lines(lines: list[Line]) -> tuple[str, dict[str, Source]]:
    """The transcript as ``L1 [mm:ss] me: text`` lines, and ``L1`` -> source."""
    out: list[str] = []
    refs: dict[str, Source] = {}
    for i, x in enumerate(lines, 1):
        out.append(f"L{i} [{mmss(x.start_ms)}] {x.speaker}: {x.text}")
        refs[f"L{i}"] = Source.of(x)
    return "\n".join(out), refs


class IdeasState:
    def __init__(self, items: list[Item], allowlist: dict[str, str]) -> None:
        self.items = {it.ref: it for it in items}
        self.allowlist = allowlist

    def _next_ref(self) -> str:
        n = max((int(m.group(1)) for r in self.items if (m := _REF.match(r))), default=0)
        return f"w{n + 1}"

    def _repo(self, repo: str | None) -> str | None:
        repo = (repo or "").strip()
        return repo if repo in self.allowlist else None

    def apply(self, delta: IdeasDelta, line_refs: dict[str, Source]) -> list[Change]:
        changes: dict[str, Change] = {}
        for up in delta.items_upsert:
            srcs = [line_refs[k] for k in (_line_key(r) for r in up.source_lines) if k in line_refs]
            item = self.items.get(up.ref or "")
            if item is None:
                item = self._match_open(up.title)
            if item is None:
                if not srcs or not self._may_create(up.title):
                    continue
                item = Item(ref=self._next_ref(), title=clip(up.title, MAX_TITLE))
                self.items[item.ref] = item
                fields = self._update(item, up, srcs, creating=True)
                changes[item.ref] = Change(item, True, fields)
                continue
            if item.state != OPEN:
                continue  # approved and closed items are the owner's now
            fields = self._update(item, up, srcs, creating=False)
            if fields:
                prev = changes.get(item.ref)
                if prev is None:
                    changes[item.ref] = Change(item, False, fields)
                else:
                    prev.fields = sorted(set(prev.fields) | set(fields))
        return list(changes.values())

    def _match_open(self, title: str) -> Item | None:
        """A proposal titled like an open draft refines that draft instead of duplicating it."""
        key = norm_title(title)
        return next(
            (it for it in self.items.values() if it.state == OPEN and norm_title(it.title) == key),
            None,
        )

    def _may_create(self, title: str) -> bool:
        key = norm_title(title)
        if not key:
            return False
        if any(norm_title(it.title) == key for it in self.items.values() if it.state in CLOSED):
            return False  # proposed again after the owner closed it
        return sum(it.state == OPEN for it in self.items.values()) < MAX_OPEN_ITEMS

    def _update(
        self, item: Item, up: ItemUpsert, srcs: list[Source], *, creating: bool
    ) -> list[str]:
        changed: list[str] = []
        locked = set(item.locked_fields)

        def put(name: str, value: Any) -> None:
            if name in locked or value == getattr(item, name):
                return
            setattr(item, name, value)
            changed.append(name)

        if title := clip(up.title, MAX_TITLE):
            put("title", title)
        if (repo := self._repo(up.repo)) is not None:
            put("repo", repo)  # an off-list or missing repo never clears a valid one
        if intent := clip_block(up.intent, MAX_INTENT):
            put("intent", intent)
        if criteria := clean_criteria(up.acceptance_criteria):
            put("acceptance_criteria", criteria)
        if done := clip(up.done_when, MAX_DONE_WHEN):
            put("done_when", done)

        have = {s.segment_id for s in item.sources}
        new = list({s.segment_id: s for s in srcs if s.segment_id not in have}.values())
        if new:
            merged = sorted([*item.sources, *new], key=lambda s: (s.start_ms, s.end_ms))
            item.sources = merged[:MAX_SOURCES]
            if len(item.sources) != len(have):
                changed.append("sources")
        if creating and "title" not in changed:
            changed.insert(0, "title")
        return changed


def _line_key(ref: str) -> str:
    m = _LINE.match(ref)
    return f"L{int(m.group(1))}" if m else ref
