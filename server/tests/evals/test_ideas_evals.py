"""Live evals for the ideas pass: does the real LLM cluster the fixture transcripts into the
work items a person would expect? Run with ``uv run pytest -q tests/evals --live`` against a
real LiteLLM (JARVIS_LIVE_LITELLM_BASE_URL; JARVIS_LIVE_IDEAS_MODEL to compare models, e.g.
``fast-chat`` against ``heavy``). Unit tests cover the server's rules; only these judge quality.

Each case runs the transcript in two passes, split part-way, the way the live loop would: an
idea revisited later must land in the item the first pass created."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from jarvis_live.config import Settings
from jarvis_live.copilot.transcript import Line
from jarvis_live.llm.client import LLM, LiteLLMClient
from jarvis_live.workitems.ideas import IDEAS_SYSTEM, ideas_user
from jarvis_live.workitems.schema import IdeasDelta
from jarvis_live.workitems.state import IdeasState, Item, number_lines
from tests.ideas_fixtures import lines

pytestmark = pytest.mark.live

REPOS = {
    "lordmuffin/jarvis-voice": "Jarvis Live: transcription server, web dashboard, apps, CI",
    "lordmuffin/np-home-homelab": "homelab GitOps: k3s cluster, TrueNAS, alerting, LXC services",
}


@dataclass(frozen=True)
class Want:
    keywords: tuple[tuple[str, ...], ...]  # every group needs one keyword in title + intent
    repo: str | None = None  # None: any (or no) repo
    heard_at: tuple[int, ...] = ()  # start_ms the item must cite


@dataclass(frozen=True)
class Case:
    fixture: str
    split: int  # lines in the first pass
    want: tuple[Want, ...]
    reject: tuple[str, ...] = field(default=())  # keywords no item may be about


CASES = [
    Case(
        "diary_three_ideas.txt",
        split=9,
        want=(
            Want(
                (("gotify",), ("retry", "retries")), "lordmuffin/jarvis-voice", (21_000, 1_263_000)
            ),
            Want((("truenas", "nfs"),), "lordmuffin/np-home-homelab", (135_000, 768_000)),
            Want((("srt", "subtitle"),)),
        ),
        reject=("grocer", "coffee", "dark mode"),
    ),
    Case(
        "meeting_them_proposes.txt",
        split=3,
        want=(Want((("changelog",),), "lordmuffin/jarvis-voice"),),
        reject=("plant", "thursday"),
    ),
]


async def two_passes(llm: LLM, cfg: Settings, transcript: list[Line], split: int) -> list[Item]:
    state = IdeasState([], REPOS)
    for part in (transcript[:split], transcript[max(0, split - 1) :]):
        text, refs = number_lines(part)
        open_json = json.dumps(
            [
                {
                    "ref": it.ref,
                    "title": it.title,
                    "repo": it.repo,
                    "intent": it.intent,
                    "acceptance_criteria": it.acceptance_criteria,
                    "heard": [s.text for s in it.sources[:3]],
                }
                for it in state.items.values()
            ]
        )
        delta = await llm.complete_json(
            model=cfg.ideas_model or cfg.copilot_model,
            system=IDEAS_SYSTEM,
            user=ideas_user(
                today=datetime.now(UTC).date().isoformat(),
                repos=REPOS,
                open_json=open_json,
                closed=[],
                transcript=text,
            ),
            schema=IdeasDelta,
            timeout_s=cfg.ideas_timeout_s,
        )
        state.apply(delta, refs)
    return list(state.items.values())


def about(item: Item, keywords: tuple[str, ...]) -> bool:
    text = f"{item.title} {item.intent}".lower()
    return any(k in text for k in keywords)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.fixture)
async def test_ideas_case(case: Case) -> None:
    cfg = Settings()
    assert cfg.litellm_base_url, "set JARVIS_LIVE_LITELLM_BASE_URL"
    async with httpx.AsyncClient() as http:
        llm = LiteLLMClient(cfg.litellm_base_url, http, api_key_file=cfg.litellm_api_key_file)
        items = await two_passes(llm, cfg, lines(case.fixture), case.split)

    failures: list[str] = []
    for want in case.want:
        found = [it for it in items if all(about(it, g) for g in want.keywords)]
        if len(found) != 1:
            failures.append(f"{len(found)} items about {want.keywords}, want exactly 1")
            continue
        it = found[0]
        if want.repo and it.repo != want.repo:
            failures.append(f"{it.title!r}: repo {it.repo}, want {want.repo}")
        heard = {s.start_ms for s in it.sources}
        if missing := [t for t in want.heard_at if t not in heard]:
            failures.append(f"{it.title!r}: does not cite the lines at {missing} ms")
    for k in case.reject:
        if bad := [it.title for it in items if about(it, (k,))]:
            failures.append(f"items about {k!r}: {bad}")
    assert not failures, "; ".join(failures)
