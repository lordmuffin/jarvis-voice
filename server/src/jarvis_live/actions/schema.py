"""What the LLM may emit as an action proposal. Tolerant like ``copilot.schema``: unknown keys are
ignored, and an entry the server cannot validate is dropped by ``actions.state``, never fatal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["task.create", "vault.append", "reminder.schedule", "git.pr_comment"]


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Evidence(_LLMModel):
    """The transcript line a proposal rests on."""

    start_ms: int = Field(ge=0)
    text: str = Field(min_length=1)


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
