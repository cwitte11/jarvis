"""One spoken sentence in, one action out. Jev sorts it, Claude fills in the details."""

import time
from datetime import datetime, timedelta

from . import db, icloud, jev, llm
from .clock import default_due, now, spoken

JEV_SURE = 0.35  # below this, Claude double-checks Jev's pick
WHEN_FORMAT = "%Y-%m-%d %H:%M"


def _parse(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), WHEN_FORMAT) if value and value.strip() else None
    except ValueError:
        return None


def _kind(text: str) -> tuple[str, float | None]:
    try:
        kind, confidence = jev.classify(text)
        if confidence >= JEV_SURE:
            return kind, confidence
    except jev.JevError:
        confidence = None
    return llm.classify(text), confidence


def _task(text: str) -> str:
    info = llm.read_task(text)
    due = _parse(info["due"]) or default_due()
    if due < now() - timedelta(minutes=5):  # a time that already passed today means the next one
        due += timedelta(days=1)
    db.add_task(info["title"], due, info.get("notes", ""))
    return f"Got it. {info['title']}, {spoken(due)}."


def _event(text: str) -> str:
    if not icloud.configured():
        return _task(text) + " Your calendar isn't hooked up yet, so I saved it as a reminder."
    try:
        calendar = icloud.describe(icloud.events(now(), days=7))
    except Exception as e:  # a calendar read problem shouldn't lose the capture
        calendar = f"(calendar unavailable: {e})"
    info = llm.read_event(text, calendar)
    start = _parse(info["start"])
    if start is None:
        return _task(text)
    end = _parse(info["end"]) or start + timedelta(hours=1)
    try:
        icloud.add_event(info["title"], start, end, info.get("notes", ""))
    except Exception:
        db.add_task(info["title"], start, info.get("notes", ""))
        return f"Couldn't reach your calendar, so I set a reminder instead. {info['title']}, {spoken(start)}."
    clash = f" Heads up, that overlaps {info['clash']}." if info.get("clash") else ""
    return f"Added {info['title']}, {spoken(start)}.{clash}"


def _done(text: str) -> str:
    tasks = db.open_tasks()
    pick = llm.match_done(text, tasks)
    task = next((t for t in tasks if t["id"] == pick["task_id"]), None)
    if task is None:
        db.queue("note", {"text": text, "at": db.iso(now())})
        return "Couldn't tell which one you meant, so I saved it to your inbox."
    db.close_task(task["id"], "dropped" if pick["dropped"] else "done")
    return f"Dropped {task['title']}." if pick["dropped"] else f"Nice. Crossed off {task['title']}."


def _note(text: str) -> str:
    db.queue("note", {"text": text, "at": db.iso(now())})
    return "Saved to your inbox."


HANDLERS = {"task": _task, "event": _event, "done": _done, "question": llm.answer, "note": _note}


def handle(text: str) -> dict:
    started = time.monotonic()
    text = (text or "").strip()
    if not text:
        db.log_capture("", "empty", None, "nothing heard", 0)
        return {"kind": "empty", "say": "Didn't catch that."}
    kind, confidence = _kind(text)
    try:
        say = HANDLERS.get(kind, _note)(text)
    except Exception as e:
        # Never lose his words. Park them for the Mac to drop in the inbox.
        db.queue("note", {"text": text, "at": db.iso(now()), "error": str(e)[:200]})
        say = "Something broke on my end. I saved what you said to your inbox."
        kind = f"{kind}-failed"
    ms = int((time.monotonic() - started) * 1000)
    db.log_capture(text, kind, confidence, say, ms)
    return {"kind": kind, "say": say, "ms": ms}
