"""The optional local cleanup: standalone fillers and stutters go, real words stay."""

import pytest

from jarvis_desktop.filler import clean


@pytest.mark.parametrize(
    ("spoken", "cleaned"),
    [
        ("So, um, I think we should ship it.", "So, I think we should ship it."),
        ("Um, I think we should ship it.", "I think we should ship it."),
        ("um so we moved it", "So we moved it"),
        ("We moved it, uh.", "We moved it."),
        ("We moved it uh to Thursday.", "We moved it to Thursday."),
        ("Erm, hmm, okay.", "Okay."),
        ("Umm uhh the plan is fine.", "The plan is fine."),
        ("I I think so.", "I think so."),
        ("We need to to check the the logs.", "We need to check the logs."),
        ("Is it ready? Um, yes.", "Is it ready? Yes."),
    ],
)
def test_removes_fillers_and_stutters(spoken: str, cleaned: str) -> None:
    assert clean(spoken) == cleaned


@pytest.mark.parametrize(
    "kept",
    [
        "Bring an umbrella.",
        "Uh-huh, that works.",
        "Captain Uhura called.",
        "I know that that works.",
        "She had had enough.",
        'He wrote "hmm" in the margin.',
        "The ERM rollout is next.",
        "",
    ],
)
def test_keeps_real_words(kept: str) -> None:
    assert clean(kept) == kept


def test_only_fillers_leaves_nothing() -> None:
    assert clean("Um, uh.") == ""
