"""The OIDC relying party refuses every ID token it cannot fully trust.

The issuer is played by ``FakeIssuer``'s keys and tokens over an httpx mock transport, so each
answer can be bent in one way at a time: a wrong issuer, a missing token, another nonce, a key
the issuer never published."""

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis_live.oidc import OIDCClient, OIDCError
from tests.fake_oidc import CLIENT_ID, FakeIssuer

ISSUER = "https://id.example"
NONCE = "n-0"


class Issuer:
    """Discovery, JWKS and a token endpoint whose answers a test can replace."""

    def __init__(self) -> None:
        self.signer = FakeIssuer(ISSUER)
        self.discovery: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
        }
        self.jwks: list[dict[str, Any]] = [self.signer.jwk()]
        self.token: Callable[[], httpx.Response] = lambda: httpx.Response(
            200, json={"id_token": self.signer.id_token("alice", CLIENT_ID, NONCE)}
        )
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        if path == "/jwks":
            return httpx.Response(200, json={"keys": self.jwks})
        if path == "/token":
            return self.token()
        return httpx.Response(404)

    def client(self, secret_file: Path | None = None) -> OIDCClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return OIDCClient(ISSUER, CLIENT_ID, secret_file, http)


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


async def _exchange(client: OIDCClient, nonce: str = NONCE) -> dict[str, Any]:
    return await client.exchange(
        code="c", redirect_uri="https://live.example/cb", verifier="v", nonce=nonce
    )


async def test_a_valid_token_yields_its_claims(issuer: Issuer) -> None:
    claims = await _exchange(issuer.client())
    assert claims["sub"] == "alice" and claims["email"] == "alice@example.com"


async def test_discovery_for_another_issuer_is_refused(issuer: Issuer) -> None:
    issuer.discovery["issuer"] = "https://evil.example"
    with pytest.raises(OIDCError, match="discovery issuer"):
        await _exchange(issuer.client())


async def test_a_failed_code_exchange_reports_the_issuers_status(issuer: Issuer) -> None:
    issuer.token = lambda: httpx.Response(400, text="invalid_grant")
    with pytest.raises(OIDCError, match="400: invalid_grant"):
        await _exchange(issuer.client())


async def test_a_token_response_without_an_id_token_is_refused(issuer: Issuer) -> None:
    issuer.token = lambda: httpx.Response(200, json={"access_token": "a"})
    with pytest.raises(OIDCError, match="no id_token"):
        await _exchange(issuer.client())


async def test_a_token_for_another_sign_in_is_refused(issuer: Issuer) -> None:
    with pytest.raises(OIDCError, match="nonce mismatch"):
        await _exchange(issuer.client(), nonce="someone-elses")


async def test_a_token_that_is_not_a_jwt_is_refused(issuer: Issuer) -> None:
    issuer.token = lambda: httpx.Response(200, json={"id_token": "not.a.jwt"})
    with pytest.raises(OIDCError, match="malformed id_token"):
        await _exchange(issuer.client())


@pytest.mark.parametrize(
    "bent",
    [
        {"aud": "another-app"},
        {"iss": "https://evil.example"},
        {"exp": 1},
    ],
)
async def test_a_token_for_another_audience_issuer_or_time_is_refused(
    issuer: Issuer, bent: dict[str, Any]
) -> None:
    aud = bent.pop("aud", CLIENT_ID)
    issuer.token = lambda: httpx.Response(
        200, json={"id_token": issuer.signer.id_token("alice", aud, NONCE, **bent)}
    )
    with pytest.raises(OIDCError, match="invalid id_token"):
        await _exchange(issuer.client())


async def test_a_token_signed_with_a_key_the_issuer_never_published_is_refused(
    issuer: Issuer,
) -> None:
    forger = FakeIssuer(ISSUER, kid="forged")
    issuer.token = lambda: httpx.Response(
        200, json={"id_token": forger.id_token("alice", CLIENT_ID, NONCE)}
    )
    with pytest.raises(OIDCError, match="no signing key 'forged'"):
        await _exchange(issuer.client())
    jwks_fetches = [r for r in issuer.requests if r.url.path == "/jwks"]
    assert len(jwks_fetches) == 2  # looked again in case the keys rotated, then gave up


async def test_a_forged_token_reusing_a_published_key_id_is_refused(issuer: Issuer) -> None:
    forger = FakeIssuer(ISSUER, kid=issuer.signer.kid)
    issuer.token = lambda: httpx.Response(
        200, json={"id_token": forger.id_token("alice", CLIENT_ID, NONCE)}
    )
    with pytest.raises(OIDCError, match="invalid id_token"):
        await _exchange(issuer.client())


async def test_keys_rotated_since_the_last_sign_in_are_fetched_again(issuer: Issuer) -> None:
    client = issuer.client()
    await _exchange(client)
    rotated = FakeIssuer(ISSUER, kid="next-key")
    issuer.jwks = [rotated.jwk()]
    issuer.token = lambda: httpx.Response(
        200, json={"id_token": rotated.id_token("alice", CLIENT_ID, NONCE)}
    )
    assert (await _exchange(client))["sub"] == "alice"


async def test_a_public_client_sends_an_empty_secret_and_a_rotated_secret_is_reread(
    issuer: Issuer, tmp_path: Path
) -> None:
    def basic(request: httpx.Request) -> str:
        user_pass = base64.b64decode(request.headers["authorization"].split()[1]).decode()
        return user_pass.split(":", 1)[1]

    def token_requests() -> list[httpx.Request]:
        return [r for r in issuer.requests if r.url.path == "/token"]

    await _exchange(issuer.client())
    assert basic(token_requests()[-1]) == ""

    secret = tmp_path / "secret"
    secret.write_text("first\n")
    client = issuer.client(secret)
    await _exchange(client)
    secret.write_text("second\n")
    await _exchange(client)
    sent = [basic(r) for r in token_requests()][-2:]
    assert sent == ["first", "second"]
