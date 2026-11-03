"""The voice eval: what the voice pass would file, from invented spoken requests, on a local model.
It sends what VoicePass._interpret sends: the real VOICE_SYSTEM, voice_user and VoiceRequest through
the real client, thinking off. A run on CPU judges quality and tokens. Its times mean nothing for a
card, so the time verdict is projected, and stays unproven until R5 measures the pod.

THE BAR, fixed before any model ran. 25 cases, three runs each:

1. Every case passes at least 2 of 3.
2. The critical cases pass 3 of 3: a wrong answer there files what nobody asked for.
3. At least 90% of all runs pass.
4. No reply is cut off.

Time is judged apart, from the rows: reply p95 at the candidate's card rates, each attempt counted,
must be within voice's 2.5 s for the LLM part. Rates are estimated, so a candidate stops on quality
only, and the time verdict is reported with the decode rate it would need.

    uv run python -m tests.evals.voice run --base http://127.0.0.1:18090/v1 --model NAME \\
        --out /somewhere/outside/the/repo/rows.jsonl
    uv run python -m tests.evals.voice report rows.jsonl --control q25-3b

Rows hold whole replies, so keep them out of the repo. --variant changes the request, never the
bar, and no real voice pass sends one. "today" is what the server sends, and it is the default.
"""

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from jarvis_live.actions.schema import ProposalIn
from jarvis_live.actions.state import Context, build_payload
from jarvis_live.llm.client import LLM, LiteLLMClient
from jarvis_live.voice.schema import VoiceRequest
from jarvis_live.voice.service import VOICE_SYSTEM, voice_user
from tests.evals.harness import due_matches
from tests.evals.voice_cases import REPOS, TODAY, VOICE_CASES, Step, VoiceCase

RUNS = 3
MIN_SHARE = 0.9
MAX_TITLE_WORDS = 12  # the prompt says under 10; a title of 11 is not what fails a spoken request
MAX_CRITERIA = 6
BUDGET_S = 2.5  # voice's target for the LLM part
PREFILL_3B_2070 = 3005.0  # tok/s, measured on jev-llm with the 3B: docs/plans/fast-tier.md
DECODE_3B_2070 = 132.0  # tok/s, same run, grammar on
_NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


def _kind(kind: str, required: Sequence[str], **fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"kind": {"const": kind}, **fields},
        "required": ["kind", *required],
    }


_TEXT: dict[str, Any] = {"type": "string"}
_SPOKEN_KINDS = [
    _kind("task.create", ["text"], text=_TEXT, due=_TEXT),
    _kind("reminder.schedule", ["title", "at"], title=_TEXT, at=_TEXT, body=_TEXT),
    _kind("git.pr_comment", ["repo", "pr", "body"], repo=_TEXT, pr={"type": "integer"}, body=_TEXT),
]


