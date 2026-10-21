"""Jarvis's own memory: the task list, things waiting for the Mac, and a few settings.

One SQLite file on the server's disk. On Railway that disk is a volume mounted at /data.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .clock import now

DB_PATH = os.environ.get("JARVIS_DB", "/data/jarvis.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    due TEXT,                       -- ISO local time, NULL means no set time
    status TEXT NOT NULL DEFAULT 'open',   -- open, done, dropped
    pushes INTEGER NOT NULL DEFAULT 0,     -- times he hit Tomorrow
    pings INTEGER NOT NULL DEFAULT 0,      -- times Jarvis nudged since the last push
    last_ping TEXT,
    suggestion TEXT,
    source TEXT NOT NULL DEFAULT 'voice',  -- voice or vault
    vault_key TEXT UNIQUE,                 -- daily-note path + task text, for vault tasks
    created TEXT NOT NULL,
    closed TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- note, vault_done
    payload TEXT NOT NULL,
    created TEXT NOT NULL,
    delivered TEXT
);
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    heard TEXT NOT NULL,
    kind TEXT,
    confidence REAL,
    result TEXT,
    ms INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


# ---- tasks ----

def add_task(title: str, due: datetime | None, notes: str = "", source: str = "voice",
             vault_key: str | None = None) -> int:
    with session() as c:
        cur = c.execute(
            "INSERT INTO tasks (title, notes, due, source, vault_key, created) VALUES (?,?,?,?,?,?)",
            (title, notes, iso(due), source, vault_key, iso(now())),
        )
        return cur.lastrowid


def get_task(task_id: int) -> sqlite3.Row | None:
    with session() as c:
        return c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def open_tasks() -> list[sqlite3.Row]:
    with session() as c:
        return c.execute(
            "SELECT * FROM tasks WHERE status='open' ORDER BY created"
        ).fetchall()


def update_task(task_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with session() as c:
        c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))


def close_task(task_id: int, status: str) -> sqlite3.Row | None:
    """Mark done or dropped. Vault tasks also get queued so the Mac checks them off in the note."""
    task = get_task(task_id)
    if task is None or task["status"] != "open":
        return task
    update_task(task_id, status=status, closed=iso(now()))
    if task["source"] == "vault" and status == "done":
        queue("vault_done", {"vault_key": task["vault_key"]})
    return get_task(task_id)


# ---- outbox (things for the Mac to write into the vault) ----

def queue(kind: str, payload: dict) -> None:
    with session() as c:
        c.execute("INSERT INTO outbox (kind, payload, created) VALUES (?,?,?)",
                  (kind, json.dumps(payload), iso(now())))


def pending_outbox() -> list[dict]:
    with session() as c:
        rows = c.execute("SELECT * FROM outbox WHERE delivered IS NULL ORDER BY id").fetchall()
    return [{"id": r["id"], "kind": r["kind"], **json.loads(r["payload"])} for r in rows]


def ack_outbox(ids: list[int]) -> None:
    with session() as c:
        c.executemany("UPDATE outbox SET delivered=? WHERE id=?", [(iso(now()), i) for i in ids])


# ---- captures log ----

def log_capture(heard: str, kind: str | None, confidence: float | None, result: str, ms: int) -> None:
    with session() as c:
        c.execute(
            "INSERT INTO captures (at, heard, kind, confidence, result, ms) VALUES (?,?,?,?,?,?)",
            (iso(now()), heard, kind, confidence, result, ms),
        )


def recent_captures(limit: int = 20) -> list[sqlite3.Row]:
    with session() as c:
        return c.execute("SELECT * FROM captures ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---- meta ----

def get_meta(key: str, default=None):
    with session() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(key: str, value) -> None:
    with session() as c:
        c.execute("INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))
