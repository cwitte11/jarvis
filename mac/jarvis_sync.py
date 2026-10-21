#!/usr/bin/env python3
"""Mark 4 vault sync. Runs on the Mac every 5 minutes (launchd).

Sends the server what's open in the vault, and writes back what Jarvis has for the vault.
- #todo lines that are unchecked, from daily notes in the last 14 days, become Jarvis tasks.
- #tomorrow lines from the latest daily note (last 3 days) go in the morning brief as thoughts, not tasks.
- Company notes with a stale last-contact go in Monday's brief.
- Notes captured by voice land in 00-Inbox/Jarvis Inbox.md.
- Tasks marked Done in Jarvis get checked off in the daily note.

Only reads 01-Daily and the Companies folder. 06-Self and 07-Therapy never leave the Mac.
Standard library only, so it runs on the Mac's built-in Python.
"""

import json
import re
import ssl
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Carson Vault"
DAILY = VAULT / "01-Daily"
COMPANIES = VAULT / "02-Projects/Career Search/Companies"
INBOX = VAULT / "00-Inbox/Jarvis Inbox.md"
CONFIG = Path.home() / "jarvis/mark4.env"
LOG = Path.home() / "jarvis/logs/mark4-sync.log"

TODO_DAYS = 14
THOUGHT_DAYS = 3
STALE_DAYS = 14
SKIP_STATUS = ("rejected", "offer", "informational", "ghosted", "on hold")
TASK_LINE = re.compile(r"^(\s*)- \[ \] (.*)$")


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {msg}\n")


def config() -> dict:
    out = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"#(todo|tomorrow)\b", "", text)).strip()


def key_for(text: str) -> str:
    return clean(text).lower()


def note_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def scan_daily() -> tuple[list[dict], list[str]]:
    today = date.today()
    tasks, thoughts, seen = [], [], set()
    notes = sorted((p for p in DAILY.glob("*.md") if note_date(p)), key=lambda p: p.stem, reverse=True)
    latest_with_thoughts = None
    for path in notes:
        day = note_date(path)
        age = (today - day).days
        if age > TODO_DAYS:
            break
        for line in path.read_text(errors="ignore").splitlines():
            m = TASK_LINE.match(line)
            if not m:
                continue
            body = m.group(2)
            text = clean(body)
            if not text:
                continue
            if "#todo" in body and key_for(body) not in seen:
                seen.add(key_for(body))
                tasks.append({"key": key_for(body), "title": text, "date": day.isoformat()})
            elif "#tomorrow" in body and age <= THOUGHT_DAYS and latest_with_thoughts in (None, path):
                latest_with_thoughts = path
                thoughts.append(text)
    return tasks, thoughts


def frontmatter(path: Path) -> dict:
    lines = path.read_text(errors="ignore").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def scan_companies() -> list[dict]:
    out = []
    cutoff = date.today() - timedelta(days=STALE_DAYS)
    for path in sorted(COMPANIES.glob("*.md")):
        fm = frontmatter(path)
        status = fm.get("status", "")
        if not status or status.lower().startswith(SKIP_STATUS):
            continue
        try:
            last = date.fromisoformat(fm.get("last-contact", "")[:10])
        except ValueError:
            continue  # no date logged, Mark 2's Monday nudge reads these from the note text
        if last <= cutoff:
            out.append({"company": path.stem, "status": status, "last_contact": last.isoformat(),
                        "contact": fm.get("contact", "")})
    return out


def write_note(item: dict) -> None:
    at = datetime.fromisoformat(item["at"]) if item.get("at") else datetime.now()
    stamp = at.strftime("%A, %B %-d, %Y at %-I:%M %p")
    text = item["text"].replace("\n", " ")
    flag = " (Jarvis had an error, sort this one by hand)" if item.get("error") else ""
    existing = INBOX.read_text() if INBOX.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    with INBOX.open("a") as f:
        f.write(f"{sep}- {stamp} | note | {text}{flag}\n")


def check_off(key: str) -> bool:
    for path in sorted(DAILY.glob("*.md"), key=lambda p: p.stem, reverse=True):
        lines = path.read_text().splitlines(keepends=True)
        for i, line in enumerate(lines):
            m = TASK_LINE.match(line.rstrip("\n"))
            if m and key_for(m.group(2)) == key:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                path.write_text("".join(lines))
                return True
    return False


def _ssl() -> ssl.SSLContext:
    try:  # uv's Python doesn't always find the Mac's certificates, certifi (from Mark 2's packages) does
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def call(cfg: dict, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        cfg["JARVIS_URL"].rstrip("/") + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Jarvis-Key": cfg["JARVIS_KEY"]}, method="POST")
    with urllib.request.urlopen(req, timeout=20, context=_ssl()) as r:
        return json.loads(r.read())


def main() -> int:
    cfg = config()
    if not cfg.get("JARVIS_URL") or not cfg.get("JARVIS_KEY"):
        return 0  # not set up yet
    tasks, thoughts = scan_daily()
    result = call(cfg, "/sync", {"tasks": tasks, "thoughts": thoughts, "followups": scan_companies()})
    done = []
    for item in result.get("outbox", []):
        try:
            if item["kind"] == "note":
                write_note(item)
            elif item["kind"] == "vault_done" and not check_off(item["vault_key"]):
                log(f"couldn't find to check off: {item['vault_key']}")
            done.append(item["id"])
        except Exception as e:
            log(f"outbox item {item.get('id')} failed: {e}")
    if done:
        call(cfg, "/sync/ack", {"ids": done})
    if result.get("added") or result.get("closed") or done:
        log(f"added {result.get('added')}, closed {result.get('closed')}, wrote {len(done)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"sync failed: {e}")
        sys.exit(1)
