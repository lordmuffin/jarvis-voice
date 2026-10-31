import math

import pytest

from jarvis_live.diarize.identify import Profile, cosine, match, merge

ANDREW = Profile("andrew", [1.0, 0.0, 0.0])
DANA = Profile("dana", [0.0, 1.0, 0.0])


def near(v: list[float], tilt: float) -> list[float]:
    """A vector at ``tilt`` radians from ``v``'s axis, in the plane of the first two axes."""
    return [
        math.cos(tilt) * v[0] - math.sin(tilt) * v[1],
        math.sin(tilt) * v[0] + math.cos(tilt) * v[1],
        0.0,
    ]


def test_cosine_of_parallel_and_orthogonal_vectors() -> None:
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 3]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 0]) == 0.0  # a silent embedding matches nothing


def test_a_close_voice_gets_the_profile() -> None:
    got = match({"c0": near(ANDREW.vector, 0.2)}, [ANDREW, DANA], threshold=0.8, margin=0.1)
    assert got == {"c0": "andrew"}


def test_below_the_threshold_stays_unknown() -> None:
    # 0.8 rad is 0.70 from Andrew and 0.72 from Dana: neither reaches 0.9
    got = match({"c0": near(ANDREW.vector, 0.8)}, [ANDREW, DANA], threshold=0.9, margin=0.0)
    assert got == {}


def test_a_voice_between_two_profiles_is_not_guessed() -> None:
    between = near(ANDREW.vector, math.pi / 4)  # equally far from Andrew and Dana
    assert match({"c0": between}, [ANDREW, DANA], threshold=0.5, margin=0.1) == {}


def test_two_clusters_cannot_both_be_the_same_person() -> None:
    clusters = {"close": near(ANDREW.vector, 0.1), "further": near(ANDREW.vector, 0.4)}
    got = match(clusters, [ANDREW], threshold=0.8, margin=0.0)
    assert got == {"close": "andrew"}


def test_no_profiles_or_no_embeddings_match_nothing() -> None:
    assert match({"c0": [1.0, 0.0, 0.0]}, [], threshold=0.5, margin=0.0) == {}
    assert match({}, [ANDREW], threshold=0.5, margin=0.0) == {}


def test_a_profile_of_another_size_is_ignored() -> None:
    assert match({"c0": [1.0, 0.0]}, [ANDREW], threshold=0.5, margin=0.0) == {}


def test_merge_is_a_weighted_mean() -> None:
    assert merge([1.0, 0.0], 3, [0.0, 1.0], 1) == pytest.approx([0.75, 0.25])
