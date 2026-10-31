"""Synthetic transcripts for the live evals, in the copilot's `[mm:ss] me|them: text` format."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expected:
    """One action the copilot must find: every group needs at least one keyword in the text."""

    keywords: tuple[tuple[str, ...], ...]
    owner: str | None = None  # substring the owner must contain; None = no owner was spoken


@dataclass(frozen=True)
class Case:
    name: str
    transcript: str
    actions: tuple[Expected, ...] = ()
    expect_no_owner_anywhere: bool = False
    extra: dict[str, str] = field(default_factory=dict)


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
