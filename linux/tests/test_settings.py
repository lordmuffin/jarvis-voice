"""The settings the desktop app reads: dictation's key and mode, and hold to ask."""

from pathlib import Path

import pytest

from jarvis_desktop.settings import Settings, SettingsError, load, save, settings_path
from jarvis_desktop.trigger import TriggerKey, TriggerMode


def env(tmp_path: Path) -> dict[str, str]:
    return {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg")}


def test_defaults_without_a_file(tmp_path: Path) -> None:
    s = load(env(tmp_path))
    assert s == Settings()
    assert s.dictation_key is TriggerKey.RIGHT_CTRL
    assert s.dictation_mode is TriggerMode.HOLD
    assert s.ask_enabled is True, "hold to ask is on by default"
    assert s.effective_ask_key() is TriggerKey.RIGHT_ALT


def test_path_is_under_xdg_config(tmp_path: Path) -> None:
    assert settings_path(env(tmp_path)) == tmp_path / "cfg" / "jarvis-voice" / "settings.toml"


def test_round_trip(tmp_path: Path) -> None:
    s = Settings(
        dictation_key=TriggerKey.RIGHT_SHIFT,
        dictation_mode=TriggerMode.DOUBLE_TAP,
        ask_enabled=False,
        ask_key=TriggerKey.RIGHT_META,
    )
    path = save(s, env(tmp_path))
    assert path == settings_path(env(tmp_path))
    assert load(env(tmp_path)) == s


def test_ask_off_switch_turns_the_key_off() -> None:
    assert Settings(ask_enabled=False).effective_ask_key() is TriggerKey.OFF


def test_default_ask_key_moves_when_dictation_has_right_alt() -> None:
    s = Settings(dictation_key=TriggerKey.RIGHT_ALT)
    assert s.effective_ask_key() is TriggerKey.RIGHT_META


def test_ask_key_equal_to_dictations_is_off() -> None:
    s = Settings(dictation_key=TriggerKey.RIGHT_CTRL, ask_key=TriggerKey.RIGHT_CTRL)
    assert s.effective_ask_key() is TriggerKey.OFF


def test_keys_builds_both_triggers() -> None:
    k = Settings(ask_key=TriggerKey.RIGHT_SHIFT).keys()
    assert k.dictation.key is TriggerKey.RIGHT_CTRL
    assert k.dictation.mode is TriggerMode.HOLD
    assert k.ask.key is TriggerKey.RIGHT_SHIFT


def test_unknown_value_is_an_error_naming_it(tmp_path: Path) -> None:
    path = settings_path(env(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text('ask_key = "left_alt"\n')
    with pytest.raises(SettingsError, match="ask_key"):
        load(env(tmp_path))


def test_ask_enabled_must_be_a_bool(tmp_path: Path) -> None:
    path = settings_path(env(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text('ask_enabled = "yes"\n')
    with pytest.raises(SettingsError, match="ask_enabled"):
        load(env(tmp_path))
