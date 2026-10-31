"""Starting a Pi Durable session for an approved ``pi.session`` proposal."""

from typing import Any, Protocol

from jarvis_live.actions.service import ExecutionError
from jarvis_live.db.models import ActionProposal
from jarvis_live.homelab.client import DpiError


class PiStarter(Protocol):
    async def start_session(self, name: str, prompt: str, *, key: str) -> str: ...


class PiSessionExecutor:
    def __init__(self, pi: PiStarter) -> None:
        self._pi = pi

    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        """The proposal's id is the idempotency key, so a retry starts nothing twice. The agent
        gets the approved text and nothing else, so what was approved is what it reads."""
        key = str(proposal.id)
        name = f"jarvis-{key[:8]}"
        try:
            session_id = await self._pi.start_session(name, proposal.payload["text"], key=key)
        except DpiError as e:
            raise ExecutionError(str(e)) from e
        return {"session_id": session_id, "session_name": name}

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        """Whether the brief reached the agent cannot be read back, so an interrupted start is
        reported as unknown. Retrying is safe: its keys repeat."""
        return None
