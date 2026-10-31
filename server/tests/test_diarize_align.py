from jarvis_live.diarize.align import Turn, assign

TURNS = [Turn(0, 5000, "a"), Turn(5000, 9000, "b"), Turn(9000, 12000, "a")]


def test_segment_inside_one_turn_gets_its_speaker() -> None:
    assert assign(1000, 4000, TURNS) == ("a", False)


def test_segment_takes_the_speaker_with_most_overlap() -> None:
    # 4000-5000 is a (1000 ms), 5000-9000 is b (4000 ms)
    assert assign(4000, 9000, TURNS) == ("b", False)


def test_a_substantial_second_speaker_marks_the_segment_mixed() -> None:
    # a: 3000 ms, b: 2000 ms of 5000 -> b is 40%, above the 25% default
    assert assign(2000, 7000, TURNS) == ("a", True)


def test_a_sliver_of_another_speaker_is_not_mixed() -> None:
    # b covers 300 of 4300 ms: 7%
    assert assign(700, 5300, TURNS) == ("a", False)


def test_no_overlapping_turn_means_unidentified() -> None:
    assert assign(20_000, 22_000, TURNS) == (None, False)


def test_too_little_coverage_means_unidentified() -> None:
    # only 200 of 4000 ms (5%) lies inside any turn
    assert assign(11_800, 15_800, TURNS) == (None, False)


def test_overlapping_turns_of_different_speakers_count_for_both() -> None:
    both = [Turn(0, 4000, "a"), Turn(0, 4000, "b")]
    speaker, mixed = assign(0, 4000, both)
    assert speaker in ("a", "b") and mixed


def test_zero_length_segment_is_unidentified() -> None:
    assert assign(1000, 1000, TURNS) == (None, False)
