"""Who a voice is: cosine matching of speaker embeddings against enrolled voices."""

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    id: str
    vector: list[float]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def match(
    clusters: dict[str, list[float]], profiles: list[Profile], *, threshold: float, margin: float
) -> dict[str, str]:
    """``cluster -> profile id`` for the voices that are clearly an enrolled one.

    A cluster needs a score of at least ``threshold`` and to beat the next-best profile by
    ``margin``, so a voice between two people is left unknown rather than guessed. A profile is
    given to one cluster only (the closest), because two voices in one channel are two people.
    Profiles built with a different embedding size are skipped.
    """
    claims: list[tuple[float, str, str]] = []  # (score, cluster, profile)
    for cluster, vector in clusters.items():
        scores = sorted(
            ((cosine(vector, p.vector), p.id) for p in profiles if len(p.vector) == len(vector)),
            reverse=True,
        )
        if not scores or scores[0][0] < threshold:
            continue
        if len(scores) > 1 and scores[0][0] - scores[1][0] < margin:
            continue
        claims.append((scores[0][0], cluster, scores[0][1]))
    out: dict[str, str] = {}
    taken: set[str] = set()
    for _, cluster, profile in sorted(claims, reverse=True):
        if profile not in taken:
            out[cluster] = profile
            taken.add(profile)
    return out


def merge(old: list[float], old_n: int, new: list[float], new_n: int) -> list[float]:
    """The mean of two voiceprints, weighted by how many samples each was built from."""
    total = old_n + new_n
    return [(a * old_n + b * new_n) / total for a, b in zip(old, new, strict=True)]
