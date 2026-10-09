"""Shared eval machinery: one copilot cycle over a whole transcript, with checks and a report."""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import COPILOT_SYSTEM, copilot_user
from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.copilot.state import CopilotState
from jarvis_live.llm.client import LLM
from jarvis_live.protocol import Action, Copilot
from tests.evals.transcripts import Case, Expected

MAX_ROUNDTRIP_S = 8.0


@dataclass
class Outcome:
    case: str
    seconds: float
    delta: CopilotDelta
    snapshot: Copilot
    failures: list[str] = field(default_factory=list)


@dataclass
class Report:
    rows: list[Outcome] = field(default_factory=list)

    def render(self) -> list[str]:
        head = f"{'case':<32} {'time':>6} {'notes':>5} {'acts':>4} {'decs':>4} {'sugg':>4} result"
        lines = [head, "-" * len(head)]
        for o in self.rows:
            s = o.snapshot
            verdict = "PASS" if not o.failures else "FAIL: " + "; ".join(o.failures)
            lines.append(
                f"{o.case:<32} {o.seconds:>5.1f}s {len(s.notes):>5} {len(s.actions):>4} "
                f"{len(s.decisions):>4} {len(s.suggestions):>4} {verdict}"
            )
        return lines


REPORT = Report()


async def run_case(
    llm: LLM, case: Case, settings: Settings | None = None
) -> tuple[CopilotDelta, CopilotState, float]:
    cfg = settings or Settings()
    state = CopilotState()
    user = copilot_user(
        today=datetime.now(UTC).date().isoformat(),
        state_json=state.prompt_json(),
        related="",
        transcript=case.transcript,
    )
    t0 = time.monotonic()
    delta = await llm.complete_json(
        model=cfg.copilot_model,
        system=COPILOT_SYSTEM,
        user=user,
        schema=CopilotDelta,
        timeout_s=cfg.copilot_timeout_s,
    )
    seconds = time.monotonic() - t0
    state.apply(delta, now_ms=0, default_ttl_s=cfg.suggestion_default_ttl_s)
    return delta, state, seconds


def find_action(actions: list[Action], exp: Expected) -> Action | None:
    for a in actions:
        text = a.text.lower()
        if all(any(k in text for k in group) for group in exp.keywords):
            return a
    return None


def check(case: Case, delta: CopilotDelta, state: CopilotState, seconds: float) -> list[str]:
    failures: list[str] = []
    snap = state.snapshot()
    Copilot.model_validate(snap.model_dump())  # protocol-valid
    if len(delta.topics) > 5:
        failures.append(f"{len(delta.topics)} topics (>5)")
    for exp in case.actions:
        label = "+".join("/".join(g) for g in exp.keywords)
        a = find_action(snap.actions, exp)
        if a is None:
            failures.append(f"missing action [{label}]")
        elif exp.owner is None and a.owner is not None:
            failures.append(f"invented owner {a.owner!r} on [{label}]")
        elif exp.owner is not None and (a.owner is None or exp.owner not in a.owner.lower()):
            failures.append(f"owner {a.owner!r} on [{label}], expected ~{exp.owner!r}")
    if case.expect_no_owner_anywhere:
        for a in snap.actions:
            if a.owner is not None:
                failures.append(f"invented owner {a.owner!r} on {a.text!r}")
    if seconds >= MAX_ROUNDTRIP_S:
        failures.append(f"round-trip {seconds:.1f}s >= {MAX_ROUNDTRIP_S:.0f}s")
    return failures
