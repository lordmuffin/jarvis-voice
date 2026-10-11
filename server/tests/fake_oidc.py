"""A fake Pocket-ID: discovery, an authorize page that signs in whichever test user you pick,
token and JWKS endpoints, RS256 ID tokens. Test double only.

``/authorize`` without ``user=`` lists the users as links (the Playwright test clicks one);
with ``user=<sub>`` it redirects straight back with a code.
"""

import base64
import hashlib
import html
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

CLIENT_ID = "jarvis-live-test"
CLIENT_SECRET = "fake-client-secret"  # noqa: S105

DEFAULT_USERS: dict[str, dict[str, Any]] = {
    "alice": {
        "email": "alice@example.com",
        "name": "Alice Example",
        "preferred_username": "alice",
        "groups": ["jarvis"],
    },
    "bob": {
        "email": "bob@example.com",
        "name": "Bob Example",
        "preferred_username": "bob",
        "groups": ["jarvis"],
    },
    "root": {
        "email": "root@example.com",
        "name": "Root Admin",
        "preferred_username": "root",
        "groups": ["jarvis", "jarvis-admins"],
    },
    "mallory": {
        "email": "mallory@example.com",
        "name": "Mallory",
        "preferred_username": "mallory",
        "groups": [],
    },
}


@dataclass
class _Grant:
    sub: str
    client_id: str
    redirect_uri: str
    nonce: str
    challenge: str


@dataclass
class FakeIssuer:
    issuer: str
    users: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_USERS))
    kid: str = "test-key"

    def __post_init__(self) -> None:
        self.issuer = self.issuer.rstrip("/")
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._grants: dict[str, _Grant] = {}
        self.token_requests = 0

    def jwk(self) -> dict[str, Any]:
        jwk: dict[str, Any] = jwt.algorithms.RSAAlgorithm.to_jwk(
            self._key.public_key(), as_dict=True
        )
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def id_token(self, sub: str, aud: str, nonce: str, **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": sub,
            "aud": aud,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "email_verified": True,
            **self.users[sub],
            **overrides,
        }
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/openid-configuration")
        async def discovery() -> dict[str, Any]:
            return {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/api/oidc/token",
                "jwks_uri": f"{self.issuer}/.well-known/jwks.json",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }

        @app.get("/.well-known/jwks.json")
        async def jwks() -> dict[str, Any]:
            return {"keys": [self.jwk()]}

        @app.get("/authorize", response_model=None)
        async def authorize(
            request: Request,
            client_id: str,
            redirect_uri: str,
            state: str,
            nonce: str,
            code_challenge: str,
            code_challenge_method: str,
            user: str = "",
        ) -> HTMLResponse | RedirectResponse:
            if client_id != CLIENT_ID or code_challenge_method != "S256":
                raise HTTPException(400, "bad client or PKCE method")
            if not user:
                links = "".join(
                    f'<li><a href="{html.escape(str(request.url))}&amp;user={sub}">{sub}</a></li>'
                    for sub in self.users
                )
                return HTMLResponse(f"<title>Fake Pocket-ID</title><ul>{links}</ul>")
            if user == "deny":
                return RedirectResponse(
                    f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}"
                )
            code = secrets.token_urlsafe(16)
            self._grants[code] = _Grant(user, client_id, redirect_uri, nonce, code_challenge)
            return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")

        @app.post("/api/oidc/token")
        async def token(request: Request) -> dict[str, Any]:
            form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
            grant_type, code = form.get("grant_type"), form.get("code", "")
            redirect_uri, code_verifier = form.get("redirect_uri"), form.get("code_verifier", "")
            self.token_requests += 1
            auth = request.headers.get("authorization", "")
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if auth != f"Basic {expected}":
                raise HTTPException(401, "invalid_client")
            grant = self._grants.pop(code, None)
            if grant is None or grant_type != "authorization_code":
                raise HTTPException(400, "invalid_grant")
            digest = hashlib.sha256(code_verifier.encode()).digest()
            if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant.challenge:
                raise HTTPException(400, "invalid_grant: PKCE")
            if redirect_uri != grant.redirect_uri:
                raise HTTPException(400, "invalid_grant: redirect_uri")
            return {
                "access_token": secrets.token_urlsafe(16),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.id_token(grant.sub, grant.client_id, grant.nonce),
            }

        return app
