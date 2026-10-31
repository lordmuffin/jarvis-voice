"""Rendering a work item into a Claude Code prompt from ``prompts/templates/work-item.md``.

No LLM and no network: the LLM drafts the fields, the checked-in template owns the structure.
Placeholders are ``{{name}}``. Substitution is a single pass, so a value that contains
``{{repo}}`` (a transcript can) stays literal. The template is checked when it is loaded:
an unknown placeholder, or a missing required one, is an error then, not at render time."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis_live.config import Settings
from jarvis_live.copilot.transcript import mmss

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
FIELDS = frozenset(
    {
        "repo",
        "title",
        "intent",
        "acceptance_criteria",
        "done_when",
        "sources",
        "session_title",
        "session_url",
        "date",
        "branch",
        "pr_marker",
    }
)
REQUIRED = frozenset({"repo", "title", "intent", "acceptance_criteria", "done_when"})
DEFAULT_DONE_WHEN = (
    "Every acceptance criterion is met, tests that prove them pass along with the repo's own "
    "checks, and the change is ready for review as a pull request."
)
TEMPLATE_RELPATH = Path("prompts/templates/work-item.md")
IMAGE_TEMPLATE = Path("/app") / TEMPLATE_RELPATH


class TemplateError(ValueError):
    pass


class PromptTemplate:
    def __init__(self, text: str) -> None:
        names = set(PLACEHOLDER.findall(text))
        if unknown := names - FIELDS:
            raise TemplateError(f"unknown placeholder(s): {', '.join(sorted(unknown))}")
        if missing := REQUIRED - names:
            raise TemplateError(f"missing placeholder(s): {', '.join(sorted(missing))}")
        self.text = text

    @classmethod
    def load(cls, path: Path) -> "PromptTemplate":
        try:
            return cls(path.read_text(encoding="utf-8"))
        except OSError as e:
            raise TemplateError(f"cannot read {path}: {e}") from e

    def render(self, values: dict[str, str]) -> str:
        if missing := FIELDS - values.keys():
            raise TemplateError(f"no value for: {', '.join(sorted(missing))}")
        out = PLACEHOLDER.sub(lambda m: values[m.group(1)], self.text)
        return out.rstrip() + "\n"


def find_template(cfg: Settings) -> Path:
    """The configured template, else the image's copy, else the repository's."""
    if cfg.work_template is not None:
        return cfg.work_template
    if IMAGE_TEMPLATE.is_file():
        return IMAGE_TEMPLATE
    for parent in Path(__file__).resolve().parents:
        if (parent / TEMPLATE_RELPATH).is_file():
            return parent / TEMPLATE_RELPATH
    return IMAGE_TEMPLATE  # reported as missing by load()


@dataclass(frozen=True)
class PromptInput:
    item_id: str
    session_id: str
    session_title: str | None
    started_at: datetime
    ref: str
    title: str
    repo: str | None
    intent: str
    acceptance_criteria: list[str]
    done_when: str | None
    sources: list[dict[str, object]]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "item"


def branch_name(session_id: str, ref: str, title: str) -> str:
    return f"claude/work-{session_id[:6]}-{ref}-{slug(title)}"


def pr_marker(item_id: str) -> str:
    """The line a prompt asks for in the PR description, which finds the PR on any branch."""
    return f"Jarvis-Work-Item: {item_id}"


def session_url(cfg: Settings, session_id: str, t_ms: int | None = None) -> str:
    base = cfg.public_base_url.rstrip("/")
    if not base:
        return ""
    return f"{base}/sessions/{session_id}" + (f"#t={t_ms}" if t_ms is not None else "")


def render_prompt(template: PromptTemplate, item: PromptInput, cfg: Settings) -> str:
    criteria = (
        "\n".join(f"- [ ] {_one_line(c)}" for c in item.acceptance_criteria)
        or "- [ ] (none yet: add at least one before approving)"
    )
    sources = []
    for s in item.sources:
        start = int(str(s["start_ms"]))
        stamp = mmss(start)
        url = session_url(cfg, item.session_id, start)
        where = f"[{stamp}]({url})" if url else stamp
        sources.append(f"- {where}: “{_one_line(str(s['text']))}”")
    tz = ZoneInfo(cfg.timezone)
    return template.render(
        {
            "repo": item.repo or "(no repository chosen yet)",
            "title": _one_line(item.title),
            "intent": item.intent.strip() or "(no description yet)",
            "acceptance_criteria": criteria,
            "done_when": _one_line(item.done_when or "") or DEFAULT_DONE_WHEN,
            "sources": "\n".join(sources) or "- (no transcript excerpts)",
            "session_title": _one_line(item.session_title or "") or "Untitled session",
            "session_url": session_url(cfg, item.session_id) or "(no link configured)",
            "date": item.started_at.astimezone(tz).date().isoformat(),
            "branch": branch_name(item.session_id, item.ref, item.title),
            "pr_marker": pr_marker(item.item_id),
        }
    )
