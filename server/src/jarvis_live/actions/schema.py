"""What the LLM may emit as an action proposal. Tolerant like ``copilot.schema``: unknown keys are
ignored, and an entry the server cannot validate is dropped by ``actions.state``, never fatal."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

Kind = Literal["task.create", "vault.append", "reminder.schedule", "git.pr_comment"]


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


_CLOCK = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")


class Evidence(_LLMModel):
    """The transcript line a proposal rests on. The model cites it as ``at: "mm:ss"`` (the
    transcript's own timestamps); the server replaces it with the real line (``ground``)."""

    start_ms: int = Field(ge=0)
    text: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_clock(cls, data: Any) -> Any:
        if isinstance(data, dict) and "start_ms" not in data and isinstance(data.get("at"), str):
            if m := _CLOCK.match(data["at"]):
                h, mm, ss = int(m[1] or 0), int(m[2]), int(m[3])
                return {**data, "start_ms": ((h * 60 + mm) * 60 + ss) * 1000}
        return data


class ProposalIn(_LLMModel):
    """One flat shape for all kinds; ``actions.state`` checks the fields each kind needs."""

    kind: Kind
    evidence: list[Evidence] = Field(default_factory=list)
    text: str | None = None  # task.create, vault.append
    due: str | None = None  # task.create
    path: str | None = None  # vault.append
    title: str | None = None  # reminder.schedule
    body: str | None = None  # reminder.schedule, git.pr_comment
    at: str | None = None  # reminder.schedule
    repo: str | None = None  # git.pr_comment
    pr: int | None = None  # git.pr_comment


def _lenient(value: Any) -> list[ProposalIn]:
    """Keep the proposals that parse; drop the rest, so one chatty entry cannot fail the whole
    delta (and burn its single retry)."""
    if not isinstance(value, list):
        return []
    out: list[ProposalIn] = []
    for item in value:
        try:
            out.append(ProposalIn.model_validate(item))
        except ValidationError:
            continue
    return out


Proposals = Annotated[list[ProposalIn], BeforeValidator(_lenient)]
