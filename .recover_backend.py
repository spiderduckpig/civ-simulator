"""Reconstruct backend/engine/simulation.py and constants.py from transcripts.

simulation.py: baseline read at 2026-04-11T20:01:41.861Z + 5 subsequent edits
constants.py: baseline read at 2026-04-11T20:02:01.533Z (no subsequent edits)
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


def strip_line_prefix(read_text: str) -> str:
    out_lines = []
    for line in read_text.splitlines():
        tab = line.find("\t")
        if tab >= 0 and line[:tab].strip().isdigit():
            out_lines.append(line[tab + 1:])
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def collect_events(needle: str):
    """Returns list of (ts, kind, data) for given file needle across all transcripts.

    kind ∈ {"read_result", "edit", "write"}
    """
    tool_uses: dict[str, dict] = {}
    events: list[tuple[str, str, dict]] = []

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
                    ptype = part.get("type")
                    if ptype == "tool_use":
                        name = part.get("name")
                        inp = part.get("input") or {}
                        fp = str(inp.get("file_path") or "")
                        if needle.lower() not in fp.lower():
                            continue
                        tuid = part.get("id", "")
                        tool_uses[tuid] = {"ts": ts, "name": name, "input": inp}
                        if name == "Edit":
                            events.append((ts, "edit", inp))
                        elif name == "Write":
                            events.append((ts, "write", inp))
                        elif name == "MultiEdit":
                            for ed in inp.get("edits", []):
                                events.append((ts, "edit", ed))
                    elif ptype == "tool_result":
                        tuid = part.get("tool_use_id", "")
                        origin = tool_uses.get(tuid)
                        if not origin or origin["name"] != "Read":
                            continue
                        rc = part.get("content")
                        text = ""
                        if isinstance(rc, str):
                            text = rc
                        elif isinstance(rc, list):
                            for piece in rc:
                                if isinstance(piece, dict) and piece.get("type") == "text":
                                    text += piece.get("text", "")
                        events.append((origin["ts"], "read_result", {"text": text}))

    events.sort(key=lambda e: e[0])
    return events


def reconstruct(needle: str, baseline_ts_prefix: str, target_path: str):
    events = collect_events(needle)
    baseline = None
    baseline_ts = None
    for ts, kind, data in events:
        if kind == "read_result" and ts.startswith(baseline_ts_prefix):
            text = data["text"]
            stripped = strip_line_prefix(text)
            # prefer the longest baseline (full-file read) at that timestamp
            if baseline is None or len(stripped) > len(baseline):
                baseline = stripped
                baseline_ts = ts
    if baseline is None:
        print(f"[{needle}] NO BASELINE at {baseline_ts_prefix}", file=sys.stderr)
        return
    print(f"[{needle}] baseline {baseline_ts} -> {len(baseline)} chars", file=sys.stderr)

    current = baseline
    for ts, kind, data in events:
        if ts <= baseline_ts:
            continue
        if kind == "edit":
            old = data.get("old_string", "")
            new = data.get("new_string", "")
            replace_all = data.get("replace_all", False)
            if old and old in current:
                if replace_all:
                    current = current.replace(old, new)
                else:
                    current = current.replace(old, new, 1)
                print(f"  [{ts}] applied edit old={len(old)} new={len(new)} -> {len(current)}", file=sys.stderr)
            elif new and new in current:
                print(f"  [{ts}] SKIP already applied", file=sys.stderr)
            else:
                print(f"  [{ts}] CONFLICT old not found old={old[:60]!r}", file=sys.stderr)
        elif kind == "write":
            current = data.get("content", "")
            print(f"  [{ts}] write -> {len(current)}", file=sys.stderr)

    Path(target_path).write_text(current, encoding="utf-8", newline="\n")
    print(f"[{needle}] wrote {target_path} ({len(current)} chars)\n", file=sys.stderr)


def main() -> None:
    reconstruct("simulation.py", "2026-04-11T20:01:41", "backend/engine/simulation.py")
    reconstruct("constants.py", "2026-04-11T20:02:01", "backend/engine/constants.py")
    reconstruct("helpers.py", "2026-04-11T07:59:36", "backend/engine/helpers.py")


if __name__ == "__main__":
    main()
