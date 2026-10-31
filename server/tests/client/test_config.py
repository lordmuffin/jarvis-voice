import os
import stat
from pathlib import Path

import pytest

from jarvis_live.client import config as cfg


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        "HOME": str(tmp_path),
        **extra,
    }


def test_save_then_load_round_trips_and_the_file_is_private(tmp_path: Path) -> None:
    env = _env(tmp_path)
    cfg.save_config(
        cfg.ClientConfig(server="https://live.lab.apj.dev", token="tok", mode="stream"), env
    )
    path = cfg.config_dir(env) / "client.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = cfg.load_config(env)
    assert (loaded.server, loaded.token, loaded.mode) == (
        "https://live.lab.apj.dev",
        "tok",
        "stream",
    )


def test_env_overrides_the_file_and_token_can_come_from_a_file(tmp_path: Path) -> None:
    env = _env(tmp_path)
    cfg.save_config(cfg.ClientConfig(server="http://a", token="file-token", mode="stream"), env)
    secret = tmp_path / "secret"
    secret.write_text("from-secret-file\n")
    loaded = cfg.load_config(
        {**env, "JARVIS_LIVE_SERVER": "http://b", "JARVIS_LIVE_TOKEN_FILE": str(secret)}
    )
    assert loaded.server == "http://b" and loaded.token == "from-secret-file"


def test_missing_config_loads_as_empty(tmp_path: Path) -> None:
    loaded = cfg.load_config(_env(tmp_path))
    assert (loaded.server, loaded.token, loaded.mode) == (None, None, None)


def test_mode_must_be_explicit(tmp_path: Path) -> None:
    with pytest.raises(cfg.ConfigError, match="mode"):
        cfg.ClientConfig(server="http://a", token="t").validate()
    with pytest.raises(cfg.ConfigError, match="mode"):
        cfg.ClientConfig(mode="auto").validate()


def test_stream_mode_needs_server_and_token_but_local_needs_neither() -> None:
    with pytest.raises(cfg.ConfigError, match="server"):
        cfg.ClientConfig(mode="stream", token="t").validate()
    with pytest.raises(cfg.ConfigError, match="token"):
        cfg.ClientConfig(mode="stream", server="http://a").validate()
    cfg.ClientConfig(mode="local").validate()


def test_paths_follow_xdg_with_a_tmp_fallback_for_the_runtime_dir(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert cfg.state_dir(env) == tmp_path / "state" / "jarvis-live"
    assert cfg.runtime_dir(env) == tmp_path / "run" / "jarvis-live"
    bare = {"HOME": str(tmp_path)}
    assert cfg.runtime_dir(bare) == Path(f"/tmp/jarvis-live-{os.getuid()}")
    assert cfg.state_dir(bare) == tmp_path / ".local" / "state" / "jarvis-live"
