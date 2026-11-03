"""Reply shapes to try in the evals, never sent by a real cycle. Each is a change to the request:
the system prompt and the JSON schema. ``TODAY`` is what copilot/loop.py sends, and it is the
default everywhere here, so the evals keep grading the real request unless asked otherwise.

A real change to the reply is a later PR: docs/plans/fast-tier.md."""

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
OWNER_FIRST = ("owner", "due", "text")
OWNER_LINE = (
    "\n- owner: the name of the person who will do it, as said on the line. When a line says "
    '"Dana will send it" or "I\'ll do it" under a name, that name is the owner, and the text '
    "does not repeat it."
)
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
                action = schema["$defs"]["ActionUpsert"]
                action["required"] = list(action_required)
                # A grammar writes required keys in this order, so this is what comes first.
                action["properties"] = {
                    **{k: action["properties"][k] for k in action_required},
                    **action["properties"],
                }
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


def owner_first(system: str) -> str:
    """Asks for the name, within the existing rule. It does not relax "do not invent"."""
    return system + OWNER_LINE


def compact_grammar(schema: dict[str, Any]) -> str:
    """GBNF for the narrow schemas here, with no whitespace outside strings: a JSON schema cannot
    say that. llama.cpp only, so this measures what compact JSON would save. The model is not told
    anything: the prompt is the variant's own. Required keys first, in order, then optional ones."""
    rules: dict[str, str] = {
        "char": r'[^"\\\x00-\x1F] | "\\" ["\\/bfnrt]',
        "null": '"null"',
        "string": r'"\"" char* "\""',
        "string-1": r'"\"" char+ "\""',
    }

    def kebab(name: str) -> str:
        return re.sub(r"(?<=[a-z])(?=[A-Z])", "-", name).lower()

    def rule(node: dict[str, Any]) -> str:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if kebab(name) not in rules:
                rules[kebab(name)] = ""  # declared first: a definition may meet itself
                rules[kebab(name)] = body(schema["$defs"][name])
            return kebab(name)
        return f"( {body(node)} )"

    def body(node: dict[str, Any]) -> str:
        kind = node.get("type")
        if "anyOf" in node:
            return " | ".join(rule(x) for x in node["anyOf"])
        if kind == "null":
            return "null"
        if kind == "string":
            return "string-1" if node.get("minLength") else "string"
        if kind == "array":
            item = rule(node["items"])
            more = f"{{0,{node['maxItems'] - 1}}}" if "maxItems" in node else "*"
            return f'"[" ( {item} ( "," {item} ){more} )? "]"'
        if kind == "object":
            need = node.get("required", [])
            props = node["properties"]
            required = [f"{quoted(k)} {rule(props[k])}" for k in props if k in need]
            parts = [' "," '.join(required)]
            for key in (k for k in props if k not in need):
                parts.append(f'( "," {quoted(key)} {rule(props[key])} )?')
            return f'"{{" {" ".join(parts)} "}}"'
        raise ValueError(f"no grammar for {kind or node}")

    def quoted(key: str) -> str:
        return '"\\"' + key + '\\":"'

    rules["root"] = body(schema)
    return "\n".join(f"{name} ::= {text}" for name, text in rules.items()) + "\n"


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
        Variant(
            "slim-owner",
            owner_first(minified(NO_SUGG_PROP)),
            narrowed(drop=SLOW_CYCLE, caps=CAPS, action_required=OWNER_FIRST),
        ),
    )
}
