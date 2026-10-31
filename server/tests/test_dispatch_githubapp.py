"""Per-run GitHub tokens: one repository, the least permissions, minted from the App key."""

import json
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from jarvis_live.dispatch.githubapp import AppTokens, TokenError


def key(tmp_path: Path) -> tuple[Path, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "app.pem"
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return path, private.public_key()


async def test_mints_a_token_for_one_repo_with_contents_and_pull_requests_only(
    tmp_path: Path,
) -> None:
    path, public = key(tmp_path)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"token": "ghs_run", "expires_at": "2026-10-12T15:00:00Z"})

    tokens = AppTokens(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api="https://api.github.test",
        app_id="12345",
        installation_id="678",
        key_file=path,
    )
    assert await tokens.mint("lordmuffin/jarvis-voice") == "ghs_run"

    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == "https://api.github.test/app/installations/678/access_tokens"
    assert json.loads(req.content) == {
        "repositories": ["jarvis-voice"],
        "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
    }
    claims = jwt.decode(
        req.headers["Authorization"].removeprefix("Bearer "), public, algorithms=["RS256"]
    )
    assert claims["iss"] == "12345"
    assert claims["iat"] <= time.time() <= claims["exp"] <= time.time() + 600


async def test_a_refusal_is_an_error(tmp_path: Path) -> None:
    path, _ = key(tmp_path)
    tokens = AppTokens(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(422, json={"message": "not installed on repo"})
            )
        ),
        api="https://api.github.test",
        app_id="1",
        installation_id="2",
        key_file=path,
    )
    with pytest.raises(TokenError, match="422.*not installed"):
        await tokens.mint("lordmuffin/other")


async def test_a_comment_token_can_ask_for_fewer_permissions(tmp_path: Path) -> None:
    path, _ = key(tmp_path)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(201, json={"token": "ghs_c"})

    tokens = AppTokens(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api="https://api.github.test",
        app_id="1",
        installation_id="2",
        key_file=path,
    )
    await tokens.mint("lordmuffin/jarvis-voice", permissions={"pull_requests": "write"})
    assert json.loads(seen[0].content)["permissions"] == {"pull_requests": "write"}
