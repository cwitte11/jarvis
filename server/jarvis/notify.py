"""Push notifications through ntfy, with Done / Tomorrow / Drop buttons that call back to the server."""

import os

import httpx

NTFY = os.environ.get("NTFY_SERVER", "https://ntfy.sh")


def _topic() -> str:
    return os.environ.get("NTFY_TOPIC", "")


def _base() -> str:
    return os.environ.get("PUBLIC_URL", "").rstrip("/")


def _key() -> str:
    return os.environ.get("JARVIS_KEY", "")


def task_buttons(task_id: int) -> list[dict]:
    headers = {"X-Jarvis-Key": _key()}
    return [
        {"action": "http", "label": label, "url": f"{_base()}/act/{task_id}/{verb}",
         "method": "POST", "headers": headers, "clear": True}
        for label, verb in (("Done", "done"), ("Tomorrow", "tomorrow"), ("Drop", "drop"))
    ]


def send(message: str, title: str = "Jarvis", actions: list[dict] | None = None,
         priority: int = 3, tags: list[str] | None = None) -> bool:
    if not _topic():
        return False
    body = {
        "topic": _topic(),
        "title": title,
        "message": message[:3900],
        "priority": priority,
        "click": f"{_base()}/tasks?key={_key()}" if _base() else None,
    }
    if actions:
        body["actions"] = actions[:3]
    if tags:
        body["tags"] = tags
    body = {k: v for k, v in body.items() if v is not None}
    try:
        httpx.post(NTFY, json=body, timeout=10).raise_for_status()
        return True
    except httpx.HTTPError:
        return False
