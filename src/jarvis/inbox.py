"""Reading and writing Jarvis Inbox.md.

Every capture is a HEARD line written by the phone. Jarvis answers it with one or
more result lines placed directly under it, so a HEARD line is unresolved exactly
when the line after it is not a result line. A `failed` result does not count as
resolved, so those get retried.
"""

import ctypes
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAULT = Path(os.environ.get("JARVIS_VAULT") or Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Carson Vault")
INBOX = VAULT / "00-Inbox/Jarvis Inbox.md"

TS_FORMAT = "%A, %B %d, %Y at %I:%M %p"
_TS = r"(?P<ts>[A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M)"
HEARD_RE = re.compile(rf"^- {_TS}\s*\| HEARD \|\s?(?P<text>.*)$")
RESULT_RE = re.compile(rf"^- {_TS}\s*\|\s*(?P<type>[^|]*?)\s*\|")
# A failed capture is retried, but not sooner than this. Writing the failed line touches the
# watched file, so without a cooldown a Claude outage would retrigger the job in a tight loop.
RETRY_AFTER = timedelta(minutes=15)
TRIED_FORMAT = "%Y-%m-%dT%H:%M"
_TRIED_RE = re.compile(r"\[tried (\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\]")


@dataclass
class Heard:
    line: str
    raw_ts: str
    at: datetime
    text: str
    occurrence: int = 0  # how many identical HEARD lines come before this one


def parse_heard(line: str) -> Heard | None:
    m = HEARD_RE.match(line)
    if not m:
        return None
    try:
        at = datetime.strptime(m["ts"], TS_FORMAT)
    except ValueError:
        return None
    return Heard(line, m["ts"], at, m["text"].strip())


def _block_end(lines: list[str], i: int) -> int:
    """Index just past the result lines that follow the HEARD line at i."""
    j = i + 1
    while j < len(lines) and lines[j].startswith("- ") and not HEARD_RE.match(lines[j]):
        j += 1
    return j


def _is_resolved(lines: list[str], i: int) -> bool:
    for line in lines[i + 1 : _block_end(lines, i)]:
        m = RESULT_RE.match(line)
        if m and m["type"] != "failed":
            return True
    return False


def _recently_failed(lines: list[str], i: int) -> bool:
    for line in lines[i + 1 : _block_end(lines, i)]:
        m = RESULT_RE.match(line)
        tried = _TRIED_RE.search(line)
        if m and m["type"] == "failed" and tried:
            if datetime.now() - datetime.strptime(tried[1], TRIED_FORMAT) < RETRY_AFTER:
                return True
    return False


def find_pending(lines: list[str], since: datetime) -> list[Heard]:
    pending = []
    seen: dict[str, int] = {}
    for i, line in enumerate(lines):
        h = parse_heard(line)
        if not h:
            continue
        h.occurrence = seen.get(line, 0)
        seen[line] = h.occurrence + 1
        if h.at >= since and not _is_resolved(lines, i) and not _recently_failed(lines, i):
            pending.append(h)
    return pending


def _clean(value: str) -> str:
    return " ".join(value.replace("|", "/").split())


def format_when(dt: datetime | None) -> str:
    return dt.strftime("%B %-d, %Y at %-I:%M %p") if dt else ""


def result_line(raw_ts: str, kind: str, title: str = "", when: datetime | None = None, details: str = "") -> str:
    return f"- {raw_ts} | {kind} | {_clean(title)} | {format_when(when)} | {_clean(details)}"


def failed_line(raw_ts: str, error: str, title: str = "", when: datetime | None = None) -> str:
    tried = datetime.now().strftime(TRIED_FORMAT)
    return result_line(raw_ts, "failed", title, when, f"{error} [tried {tried}]")


def allow_icloud_downloads() -> None:
    """Let this process download cloud-only iCloud files.

    Background launchd jobs are denied by default, so reading an evicted Jarvis Inbox.md
    fails with EDEADLK ("Resource deadlock avoided"). Constants are from sys/resource.h:
    IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES, IOPOL_SCOPE_PROCESS, IOPOL_MATERIALIZE_DATALESS_FILES_ON.
    """
    ctypes.CDLL(None, use_errno=True).setiopolicy_np(3, 0, 2)


def read_lines(path: Path = INBOX) -> list[str]:
    return path.read_text(encoding="utf-8").split("\n")


def write_results(path: Path, heard: Heard, results: list[str]) -> bool:
    """Put result lines right under the first still-unresolved copy of this HEARD line.

    Re-reads the file each time so lines the phone appended since the run started
    are kept. Returns False if the HEARD line is no longer there.
    """
    lines = read_lines(path)
    for i, line in enumerate(lines):
        if line == heard.line and not _is_resolved(lines, i):
            end = _block_end(lines, i)
            lines[i + 1 : end] = results
            _atomic_write(path, "\n".join(lines))
            return True
    return False


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".jarvis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
