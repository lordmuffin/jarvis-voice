"""Homelab Q&A: forwards a device's question to the persistent Pi Durable `jarvis` session and
returns its answer so the client can speak it (docs/pi-durable.md in np-home-homelab). The
DPI token lives only here — the server's filesystem — never on the device; see
`jarvis_live/homelab/client.py`. Same auth as every other route: a signed-in device or web
session, not a separate credential."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis_live.auth import Principal, require_owner_or_admin
from jarvis_live.homelab.client import FALLBACK, DpiClient, DpiError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/homelab")

PrincipalDep = Annotated[Principal, Depends(require_owner_or_admin)]


class AskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(min_length=1, max_length=4000)
    # The caller's own idempotency key (e.g. "<conversation id>-<turn>"), reused verbatim on
    # dpi-backend's side: a client retry with the same key is answered once.
    request_id: str = Field(min_length=1, max_length=200, alias="requestId")


class AskResponse(BaseModel):
    answer: str
    # False when `answer` is the fallback phrase, not a real answer from the homelab assistant.
    ok: bool


@router.post("/ask")
async def ask(body: AskRequest, request: Request, _principal: PrincipalDep) -> AskResponse:
    dpi: DpiClient | None = request.app.state.dpi
    if dpi is None:
        return AskResponse(answer=FALLBACK, ok=False)
    try:
        answer = await dpi.ask(body.question, body.request_id)
    except DpiError as e:
        log.warning("homelab ask failed: %s", e)
        return AskResponse(answer=FALLBACK, ok=False)
    return AskResponse(answer=answer, ok=True)
