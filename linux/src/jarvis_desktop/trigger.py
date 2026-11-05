"""Turns presses of a lone right-hand modifier into dictation actions. Pure: no evdev, no Qt.

A port of macos/JarvisVoiceMac/Sources/Dictation/DictationTrigger.swift, with the same rules and
constants. The key only counts when nothing else happens while it is down: any other key,
modifier or mouse button makes the press a chord (Ctrl+C, Ctrl+click), which never starts
dictation and cancels a hold that already began. Whether dictation is running is the
controller's business, so a tap reports ``TOGGLE`` (or, in double-tap mode, ``TAP`` and
``DOUBLE_TAP``) and the controller decides between starting and stopping.

Hold to ask (docs/plans/voice-push-to-talk.md) has its own key, hold only: ``AskTrigger``.
``Keys`` routes evdev key codes to both triggers and feeds each trigger key's press to the other
one as other input, so the two keys can never both be held into an action.

Times are seconds on one monotonic clock (evdev event timestamps)."""

from enum import Enum

# linux/input-event-codes.h. The left-hand keys have their own codes, so they never match.
_CODES = {"right_ctrl": 97, "right_alt": 100, "right_shift": 54, "right_meta": 126}


class Action(Enum):
    BEGIN = "begin"
    COMMIT = "commit"
    CANCEL = "cancel"
    TOGGLE = "toggle"
    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    ASK_BEGIN = "ask_begin"
    ASK_END = "ask_end"


class TriggerMode(Enum):
    HOLD = "hold"
    TAP = "tap"
    DOUBLE_TAP = "double_tap"


class TriggerKey(Enum):
    OFF = "off"
    RIGHT_CTRL = "right_ctrl"
    RIGHT_ALT = "right_alt"
    RIGHT_SHIFT = "right_shift"
    RIGHT_META = "right_meta"

    @property
    def code(self) -> int | None:
        """The evdev key code (``KEY_RIGHTCTRL`` etc.)."""
        return _CODES.get(self.value)

    @classmethod
    def default(cls) -> "TriggerKey":
        # Right Alt is AltGr on many layouts and Right Meta is missing on many keyboards.
        return cls.RIGHT_CTRL


class DictationTrigger:
    #: Hold mode starts listening only after this long, so Ctrl shortcuts never open the mic.
    HOLD_THRESHOLD = 0.2
    #: Tap mode: a longer press is not a tap.
    TAP_MAX_DURATION = 0.35
    #: Double-tap mode: the longest gap between releasing the first tap and pressing the second.
    DOUBLE_TAP_GAP = 0.4
    #: Timer slack when ``tick`` fires right at the threshold.
    _TOLERANCE = 0.01

    def __init__(self, key: TriggerKey, mode: TriggerMode) -> None:
        self.key = key
        self.mode = mode
        self.pressed_at: float | None = None
        self._chorded = False
        self._began = False
        # Double-tap mode: when the last lone tap was released, while it can still open one.
        self._last_tap_at: float | None = None

    @property
    def is_pressed(self) -> bool:
        return self.pressed_at is not None

    def key_down(self, time: float) -> Action | None:
        # An autorepeat of a held key arrives as another key down and is ignored here.
        if self.key is TriggerKey.OFF or self.pressed_at is not None:
            return None
        self.pressed_at = time
        self._chorded = False
        self._began = False
        return None

    def tick(self, time: float) -> Action | None:
        """Called once the hold threshold has passed since ``key_down``."""
        if (
            self.mode is not TriggerMode.HOLD
            or self.pressed_at is None
            or self._chorded
            or self._began
            or time - self.pressed_at < self.HOLD_THRESHOLD - self._TOLERANCE
        ):
            return None
        self._began = True
        return Action.BEGIN

    def key_up(self, time: float) -> Action | None:
        pressed_at = self.pressed_at
        if pressed_at is None:
            return None
        self.pressed_at = None
        lone_tap = not self._chorded and time - pressed_at <= self.TAP_MAX_DURATION
        if self.mode is TriggerMode.HOLD:
            began, self._began = self._began, False
            return Action.COMMIT if began else None
        if self.mode is TriggerMode.TAP:
            return Action.TOGGLE if lone_tap else None
        if not lone_tap:
            self._last_tap_at = None
            return None
        if self._last_tap_at is not None and pressed_at - self._last_tap_at <= self.DOUBLE_TAP_GAP:
            self._last_tap_at = None
            return Action.DOUBLE_TAP
        self._last_tap_at = time
        return Action.TAP

    def other_input(self) -> Action | None:
        """Any other key, modifier or mouse button. Between two taps it breaks the double tap."""
        self._last_tap_at = None
        if self.pressed_at is None:
            return None
        self._chorded = True
        if not self._began:
            return None
        self._began = False
        return Action.CANCEL

    def escape(self) -> Action | None:
        """Esc cancels dictation in every mode."""
        self.other_input()
        return Action.CANCEL


