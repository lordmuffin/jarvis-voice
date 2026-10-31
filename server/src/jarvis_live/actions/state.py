"""Server-owned merging of action proposals. The LLM proposes; this module decides what may be
shown for review. Pure (no I/O), so the rules are unit-tested directly.

Nothing here executes anything: a proposal only ever becomes reviewable."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.vault.index import is_excluded

MAX_NEW_PER_CYCLE = 3
MAX_OPEN = 15
MAX_TEXT = 500
MAX_REMINDER_DAYS = 90

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def one_line(text: str | None, n: int = MAX_TEXT) -> str:
    """Collapse whitespace and control characters, so a proposal cannot add Markdown structure
    (a new list item, a heading) to the file it is appended to."""
    return " ".join(_CONTROL.sub(" ", text or "").split())[:n].rstrip()


def payload_sha256(kind: str, payload: dict[str, Any]) -> str:
    """What an approval binds to: the exact content the reviewer saw."""
    canonical = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Context:
    now: datetime
    timezone: str
    vault_has: Callable[[str], bool]  # is this path an indexed note?
    repos: frozenset[str]  # allowlisted ``owner/name``


@dataclass
class Proposal:
    ref: str
    kind: str
    payload: dict[str, Any]
    evidence: list[Evidence]
    dedupe_key: str
    payload_sha256: str
    open: bool = True


def _norm(payload: dict[str, Any]) -> str:
    return " ".join(json.dumps(payload, sort_keys=True, ensure_ascii=False).lower().split())


def _build(p: ProposalIn, ctx: Context) -> dict[str, Any] | None:
    """The validated, canonical payload for ``p``, or None if it must not be shown."""
    if p.kind == "task.create":
        text = one_line(p.text)
        if not text:
            return None
        out: dict[str, Any] = {"text": text}
        if due := one_line(p.due, 10):
            out["due"] = due
        return out
    if p.kind == "vault.append":
        path, text = (p.path or "").strip(), (p.text or "").strip()
        rel = PurePosixPath(path)
        if (
            not text
            or rel.suffix != ".md"
            or rel.is_absolute()
            or ".." in rel.parts
            or is_excluded(rel)
            or not ctx.vault_has(path)
        ):
            return None
        return {"path": path, "text": text[: MAX_TEXT * 4]}
    if p.kind == "reminder.schedule":
        title = one_line(p.title, 120)
        if not title or not p.at:
            return None
        try:
            at = datetime.fromisoformat(p.at)
        except ValueError:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=ZoneInfo(ctx.timezone))
        at = at.astimezone(UTC)
        if at <= ctx.now or at > ctx.now + timedelta(days=MAX_REMINDER_DAYS):
            return None
        out = {"title": title, "at": at.isoformat()}
        if body := one_line(p.body):
            out["body"] = body
        return out
    if p.kind == "git.pr_comment":
        body = (p.body or "").strip()
        if not body or p.pr is None or p.pr < 1 or p.repo not in ctx.repos:
            return None
        return {"repo": p.repo, "pr": p.pr, "body": body[: MAX_TEXT * 4]}
    return None


@dataclass
class ProposalState:
    proposals: list[Proposal] = field(default_factory=list)
    _next: int = 1

    def apply(self, incoming: list[ProposalIn], ctx: Context) -> list[Proposal]:
        """Admit the valid, new proposals; returns those admitted (at most
        ``MAX_NEW_PER_CYCLE`` per call and ``MAX_OPEN`` open overall)."""
        known = {p.dedupe_key for p in self.proposals}
        admitted: list[Proposal] = []
        for p in incoming:
            if len(admitted) >= MAX_NEW_PER_CYCLE:
                break
            if sum(x.open for x in self.proposals) >= MAX_OPEN or not p.evidence:
                continue
            payload = _build(p, ctx)
            if payload is None:
                continue
            key = hashlib.sha256(f"{p.kind}|{_norm(payload)}".encode()).hexdigest()
            if key in known:
                continue
            known.add(key)
            prop = Proposal(
                ref=f"p{self._next}",
                kind=p.kind,
                payload=payload,
                evidence=list(p.evidence),
                dedupe_key=key,
                payload_sha256=payload_sha256(p.kind, payload),
            )
            self._next += 1
            self.proposals.append(prop)
            admitted.append(prop)
        return admitted

    def close(self, ref: str) -> None:
        """Approved, rejected or expired: stays known (so it is not proposed again) but no
        longer counts against the open cap."""
        for p in self.proposals:
            if p.ref == ref:
                p.open = False
