"""Weekly nudge for follow-ups he owes after an interview.

Runs on Mondays, after 8 AM, on whichever launchd trigger fires first. Claude reads the
company notes and decides whether a follow-up is owed, because `last-contact` is blank on most
notes and the real dates are in the text. Each nudge becomes a Reminder with the reason and a draft.
Nothing is ever sent.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from . import actions, handled, inbox

STATE = Path(__file__).resolve().parents[2] / "state.json"
COMPANIES = "02-Projects/Career Search/Companies"
START_HOUR = 8
NUDGE_WEEKDAYS = (0,)  # Monday. For anything time-sensitive he dictates "nudge me ... by <time>" instead.
COOLDOWN_DAYS = 7  # don't nudge about the same company again within this window
MAX_NUDGES = 3
MAX_ATTEMPTS_PER_DAY = 3
MAX_USD = 0.60
# Networking-only and finished threads never get nudged, so they are not even read.
SKIP_STATUS = ("rejected", "offer", "informational", "ghosted")

SCHEMA = {
    "type": "object",
    "properties": {
        "nudges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "contact": {"type": "string"},
                    "reason": {"type": "string"},
                    "draft": {"type": "string"},
                },
                "required": ["company", "contact", "reason", "draft"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["nudges"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You are Jarvis, Carson's assistant, running once a week in the background. Nobody can answer a question from you.
Decide whether he owes anyone a follow-up after an interview. You only read. You do not edit anything.

The vault is your working directory and its CLAUDE.md is loaded. Follow it, including its tone rules and what it says about outreach.
The pipeline table in the prompt comes from each company note's frontmatter. `last-contact` is blank on most notes, so read the notes of the companies in the table and find the real dates in the text. Use Read, Grep and Glob.

Nudge only for:
- A thank-you or follow-up email owed after an interview or a formal conversation about a role, when the note shows it happened within the last 10 days and doesn't record that he sent one.
- Something he promised to send (a CV, materials, a reply) that the note doesn't show as sent.
Do not nudge for networking, relationship upkeep, circling back with someone who went quiet, or an application with no response.
Skip anything in the recently nudged list.

Return at most {MAX_NUDGES} nudges, the most time-sensitive first. Returning none is normal and correct. Don't pad.
Each nudge: the company, the specific person to write to, one sentence of reason that names the dates, and a draft message.
The draft is short, under 90 words, plain and specific to something real in the note, and it earns the next conversation rather than being it. No em dashes. No filler like "just checking in". Never claim Carson did something the note doesn't say he did. Put anything he still has to supply in [square brackets]."""


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    data = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def pipeline() -> list[str]:
    """One line per active company note, from frontmatter."""
    rows = []
    for path in sorted((inbox.VAULT / COMPANIES).glob("*.md")):
        fm = _frontmatter(path)
        status = fm.get("status", "")
        if status.lower().startswith(SKIP_STATUS):
            continue
        rows.append(
            f"- {path.stem} | status: {status or '(blank)'} | contact: {fm.get('contact') or '(blank)'} "
            f"| last-contact: {fm.get('last-contact') or '(blank)'} | file: {COMPANIES}/{path.name}"
        )
    return rows


def _load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _reminder_time(now: datetime) -> datetime:
    nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now < nine:
        return nine
    later = now + timedelta(minutes=30)
    return later.replace(minute=later.minute - later.minute % 5, second=0, microsecond=0)


async def _ask(prompt: str):
    tools = ["Read", "Grep", "Glob"]
    options = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_PROMPT},
        model=os.environ.get("JARVIS_MODEL", "claude-sonnet-5"),
        tools=tools,
        allowed_tools=tools,
        setting_sources=["project"],
        cwd=inbox.VAULT,
        max_turns=30,
        max_budget_usd=MAX_USD,
        output_format={"type": "json_schema", "schema": SCHEMA},
    )
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error or not result.structured_output:
        detail = (result.errors or result.result) if result else "no result"
        raise RuntimeError(f"Claude failed: {detail}")
    return result.structured_output, result.total_cost_usd or 0.0


async def run(dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now()
    today = date.today().isoformat()
    state = _load_state()
    if not force:
        if now.weekday() not in NUDGE_WEEKDAYS:
            print(f"[{now:%H:%M:%S}] nudges: not a nudge day")
            return 0
        if now.hour < START_HOUR:
            print(f"[{now:%H:%M:%S}] nudges: before {START_HOUR}:00, nothing to do")
            return 0
        if state.get("last_nudge_date") == today:
            print(f"[{now:%H:%M:%S}] nudges: already ran today")
            return 0
        if state.get("attempts", {}).get(today, 0) >= MAX_ATTEMPTS_PER_DAY:
            print(f"[{now:%H:%M:%S}] nudges: {MAX_ATTEMPTS_PER_DAY} failed attempts today, giving up until tomorrow")
            return 1
    if not dry_run:
        state["attempts"] = {today: state.get("attempts", {}).get(today, 0) + 1}
        _save_state(state)

    recent = {
        company: when
        for company, when in state.get("nudged", {}).items()
        if date.fromisoformat(when) > date.today() - timedelta(days=COOLDOWN_DAYS)
    }
    recent_text = "\n".join(f"- {c} (nudged {w})" for c, w in recent.items()) or "(none)"
    prompt = (
        f"Today is {now:%A, %B %-d, %Y}.\n\nPipeline:\n" + "\n".join(pipeline())
        + f"\n\nRecently nudged, skip these:\n{recent_text}"
    )
    rows = pipeline()
    if not rows:
        print(f"[{now:%H:%M:%S}] nudges: no companies in an active interview or application stage, skipping Claude")
        if not dry_run:
            state["last_nudge_date"] = today
            _save_state(state)
        return 0
    print(f"[{now:%H:%M:%S}] nudges: asking Claude about {len(rows)} companies")
    output, cost = await _ask(prompt)
    print(f"cost ${cost:.3f}")
    if not dry_run:
        handled.log_cost("nudges", cost)

    nudges = [n for n in output["nudges"] if n["company"] not in recent][:MAX_NUDGES]
    for n in nudges:
        print(f"\nFOLLOW UP: {n['contact']} ({n['company']})\n  why: {n['reason']}\n  draft: {n['draft']}")
        if dry_run:
            continue
        actions.create_reminder(
            f"Follow up: {n['contact']} ({n['company']})",
            f"{n['reason']}\n\nDraft:\n{n['draft']}",
            _reminder_time(now),
            source=f"nudge:{n['company']}",
        )
        state.setdefault("nudged", {})[n["company"]] = today
    if not nudges:
        print("\nNo follow-ups due today.")

    if not dry_run:
        state["last_nudge_date"] = today
        _save_state(state)
        if nudges:
            actions.notify(f"{len(nudges)} follow-up(s) due: " + ", ".join(n["company"] for n in nudges))
    return 0
