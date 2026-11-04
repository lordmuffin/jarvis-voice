"""What the voice pass asks the LLM for. Tolerant like ``copilot.schema``: anything it cannot
read as a target means ``none``, and an unusable proposal is dropped.

Parsing shares ``ProposalIn`` with the copilot, so the server reads both the same way. Only what
the voice request *sends* differs: each spoken kind with the fields ``build_payload`` needs to file
it, in place of the shared flat shape whose fields are all optional. On the flat shape every model
R2 ran sent a ``git.pr_comment`` with no body, or a reminder with no title, and the server refused
them (docs/plans/fast-tier.md). Evidence is left out: the server sets it to what it heard."""

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from jarvis_live.actions.schema import Proposals

TARGETS = ("cloud", "pi", "action", "none")
Target = Literal["cloud", "pi", "action", "none"]


def _target(value: Any) -> Any:
    return value if value in TARGETS else "none"


def _kind(kind: str, required: Sequence[str], **fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"kind": {"const": kind}, **fields},
        "required": ["kind", *required],
    }


_TEXT: dict[str, Any] = {"type": "string"}
_SPOKEN: tuple[dict[str, Any], ...] = (
    _kind("task.create", ["text"], text=_TEXT, due=_TEXT),
    _kind("reminder.schedule", ["title", "at"], title=_TEXT, at=_TEXT, body=_TEXT),
    _kind("git.pr_comment", ["repo", "pr", "body"], repo=_TEXT, pr={"type": "integer"}, body=_TEXT),
)
SPOKEN_KINDS = tuple(k["properties"]["kind"]["const"] for k in _SPOKEN)


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: Annotated[Target, BeforeValidator(_target)] = "none"
    title: str = ""
    task: str = ""
    repo: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    done_when: str | None = None
    proposals: Proposals = Field(default_factory=list)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"]["proposals"]["items"] = {"anyOf": [dict(k) for k in _SPOKEN]}
        schema.pop("$defs", None)
        return schema
