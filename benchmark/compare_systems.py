#!/usr/bin/env python3
"""Head-to-head compaction benchmark: OpenClaw vs Hermes vs NeverFull V11.

Design
------
Same transcript, same summarizer model (local OpenAI-compatible endpoint).
Each system keeps its own policy/prompt:
  - openclaw: prompt + checkpoint sections + keepRecentTokens tail,
              extracted from openclaw 2026.10.5 dist (agent-core).
  - hermes:   the real ContextCompressor from the local Hermes install,
              run in its own venv (subprocess), aux LLM monkeypatched to the
              same endpoint.
  - neverfull: V11 pipeline (state snapshot + fold digest + sectioned summary
              + commitment/evidence probes).

Metrics: compression, evidence recall, stale-state handling, latency, token
cost, and a 4-question reader test on the compacted context.

Usage:
    python benchmark/compare_systems.py --quick          # validation run
    python benchmark/compare_systems.py                  # full run
    python benchmark/compare_systems.py --json out.json --markdown out.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folding import build_fold_digest, render_fold_block  # noqa: E402
from state_table import StateTable  # noqa: E402
from evidence import run_evidence_probes  # noqa: E402

CFG_PATH = "/home/qq/.openclaw/openclaw.json"
HERMES_PY = "/media/qq/文档/dot_hermes/hermes-agent/venv/bin/python"
HERMES_SITE = "/media/qq/文档/dot_hermes/hermes-agent/venv/lib/python3.11/site-packages"
HERMES_RUNNER = ROOT / "benchmark" / "hermes_runner.py"

# ── OpenClaw prompt text (extracted from openclaw 2026.10.5, agent-core) ──
OC_SYSTEM = (
    "You are a context summarization assistant. Your task is to read a conversation "
    "between a user and an AI assistant, then produce a structured summary following "
    "the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the conversation. "
    "ONLY output the structured summary."
)
OC_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]
### In Progress
- [ ] [Current work]
### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

NF_PROMPT = """You are a context summarization assistant. Summarize the conversation so another agent can continue the work.

Use exactly these sections:
### FACTS
Only facts established by the environment, tools, files, or the user.
### DECISIONS
Decisions made, with status (done/pending/reverted) and reason.
### OPEN
Unresolved questions, blockers, and the next concrete step.
### COMPRESSOR_INFERENCE
Your own inferences, clearly labelled.
### COMMITMENTS
Goals, constraints, preferences and exact identifiers/paths that must survive.

