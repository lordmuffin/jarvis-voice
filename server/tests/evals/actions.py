"""The action-proposal precision eval: replay transcripts through the copilot's real prompt, keep
only what the owner would be shown (grounded, validated, capped), and count how many are ones
they would accept. Reported, never enforced.

Two kinds of case. Synthetic ones carry their own answer key (``asks``): a proposal that matches
an ask is accepted, and in an ``exhaustive`` case any other proposal is a false positive; these
are labelled ``auto``. Recorded sessions (``jarvis-live export-transcript``) live in a private,
git-ignored directory with the owner's own labels: ``human``. A proposal nobody has labelled is
listed in ``unlabelled.jsonl`` to be filled in, and counted in neither precision."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_live.actions.report import PrecisionReport, Status, Verdict, label_key, summarize
from jarvis_live.actions.state import Context, ProposalState, ground
from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import COPILOT_SYSTEM, copilot_user
from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.copilot.state import CopilotState
from jarvis_live.copilot.transcript import Line
from jarvis_live.llm.client import LLM
from tests.evals.transcripts import CASES, Expected

HERE = Path(__file__).parent
PRIVATE_DIR = Path(os.environ.get("JARVIS_LIVE_EVAL_ACTIONS_DIR") or HERE / "private")
REPORT_PATH = HERE.parent.parent / ".eval-actions-report.json"
_LINE = re.compile(r"^\[(\d+):(\d{2})\]\s+(me|them):\s*(.*)$")
_VERDICTS = ("accept", "accept_edited", "reject")
LINE_MS = 6000  # a spoken line is taken to last this long, or until the next one


@dataclass(frozen=True)
class ProposalCase:
    name: str
    transcript: str
    asks: tuple[Expected, ...] = ()  # what was really asked for out loud (keywords, as Expected)
    exhaustive: bool | None = None  # asks lists every real ask; default: it does if there are any

    @property
    def closed(self) -> bool:
        return bool(self.asks) if self.exhaustive is None else self.exhaustive


EXPLICIT_ASKS = ProposalCase(
    "explicit asks",
    """\
[00:03] me: Okay, quick brain dump. Remind me tomorrow at nine to send the invoice to Dana.
[00:12] me: Also add a task to call the vendor about the quote.
[00:21] me: I keep thinking I should redo the garden beds at some point, no idea when.
[00:30] me: The quote was eleven thousand, which is roughly what I expected.
""",
    asks=(Expected(keywords=(("invoice",),)), Expected(keywords=(("vendor",), ("quote",)))),
)
TOPIC_ONLY = ProposalCase(
    "talk, no asks",
    """\
[00:04] them: How are you thinking about the tax situation this year?
[00:12] me: Honestly I'm not sure. The accountant mentioned the estimated payments might change.
[00:25] them: Do you need to file an extension?
[00:31] me: Possibly. We should look at that, and whether the vendor invoices land in this year.
[00:44] them: Makes sense. Let's talk again next month.
""",
    exhaustive=True,
)
SYNTHETIC = [
    EXPLICIT_ASKS,
    TOPIC_ONLY,
    # The copilot evals' own transcripts hold commitments, but nothing said to Jarvis.
    *(ProposalCase(c.name, c.transcript, exhaustive=True) for c in CASES),
]


def parse_transcript(text: str) -> list[Line]:
    """``[mm:ss] me|them: text`` lines, as the copilot prompt shows them."""
    found = [
        (int(m[1]) * 60_000 + int(m[2]) * 1000, m[3], m[4].strip())
        for raw in text.splitlines()
        if (m := _LINE.match(raw.strip()))
    ]
    lines: list[Line] = []
    for i, (start, speaker, body) in enumerate(found):
        nxt = found[i + 1][0] if i + 1 < len(found) else start + LINE_MS
        lines.append(Line(start, min(nxt, start + LINE_MS), speaker, body))
    return lines


def load_private_cases(directory: Path = PRIVATE_DIR) -> list[ProposalCase]:
    """Recorded sessions: ``<name>.txt`` transcripts, judged by their owner (no answer key)."""
    if not directory.is_dir():
        return []
    return [
        ProposalCase(p.stem, p.read_text(encoding="utf-8"), exhaustive=False)
        for p in sorted(directory.glob("*.txt"))
    ]


@dataclass(frozen=True)
class Shown:
    """A proposal as the owner would see it in the review queue."""

    kind: str
    payload: dict[str, Any]
    text: str
    quote: str

    @property
    def key(self) -> str:
        return label_key(self.kind, self.payload)


async def propose(llm: LLM, case: ProposalCase, cfg: Settings | None = None) -> list[Shown]:
    """One copilot cycle over the whole transcript, then what survives the server's checks."""
    cfg = cfg or Settings()
    user = copilot_user(
        today=datetime.now(UTC).date().isoformat(),
        title=None,
        state_json=CopilotState().prompt_json(),
        related="",
        transcript=case.transcript,
    )
    delta = await llm.complete_json(
        model=cfg.copilot_model,
        system=COPILOT_SYSTEM,
        user=user,
        schema=CopilotDelta,
        timeout_s=cfg.copilot_timeout_s,
    )
    ctx = Context(
        now=datetime.now(UTC), timezone=cfg.timezone, vault_has=lambda _: False, repos=frozenset()
    )
    grounded = ground(delta.proposals, parse_transcript(case.transcript))
    return [
        Shown(
            p.kind,
            p.payload,
            str(p.payload.get("text") or p.payload.get("title") or p.payload.get("body") or ""),
            p.evidence[0].text if p.evidence else "",
        )
        for p in ProposalState().apply(grounded, ctx)
    ]


