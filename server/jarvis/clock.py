"""Everything runs on Carson's time, Central."""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("JARVIS_TZ", "America/Chicago"))

# No pings outside these hours. The brief and anything due overnight wait for morning.
QUIET_START = int(os.environ.get("JARVIS_QUIET_START", "21"))  # 9 PM
QUIET_END = int(os.environ.get("JARVIS_QUIET_END", "8"))       # 8 AM
NUDGE_HOURS = float(os.environ.get("JARVIS_NUDGE_HOURS", "3"))
BRIEF_BACKUP_HOUR = int(os.environ.get("JARVIS_BRIEF_BACKUP_HOUR", "10"))
TOMORROW_HOUR = 9


def now() -> datetime:
    """Local time without tzinfo, so it compares cleanly with stored times."""
    return datetime.now(TZ).replace(tzinfo=None, second=0, microsecond=0)


def is_quiet(t: datetime | None = None) -> bool:
    h = (t or now()).hour
    return h >= QUIET_START or h < QUIET_END


def tomorrow_morning(t: datetime | None = None) -> datetime:
    t = t or now()
    return (t + timedelta(days=1)).replace(hour=TOMORROW_HOUR, minute=0)


def default_due(t: datetime | None = None) -> datetime:
    """No time given: 6 PM today if it's before 5 PM, otherwise 9 AM tomorrow."""
    t = t or now()
    if t.hour < 17:
        return t.replace(hour=18, minute=0)
    return tomorrow_morning(t)


def spoken(dt: datetime) -> str:
    today = now().date()
    if dt.date() == today:
        day = "today"
    elif dt.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = dt.strftime("%A %b %-d")
    return f"{day} at {dt.strftime('%-I:%M %p').replace(':00', '')}"