class _Kinds(VoiceRequest):
    """Each spoken kind with the fields build_payload needs, in place of one flat ProposalIn whose
    fields are all optional. It parses exactly as VoiceRequest does: only what is sent differs."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        schema["properties"]["proposals"]["items"] = {"anyOf": _SPOKEN_KINDS}
        schema.pop("$defs", None)
        return {**schema, "title": "VoiceRequest"}


_Kinds.__name__ = "VoiceRequest"


_REPO_RULE = (
    "- repo: for cloud, exactly one of the repositories listed, or null if it is not clear which."
)
_REPO_NULL = (
    "- repo: for cloud, exactly one of the repositories listed, only if the request names it or "
    "says what it is for. Otherwise null: never pick one for its place in the list."
)
assert _REPO_RULE in VOICE_SYSTEM


@dataclass(frozen=True)
class Variant:
    name: str
    schema: type[VoiceRequest] = VoiceRequest
    named_day: bool = False
    system: str = VOICE_SYSTEM


VARIANTS = {
    v.name: v
    for v in (
        Variant("today"),
        Variant("named-day", named_day=True),
        Variant("kinds", schema=_Kinds),
        Variant("named-day+kinds", schema=_Kinds, named_day=True),
        Variant(
            "named-day+kinds+repo",
            schema=_Kinds,
            named_day=True,
            system=VOICE_SYSTEM.replace(_REPO_RULE, _REPO_NULL),
        ),
    )
}


@dataclass
class Row:
    model: str
    run: int
    case: str
    ok: bool
    fails: list[str]
    variant: str = "today"
    tokens: list[int] = field(default_factory=list)  # one per attempt, as the caller waits for all
    prompts: list[int] = field(default_factory=list)
    finish: list[str] = field(default_factory=list)
    reasoning: list[int] = field(default_factory=list)  # characters
    prompt_rates: list[float] = field(default_factory=list)  # tok/s here, not on a card
    decode_rates: list[float] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    cases_met: int
    critical_ok: bool
    total: int
    runs: int
    uncut: bool

    @property
    def quality_ok(self) -> bool:
        return (
            self.cases_met == len(VOICE_CASES)
            and self.critical_ok
            and self.total >= MIN_SHARE * len(VOICE_CASES) * RUNS
            and self.uncut
        )


def _words(p: ProposalIn) -> str:
    return " ".join(x or "" for x in (p.text, p.title, p.body)).lower()


def _has(text: str, groups: Sequence[Sequence[str]]) -> bool:
    return all(any(k in text for k in group) for group in groups)


def _context(case: VoiceCase) -> Context:
    return Context(_NOW, "UTC", lambda _: False, frozenset(case.commentable))


def _check_step(step: Step, p: ProposalIn) -> list[str]:
    fails: list[str] = []
    if step.date is not None:
        at = datetime.fromisoformat(p.at or "")
        if at.date().isoformat() != step.date:
            fails.append(f"at {p.at!r}, expected {step.date}")
        elif step.hours is not None and not step.hours[0] <= at.hour <= step.hours[1]:
            fails.append(f"at {p.at!r}, expected hour {step.hours}")
    if step.due is not None and not due_matches(p.due, step.due):
        fails.append(f"due {p.due!r}, expected {step.due}")
    if step.pr is not None and p.pr != step.pr:
        fails.append(f"pr {p.pr}, expected {step.pr}")
    if step.repo is not None and p.repo != step.repo:
        fails.append(f"comment repo {p.repo!r}, expected {step.repo}")
    return fails


def _check_action(case: VoiceCase, reply: VoiceRequest) -> list[str]:
    ctx = _context(case)
    fails: list[str] = []
    admitted: list[ProposalIn] = []
    for p in reply.proposals:
        if build_payload(p, ctx) is None:
            fails.append(f"server would refuse {p.kind}")
        else:
            admitted.append(p)
    left = list(admitted)
    for step in case.steps:
        found = next(
            (p for p in left if p.kind == step.kind and _has(_words(p), step.keywords)), None
        )
        if found is None:
            fails.append(f"missing {step.kind} {step.keywords}")
            continue
        left.remove(found)
        fails.extend(_check_step(step, found))
    fails.extend(f"extra step {p.kind}: {_words(p).strip()[:60]!r}" for p in left)
    return fails


def _check_nothing_filed(case: VoiceCase, reply: VoiceRequest) -> list[str]:
    if reply.target in ("cloud", "pi"):
        return [f"filed a {reply.target} session"]
    if reply.target == "none":
        return []
    ctx = _context(case)
    return [f"filed {p.kind}" for p in reply.proposals if build_payload(p, ctx) is not None]


def _check_session(case: VoiceCase, reply: VoiceRequest) -> list[str]:
    fails: list[str] = []
    if not reply.title.strip() or not reply.task.strip():
        fails.append("no title or task")
    if len(reply.title.split()) > MAX_TITLE_WORDS:
        fails.append(f"title of {len(reply.title.split())} words")
    if not _has(f"{reply.title} {reply.task}".lower(), case.keywords):
        fails.append(f"missing {case.keywords} from the title and task")
    if case.target == "cloud":
        repo = reply.repo if reply.repo in REPOS else None  # as IdeasState stores it
        if repo != case.repo:
            fails.append(f"repo {reply.repo!r}, expected {case.repo!r}")
        if not 1 <= len(reply.acceptance_criteria) <= MAX_CRITERIA:
            fails.append(f"{len(reply.acceptance_criteria)} acceptance criteria")
        if not (reply.done_when or "").strip():
            fails.append("no done_when")
    return fails


def check(case: VoiceCase, reply: VoiceRequest) -> list[str]:
    """What is wrong with the reply, as the server would act on it. Empty means it passes."""
    if case.files_nothing:
        return _check_nothing_filed(case, reply)
    if reply.target != case.target:
        return [f"target {reply.target}, expected {case.target}"]
    if case.target == "none":
        return []
    return _check_action(case, reply) if case.target == "action" else _check_session(case, reply)


async def run_case(
    llm: LLM,
    case: VoiceCase,
    model: str,
    *,
    timeout_s: float,
    variant: Variant = VARIANTS["today"],
) -> VoiceRequest:
    user = voice_user(
        today=TODAY,
        repos=REPOS,
        commentable=case.commentable,
        known=list(case.known),
        said=case.said,
    )
    if variant.named_day:
        day = date.fromisoformat(TODAY).strftime("%A")
        user = user.replace(f"Today: {TODAY}", f"Today: {day} {TODAY}", 1)
    return await llm.complete_json(
        model=model,
        system=variant.system,
        user=user,
        schema=variant.schema,
        timeout_s=timeout_s,
        think=False,
    )


class Recorder(httpx.AsyncBaseTransport):
    """Passes each request through and keeps what the client throws away, one row per attempt."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.attempts: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        await response.aread()
        if response.status_code == 200:
            reply = json.loads(response.content)
            choice, usage = reply["choices"][0], reply.get("usage") or {}
            timings = reply.get("timings") or {}
            message = choice["message"]
            self.attempts.append(
                {
                    "prompt": usage.get("prompt_tokens"),
                    "tokens": usage.get("completion_tokens"),
                    "finish": choice.get("finish_reason"),
                    "reasoning": len(message.get("reasoning_content") or ""),
                    "content": message.get("content"),
                    "prompt_rate": timings.get("prompt_per_second"),
                    "decode_rate": timings.get("predicted_per_second"),
                }
            )
        return response


