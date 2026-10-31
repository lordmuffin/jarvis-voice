"""Where proposals from the live copilot and the finalizer enter the review queue.

One ``ProposalState`` per session, shared by both producers, so a proposal the copilot made at
minute 10 is not made again by the finalizer, and refs never collide. The state is rebuilt from
the database after a restart. Nothing here executes anything."""

import asyncio
import uuid
from collections.abc import Callable, Sequence

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.service import ActionService
from jarvis_live.actions.state import Context, Proposal, ProposalState, ground
from jarvis_live.copilot.transcript import Line
from jarvis_live.db.models import ActionProposal


def _label(kind: str, payload: dict[str, object]) -> str:
    text = payload.get("text") or payload.get("title") or payload.get("body") or ""
    return f"{kind}: {text}"


class ActionIntake:
    def __init__(self, service: ActionService, context: Callable[[], Context]) -> None:
        self.service = service
        self._context = context
        self._states: dict[uuid.UUID, ProposalState] = {}
        self._lock = asyncio.Lock()

    def for_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> "SessionProposals":
        return SessionProposals(self, session_id, user_id)

    async def _state(self, session_id: uuid.UUID, user_id: uuid.UUID) -> ProposalState:
        if session_id not in self._states:
            rows = await self.service.for_session(session_id, user_id)
            self._states[session_id] = ProposalState.restore([_proposal(r) for r in rows])
        return self._states[session_id]


def _proposal(r: ActionProposal) -> Proposal:
    return Proposal(
        ref=r.ref,
        kind=r.kind,
        payload=dict(r.payload),
        evidence=[Evidence(**e) for e in r.evidence],
        dedupe_key=r.dedupe_key,
        payload_sha256=r.payload_sha256,
        open=r.state == "proposed",
    )


class SessionProposals:
    """One session's door into the queue: what the loop and the finalizer hold."""

    def __init__(self, intake: ActionIntake, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._intake, self._sid, self._uid = intake, session_id, user_id

    async def propose(
        self, incoming: Sequence[ProposalIn], lines: Sequence[Line], *, proposed_by: str
    ) -> int:
        """File the valid, new, transcript-backed proposals for review; returns how many."""
        grounded = ground(incoming, lines)
        if not grounded:
            return 0
        async with self._intake._lock:  # one admit at a time: refs and dedupe stay consistent
            state = await self._intake._state(self._sid, self._uid)
            admitted = state.apply(grounded, self._intake._context())
            if not admitted:
                return 0
            stored = await self._intake.service.admit(
                self._sid, self._uid, admitted, proposed_by=proposed_by
            )
            if len(stored) != len(admitted):  # already stored by another producer: resync
                self._intake._states.pop(self._sid, None)
            return len(stored)

    async def listing(self) -> list[ActionProposal]:
        return await self._intake.service.for_session(self._sid, self._uid)

    async def known(self) -> list[str]:
        """Everything proposed so far, open or closed: told to the model so it does not repeat."""
        return [_label(r.kind, r.payload) for r in await self.listing()]
