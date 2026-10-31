from datetime import UTC, datetime, timedelta

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.state import (
    MAX_NEW_PER_CYCLE,
    MAX_OPEN,
    Context,
    ProposalState,
    payload_sha256,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
EV = [Evidence(start_ms=61_000, text="add a task to call the vendor")]


def ctx(**kw: object) -> Context:
    base: dict[str, object] = {
        "now": NOW,
        "timezone": "UTC",
        "vault_has": lambda path: path == "30 Resources/Sleep.md",
        "repos": frozenset({"lordmuffin/jarvis-voice"}),
    }
    base.update(kw)
    return Context(**base)  # type: ignore[arg-type]


def task(text: str = "Call the vendor", **kw: object) -> ProposalIn:
    return ProposalIn(kind="task.create", text=text, evidence=EV, **kw)  # type: ignore[arg-type]


def test_valid_task_gets_server_ref_and_hash() -> None:
    st = ProposalState()
    new = st.apply([task()], ctx())
    assert [(p.ref, p.kind) for p in new] == [("p1", "task.create")]
    assert new[0].payload == {"text": "Call the vendor"}
    assert new[0].payload_sha256 == payload_sha256("task.create", {"text": "Call the vendor"})


def test_task_text_is_one_line_without_control_chars() -> None:
    st = ProposalState()
    [p] = st.apply([task("Call\nthe\tvendor\x00 \n- [x] injected")], ctx())
    assert p.payload["text"] == "Call the vendor - [x] injected"


def test_proposal_without_evidence_is_dropped() -> None:
    st = ProposalState()
    assert st.apply([ProposalIn(kind="task.create", text="x", evidence=[])], ctx()) == []


def test_restated_proposal_is_not_duplicated() -> None:
    st = ProposalState()
    st.apply([task("Call the vendor")], ctx())
    assert st.apply([task("  call the  VENDOR ")], ctx()) == []
    assert len(st.proposals) == 1


def test_closed_proposal_is_not_proposed_again() -> None:
    st = ProposalState()
    [p] = st.apply([task()], ctx())
    st.close(p.ref)
    assert st.apply([task()], ctx()) == []


def test_reminder_must_be_in_the_future_and_within_90_days() -> None:
    st = ProposalState()

    def rem(at: datetime) -> ProposalIn:
        return ProposalIn(
            kind="reminder.schedule", title="Send invoice", at=at.isoformat(), evidence=EV
        )

    assert st.apply([rem(NOW - timedelta(minutes=1))], ctx()) == []
    assert st.apply([rem(NOW + timedelta(days=91))], ctx()) == []
    assert len(st.apply([rem(NOW + timedelta(days=2))], ctx())) == 1


def test_naive_reminder_time_uses_the_display_timezone() -> None:
    st = ProposalState()
    [p] = st.apply(
        [ProposalIn(kind="reminder.schedule", title="t", at="2026-11-03T09:00:00", evidence=EV)],
        ctx(timezone="America/Chicago"),
    )
    assert p.payload["at"] == "2026-11-03T15:00:00+00:00"


def test_vault_append_needs_an_existing_indexed_markdown_note() -> None:
    st = ProposalState()

    def app(path: str) -> ProposalIn:
        return ProposalIn(kind="vault.append", path=path, text="note this", evidence=EV)

    assert st.apply([app("30 Resources/Missing.md")], ctx()) == []
    assert st.apply([app("40 Archives/Old.md")], ctx(vault_has=lambda p: True)) == []
    assert st.apply([app("../etc/passwd")], ctx(vault_has=lambda p: True)) == []
    assert st.apply([app("30 Resources/notes.txt")], ctx(vault_has=lambda p: True)) == []
    assert len(st.apply([app("30 Resources/Sleep.md")], ctx())) == 1


def test_pr_comment_repo_must_be_allowlisted() -> None:
    st = ProposalState()

    def cm(repo: str) -> ProposalIn:
        return ProposalIn(kind="git.pr_comment", repo=repo, pr=12, body="LGTM", evidence=EV)

    assert st.apply([cm("someone/else")], ctx()) == []
    assert len(st.apply([cm("lordmuffin/jarvis-voice")], ctx())) == 1


def test_cap_per_cycle_and_open_cap() -> None:
    st = ProposalState()
    batch = [task(f"task {i}") for i in range(MAX_NEW_PER_CYCLE + 2)]
    assert len(st.apply(batch, ctx())) == MAX_NEW_PER_CYCLE
    for cycle in range(10):
        st.apply([task(f"c{cycle} {i}") for i in range(MAX_NEW_PER_CYCLE)], ctx())
    assert len([p for p in st.proposals if p.open]) == MAX_OPEN


def test_hash_ignores_key_order_and_changes_with_content() -> None:
    a = payload_sha256("vault.append", {"path": "p.md", "text": "t"})
    assert a == payload_sha256("vault.append", {"text": "t", "path": "p.md"})
    assert a != payload_sha256("vault.append", {"path": "p.md", "text": "t2"})
