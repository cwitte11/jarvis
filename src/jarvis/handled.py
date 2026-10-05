"""Jarvis's own record of what it did with each HEARD line, kept outside the vault.

The phone and the Mac both write Jarvis Inbox.md through iCloud, and a phone that has not
synced yet can write back an older copy, dropping the Mac's result lines. Without this
record the capture would look unhandled and get acted on twice. With it, Jarvis restores
the result lines instead of redoing the action.
"""

import json
from datetime import datetime
from pathlib import Path

from .inbox import Heard

HANDLED_LOG = Path(__file__).resolve().parents[2] / "handled.jsonl"
COSTS_LOG = HANDLED_LOG.with_name("costs.jsonl")


def lookup(heard: Heard) -> list[str] | None:
    """Result lines already produced for this HEARD line, or None if it was never handled.

    Identical HEARD lines (same minute, same words) pair up in order via heard.occurrence.
    """
    if not HANDLED_LOG.exists():
        return None
    records = [json.loads(line) for line in HANDLED_LOG.read_text(encoding="utf-8").splitlines() if line]
    matches = [r["results"] for r in records if r["heard"] == heard.line]
    return matches[heard.occurrence] if heard.occurrence < len(matches) else None


def record(heard: Heard, results: list[str]) -> None:
    with HANDLED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"heard": heard.line, "results": results}) + "\n")


def log_cost(kind: str, usd: float) -> None:
    """Track what each Claude call costs, so the monthly Agent SDK credit is not a surprise."""
    with COSTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "usd": round(usd, 4)}) + "\n")
