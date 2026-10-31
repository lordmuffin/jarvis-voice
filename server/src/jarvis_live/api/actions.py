"""The action review API: what the copilot proposes to do, and the owner's approval.

Only the session's owner sees and acts on its proposals; anyone else gets 404. Unlike work item
dispatch, a device token may approve (the macOS app has no dashboard sign-in): an approval must
carry the ``payload_sha256`` the client displayed, so it only ever approves what it showed, and
every approval records who (``web`` or ``device``). Approving returns 202 and the actions run in
the background; clients poll the list."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis_live.actions.service import ActionError, ActionService
from jarvis_live.api.sessions import PrincipalDep
from jarvis_live.auth import Principal
from jarvis_live.db.models import ActionProposal, ActionProposalEvent
from jarvis_live.workitems.dispatch import Actor

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")


def _service(request: Request) -> ActionService:
    service: ActionService = request.app.state.actions
    return service


def _enabled(request: Request) -> ActionService:
    service = _service(request)
    if not service.enabled:
        raise HTTPException(503, "action proposals are not enabled")
    return service


def _actor(principal: Principal) -> Actor:
    return Actor(principal.user.id, "web" if principal.device is None else "device")


def _raise(e: ActionError) -> HTTPException:
    return HTTPException(e.status, e.message)


class EvidenceOut(BaseModel):
    start_ms: int
    text: str


class ActionOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    ref: str
    position: int
    kind: str
    state: str
    payload: dict[str, Any]
    payload_sha256: str
    evidence: list[EvidenceOut]
    proposed_by: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    result: dict[str, Any] | None
    executed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, a: ActionProposal) -> "ActionOut":
        return cls(
            id=a.id,
            session_id=a.session_id,
            ref=a.ref,
            position=a.position,
            kind=a.kind,
            state=a.state,
            payload=dict(a.payload),
            payload_sha256=a.payload_sha256,
            evidence=[EvidenceOut(**e) for e in a.evidence],
            proposed_by=a.proposed_by,
            approved_by=a.approved_by,
            approved_at=a.approved_at,
            result=a.result,
            executed_at=a.executed_at,
            version=a.version,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )


class ActionList(BaseModel):
    enabled: bool
    items: list[ActionOut]


class EventOut(BaseModel):
    at: datetime
    actor_user_id: uuid.UUID | None
    actor_kind: str
    event: str
    payload: dict[str, Any]

    @classmethod
    def of(cls, e: ActionProposalEvent) -> "EventOut":
        return cls(
            at=e.at,
            actor_user_id=e.actor_user_id,
            actor_kind=e.actor_kind,
            event=e.event,
            payload=dict(e.payload),
        )


class Events(BaseModel):
    events: list[EventOut]


class Approval(BaseModel):
    id: uuid.UUID
    payload_sha256: str = Field(min_length=64, max_length=64)


class ApproveBody(BaseModel):
    items: list[Approval] = Field(max_length=50)


class Result(BaseModel):
    id: uuid.UUID
    outcome: str


class Approved(BaseModel):
    results: list[Result]


class EditBody(BaseModel):
    changes: dict[str, Any]


def _run_in_background(request: Request, service: ActionService, session_id: uuid.UUID) -> None:
    async def go() -> None:
        try:
            await service.execute_approved(session_id)
        except Exception:
            log.exception("executing actions of session %s failed", session_id)

    tasks: set[asyncio.Task[None]] = request.app.state.action_tasks
    task = asyncio.get_running_loop().create_task(go())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.get("/sessions/{session_id}/actions", response_model=ActionList)
async def list_actions(session_id: uuid.UUID, principal: PrincipalDep, request: Request) -> Any:
    service = _service(request)
    if not service.enabled:
        return ActionList(enabled=False, items=[])
    rows = await service.for_session(session_id, principal.user.id)
    return ActionList(enabled=True, items=[ActionOut.of(r) for r in rows])


@router.post("/sessions/{session_id}/actions/approve", response_model=Approved, status_code=202)
async def approve(
    session_id: uuid.UUID, body: ApproveBody, principal: PrincipalDep, request: Request
) -> Any:
    service = _enabled(request)
    outcomes = await service.approve(
        session_id, _actor(principal), [(i.id, i.payload_sha256) for i in body.items]
    )
    if any(o.outcome == "approved" for o in outcomes):
        _run_in_background(request, service, session_id)
    return Approved(results=[Result(id=o.id, outcome=o.outcome) for o in outcomes])


@router.patch("/actions/{action_id}", response_model=ActionOut)
async def edit(
    action_id: uuid.UUID, body: EditBody, principal: PrincipalDep, request: Request
) -> Any:
    try:
        return ActionOut.of(
            await _enabled(request).edit(action_id, _actor(principal), body.changes)
        )
    except ActionError as e:
        raise _raise(e) from e


@router.post("/actions/{action_id}/reject", response_model=ActionOut)
async def reject(action_id: uuid.UUID, principal: PrincipalDep, request: Request) -> Any:
    try:
        return ActionOut.of(await _enabled(request).reject(action_id, _actor(principal)))
    except ActionError as e:
        raise _raise(e) from e


@router.post("/actions/{action_id}/retry", response_model=ActionOut, status_code=202)
async def retry(action_id: uuid.UUID, principal: PrincipalDep, request: Request) -> Any:
    service = _enabled(request)
    try:
        row = await service.retry(action_id, _actor(principal))
    except ActionError as e:
        raise _raise(e) from e
    _run_in_background(request, service, row.session_id)
    return ActionOut.of(row)


@router.get("/actions/{action_id}/events", response_model=Events)
async def events(action_id: uuid.UUID, principal: PrincipalDep, request: Request) -> Any:
    try:
        rows = await _service(request).events(action_id, principal.user.id)
    except ActionError as e:
        raise _raise(e) from e
    return Events(events=[EventOut.of(e) for e in rows])


__all__ = ["router"]