def _counts(rows: Sequence[Row]) -> tuple[dict[str, int], dict[str, int]]:
    passes = {c.name: 0 for c in VOICE_CASES}
    fails = {c.name: 0 for c in VOICE_CASES}
    for r in rows:
        (passes if r.ok else fails)[r.case] += 1
    return passes, fails


def verdict(rows: Sequence[Row]) -> Verdict:
    passes, fails = _counts(rows)
    met = sum(passes[c.name] >= 2 and passes[c.name] + fails[c.name] >= RUNS for c in VOICE_CASES)
    critical = [c.name for c in VOICE_CASES if c.critical]
    critical_ok = all(passes[n] >= RUNS and fails[n] == 0 for n in critical)
    uncut = not any("length" in r.finish for r in rows)
    return Verdict(met, critical_ok, sum(r.ok for r in rows), len(rows), uncut)


def can_still_pass(rows: Sequence[Row]) -> bool:
    _, fails = _counts(rows)
    allowed = (1 - MIN_SHARE) * len(VOICE_CASES) * RUNS
    return (
        all(fails[c.name] == 0 if c.critical else fails[c.name] < 2 for c in VOICE_CASES)
        and sum(fails.values()) <= allowed
        and not any("length" in r.finish for r in rows)
    )


def project(row: Row, *, prefill: float, decode: float) -> float:
    """Seconds this reply would take at these rates. A retry is time the caller waited."""
    return sum(row.prompts) / prefill + sum(row.tokens) / decode


def p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def card_rates(
    *, cpu_prefill: float, cpu_decode: float, control_prefill: float, control_decode: float
) -> tuple[float, float]:
    """The 3B's measured 2070 rates, scaled by how this model compares with the 3B on the same CPU.
    An estimate: a card is limited by different things from a CPU."""
    return (
        PREFILL_3B_2070 * cpu_prefill / control_prefill,
        DECODE_3B_2070 * cpu_decode / control_decode,
    )


def break_even(rows: Sequence[Row]) -> float:
    """The decode rate at which reply p95 lands on the budget, prefill scaling with it."""
    at_3b = p95([project(r, prefill=PREFILL_3B_2070, decode=DECODE_3B_2070) for r in rows])
    return DECODE_3B_2070 * at_3b / BUDGET_S


async def run_one(
    llm: LiteLLMClient, rec: Recorder, case: VoiceCase, args: argparse.Namespace
) -> Row:
    rec.attempts = []
    try:
        reply = await run_case(
            llm, case, args.model, timeout_s=args.timeout, variant=VARIANTS[args.variant]
        )
        fails = check(case, reply)
    except Exception as e:  # an unusable reply is a failed case, not a stopped run
        fails = [f"no valid reply: {type(e).__name__}"]
    done = [a for a in rec.attempts if a["tokens"] is not None]
    return Row(
        model=args.tag or args.model,
        variant=args.variant,
        run=-1,
        case=case.name,
        ok=not fails,
        fails=fails,
        tokens=[a["tokens"] for a in done],
        prompts=[a["prompt"] for a in done],
        finish=[a["finish"] or "" for a in done],
        reasoning=[a["reasoning"] for a in done],
        prompt_rates=[a["prompt_rate"] for a in done if a["prompt_rate"]],
        decode_rates=[a["decode_rate"] for a in done if a["decode_rate"]],
        replies=[a["content"] or "" for a in done],
    )


