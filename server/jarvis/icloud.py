"""iCloud Calendar over CalDAV, using an app-specific password.

He has three iCloud calendars named "Home", so the one to use is pinned by URL (ICLOUD_CALENDAR_URL).
/admin/calendars lists them so the right one can be picked once.
Jarvis only reads events and adds new ones. It never edits or deletes.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from .clock import TZ

CALDAV_URL = "https://caldav.icloud.com"


def configured() -> bool:
    return bool(os.environ.get("ICLOUD_USER") and os.environ.get("ICLOUD_APP_PASSWORD"))


def _client():
    import caldav
    return caldav.DAVClient(url=CALDAV_URL, username=os.environ["ICLOUD_USER"],
                            password=os.environ["ICLOUD_APP_PASSWORD"])


def list_calendars() -> list[dict]:
    principal = _client().principal()
    return [{"name": c.name, "url": str(c.url)} for c in principal.calendars()]


def _calendar():
    url = os.environ.get("ICLOUD_CALENDAR_URL")
    if not url:
        raise RuntimeError("ICLOUD_CALENDAR_URL not set. Pick one from /admin/calendars.")
    return _client().calendar(url=url)


def _local(dt) -> tuple[datetime, bool]:
    """CalDAV gives dates for all-day events and aware datetimes otherwise."""
    if not isinstance(dt, datetime):
        return datetime(dt.year, dt.month, dt.day), True
    if dt.tzinfo:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt, False


def events(start: datetime, days: int = 1) -> list[dict]:
    cal = _calendar()
    found = cal.search(start=start.replace(hour=0, minute=0, tzinfo=TZ),
                       end=(start + timedelta(days=days)).replace(hour=0, minute=0, tzinfo=TZ),
                       event=True, expand=True)
    out = []
    for ev in found:
        comp = ev.icalendar_component
        begin, all_day = _local(comp.get("dtstart").dt)
        end_raw = comp.get("dtend")
        end = _local(end_raw.dt)[0] if end_raw else begin + timedelta(hours=1)
        out.append({"title": str(comp.get("summary", "(no title)")), "start": begin, "end": end,
                    "all_day": all_day})
    out.sort(key=lambda e: e["start"])
    return out


def describe(evs: list[dict]) -> str:
    lines = []
    for e in evs:
        if e["all_day"]:
            lines.append(f"{e['start']:%a %b %-d} all day: {e['title']}")
        else:
            lines.append(f"{e['start']:%a %b %-d}, {e['start']:%-I:%M %p} to {e['end']:%-I:%M %p}: {e['title']}")
    return "\n".join(lines) or "(nothing scheduled)"


def add_event(title: str, start: datetime, end: datetime, notes: str = "") -> str:
    def stamp(dt: datetime) -> str:
        return dt.replace(tzinfo=TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    uid = f"jarvis-{uuid.uuid4()}"
    desc = (notes or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
    summary = title.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jarvis//Mark 4//EN",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp(datetime.now(TZ).replace(tzinfo=None))}",
        f"DTSTART:{stamp(start)}", f"DTEND:{stamp(end)}", f"SUMMARY:{summary}",
        f"DESCRIPTION:{desc}" if desc else "DESCRIPTION:Added by Jarvis",
        "END:VEVENT", "END:VCALENDAR", "",
    ])
    _calendar().save_event(ics)
    return uid
