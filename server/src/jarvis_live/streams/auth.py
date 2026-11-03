"""The hook MediaMTX calls for every publish, read and API request (``authMethod: http``).

- **publish**: only with the ingest key of that very path, and only while ingest has room.
  OBS and phones send it as the SRT streamid's password, RTMP's ``?pass=`` or a token.
- **read** and **api**: only with the reader credential this server process generated at start.
  The server is MediaMTX's only reader, so nothing else can watch or list a stream.
- anything else (playback, metrics, pprof): never.

A 200 allows, anything else refuses; an empty attempt gets 401, which is what makes an RTSP
client send its credentials. The hook is served on its own port, never on the app port the
Ingress routes, and logs no secret.
"""

import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs

from fastapi import FastAPI, Response
from pydantic import BaseModel, ConfigDict

from jarvis_live.streams import keys

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReaderCredential:
    user: str
    password: str

    def matches(self, user: str, password: str) -> bool:
        return hmac.compare_digest(user, self.user) and hmac.compare_digest(
            password, self.password
        )


class AuthRequest(BaseModel):
    """MediaMTX's payload. Fields it may add later are ignored."""

    model_config = ConfigDict(extra="ignore")

    user: str = ""
    password: str = ""
    token: str = ""
    ip: str = ""
    action: str = ""
    path: str = ""
    protocol: str = ""
    query: str = ""

    def secret(self) -> str:
        """The key, wherever the client put it."""
        if self.password:
            return self.password
        if self.token:
            return self.token
        return (parse_qs(self.query).get("pass") or [""])[0]


class StreamAuth:
    def __init__(
        self, sm: keys.SM, reader: ReaderCredential, has_room: Callable[[str], bool]
    ) -> None:
        self._sm = sm
        self.reader = reader
        self._has_room = has_room

    async def allows(self, req: AuthRequest) -> bool:
        if req.action == "publish":
            return await self._publish(req)
        if req.action in ("read", "api"):
            return self.reader.matches(req.user, req.password)
        return False

    async def _publish(self, req: AuthRequest) -> bool:
        secret = req.secret()
        if not secret:
            return False  # a challenge, not a failure: RTSP retries with credentials
        if await keys.verify(self._sm, req.path, secret) is None:
            log.warning(
                "stream publish refused: bad key",
                extra={"path": req.path, "protocol": req.protocol, "ip": req.ip},
            )
            return False
        if not self._has_room(req.path):
            log.warning("stream publish refused: ingest is full", extra={"path": req.path})
            return False
        return True


def auth_app(auth: StreamAuth) -> FastAPI:
    app = FastAPI(title="Jarvis Live stream auth", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/mediamtx/auth")
    async def check(req: AuthRequest) -> Response:
        return Response(status_code=200 if await auth.allows(req) else 401)

    return app
