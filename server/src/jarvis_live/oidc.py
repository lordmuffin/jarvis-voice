"""A minimal OpenID Connect relying party for Pocket-ID: authorization code + PKCE (S256),
confidential client, ID token verified against the issuer's JWKS."""

import base64
import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

log = logging.getLogger(__name__)

SCOPES = "openid email profile groups"
ALGORITHMS = ["RS256", "ES256", "EdDSA"]


class OIDCError(Exception):
    pass


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def s256(verifier: str) -> str:
    """The PKCE S256 challenge for ``verifier``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OIDCClient:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret_file: Path | None,
        client: httpx.AsyncClient,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._secret_file = client_secret_file
        self._http = client
        self._meta: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKSet | None = None

    async def metadata(self) -> dict[str, Any]:
        if self._meta is None:
            r = await self._http.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            meta = r.json()
            if str(meta.get("issuer", "")).rstrip("/") != self.issuer:
                raise OIDCError(f"discovery issuer {meta.get('issuer')!r} != {self.issuer!r}")
            self._meta = meta
        return self._meta

    async def authorize_url(
        self, *, redirect_uri: str, state: str, nonce: str, verifier: str
    ) -> str:
        meta = await self.metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": s256(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{meta['authorization_endpoint']}?{query}"

    def _secret(self) -> str:
        if self._secret_file is None:
            return ""
        return self._secret_file.read_text().strip()  # re-read: a rotated secret just works

    async def exchange(
        self, *, code: str, redirect_uri: str, verifier: str, nonce: str
    ) -> dict[str, Any]:
        """Redeem an authorization code; returns the verified ID token claims."""
        meta = await self.metadata()
        r = await self._http.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "client_id": self._client_id,
            },
            auth=(self._client_id, self._secret()),
            timeout=15,
        )
        if r.status_code != 200:
            raise OIDCError(f"token endpoint returned {r.status_code}: {r.text[:200]}")
        id_token = r.json().get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("token response has no id_token")
        claims = await self._verify(id_token)
        if claims.get("nonce") != nonce:
            raise OIDCError("nonce mismatch")
        return claims

    async def _verify(self, id_token: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError as e:
            raise OIDCError(f"malformed id_token: {e}") from e
        key = await self._key(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=ALGORITHMS,
                audience=self._client_id,
                issuer=(await self.metadata())["issuer"],
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=60,
            )
        except jwt.PyJWTError as e:
            raise OIDCError(f"invalid id_token: {e}") from e
        return claims

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        for refresh in (False, True):  # a new kid means the issuer rotated keys
            if self._jwks is None or refresh:
                r = await self._http.get((await self.metadata())["jwks_uri"], timeout=10)
                r.raise_for_status()
                self._jwks = jwt.PyJWKSet.from_dict(r.json())
            keys = self._jwks.keys
            for k in keys:
                if kid is None or k.key_id == kid:
                    return k
        raise OIDCError(f"no signing key {kid!r}")
