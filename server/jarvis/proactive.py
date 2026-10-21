"""The part that doesn't wait to be asked: morning brief, nudges, suggestions, and button taps."""

from datetime import datetime, timedelta

from . import db, icloud, llm, notify
from .clock import BRIEF_BACKUP_HOUR, NUDGE_HOURS, is_quiet, now, spoken, tomorrow_morning

SUGGEST_AFTER_PUSHES = 3
RECENT_KEEP = 21  # facts and skill reads remembered so they don't repeat


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _age_days(task) -> int:
    return max(0, (now() - _dt(task["created"])).days)


# ---- button taps and the tasks page ----

def act(task_id: int, verb: str) -> str:
    task = db.get_task(task_id)
    if task is None:
        return "That one's gone."
    if task["status"] != "open":
        return f"Already {task['status']}: {task['title']}."
    if verb == "done":
        db.close_task(task_id, "done")
        return f"Done: {task['title']}."
    if verb == "drop":
        db.close_task(task_id, "dropped")
        return f"Dropped: {task['title']}."
    if verb == "tomorrow":
        due = tomorrow_morning()
        db.update_task(task_id, due=db.iso(due), pushes=task["pushes"] + 1, pings=0, last_ping=None)
        return f"Moved to {spoken(due)}: {task['title']}."
    return "Not sure what that button was."


# ---- morning brief ----

def _brief_context() -> str:
    parts = []
    if icloud.configured():
        try:
            parts.append("Calendar today:\n" + icloud.describe(icloud.events(now(), days=1)))
        except Exception as e:
            parts.append(f"Calendar today: couldn't load it ({e}).")
    else:
        parts.append("Calendar today: not connected yet.")

    tasks = db.open_tasks()
    if tasks:
        lines = []
        for t in tasks:
            bits = [f"on the list {_age_days(t)} days"]
            if t["pushes"]:
                bits.append(f"pushed {t['pushes']}x")
            if t["due"]:
                due = _dt(t["due"])
                bits.append(("overdue since " if due < now() else "due ") + spoken(due))
            if t["source"] == "vault":
                bits.append("from his daily notes")
            lines.append(f"- {t['title']} ({', '.join(bits)})")
        parts.append("Still open:\n" + "\n".join(lines))
    else:
        parts.append("Still open: nothing.")

    thoughts = db.get_meta("thoughts", [])
    if thoughts:
        parts.append("Thoughts he left himself for today in his daily note (not tasks, just pass them along):\n"
                     + "\n".join(f"- {x}" for x in thoughts))

    followups = db.get_meta("followups", []) if now().weekday() == 0 else []  # Mondays only
    if followups:
        parts.append("Job search follow-ups that look stale (from his company notes):\n" + "\n".join(
            f"- {f['company']}: status {f.get('status') or '?'}, last contact {f.get('last_contact') or 'never logged'}"
            + (f", contact {f['contact']}" if f.get("contact") else "") for f in followups))

    last_sync = db.get_meta("last_sync")
    if last_sync and now() - _dt(last_sync) > timedelta(days=1):
        parts.append(f"Note: his Mac last synced the vault {last_sync}, so vault items may be out of date. "
                     "Mention it briefly.")

    recent = db.get_meta("recent", {"facts": [], "skills": []})
    parts.append("Recent facts, don't repeat: " + " | ".join(recent["facts"][-RECENT_KEEP:]))
    parts.append("Recent skill reads, don't repeat: " + " | ".join(recent["skills"][-RECENT_KEEP:]))
    return "\n\n".join(parts)