Preserve exact file paths, function names, and error messages. Do not invent facts."""


def endpoint():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    p = cfg["models"]["providers"]["tierpool"]
    return p["baseUrl"].rstrip("/"), p["apiKey"]


def chat(messages, max_tokens=1500, timeout=240, model="tierflow_pro"):
    base, key = endpoint()
    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"].get("content") or "", d.get("usage", {})


def est(text):
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return int(cjk / 1.5 + (len(text) - cjk) / 4.0)


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def transcript(messages):
    lines = []
    for m in messages:
        role = m.get("role", "?")
        t = text_of(m).strip()
        if t:
            lines.append(f"[{role}]\n{t}")
    return "\n\n".join(lines)


# ── fixture ──────────────────────────────────────────────────────────────
FILLER = (
    "Status update: the service stayed healthy, dashboards were reviewed, and no new "
    "incidents were filed. We compared the last two releases, checked the queue depth, "
    "and confirmed the nightly job completed without retries. Nothing here changes the "
    "task, but it is part of the working log."
)


def fixture(n_filler=120):
    msgs = [{"role": "system", "content": "You are a coding agent working in /tmp/proj."}]
    msgs.append({"role": "user", "content":
        "Fix the offline sync bug in /tmp/proj/src/core.py. Constraints: never change "
        "/api/v1 paths and always keep offline mode enabled. Start by reading the config."})
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "name": "read", "input": {"path": "/tmp/proj/config.yaml"}},
    ]})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": "legacy_timeout: 30\nmode: offline\napi_prefix: /api/v1"}]})
    for i in range(n_filler):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"Working log {i}: {FILLER}"})
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "name": "edit",
         "input": {"path": "/tmp/proj/config.yaml", "content": "timeout: 60"}},
    ]})
    msgs.append({"role": "user", "content": "We decided to use sqlite for the session cache."})
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "name": "exec", "input": {"command": "pytest -q"}},
    ]})
    msgs.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2",
         "content": "ERROR: 2 failed in test_offline.py\nTraceback (most recent call last): ..."}]})
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "name": "exec", "input": {"command": "npm test"}},
    ]})
    msgs.append({"role": "user", "content": "We decided to use zod for validation in /tmp/proj/app/api.ts."})
    msgs.append({"role": "user", "content":
        "TODO: migrate config.yaml to the new schema after tests pass. Continue with that next."})
    return msgs


GOLD = [
    "/tmp/proj/src/core.py", "/tmp/proj/config.yaml", "/tmp/proj/app/api.ts",
    "sqlite", "zod", "offline mode", "/api/v1", "pytest -q", "npm test",
    "test_offline.py", "migrate config.yaml",
]
QUESTIONS = [
    ("Which storage did we decide to use for the session cache?", "sqlite"),
    ("Which path prefix must never change?", "/api/v1"),
    ("Which command reproduces the failing offline test?", "pytest"),
    ("What is the pending task?", "migrate config.yaml"),
]


def stale_score(text):
    if "config.yaml" not in text:
        return 0.0
    if re.search(r"(stale|superseded|outdated|updated|now 60|timeout: 60|60)", text, re.I):
        return 1.0
    return 0.5


# ── adapters ─────────────────────────────────────────────────────────────
def run_openclaw(messages, keep_recent_tokens=None):
    total = sum(est(text_of(m)) for m in messages)
    if keep_recent_tokens is None:
        keep_recent_tokens = max(600, min(4000, total // 2))
    t0 = time.monotonic()
    body = transcript(messages)
    content, usage = chat([
        {"role": "system", "content": OC_SYSTEM},
        {"role": "user", "content": body + "\n\n" + OC_PROMPT},
    ], max_tokens=1500)
    # keep tail by token budget, cut at a user turn boundary
    kept, acc = [], 0
    for m in reversed(messages):
        t = est(text_of(m))
        if kept and acc + t > keep_recent_tokens and m.get("role") == "user":
            break
        kept.append(m)
        acc += t
        if acc >= keep_recent_tokens:
            break
    kept.reverse()
    out = content.strip() + "\n\n--- recent messages (verbatim) ---\n" + transcript(kept)
    return {
        "name": "openclaw",
        "text": out,
        "usage": usage,
        "seconds": round(time.monotonic() - t0, 2),
    }


def run_hermes(messages, context_length=16000, protect_last_n=20):
    tmp_in = ROOT / "benchmark" / ".hermes_in.json"
    tmp_out = ROOT / "benchmark" / ".hermes_out.json"
    payload = {
        "messages": messages,
        "context_length": context_length,
        "threshold": 0.5,
        "protect_last_n": protect_last_n,
        "current_tokens": sum(est(text_of(m)) for m in messages),
    }
    tmp_in.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    t0 = time.monotonic()
    env = os.environ.copy()
    env["PYTHONPATH"] = HERMES_SITE + ((":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [HERMES_PY, str(HERMES_RUNNER), str(tmp_in), str(tmp_out)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    if proc.returncode != 0:
        return {"name": "hermes", "text": "", "usage": {}, "seconds": 0,
                "error": (proc.stderr or proc.stdout)[-400:]}
    data = json.loads(tmp_out.read_text(encoding="utf-8"))
    out_msgs = data.get("messages") or messages
    return {
        "name": "hermes",
        "text": transcript(out_msgs),
        "usage": data.get("usage", {}),
        "seconds": round(time.monotonic() - t0, 2),
        "error": data.get("error"),
        "messages": out_msgs,
    }


def run_neverfull(messages, sid="compare"):
    import tempfile
    t0 = time.monotonic()
    state = StateTable(Path(tempfile.mkdtemp(prefix="nf-cmp-")) / "state.db")
    state.update(sid, messages)
    snapshot = state.render(sid)
    digest = build_fold_digest(messages, commitments=[])
    fold_block = render_fold_block(digest, "fold:compare")
    body = transcript(messages)
    content, usage = chat([
        {"role": "system", "content": NF_PROMPT},
        {"role": "user", "content": body},
    ], max_tokens=1500)
    ids = sorted(set(re.findall(r"/[\w/.\-]+\.\w{1,10}", body)))[:30]
    probes = run_evidence_probes(messages, content, [], ids)
    # V11 keeps recent turns verbatim (KEEP_RECENT_TURNS=6); mirror that policy.
    tail, turns = [], 0
    for m in reversed(messages):
        tail.append(m)
        if m.get("role") == "user":
            turns += 1
            if turns >= 6:
                break
    tail.reverse()
    out = (snapshot + "\n\n" + fold_block + "\n\n" + content
           + "\n\n--- recent messages (verbatim) ---\n" + transcript(tail))
    if probes["failures"]:
        out += "\n\n" + probes["note"]
    return {
        "name": "neverfull",
        "text": out,
        "usage": usage,
        "seconds": round(time.monotonic() - t0, 2),
        "evidence": probes,
    }


# ── evaluation ───────────────────────────────────────────────────────────
def evaluate(result, messages):
    body = result["text"]
    before = sum(est(text_of(m)) for m in messages)
    after = est(body)
    recall = sum(1 for g in GOLD if g.lower() in body.lower()) / len(GOLD)
    qa = []
    for q, expected in QUESTIONS:
        ans = ""
        try:
            ans, _ = chat([
                {"role": "system", "content": "Answer with the shortest exact phrase from the context (command, path, or task). Do not explain."},
                {"role": "user", "content": f"CONTEXT:\n{body[:24000]}\n\nQUESTION: {q}"},
            ], max_tokens=600, timeout=180, model="tierflow_pro")
        except Exception as e:
            ans = f"<error {e}>"
        ok = expected.lower() in ans.lower()
        qa.append({"q": q, "expected": expected, "answer": ans.strip()[:160], "ok": ok})
    return {
        "name": result["name"],
        "compression": round(1 - after / max(1, before), 3),
        "tokens_before": before,
        "tokens_after": after,
        "evidence_recall": round(recall, 3),
        "stale_score": stale_score(body),
        "latency_s": result.get("seconds", 0),
        "cost_tokens": int(result.get("usage", {}).get("prompt_tokens", 0))
                       + int(result.get("usage", {}).get("completion_tokens", 0)),
        "qa_score": round(sum(1 for x in qa if x["ok"]) / len(qa), 3),
        "qa": qa,
        "error": result.get("error"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args()

    msgs = fixture(8 if args.quick else 120)
    systems = [run_openclaw, run_hermes, run_neverfull]
    results = []
    for fn in systems:
        sys.stderr.write(f"running {fn.__name__}...\n")
        try:
            raw = fn(msgs)
        except Exception as e:
            raw = {"name": fn.__name__.replace("run_", ""), "text": "", "usage": {},
                   "seconds": 0, "error": f"{type(e).__name__}: {e}"}
        if not raw.get("text"):
            results.append({
                "name": raw["name"], "compression": 0, "tokens_before": 0, "tokens_after": 0,
                "evidence_recall": 0, "stale_score": 0, "latency_s": raw.get("seconds", 0),
                "cost_tokens": 0, "qa_score": 0, "qa": [], "error": raw.get("error"),
            })
            continue
        results.append(evaluate(raw, msgs))

    header = "| system | compression | evidence recall | stale | QA | latency | cost tokens |"
    rows = [header, "|---|---|---|---|---|---|---|"]
    for r in results:
        rows.append(
            f"| {r['name']} | {r['compression']:.3f} | {r['evidence_recall']:.3f} | "
            f"{r['stale_score']:.1f} | {r['qa_score']:.2f} | {r['latency_s']}s | {r['cost_tokens']} |"
        )
    md = "\n".join([
        "# Compaction systems comparison (2026-10-26)",
        "",
        f"Transcript: {len(msgs)} messages, ~{sum(est(text_of(m)) for m in msgs)} tokens. "
        "Same summarizer model for all three.",
        "",
        *rows,
        "",
        "Legend: evidence recall = fraction of 11 gold spans retained; stale = whether the "
        "edited config is marked current/stale; QA = 4 reader questions answered correctly.",
    ])
    print(md)
    for r in results:
        if r.get("error"):
            print(f"\n[{r['name']}] error: {r['error']}")
        for x in r.get("qa", []):
            print(f"  [{r['name']}] Q: {x['q']} -> {x['answer'][:80]!r} ({'OK' if x['ok'] else 'MISS'})")

    out = {"transcript_tokens": sum(est(text_of(m)) for m in msgs), "results": results}
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
