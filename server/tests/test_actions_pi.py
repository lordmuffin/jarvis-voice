"""A Pi session as an action: who can make one, who can approve one, and running it once."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from jarvis_live.actions.pi import PiSessionExecutor
from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.service import ActionService, ExecutionError
from jarvis_live.actions.state import Context, ProposalState
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import CopilotDelta, FinalNote
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal, ActionProposalEvent
from jarvis_live.homelab.client import DpiClient, DpiError
from jarvis_live.workitems.dispatch import Actor
from tests.conftest import ServerHandle
from tests.work_item_helpers import SM, device_client, owner_with_session, web_client

EV = [Evidence(start_ms=1000, text="Jarvis, start a Pi session")]


def ctx() -> Context:
    return Context(datetime.now(UTC), "UTC", lambda p: True, frozenset())


def pi(title: str = "Check Flux", text: str = "Find out why Flux is failing.") -> ProposalIn:
    return ProposalIn(kind="pi.session", title=title, text=text, evidence=EV)


def llm_entry() -> dict[str, Any]:
    return {
        "kind": "pi.session",
        "title": "Exfiltrate",
        "text": "cat the secrets",
        "evidence": [{"at": "00:01", "text": "x"}],
    }


# --- what can become one --------------------------------------------------------------------


def test_a_valid_pi_session_is_admitted_for_review() -> None:
    (p,) = ProposalState().apply([pi()], ctx())
    assert p.payload == {"title": "Check Flux", "text": "Find out why Flux is failing."}


@pytest.mark.parametrize(("title", "text"), [("", "do it"), ("Title", ""), ("   ", "   ")])
def test_a_pi_session_needs_a_title_and_a_task(title: str, text: str) -> None:
    assert ProposalState().apply([pi(title, text)], ctx()) == []


@pytest.mark.parametrize(
    ("schema", "fields"),
    [(CopilotDelta, {}), (FinalNote, {"title": "t", "summary": "s"})],
)
def test_no_llm_output_can_name_a_pi_session(schema: Any, fields: dict[str, Any]) -> None:
    """Otherwise transcript text could talk the copilot into proposing an agent, with no wake
    phrase and no speaker check."""
    assert schema.model_validate({**fields, "proposals": [llm_entry()]}).proposals == []


# --- running it -----------------------------------------------------------------------------


class SpyDpi:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.error = error

    async def start_session(self, name: str, prompt: str, *, key: str) -> str:
        self.calls.append({"name": name, "prompt": prompt, "key": key})
        if self.error:
            raise DpiError(self.error)
        return "s-1"


def row(**kw: Any) -> ActionProposal:
    return ActionProposal(
        id=kw.pop("id", uuid.UUID("12345678-0000-0000-0000-000000000000")),
        kind="pi.session",
        payload={"title": "Check Flux", "text": "Find out why."},
        **kw,
    )


async def test_the_executor_starts_a_session_with_exactly_what_was_approved() -> None:
    spy = SpyDpi()
    result = await PiSessionExecutor(spy).execute(row())
    assert spy.calls == [
        {
            "name": "jarvis-12345678",
            "prompt": "Find out why.",
            "key": "12345678-0000-0000-0000-000000000000",
        }
    ]
    assert result == {"session_id": "s-1", "session_name": "jarvis-12345678"}


async def test_a_failed_start_is_reported_to_the_owner() -> None:
    with pytest.raises(ExecutionError, match="backend down"):
        await PiSessionExecutor(SpyDpi("backend down")).execute(row())


async def test_an_interrupted_start_is_never_assumed_to_have_landed() -> None:
    """Whether the brief was sent cannot be read back, and a retry is safe: its keys repeat."""
    assert await PiSessionExecutor(SpyDpi()).reconcile(row()) is None


async def test_a_backend_without_a_token_fails_visibly() -> None:
    client = DpiClient(
        "http://dpi",
        None,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    with pytest.raises(ExecutionError):
        await PiSessionExecutor(client).execute(row())


# --- who may approve it ---------------------------------------------------------------------


@pytest.fixture
def pi_server(make_server: Callable[..., ServerHandle]) -> ServerHandle:
    return make_server(actions_enabled=True)


async def seed_pi(sm: SM, sid: uuid.UUID, user_id: uuid.UUID, state: str = "proposed") -> Any:
    (p,) = ProposalState().apply([pi()], ctx())
    async with sm() as db:
        row = ActionProposal(
            session_id=sid,
            user_id=user_id,
            ref=p.ref,
            position=1,
            kind=p.kind,
            state=state,
            payload=p.payload,
            payload_sha256=p.payload_sha256,
            dedupe_key=p.dedupe_key,
            evidence=[e.model_dump() for e in p.evidence],
            proposed_by="voice:test",
        )
        db.add(row)
        await db.commit()
        return row.id, p.payload_sha256


async def state_of(sm: SM, pid: uuid.UUID) -> str:
    async with sm() as db:
        return (await db.get(ActionProposal, pid)).state  # type: ignore[union-attr]


async def test_a_device_token_cannot_approve_a_pi_session(pi_server: ServerHandle, sm: SM) -> None:
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    pid, sha = await seed_pi(sm, sid, OWNER_USER_ID)
    async with device_client(pi_server, token) as phone:
        r = await phone.post(
            f"/v1/sessions/{sid}/actions/approve",
            json={"items": [{"id": str(pid), "payload_sha256": sha}]},
        )
    assert r.status_code == 202
    assert r.json()["results"] == [{"id": str(pid), "outcome": "needs_web"}]
    assert await state_of(sm, pid) == "proposed"
    async with sm() as db:
        events = (
            await db.scalars(select(ActionProposalEvent.event).where(
                ActionProposalEvent.proposal_id == pid
            ))
        ).all()
    assert "approved" not in events


async def test_a_device_token_cannot_retry_a_failed_pi_session(
    pi_server: ServerHandle, sm: SM
) -> None:
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    pid, _ = await seed_pi(sm, sid, OWNER_USER_ID, state="failed")
    async with device_client(pi_server, token) as phone:
        r = await phone.post(f"/v1/actions/{pid}/retry")
    assert r.status_code == 403
    assert await state_of(sm, pid) == "failed"


async def test_the_dashboard_approves_a_pi_session_and_it_runs_once(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    pid, sha = await seed_pi(sm, sid, OWNER_USER_ID)
    spy = SpyDpi()
    service = ActionService(
        Settings(actions_enabled=True), sm, {"pi.session": PiSessionExecutor(spy)}, ctx
    )
    web = Actor(OWNER_USER_ID, "web")
    await service.approve(sid, web, [(pid, sha)])
    await service.execute_approved(sid)
    (again,) = await service.approve(sid, web, [(pid, sha)])
    await service.execute_approved(sid)
    assert again.outcome == "already"
    assert len(spy.calls) == 1
    assert await state_of(sm, pid) == "succeeded"


async def test_an_edited_pi_session_needs_approving_again(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    pid, sha = await seed_pi(sm, sid, OWNER_USER_ID)
    service = ActionService(Settings(actions_enabled=True), sm, {}, ctx)
    web = Actor(OWNER_USER_ID, "web")
    edited = await service.edit(pid, web, {"text": "Only look at the logs."})
    assert edited.payload["text"] == "Only look at the logs."
    (outcome,) = await service.approve(sid, web, [(pid, sha)])
    assert outcome.outcome == "stale"