def brief(trigger: str = "wake", force: bool = False) -> dict:
    today = now().date().isoformat()
    if not force and db.get_meta("brief_date") == today:
        return {"say": db.get_meta("brief_say", ""), "repeat": True}

    out = llm.brief(_brief_context())
    notify.send(out["text"] + f"\n\nFact: {out['fact']}", title="Morning brief", tags=["sunrise"])
    notify.send(out["skill"], title="2-minute read", priority=2, tags=["books"])

    recent = db.get_meta("recent", {"facts": [], "skills": []})
    recent["facts"] = (recent["facts"] + [out["fact"][:120]])[-RECENT_KEEP:]
    recent["skills"] = (recent["skills"] + [out["skill"][:80]])[-RECENT_KEEP:]
    db.set_meta("recent", recent)
    db.set_meta("brief_date", today)
    db.set_meta("brief_say", out["say"])
    db.set_meta("brief_trigger", trigger)

    # Everything due was just covered, so the first nudge comes a few hours later, not right now.
    for t in db.open_tasks():
        if t["due"] and _dt(t["due"]) <= now():
            db.update_task(t["id"], last_ping=db.iso(now()))
    return {"say": out["say"], "repeat": False}


# ---- the loop ----

def _nudge(task) -> None:
    suggestion = task["suggestion"]
    if task["pushes"] >= SUGGEST_AFTER_PUSHES and not suggestion:
        try:
            suggestion = llm.suggest(task, _age_days(task))
            db.update_task(task["id"], suggestion=suggestion)
        except Exception:
            suggestion = None
    lines = [task["title"]]
    age = _age_days(task)
    if age >= 2:
        lines.append(f"On your list {age} days" + (f", pushed {task['pushes']}x." if task["pushes"] else "."))
    if suggestion:
        lines.append(suggestion)
    notify.send("\n".join(lines), title="Jarvis", actions=notify.task_buttons(task["id"]),
                priority=4 if task["pushes"] >= SUGGEST_AFTER_PUSHES else 3)
    db.update_task(task["id"], last_ping=db.iso(now()), pings=task["pings"] + 1)


def tick() -> list[str]:
    """Runs every few minutes. Returns what it did, for the log."""
    did = []
    t = now()
    if is_quiet(t):
        return did
    today = t.date().isoformat()
    if db.get_meta("brief_date") != today:
        if t.hour >= BRIEF_BACKUP_HOUR:
            failed = _dt(db.get_meta("brief_failed"))
            if failed and t - failed < timedelta(minutes=30):
                return did
            try:
                brief(trigger="backup")
                did.append("brief (backup)")
            except Exception as e:
                db.set_meta("brief_failed", db.iso(t))
                did.append(f"brief failed: {e}")
                return did
        else:
            return did  # hold nudges until the morning brief has gone out
    for task in db.open_tasks():
        due, last = _dt(task["due"]), _dt(task["last_ping"])
        if due is None or due > t:
            continue
        if last is None or t - last >= timedelta(hours=NUDGE_HOURS):
            _nudge(task)
            did.append(f"nudge {task['id']}")
    return did


# ---- vault sync from the Mac ----

def sync(vault_tasks: list[dict], followups: list[dict], thoughts: list[str] | None = None) -> dict:
    """The Mac sends what's unchecked in the daily notes. Anything Jarvis has that's no longer
    there got checked off in Obsidian, so it closes here too."""
    seen = set()
    with db.session() as c:
        existing = {r["vault_key"]: r for r in c.execute("SELECT * FROM tasks WHERE source='vault'")}
    added = closed = 0
    for vt in vault_tasks:
        key = vt["key"]
        seen.add(key)
        if key in existing:
            continue
        note_day = datetime.fromisoformat(vt["date"]) if vt.get("date") else now()
        task_id = db.add_task(vt["title"], tomorrow_morning(note_day), source="vault", vault_key=key)
        db.update_task(task_id, created=db.iso(note_day))
        added += 1
    for key, row in existing.items():
        if row["status"] == "open" and key not in seen:
            db.update_task(row["id"], status="done", closed=db.iso(now()))
            closed += 1
    db.set_meta("followups", followups)
    db.set_meta("thoughts", thoughts or [])
    db.set_meta("last_sync", db.iso(now()))
    return {"added": added, "closed": closed, "outbox": db.pending_outbox()}
