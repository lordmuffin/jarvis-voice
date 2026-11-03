"""Word error rate, for scoring engines against a reference transcript. Pure.

Normalisation is deliberately light (case, punctuation, hyphens, curly quotes), so engines are
compared on words, not on formatting. Numbers are not normalised: "ten" and "10" count as an
error, so compare engines against each other on the same references, not to published WERs."""

import re

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'"})
_NOT_WORD = re.compile(r"[^\w']+")


def normalise(text: str) -> list[str]:
    words = _NOT_WORD.sub(" ", text.translate(_APOSTROPHES).lower().replace("_", " ")).split()
    return [w.strip("'") for w in words if w.strip("'")]


def edits(reference: str, hypothesis: str) -> int:
    """Word-level Levenshtein distance after normalisation."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def corpus_wer(pairs: list[tuple[str, str]]) -> float:
    """Total edits over total reference words, for ``(reference, hypothesis)`` pairs."""
    words = sum(len(normalise(ref)) for ref, _ in pairs)
    return sum(edits(ref, hyp) for ref, hyp in pairs) / words if words else 0.0
