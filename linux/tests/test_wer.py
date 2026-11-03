"""Word error rate between a reference and a hypothesis, after light normalisation."""

import pytest

from jarvis_desktop.stt.wer import corpus_wer, edits, normalise


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Okay, quick update.", ["okay", "quick", "update"]),
        ("I'm moving it — to Thursday!", ["i'm", "moving", "it", "to", "thursday"]),
        ("Re-run the follow-up", ["re", "run", "the", "follow", "up"]),
        ("  ", []),
        ("“Quoted” ‘text’", ["quoted", "text"]),
    ],
)
def test_normalise_lowercases_and_drops_punctuation(raw: str, norm: list[str]) -> None:
    assert normalise(raw) == norm


def test_edits_counts_substitutions_insertions_and_deletions() -> None:
    assert edits("the cat sat", "the cat sat") == 0
    assert edits("the cat sat", "the bat sat") == 1
    assert edits("the cat sat", "the cat sat down") == 1
    assert edits("the cat sat", "cat sat") == 1
    assert edits("", "anything here") == 2
    assert edits("Okay, update.", "okay update") == 0


def test_corpus_wer_weights_by_reference_length() -> None:
    pairs = [("one two three four", "one two three four"), ("five six", "five seven")]
    assert corpus_wer(pairs) == pytest.approx(1 / 6)


def test_corpus_wer_of_nothing_is_zero() -> None:
    assert corpus_wer([]) == 0.0
