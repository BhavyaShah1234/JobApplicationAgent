"""The agent's notion of "now": the user's local date and time (settings.timezone, America/Phoenix by default).

Timestamps that are only stored or compared stay in UTC; anything a model reasons about ("present", "latest",
"recent news", a posting's deadline) gets today's local date through `today_line()` in its system prompt.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobagent.config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now() -> datetime:
    return datetime.now(tz())


def today() -> date:
    return now().date()


def today_line() -> str:
    """e.g. 'Today is Tuesday, October 27, 2026, 8:34 PM MST (America/Phoenix).' for system prompts."""
    n = now()
    return (f"Today is {n:%A, %B} {n.day}, {n.year}, {n:%I:%M %p %Z}".replace(" 0", " ") +
            f" ({get_settings().timezone}). Judge 'present', 'current', 'latest' and 'recent' against this date.")


def with_today(system_prompt: str) -> str:
    return f"{system_prompt}\n\n{today_line()}"
