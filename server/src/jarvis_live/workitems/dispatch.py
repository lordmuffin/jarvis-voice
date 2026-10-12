"""The dispatch gate. Nothing reaches a dispatcher unless this says so.

Phase (a) ships the gate and no dispatcher: an item that passes gets 503. Phase (b) plugs a
dispatcher in behind the same gate (docs/plans/copilot-to-claude-code.md §3)."""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from jarvis_live.db.models import WorkItem

ActorKind = Literal["web", "device", "system"]


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    kind: ActorKind  # "web": the dashboard sign-in; "device": a device token


class Dispatcher(Protocol):
    async def dispatch(
        self,
        item_id: uuid.UUID,
        *,
        repo: str,
        prompt: str,
        prompt_sha256: str,
        actor_user_id: uuid.UUID,
    ) -> None:
        """Start running an approved item. Called only after ``refusal`` returned None."""
        ...


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def refusal(
    item: WorkItem, actor: Actor, allowlist: dict[str, str], dispatchable: dict[str, str]
) -> str | None:
    """Why ``actor`` may not dispatch ``item``, or None if they may. ``allowlist`` is the
    work repos; ``dispatchable`` the repos a run may target (lab: only its sandbox)."""
    if actor.kind != "web":
        return "dispatching needs the dashboard sign-in, not a device token"
    if item.user_id != actor.user_id:
        return "not your work item"
    if item.state != "approved":
        return f"the work item is {item.state}, not approved"
    if not item.prompt_md or not item.prompt_sha256 or sha256(item.prompt_md) != item.prompt_sha256:
        return "the prompt changed since it was approved"
    if item.approved_by != actor.user_id:
        return "approved by someone else"
    if not item.repo or item.repo not in allowlist:
        return f"{item.repo or 'no repository'} is not on the allowlist"
    if item.repo not in dispatchable:
        return f"{item.repo} is not on the dispatch allowlist of this server"
    return None
