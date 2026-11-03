"""Twins of macos/JarvisVoiceMac/Tests/DictationTriggerTests.swift, plus Linux autorepeat.

The rules are the Mac's: a lone right-hand modifier, hold waits out a threshold, a chord never
starts dictation and cancels a hold that has begun, Esc cancels."""

from jarvis_desktop.trigger import Action, DictationTrigger, TriggerKey, TriggerMode

HOLD = DictationTrigger.HOLD_THRESHOLD
TAP_MAX = DictationTrigger.TAP_MAX_DURATION
GAP = DictationTrigger.DOUBLE_TAP_GAP


def make(mode: TriggerMode, key: TriggerKey = TriggerKey.RIGHT_CTRL) -> DictationTrigger:
    return DictationTrigger(key, mode)


# --- hold -------------------------------------------------------------------------------------


def test_hold_begins_after_threshold_and_commits_on_release() -> None:
    t = make(TriggerMode.HOLD)
    assert t.key_down(10) is None
    assert t.tick(10 + HOLD) is Action.BEGIN
    assert t.tick(10 + HOLD + 1) is None, "begins once per press"
    assert t.key_up(13) is Action.COMMIT


def test_hold_released_before_threshold_does_nothing() -> None:
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    assert t.key_up(10.1) is None
    assert t.tick(10 + HOLD) is None, "a late timer after release is ignored"


def test_shortcut_before_threshold_never_begins() -> None:
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    assert t.other_input() is None, "Ctrl+C before anything started"
    assert t.tick(10 + HOLD) is None
    assert t.key_up(11) is None


def test_shortcut_during_hold_cancels() -> None:
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    assert t.tick(10 + HOLD) is Action.BEGIN
    assert t.other_input() is Action.CANCEL
    assert t.other_input() is None
    assert t.key_up(12) is None, "nothing to commit after a cancel"


def test_escape_cancels() -> None:
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    t.tick(10 + HOLD)
    assert t.escape() is Action.CANCEL
    assert t.key_up(12) is None


def test_other_input_while_key_is_up_is_ignored() -> None:
    t = make(TriggerMode.HOLD)
    assert t.other_input() is None
    assert t.key_up(1) is None, "release without a press"


def test_next_press_starts_clean() -> None:
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    t.other_input()
    t.key_up(10.5)
    t.key_down(20)
    assert t.tick(20 + HOLD) is Action.BEGIN, "the earlier chord doesn't stick"


def test_autorepeat_key_down_keeps_the_first_press_time() -> None:
    """evdev reports a held key again as a repeat; a repeat must not restart the hold."""
    t = make(TriggerMode.HOLD)
    t.key_down(10)
    assert t.key_down(10 + HOLD - 0.05) is None
    assert t.tick(10 + HOLD) is Action.BEGIN


# --- tap --------------------------------------------------------------------------------------


def test_tap_toggles() -> None:
    t = make(TriggerMode.TAP, TriggerKey.RIGHT_ALT)
    assert t.key_down(10) is None
    assert t.tick(10 + HOLD) is None, "tap mode ignores the hold timer"
    assert t.key_up(10.1) is Action.TOGGLE
    t.key_down(15)
    assert t.key_up(15.1) is Action.TOGGLE


def test_long_press_is_not_a_tap() -> None:
    t = make(TriggerMode.TAP)
    t.key_down(10)
    assert t.key_up(10 + TAP_MAX + 0.05) is None


def test_chord_is_not_a_tap() -> None:
    t = make(TriggerMode.TAP)
    t.key_down(10)
    assert t.other_input() is None
    assert t.key_up(10.1) is None


# --- double tap -------------------------------------------------------------------------------


def test_double_tap_reports_tap_then_double_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    assert t.key_down(10) is None
    assert t.tick(10 + HOLD) is None, "double-tap mode ignores the hold timer"
    assert t.key_up(10.1) is Action.TAP
    t.key_down(10.1 + GAP - 0.05)
    assert t.key_up(10.2 + GAP - 0.05) is Action.DOUBLE_TAP


def test_tap_after_double_tap_is_a_single_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    t.key_up(10.1)
    t.key_down(10.2)
    assert t.key_up(10.3) is Action.DOUBLE_TAP
    t.key_down(10.4)
    assert t.key_up(10.5) is Action.TAP, "a double tap doesn't open another one"
    t.key_down(10.6)
    assert t.key_up(10.7) is Action.DOUBLE_TAP


def test_slow_second_tap_is_a_single_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    assert t.key_up(10.1) is Action.TAP
    t.key_down(10.1 + GAP + 0.05)
    assert t.key_up(10.2 + GAP + 0.05) is Action.TAP


def test_long_press_breaks_a_double_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    t.key_up(10.1)
    t.key_down(10.2)
    assert t.key_up(10.2 + TAP_MAX + 0.05) is None
    t.key_down(10.7)
    assert t.key_up(10.8) is Action.TAP, "the long press doesn't leave a tap behind"


def test_input_between_taps_breaks_a_double_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    t.key_up(10.1)
    assert t.other_input() is None, "typing a letter after the tap"
    t.key_down(10.2)
    assert t.key_up(10.3) is Action.TAP


def test_chord_is_not_a_double_tap() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    t.key_up(10.1)
    t.key_down(10.2)
    assert t.other_input() is None, "Ctrl+C right after a tap"
    assert t.key_up(10.3) is None


def test_escape_cancels_in_double_tap_mode() -> None:
    t = make(TriggerMode.DOUBLE_TAP)
    t.key_down(10)
    t.key_up(10.1)
    assert t.escape() is Action.CANCEL
    t.key_down(10.2)
    assert t.key_up(10.3) is Action.TAP, "Esc also breaks a double tap"


# --- keys -------------------------------------------------------------------------------------


def test_off_never_fires() -> None:
    t = make(TriggerMode.HOLD, TriggerKey.OFF)
    assert t.key_down(10) is None
    assert t.tick(11) is None
    assert t.key_up(12) is None
    assert TriggerKey.OFF.code is None


def test_right_hand_evdev_key_codes() -> None:
    """From linux/input-event-codes.h; left-hand keys have their own codes, so they never match."""
    assert TriggerKey.RIGHT_CTRL.code == 97
    assert TriggerKey.RIGHT_ALT.code == 100
    assert TriggerKey.RIGHT_SHIFT.code == 54
    assert TriggerKey.RIGHT_META.code == 126
    codes = [k.code for k in TriggerKey if k is not TriggerKey.OFF]
    assert len(set(codes)) == len(codes)


def test_default_key_is_right_ctrl() -> None:
    assert TriggerKey.default() is TriggerKey.RIGHT_CTRL
