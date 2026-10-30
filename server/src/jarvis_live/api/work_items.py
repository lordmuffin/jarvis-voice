"""The work item review queue: the copilot's draft Claude Code prompts, per session and across
sessions. See ``workitems/service.py`` for the rules, and docs/plans/copilot-to-claude-code.md.

Only the session's owner sees its work items; anyone else gets 404. Approving and dispatching
need the dashboard sign-in: a device token can read and edit, not authorize work."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis_live.api.sessions import Db, PrincipalDep, _visible
from jarvis_live.auth import Principal
from jarvis_live.db.models import WorkItem, WorkItemDispatch, WorkItemEvent
from jarvis_live.dispatch.service import DispatchService, latest_runs
from jarvis_live.workitems.dispatch import Actor
from jarvis_live.workitems.service import (
    MAX_GUIDANCE,
    WorkItemError,
    WorkItemService,
    approval_problems,
)
from jarvis_live.workitems.template import branch_name

router = APIRouter(prefix="/v1")

State = Literal["draft", "approved", "discarded", "merged", "done"]


def _service(request: Request) -> WorkItemService:
    service: WorkItemService = request.app.state.work_items
    return service


def _actor(principal: Principal) -> Actor:
    return Actor(principal.user.id, "web" if principal.device is None else "device")


def _raise(e: WorkItemError) -> HTTPException:
    return HTTPException(e.status, e.message)


class SourceOut(BaseModel):
    segment_id: str
    start_ms: int
    end_ms: int
    text: str


class DispatchOut(BaseModel):
    """The item's latest run as headless Claude Code."""

    id: uuid.UUID
    attempt: int
    state: Literal[
        "queued",
        "running",
        "pr_opened",
        "no_changes",
        "failed",
        "rate_limited",
        "timed_out",
        "cancelled",
        "needs_input",
    ]
    repo: str
    branch: str
    pr_url: str | None
    pr_number: int | None
    summary: str | None
    error: str | None
    # What the run asked the owner (``needs_input``); the owner's reply it ran with, if any.
    questions: str | None
    guidance: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, d: WorkItemDispatch) -> "DispatchOut":
        return cls(
            id=d.id,
            attempt=d.attempt,
            state=d.state,
            repo=d.repo,
            branch=d.branch,
            pr_url=d.pr_url,
            pr_number=d.pr_number,
            summary=d.summary,
            error=d.error,
            questions=d.questions,
            guidance=d.guidance,
            created_at=d.created_at,
            started_at=d.started_at,
            finished_at=d.finished_at,
        )


class WorkItemOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    ref: str
    state: State
    title: str
    repo: str | None
    intent: str
    acceptance_criteria: list[str]
    done_when: str | None
    sources: list[SourceOut]
    locked_fields: list[str]
    merged_into: uuid.UUID | None
    branch: str
    prompt_sha256: str | None
    approved_at: datetime | None
    done_at: datetime | None
    merged_pr_url: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    # What stands between this draft and approval; empty when it can be approved.
    problems: list[str]
    session_title: str | None = None
    session_started_at: datetime | None = None
    dispatch: DispatchOut | None = None

    @classmethod
    def of(cls, item: WorkItem, allowlist: dict[str, str], **extra: Any) -> "WorkItemOut":
        return cls(
            id=item.id,
            session_id=item.session_id,
            ref=item.ref,
            state=item.state,
            title=item.title,
            repo=item.repo,
            intent=item.intent,
            acceptance_criteria=list(item.acceptance_criteria),
            done_when=item.done_when,
            sources=[SourceOut(**s) for s in item.sources],
            locked_fields=list(item.locked_fields),
            merged_into=item.merged_into,
            branch=branch_name(str(item.session_id), item.ref, item.title),
            prompt_sha256=item.prompt_sha256,
            approved_at=item.approved_at,
            done_at=item.done_at,
            merged_pr_url=item.merged_pr_url,
            version=item.version,
            created_at=item.created_at,
            updated_at=item.updated_at,
            problems=approval_problems(item, allowlist) if item.state == "draft" else [],
            **extra,
        )


class RepoOut(BaseModel):
    name: str
    description: str


class WorkItemPage(BaseModel):
    enabled: bool
    dispatch_enabled: bool
    repos: list[RepoOut]
    items: list[WorkItemOut]


