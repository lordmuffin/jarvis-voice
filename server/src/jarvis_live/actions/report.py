"""Proposal precision: of the actions the copilot proposed, how many the owner accepts.

Reported, never enforced. The production signal is the owner's own decisions (approved,
rejected, left to expire); the offline eval (tests/evals) uses the same arithmetic over
labelled transcripts."""

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import ActionProposal, ActionProposalEvent

Verdict = Literal["accept", "accept_edited", "reject"]
Status = Literal["accept", "accept_edited", "reject", "ignored", "pending"]

_KEY_FIELDS = {
    "task.create": ("text",),
    "vault.append": ("path", "text"),
    "reminder.schedule": ("title", "body"),  # not `at`: the same ask names a different day each run
    "git.pr_comment": ("repo", "pr", "body"),
}


def label_key(kind: str, payload: dict[str, Any]) -> str:
    """A proposal's identity for labelling: its kind and what it says, ignoring case, spacing and
    (for reminders) the clock time."""
    parts = " ".join(str(payload.get(f) or "") for f in _KEY_FIELDS.get(kind, ())).lower().split()
    return hashlib.sha1(f"{kind}|{' '.join(parts)}".encode(), usedforsecurity=False).hexdigest()[
        :12
    ]


@dataclass
class KindStats:
    accepted: int = 0
    accepted_edited: int = 0
    rejected: int = 0

    @property
    def decided(self) -> int:
        return self.accepted + self.accepted_edited + self.rejected

    @property
    def precision(self) -> float | None:
        return (self.accepted + self.accepted_edited) / self.decided if self.decided else None


@dataclass
class PrecisionReport:
    made: int = 0
    accepted: int = 0  # as proposed
    accepted_edited: int = 0  # after an edit
    rejected: int = 0
    ignored: int = 0  # expired unreviewed
    pending: int = 0  # not decided yet
    by_kind: dict[str, KindStats] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return self.accepted + self.accepted_edited + self.rejected

    @property
    def precision(self) -> float | None:
        """Accepted (edited or not) of those decided: the share the owner would accept."""
        return (self.accepted + self.accepted_edited) / self.decided if self.decided else None

    @property
    def strict_precision(self) -> float | None:
        """Accepted exactly as proposed."""
        return self.accepted / self.decided if self.decided else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "made": self.made,
            "accepted": self.accepted,
            "accepted_edited": self.accepted_edited,
            "rejected": self.rejected,
            "ignored": self.ignored,
            "pending": self.pending,
            "precision": self.precision,
            "strict_precision": self.strict_precision,
            "by_kind": {
                k: {
                    "accepted": s.accepted,
                    "accepted_edited": s.accepted_edited,
                    "rejected": s.rejected,
                    "precision": s.precision,
                }
                for k, s in sorted(self.by_kind.items())
            },
        }

    def render(self) -> list[str]:
        out = [
            f"action proposals: {self.made} made · {self.accepted} accepted "
            f"({self.accepted_edited} after an edit) · {self.rejected} rejected · "
            f"{self.ignored} ignored · {self.pending} pending"
        ]
        if self.precision is None or self.strict_precision is None:
            return [*out, "no decisions yet"]
        out.append(
            f"precision {self.precision:.1%} ({self.accepted + self.accepted_edited} of "
            f"{self.decided} decided) · strict {self.strict_precision:.1%} (without an edit)"
        )
        for kind, s in sorted(self.by_kind.items()):
            if s.precision is not None:
                out.append(
                    f"  {kind:<18} {s.precision:>6.1%}  "
                    f"({s.accepted + s.accepted_edited} of {s.decided})"
                )
        return out


def summarize(rows: Iterable[tuple[str, Status]]) -> PrecisionReport:
    r = PrecisionReport()
    for kind, status in rows:
        r.made += 1
        stats = r.by_kind.setdefault(kind, KindStats())
        if status == "accept":
            r.accepted += 1
            stats.accepted += 1
        elif status == "accept_edited":
            r.accepted_edited += 1
            stats.accepted_edited += 1
        elif status == "reject":
            r.rejected += 1
            stats.rejected += 1
        elif status == "ignored":
            r.ignored += 1
        else:
            r.pending += 1
    return r


async def production_rows(
    sm: "async_sessionmaker[AsyncSession]",
    *,
    user_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> list[tuple[str, Status]]:
    """What the owner did with each proposal. Approving is acceptance, whatever the execution
    then did; an edit before approving makes it ``accept_edited``."""
    async with sm() as db:
        q = select(ActionProposal.id, ActionProposal.kind, ActionProposal.state)
        if user_id is not None:
            q = q.where(ActionProposal.user_id == user_id)
        if since is not None:
            q = q.where(ActionProposal.created_at >= since)
        found = (await db.execute(q.order_by(ActionProposal.created_at))).all()
        edited = set(
            (
                await db.scalars(
                    select(ActionProposalEvent.proposal_id).where(
                        ActionProposalEvent.event == "edited",
                        ActionProposalEvent.proposal_id.in_([r.id for r in found]),
                    )
                )
            ).all()
        )
    rows: list[tuple[str, Status]] = []
    for pid, kind, state in found:
        status: Status
        if state == "rejected":
            status = "reject"
        elif state == "expired":
            status = "ignored"
        elif state == "proposed":
            status = "pending"
        else:  # approved, executing, succeeded, failed
            status = "accept_edited" if pid in edited else "accept"
        rows.append((kind, status))
    return rows


def dump(report: PrecisionReport) -> str:
    return json.dumps(report.to_dict(), indent=2)
