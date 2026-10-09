"""Gotify push notifications. Never raises into callers."""

import logging
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def notify(
        self, title: str, message: str, priority: int, url: str | None = None
    ) -> None: ...


class NullNotifier:
    async def notify(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        return None


class GotifyNotifier:
    def __init__(
        self,
        base_url: str,
        token_file: Path | None,
        client: httpx.AsyncClient,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
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
            assert self._token_file is not None
            token = self._token_file.read_text().strip()
            extras: dict[str, object] = {
                "client::display": {"contentType": "text/markdown"},
            }
            if url:
                extras["client::notification"] = {"click": {"url": url}}
            r = await self._client.post(
                f"{self._base}/message",
                json={"title": title, "message": message, "priority": priority, "extras": extras},
                headers={"X-Gotify-Key": token},
                timeout=self._timeout,
            )
            r.raise_for_status()
        except Exception as e:  # notifications must never break the pipeline
            log.warning("gotify notification failed: %r", e)
