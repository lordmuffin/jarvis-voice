"""Pi Durable (dpi-backend) client: forwards a homelab question to the persistent `jarvis`
session (docs/pi-durable.md in np-home-homelab) and returns its answer, or starts a new session
for a task. Never raises past `ask` into routing code that doesn't expect it — callers get
`DpiError` and speak `FALLBACK`.
"""

import logging
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

FALLBACK = "I can't reach the homelab assistant right now."


class DpiError(Exception):
    """The homelab backend is not configured, unreachable, or did not answer in time."""


class DpiClient:
    def __init__(
        self,
        base_url: str,
        token_file: Path | None,
        client: httpx.AsyncClient,
        *,
        session: str = "jarvis",
        agent: str = "main",
        wait_ms: int = 60_000,
        timeout_s: float = 65.0,
        model: str = "qwen3.8-flash-next",
        work_cwd: str = "/var/lib/dpi/work",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token_file = token_file
        self._client = client
        self._session = session
        self._agent = agent
        self._wait_ms = wait_ms
        self._timeout = timeout_s
        self._model = model
        self._work_cwd = work_cwd

    @property
    def configured(self) -> bool:
        return bool(self._base) and self._token_file is not None

    def _headers(self) -> dict[str, str]:
        assert self._token_file is not None
        # JARVIS_LIVE_DPI_TOKEN_FILE is a fixed path (deployment.yaml), set whenever
        # dpi_base_url is, independent of whether the 1Password field behind it has landed
        # yet (same as JARVIS_LIVE_OIDC_CLIENT_SECRET_FILE) -- so a missing file is the
        # ordinary "not configured yet" case, not a bug.
        try:
            token = self._token_file.read_text().strip()
        except OSError as e:
            raise DpiError(f"cannot read the DPI token file: {e!r}") from e
        return {"Authorization": f"Bearer {token}"}

    async def _post(
        self, path: str, body: dict[str, object], headers: dict[str, str] | None = None
    ) -> Any:
        """POST and return the JSON. One retry, with the identical body and headers, on a
        transport-level failure only; never on an HTTP error response, which is not safe to
        assume didn't already land. Only for calls pi-durable admits once per key."""
        if not self.configured:
            raise DpiError("homelab backend is not configured (DPI_BASE_URL/DPI_TOKEN unset)")
        url = f"{self._base}{path}"
        sent = {**self._headers(), **(headers or {})}
        last: Exception | None = None
        for attempt in (1, 2):
            try:
                r = await self._client.post(url, json=body, headers=sent, timeout=self._timeout)
                r.raise_for_status()
                return r.json()
            except httpx.TransportError as e:
                last = e
                log.warning("homelab request attempt %d failed: %r", attempt, e)
                continue
            except (httpx.HTTPStatusError, ValueError) as e:
                raise DpiError(f"homelab request failed: {e!r}") from e
        raise DpiError(f"homelab backend unreachable: {last!r}")

    async def ask(self, question: str, request_id: str) -> str:
        """Send `question` to the `jarvis` session's `main` agent with the given idempotent
        `request_id` and return its answer text. A retry reuses the *same* `request_id`
        (pi-durable admits each `requestId` exactly once)."""
        data = await self._post(
            f"/v1/sessions/{self._session}/agents/{self._agent}/messages",
            {"content": question, "requestId": request_id, "waitMs": self._wait_ms},
        )
        status = data.get("status")
        if status == "done":
            text = (data.get("answer") or {}).get("text")
            if isinstance(text, str) and text:
                return text
            raise DpiError("homelab answer had no text")
        if status == "unanswered":
            raise DpiError(f"homelab session did not answer: {data.get('reason')}")
        raise DpiError(f"homelab request did not complete within waitMs: status={status!r}")

    async def start_session(self, name: str, prompt: str, *, key: str) -> str:
        """Create a new session and brief its `main` agent, without waiting for the agent;
        returns the session id. `key` is the idempotency key of both calls, so repeating them
        (within pi-durable's 24 h) neither makes a second session nor briefs it twice."""
        created = await self._post(
            "/v1/sessions",
            {
                "name": name,
                "cwd": self._work_cwd,
                "model": {"provider": "openai-compatible", "modelId": self._model},
            },
            {"Idempotency-Key": key[:128]},
        )
        session_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise DpiError("homelab backend did not return a session id")
        await self._post(
            f"/v1/sessions/{session_id}/agents/{self._agent}/messages",
            {"content": prompt, "requestId": key, "whenBusy": "followUp"},
        )
        return session_id


__all__ = ["FALLBACK", "DpiClient", "DpiError"]
