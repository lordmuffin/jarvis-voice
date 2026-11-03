"""Turns presses of a lone right-hand modifier into dictation actions. Pure: no evdev, no Qt.

A port of macos/JarvisVoiceMac/Sources/Dictation/DictationTrigger.swift, with the same rules and
constants. The key only counts when nothing else happens while it is down: any other key,
modifier or mouse button makes the press a chord (Ctrl+C, Ctrl+click), which never starts
dictation and cancels a hold that already began. Whether dictation is running is the
controller's business, so a tap reports ``TOGGLE`` (or, in double-tap mode, ``TAP`` and
``DOUBLE_TAP``) and the controller decides between starting and stopping.

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
