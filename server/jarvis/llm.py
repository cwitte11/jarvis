"""Claude does the writing: titles, times, answers, suggestions, and the morning brief.

Haiku for quick reads of a capture, Sonnet where the writing matters. Every call's cost is logged
so the monthly bill can be checked at /admin/costs.
"""

import json
import os
from datetime import datetime

import anthropic

from . import db
from .clock import now

FAST = os.environ.get("JARVIS_FAST_MODEL", "claude-haiku-5-5")
SMART = os.environ.get("JARVIS_SMART_MODEL", "claude-sonnet-5-5")

# USD per million tokens (input, output), from Anthropic's pricing page 2026-10-19.
# Haiku 5.5 rates are for prompts under 100K tokens, which every Jarvis call is.
PRICES = {FAST: (0.10, 0.50), SMART: (2.00, 10.00)}

WHO = ("You are Jarvis, Carson's personal assistant. He's a 2026 Pitt grad (economics and statistics), "
       "former D1 swimmer, job searching in finance and corporate development, based in Minnesota. "
       "Write the way he talks: plain words, short sentences, casual and direct. Never use em dashes. "
       "No corporate filler. Don't make things up.")

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _client


def _record_cost(model: str, usage) -> None:
    rate_in, rate_out = PRICES.get(model, (0, 0))
    usd = usage.input_tokens / 1e6 * rate_in + usage.output_tokens / 1e6 * rate_out
    month = now().strftime("%Y-%m")
    costs = db.get_meta("costs", {})
    entry = costs.setdefault(month, {"usd": 0.0, "calls": 0})
    entry["usd"] = round(entry["usd"] + usd, 6)
    entry["calls"] += 1
    db.set_meta("costs", costs)


def _call(model: str, system: str, prompt: str, schema: dict, max_tokens: int = 600) -> dict:
    """Asks for JSON that matches the schema (structured outputs), so the reply is always the shape we asked for.

    Forced tool use isn't supported on the newest models, so this uses output_config instead.
    """
    msg = client().messages.create(
        model=model, max_tokens=max_tokens, system=f"{WHO}\n\n{system}",
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    _record_cost(model, msg.usage)
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped early: {msg.stop_reason}")
    text = next((b.text for b in msg.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("Claude gave no reply")
    return json.loads(text)


def _when_line(t: datetime) -> str:
    return f"Right now it is {t.strftime('%A, %B %-d, %Y at %-I:%M %p')}, Central time."


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


S = {"type": "string"}


def classify(text: str) -> str:
    """Backup when Jev is down or unsure."""
    from .jev import KINDS
    out = _call(FAST, "Sort what he said into exactly one kind.\n" +
                "\n".join(f"- {k}: {v}" for k, v in KINDS.items()),
                f'He said: "{text}"', _obj({"kind": {"type": "string", "enum": list(KINDS)}}), 100)
    return out["kind"]


def read_task(text: str) -> dict:
    return _call(FAST, (
        "He told you about something he needs to do. Pull out:\n"
        "- title: short, starts with a verb, his words.\n"
        "- due: when to remind him, as YYYY-MM-DD HH:MM in 24-hour local time. Work out relative times "
        "('tonight', 'Friday', 'in 2 hours') from right now. If he gave no time at all, leave it empty.\n"
        "- notes: anything else he said that matters, or empty."),
        f'{_when_line(now())}\nHe said: "{text}"', _obj({"title": S, "due": S, "notes": S}), 300)


def read_event(text: str, calendar: str) -> dict:
    return _call(FAST, (
        "He told you about something at a set time that goes on his calendar. Pull out:\n"
        "- title: short.\n"
        "- start and end: YYYY-MM-DD HH:MM, 24-hour local time. No end given means one hour after start.\n"
        "- notes: anything else that matters, or empty.\n"
        "- clash: if it overlaps something already on his calendar, name it in a few words. Otherwise empty."),
        f"{_when_line(now())}\nHis calendar for the coming week:\n{calendar}\n\nHe said: \"{text}\"",
        _obj({"title": S, "start": S, "end": S, "notes": S, "clash": S}), 300)


def match_done(text: str, tasks: list) -> dict:
    listing = "\n".join(f"{t['id']}: {t['title']}" for t in tasks) or "(list is empty)"
    return _call(FAST, (
        "He says he finished something, or doesn't need it anymore. Find it on his open list.\n"
        "- task_id: the id of the matching task, or 0 if nothing on the list clearly matches.\n"
        "- dropped: true if he means he's not doing it (never mind, skip it), false if he did it."),
        f"His open list:\n{listing}\n\nHe said: \"{text}\"",
        _obj({"task_id": {"type": "integer"}, "dropped": {"type": "boolean"}}), 100)


def answer(text: str) -> str:
    out = _call(SMART, (
        "He asked you something out loud. Answer in 2 or 3 short sentences meant to be heard, not read. "
        "If it depends on facts that could have changed recently or you aren't sure, say so plainly "
        "instead of guessing."),
        f'{_when_line(now())}\nHe asked: "{text}"', _obj({"answer": S}), 400)
    return out["answer"]


def suggest(task, age_days: int) -> str:
    out = _call(SMART, (
        "This has been on his list a while and he keeps pushing it to tomorrow. Don't just remind him again. "
        "Suggest one concrete move in one or two short sentences. Pick whichever fits best: knock it out now "
        "if it's quick, a smaller first step that takes under 10 minutes, or drop it if it clearly isn't "
        "going to happen. No guilt trip."),
        f"Task: {task['title']}\nNotes: {task['notes'] or 'none'}\nOn the list for {age_days} days, "
        f"pushed to tomorrow {task['pushes']} times.",
        _obj({"suggestion": S}), 200)
    return out["suggestion"]


def brief(context: str) -> dict:
    return _call(SMART, (
        "Write his morning brief. He just woke up. This is how he finds out what's on his plate without "
        "opening anything.\n"
        "- say: what Jarvis speaks out loud. 3 to 5 short sentences, no lists. The most important thing first. "
        "If something has been sitting a long time or keeps getting pushed, call it out.\n"
        "- text: the written version for the notification. Short lines, no markdown headers. Sections in "
        "this order, skipping any that are empty: Today (calendar), Still open (oldest first, with how long "
        "it's been sitting), Follow-ups. Leave the fact and skill read out of it, they're sent separately.\n"
        "- fact: one interesting, true, checkable fact. Not about him. Rotate topics: science, history, "
        "sports, markets, nature, tech. Don't repeat recent ones.\n"
        "- skill: a 2-minute read that builds a skill for finance or corporate development work: one idea, "
        "explained plainly with a quick example, 4 to 6 sentences. Don't repeat recent ones."),
        f"{_when_line(now())}\n\n{context}", _obj({"say": S, "text": S, "fact": S, "skill": S}), 1500)
