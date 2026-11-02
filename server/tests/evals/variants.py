"""Reply shapes to try in the evals, never sent by a real cycle. Each is a change to the request:
the system prompt and the JSON schema. ``TODAY`` is what copilot/loop.py sends, and it is the
default everywhere here, so the evals keep grading the real request unless asked otherwise.

A real change to the reply is a later PR, after the lab soak of #260: docs/plans/fast-tier.md."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jarvis_live.copilot.prompts import COPILOT_SYSTEM
from jarvis_live.copilot.schema import CopilotDelta


@dataclass(frozen=True)
class Variant:
    name: str
    system: str = COPILOT_SYSTEM
    schema: type[CopilotDelta] = CopilotDelta


TODAY = Variant("today")

CAPS = {"notes_upsert": 2, "decisions_upsert": 2, "topics": 3}
OWNER_DUE = ("text", "owner", "due")
SLOW_CYCLE = ("suggestions", "proposals")
MINIFIED = "\n- Write the JSON on one line, with no spaces or line breaks between tokens."


def narrowed(
    *,
    drop: Sequence[str] = (),
    caps: Mapping[str, int] | None = None,
    action_required: Sequence[str] = (),
) -> type[CopilotDelta]:
    """CopilotDelta sending a narrower schema, parsing as tolerantly as it does. Keeps the name,
    so a request differs from today's only where the shape does."""

    class Narrowed(CopilotDelta):
        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            schema = super().model_json_schema(*args, **kwargs)
            for key in drop:
                del schema["properties"][key]
            for key, limit in (caps or {}).items():
                schema["properties"][key]["maxItems"] = limit
            schema["required"] = list(schema["properties"])
            if action_required:
                schema["$defs"]["ActionUpsert"]["required"] = list(action_required)
            schema["title"] = "CopilotDelta"
            return schema

    Narrowed.__name__ = "CopilotDelta"
    return Narrowed


def without_keys(system: str, keys: Sequence[str]) -> str:
    """The prompt with those keys' bullets taken out, to the start of the next bullet."""
    for key in keys:
        system, found = re.subn(rf"- {key}:.*?\n(?=- )", "", system, flags=re.DOTALL)
        assert found == 1, key
    return system


def minified(system: str) -> str:
    return system + MINIFIED


NO_SUGG_PROP = without_keys(COPILOT_SYSTEM, SLOW_CYCLE)

VARIANTS = {
    v.name: v
    for v in (
        TODAY,
        Variant("caps", schema=narrowed(caps={**CAPS, "suggestions": 1, "proposals": 1})),
        Variant("no-sugg-prop", NO_SUGG_PROP, narrowed(drop=SLOW_CYCLE)),
        Variant("no-sugg-prop-caps", NO_SUGG_PROP, narrowed(drop=SLOW_CYCLE, caps=CAPS)),
        Variant("owners", schema=narrowed(action_required=OWNER_DUE)),
        Variant(
            "slim",
            NO_SUGG_PROP,
            narrowed(drop=SLOW_CYCLE, caps=CAPS, action_required=OWNER_DUE),
        ),
        Variant(
            "slim-min",
            minified(NO_SUGG_PROP),
            narrowed(drop=SLOW_CYCLE, caps=CAPS, action_required=OWNER_DUE),
        ),
    )
}