async def run(args: argparse.Namespace) -> int:
    rows: list[Row] = []
    rec = Recorder()
    async with httpx.AsyncClient(transport=rec) as http:
        llm = LiteLLMClient(args.base, http, timeout_s=args.timeout)
        with Path(args.out).open("a", encoding="utf-8") as out:
            for run_no in range(args.runs):
                for case in VOICE_CASES:
                    t0 = time.monotonic()
                    row = await run_one(llm, rec, case, args)
                    row.run = run_no
                    rows.append(row)
                    out.write(json.dumps(asdict(row)) + "\n")
                    out.flush()
                    print(
                        f"r{run_no} {'PASS' if row.ok else 'FAIL'} {case.name[:44]:44} "
                        f"{row.tokens} {time.monotonic() - t0:.0f}s {row.fails[:2]}",
                        flush=True,
                    )
                    if not args.keep_going and not can_still_pass(rows):
                        print(f"STOPPED, it cannot pass voice: {case.name}")
                        return 3
    return 0


def _rate(rows: Sequence[Row], name: str) -> float:
    values = [x for r in rows for x in getattr(r, name)]
    return statistics.median(values) if values else 0.0


def report(paths: Sequence[str], control: str | None) -> int:
    groups: dict[str, list[Row]] = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            row = Row(**json.loads(line))
            groups.setdefault(f"{row.model} {row.variant}", []).append(row)
    base = next((rows for name, rows in groups.items() if name.split()[0] == control), [])
    for model, rows in groups.items():
        v = verdict(rows)
        tokens = sorted(t for r in rows for t in r.tokens) or [0]
        prompts = sorted(t for r in rows for t in r.prompts) or [0]
        print(
            f"{model}: {'PASS' if v.quality_ok else 'fail'} {v.total}/{v.runs} runs, "
            f"{v.cases_met}/{len(VOICE_CASES)} cases, critical {v.critical_ok}, "
            f"retries {sum(max(0, len(r.tokens) - 1) for r in rows)}, cut {not v.uncut}, "
            f"reasoning chars {sum(sum(r.reasoning) for r in rows)}"
        )
        print(
            f"  tokens p50 {statistics.median(tokens)} p95 {p95([float(t) for t in tokens])} "
            f"max {tokens[-1]}, prompt p50 {statistics.median(prompts)}, CPU prefill "
            f"{_rate(rows, 'prompt_rates'):.0f} decode {_rate(rows, 'decode_rates'):.1f} tok/s, "
            f"decode needed for {BUDGET_S} s p95: {break_even(rows):.0f} tok/s"
        )
        if base:
            prefill, decode = card_rates(
                cpu_prefill=_rate(rows, "prompt_rates"),
                cpu_decode=_rate(rows, "decode_rates"),
                control_prefill=_rate(base, "prompt_rates"),
                control_decode=_rate(base, "decode_rates"),
            )
            seconds = p95([project(r, prefill=prefill, decode=decode) for r in rows])
            print(
                f"  2070, unproven: prefill {prefill:.0f} decode {decode:.0f} tok/s, "
                f"projected p95 {seconds:.2f} s, {'fits' if seconds <= BUDGET_S else 'misses'}"
            )
        passes, fails = _counts(rows)
        for c in VOICE_CASES:
            if fails[c.name]:
                seen = sorted({f for r in rows if r.case == c.name for f in r.fails})[:3]
                print(f"  {passes[c.name]}/{passes[c.name] + fails[c.name]} {c.name}: {seen}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--tag", default="")
    r.add_argument("--runs", type=int, default=RUNS)
    r.add_argument("--out", required=True)
    r.add_argument("--timeout", type=float, default=300.0)
    r.add_argument("--keep-going", action="store_true")
    r.add_argument("--variant", default="today", choices=list(VARIANTS))
    p = sub.add_parser("report")
    p.add_argument("paths", nargs="+")
    p.add_argument("--control", default=None)
    args = parser.parse_args()
    if args.cmd == "report":
        return report(args.paths, args.control)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