class WorkItemEdit(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    repo: str | None = None
    intent: str | None = Field(default=None, max_length=10_000)
    acceptance_criteria: list[Annotated[str, Field(max_length=1000)]] | None = Field(
        default=None, max_length=50
    )
    done_when: str | None = Field(default=None, max_length=1000)


class MergeRequest(BaseModel):
    into: uuid.UUID
    sources: list[uuid.UUID] = Field(min_length=1, max_length=20)


class ContinueRequest(BaseModel):
    guidance: str = Field(min_length=1, max_length=MAX_GUIDANCE)


class PromptOut(BaseModel):
    markdown: str
    frozen: bool  # approved: exactly what is copied or dispatched
    sha256: str | None


class EventOut(BaseModel):
    at: datetime
    event: str
    actor_kind: str
    actor_user_id: uuid.UUID | None
    payload: dict[str, Any]

    @classmethod
    def of(cls, e: WorkItemEvent) -> "EventOut":
        return cls(
            at=e.at,
            event=e.event,
            actor_kind=e.actor_kind,
            actor_user_id=e.actor_user_id,
            payload=e.payload,
        )


async def _with_runs(request: Request, items: list[WorkItemOut]) -> list[WorkItemOut]:
    async with request.app.state.sessionmaker() as db:
        runs = await latest_runs(db, [i.id for i in items])
    return [
        i.model_copy(update={"dispatch": DispatchOut.of(runs[i.id])}) if i.id in runs else i
        for i in items
    ]


def _page(service: WorkItemService, items: list[WorkItemOut]) -> WorkItemPage:
    return WorkItemPage(
        enabled=service.enabled,
        dispatch_enabled=service.dispatch_enabled,
        repos=[RepoOut(name=n, description=d) for n, d in service.allowlist.items()],
        items=items,
    )


@router.get("/sessions/{session_id}/work-items")
async def session_work_items(
    session_id: uuid.UUID, principal: PrincipalDep, db: Db, request: Request
) -> WorkItemPage:
    await _visible(db, principal, session_id)
    service = _service(request)
    rows = await service.for_session(session_id, principal.user.id)
    items = [WorkItemOut.of(r, service.allowlist) for r in rows]
    return _page(service, await _with_runs(request, items))


@router.get("/work-items")
async def my_work_items(
    principal: PrincipalDep,
    request: Request,
    state: Annotated[list[State] | None, Query()] = None,
) -> WorkItemPage:
    """The caller's work items across sessions, newest session first. Default: drafts and
    approved items."""
    service = _service(request)
    listed = await service.for_user(principal.user.id, state or ["draft", "approved"])
    items = [
        WorkItemOut.of(
            x.item,
            service.allowlist,
            session_title=x.session_title,
            session_started_at=x.session_started_at,
        )
        for x in listed
    ]
    return _page(service, await _with_runs(request, items))


async def _act(request: Request, call: Any, *, write: bool = True) -> WorkItemOut:
    service = _service(request)
    try:
        if write:
            service.require_enabled()
        item = await call(service)
    except WorkItemError as e:
        raise _raise(e) from e
    (out,) = await _with_runs(request, [WorkItemOut.of(item, service.allowlist)])
    return out


@router.get("/work-items/{item_id}")
async def get_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.get(item_id, principal.user.id), write=False)


@router.patch("/work-items/{item_id}")
async def edit_work_item(
    item_id: uuid.UUID, body: WorkItemEdit, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    """Edit fields. Edited fields are locked against the LLM. Editing an approved item puts it
    back to draft: its frozen prompt no longer matches."""
    fields = body.model_dump(exclude_unset=True)
    return await _act(request, lambda s: s.edit(item_id, _actor(principal), fields))


@router.post("/work-items/{item_id}/approve")
async def approve_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.approve(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/unapprove")
async def unapprove_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.unapprove(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/discard")
async def discard_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.discard(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/restore")
async def restore_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.restore(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/done")
async def mark_work_item_done(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.done(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/reopen")
async def reopen_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    """Back to approved with its frozen prompt, or to draft if it had none."""
    return await _act(request, lambda s: s.reopen(item_id, _actor(principal)))


@router.post("/work-items/merge")
async def merge_work_items(
    body: MergeRequest, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    return await _act(request, lambda s: s.merge(body.into, body.sources, _actor(principal)))


@router.get("/work-items/{item_id}/prompt")
async def work_item_prompt(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> PromptOut:
    service = _service(request)
    try:
        markdown, frozen = await service.prompt(item_id, principal.user.id)
        item = await service.get(item_id, principal.user.id)
    except WorkItemError as e:
        raise _raise(e) from e
    digest = item.prompt_sha256 if frozen else None
    return PromptOut(markdown=markdown, frozen=frozen, sha256=digest)


@router.post("/work-items/{item_id}/copied", status_code=204)
async def work_item_copied(item_id: uuid.UUID, principal: PrincipalDep, request: Request) -> None:
    """Record that the prompt was copied (the audit log)."""
    try:
        _service(request).require_enabled()
        await _service(request).copied(item_id, _actor(principal))
    except WorkItemError as e:
        raise _raise(e) from e


@router.get("/work-items/{item_id}/events")
async def work_item_events(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> list[EventOut]:
    try:
        events = await _service(request).events(item_id, principal.user.id)
    except WorkItemError as e:
        raise _raise(e) from e
    return [EventOut.of(e) for e in events]


@router.post("/work-items/{item_id}/dispatch", status_code=202)
async def dispatch_work_item(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    """Run an approved item as headless Claude Code. Refused (409, and logged) unless it is
    approved, its prompt is unchanged since, it was approved from the dashboard by the caller,
    and its repo is still on the allowlist. 503 while no dispatcher is configured."""
    return await _act(request, lambda s: s.dispatch(item_id, _actor(principal)))


@router.post("/work-items/{item_id}/dispatch/continue", status_code=202)
async def continue_dispatch(
    item_id: uuid.UUID, body: ContinueRequest, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    """Reply to the item's last run: a follow-up run continues its branch and draft PR, with
    ``guidance`` added to the approved prompt. The same gate as a first run; 409 while a run is
    active or before any has run."""
    return await _act(
        request, lambda s: s.dispatch(item_id, _actor(principal), guidance=body.guidance)
    )


@router.post("/work-items/{item_id}/dispatch/cancel")
async def cancel_dispatch(
    item_id: uuid.UUID, principal: PrincipalDep, request: Request
) -> WorkItemOut:
    """Stop the item's queued or running run (its Job is deleted)."""
    dispatch: DispatchService | None = request.app.state.dispatch
    if dispatch is None:
        raise HTTPException(503, "Dispatch is not configured on this server.")
    try:
        await dispatch.cancel(item_id, _actor(principal))
    except WorkItemError as e:
        raise _raise(e) from e
    return await _act(request, lambda s: s.get(item_id, principal.user.id), write=False)
