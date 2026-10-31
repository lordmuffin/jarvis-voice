import math

from jarvis_live.diarize.live import LiveSpeakers, ProfileRef

M = "m1"


def at(angle: float) -> list[float]:
    return [math.cos(angle), math.sin(angle), 0.0]


def tracker(profiles: list[ProfileRef] | None = None) -> LiveSpeakers:
    return LiveSpeakers(
        profiles or [], cluster_threshold=0.8, match_threshold=0.85, match_margin=0.05
    )


def test_the_same_voice_keeps_one_id_and_a_different_one_gets_another() -> None:
    t = tracker()
    first, new1 = t.assign("system", at(0.0), M)
    again, new2 = t.assign("system", at(0.1), M)
    other, new3 = t.assign("system", at(1.5), M)
    assert (first.id, new1) == ("live_system_0", True)
    assert (again.id, new2) == (first.id, False)
    assert (other.id, new3) == ("live_system_1", True)


def test_channels_are_told_apart_separately() -> None:
    t = tracker()
    assert t.assign("mic", at(0.0), M)[0].id == "live_mic_0"
    assert t.assign("system", at(0.0), M)[0].id == "live_system_0"


def test_a_voice_drifting_slowly_stays_one_speaker() -> None:
    t = tracker()
    ids = {
        t.assign("mic", at(0.15 * i), M)[0].id for i in range(6)
    }  # each step is close to the last
    assert ids == {"live_mic_0"}


def test_an_enrolled_voice_is_named_and_marked_as_me() -> None:
    andrew = ProfileRef("p1", "Andrew", True, M, at(0.0))
    t = tracker([andrew])
    voice, new = t.assign("mic", at(0.05), M)
    assert new and (voice.label, voice.is_owner, voice.profile_id) == ("Andrew", True, "p1")
    again, new_again = t.assign("mic", at(0.0), M)
    assert again.id == voice.id and not new_again


def test_a_stranger_beside_an_enrolled_voice_is_not_named() -> None:
    andrew = ProfileRef("p1", "Andrew", True, M, at(0.0))
    t = tracker([andrew])
    stranger, _ = t.assign("mic", at(1.4), M)
    assert (stranger.label, stranger.is_owner, stranger.profile_id) == (None, False, None)


def test_a_print_from_another_model_never_matches_a_profile_or_a_cluster() -> None:
    andrew = ProfileRef("p1", "Andrew", True, "older", at(0.0))
    t = tracker([andrew])
    a, _ = t.assign("mic", at(0.0), M)
    assert a.label is None
    b, new = t.assign("mic", at(0.0), "newer-still")
    assert new and b.id != a.id  # same numbers, different space: a different voice


def test_the_voiceprint_follows_the_voice() -> None:
    t = tracker()
    t.assign("mic", [1.0, 0.0, 0.0], M)
    voice, _ = t.assign("mic", [0.8, 0.6, 0.0], M)  # within the cluster threshold
    assert voice.embedding is not None and voice.embedding[1] > 0  # moved towards the new print
    assert voice.model_id == M


def test_an_empty_embedding_is_not_assigned() -> None:
    assert tracker().assign("mic", [0.0, 0.0, 0.0], M) is None
