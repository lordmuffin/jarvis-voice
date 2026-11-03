"""Hold to ask: the audio an ask is cut from (docs/plans/voice-push-to-talk.md). Pure parts."""

from jarvis_live.voice.ask import AskAudio

MS = 32  # bytes per ms of PCM16 16 kHz mono


def frame(value: int, ms: int = 100) -> bytes:
    return bytes([value]) * (ms * MS)


def fed(audio: AskAudio, start_t: int, values: list[int]) -> None:
    for i, v in enumerate(values):
        audio.feed(start_t + i * 100, frame(v))


def test_an_ask_is_the_frames_between_the_markers_plus_preroll() -> None:
    audio = AskAudio(preroll_ms=200, max_ms=30_000)
    fed(audio, 0, [1, 2, 3, 4])  # t 0..400
    audio.begin("a", 400)
    fed(audio, 400, [5, 6, 7])  # t 400..700
    audio.end("a", 700)
    assert audio.complete("a")
    clip = audio.take("a")
    assert clip is not None
    assert clip.pcm == frame(3) + frame(4) + frame(5) + frame(6) + frame(7)
    assert (clip.start_ms, clip.end_ms) == (200, 700)


def test_an_ask_waits_for_the_frame_that_carries_its_end() -> None:
    audio = AskAudio(preroll_ms=0, max_ms=30_000)
    audio.begin("a", 0)
    fed(audio, 0, [1, 2])  # t 0..200
    audio.end("a", 250)  # the release overtook its last frame
    assert not audio.complete("a")
    fed(audio, 200, [3])
    assert audio.complete("a")


def test_positions_count_audio_fed_not_the_clients_clock() -> None:
    audio = AskAudio(preroll_ms=0, max_ms=30_000)
    fed(audio, 5_000, [1, 2])  # the Mac's frames start at an offset
    audio.begin("a", 5_200)
    fed(audio, 5_200, [3])
    audio.end("a", 5_300)
    clip = audio.take("a")
    assert clip is not None
    assert (clip.start_ms, clip.end_ms) == (200, 300)


def test_without_an_ask_only_the_preroll_is_kept() -> None:
    audio = AskAudio(preroll_ms=200, max_ms=30_000)
    fed(audio, 0, list(range(1, 51)))
    assert audio.buffered_ms() <= 300


def test_a_long_hold_is_cut_at_the_limit() -> None:
    audio = AskAudio(preroll_ms=0, max_ms=1_000)
    audio.begin("a", 0)
    fed(audio, 0, list(range(1, 21)))  # 2 s held
    assert audio.complete("a")
    clip = audio.take("a")
    assert clip is not None
    assert clip.end_ms - clip.start_ms == 1_000


def test_a_new_ask_replaces_one_still_held() -> None:
    audio = AskAudio(preroll_ms=0, max_ms=30_000)
    audio.begin("a", 0)
    fed(audio, 0, [1])
    audio.begin("b", 100)
    fed(audio, 100, [2])
    audio.end("b", 200)
    assert audio.take("a") is None
    clip = audio.take("b")
    assert clip is not None and clip.pcm == frame(2)


def test_unknown_or_unended_asks_give_nothing() -> None:
    audio = AskAudio(preroll_ms=0, max_ms=30_000)
    assert not audio.complete("x")
    assert audio.take("x") is None
    audio.begin("a", 0)
    fed(audio, 0, [1])
    assert not audio.complete("a")


def test_windows_cover_held_and_finished_asks_in_stream_position() -> None:
    audio = AskAudio(preroll_ms=200, max_ms=30_000)
    fed(audio, 0, [1, 2, 3])
    audio.begin("a", 300)
    assert audio.windows() == [(100, None)]
    fed(audio, 300, [4, 5])
    audio.end("a", 500)
    audio.take("a")
    assert audio.windows() == [(100, 500)]


# --- what is spoken -------------------------------------------------------------------------

from jarvis_live.voice.ask import EchoGuard, spoken  # noqa: E402


def test_spoken_strips_markdown_and_keeps_short_replies_whole() -> None:
    assert spoken("**Priya** owns the `survey`.\n- Due Friday.", 40) == (
        "Priya owns the survey. Due Friday."
    )


def test_spoken_cuts_a_long_reply_at_a_sentence_inside_the_limit() -> None:
    text = "One two three. Four five six seven. Eight nine ten eleven twelve."
    assert spoken(text, 8) == "One two three. Four five six seven."


def test_spoken_cuts_one_long_sentence_at_the_limit() -> None:
    assert spoken("one two three four five six", 4) == "one two three four."


def test_spoken_of_nothing_is_nothing() -> None:
    assert spoken("  **  ", 40) == ""


# --- Jarvis heard by a mic ------------------------------------------------------------------


def test_jarvis_words_inside_the_played_window_are_echo() -> None:
    guard = EchoGuard(pad_ms=300, threshold=0.6)
    guard.add(10_000, 13_000, "Priya owns the survey, due Friday.")
    assert guard.is_echo(10_500, 12_900, "priya owns the survey due friday")


def test_real_speech_over_jarvis_is_kept() -> None:
    guard = EchoGuard(pad_ms=300, threshold=0.6)
    guard.add(10_000, 13_000, "Priya owns the survey, due Friday.")
    assert not guard.is_echo(10_500, 12_900, "no wait, I think Tomas took that one over")


def test_the_same_words_outside_the_window_are_kept() -> None:
    guard = EchoGuard(pad_ms=300, threshold=0.6)
    guard.add(10_000, 13_000, "Priya owns the survey, due Friday.")
    assert not guard.is_echo(20_000, 22_000, "Priya owns the survey, due Friday.")


def test_a_window_still_playing_is_open_ended() -> None:
    guard = EchoGuard(pad_ms=300, threshold=0.6)
    guard.add(10_000, None, "Priya owns the survey, due Friday.")
    assert guard.is_echo(15_000, 16_000, "owns the survey due friday")
    guard.close(16_500)
    assert not guard.is_echo(30_000, 31_000, "owns the survey due friday")
