"""Safety net for the edits Claude makes in the vault.

Claude only gets Edit and Write, so it cannot delete or move anything. Before each edit this
hook copies the file to ~/jarvis/backups, logs it, and refuses paths outside the vault or in
.obsidian. `undo_edits` puts a capture's files back the way they were.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import HookMatcher

ROOT = Path(__file__).resolve().parents[2]
BACKUPS = ROOT / "backups"
EDITS_LOG = ROOT / "edits.jsonl"


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}


def _log(entry: dict) -> None:
    with EDITS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def hooks_for(vault: Path, source: str, edits: list[dict]) -> dict:
    """PreToolUse hooks for one capture. `edits` collects what changed, for the caller to report."""
    vault_root = os.path.realpath(vault)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")

    async def before_edit(input_data, tool_use_id, context):
        raw = input_data["tool_input"].get("file_path") or input_data["tool_input"].get("notebook_path") or ""
        path = os.path.realpath(os.path.expanduser(raw))
        if not path.startswith(vault_root + os.sep):
            return _deny("Edits are only allowed inside the vault.")
        rel = Path(path).relative_to(vault_root)
        if rel.parts[0] == ".obsidian":
            return _deny("Do not edit Obsidian's own settings.")
        backup = None
        if os.path.exists(path):
            backup = BACKUPS / stamp / rel
            if not backup.exists():  # keep the first copy: that is the file as it was before this capture
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
        edits.append({"file": str(rel), "new": backup is None})
        _log({"stamp": stamp, "source": source, "file": str(rel), "backup": str(backup) if backup else None})
        return {}

    return {"PreToolUse": [HookMatcher(matcher="Edit|Write|MultiEdit|NotebookEdit", hooks=[before_edit])]}


def undo_edits(vault: Path, stamp: str = "last") -> list[str]:
    """Restore every file changed in one capture (the latest by default). New files are removed."""
    entries = [json.loads(line) for line in EDITS_LOG.read_text(encoding="utf-8").splitlines() if line] if EDITS_LOG.exists() else []
    entries = [e for e in entries if not e.get("undone")]
    if not entries:
        raise LookupError("no edits to undo")
    target = entries[-1]["stamp"] if stamp == "last" else stamp
    chosen = [e for e in entries if e["stamp"] == target]
    if not chosen:
        raise LookupError(f"no edits recorded for {target}")
    restored = []
    for e in reversed(chosen):
        path = vault / e["file"]
        if e["backup"]:
            shutil.copy2(e["backup"], path)
        elif path.exists():
            path.unlink()
        restored.append(e["file"])
    _log({"stamp": target, "undone": True})
    return restored
