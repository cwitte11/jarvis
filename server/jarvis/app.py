"""Jarvis Mark 4 server.

Phone  -> POST /capture        one sentence in, what Jarvis said back out
Phone  -> GET  /brief          morning brief, fired by a wake-up automation
ntfy   -> POST /act/{id}/{verb} Done / Tomorrow / Drop buttons
Mac    -> POST /sync           open vault tasks in, notes for the vault out
You    -> GET  /tasks          the list, with buttons
"""

import asyncio
import hmac
import html
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import capture, db, icloud, proactive
from .clock import now

log = logging.getLogger("jarvis")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
TICK_SECONDS = 300


def _check(given: str | None) -> None:
    key = os.environ.get("JARVIS_KEY", "")
    if not key or not given or not hmac.compare_digest(given, key):
        raise HTTPException(401, "wrong key")


def auth(x_jarvis_key: str | None = Header(None), key: str | None = Query(None)) -> None:
    _check(x_jarvis_key or key)


async def _loop():
    while True:
        try:
            did = await asyncio.to_thread(proactive.tick)
            if did:
                log.info("tick: %s", ", ".join(did))
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect().close()
    db.set_meta("brief_failed", None)  # a fresh start retries the brief right away
    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="Jarvis", lifespan=lifespan)


class Capture(BaseModel):
    text: str = ""


class SyncIn(BaseModel):
    tasks: list[dict] = []
    followups: list[dict] = []
    thoughts: list[str] = []


class Ack(BaseModel):
    ids: list[int]


@app.get("/health")
def health():
    return {"ok": True, "time": db.iso(now())}


@app.post("/capture", dependencies=[Depends(auth)])
def post_capture(body: Capture):
    return capture.handle(body.text)


@app.get("/brief", dependencies=[Depends(auth)])
def get_brief(trigger: str = "wake", force: bool = False):
    return proactive.brief(trigger=trigger, force=force)


@app.post("/act/{task_id}/{verb}", dependencies=[Depends(auth)])
def post_act(task_id: int, verb: str):
    return {"say": proactive.act(task_id, verb)}


@app.post("/sync", dependencies=[Depends(auth)])
def post_sync(body: SyncIn):
    return proactive.sync(body.tasks, body.followups, body.thoughts)


@app.post("/sync/ack", dependencies=[Depends(auth)])
def post_ack(body: Ack):
    db.ack_outbox(body.ids)
    return {"ok": True}


@app.post("/tick", dependencies=[Depends(auth)])
def post_tick():
    return {"did": proactive.tick()}


@app.get("/admin/calendars", dependencies=[Depends(auth)])
def admin_calendars():
    return icloud.list_calendars()


@app.get("/admin/costs", dependencies=[Depends(auth)])
def admin_costs():
    return {"claude_usd_by_month": db.get_meta("costs", {}),
            "note": "Claude only. Jev and Railway bill separately."}


@app.get("/admin/captures", dependencies=[Depends(auth)])
def admin_captures():
    return [dict(r) for r in db.recent_captures(30)]


# ---- a plain page for the list, opened by tapping a notification ----

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis</title><style>
:root{{color-scheme:light dark;--bg:#0b0f14;--fg:#e6edf3;--dim:#8b98a5;--line:#1f2a36;--accent:#4fc3f7}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:16px/1.4 -apple-system,system-ui,sans-serif}}
h1{{font-size:20px;color:var(--accent);margin:0 0 12px}}
.t{{border-bottom:1px solid var(--line);padding:12px 0}} .m{{color:var(--dim);font-size:13px}}
.s{{font-size:14px;margin-top:4px}} form{{display:inline}}
button{{margin:8px 6px 0 0;padding:8px 12px;border-radius:8px;border:1px solid var(--line);
background:#131a22;color:var(--fg);font-size:14px}}
</style></head><body><h1>Jarvis</h1>{rows}</body></html>"""


@app.get("/tasks", response_class=HTMLResponse, dependencies=[Depends(auth)])
def tasks_page(key: str = Query(...)):
    rows = []
    for t in db.open_tasks():
        meta = [f"{proactive._age_days(t)}d on list"]
        if t["pushes"]:
            meta.append(f"pushed {t['pushes']}x")
        if t["due"]:
            meta.append("due " + proactive.spoken(proactive._dt(t["due"])))
        if t["source"] == "vault":
            meta.append("daily note")
        buttons = "".join(
            f'<form method="post" action="/tasks/{t["id"]}/{v}?key={html.escape(key)}"><button>{label}</button></form>'
            for label, v in (("Done", "done"), ("Tomorrow", "tomorrow"), ("Drop", "drop")))
        sug = f'<div class="s">{html.escape(t["suggestion"])}</div>' if t["suggestion"] else ""
        rows.append(f'<div class="t"><div>{html.escape(t["title"])}</div>'
                    f'<div class="m">{" · ".join(meta)}</div>{sug}{buttons}</div>')
    return PAGE.format(rows="".join(rows) or "<p>Nothing open.</p>")


@app.post("/tasks/{task_id}/{verb}", response_class=HTMLResponse, dependencies=[Depends(auth)])
def tasks_act(task_id: int, verb: str, key: str = Query(...)):
    proactive.act(task_id, verb)
    return HTMLResponse(status_code=303, headers={"Location": f"/tasks?key={key}"})
