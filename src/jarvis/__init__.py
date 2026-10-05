"""Jarvis Mark 2: sort HEARD captures in the vault inbox and act on them."""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from . import actions, handled, inbox, nudges, sorter, vaultguard

# Captures before this were made under Mark 1 and are not backfilled. Pass --since to change that.
DEFAULT_SINCE = "2026-09-18"


def _act(item: sorter.Item, source: str) -> None:
    if item.type == "reminder":
        actions.create_reminder(item.title, item.details, item.when, source)
    elif item.type == "event":
        actions.create_event(item.title, item.details, item.when, item.end, source)
    actions.notify(item.say or item.title)


async def _handle(heard: inbox.Heard, dry_run: bool) -> list[str]:
    """Result lines for one HEARD line. Always returns at least one line."""
    if not heard.text:
        return [inbox.result_line(heard.raw_ts, "note", "Empty input", details="No speech was captured.")]
    try:
        sorted_ = await sorter.sort(heard.text, heard.at, heard.raw_ts, dry_run)
    except Exception as e:
        return [inbox.failed_line(heard.raw_ts, f"{type(e).__name__}: {e}")]
    items = sorted_.items
    for edit in sorted_.edits:
        print(f"    vault edit: {'created' if edit['new'] else 'changed'} {edit['file']}")
    print(f"    cost ${sorted_.cost:.3f}")
    if not dry_run:
        handled.log_cost("capture", sorted_.cost)

    lines = []
    for item in items:
        try:
            if not dry_run:
                _act(item, heard.raw_ts)
            lines.append(inbox.result_line(heard.raw_ts, item.type, item.title, item.when, item.details))
        except Exception as e:
            lines.append(inbox.failed_line(heard.raw_ts, str(e), item.title, item.when))
    return lines


async def run(path: Path, since: datetime, dry_run: bool) -> int:
    pending = inbox.find_pending(inbox.read_lines(path), since)
    print(f"[{datetime.now():%H:%M:%S}] {len(pending)} unresolved HEARD line(s) since {since:%Y-%m-%d}")
    failures = 0
    for heard in pending:
        print(f"- {heard.raw_ts}: {heard.text or '(empty)'}")
        lines = None if dry_run else handled.lookup(heard)
        if lines:
            print("    already handled earlier, restoring its result lines without redoing anything")
        else:
            lines = await _handle(heard, dry_run)
            if not dry_run and not any(" | failed | " in line for line in lines):
                handled.record(heard, lines)
        for line in lines:
            print(f"    {line}")
        failures += sum(" | failed | " in line for line in lines)
        if not dry_run and not inbox.write_results(path, heard, lines):
            print("    HEARD line changed while working, left alone", file=sys.stderr)
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inbox", type=Path, default=inbox.INBOX)
    parser.add_argument("--since", default=DEFAULT_SINCE, help="only handle captures on or after YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="ask Claude but change nothing")
    parser.add_argument("--nudges", action="store_true", help="run the daily follow-up check instead of sorting captures")
    parser.add_argument("--force", action="store_true", help="with --nudges: run even if it already ran today")
    parser.add_argument("--undo", metavar="ID", help="delete one item Jarvis created, by its logged ID (or 'last')")
    parser.add_argument("--undo-edits", metavar="STAMP", nargs="?", const="last", help="put back the vault files one capture changed (default: the latest)")
    args = parser.parse_args()
    inbox.allow_icloud_downloads()
    if args.undo:
        try:
            entry = actions.undo(args.undo)
        except (LookupError, RuntimeError) as e:
            sys.exit(f"undo failed: {e}")
        print(f"deleted {entry['kind']}: {entry['title']}")
        return
    if args.undo_edits:
        try:
            files = vaultguard.undo_edits(inbox.VAULT, args.undo_edits)
        except LookupError as e:
            sys.exit(f"undo failed: {e}")
        print("restored:", *files, sep="\n  ")
        return
    if args.nudges:
        sys.exit(asyncio.run(nudges.run(args.dry_run, args.force)))
    since = datetime.strptime(args.since, "%Y-%m-%d")
    sys.exit(asyncio.run(run(args.inbox, since, args.dry_run)))
