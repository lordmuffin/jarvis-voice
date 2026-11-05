"""The owner's today, as every LLM caller names it: voice, the ask and the copilot."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def named_today(day: date) -> str:
    """Today as the model is told it, weekday first: given only the date, every model R2 ran
    counted "Friday" from a Wednesday wrong (docs/plans/fast-tier.md)."""
    return f"{day:%A} {day.isoformat()}"


def owners_day(now: datetime, timezone: str) -> date:
    """Today's date on the owner's calendar: an evening request in US time is not tomorrow."""
    return now.astimezone(ZoneInfo(timezone)).date()


def owners_today(now: datetime, timezone: str) -> str:
    return named_today(owners_day(now, timezone))


def named_week(now: datetime, timezone: str) -> str:
    """The seven days after today on the owner's calendar, each by name: given Wednesday, Gemma
    still counted "Friday" as the 7th (docs/plans/fast-tier.md). Counted in dates, not hours, so
    a 23-hour day is not skipped."""
    today = owners_day(now, timezone)
    return ", ".join(named_today(today + timedelta(days=n)) for n in range(1, 8))


__all__ = ["named_today", "named_week", "owners_day", "owners_today"]
