"""Ask Claude, through the Agent SDK, to sort one HEARD capture into actions.

Claude works inside the vault with the vault's CLAUDE.md loaded, so it finds notes itself with
Glob and Grep. New folders and notes need no setup here.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from . import cal, inbox, vaultguard

MODEL = os.environ.get("JARVIS_MODEL", "claude-sonnet-5")
MAX_USD_PER_CAPTURE = 0.75
WHEN_FORMAT = "%Y-%m-%d %H:%M"
TYPES = ("reminder", "event", "note", "question")

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "title": {"type": "string"},
        "when": {"type": "string"},
        "end": {"type": "string"},
        "details": {"type": "string"},
        "say": {"type": "string"},
    },
    "required": ["type", "title", "when", "end", "details", "say"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": ITEM_SCHEMA, "minItems": 1}},
    "required": ["items"],
    "additionalProperties": False,
}
FIRST_PASS_SCHEMA = {
    "type": "object",
    "properties": {**SCHEMA["properties"], "needs_vault": {"type": "boolean"}},
    "required": ["items", "needs_vault"],
    "additionalProperties": False,
}

RULES = """You are Jarvis, Carson's assistant. He speaks a capture into his phone and it reaches you as text.
You run unattended in the background, so nobody can answer a question from you. Sort what he said into actions.

Return one item per separate thing he said. If he said two things, return two items.

Item rules:
- type is one of: reminder, event, note, question.
- reminder: something he needs to do. title is short and starts with a verb. when is when to remind him. If he gave no time: today at 18:00 if it's before 17:00 when he spoke, otherwise tomorrow at 09:00.
- When he asks to be nudged or reminded to do something "by" a time ("nudge me to finish this by 5"), the reminder goes off at that time. title is the thing to do, like "Finish the SIE chapter".
- event: something at a set time, like a call, interview, or plans. when is the start, end is the end. No end given means one hour after the start. It goes on his personal calendar.
- note: an idea, a thought, a fact to keep, or a change you made to the vault. title is a short label. details is what he said (fixing only obvious dictation mistakes) or what you changed.
- question: he wants something answered or looked up. details is the answer in 2 or 3 short sentences.
- when and end are local time written exactly as YYYY-MM-DD HH:MM in 24-hour time. Work out relative times ("in two hours", "tomorrow morning", "Friday") from when he spoke.
- say is one short sentence for a notification. For reminders and events, repeat the title and time so he can catch mistakes. For questions, say is the same answer as details.
- Any field that doesn't apply is "".

Calendar:
- His upcoming events on his personal calendar are in the prompt.
- If a new event overlaps one of them, still create it, and say so in say and details."""

FIRST_PASS = RULES + """

You have no access to his notes in this pass. Set needs_vault to true only when the capture needs them:
- he asks something that his own notes would answer, or
- he wants something recorded or changed in his notes ("I applied to X", "add to ...", "note that ...", "log ...").
A reminder or event that merely names a person or company does not need the vault. When in doubt, false."""

VAULT_PASS = RULES + """

The vault is your working directory and its CLAUDE.md is loaded. Follow it exactly: its tone rules, its conventions, and its therapy rule.
- Look things up when it helps: names, companies, projects, questions about his own notes. Use Glob and Grep, read what you need, and don't crawl the vault. Answer questions from the vault first.
- Edit only when the capture clearly calls for it, such as "I applied to X", "note that Y", or a new fact about a person or company. Make small additive changes: append a line, or update a frontmatter field the vault says to keep current. Never delete, rename, reformat, or restructure anything. Never touch .obsidian.
- Dictation is often garbled ("Phantom time bolt" for "Phantom Time vault"). Use the vault to work out what he meant. If you can't tell which note or fact he means, don't edit. Return a note item and leave the words in the inbox.
- Whenever you change the vault, return a note item whose details say which file and what changed.
- You may read anywhere in the vault, including the personal folders, when a capture needs it. A dictated capture is not a source for the therapy record, so never write into 07-Therapy."""


@dataclass
class Item:
    type: str
    title: str
    when: datetime | None
    end: datetime | None
    details: str
    say: str


@dataclass
class Sorted:
    items: list[Item]
    edits: list[dict] = field(default_factory=list)
    cost: float = 0.0


def _parse_when(value: str) -> datetime | None:
    return datetime.strptime(value.strip(), WHEN_FORMAT) if value.strip() else None


def _to_item(raw: dict) -> Item:
    item = Item(
        type=raw["type"],
        title=raw["title"].strip(),
        when=_parse_when(raw["when"]),
        end=_parse_when(raw["end"]),
        details=raw["details"].strip(),
        say=raw["say"].strip(),
    )
    if item.type in ("reminder", "event") and item.when is None:
        raise ValueError(f"{item.type} without a time: {raw!r}")
    if item.type == "event" and item.end is None:
        item.end = item.when + timedelta(hours=1)
    return item


CALENDAR_DAYS = 7  # every extra day of calendar is more tokens on every capture


def _calendar_text(heard_at: datetime) -> str:
    try:
        return cal.upcoming(heard_at, days=CALENDAR_DAYS)
    except Exception as e:  # a calendar problem should not stop a capture from being sorted
        return f"(calendar unavailable: {e})"


async def _ask(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error:
        detail = (result.errors or result.result) if result else "no result"
        raise RuntimeError(f"Claude failed: {detail}")
    if not result.structured_output:
        raise RuntimeError(f"Claude returned no structured output: {result.result!r}")
    return result


async def sort(text: str, heard_at: datetime, source: str, dry_run: bool = False) -> Sorted:
    """Two passes. The first is cheap and has no vault. Only captures that need his notes get the second."""
    prompt = (
        f"When he spoke: {heard_at.strftime('%A, %B %-d, %Y at %-I:%M %p')}, Central time.\n\n"
        f"His personal calendar from that day, next {CALENDAR_DAYS} days:\n{_calendar_text(heard_at)}\n\n"
        f'He said: "{text}"'
    )
    first = await _ask(
        prompt,
        ClaudeAgentOptions(
            system_prompt=FIRST_PASS,
            model=MODEL,
            tools=[],
            setting_sources=[],
            max_turns=5,
            max_budget_usd=MAX_USD_PER_CAPTURE,
            output_format={"type": "json_schema", "schema": FIRST_PASS_SCHEMA},
            cwd=inbox.ROOT,
        ),
    )
    cost = first.total_cost_usd or 0.0
    if not first.structured_output["needs_vault"]:
        return Sorted([_to_item(raw) for raw in first.structured_output["items"]], [], cost)

    tools = ["Read", "Grep", "Glob"] + ([] if dry_run else ["Edit", "Write"])
    edits: list[dict] = []
    second = await _ask(
        prompt,
        ClaudeAgentOptions(
            system_prompt={"type": "preset", "preset": "claude_code", "append": VAULT_PASS},
            model=MODEL,
            tools=tools,
            allowed_tools=tools,
            permission_mode="acceptEdits",
            setting_sources=["project"],
            cwd=inbox.VAULT,
            max_turns=25,
            max_budget_usd=MAX_USD_PER_CAPTURE,
            output_format={"type": "json_schema", "schema": SCHEMA},
            hooks=vaultguard.hooks_for(inbox.VAULT, source, edits),
        ),
    )
    cost += second.total_cost_usd or 0.0
    return Sorted([_to_item(raw) for raw in second.structured_output["items"]], edits, cost)
