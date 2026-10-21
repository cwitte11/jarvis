"""Jev picks what kind of capture this is. Fast and cheap. It never writes anything.

API: POST https://api.typesafe.ai/v1/systemone (TypeSafe docs, checked 2026-10-19).
"""

import os

import httpx

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")

KINDS = {
    "task": "Something he needs to do, or wants to be reminded about. Includes 'remind me', 'I need to', "
            "'don't let me forget', errands, follow-ups, chores.",
    "event": "Something happening at a set time with other people or a place: a call, interview, meeting, "
             "appointment, plans, a game he's going to. He wants it on his calendar.",
    "done": "He's reporting he finished, did, or no longer needs something that was already on his list. "
            "'I did X', 'finished X', 'X is done', 'cross off X', 'never mind about X'.",
    "question": "He wants something answered, explained, or looked up.",
    "note": "An idea, a thought, or a fact to keep that isn't a task, event, or question.",
}


class JevError(RuntimeError):
    pass


def classify(text: str, timeout: float = 4.0) -> tuple[str, float]:
    """Returns (kind, confidence 0-1). Raises JevError if Jev is unreachable or not set up."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("TYPESAFE_API_KEY not set")
    body = {
        "state": text,
        "model": MODEL,
        "questions": {
            "kind": {
                "type": "choice",
                "instructions": "Carson spoke this into his phone for his personal assistant. "
                                "What kind of capture is it?",
                "criteria": KINDS,
            }
        },
    }
    try:
        r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        r.raise_for_status()
        answer = r.json()["answers"]["kind"]
        return answer["choice"], float(answer.get("confidence", 0.0))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise JevError(str(e)) from e
