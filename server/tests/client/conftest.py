import socket
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fails any use of the network; unix sockets (the daemon's control socket) are allowed.

    Yields the list of attempted outbound addresses, which must stay empty."""
    attempts: list[str] = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guard(self: socket.socket, address: object) -> None:
        if self.family != socket.AF_UNIX:
            attempts.append(f"{self.family.name} {address}")
            raise AssertionError(f"outbound connection attempted: {address}")

    def connect(self: socket.socket, address: object) -> None:
        guard(self, address)
        real_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object) -> int:
        guard(self, address)
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> list[object]:
        attempts.append(f"dns {host}")
        raise AssertionError(f"DNS lookup attempted: {host}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
