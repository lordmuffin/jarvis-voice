"""The desktop app's settings: dictation's key and mode, and hold to ask.

Kept in ``$XDG_CONFIG_HOME/jarvis-voice/settings.toml``, apart from the capture client's
``client.toml``: nothing here is secret, and the client never reads it."""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from jarvis_desktop.trigger import AskTrigger, DictationTrigger, Keys, TriggerKey, TriggerMode


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    dictation_key: TriggerKey = TriggerKey.default()
    dictation_mode: TriggerMode = TriggerMode.HOLD
    #: Hold to ask, on by default. It does anything only in a streaming session with voice on.
    ask_enabled: bool = True
    #: None: Right Alt, or Right Meta when dictation has Right Alt (``AskTrigger.default_key``).
    ask_key: TriggerKey | None = None

    def effective_ask_key(self) -> TriggerKey:
        """The key that asks: off when switched off, or when it is dictation's key too."""
        if not self.ask_enabled:
            return TriggerKey.OFF
        key = self.ask_key or AskTrigger.default_key(self.dictation_key)
        return TriggerKey.OFF if key is self.dictation_key else key

    def keys(self) -> Keys:
        return Keys(
            DictationTrigger(self.dictation_key, self.dictation_mode),
            AskTrigger(self.effective_ask_key()),
        )


def settings_path(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or Path.home()) / ".config")
    return Path(base) / "jarvis-voice" / "settings.toml"


def load(env: Mapping[str, str] = os.environ) -> Settings:
    path = settings_path(env)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return Settings()
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SettingsError(f"{path}: {e}") from e
    values: dict[str, Any] = {}
    enums: dict[str, type[Enum]] = {
        "dictation_key": TriggerKey,
        "dictation_mode": TriggerMode,
        "ask_key": TriggerKey,
    }
    for name, kind in enums.items():
        if name in raw:
            try:
                values[name] = kind(raw[name])
            except ValueError:
                choices = ", ".join(str(m.value) for m in kind)
                raise SettingsError(
                    f"{path}: {name} = {raw[name]!r} is not one of {choices}"
                ) from None
    if "ask_enabled" in raw:
        if not isinstance(raw["ask_enabled"], bool):
            raise SettingsError(f"{path}: ask_enabled must be true or false")
        values["ask_enabled"] = raw["ask_enabled"]
    return Settings(**values)


def save(settings: Settings, env: Mapping[str, str] = os.environ) -> Path:
    path = settings_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'dictation_key = "{settings.dictation_key.value}"',
        f'dictation_mode = "{settings.dictation_mode.value}"',
        f"ask_enabled = {'true' if settings.ask_enabled else 'false'}",
    ]
    if settings.ask_key is not None:
        lines.append(f'ask_key = "{settings.ask_key.value}"')
    path.write_text("\n".join(lines) + "\n")
    return path
