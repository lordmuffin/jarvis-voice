"""What the voice pass asks the LLM for. Tolerant like ``copilot.schema``: anything it cannot
read as a target means ``none``, and an unusable proposal is dropped."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from jarvis_live.actions.schema import Proposals

TARGETS = ("cloud", "pi", "action", "none")
Target = Literal["cloud", "pi", "action", "none"]


def _target(value: Any) -> Any:
    return value if value in TARGETS else "none"


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: Annotated[Target, BeforeValidator(_target)] = "none"
    title: str = ""
    task: str = ""
    repo: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    done_when: str | None = None
    proposals: Proposals = Field(default_factory=list)
