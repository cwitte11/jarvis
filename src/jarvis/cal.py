"""Calendar access through EventKit, limited to the personal Home calendar.

There are three iCloud calendars named "Home", so the target is pinned by ID rather than name.
"""

import os
import threading
from datetime import datetime, timedelta

import EventKit as EK
from Foundation import NSDate

# Carson's personal, unshared "Home" calendar.
PERSONAL_CALENDAR_ID = os.environ.get("JARVIS_CALENDAR_ID", "A0F6B145-7A6A-469A-9DD3-D7C2D2DEC62A")
PERSONAL_LABEL = "Home (personal)"

_store = None


def _get_store():
    global _store
    if _store is None:
        store = EK.EKEventStore.alloc().init()
        done = threading.Event()
        outcome = {}

        def on_access(granted, error):
            outcome["granted"], outcome["error"] = granted, error
            done.set()

        store.requestFullAccessToEventsWithCompletion_(on_access)
        if not done.wait(120) or not outcome.get("granted"):
            raise PermissionError(f"Calendar access not granted: {outcome.get('error')}")
        _store = store
    return _store


def _ns(dt: datetime):
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _py(ns_date) -> datetime:
    return datetime.fromtimestamp(ns_date.timeIntervalSince1970())


def _label(cal) -> str:
    return PERSONAL_LABEL if cal.calendarIdentifier() == PERSONAL_CALENDAR_ID else cal.title()


def upcoming(start: datetime, days: int = 7, limit: int = 100) -> str:
    """Events on the personal Home calendar as plain lines, for the model to read."""
    store = _get_store()
    calendar = store.calendarWithIdentifier_(PERSONAL_CALENDAR_ID)
    if calendar is None:
        raise RuntimeError(f"personal calendar {PERSONAL_CALENDAR_ID} not found")
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        _ns(start.replace(hour=0, minute=0)), _ns(start + timedelta(days=days)), [calendar]
    )
    rows = []
    for ev in store.eventsMatchingPredicate_(predicate):
        begin, finish = _py(ev.startDate()), _py(ev.endDate())
        when = f"{begin:%a %b %-d} all day" if ev.isAllDay() else f"{begin:%a %b %-d}, {begin:%-I:%M %p} to {finish:%-I:%M %p}"
        rows.append((begin, f"{when}: {ev.title()}"))
    rows.sort(key=lambda r: r[0])
    return "\n".join(line for _, line in rows[:limit]) or "(nothing scheduled)"


def create_event(title: str, notes: str, start: datetime, end: datetime) -> tuple[str, str]:
    """Create an event on the personal Home calendar. Returns (calendar label, event ID)."""
    store = _get_store()
    calendar = store.calendarWithIdentifier_(PERSONAL_CALENDAR_ID)
    if calendar is None:
        raise RuntimeError(f"personal calendar {PERSONAL_CALENDAR_ID} not found")
    event = EK.EKEvent.eventWithEventStore_(store)
    event.setTitle_(title)
    event.setNotes_(notes or None)
    event.setStartDate_(_ns(start))
    event.setEndDate_(_ns(end))
    event.setCalendar_(calendar)
    ok, error = store.saveEvent_span_error_(event, EK.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"could not save event: {error}")
    return _label(calendar), event.eventIdentifier()


def delete_event(event_id: str) -> None:
    """Delete one event by ID, and only if it is on the personal calendar."""
    store = _get_store()
    event = store.eventWithIdentifier_(event_id)
    if event is None:
        raise LookupError(f"event {event_id} not found")
    if event.calendar().calendarIdentifier() != PERSONAL_CALENDAR_ID:
        raise RuntimeError("refusing to delete an event outside the personal calendar")
    ok, error = store.removeEvent_span_error_(event, EK.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"could not delete event: {error}")
