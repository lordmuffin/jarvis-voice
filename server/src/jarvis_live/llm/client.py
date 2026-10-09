"""LiteLLM (OpenAI-compatible) chat client with schema-validated JSON output."""

import logging
import re
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMError(Exception):
    """The LLM could not produce a valid response (after the one retry for invalid JSON)."""


class LLM(Protocol):
    async def complete_json(
        self, *, model: str, system: str, user: str, schema: type[T], timeout_s: float | None = None
    ) -> T:
        """One structured completion. Raises ``LLMError`` on failure."""
        ...


def _extract_json(content: str) -> str:
    content = _THINK.sub("", content).strip()
    if m := _FENCE.match(content):
        return m.group(1)
    return content


class LiteLLMClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        api_key_file: Path | None = None,
        timeout_s: float = 60.0,
        temperature: float = 0.2,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._key_file = api_key_file
        self._timeout = timeout_s
        self._temperature = temperature

    def _headers(self) -> dict[str, str]:
        if self._key_file is None:
            return {}
        try:
            key = self._key_file.read_text().strip()
        except OSError as e:
            log.warning("cannot read LITELLM_API_KEY_FILE: %s", e)
            return {}
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: type[T], timeout_s: float | None = None
    ) -> T:
        body: dict[str, Any] = {
            "model": model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            },
        }
        last: Exception | None = None
        for attempt in (1, 2):
            content = await self._chat(body, timeout_s or self._timeout)
            try:
                return schema.model_validate_json(_extract_json(content))
            except ValidationError as e:
                last = e
                log.warning(
                    "invalid %s JSON from %s (attempt %d): %s",
                    schema.__name__,
                    model,
                    attempt,
                    str(e).splitlines()[0],
                )
        raise LLMError(f"invalid {schema.__name__} JSON after retry") from last

    async def _chat(self, body: dict[str, Any], timeout_s: float) -> str:
        try:
            r = await self._client.post(
                f"{self._base}/chat/completions",
                json=body,
                headers=self._headers(),
                timeout=timeout_s,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM request failed: {e!r}") from e
        if not isinstance(content, str):
            raise LLMError("LLM response has no text content")
        return content


__all__ = ["LLM", "LLMError", "LiteLLMClient"]
