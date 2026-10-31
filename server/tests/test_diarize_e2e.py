"""A recording played through the real app comes out as labelled speaker turns."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jarvis_live.copilot.schema import FinalNote
from jarvis_live.diarize.align import Turn
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM
from tests.test_processing_api import _until
from tests.test_publish import client
from tests.test_upload import wav_bytes


class ScriptedDiarizer:
    """Two voices: the first utterance (1.0-2.5 s) is one, the second (3.5-5.5 s) another."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def diarize(self, pcm: Path) -> list[Turn]:
        if self.fail:
            raise RuntimeError("sidecar exploded")
        return [Turn(0, 3000, "SPEAKER_00"), Turn(3000, 7000, "SPEAKER_01")]


def _llm() -> FakeLLM:
    return FakeLLM().queue("FinalNote", FinalNote(title="Standup", summary="S."))


async def _replay(c: httpx.AsyncClient) -> dict[str, Any]:
    r = await c.post(
        "/v1/sessions/upload",
        params={"filename": "memo.wav"},
        content=wav_bytes(TWO_UTTERANCES),
        headers={"Content-Type": "audio/wav"},
    )

    def finished(d: dict[str, Any]) -> bool:
        return bool(d["status"] == "done" and d["processing"]["summary"] == "idle")

    return await _until(c, r.json()["id"], finished)


async def test_a_replayed_two_voice_recording_has_labelled_turns(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    llm = _llm()
    server = make_server(llm=llm, diarizer=ScriptedDiarizer())
    async with client(server, token) as c:
        d = await _replay(c)

    assert [(s["text"], s["speaker"], s["speaker_id"]) for s in d["segments"]] == [
        ("seg-1", "me", "spk_mic_0"),
        ("seg-2", "me", "spk_mic_1"),
    ]
    assert all(s["speaker_rev"] == 2 and s["mixed"] is False for s in d["segments"])
    assert d["speakers"] == [
        {"id": "spk_mic_0", "label": None, "ordinal": 1, "enrolled": False, "is_owner": False},
        {"id": "spk_mic_1", "label": None, "ordinal": 2, "enrolled": False, "is_owner": False},
    ]
    # the note was written from a transcript that tells the voices apart
    assert "Speaker 1: seg-1" in llm.calls[-1].user and "Speaker 2: seg-2" in llm.calls[-1].user


async def test_a_broken_diarizer_gives_todays_undiarized_transcript(
    make_server: Callable[..., ServerHandle], token: str
) -> None:
    llm = _llm()
    server = make_server(llm=llm, diarizer=ScriptedDiarizer(fail=True))
    async with client(server, token) as c:
        d = await _replay(c)

    assert d["status"] == "done"
    assert [(s["text"], s["speaker"], s["speaker_id"]) for s in d["segments"]] == [
        ("seg-1", "me", None),
        ("seg-2", "me", None),
    ]
    assert d["speakers"] == []
    assert "me: seg-1" in llm.calls[-1].user
