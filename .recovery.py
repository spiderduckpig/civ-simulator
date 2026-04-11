"""Scan Claude Code transcripts for all tool interactions with a given file.

Captures:
  - Read tool_use + its tool_result (full file snapshot at read time)
  - Write tool_use (full file rewrite)
  - Edit tool_use (old_string / new_string pairs)
  - MultiEdit tool_use

Outputs everything sorted by timestamp, across all transcripts given.
Emits a manifest so we can pick the most recent baseline snapshot and
any Edit/Write calls after it.

Usage:
    python .recovery.py <target_substring> <transcript1.jsonl> [...]
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def iter_records(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            try:
                yield line_no, json.loads(raw)
            except json.JSONDecodeError:
                continue


def extract(transcripts: list[str], needle: str) -> list[dict]:
    """Return a flat list of events for the target file across all transcripts.

    Each event: {ts, kind, transcript, line, tool_use_id, file_path, data}
    kind ∈ {read_call, read_result, write, edit, multiedit}
    """
    # Step 1: collect tool_use calls keyed by id.
    tool_uses: dict[str, dict] = {}
    events: list[dict] = []

    for t in transcripts:
        tname = Path(t).name
        for line_no, rec in iter_records(t):
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
                    fp = str(inp.get("file_path") or inp.get("path") or "")
                    if needle.lower() not in fp.lower():
                        continue
                    tuid = part.get("id", "")
                    entry = {
                        "ts": ts, "kind": None, "transcript": tname,
                        "line": line_no, "tool_use_id": tuid,
                        "file_path": fp, "data": {},
                        "name": name,
                    }
                    if name == "Read":
                        entry["kind"] = "read_call"
                        entry["data"] = {"offset": inp.get("offset"), "limit": inp.get("limit")}
                    elif name == "Write":
                        entry["kind"] = "write"
                        entry["data"] = {"content": inp.get("content", "")}
                    elif name == "Edit":
                        entry["kind"] = "edit"
                        entry["data"] = {
                            "old": inp.get("old_string", ""),
                            "new": inp.get("new_string", ""),
                            "replace_all": inp.get("replace_all", False),
                        }
                    elif name == "MultiEdit":
                        entry["kind"] = "multiedit"
                        entry["data"] = {"edits": inp.get("edits", [])}
                    else:
                        continue
                    tool_uses[tuid] = entry
                    events.append(entry)

                elif ptype == "tool_result":
                    tuid = part.get("tool_use_id", "")
                    if tuid not in tool_uses:
                        continue
                    origin = tool_uses[tuid]
                    if origin["kind"] != "read_call":
                        continue
                    # Extract the textual content of the Read result.
                    rc = part.get("content")
                    text = ""
                    if isinstance(rc, str):
                        text = rc
                    elif isinstance(rc, list):
                        for piece in rc:
                            if isinstance(piece, dict) and piece.get("type") == "text":
                                text += piece.get("text", "")
                    events.append({
                        "ts": ts, "kind": "read_result", "transcript": tname,
                        "line": line_no, "tool_use_id": tuid,
                        "file_path": origin["file_path"],
                        "data": {"text": text, "origin_line": origin["line"]},
                        "name": "Read",
                    })

    events.sort(key=lambda e: (e["ts"], e["line"]))
    return events


def strip_line_prefix(read_text: str) -> str:
    """Read tool results prefix each line with 'N\t'. Undo that."""
    out_lines = []
    for line in read_text.splitlines():
        # Lines look like: "   123\tactual content"
        tab = line.find("\t")
        if tab >= 0 and line[:tab].strip().isdigit():
            out_lines.append(line[tab + 1:])
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def main() -> None:
    needle = sys.argv[1]
    transcripts = sys.argv[2:]
    events = extract(transcripts, needle)

    # Print a manifest to stderr so we can see what we have.
    print(f"[{len(events)} events for '{needle}']", file=sys.stderr)
    for e in events:
        extra = ""
        if e["kind"] == "read_result":
            extra = f" ({len(e['data']['text'])} chars)"
        elif e["kind"] == "read_call":
            extra = f" off={e['data']['offset']} lim={e['data']['limit']}"
        elif e["kind"] == "write":
            extra = f" ({len(e['data']['content'])} chars)"
        elif e["kind"] == "edit":
            extra = f" old={len(e['data']['old'])}ch new={len(e['data']['new'])}ch"
        print(f"  {e['ts']} [{e['kind']:12s}] {e['transcript']}:{e['line']}{extra}", file=sys.stderr)

    # Dump each event to stdout with a clear separator so we can slice them out.
    for i, e in enumerate(events):
        print(f"\n########## EVENT #{i} ##########")
        print(f"ts={e['ts']}")
        print(f"kind={e['kind']}")
        print(f"transcript={e['transcript']}")
        print(f"line={e['line']}")
        print(f"name={e['name']}")
        print(f"file_path={e['file_path']}")
        if e["kind"] == "read_result":
            print("--- READ RESULT ---")
            print(strip_line_prefix(e["data"]["text"]))
            print("--- END READ RESULT ---")
        elif e["kind"] == "write":
            print("--- WRITE CONTENT ---")
            print(e["data"]["content"])
            print("--- END WRITE CONTENT ---")
        elif e["kind"] == "edit":
            print(f"replace_all={e['data']['replace_all']}")
            print("--- OLD ---")
            print(e["data"]["old"])
            print("--- NEW ---")
            print(e["data"]["new"])
            print("--- END EDIT ---")
        elif e["kind"] == "multiedit":
            for j, ed in enumerate(e["data"]["edits"]):
                print(f"--- MULTIEDIT[{j}] OLD ---")
                print(ed.get("old_string", ""))
                print(f"--- MULTIEDIT[{j}] NEW ---")
                print(ed.get("new_string", ""))


if __name__ == "__main__":
    main()
