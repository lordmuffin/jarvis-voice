"""Gotify push notifications. ``notify`` never raises into callers (a pipeline must not break on
a push); ``send`` reports a failure, for pushes whose delivery is the point (reminders)."""

import logging
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class NotifyError(Exception):
    """A push that could not be delivered, in words the owner can read."""


class Notifier(Protocol):
    @property
    def configured(self) -> bool: ...

    async def notify(
        self, title: str, message: str, priority: int, url: str | None = None
    ) -> None: ...

    async def send(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        """Like ``notify``, but raises ``NotifyError`` when it was not delivered."""
        ...


class NullNotifier:
    configured = False

    async def notify(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        return None

    async def send(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        raise NotifyError("Gotify is not configured")


class GotifyNotifier:
    def __init__(
        self,
        base_url: str,
        token_file: Path | None,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 10.0,
        title_prefix: str = "",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._title_prefix = title_prefix  # e.g. "[LAB] ", so a lab push never reads as live
        self._token_file = token_file
        self._client = client
        self._timeout = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self._base) and self._token_file is not None

    async def notify(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        if not self.configured:
            return
        try:
            await self.send(title, message, priority, url)
        except NotifyError as e:  # notifications must never break the pipeline
            log.warning("gotify notification failed: %s", e)

    async def send(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        if not self.configured:
            raise NotifyError("Gotify is not configured")
        try:
            assert self._token_file is not None
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise NotifyError(f"cannot read the Gotify token: {e}") from e
        extras: dict[str, object] = {"client::display": {"contentType": "text/markdown"}}
        if url:
            extras["client::notification"] = {"click": {"url": url}}
        try:
            r = await self._client.post(
                f"{self._base}/message",
                json={
                    "title": self._title_prefix + title,
                    "message": message,
                    "priority": priority,
                    "extras": extras,
                },
                headers={"X-Gotify-Key": token},
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise NotifyError(f"Gotify unreachable: {e!r}") from e
        if r.status_code >= 400:
            raise NotifyError(f"Gotify answered {r.status_code}")
