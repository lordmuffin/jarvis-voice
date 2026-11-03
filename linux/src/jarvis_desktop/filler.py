"""Optional local cleanup of dictated text (off by default). Pure.

Removes standalone hesitation words and stutters of short function words, and repairs the
punctuation and capital letter around what it removed. Anything inside double quotes is left as
said, and so is an all-caps word (``ERM`` is an acronym, not a hesitation). Repeats of other words
stay, because "that that" and "had had" are grammatical."""

import re

FILLERS = frozenset({"um", "umm", "uh", "uhh", "erm", "hmm", "mm"})
STUTTER_WORDS = frozenset({"i", "a", "an", "the", "to", "and", "we", "it", "so", "but"})
_TERMINAL = ".?!"
_SPLIT = re.compile(r"^(\W*)(.*?)([,.;:!?]*)$")


def clean(text: str) -> str:
    kept: list[str] = []
    capitalize_next = False
    in_quote = False
    for token in text.split():
        quoted = in_quote or token.startswith('"')
        in_quote ^= token.count('"') % 2 == 1
        match = _SPLIT.match(token)
        assert match is not None  # the pattern matches any string
        _, core, punct = match.groups()
        if not quoted and _is_filler(core):
            at_sentence_start = not kept or kept[-1][-1] in _TERMINAL
            if any(c in _TERMINAL for c in punct) and kept:
                kept[-1] = kept[-1].rstrip(",;:") + punct.lstrip(",;:")
            capitalize_next = capitalize_next or at_sentence_start
            continue
        if not quoted and kept and _is_stutter(kept[-1], core):
            continue
        kept.append(_capitalize(token) if capitalize_next else token)
        capitalize_next = False
    return " ".join(kept)


def _is_filler(core: str) -> bool:
    return core.lower() in FILLERS and not (len(core) > 1 and core.isupper())


def _is_stutter(previous: str, core: str) -> bool:
    return core.lower() in STUTTER_WORDS and previous.lower() == core.lower()


def _capitalize(token: str) -> str:
    for i, c in enumerate(token):
        if c.isalpha():
            return token[:i] + c.upper() + token[i + 1 :]
    return token
