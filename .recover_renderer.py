"""Recover frontend/renderer.js by replaying only the missing edits from
transcripts onto the current on-disk file.

Strategy: for each edit event in chronological order, check if the current
file contains the `old_string` or the `new_string`:

  - old present, new absent     -> edit not yet applied, APPLY it
  - new present, old absent     -> edit already applied, SKIP
  - both absent                 -> conflict/unknown, REPORT
  - both present                -> ambiguous, SKIP and REPORT
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

TARGET_FILE = "frontend/renderer.js"
NEEDLE = "renderer.js"


def iter_edits(transcript: str):
    """Yield (ts, name, input) for each Edit/Write/MultiEdit targeting NEEDLE."""
    with open(transcript, "r", encoding="utf-8") as f:
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
                if NEEDLE.lower() not in fp.lower():
                    continue
                yield (ts, name, inp)


def main() -> None:
    all_edits: list[tuple[str, str, dict]] = []
    for t in TRANSCRIPTS:
        all_edits.extend(iter_edits(t))
    all_edits.sort(key=lambda e: e[0])
    print(f"[{len(all_edits)} total Edit/Write events]", file=sys.stderr)

    current = Path(TARGET_FILE).read_text(encoding="utf-8")
    print(f"[starting from on-disk {TARGET_FILE}: {len(current)} chars]", file=sys.stderr)

    applied = 0
    skipped_already = 0
    skipped_conflict = 0
    write_ops = 0

    for ts, name, inp in all_edits:
        if name == "Write":
            write_ops += 1
            # Write operations REPLACE the whole file. We don't want to apply
            # an ancient Write — only apply one if the current file doesn't
            # have any edits after it we've missed. Skip all Writes; the
            # current file is our starting point.
            print(f"  [skip Write @ {ts}: would replace the whole file]", file=sys.stderr)
            continue

        if name == "MultiEdit":
            for ed in inp.get("edits", []):
                current, result = _apply_one(current, ed, ts)
                if result == "applied":
                    applied += 1
                elif result == "already":
                    skipped_already += 1
                else:
                    skipped_conflict += 1
            continue

        # Single Edit
        current, result = _apply_one(current, inp, ts)
        if result == "applied":
            applied += 1
        elif result == "already":
            skipped_already += 1
        else:
            skipped_conflict += 1

    print(f"\n[summary] applied={applied}  already={skipped_already}  "
          f"conflicted={skipped_conflict}  writes_skipped={write_ops}",
          file=sys.stderr)
    print(f"[final size: {len(current)} chars]", file=sys.stderr)

    Path(TARGET_FILE).write_text(current, encoding="utf-8", newline="\n")
    print(f"[wrote {TARGET_FILE}]", file=sys.stderr)


def _apply_one(current: str, inp: dict, ts: str) -> tuple[str, str]:
    old = inp.get("old_string", "")
    new = inp.get("new_string", "")
    replace_all = inp.get("replace_all", False)

    old_in = old in current if old else False
    new_in = new in current if new else False

    if old_in and not new_in:
        if replace_all:
            return current.replace(old, new), "applied"
        return current.replace(old, new, 1), "applied"
    if new_in and not old_in:
        return current, "already"
    if old_in and new_in:
        # Both present — the new may just be a substring of the old, common
        # when an edit shrinks a block. Apply it as-is (match on old).
        if replace_all:
            return current.replace(old, new), "applied"
        return current.replace(old, new, 1), "applied"
    # Neither present — conflict
    print(f"  [CONFLICT @ {ts}] old/new both missing", file=sys.stderr)
    print(f"     old preview: {old[:100]!r}", file=sys.stderr)
    print(f"     new preview: {new[:100]!r}", file=sys.stderr)
    return current, "conflict"


if __name__ == "__main__":
    main()
