"""What the ideas pass asks the LLM for. Tolerant like ``copilot.schema``: unknown keys are
ignored, line refs may come as numbers, and the server enforces every limit afterwards."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str | None = None  # an existing item's ref to refine it; omit to propose a new one
    title: str = Field(min_length=1)
    repo: str | None = None
    intent: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    done_when: str | None = None
    source_lines: list[str] = Field(default_factory=list)  # "L12", the transcript lines cited

    @field_validator("source_lines", mode="before")
    @classmethod
    def _refs_as_strings(cls, v: object) -> object:
        return [str(x) for x in v] if isinstance(v, list) else v

    @field_validator("ref", mode="before")
    @classmethod
    def _ref_as_string(cls, v: object) -> object:
        return None if v in (None, "") else str(v)


class IdeasDelta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items_upsert: list[ItemUpsert] = Field(default_factory=list)
