"""The owner's today, as every LLM caller names it: voice, the ask and the copilot."""

from datetime import date, datetime
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


__all__ = ["named_today", "owners_day", "owners_today"]
