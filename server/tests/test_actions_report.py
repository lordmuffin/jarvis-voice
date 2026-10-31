"""Proposal precision: how many of the copilot's proposals the owner accepts. Report only."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from jarvis_live import cli
from jarvis_live.actions.report import label_key, production_rows, summarize
from jarvis_live.config import get_settings
from jarvis_live.db.models import ActionProposal, ActionProposalEvent
from jarvis_live.users import create_user
from tests.fakes import add_segments
from tests.work_item_helpers import SM, owner_with_session


def test_precision_counts_accepted_edited_and_rejected() -> None:
    r = summarize(
        [
            ("task.create", "accept"),
            ("task.create", "accept_edited"),
            ("task.create", "reject"),
            ("reminder.schedule", "accept"),
            ("reminder.schedule", "ignored"),
            ("vault.append", "pending"),
        ]
    )
    assert (r.made, r.accepted, r.accepted_edited, r.rejected) == (6, 2, 1, 1)
    assert (r.ignored, r.pending) == (1, 1)
    assert r.precision == pytest.approx(3 / 4)  # accepted as-is or edited, of those decided
    assert r.strict_precision == pytest.approx(2 / 4)  # accepted without an edit
    by = r.by_kind
    assert by["task.create"].precision == pytest.approx(2 / 3)
    assert by["reminder.schedule"].precision == 1.0
    assert by["vault.append"].precision is None  # nothing decided yet


def test_nothing_decided_is_no_number_not_zero() -> None:
    r = summarize([("task.create", "pending")])
    assert r.precision is None and r.strict_precision is None
    assert summarize([]).precision is None
    assert "no decisions yet" in "\n".join(r.render())


def test_the_report_reads_as_a_table() -> None:
    text = "\n".join(summarize([("task.create", "accept"), ("task.create", "reject")]).render())
    assert "precision 50.0%" in text and "1 of 2" in text
    assert "task.create" in text


def test_a_label_ignores_case_spacing_and_clock_time() -> None:
    a = label_key(
        "reminder.schedule", {"title": "Send  Invoice", "at": "2026-11-02T15:00:00+00:00"}
    )
    b = label_key("reminder.schedule", {"title": "send invoice", "at": "2026-12-09T08:00:00+00:00"})
    assert a == b
    assert a != label_key("task.create", {"text": "send invoice"})
    assert label_key("git.pr_comment", {"repo": "o/r", "pr": 1, "body": "x"}) != label_key(
        "git.pr_comment", {"repo": "o/r", "pr": 2, "body": "x"}
    )


# --- from what really happened -------------------------------------------------------------


async def seed(sm: SM, user_id: uuid.UUID, state: str, *events: str, n: int = 0) -> None:
    sid, _ = await owner_with_session(sm, user_id)
    payload = {"text": f"thing {uuid.uuid4().hex[:6]}"}
    async with sm() as db:
        row = ActionProposal(
            session_id=sid,
            user_id=user_id,
            ref="p1",
            position=1,
            kind="task.create",
            payload=payload,
            payload_sha256="0" * 64,
            dedupe_key=uuid.uuid4().hex + "0" * 32,
            evidence=[],
            state=state,
            proposed_by="copilot:test",
        )
        db.add(row)
        await db.flush()
        for e in ("proposed", *events):
            db.add(ActionProposalEvent(proposal_id=row.id, actor_kind="web", event=e))
        await db.commit()


async def test_what_the_owner_decided_is_the_signal(sm: SM) -> None:
    user = await create_user(
        sm, f"owner {uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:8]}@x.io"
    )
    for state, events in (
        ("succeeded", ("approved", "exec_succeeded")),  # accepted as proposed
        ("failed", ("approved", "exec_failed")),  # accepted; the execution is a separate matter
        ("succeeded", ("edited", "approved")),  # accepted after an edit
        ("rejected", ("rejected",)),
        ("expired", ("expired",)),
        ("proposed", ()),
    ):
        await seed(sm, user.id, state, *events)
    r = summarize(await production_rows(sm, user_id=user.id))
    assert (r.made, r.accepted, r.accepted_edited, r.rejected, r.ignored, r.pending) == (
        6, 2, 1, 1, 1, 1,
    )  # fmt: skip
    assert r.precision == pytest.approx(3 / 4)


async def test_since_leaves_out_older_proposals(sm: SM) -> None:
    user = await create_user(
        sm, f"owner {uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:8]}@x.io"
    )
    await seed(sm, user.id, "rejected", "rejected")
    assert await production_rows(sm, user_id=user.id, since=datetime.now(UTC)) == []
    assert len(await production_rows(sm, user_id=user.id)) == 1


# --- the commands ---------------------------------------------------------------------------


async def test_actions_report_command(
    postgres_url: str, sm: SM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    email = f"{uuid.uuid4().hex[:8]}@x.io"
    user = await create_user(sm, f"owner {uuid.uuid4().hex[:6]}", email=email)
    await seed(sm, user.id, "succeeded", "approved", "exec_succeeded")
    await seed(sm, user.id, "rejected", "rejected")
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    try:
        assert await asyncio.to_thread(cli.main, ["actions-report", "--user", email]) == 0
        table = capsys.readouterr().out
        assert "precision 50.0%" in table and "1 of 2" in table
        assert await asyncio.to_thread(cli.main, ["actions-report", "--user", email, "--json"]) == 0
        data: dict[str, Any] = json.loads(capsys.readouterr().out)
        assert (data["made"], data["accepted"], data["rejected"], data["precision"]) == (
            2,
            1,
            1,
            0.5,
        )
        assert await asyncio.to_thread(cli.main, ["actions-report", "--user", "nobody@x.io"]) == 1
    finally:
        get_settings.cache_clear()


async def test_export_transcript_prints_what_the_eval_reads(
    postgres_url: str, sm: SM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sid, _ = await owner_with_session(sm, (await create_user(sm, f"u {uuid.uuid4().hex[:6]}")).id)
    await add_segments(
        sm,
        sid,
        [
            (61_000, 64_000, "me", "Remind me to send the invoice."),
            (70_000, 72_000, "them", "Sure."),
        ],
    )
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    try:
        assert await asyncio.to_thread(cli.main, ["export-transcript", str(sid)]) == 0
    finally:
        get_settings.cache_clear()
    assert capsys.readouterr().out.splitlines() == [
        "[01:01] me: Remind me to send the invoice.",
        "[01:10] them: Sure.",
    ]
