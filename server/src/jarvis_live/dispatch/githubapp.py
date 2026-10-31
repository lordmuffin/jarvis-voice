"""GitHub App installation tokens for dispatch runs.

Each run gets its own token, minted for exactly one repository with Contents and Pull requests
write (and Metadata read). Never Workflows: GitHub then refuses any push that touches
``.github/workflows``, so a run cannot change CI that holds secrets. Tokens last about an hour;
the dispatcher refreshes a long run's token in place. The App key is re-read on every mint, so
a rotated key is picked up."""

import time
from pathlib import Path

import httpx
import jwt

TIMEOUT_S = 30.0
PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}


class TokenError(Exception):
    pass


class AppTokens:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api: str,
        app_id: str,
        installation_id: str,
        key_file: Path,
    ) -> None:
        self._client = client
        self._api = api.rstrip("/")
        self._app_id = app_id
        self._installation = installation_id
        self._key_file = key_file

    def _app_jwt(self) -> str:
        now = int(time.time())
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return jwt.encode(claims, self._key_file.read_text(), algorithm="RS256")

    async def mint(self, repo: str, permissions: dict[str, str] | None = None) -> str:
        """A token for ``repo`` alone; ``permissions`` narrows it below a dispatch run's."""
        name = repo.split("/", 1)[1]
        try:
            r = await self._client.post(
                f"{self._api}/app/installations/{self._installation}/access_tokens",
                json={"repositories": [name], "permissions": permissions or PERMISSIONS},
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=TIMEOUT_S,
            )
        except httpx.HTTPError as e:
            raise TokenError(f"GitHub unreachable: {e!r}") from e
        if r.status_code >= 400:
            try:
                message = str(r.json().get("message") or r.text)
            except ValueError:
                message = r.text
            raise TokenError(f"GitHub {r.status_code}: {message[:300]}")
        return str(r.json()["token"])
