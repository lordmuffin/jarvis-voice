"""Client configuration and the XDG directories it lives in.

Precedence is CLI flag, then environment, then ``client.toml``. The file is written mode 0600
because it holds the device token."""

import os
import platform
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

MODES = ("stream", "local")
ENV_PREFIX = "JARVIS_LIVE_"


class ConfigError(Exception):
    pass


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def config_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_CONFIG_HOME") or str(_home(env) / ".config")
    return Path(base) / "jarvis-live"


def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    base = env.get("XDG_STATE_HOME") or str(_home(env) / ".local" / "state")
    return Path(base) / "jarvis-live"


def runtime_dir(env: Mapping[str, str] = os.environ) -> Path:
    """Control socket and pid file. Plain SSH sessions on some hosts have no XDG_RUNTIME_DIR."""
    base = env.get("XDG_RUNTIME_DIR")
    return Path(base) / "jarvis-live" if base else Path(f"/tmp/jarvis-live-{os.getuid()}")  # noqa: S108


@dataclass
class ClientConfig:
    server: str | None = None
    token: str | None = None
    mode: str | None = None  # "stream" | "local"; deliberately has no default
    device: str = field(default_factory=platform.node)
    source: str | None = None
    max_session_hours: float = 4.0
    max_outbox_mb: int = 2048

    def validate(self) -> None:
        """Local-only is never a silent fallback and streaming is never a silent default."""
        if self.mode not in MODES:
            raise ConfigError(
                "mode is not set: run `jarvis-live capture configure --mode stream|local` "
                "(or pass --local-only)"
            )
        if self.mode == "stream":
            if not self.server:
                raise ConfigError("no server configured: use `capture configure --server URL`")
            if not self.token:
                raise ConfigError("no device token configured: use `capture configure --token T`")


def load_config(env: Mapping[str, str] = os.environ, *, include_env: bool = True) -> ClientConfig:
    cfg = ClientConfig()
    path = config_dir(env) / "client.toml"
    if path.exists():
        data = tomllib.loads(path.read_text())
        for f in fields(ClientConfig):
            if f.name in data:
                setattr(cfg, f.name, data[f.name])
    if not include_env:
        return cfg
    if (v := env.get(ENV_PREFIX + "SERVER")) is not None:
        cfg.server = v
    if (v := env.get(ENV_PREFIX + "TOKEN")) is not None:
        cfg.token = v
    if (v := env.get(ENV_PREFIX + "TOKEN_FILE")) is not None:
        cfg.token = Path(v).read_text().strip()
    if (v := env.get(ENV_PREFIX + "MODE")) is not None:
        cfg.mode = v
    return cfg


def save_config(cfg: ClientConfig, env: Mapping[str, str] = os.environ) -> Path:
    d = config_dir(env)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "client.toml"
    lines = []
    for f in fields(ClientConfig):
        v = getattr(cfg, f.name)
        if v is None:
            continue
        lines.append(f"{f.name} = {_toml(v)}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def _toml(v: object) -> str:
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return str(v)
