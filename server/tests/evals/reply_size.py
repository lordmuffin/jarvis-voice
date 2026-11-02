"""The bar for step 4 of docs/plans/fast-tier.md, measured on a local model before any pod exists.
One model and one reply variant, the 17 cases, three runs each, through the real client. Judge a CPU
run on quality and tokens: its times mean nothing for a card.

    uv run python -m tests.evals.reply_size run --base http://127.0.0.1:18080/v1 --model NAME \\
        --variant slim --out /somewhere/outside/the/repo/rows.jsonl
    uv run python -m tests.evals.reply_size report rows.jsonl

Rows hold whole replies, so keep them out of the repo. A candidate stops as soon as a bar case can
no longer reach 2 of 3. --keep-going runs a variant to the end, to measure it anyway."""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from jarvis_live.config import Settings
from jarvis_live.llm.client import LiteLLMClient
from tests.evals.harness import check, run_case
from tests.evals.transcripts import CASES, ROLLING_REPLAY, SMALL_MODEL_CASES, Case
from tests.evals.variants import VARIANTS, Variant

RUNS = 3
TOKENS_2070 = 225  # 2.5 s on the 2070, at 132 tok/s after 0.8 s of prefill
ALL_CASES: list[Case] = [*CASES, *SMALL_MODEL_CASES, *ROLLING_REPLAY]
BAR_CASES = frozenset(
    {  # the 11 Flash passes 3 of 3
        "1:1 status meeting",
        "vendor call, two owned actions",
        "solo brain dump",
        "named speakers, who said what",
        "small talk, nothing committed",
        "state carried, one new action",
        "correction updates the item in place",
        "rolling: Order the spare batteries",
        "rolling: Send Tomas the Lakeside unit count",
        "rolling: Book two agency temps for the returns backlog",
        "rolling: Ask Rosa's manager whether Rosa can review the Spanish card",
    }
)


@dataclass
class Row:
    variant: str
    run: int
    case: str
    ok: bool
    fails: list[str]
    tokens: list[int]
    model: str = ""
    actions: int | None = None
    prompts: list[int] = field(default_factory=list)
    finish: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    bar_cases_met: int
    quality_ok: bool
    p95: int
    size_ok: bool
    total: int

    @property
    def passed(self) -> bool:
        return self.quality_ok and self.size_ok


def reply_p95(rows: list[Row]) -> int:
    """Every attempt is a reply: a retry is a reply the caller waited for."""
    tokens = sorted(t for r in rows for t in r.tokens)
    return tokens[min(len(tokens) - 1, int(0.95 * len(tokens)))] if tokens else 0


def _bar_counts(rows: list[Row]) -> tuple[dict[str, int], dict[str, int]]:
    passes: dict[str, int] = defaultdict(int)
    fails: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.case in BAR_CASES:
            (passes if r.ok else fails)[r.case] += 1
    return passes, fails


def verdict(rows: list[Row], budget: int = TOKENS_2070) -> Verdict:
    passes, fails = _bar_counts(rows)
    met = sum(passes[c] >= 2 and passes[c] + fails[c] >= RUNS for c in BAR_CASES)
    p95 = reply_p95(rows)
    return Verdict(met, met == len(BAR_CASES), p95, p95 <= budget, sum(r.ok for r in rows))


def can_still_pass(rows: list[Row]) -> bool:
    _, fails = _bar_counts(rows)
    return all(n <= RUNS - 2 for n in fails.values())


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
            self.attempts.append(
                {
                    "tokens": usage.get("completion_tokens"),
                    "prompt": usage.get("prompt_tokens"),
                    "finish": choice.get("finish_reason"),
                    "content": choice["message"].get("content"),
                }
            )
        return response


async def run_one(llm: LiteLLMClient, rec: Recorder, case: Case, cfg: Settings, v: Variant) -> Row:
    rec.attempts = []
    actions: int | None = None
    try:
        delta, state, seconds = await run_case(llm, case, cfg, v)
        fails = [f for f in check(case, delta, state, seconds) if "round-trip" not in f]
        actions = len(state.snapshot().actions)
    except Exception as e:  # an unusable reply is a failed case, not a stopped run
        fails = [f"no valid reply: {type(e).__name__}"]
    return Row(
        variant=v.name,
        run=-1,
        case=case.name,
        ok=not fails,
        fails=fails,
        tokens=[a["tokens"] for a in rec.attempts if a["tokens"] is not None],
        actions=actions,
        prompts=[a["prompt"] for a in rec.attempts if a["prompt"] is not None],
        finish=[a["finish"] or "" for a in rec.attempts],
        replies=[a["content"] or "" for a in rec.attempts],
    )


async def run(args: argparse.Namespace) -> int:
    variant = VARIANTS[args.variant]
    cfg = Settings(copilot_model=args.model, copilot_timeout_s=args.timeout)
    rows: list[Row] = []
    rec = Recorder()
    async with httpx.AsyncClient(transport=rec) as http:
        llm = LiteLLMClient(args.base, http, timeout_s=args.timeout)
        with Path(args.out).open("a", encoding="utf-8") as out:
            for run_no in range(args.runs):
                for case in ALL_CASES:
                    t0 = time.monotonic()
                    row = await run_one(llm, rec, case, cfg, variant)
                    row.run, row.model = run_no, args.tag or args.model
                    rows.append(row)
                    out.write(json.dumps(asdict(row)) + "\n")
                    out.flush()
                    state = "PASS" if row.ok else "FAIL"
                    print(
                        f"{args.variant} r{run_no} {state} {case.name[:44]:44} "
                        f"{row.tokens} {time.monotonic() - t0:.0f}s {row.fails[:2]}",
                        flush=True,
                    )
                    if not args.keep_going and not can_still_pass(rows):
                        print(f"STOPPED, a bar case cannot reach 2 of {RUNS}: {case.name}")
                        return 3
    return 0


def _replies(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        parsed = json.loads(row["replies"][-1])
    except (IndexError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def report(paths: list[str], budget: int) -> int:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            groups[(r["model"], r["variant"])].append(r)
    keys = ("actions_upsert", "notes_upsert", "decisions_upsert", "suggestions", "topics")
    print("model variant runs pass bar p50 p95 max <=budget retries cut | items per reply", keys)
    for (model, name), raw in groups.items():
        rows = [Row(**r) for r in raw]
        v = verdict(rows, budget)
        tokens = sorted(t for r in rows for t in r.tokens) or [0]
        parsed = [p for r in raw if (p := _replies(r)) is not None]
        items = [statistics.mean(len(p.get(k) or []) for p in parsed or [{}]) for k in keys]
        print(
            model,
            name,
            len({r.run for r in rows}),
            f"{v.total}/{len(rows)}",
            f"{v.bar_cases_met}/11",
            statistics.median(tokens),
            v.p95,
            tokens[-1],
            f"{sum(t <= budget for t in tokens)}/{len(tokens)}",
            sum(len(r.tokens) - 1 for r in rows if r.tokens),
            sum("length" in r.finish for r in rows),
            "|",
            " ".join(f"{x:.1f}" for x in items),
            "PASS" if v.passed else "fail",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--tag", default="")
    r.add_argument("--variant", default="today", choices=list(VARIANTS))
    r.add_argument("--runs", type=int, default=RUNS)
    r.add_argument("--out", required=True)
    r.add_argument("--timeout", type=float, default=300.0)
    r.add_argument("--keep-going", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("paths", nargs="+")
    p.add_argument("--budget", type=int, default=TOKENS_2070)
    args = parser.parse_args()
    if args.cmd == "report":
        return report(args.paths, args.budget)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
