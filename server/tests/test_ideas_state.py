"""IdeasState: the server-side rules applied to whatever the LLM proposes. Pure, no DB."""

from jarvis_live.workitems.schema import IdeasDelta, ItemUpsert
from jarvis_live.workitems.state import (
    MAX_CRITERIA,
    MAX_OPEN_ITEMS,
    IdeasState,
    Item,
    number_lines,
)
from tests.ideas_fixtures import lines

REPOS = {
    "lordmuffin/jarvis-voice": "Jarvis Live server, web dashboard, apps",
    "lordmuffin/np-home-homelab": "homelab GitOps",
}
DIARY = lines("diary_three_ideas.txt")


def delta(*items: ItemUpsert) -> IdeasDelta:
    return IdeasDelta(items_upsert=list(items))


def gotify(**kw: object) -> ItemUpsert:
    base: dict[str, object] = {
        "title": "Retry failed Gotify notifications",
        "repo": "lordmuffin/jarvis-voice",
        "intent": "Gotify pushes vanish when Gotify restarts; retry with backoff.",
        "acceptance_criteria": ["A failed push is retried with backoff"],
        "source_lines": ["L2", "L3"],
    }
    return ItemUpsert.model_validate({**base, **kw})


def test_transcript_is_numbered_and_refs_map_to_segments() -> None:
    text, refs = number_lines(DIARY[:3])
    assert text.splitlines()[1] == (
        "L2 [00:21] me: The Gotify pushes from jarvis live sometimes just vanish when Gotify "
        "restarts."
    )
    assert refs["L2"].segment_id == str(DIARY[1].segment_id)
    assert refs["L2"].start_ms == 21_000


def test_new_items_get_server_refs_and_cite_the_transcript() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    changes = st.apply(
        delta(
            gotify(),
            ItemUpsert(
                title="Alert on TrueNAS nfsd saturation",
                repo="lordmuffin/np-home-homelab",
                source_lines=["L5", "L6"],
            ),
        ),
        refs,
    )
    assert [(c.item.ref, c.created) for c in changes] == [("w1", True), ("w2", True)]
    w1 = st.items["w1"]
    assert [s.text for s in w1.sources] == [DIARY[1].text, DIARY[2].text]
    assert w1.repo == "lordmuffin/jarvis-voice"


def test_an_item_without_valid_source_lines_is_dropped() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    assert (
        st.apply(delta(gotify(source_lines=[]), gotify(title="x", source_lines=["L99"])), refs)
        == []
    )
    assert st.items == {}


def test_line_refs_are_tolerant() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    st.apply(delta(gotify(source_lines=["l2", " 3 ", 3])), refs)  # type: ignore[list-item]
    assert len(st.items["w1"].sources) == 2


def test_a_later_pass_attaches_a_revisit_to_the_existing_item() -> None:
    """Clustering across the session: the Gotify idea at 00:21 and again at 21:03."""
    first, second = DIARY[:9], DIARY[7:]  # second pass: overlap plus new lines
    _, refs1 = number_lines(first)
    st = IdeasState([], REPOS)
    st.apply(delta(gotify()), refs1)

    _, refs2 = number_lines(second)
    changes = st.apply(
        delta(
            gotify(
                ref="w1",
                acceptance_criteria=[
                    "A failed push is retried with backoff",
                    "At most three attempts; giving up is logged",
                    "A test covers Gotify being down for the first two tries",
                ],
                source_lines=["L4", "L5"],  # 21:03 and 21:20 in this pass's numbering
            )
        ),
        refs2,
    )
    assert [(c.item.ref, c.created) for c in changes] == [("w1", False)]
    assert set(changes[0].fields) == {"acceptance_criteria", "sources"}
    w1 = st.items["w1"]
    assert [s.start_ms for s in w1.sources] == [21_000, 34_000, 1_263_000, 1_280_000]
    assert len(w1.acceptance_criteria) == 3


def test_a_proposal_titled_like_an_open_draft_refines_it() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    st.apply(delta(gotify()), refs)
    changes = st.apply(
        delta(gotify(title="retry failed gotify notifications!", source_lines=["L11"])), refs
    )
    assert len(st.items) == 1
    assert changes[0].item.ref == "w1" and not changes[0].created


def test_fields_the_owner_edited_are_locked() -> None:
    _, refs = number_lines(DIARY)
    owned = Item(
        ref="w1",
        title="My title",
        repo="lordmuffin/jarvis-voice",
        intent="My words",
        locked_fields=["title", "intent"],
    )
    st = IdeasState([owned], REPOS)
    changes = st.apply(delta(gotify(ref="w1", title="LLM title", intent="LLM words")), refs)
    assert owned.title == "My title" and owned.intent == "My words"
    assert set(changes[0].fields) == {"acceptance_criteria", "sources"}


def test_closed_items_stay_closed() -> None:
    _, refs = number_lines(DIARY)
    items = [
        Item(ref="w1", title="Retry failed Gotify notifications", state="discarded"),
        Item(ref="w2", title="Alert on TrueNAS nfsd saturation", state="approved"),
        Item(ref="w3", title="Merged away", state="merged"),
    ]
    st = IdeasState(items, REPOS)
    changes = st.apply(
        delta(
            gotify(),  # same title as the discarded one: not proposed again
            ItemUpsert(ref="w2", title="Changed", source_lines=["L5"]),  # approved: read-only
            ItemUpsert(ref="w3", title="Merged away", source_lines=["L6"]),
        ),
        refs,
    )
    assert changes == []
    assert items[1].title == "Alert on TrueNAS nfsd saturation"


def test_a_ref_the_server_never_issued_is_a_new_item() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    st.apply(delta(gotify(ref="w7")), refs)
    assert list(st.items) == ["w1"]


def test_repos_must_be_on_the_allowlist_and_are_never_cleared() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    st.apply(
        delta(
            ItemUpsert(title="SRT export CLI", repo="someone/else", source_lines=["L8"]),
            gotify(),
        ),
        refs,
    )
    assert st.items["w1"].repo is None
    st.apply(delta(gotify(ref="w2", repo=None)), refs)
    assert st.items["w2"].repo == "lordmuffin/jarvis-voice"


def test_caps_hold() -> None:
    _, refs = number_lines(DIARY)
    st = IdeasState([], REPOS)
    st.apply(delta(gotify(acceptance_criteria=[f"criterion {i}" for i in range(20)])), refs)
    assert len(st.items["w1"].acceptance_criteria) == MAX_CRITERIA
    many = [ItemUpsert(title=f"Idea number {i}", source_lines=["L1"]) for i in range(30)]
    st.apply(delta(*many), refs)
    assert sum(it.state == "draft" for it in st.items.values()) == MAX_OPEN_ITEMS
