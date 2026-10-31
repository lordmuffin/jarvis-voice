import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.copilot.prompts import COPILOT_SYSTEM, FINAL_SYSTEM
from jarvis_live.copilot.transcript import Line, load_final_lines, load_lines, render_lines
from jarvis_live.db.models import OWNER_USER_ID, SegmentRow, SessionSpeaker
from jarvis_live.finalize.render import render_note
from jarvis_live.workitems.state import number_lines
from tests.fakes import add_segments, make_session, new_device
from tests.test_render import META, NOTE

SM = async_sessionmaker  # type: ignore[type-arg]


def test_a_named_speaker_replaces_the_side_in_every_rendering() -> None:
    lines = [Line(1_000, 2_000, "them", "I will send it", speaker_label="Dana")]
    assert render_lines(lines) == "[00:01] Dana: I will send it"
    assert number_lines(lines)[0] == "L1 [00:01] Dana: I will send it"
    assert "**Dana:** I will send it" in render_note(NOTE, META, lines)


def test_unlabelled_lines_render_exactly_as_before() -> None:
    lines = [Line(1_000, 2_000, "them", "a"), Line(2_000, 3_000, "me", "b")]
    assert render_lines(lines) == "[00:01] them: a\n[00:02] me: b"
    assert "**Them:** a" in render_note(NOTE, META, lines)
    assert "**Me:** b" in render_note(NOTE, META, lines)


def test_prompts_tell_the_model_other_names_are_not_the_owner() -> None:
    for prompt in (COPILOT_SYSTEM, FINAL_SYSTEM):
        assert "Speaker 2" in prompt and "not" in prompt.lower()


async def _speakers(
    sm: SM, sid: uuid.UUID, rows: list[tuple[str, str, int, str | None, bool]]
) -> None:
    async with sm() as db:
        for speaker_id, channel, ordinal, label, owner in rows:
            db.add(
                SessionSpeaker(
                    session_id=sid,
                    speaker_id=speaker_id,
                    channel=channel,
                    ordinal=ordinal,
                    label=label,
                    is_owner=owner,
                )
            )
        await db.commit()


async def _assign(sm: SM, sid: uuid.UUID, by_text: dict[str, str]) -> None:
    async with sm() as db:
        for text, speaker_id in by_text.items():
            await db.execute(
                update(SegmentRow)
                .where(SegmentRow.session_id == sid, SegmentRow.text == text)
                .values(speaker_id=speaker_id)
            )
        await db.commit()


async def _session(sm: SM) -> uuid.UUID:
    device, _ = await new_device(sm, f"d-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    return await make_session(sm, device, status="finalizing")


async def _labels(sm: SM, sid: uuid.UUID) -> dict[str, str | None]:
    async with sm() as db:
        lines = await load_lines(db, sid)
        finals = await load_final_lines(db, sid)
    assert [(x.text, x.speaker_label) for x in lines] == [(x.text, x.speaker_label) for x in finals]
    return {x.text: x.speaker_label for x in lines}


async def test_a_channel_with_several_voices_shows_speaker_numbers(sm: SM, tmp_path: Path) -> None:
    sid = await _session(sm)
    await add_segments(
        sm,
        sid,
        [(0, 1000, "them", "one"), (2000, 3000, "them", "two"), (4000, 5000, "them", "n/a")],
    )
    await _speakers(sm, sid, [("s0", "system", 1, None, False), ("s1", "system", 2, None, False)])
    await _assign(sm, sid, {"one": "s0", "two": "s1"})
    assert await _labels(sm, sid) == {"one": "Speaker 1", "two": "Speaker 2", "n/a": None}


async def test_a_channel_with_one_voice_keeps_its_side(sm: SM) -> None:
    sid = await _session(sm)
    await add_segments(sm, sid, [(0, 1000, "them", "solo")])
    await _speakers(sm, sid, [("s0", "system", 1, None, False)])
    await _assign(sm, sid, {"solo": "s0"})
    assert await _labels(sm, sid) == {"solo": None}


async def test_a_name_always_shows_and_the_owner_stays_me(sm: SM) -> None:
    sid = await _session(sm)
    await add_segments(sm, sid, [(0, 1000, "them", "dana"), (2000, 3000, "me", "mine")])
    await _speakers(sm, sid, [("s0", "system", 1, "Dana", False), ("m0", "mic", 2, "Andrew", True)])
    await _assign(sm, sid, {"dana": "s0", "mine": "m0"})
    assert await _labels(sm, sid) == {"dana": "Dana", "mine": None}  # the owner is "me"
