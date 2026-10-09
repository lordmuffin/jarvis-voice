"""LLM output schemas. Deliberately tolerant (unknown keys ignored, over-long lists trimmed) so a
slightly chatty model does not burn the single retry."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TOPICS = 5


class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NoteUpsert(_LLMModel):
    id: str | None = None
    text: str = Field(min_length=1)


class ActionUpsert(_LLMModel):
    id: str | None = None
    text: str = Field(min_length=1)
    owner: str | None = None
    due: str | None = None


class SuggestionIn(_LLMModel):
    kind: Literal["question", "gap", "counterpoint", "fact_check"]
    text: str = Field(min_length=1)
    ttl_s: Annotated[int, Field(ge=1, le=3600)] = 120


class CopilotDelta(_LLMModel):
    notes_upsert: list[NoteUpsert] = Field(default_factory=list)
    actions_upsert: list[ActionUpsert] = Field(default_factory=list)
    decisions_upsert: list[NoteUpsert] = Field(default_factory=list)
    remove_ids: list[str] = Field(default_factory=list)
    suggestions: list[SuggestionIn] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)

    @field_validator("topics")
    @classmethod
    def _trim_topics(cls, v: list[str]) -> list[str]:
        return [t.strip() for t in v if t.strip()][:MAX_TOPICS]


class FinalAction(_LLMModel):
    text: str = Field(min_length=1)
    owner: str | None = None
    due: str | None = None


class FinalNote(_LLMModel):
    title: str = Field(min_length=1)
    summary: str
    decisions: list[str] = Field(default_factory=list)
    actions: list[FinalAction] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    # Names of existing vault notes. The finalizer drops anything it cannot resolve in the index.
    related: list[str] = Field(default_factory=list)
