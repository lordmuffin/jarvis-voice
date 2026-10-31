"""The Actions view of a finished session includes what its note lists, not only what the live
copilot found (it may never have run: uploads, short or solo sessions)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem
from jarvis_live.finalize.note_items import merge_note_items, note_items
from jarvis_live.finalize.render import NoteMeta, render_note
from jarvis_live.protocol import Action, Copilot, Note
from tests.conftest import ServerHandle
from tests.fakes import new_device
from tests.test_publish import SM, client, finished

EMPTY = Copilot(version=0, notes=[], actions=[], decisions=[], suggestions=[], related=[])


def rendered(note: FinalNote) -> str:
    meta = NoteMeta(
        created=datetime(2026, 10, 24, 9, 0, tzinfo=UTC),
        session_id="s1",
        device="mac",
        user=None,
        mode="solo",
        source=None,
        duration_minutes=1,
    )
    return render_note(note, meta, [], [])


NOTE = FinalNote(
    title="Planning",
    summary="We planned.",
    actions=[
        FinalAction(text="Send the node inventory", owner="Me", due="Fri"),
        FinalAction(text="Book the room"),
    ],
    decisions=["Pilot on k3s2 first"],
    follow_ups=["Ask about ARM images"],
)


def test_reads_the_actions_and_decisions_the_finalizer_wrote() -> None:
    actions, decisions = note_items(rendered(NOTE))
    assert [(a.text, a.owner, a.due) for a in actions] == [
        ("Send the node inventory", "Me", "Fri"),
        ("Book the room", None, None),
    ]
    assert decisions == ["Pilot on k3s2 first"]


def test_reads_an_edited_note_and_stops_at_the_next_section() -> None:
    md = (
        "# T\n\n## Actions\n\n- [x] Done already\n- plain bullet\n\nprose\n\n"
        "## Transcript\n\n- [ ] not one\n"
    )
    actions, decisions = note_items(md)
    assert [a.text for a in actions] == ["Done already", "plain bullet"]
    assert decisions == []


def test_a_note_without_items_changes_nothing() -> None:
    assert merge_note_items(EMPTY, "# T\n\n## Summary\n\nNothing to do.\n") == EMPTY
    assert merge_note_items(EMPTY, None) == EMPTY


def test_note_items_fill_an_empty_copilot() -> None:
    c = merge_note_items(EMPTY, rendered(NOTE))
    assert [a.text for a in c.actions] == ["Send the node inventory", "Book the room"]
    assert c.actions[0].owner == "Me" and c.actions[0].due == "Fri"
    assert len({a.id for a in c.actions}) == 2
    assert [d.text for d in c.decisions] == ["Pilot on k3s2 first"]


def test_live_items_are_kept_and_not_repeated() -> None:
    live = EMPTY.model_copy(
        update={
            "version": 7,
            "actions": [Action(id="a1", text="send the node  inventory.", owner="Andrew")],
            "decisions": [Note(id="d1", text="Pilot on k3s2 first")],
        }
    )
    c = merge_note_items(live, rendered(NOTE))
    assert [(a.id, a.text) for a in c.actions] == [
        ("a1", "send the node  inventory."),
        (c.actions[1].id, "Book the room"),
    ]
    assert [d.id for d in c.decisions] == ["d1"]
    assert c.version == 7


async def test_a_finished_session_without_a_copilot_snapshot_lists_its_note_actions(
    server: ServerHandle, sm: SM, token: str
) -> None:
    device_id = (await new_device(sm, f"mac-{uuid.uuid4()}", user_id=OWNER_USER_ID))[0]
    outbox = server.settings.data_dir / "outbox"
    sid = await finished(sm, outbox, device_id, name=f"{uuid.uuid4().hex}.md")
    async with sm() as db:
        payload = await db.scalar(select(CopilotItem.payload).where(CopilotItem.session_id == sid))
    assert payload is not None  # the note only: no copilot snapshot was ever saved
    body = rendered(NOTE).split("---\n", 2)[2]
    (outbox / payload["path"]).write_text(f"---\nsession_id: {sid}\n---\n{body}")
    async with client(server, token) as c:
        d = (await c.get(f"/v1/sessions/{sid}")).json()
    assert [a["text"] for a in d["copilot"]["actions"]] == [
        "Send the node inventory",
        "Book the room",
    ]
    assert [x["text"] for x in d["copilot"]["decisions"]] == ["Pilot on k3s2 first"]
