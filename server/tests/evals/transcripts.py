"""Synthetic transcripts for the live evals, in the copilot's `[mm:ss] me|them: text` format."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from jarvis_live.copilot.schema import ActionUpsert, CopilotDelta


@dataclass(frozen=True)
class Expected:
    """One action the copilot must find: every group needs at least one keyword in the text."""

    keywords: tuple[tuple[str, ...], ...]
    owner: str | None = None  # substring the owner must contain; None = no owner was spoken
    due: str | None = None  # substring the due date must contain; None = not checked


@dataclass(frozen=True)
class Case:
    name: str
    transcript: str
    actions: tuple[Expected, ...] = ()
    expect_no_owner_anywhere: bool = False
    extra: dict[str, str] = field(default_factory=dict)
    seed: CopilotDelta | None = None  # what an earlier cycle left in the state
    max_actions: int | None = None  # in the state afterwards: more is a duplicate or an invention


STATUS_1ON1 = Case(
    name="1:1 status meeting",
    transcript="""\
[00:03] them: Morning. Quick status check before the review on Thursday. Where are we on the billing migration?
[00:11] me: Most of it is done. The invoices table is migrated and the reconciliation job passes on last month's data.
[00:24] them: Great. Any blockers?
[00:28] me: One. The staging deploys have been flaky, they fail about one in four runs, which slows down testing.
[00:41] them: Yeah, somebody should really dig into why the staging deploys are flaky. It's been going on for weeks.
[00:50] me: Agreed. I don't know the cause yet, could be the image pull timeouts.
[01:02] them: Okay. What about the customer export feature?
[01:07] me: That's on track. Design is approved and we start building next sprint.
[01:18] them: Good. And the on-call rotation, are you comfortable with the new schedule?
[01:25] me: It's fine. The handoff notes help a lot.
[01:33] them: Alright, we decided to keep the Thursday review as is, no changes to the agenda.
[01:41] me: Sounds good. That's all from me.
[01:45] them: Thanks, talk next week.
""",
    actions=(Expected(keywords=(("staging",), ("flaky", "deploy")), owner=None),),
    expect_no_owner_anywhere=True,
)

VENDOR_CALL = Case(
    name="vendor call, two owned actions",
    transcript="""\
[00:04] them: Thanks for making time. I'm Dana from Northwind, I look after the account side.
[00:10] me: Great to meet you. We're evaluating you for the log archiving contract.
[00:19] them: Understood. Pricing for your volume would be eleven thousand a year on the standard tier.
[00:30] me: That's within what we budgeted. What about the security review? Our team will need a questionnaire completed.
[00:42] them: We can do that. Dana, that's me, will send the revised quote with the SOC 2 report attached by Friday.
[00:55] me: Perfect. And on our side, Marcus will return the filled-in security questionnaire by Tuesday.
[01:06] them: Good. Once we have it, our security team turns it around in two days.
[01:15] me: Okay. Do you support single sign-on?
[01:20] them: Yes, SAML and OIDC on all tiers.
[01:28] me: Then I think we're aligned. We'll decide on the tier after the questionnaire is done.
[01:37] them: Sounds like a plan. Thanks, everyone.
""",
    actions=(
        Expected(keywords=(("quote",),), owner="dana"),
        Expected(keywords=(("questionnaire",),), owner="marcus"),
    ),
)

SOLO_BRAIN_DUMP = Case(
    name="solo brain dump",
    transcript="""\
