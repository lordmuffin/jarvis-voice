"""Executing approved tasks and appends: a PR on the vault repo, merged on approval. Against the
fake GitHub; the client and executor are the real ones."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from jarvis_live.actions.service import ExecutionError
from jarvis_live.actions.state import payload_sha256
from jarvis_live.actions.vault import VaultExecutor
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal, CopilotItem
from jarvis_live.publish.github import GitHubClient
from tests.conftest import ServerHandle
from tests.fake_github import FakeGitHub
from tests.work_item_helpers import SM, device_client, owner_with_session

TRACKER = "20 Areas/Personal/Work Tracker.md"
BOARD = (
    "---\nkanban-plugin: board\n---\n\n## Active\n\n- [ ] keep this\n\n"
    "## Needs Triage\n*Ambiguous captures: decide keep-or-kill.*\n\n"
    "- [ ] old one\n- [ ] older one [[Jarvis]]\n\n## Done\n\n- [x] shipped\n"
)
NOTE = "00 Inbox/AI Output/2026-10-30 - Vendor call.md"


@pytest.fixture
def board(github: FakeGitHub) -> FakeGitHub:
    github.files("main")[TRACKER] = BOARD
    github.files("main")["30 Resources/Sleep.md"] = "# Sleep\n\nExisting.\n"
    return github


def make(
    sm: SM, github: FakeGitHub, tmp_path: Path, **cfg: Any
) -> tuple[VaultExecutor, GitHubClient]:
    token = tmp_path / "tok"
    token.write_text("fake-github-token\n")
    gh = GitHubClient(httpx.AsyncClient(), api=github.url, repo=github.repo, token_file=token)
    settings = Settings(public_base_url="https://live.example", **cfg)
    return VaultExecutor(settings, sm, gh), gh


async def proposal(
    sm: SM, kind: str = "task.create", note: bool = True, **payload: Any
) -> ActionProposal:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    if note:
        async with sm() as db:
            db.add(
                CopilotItem(
                    session_id=sid,
                    kind="final_note",
                    payload={"path": NOTE.split("/")[-1], "title": "T"},
                )
            )
            await db.commit()
    payload = payload or {"text": "Call the vendor about the quote"}
    return ActionProposal(
        id=uuid.uuid4(),
        session_id=sid,
        user_id=OWNER_USER_ID,
        ref="p1",
        kind=kind,
        payload=payload,
        payload_sha256=payload_sha256(kind, payload),
        evidence=[{"start_ms": 61_000, "text": "add a task"}],
        approved_by=OWNER_USER_ID,
    )


def tracker_lines(gh: FakeGitHub) -> list[str]:
    return gh.files("main")[TRACKER].split("\n")


async def test_a_task_lands_in_needs_triage_with_the_session_linked_and_is_merged(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm, due="2026-11-03", text="Call the vendor")
    result = await ex.execute(p)
    marker = f"^jv-{p.id.hex[:8]}"
    lines = tracker_lines(board)
    i = lines.index("- [ ] older one [[Jarvis]]")
    new = lines[i + 1]
    assert new == (
        f"- [ ] Call the vendor [[2026-10-30 - Vendor call]] "
        f"[session](https://live.example/sessions/{p.session_id}#t=61000) 📅 2026-11-03 {marker}"
    )
    assert lines[i + 2 :][:2] == ["", "## Done"]  # still inside Needs Triage, above Done
    assert result["landed"] == "merged" and result["merged"] is True
    assert board.files("main")["30 Resources/Sleep.md"] == "# Sleep\n\nExisting.\n"  # untouched
    pr = board.pulls[result["pr_number"]]
    assert pr.merged and pr.head == f"jarvis-live/action-{p.id.hex[:8]}"


async def test_running_it_again_never_writes_twice(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm)
    await ex.execute(p)
    before = (dict(board.pulls), board.files("main")[TRACKER])
    again = await ex.execute(p)
    assert again["landed"] == "already_present"
    assert (dict(board.pulls), board.files("main")[TRACKER]) == before
    assert board.files("main")[TRACKER].count(f"^jv-{p.id.hex[:8]}") == 1


async def test_with_merge_off_it_stops_at_an_open_pr_and_a_rerun_reuses_it(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path, actions_vault_merge=False)
    p = await proposal(sm)
    first = await ex.execute(p)
    assert first["landed"] == "pr_open" and not board.pulls[first["pr_number"]].merged
    assert f"^jv-{p.id.hex[:8]}" not in board.files("main")[TRACKER]
    second = await ex.execute(p)
    assert second["pr_number"] == first["pr_number"] and len(board.pulls) == 1
    assert (await ex.reconcile(p)) is not None


async def test_reconcile_is_read_only_and_finds_what_landed(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm)
    assert await ex.reconcile(p) is None
    await ex.execute(p)
    calls = len(board.calls)
    found = await ex.reconcile(p)
    assert found is not None and found["landed"] == "already_present"
    assert {m for m, _ in board.calls[calls:]} == {"GET"}  # it only looked
    assert len(board.pulls) == 1


async def test_a_board_without_needs_triage_fails_visibly_and_writes_nothing(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    board.files("main")[TRACKER] = "## Active\n\n- [ ] x\n"
    ex, _ = make(sm, board, tmp_path)
    with pytest.raises(ExecutionError, match="Needs Triage"):
        await ex.execute(await proposal(sm))
    assert not board.pulls


async def test_a_github_failure_is_reported_and_a_manual_retry_succeeds(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm)
    board.fail_next[("PUT", f"/contents/{TRACKER}")] = (409, "stale sha")
    with pytest.raises(ExecutionError, match="stale sha"):
        await ex.execute(p)
    assert f"^jv-{p.id.hex[:8]}" not in board.files("main")[TRACKER]
    assert (await ex.execute(p))["landed"] == "merged"


async def test_a_pr_touching_other_files_is_not_merged(
    sm: SM, board: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ex, gh = make(sm, board, tmp_path)

    async def sneaky(number: int) -> list[str]:
        return [TRACKER, "README.md"]

    monkeypatch.setattr(gh, "pr_files", sneaky)
    p = await proposal(sm)
    with pytest.raises(ExecutionError, match="README.md"):
        await ex.execute(p)
    assert f"^jv-{p.id.hex[:8]}" not in board.files("main")[TRACKER]  # left open for you to see
    assert next(iter(board.pulls.values())).state == "open"


async def test_concurrent_tasks_do_not_lose_each_other(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    a, b = await proposal(sm, text="First"), await proposal(sm, text="Second")
    await asyncio.gather(ex.execute(a), ex.execute(b))
    text = board.files("main")[TRACKER]
    assert "First" in text and "Second" in text


async def test_words_for_a_due_date_stay_in_the_text(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    await ex.execute(await proposal(sm, text="Send invoice", due="next Friday"))
    assert (
        "- [ ] Send invoice (due: next Friday) [[2026-10-30 - Vendor call]]"
        in tracker_lines(board)[tracker_lines(board).index("- [ ] older one [[Jarvis]]") + 1]
    )


async def test_without_a_note_yet_only_the_session_url_is_linked(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm, note=False)
    await ex.execute(p)
    line = next(x for x in tracker_lines(board) if f"^jv-{p.id.hex[:8]}" in x)
    assert "[[" not in line and f"/sessions/{p.session_id}#t=61000" in line


async def test_append_adds_a_block_to_an_existing_note(
    sm: SM, board: FakeGitHub, tmp_path: Path
) -> None:
    ex, _ = make(sm, board, tmp_path)
    p = await proposal(sm, "vault.append", path="30 Resources/Sleep.md", text="Magnesium helped.")
    await ex.execute(p)
    out = board.files("main")["30 Resources/Sleep.md"]
    assert out.startswith("# Sleep\n\nExisting.\n\nMagnesium helped. ")
    assert out.rstrip().endswith(f"^jv-{p.id.hex[:8]}")
    gone = await proposal(sm, "vault.append", path="30 Resources/Gone.md", text="x")
    with pytest.raises(ExecutionError, match="no longer exists"):
        await ex.execute(gone)


# --- the whole path: the "done when" ---------------------------------------------------------


async def test_approving_a_proposed_task_puts_it_in_the_tracker_with_the_session_linked(
    make_server: Callable[..., ServerHandle],
    publish_settings: dict[str, Any],
    board: FakeGitHub,
    sm: SM,
) -> None:
    server = make_server(**publish_settings, actions_enabled=True)
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    payload = {"text": "Call the vendor about the quote"}
    async with sm() as db:
        db.add(
            CopilotItem(
                session_id=sid, kind="final_note", payload={"path": "Vendor call.md", "title": "T"}
            )
        )
        db.add(
            ActionProposal(
                session_id=sid, user_id=OWNER_USER_ID, ref="p1", position=1, kind="task.create",
                payload=payload, payload_sha256=payload_sha256("task.create", payload),
                dedupe_key=uuid.uuid4().hex + "0" * 32, evidence=[{"start_ms": 5000, "text": "t"}],
                proposed_by="copilot:test",
            )
        )  # fmt: skip
        await db.commit()
        pid = (
            await db.scalars(select(ActionProposal.id).where(ActionProposal.session_id == sid))
        ).one()
    sha = payload_sha256("task.create", payload)
    async with device_client(server, token) as phone:
        body = {"items": [{"id": str(pid), "payload_sha256": sha}]}
        assert (
            await phone.post(f"/v1/sessions/{sid}/actions/approve", json=body)
        ).status_code == 202
        for _ in range(100):
            item = (await phone.get(f"/v1/sessions/{sid}/actions")).json()["items"][0]
            if item["state"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.1)
        assert item["state"] == "succeeded", item
        assert item["result"]["merged"] is True and item["result"]["pr_url"]
        await phone.post(f"/v1/sessions/{sid}/actions/approve", json=body)  # tap again
        await asyncio.sleep(0.5)
    text = board.files("main")[TRACKER]
    assert text.count(f"^jv-{pid.hex[:8]}") == 1
    line = next(x for x in text.split("\n") if f"^jv-{pid.hex[:8]}" in x)
    assert "Call the vendor about the quote" in line and "[[Vendor call]]" in line
    assert f"https://live.example/sessions/{sid}#t=5000" in line
    assert text.index(line) < text.index("## Done")


async def test_without_publishing_the_task_fails_visibly(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    """Lab has no vault credential: approving there reports it, and writes nothing."""
    server = make_server(actions_enabled=True)
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    payload = {"text": "x"}
    async with sm() as db:
        db.add(
            ActionProposal(
                session_id=sid, user_id=OWNER_USER_ID, ref="p1", position=1, kind="task.create",
                payload=payload, payload_sha256=payload_sha256("task.create", payload),
                dedupe_key=uuid.uuid4().hex + "0" * 32, evidence=[], proposed_by="copilot:test",
            )
        )  # fmt: skip
        await db.commit()
        pid = (
            await db.scalars(select(ActionProposal.id).where(ActionProposal.session_id == sid))
        ).one()
    async with device_client(server, token) as phone:
        body = {
            "items": [{"id": str(pid), "payload_sha256": payload_sha256("task.create", payload)}]
        }
        await phone.post(f"/v1/sessions/{sid}/actions/approve", json=body)
        for _ in range(50):
            item = (await phone.get(f"/v1/sessions/{sid}/actions")).json()["items"][0]
            if item["state"] == "failed":
                break
            await asyncio.sleep(0.1)
    assert "not available" in item["result"]["error"]
