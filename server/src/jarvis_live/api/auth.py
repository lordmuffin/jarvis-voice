"""Sign-in with Pocket-ID.

Web: ``/auth/login`` -> Pocket-ID -> ``/auth/callback`` sets the session cookie.

Android: the app opens ``/auth/login?client=android&challenge=<S256(verifier)>`` in a browser
tab; the callback redirects to the app's fixed URI with a one-time code, which the app redeems
with its verifier at ``POST /v1/auth/device-code`` for a device token of its own.
"""

import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, update

from jarvis_live.auth import (
    SESSION_COOKIE,
    create_device,
    create_login_session,
    hash_token,
    set_session_cookie,
)
from jarvis_live.db.models import DeviceCode, LoginSession, OidcFlow
from jarvis_live.oidc import OIDCClient, OIDCError, new_secret, s256
from jarvis_live.users import Claims, SignInDenied, sign_in

log = logging.getLogger(__name__)

router = APIRouter()

ANDROID_REDIRECT = "com.lordmuffin.jarvisvoice://auth/callback"
FLOW_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
_CHALLENGE = r"^[A-Za-z0-9_-]{43}$"  # base64url(sha256), unpadded


def _oidc(request: Request) -> OIDCClient:
    oidc: OIDCClient | None = request.app.state.oidc
    if oidc is None:
        raise HTTPException(404, "sign-in is not configured")
    return oidc


def _redirect_uri(request: Request) -> str:
    base = request.app.state.settings.public_base_url.rstrip("/") or str(request.base_url).rstrip(
        "/"
    )
    return f"{base}/auth/callback"


def _safe_return_to(value: str) -> str:
    """Only same-origin paths: no scheme, no host, no protocol-relative ``//``."""
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


@router.get("/auth/login", include_in_schema=False)
async def login(
    request: Request,
    client: Literal["web", "android"] = "web",
    return_to: str = "/",
    challenge: Annotated[str | None, Query(pattern=_CHALLENGE)] = None,
) -> RedirectResponse:
    oidc = _oidc(request)
    if client == "android" and challenge is None:
        raise HTTPException(400, "android sign-in needs a PKCE challenge")
    flow = OidcFlow(
        state=new_secret(),
        nonce=new_secret(),
        pkce_verifier=new_secret(),
        client=client,
        app_challenge=challenge if client == "android" else None,
        return_to=_safe_return_to(return_to),
        expires_at=datetime.now(UTC) + FLOW_TTL,
    )
    async with request.app.state.sessionmaker() as db:
        await db.execute(delete(OidcFlow).where(OidcFlow.expires_at < datetime.now(UTC)))
        db.add(flow)
        await db.commit()
    try:
        url = await oidc.authorize_url(
            redirect_uri=_redirect_uri(request),
            state=flow.state,
            nonce=flow.nonce,
            verifier=flow.pkce_verifier,
        )
    except Exception as e:
        log.exception("identity provider unavailable")
        raise HTTPException(502, "the identity provider is unavailable") from e
    return RedirectResponse(url, status_code=303)


def _android_error(error: str) -> RedirectResponse:
    return RedirectResponse(f"{ANDROID_REDIRECT}?{urlencode({'error': error})}", status_code=303)


@router.get("/auth/callback", include_in_schema=False)
async def callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
) -> Response:
    oidc = _oidc(request)
    sm = request.app.state.sessionmaker
    async with sm() as db:
        flow = (
            await db.execute(delete(OidcFlow).where(OidcFlow.state == state).returning(OidcFlow))
        ).scalar_one_or_none()
        await db.commit()
    if flow is None or flow.expires_at <= datetime.now(UTC):
        return PlainTextResponse("This sign-in link has expired. Please try again.", 400)
    android = flow.client == "android"
    if error or not code:
        log.info("sign-in cancelled at the identity provider: %s", error or "no code")
        if android:
            return _android_error(error or "cancelled")
        return PlainTextResponse(f"Sign-in failed: {error or 'no code'}", 400)
    try:
        claims = await oidc.exchange(
            code=code,
            redirect_uri=_redirect_uri(request),
            verifier=flow.pkce_verifier,
            nonce=flow.nonce,
        )
        user = await sign_in(sm, Claims.of(claims), request.app.state.settings)
    except SignInDenied as e:
        log.info("sign-in denied: %s", e)
        if android:
            return _android_error("access_denied")
        return PlainTextResponse("Your account is not allowed to use Jarvis Live.", 403)
    except (OIDCError, KeyError) as e:
        log.warning("sign-in failed: %s", e)
        if android:
            return _android_error("server_error")
        return PlainTextResponse("Sign-in failed. Please try again.", 400)
    log.info("signed in", extra={"user": str(user.id), "client": flow.client})

    if android:
        assert flow.app_challenge is not None
        one_time = new_secret()
        async with sm() as db:
            await db.execute(delete(DeviceCode).where(DeviceCode.expires_at < datetime.now(UTC)))
            db.add(
                DeviceCode(
                    code_hash=hash_token(one_time),
                    user_id=user.id,
                    app_challenge=flow.app_challenge,
                    expires_at=datetime.now(UTC) + CODE_TTL,
                )
            )
            await db.commit()
        return RedirectResponse(
            f"{ANDROID_REDIRECT}?{urlencode({'code': one_time})}", status_code=303
        )

    settings = request.app.state.settings
    token = await create_login_session(sm, user.id, settings.login_session_days)
    response = RedirectResponse(flow.return_to, status_code=303)
    set_session_cookie(response, token, settings)
    return response


@router.post("/auth/logout", status_code=204, include_in_schema=False)
async def logout(request: Request) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        async with request.app.state.sessionmaker() as db:
            await db.execute(
                delete(LoginSession).where(LoginSession.token_hash == hash_token(cookie))
            )
            await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


class DeviceCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    verifier: str = Field(min_length=43, max_length=128)
    device_name: str = Field(min_length=1, max_length=200)


class DeviceTokenResponse(BaseModel):
    device_id: str
    token: str


@router.post("/v1/auth/device-code")
async def redeem_device_code(body: DeviceCodeRequest, request: Request) -> DeviceTokenResponse:
    """Exchange the one-time code from an Android sign-in for a new device token."""
    sm = request.app.state.sessionmaker
    now = datetime.now(UTC)
    async with sm() as db:
        # Consumed by the first attempt whatever the outcome, so a code can't be brute-forced.
        found = (
            await db.execute(
                update(DeviceCode)
                .where(
                    DeviceCode.code_hash == hash_token(body.code),
                    DeviceCode.used_at.is_(None),
                    DeviceCode.expires_at > now,
                )
                .values(used_at=now)
                .returning(DeviceCode.user_id, DeviceCode.app_challenge)
            )
        ).one_or_none()
        await db.commit()
    if found is None or not hmac.compare_digest(s256(body.verifier), found.app_challenge):
        raise HTTPException(400, "invalid or expired code")
    device_id, token = await create_device(sm, body.device_name, found.user_id)
    return DeviceTokenResponse(device_id=str(device_id), token=token)
