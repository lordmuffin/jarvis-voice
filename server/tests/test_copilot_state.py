from jarvis_live.copilot.schema import (
    ActionUpsert,
    CopilotDelta,
    NoteUpsert,
    SuggestionIn,
)
from jarvis_live.copilot.state import MAX_SUGGESTIONS, CopilotState
from jarvis_live.protocol import Related


def test_missing_ids_are_new_and_server_assigned() -> None:
    st = CopilotState()
    changed = st.apply(
        CopilotDelta(
            notes_upsert=[NoteUpsert(text="one"), NoteUpsert(text="two")],
            actions_upsert=[ActionUpsert(text="Send deck", owner="sam", due="2026-10-16")],
            decisions_upsert=[NoteUpsert(text="Ship Friday")],
        ),
        now_ms=0,
    )
    assert changed and st.version == 1
    assert [n.id for n in st.notes] == ["n1", "n2"]
    assert (st.actions[0].id, st.actions[0].owner) == ("a1", "sam")
    assert st.decisions[0].id == "d1"


def test_existing_id_updates_in_place() -> None:
    st = CopilotState()
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="a"), NoteUpsert(text="b")]), 0)
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(id="n1", text="a, corrected")]), 0)
    assert [(n.id, n.text) for n in st.notes] == [("n1", "a, corrected"), ("n2", "b")]
    assert st.version == 2


def test_unknown_id_is_treated_as_new() -> None:
    st = CopilotState()
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(id="n99", text="hallucinated id")]), 0)
    assert [n.id for n in st.notes] == ["n1"]


def test_action_update_keeps_owner_when_omitted() -> None:
    st = CopilotState()
    st.apply(CopilotDelta(actions_upsert=[ActionUpsert(text="Call vendor", owner="dana")]), 0)
    st.apply(CopilotDelta(actions_upsert=[ActionUpsert(id="a1", text="Call vendor by Tuesday")]), 0)
    assert (st.actions[0].text, st.actions[0].owner) == ("Call vendor by Tuesday", "dana")


def test_repeated_text_is_not_duplicated_and_is_not_a_change() -> None:
    st = CopilotState()
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="Budget is 10k")]), 0)
    v = st.version
    assert not st.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="  budget is 10K ")]), 0)
    assert len(st.notes) == 1 and st.version == v


def test_empty_delta_does_not_bump_version() -> None:
    st = CopilotState()
    assert not st.apply(CopilotDelta(), 0)
    assert st.version == 0


def test_remove_ids_across_kinds_and_ids_not_reused() -> None:
    st = CopilotState()
    st.apply(
        CopilotDelta(
            notes_upsert=[NoteUpsert(text="n")],
            actions_upsert=[ActionUpsert(text="a")],
            suggestions=[SuggestionIn(kind="gap", text="s")],
        ),
        0,
    )
    st.apply(CopilotDelta(remove_ids=["n1", "a1", "s1"]), 0)
    assert not st.notes and not st.actions and not st.suggestions
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="again")]), 0)
    assert st.notes[0].id == "n2"


def test_suggestions_expire_server_side() -> None:
    st = CopilotState()
    st.apply(
        CopilotDelta(
            suggestions=[
                SuggestionIn(kind="question", text="short", ttl_s=10),
                SuggestionIn(kind="gap", text="long", ttl_s=120),
            ]
        ),
        now_ms=5_000,
    )
    assert [s.expires_at_ms for s in st.suggestions] == [15_000, 125_000]
    assert not st.expire(14_999)
    v = st.version
    assert st.expire(15_000)
    assert [s.text for s in st.suggestions] == ["long"] and st.version == v + 1


def test_suggestion_cap_drops_oldest() -> None:
    st = CopilotState()
    st.apply(
        CopilotDelta(
            suggestions=[SuggestionIn(kind="gap", text=f"s{i}") for i in range(MAX_SUGGESTIONS + 2)]
        ),
        0,
    )
    assert [s.text for s in st.suggestions] == [f"s{i}" for i in range(2, MAX_SUGGESTIONS + 2)]


def test_snapshot_roundtrip_continues_ids() -> None:
    st = CopilotState()
    st.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="a"), NoteUpsert(text="b")]), 0)
    st.set_related([Related(path="X.md", title="X", snippet="s", uri="u")])
    restored = CopilotState.from_snapshot(st.snapshot())
    assert restored.version == st.version and restored.shown == {"X.md"}
    restored.apply(CopilotDelta(notes_upsert=[NoteUpsert(text="c")]), 0)
    assert restored.notes[-1].id == "n3"
