"""Runs the whole flow with Jev, Claude, ntfy and iCloud faked out."""

import importlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

KEY = "test-key"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DB", str(tmp_path / "j.db"))
    monkeypatch.setenv("JARVIS_KEY", KEY)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("PUBLIC_URL", "https://jarvis.example")
    from jarvis import app, capture, clock, db, icloud, jev, llm, notify, proactive
    importlib.reload(db)
    clockbox = {"t": datetime(2026, 10, 20, 7, 30)}
    for mod in (clock, db, capture, proactive, llm):
        monkeypatch.setattr(mod, "now", lambda: clockbox["t"], raising=False)

    sent = []
    monkeypatch.setattr(notify, "send", lambda msg, **kw: sent.append((msg, kw)) or True)
    monkeypatch.setattr(icloud, "configured", lambda: False)

    jev_answer = {"kind": "task", "conf": 0.9}
    monkeypatch.setattr(jev, "classify", lambda text: (jev_answer["kind"], jev_answer["conf"]))

    calls = []
    replies = {}

    def fake_call(model, system, prompt, schema, max_tokens=600):
        calls.append((model, prompt))
        return replies[next(k for k in replies if k in system)]

    monkeypatch.setattr(llm, "_call", fake_call)
    client = TestClient(app.app)
    return dict(client=client, sent=sent, jev=jev_answer, replies=replies, calls=calls,
                clock=clockbox, db=db, proactive=proactive)


def post(env, path, **kw):
    return env["client"].post(path, headers={"X-Jarvis-Key": KEY}, **kw)


def test_needs_key(env):
    assert env["client"].post("/capture", json={"text": "hi"}).status_code == 401


def test_empty_capture(env):
    r = post(env, "/capture", json={"text": "  "}).json()
    assert r["kind"] == "empty"


def test_task_capture_and_default_time(env):
    env["replies"]["something he needs to do"] = {"title": "Call Benz", "due": "", "notes": ""}
    r = post(env, "/capture", json={"text": "remind me to call Benz"}).json()
    assert r["kind"] == "task"
    assert "Call Benz" in r["say"] and "today at 6 PM" in r["say"]
    t = env["db"].open_tasks()[0]
    assert t["due"] == "2026-10-20T18:00"


def test_low_jev_confidence_asks_claude(env):
    env["jev"].update(kind="note", conf=0.1)
    env["replies"]["Sort what he said"] = {"kind": "question"}
    env["replies"]["asked you something"] = {"answer": "It's 42."}
    r = post(env, "/capture", json={"text": "what's the answer"}).json()
    assert r["kind"] == "question" and r["say"] == "It's 42."


def test_done_by_voice(env):
    env["db"].add_task("Do laundry", datetime(2026, 10, 20, 9))
    env["jev"]["kind"] = "done"
    env["replies"]["finished something"] = {"task_id": 1, "dropped": False}
    r = post(env, "/capture", json={"text": "I did the laundry"}).json()
    assert "Crossed off Do laundry" in r["say"]
    assert env["db"].open_tasks() == []


def test_failure_keeps_words(env):
    env["jev"]["kind"] = "task"  # no reply registered -> handler raises
    r = post(env, "/capture", json={"text": "remind me about the thing"}).json()
    assert r["kind"] == "task-failed"
    assert env["db"].pending_outbox()[0]["text"] == "remind me about the thing"


def test_nudges_wait_for_brief_then_ping_every_3_hours(env):
    db, pro, sent, clock = env["db"], env["proactive"], env["sent"], env["clock"]
    db.add_task("Email Baird", datetime(2026, 10, 20, 7, 0))
    assert pro.tick() == []  # 7:30, quiet hours
    clock["t"] = datetime(2026, 10, 20, 8, 30)
    assert pro.tick() == []  # brief hasn't gone out yet
    env["replies"]["morning brief"] = {"say": "Morning.", "text": "Still open: Email Baird", "fact": "f",
                                       "skill": "s"}
    r = env["client"].get("/brief", headers={"X-Jarvis-Key": KEY}).json()
    assert r["say"] == "Morning." and len(sent) == 2
    assert env["client"].get("/brief", headers={"X-Jarvis-Key": KEY}).json()["repeat"] is True
    assert pro.tick() == []  # brief just covered it
    clock["t"] = datetime(2026, 10, 20, 11, 31)
    assert pro.tick() == ["nudge 1"]
    msg, kw = sent[-1]
    assert "Email Baird" in msg and [a["label"] for a in kw["actions"]] == ["Done", "Tomorrow", "Drop"]
    assert kw["actions"][0]["url"] == "https://jarvis.example/act/1/done"
    clock["t"] = datetime(2026, 10, 20, 12, 0)
    assert pro.tick() == []
    clock["t"] = datetime(2026, 10, 20, 21, 30)
    assert pro.tick() == []  # quiet


def test_backup_brief_at_10(env):
    env["clock"]["t"] = datetime(2026, 10, 20, 10, 5)
    env["replies"]["morning brief"] = {"say": "m", "text": "t", "fact": "f", "skill": "s"}
    assert "brief (backup)" in env["proactive"].tick()


def test_tomorrow_three_times_gets_suggestion(env):
    db, pro, clock = env["db"], env["proactive"], env["clock"]
    db.set_meta("brief_date", "2026-10-20")
    tid = db.add_task("Update resume", datetime(2026, 10, 20, 9))
    for _ in range(3):
        r = post(env, f"/act/{tid}/tomorrow").json()
    assert "Moved to tomorrow" in r["say"]
    assert db.get_task(tid)["pushes"] == 3
    env["replies"]["keeps pushing it"] = {"suggestion": "Just open the doc and fix one bullet."}
    clock["t"] = datetime(2026, 10, 21, 9, 0)
    db.set_meta("brief_date", "2026-10-21")
    assert pro.tick() == [f"nudge {tid}"]
    msg, kw = env["sent"][-1]
    assert "fix one bullet" in msg and kw["priority"] == 4


def test_vault_sync_roundtrip(env):
    db = env["db"]
    out = post(env, "/sync", json={"tasks": [{"key": "send thank you note", "title": "Send thank you note",
                                              "date": "2026-09-28"}],
                                   "followups": [{"company": "Baird", "status": "applied"}]}).json()
    assert out["added"] == 1
    t = db.open_tasks()[0]
    assert t["source"] == "vault" and t["created"] == "2026-09-28T00:00"
    post(env, f"/act/{t['id']}/done")
    out = post(env, "/sync", json={"tasks": [{"key": "send thank you note", "title": "x"}]}).json()
    assert out["outbox"][0]["kind"] == "vault_done"
    post(env, "/sync/ack", json={"ids": [out["outbox"][0]["id"]]})
    assert db.pending_outbox() == []
    # checked off in Obsidian -> closes in Jarvis
    db.add_task("Other", None, source="vault", vault_key="other")
    post(env, "/sync", json={"tasks": []})
    assert db.open_tasks() == []


def test_tasks_page(env):
    env["db"].add_task("Buy <milk>", datetime(2026, 10, 20, 9))
    page = env["client"].get(f"/tasks?key={KEY}").text
    assert "Buy &lt;milk&gt;" in page and "Tomorrow" in page
    r = env["client"].post(f"/tasks/1/done?key={KEY}", follow_redirects=False)
    assert r.status_code == 303
    assert env["db"].open_tasks() == []
