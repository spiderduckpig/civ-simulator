"""For each tracked file, find the chronologically last Edit or Write event
in transcripts and check if its resulting state ('new_string' or content) is
present in the current on-disk file. Report mismatches.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

TRANSCRIPTS = [
    "C:/Users/brian/.claude/projects/c--Users-brian--vscode-2026projects-civ-simulator/153fc6c3-d1db-49bd-b550-66180294fa6f.jsonl",
    "C:/Users/brian/.claude/projects/c--Users-brian--vscode-2026projects-civ-simulator/46132d41-0a5d-4f48-b3cd-d4f03309a9c3.jsonl",
    "C:/Users/brian/.claude/projects/c--Users-brian--vscode-2026projects-civ-simulator/b874df8a-55ec-477f-b918-821276b859bf.jsonl",
    "C:/Users/brian/.claude/projects/c--Users-brian--vscode-2026projects-civ-simulator/962615d8-da57-46e5-a5c2-3d056697cb10.jsonl",
]

TARGETS = [
    ("backend/engine/mapgen.py", "mapgen.py"),
    ("backend/engine/civ.py", "civ.py"),
    ("backend/engine/combat.py", "combat.py"),
    ("backend/engine/diplomacy.py", "diplomacy.py"),
    ("backend/engine/city_dev.py", "city_dev.py"),
    ("backend/engine/improvements.py", "improvements.py"),
    ("backend/engine/models.py", "models.py"),
    ("backend/main.py", "main.py"),
    ("frontend/index.html", "index.html"),
    ("frontend/ui.js", "ui.js"),
    ("frontend/renderer.js", "renderer.js"),
    ("backend/engine/simulation.py", "simulation.py"),
    ("backend/engine/constants.py", "constants.py"),
    ("backend/engine/helpers.py", "helpers.py"),
]


def events_for(needle: str):
    ev = []
    for t in TRANSCRIPTS:
        with open(t, "r", encoding="utf-8") as f:
            for raw in f:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp") or ""
                msg = rec.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "tool_use":
                        continue
                    name = part.get("name")
                    if name not in ("Edit", "Write", "MultiEdit"):
                        continue
                    inp = part.get("input") or {}
                    fp = str(inp.get("file_path") or "")
                    if needle.lower() not in fp.lower():
                        continue
                    if name == "Edit":
                        ev.append((ts, "edit", inp.get("new_string", "")))
                    elif name == "Write":
                        ev.append((ts, "write", inp.get("content", "")))
                    elif name == "MultiEdit":
                        for ed in inp.get("edits", []):
                            ev.append((ts, "edit", ed.get("new_string", "")))
    ev.sort(key=lambda e: e[0])
    return ev


def main():
    bad = []
    for path, needle in TARGETS:
        p = Path(path)
        if not p.exists():
            print(f"  MISSING  {path}")
            bad.append(path)
            continue
        cur = p.read_text(encoding="utf-8", errors="replace")
        ev = events_for(needle)
        if not ev:
            print(f"  no-events {path}  ({len(cur)} chars)")
            continue
        last_ts, last_kind, last_new = ev[-1]
        if not last_new:
            print(f"  no-new-str {path}  last={last_kind}@{last_ts}")
            continue
        needle_excerpt = last_new[:400]
        if needle_excerpt in cur:
            print(f"  OK       {path}  last={last_kind}@{last_ts} new={len(last_new)}ch")
        else:
            print(f"  STALE    {path}  last={last_kind}@{last_ts} new={len(last_new)}ch")
            print(f"           preview: {needle_excerpt[:120]!r}")
            bad.append(path)

    if bad:
        print(f"\n[{len(bad)} STALE] {bad}")
    else:
        print("\n[all files current]")


if __name__ == "__main__":
    main()
