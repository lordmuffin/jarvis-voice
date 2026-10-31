"""Enrolling a voice once, and being recognised in a later session: the real app, REST only."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jarvis_live.copilot.schema import FinalNote
from jarvis_live.diarize.align import Turn
from jarvis_live.diarize.client import Diarization
from jarvis_live.users import create_user
from tests.audio import TWO_UTTERANCES
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM, new_device
from tests.test_processing_api import _until
from tests.test_publish import SM, client
from tests.test_upload import wav_bytes

MODEL = "wespeaker-test"
ANDREW = [1.0, 0.0, 0.0]
DANA = [0.0, 1.0, 0.0]


class Voices:
    """Plays back one scripted result per recording, in the order they are diarized."""

    def __init__(self, *results: Diarization) -> None:
        self.results = list(results)

    async def diarize(self, pcm: Path) -> Diarization:
        return self.results.pop(0)


def two_voices(first: list[float], second: list[float]) -> Diarization:
    """The first utterance (1.0-2.5 s) is one voice, the second (3.5-5.5 s) another."""
    return Diarization(
        [Turn(0, 3000, "A"), Turn(3000, 7000, "B")], {"A": first, "B": second}, MODEL
    )


def llm() -> FakeLLM:
    return (
        FakeLLM()
        .queue("FinalNote", FinalNote(title="T", summary="S."))
        .queue("FinalNote", FinalNote(title="T", summary="S."))
    )


async def replay(c: httpx.AsyncClient) -> dict[str, Any]:
    r = await c.post(
        "/v1/sessions/upload",
        params={"filename": "memo.wav"},
        content=wav_bytes(TWO_UTTERANCES),
        headers={"Content-Type": "audio/wav"},
    )

    def finished(d: dict[str, Any]) -> bool:
        return bool(d["status"] == "done" and d["processing"]["summary"] == "idle")

    return await _until(c, r.json()["id"], finished)


async def name(
    c: httpx.AsyncClient, sid: str, speaker: str, label: str, *, owner: bool = False
) -> httpx.Response:
    body = {"label": label, "is_owner": owner}
    return await c.put(f"/v1/sessions/{sid}/speakers/{speaker}", json=body)


async def a_user(sm: SM) -> str:
    """A user of their own with a device: enrolled voices are per user."""
    user = await create_user(sm, "Andrew", email=f"{uuid.uuid4()}@x.test")
    _, token = await new_device(sm, f"d-{uuid.uuid4()}", user_id=user.id)
    return token


async def test_my_enrolled_voice_is_recognised_in_a_fresh_session(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token = await a_user(sm)
    log = llm()
    server = make_server(
        llm=log,
        diarizer=Voices(two_voices(ANDREW, DANA), two_voices([0.97, 0.12, 0.0], [0.05, 0.99, 0.0])),
    )
    async with client(server, token) as c:
        first = await replay(c)
        mine = first["segments"][0]["speaker_id"]
        assert [s["enrolled"] for s in first["speakers"]] == [False, False]  # nobody known yet

        r = await name(c, first["id"], mine, "Andrew", owner=True)
        assert r.status_code == 200
        assert r.json() == {
            "id": mine,
            "label": "Andrew",
            "ordinal": 1,
            "enrolled": True,
            "is_owner": True,
        }

        second = await replay(c)  # a different session, a new recording

    by_id = {s["id"]: s for s in second["speakers"]}
    me, other = by_id["spk_mic_0"], by_id["spk_mic_1"]
    assert (me["label"], me["enrolled"], me["is_owner"]) == ("Andrew", True, True)
    assert (other["label"], other["enrolled"], other["is_owner"]) == (None, False, False)
    # the note's transcript credits my words to "me" and the other voice to "Speaker 2"
    assert "me: seg-3" in log.calls[-1].user and "Speaker 2: seg-4" in log.calls[-1].user


async def test_naming_someone_else_enrols_them_without_making_them_me(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token = await a_user(sm)
    server = make_server(
        llm=llm(), diarizer=Voices(two_voices(ANDREW, DANA), two_voices(DANA, [0.0, 0.0, 1.0]))
    )
    async with client(server, token) as c:
        first = await replay(c)
        r = await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_1", json={"label": "Dana"})
        assert r.json()["is_owner"] is False and r.json()["enrolled"] is True
        second = await replay(c)
    assert second["speakers"][0]["label"] == "Dana"
    assert second["speakers"][0]["is_owner"] is False


async def test_renaming_without_a_voice_print_still_names_but_does_not_enrol(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token = await a_user(sm)
    plain = Diarization([Turn(0, 3000, "A"), Turn(3000, 7000, "B")])  # sidecar sent no prints
    server = make_server(llm=llm(), diarizer=Voices(plain))
    async with client(server, token) as c:
        first = await replay(c)
        r = await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_0", json={"label": "Dana"})
        assert r.status_code == 200
        assert (r.json()["label"], r.json()["enrolled"]) == ("Dana", False)
        assert (await c.get("/v1/speaker-profiles")).json() == []


async def test_only_the_sessions_owner_can_name_its_speakers(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token, stranger = await a_user(sm), await a_user(sm)
    server = make_server(llm=llm(), diarizer=Voices(two_voices(ANDREW, DANA)))
    async with client(server, token) as c:
        first = await replay(c)
    async with client(server, stranger) as c:
        r = await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_0", json={"label": "X"})
        assert r.status_code == 404
    async with client(server, token) as c:
        for bad in ({"label": "  "}, {}, {"label": "x" * 101}):
            r = await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_0", json=bad)
            assert r.status_code == 422
        r = await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_9", json={"label": "X"})
        assert r.status_code == 404


async def test_only_one_voice_is_me_and_naming_again_refines_rather_than_duplicates(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token = await a_user(sm)
    server = make_server(llm=llm(), diarizer=Voices(two_voices(ANDREW, DANA)))
    async with client(server, token) as c:
        first = await replay(c)
        sid = first["id"]
        await c.put(
            f"/v1/sessions/{sid}/speakers/spk_mic_0", json={"label": "Andrew", "is_owner": True}
        )
        r = await c.put(
            f"/v1/sessions/{sid}/speakers/spk_mic_1", json={"label": "Andrew", "is_owner": True}
        )
        assert r.status_code == 200  # the same label is the same person: one profile, refined
        profiles = (await c.get("/v1/speaker-profiles")).json()
        assert [(p["label"], p["is_owner"], p["n_samples"]) for p in profiles] == [
            ("Andrew", True, 2)
        ]
        detail = (await c.get(f"/v1/sessions/{sid}")).json()
        assert [s["is_owner"] for s in detail["speakers"]] == [True, True]


async def test_profiles_never_expose_the_voice_print_and_can_be_deleted(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token = await a_user(sm)
    server = make_server(
        llm=llm(), diarizer=Voices(two_voices(ANDREW, DANA), two_voices(ANDREW, DANA))
    )
    async with client(server, token) as c:
        first = await replay(c)
        await c.put(
            f"/v1/sessions/{first['id']}/speakers/spk_mic_0",
            json={"label": "Andrew", "is_owner": True},
        )
        (p,) = (await c.get("/v1/speaker-profiles")).json()
        assert set(p) == {"id", "label", "is_owner", "n_samples", "model_id", "created_at"}

        assert (await c.delete(f"/v1/speaker-profiles/{p['id']}")).status_code == 204
        assert (await c.get("/v1/speaker-profiles")).json() == []
        assert (await c.delete(f"/v1/speaker-profiles/{p['id']}")).status_code == 404
        # forgotten: the next session no longer recognises the voice, the old one keeps its name
        second = await replay(c)
        assert all(s["label"] is None for s in second["speakers"])
        old = (await c.get(f"/v1/sessions/{first['id']}")).json()
        assert old["speakers"][0]["label"] == "Andrew" and old["speakers"][0]["enrolled"] is False


async def test_nobody_sees_another_users_profiles(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    token, stranger = await a_user(sm), await a_user(sm)
    server = make_server(llm=llm(), diarizer=Voices(two_voices(ANDREW, DANA)))
    async with client(server, token) as c:
        first = await replay(c)
        await c.put(f"/v1/sessions/{first['id']}/speakers/spk_mic_0", json={"label": "Andrew"})
        (p,) = (await c.get("/v1/speaker-profiles")).json()
    async with client(server, stranger) as c:
        assert (await c.get("/v1/speaker-profiles")).json() == []
        assert (await c.delete(f"/v1/speaker-profiles/{p['id']}")).status_code == 404
