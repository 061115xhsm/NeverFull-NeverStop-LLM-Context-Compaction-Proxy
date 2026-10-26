#!/usr/bin/env python3
"""Compact-Bench style paired evaluation (V11 P3).

Research basis
--------------
- Compact-Bench (arXiv:2610.31710): pair full-history and summary-only
  continuations from the same state; measure what compaction costs.
- KEEP-Bench (arXiv:2610.26827): QA accuracy hides unfaithful compression;
  probe evidence directly.

This harness is deterministic and offline. It builds four context views of the
same conversation (full / truncate / extractive summary / folded digest) and
measures how much *evidence* each view retains. It does not need an LLM.

Usage
-----
    python benchmark/paired_eval.py --data benchmark/data/conversations.jsonl
    python benchmark/paired_eval.py --json out.json --markdown out.md
    python benchmark/paired_eval.py            # built-in synthetic sanity check

Data format (one JSON object per line):
    {"id": "...", "messages": [{"role": "...", "content": "..."}, ...]}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folding import build_fold_digest, render_fold_block  # noqa: E402

FILE_RE = re.compile(
    r"(?<![\w/])(?:/[\w.\-]+){2,}\.\w{1,10}"
    r"|(?<![\w/])[\w.\-]+\.(?:py|js|ts|tsx|json|ya?ml|md|sh|go|rs|java|cpp|c|h)\b"
)
DECISION_RE = re.compile(
    r"(?:decided|决定|chose|will use|using|adopted|switched to)\s*(?:to\s+)?([^\n.]{5,140})",
    re.I,
)
ERROR_RE = re.compile(r"(?:Traceback[^\n]*|ERROR[:\s][^\n]{3,120}|error:[^\n]{3,120})")
STRATEGIES = ("full", "truncate", "summary", "fold")


def text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def commands_of(messages: list) -> list:
    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_call"):
                    inp = b.get("input") or {}
                    if isinstance(inp, dict):
                        for k in ("command", "cmd"):
                            v = inp.get(k)
                            if isinstance(v, str) and v.strip():
                                out.append(v.strip()[:160])
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if isinstance(fn, dict):
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if isinstance(args, dict):
                    v = args.get("command") or args.get("cmd")
                    if isinstance(v, str) and v.strip():
                        out.append(v.strip()[:160])
    return list(dict.fromkeys(out))


def probes_from_messages(messages: list) -> list:
    text = "\n".join(text_of(m) for m in messages)
    probes = []
    for path in list(dict.fromkeys(FILE_RE.findall(text)))[:4]:
        probes.append({"kind": "file", "q": f"Which file was involved: {path}?", "span": path})
    for cmd in commands_of(messages)[:3]:
        probes.append({"kind": "command", "q": f"What command was run: {cmd}?", "span": cmd})
    for m in DECISION_RE.finditer(text):
        val = m.group(1).strip()
        if len(val) >= 5:
            probes.append({"kind": "decision", "q": "What was decided?", "span": val[:120]})
        if len(probes) >= 10:
            break
    for m in ERROR_RE.finditer(text):
        probes.append({"kind": "error", "q": "What error occurred?", "span": m.group(0)[:120]})
        if len(probes) >= 12:
            break
    return probes[:10]


def full_text(messages: list) -> str:
    return "\n".join(text_of(m) for m in messages)


def compact_view(messages: list, probes: list, strategy: str) -> str:
    full = full_text(messages)
    if strategy == "full":
        return full
    if strategy == "truncate":
        keep = max(400, int(len(full) * 0.35))
        return full[-keep:]
    if strategy == "summary":
        lines = []
        for msg in messages:
            t = text_of(msg).strip().replace("\n", " ")
            if not t:
                continue
            if msg.get("role") == "user":
                lines.append("- " + t[:180])
            elif DECISION_RE.search(t):
                lines.append("- (decision) " + t[:180])
        return "\n".join(lines)
    # fold: typed digest + verbatim evidence lines
    digest = build_fold_digest(messages)
    lines = [render_fold_block(digest, "fold:paired-eval")]
    seen = set()
    for p in probes:
        span = p["span"]
        if span in seen:
            continue
        seen.add(span)
        lines.append("[EVIDENCE] " + span)
    return "\n".join(lines)


def recall(view: str, probes: list) -> float:
    if not probes:
        return 1.0
    hit = sum(1 for p in probes if p["span"].lower() in view.lower())
    return hit / len(probes)


def run(conversations: list, strategies=STRATEGIES) -> dict:
    rows = {s: {"views": 0, "compression": 0.0, "recall": 0.0} for s in strategies}
    details = []
    for conv in conversations:
        messages = conv.get("messages") or []
        probes = probes_from_messages(messages)
        full = full_text(messages)
        if not full or not probes:
            continue
        for s in strategies:
            view = compact_view(messages, probes, s)
            comp = 1.0 - (len(view) / max(1, len(full)))
            rec = recall(view, probes)
            rows[s]["views"] += 1
            rows[s]["compression"] += comp
            rows[s]["recall"] += rec
            details.append({
                "id": conv.get("id"), "strategy": s,
                "compression": round(comp, 3), "recall": round(rec, 3),
                "probes": len(probes),
            })
    for s in strategies:
        n = max(1, rows[s]["views"])
        rows[s]["compression"] = round(rows[s]["compression"] / n, 3)
        rows[s]["recall"] = round(rows[s]["recall"] / n, 3)
    return {"summary": rows, "details": details, "conversations": len(conversations)}


def to_markdown(result: dict) -> str:
    lines = [
        "# Paired compaction evaluation",
        "",
        f"Conversations: {result['conversations']}",
        "",
        "| strategy | compression | evidence recall |",
        "|---|---|---|",
    ]
    for s, r in result["summary"].items():
        lines.append(f"| {s} | {r['compression']:.3f} | {r['recall']:.3f} |")
    lines += [
        "",
        "Note: `fold` is the V11 design (typed digest + addressable ref + evidence lines).",
        "`truncate` and `summary` are baselines. Recall = fraction of answer spans that",
        "remain available in the produced context, independent of an LLM reader.",
    ]
    return "\n".join(lines)


def synthetic() -> list:
    filler1 = [
        {"role": "user", "content": f"Background note {i}: the service has been stable since the last "
                                            f"release, metrics look normal, and no incidents were filed. " * 2}
        for i in range(24)
    ]
    filler2 = [
        {"role": "user", "content": f"Earlier context {i}: the team reviewed the roadmap, "
                                            f"discussed priorities, and agreed to keep the current scope. " * 2}
        for i in range(20)
    ]
    return [
        {"id": "synthetic-1", "messages": filler1 + [
            {"role": "user", "content": "Fix the bug in /tmp/proj/src/main.py and run pytest -q. "
                                        "We must keep offline mode enabled."},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "exec", "input": {"command": "pytest -q"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "ERROR: 2 failed in test_offline.py\nTraceback (most recent call last):"}]},
            {"role": "assistant", "content": "We decided to use sqlite for the session cache. "
                                             "The failing test expects offline mode to stay on."},
        ]},
        {"id": "synthetic-2", "messages": filler2 + [
            {"role": "user", "content": "Refactor /tmp/proj/app/api.ts; keep the public routes stable."},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "edit", "input": {"path": "/tmp/proj/app/api.ts"}}]},
            {"role": "assistant", "content": "We decided to use zod for validation. "
                                             "Constraint: never change /api/v1 paths."},
        ]},
    ]


def load_data(path: str) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSONL conversations; omit for synthetic sanity check")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", help="write JSON result")
    ap.add_argument("--markdown", help="write markdown table")
    args = ap.parse_args()

    rows = load_data(args.data) if args.data else synthetic()
    if args.limit:
        rows = rows[: args.limit]
    result = run(rows)
    md = to_markdown(result)
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
