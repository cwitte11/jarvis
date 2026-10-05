"""Things Jarvis does on the Mac: Reminders, Calendar, notifications."""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from . import cal

ROOT = Path(__file__).resolve().parents[2]
# Everything Jarvis creates is logged here, outside the vault, so it can be undone by ID.
CREATED_LOG = ROOT / "created.jsonl"

_REMINDER = """
on makeDate(y, mo, d, secs)
    set dt to current date
    set day of dt to 1
    set year of dt to y
    set month of dt to mo
    set day of dt to d
    set time of dt to secs
    return dt
end makeDate

on run argv
    set dt to my makeDate((item 3 of argv) as integer, (item 4 of argv) as integer, (item 5 of argv) as integer, (item 6 of argv) as integer)
    tell application "Reminders"
        set r to make new reminder with properties {name:item 1 of argv, body:item 2 of argv, remind me date:dt}
        return id of r
    end tell
end run
"""

_DELETE_REMINDER = """
on run argv
    tell application "Reminders"
        set matches to (reminders whose id is item 1 of argv)
        if (count of matches) is not 1 then error "expected exactly one reminder, found " & (count of matches)
        delete item 1 of matches
    end tell
end run
"""

_NOTIFY = """
on run argv
    display notification (item 1 of argv) with title "Jarvis"
end run
"""


def _osascript(script: str, *args: str) -> str:
    proc = subprocess.run(["osascript", "-", *args], input=script, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"osascript failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _log(entry: dict) -> None:
    with CREATED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def create_reminder(title: str, notes: str, when: datetime, source: str = "") -> str:
    seconds = when.hour * 3600 + when.minute * 60
    item_id = _osascript(_REMINDER, title, notes, str(when.year), str(when.month), str(when.day), str(seconds))
    _log({"kind": "reminder", "id": item_id, "title": title, "source": source})
    return item_id


def create_event(title: str, notes: str, start: datetime, end: datetime, source: str = "") -> str:
    calendar, item_id = cal.create_event(title, notes, start, end)
    _log({"kind": "event", "id": item_id, "calendar": calendar, "title": title, "source": source})
    return item_id


def notify(message: str) -> None:
    _osascript(_NOTIFY, message)


def _created() -> list[dict]:
    """Logged creations that have not been undone, oldest first."""
    if not CREATED_LOG.exists():
        return []
    entries = [json.loads(line) for line in CREATED_LOG.read_text(encoding="utf-8").splitlines() if line]
    undone = {e["id"] for e in entries if e.get("undone")}
    return [e for e in entries if not e.get("undone") and e["id"] not in undone]


def undo(item_id: str) -> dict:
    """Delete one item Jarvis created, looked up by ID. IDs that are not in the log are refused."""
    if item_id == "last":
        entries = _created()
        if not entries:
            raise LookupError("nothing to undo")
        entry = entries[-1]
    else:
        entry = next((e for e in _created() if e["id"] == item_id), None)
        if entry is None:
            raise LookupError(f"{item_id} is not in {CREATED_LOG.name} or was already undone")
    if entry["kind"] == "reminder":
        _osascript(_DELETE_REMINDER, entry["id"])
    else:
        cal.delete_event(entry["id"])
    _log({"id": entry["id"], "undone": True})
    return entry
