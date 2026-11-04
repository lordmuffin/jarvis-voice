"""A voice proposal that adds nothing the speaker said is a duplicate of the stored one.

The rule (``actions.state.adds_nothing``): a proposal repeats a stored one of the same kind when
each of its fields either equals the stored field (case, spacing and end punctuation aside) or is a
date field (``due``, ``at``) and the request named no date or time. A field the stored one has
and the new one lacks adds nothing. Anything else, a new word in the text or a date the speaker
said, makes it new. The copilot's dedupe is unchanged: it passes no words."""

from datetime import UTC, datetime, timedelta

import pytest

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.state import Context, ProposalState, says_when

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
EV = [Evidence(start_ms=1_000, text="Jarvis, add a task")]
CTX = Context(NOW, "UTC", lambda _: False, frozenset({"lordmuffin/jarvis-voice"}))


def task(text: str = "Review the pricing proposal", **kw: object) -> ProposalIn:
    return ProposalIn(kind="task.create", text=text, evidence=EV, **kw)  # type: ignore[arg-type]


def reminder(title: str, at: datetime, **kw: object) -> ProposalIn:
    return ProposalIn(
        kind="reminder.schedule",
        title=title,
        at=at.isoformat(),
        evidence=EV,
        **kw,  # type: ignore[arg-type]
    )


def stored(*proposals: ProposalIn) -> ProposalState:
    st = ProposalState()
    assert len(st.apply(list(proposals), CTX)) == len(proposals)
    return st


SAID = "Jarvis, add a task to review the pricing proposal."


def test_a_repeat_with_a_due_date_nobody_said_is_a_duplicate() -> None:
    st = stored(task())
    assert st.apply([task(due="2026-11-04")], CTX, said=SAID) == []
    assert len(st.proposals) == 1


def test_a_repeat_with_a_newly_spoken_date_is_not_a_duplicate() -> None:
    st = stored(task())
    said = "Jarvis, add a task to review the pricing proposal by Friday."
    [new] = st.apply([task(due="2026-11-06")], CTX, said=said)
    assert new.payload == {"text": "Review the pricing proposal", "due": "2026-11-06"}


def test_a_repeat_that_drops_a_stored_due_date_adds_nothing() -> None:
    st = stored(task(due="2026-11-06"))
    assert st.apply([task()], CTX, said=SAID) == []


def test_a_repeat_differing_only_in_case_spacing_and_end_punctuation_is_a_duplicate() -> None:
    st = stored(task())
    assert st.apply([task("review the  Pricing proposal.", due="2026-11-05")], CTX, said=SAID) == []


def test_new_words_in_the_text_make_it_new() -> None:
    st = stored(task())
    assert len(st.apply([task("Review the pricing proposal with Sam")], CTX, said=SAID)) == 1


def test_a_repeat_of_a_closed_proposal_is_still_a_duplicate() -> None:
    st = stored(task())
    st.close("p1")
    assert st.apply([task(due="2026-11-04")], CTX, said=SAID) == []


def test_the_same_task_twice_in_one_request_is_filed_once() -> None:
    st = ProposalState()
    new = st.apply([task(), task(due="2026-11-04")], CTX, said=SAID)
    assert [p.payload for p in new] == [{"text": "Review the pricing proposal"}]


def test_another_kind_with_the_same_words_is_not_a_duplicate() -> None:
    st = stored(task("Send the invoice"))
    at = NOW + timedelta(days=1)
    assert len(st.apply([reminder("Send the invoice", at)], CTX, said="Jarvis, the invoice")) == 1


def test_a_reminder_moved_to_a_time_nobody_said_is_a_duplicate() -> None:
    st = stored(reminder("Look at the churn numbers", NOW + timedelta(days=1)))
    again = reminder("Look at the churn numbers", NOW + timedelta(days=2))
    assert st.apply([again], CTX, said="Jarvis, remind me to look at the churn numbers") == []


def test_a_reminder_at_a_newly_spoken_time_is_not_a_duplicate() -> None:
    st = stored(reminder("Look at the churn numbers", NOW + timedelta(days=1)))
    again = reminder("Look at the churn numbers", NOW + timedelta(days=2))
    said = "Jarvis, remind me on Friday to look at the churn numbers"
    assert len(st.apply([again], CTX, said=said)) == 1


def test_a_new_reminder_body_makes_it_new() -> None:
    at = NOW + timedelta(days=1)
    st = stored(reminder("Look at the churn numbers", at))
    again = reminder("Look at the churn numbers", at, body="Bring the deck")
    said = "Jarvis, remind me to look at the churn numbers and bring the deck"
    assert len(st.apply([again], CTX, said=said)) == 1


def test_the_copilot_still_files_a_repeat_that_differs_in_its_due_date() -> None:
    st = stored(task())
    assert len(st.apply([task(due="2026-11-04")], CTX)) == 1


@pytest.mark.parametrize(
    "said",
    [
        "by Friday",
        "on monday",
        "tomorrow",
        "today",
        "tonight",
        "next week",
        "this weekend",
        "at nine",
        "at 9",
        "at 9:30",
        "on the 5th",
        "on the fifth",
        "at noon",
        "in the afternoon",
        "in two hours",
        "at ten o'clock",
        "in December",
        "end of the month",
    ],
)
def test_a_spoken_date_or_time_counts_as_said(said: str) -> None:
    assert says_when(f"Jarvis, add a task to review the pricing proposal {said}.")


@pytest.mark.parametrize(
    "said",
    [
        "Jarvis, add a task to review the pricing proposal.",
        "Jarvis, add a task to review the phone bill.",  # "one" inside a word
        "Jarvis, add a task to order a sundae machine.",  # "sunday" is not inside it
        "Jarvis, add a task to answer the someone email.",
        "Jarvis, I am adding a task to review the pricing proposal.",  # "am" alone is not a time
    ],
)
def test_words_that_only_contain_a_date_word_do_not(said: str) -> None:
    assert not says_when(said)
