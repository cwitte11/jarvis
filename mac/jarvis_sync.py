#!/usr/bin/env python3
"""Mark 4 vault sync. Runs on the Mac every 5 minutes (launchd).

Two jobs:
- Voice notes captured by Jarvis land in 00-Inbox/Jarvis Inbox.md.
- Company notes with a stale last-contact go to the server for Monday's brief.

Daily notes aren't used anymore (10/21). Jarvis's own list replaced them.
Only reads the Companies folder and writes Jarvis Inbox. 06-Self and 07-Therapy never leave the Mac.
Standard library only, so it runs without extra installs.
"""

import json
import ssl
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

VAULT = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Carson Vault"
COMPANIES = VAULT / "02-Projects/Career Search/Companies"
INBOX = VAULT / "00-Inbox/Jarvis Inbox.md"
CONFIG = Path.home() / "jarvis/mark4.env"
LOG = Path.home() / "jarvis/logs/mark4-sync.log"

STALE_DAYS = 14
SKIP_STATUS = ("rejected", "offer", "informational", "ghosted", "on hold")


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
    flag = f" (Jarvis had an error, sort this one by hand: {item['error'][:120]})" if item.get("error") else ""
    existing = INBOX.read_text() if INBOX.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    with INBOX.open("a") as f:
        f.write(f"{sep}- {stamp} | note | {text}{flag}\n")


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
    result = call(cfg, "/sync", {"tasks": [], "thoughts": [], "followups": scan_companies()})
    done = []
    for item in result.get("outbox", []):
        try:
            if item["kind"] == "note":
                write_note(item)
            done.append(item["id"])
        except Exception as e:
            log(f"outbox item {item.get('id')} failed: {e}")
    if done:
        call(cfg, "/sync/ack", {"ids": done})
    if done:
        log(f"wrote {len(done)} note(s) to Jarvis Inbox")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"sync failed: {e}")
        sys.exit(1)