@dataclass(frozen=True)
class Labels:
    """The owner's verdicts, by ``(case, key)``."""

    verdicts: dict[tuple[str, str], Verdict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Labels":
        out: dict[tuple[str, str], Verdict] = {}
        if path.is_file():
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if row.get("verdict") in _VERDICTS:  # a blank one is still to be judged
                    out[(row["case"], row["key"])] = row["verdict"]
        return cls(out)


@dataclass(frozen=True)
class Row:
    case: str
    kind: str
    text: str
    key: str
    quote: str
    verdict: Verdict | None
    source: str | None  # "human" | "auto" | None (unlabelled)


def _matches(ask: Expected, s: Shown) -> bool:
    text = s.text.lower()
    return all(any(k in text for k in group) for group in ask.keywords)


class ActionEvalReport:
    def __init__(self, labels: Labels) -> None:
        self.labels = labels
        self.rows: list[Row] = []

    def add(self, case: ProposalCase, s: Shown) -> None:
        verdict: Verdict | None = None
        source: str | None = None
        if (v := self.labels.verdicts.get((case.name, s.key))) is not None:
            verdict, source = v, "human"
        elif any(_matches(a, s) for a in case.asks):
            verdict, source = "accept", "auto"
        elif case.closed:
            verdict, source = "reject", "auto"
        self.rows.append(Row(case.name, s.kind, s.text, s.key, s.quote, verdict, source))

    def _precision(self, source: str) -> PrecisionReport:
        rows: list[tuple[str, Status]] = [
            (r.kind, r.verdict) for r in self.rows if r.source == source and r.verdict
        ]
        return summarize(rows)

    def summary(self) -> dict[str, Any]:
        return {
            "auto": self._precision("auto").to_dict(),
            "human": self._precision("human").to_dict(),
            "unlabelled": sum(r.verdict is None for r in self.rows),
        }

    def render(self) -> list[str]:
        out: list[str] = []
        for source, title in (("auto", "synthetic cases (answer key)"), ("human", "your labels")):
            out += [f"-- {title}", *self._precision(source).render()]
        todo = [r for r in self.rows if r.verdict is None]
        if todo:
            out.append(f"-- {len(todo)} proposal(s) need your label (unlabelled.jsonl)")
        out.append("-- every proposal")
        out += [f"  [{r.verdict or '?':<13}] {r.case}: {r.kind} {r.text!r}" for r in self.rows]
        return out

    def write(self, report_path: Path, todo_path: Path) -> None:
        report_path.write_text(json.dumps(self.summary(), indent=2) + "\n", encoding="utf-8")
        todo = [r for r in self.rows if r.verdict is None]
        if todo:
            todo_path.parent.mkdir(parents=True, exist_ok=True)
            todo_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "case": r.case,
                            "key": r.key,
                            "kind": r.kind,
                            "text": r.text,
                            "quote": r.quote,
                            "verdict": "",  # accept | accept_edited | reject
                        }
                    )
                    + "\n"
                    for r in todo
                ),
                encoding="utf-8",
            )


REPORT = ActionEvalReport(Labels.load(PRIVATE_DIR / "labels.jsonl"))