class AskTrigger:
    """Hold to ask: a lone hold past ``HOLD_THRESHOLD`` begins an ask, its release ends it.

    A chord before the threshold never begins one (AltGr+e, Alt+Tab). Once begun, other input
    and Esc leave it alone: the wire has no cancel, so a begun ask always ends with its release."""

    HOLD_THRESHOLD = DictationTrigger.HOLD_THRESHOLD
    _TOLERANCE = DictationTrigger._TOLERANCE

    def __init__(self, key: TriggerKey) -> None:
        self.key = key
        self.pressed_at: float | None = None
        self._chorded = False
        self._began = False

    @staticmethod
    def default_key(dictation: TriggerKey) -> TriggerKey:
        """Right Alt, or Right Meta when Right Alt is dictation's key."""
        return TriggerKey.RIGHT_META if dictation is TriggerKey.RIGHT_ALT else TriggerKey.RIGHT_ALT

    @property
    def is_pressed(self) -> bool:
        return self.pressed_at is not None

    def key_down(self, time: float) -> Action | None:
        if self.key is TriggerKey.OFF or self.pressed_at is not None:
            return None  # off, or an autorepeat of the held key
        self.pressed_at = time
        self._chorded = False
        self._began = False
        return None

    def tick(self, time: float) -> Action | None:
        if (
            self.pressed_at is None
            or self._chorded
            or self._began
            or time - self.pressed_at < self.HOLD_THRESHOLD - self._TOLERANCE
        ):
            return None
        self._began = True
        return Action.ASK_BEGIN

    def key_up(self, time: float) -> Action | None:
        if self.pressed_at is None:
            return None
        self.pressed_at = None
        began, self._began = self._began, False
        return Action.ASK_END if began else None

    def other_input(self) -> Action | None:
        if self.pressed_at is not None and not self._began:
            self._chorded = True
        return None

    def escape(self) -> Action | None:
        return self.other_input()


class Keys:
    """Both triggers on one keyboard. Feed it every key press and release (evdev codes), every
    mouse button as ``other_input``, Esc as ``escape``, and ``tick`` once the hold threshold has
    passed since a trigger key went down. At most one action comes back per call."""

    def __init__(self, dictation: DictationTrigger, ask: AskTrigger) -> None:
        if ask.key is dictation.key:
            ask.key = TriggerKey.OFF  # one key can't do both; dictation keeps it
        self.dictation = dictation
        self.ask = ask

    def key_down(self, code: int, time: float) -> Action | None:
        if code == self.dictation.key.code:
            self.ask.other_input()
            action = self.dictation.key_down(time)
            if self.ask.is_pressed:
                self.dictation.other_input()  # a chord with the ask key: never dictates
            return action
        if code == self.ask.key.code:
            action = self.dictation.other_input()  # cancels a dictation already under way
            self.ask.key_down(time)
            if self.dictation.is_pressed:
                self.ask.other_input()
            return action
        return self.other_input()

    def key_up(self, code: int, time: float) -> Action | None:
        if code == self.dictation.key.code:
            return self.dictation.key_up(time)
        if code == self.ask.key.code:
            return self.ask.key_up(time)
        return None

    def tick(self, time: float) -> Action | None:
        return self.ask.tick(time) or self.dictation.tick(time)

    def other_input(self) -> Action | None:
        self.ask.other_input()
        return self.dictation.other_input()

    def escape(self) -> Action | None:
        self.ask.escape()
        return self.dictation.escape()
