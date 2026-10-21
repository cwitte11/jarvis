# Jarvis

My personal assistant. Built piece by piece with Claude.

- `server/` Mark 4. Always-on server on Railway. Jev sorts what I say, Claude fills in the details. Keeps its own task list, sends a morning brief when I wake up, and nudges me about anything I keep pushing off.
- `mac/` Mark 4 vault sync. Runs on my Mac every 5 minutes and keeps the server and my Obsidian vault in step.
- `src/jarvis/` Mark 2. The first Python version, ran on the Mac with the Claude Agent SDK.

The HUD lives in its own repo, jarvis-hud.
