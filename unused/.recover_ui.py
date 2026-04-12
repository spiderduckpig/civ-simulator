"""Reconstruct frontend/ui.js from the last-session transcript.

Plan:
  1. Baseline: read_result at 2026-04-11T20:21:15 (30649 chars, full file)
  2. Apply edit at 2026-04-11T20:21:25 (old=240 new=803)
  3. Apply edit at 2026-04-11T20:22:11 (old=163 new=305)

Writes the result to frontend/ui.js.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

TRANSCRIPT = "C:/Users/brian/.claude/projects/c--Users-brian--vscode-2026projects-civ-simulator/962615d8-da57-46e5-a5c2-3d056697cb10.jsonl"
TARGET_TS_BASELINE = "2026-04-11T20:21:15"
TARGET_FILE_NEEDLE = "ui.js"


def strip_line_prefix(read_text: str) -> str:
    out_lines = []
    for line in read_text.splitlines():
        tab = line.find("\t")
        if tab >= 0 and line[:tab].strip().isdigit():
            out_lines.append(line[tab + 1:])
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def iter_events():
    tool_uses: dict[str, dict] = {}
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
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
                    if TARGET_FILE_NEEDLE.lower() not in fp.lower():
                        continue
                    tuid = part.get("id", "")
                    tool_uses[tuid] = {"ts": ts, "name": name, "input": inp, "line": line_no}
                    if name in ("Edit", "Write"):
                        yield {"kind": name.lower(), "ts": ts, "input": inp}
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
                    yield {"kind": "read_result", "ts": ts,
                           "text": text, "origin_ts": origin["ts"]}


def main() -> None:
    events = list(iter_events())

    # Find the full-file baseline at TARGET_TS_BASELINE.
    baseline = None
    baseline_ts = None
    for e in events:
        if e["kind"] == "read_result" and e["origin_ts"].startswith(TARGET_TS_BASELINE):
            baseline = strip_line_prefix(e["text"])
            baseline_ts = e["origin_ts"]
            print(f"[baseline: {baseline_ts}  {len(baseline)} chars]", file=sys.stderr)
            break

    if baseline is None:
        print("No baseline found!", file=sys.stderr)
        sys.exit(1)

    # Apply any edits chronologically AFTER the baseline timestamp.
    current = baseline
    for e in events:
        if e["ts"] <= baseline_ts:
            continue
        if e["kind"] == "edit":
            old = e["input"].get("old_string", "")
            new = e["input"].get("new_string", "")
            replace_all = e["input"].get("replace_all", False)
            if old not in current:
                print(f"[SKIP edit {e['ts']}: old_string not found in current file]", file=sys.stderr)
                print(f"    old preview: {old[:80]!r}", file=sys.stderr)
                continue
            if replace_all:
                new_current = current.replace(old, new)
            else:
                new_current = current.replace(old, new, 1)
            print(f"[apply edit {e['ts']}: old={len(old)}ch -> new={len(new)}ch  result={len(new_current)}ch]", file=sys.stderr)
            current = new_current
        elif e["kind"] == "write":
            current = e["input"].get("content", "")
            print(f"[apply write {e['ts']}: {len(current)} chars]", file=sys.stderr)

    out = Path("frontend/ui.js")
    out.write_text(current, encoding="utf-8", newline="\n")
    print(f"[wrote {out} ({len(current)} chars)]", file=sys.stderr)


if __name__ == "__main__":
    main()