[00:02] me: Okay, brain dump for the week. Let me just get everything out.
[00:10] me: The garden. The tomatoes need staking before the storm on Wednesday, otherwise they'll flatten.
[00:22] me: The passport is expiring in March so that needs renewing, probably should do that soon because processing takes weeks.
[00:36] me: Thinking about the blog post on home lab backups. The angle should be the restore test, not the backup itself, nobody tests restores.
[00:50] me: The dentist appointment still needs booking, I keep putting it off.
[01:02] me: Also wondering whether to move the NAS to the new rack or leave it. Leaning toward leaving it until summer.
[01:15] me: Oh, and the car insurance renewal comes up next month, should compare quotes.
[01:25] me: That's it. Good dump.
""",
    actions=(
        Expected(keywords=(("passport",),), owner=None),
        Expected(keywords=(("dentist",),), owner=None),
    ),
    expect_no_owner_anywhere=True,
)

CASES = (STATUS_1ON1, VENDOR_CALL, SOLO_BRAIN_DUMP)

# The rest are for choosing a small model, where the risk is judgement, not format: the long
# prompt, telling people apart, leaving what was only discussed, and using the state it is given.

_MEETING = json.loads((Path(__file__).parent / "fixtures" / "planning_meeting.json").read_text())


def line_at(line: str) -> int:
    return int(line[1:3]) * 60 + int(line[4:6])


def _planning(end_s: int) -> str:
    return "\n".join(x for x in _MEETING["lines"] if line_at(x) <= end_s) + "\n"


PLANNING_FIRST = Case(
    name="long meeting, first 5 minutes",
    transcript=_planning(300),
    actions=(
        Expected(keywords=(("batter",),), owner="tomas", due="thursday"),
        Expected(keywords=(("count", "unit", "lakeside"),), owner="priya"),
        Expected(keywords=(("survey", "signal", "meter", "wi-fi"),), owner=None),
    ),
)

PLANNING_LONG = Case(
    name="long meeting, 8 minutes",
    transcript=_planning(480),
    actions=(
        *PLANNING_FIRST.actions,
        Expected(keywords=(("temp", "agency"),), owner="marcus"),
        Expected(keywords=(("module", "price", "cost"),), owner=None),
    ),
)

NAMED_SPEAKERS = Case(
    name="named speakers, who said what",
    transcript="""\
[00:03] Priya: Before we start, the access review is overdue. I'll finish the access review by Friday.
[00:11] me: Thanks. The audit people asked about the backup logs as well.
[00:17] Speaker 2: I can pull the backup logs. I'll have them ready on Monday.
[00:24] Priya: Great. What's the state of the firewall rules?
[00:29] me: They're fine for now. I'd like to revisit them next quarter, nothing to do yet.
[00:37] Speaker 2: Agreed, leave them. Are we still using the old ticket queue?
[00:43] me: Only for a few vendors. Tom said he might tidy that up at some point, but he didn't commit.
""",
    actions=(
        Expected(keywords=(("access review",),), owner="priya", due="friday"),
        Expected(keywords=(("backup log",),), owner="speaker 2", due="monday"),
    ),
    max_actions=2,
)

NO_ACTIONS = Case(
    name="small talk, nothing committed",
    transcript="""\
[00:02] them: How was the long weekend? Did you get away in the end?
[00:07] me: We did, up to the lake. The weather held until the last day, then it rained sideways.
[00:16] them: That's the way it goes. We stayed home and the kids took over the garden.
[00:23] me: Sounds nice. Are they still into the football?
[00:27] them: Very much so, there's a tournament coming up, it's all anyone talks about.
[00:34] me: Ha, I remember those days. Anyway, good to see you, we should do lunch sometime.
[00:41] them: Yes, we should. Take care.
""",
    max_actions=0,
)

ROLLING_STATE = Case(
    name="state carried, one new action",
    transcript="""\
[05:02] me: Just to recap, Priya is fixing the staging image pull timeout.
[05:09] them: Yes, and Marcus is still on the questionnaire for the vendor.
[05:15] me: Good. One new thing, the release notes need writing. Helen, could you do those by Tuesday?
[05:24] them: Helen here, happy to, Tuesday is fine.
""",
    seed=CopilotDelta(
        actions_upsert=[
            ActionUpsert(text="Fix the staging image pull timeout", owner="Priya"),
            ActionUpsert(text="Return the security questionnaire", owner="Marcus"),
        ]
    ),
    actions=(
        Expected(keywords=(("staging",),), owner="priya"),
        Expected(keywords=(("release notes",),), owner="helen", due="tuesday"),
    ),
    max_actions=3,
)

MOVED_DEADLINE = Case(
    name="correction updates the item in place",
    transcript="""\
[02:10] them: About the deck, I know we said Thursday.
[02:15] me: Right, Sam is sending the deck on Thursday.
[02:20] them: Sorry, that's changed. The board moved, so Sam needs to send it by Wednesday, not Thursday.
[02:29] me: Understood, Wednesday it is for the deck.
""",
    seed=CopilotDelta(
        actions_upsert=[ActionUpsert(text="Send the deck", owner="Sam", due="Thursday")]
    ),
    actions=(Expected(keywords=(("deck",),), owner="sam", due="wednesday"),),
    max_actions=1,
)

SMALL_MODEL_CASES = [
    PLANNING_FIRST,
    PLANNING_LONG,
    NAMED_SPEAKERS,
    NO_ACTIONS,
    ROLLING_STATE,
    MOVED_DEADLINE,
]
