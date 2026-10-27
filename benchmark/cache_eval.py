#!/usr/bin/env python3
"""Cache-economics measurement: head vs tail state frame (V12 Phase 1).

Sends a large static prefix plus a small per-turn state frame to the upstream
model. In `head`, the state is prepended to the system prompt (breaks prefix);
in `tail`, it is appended as a trailing user message (prefix stays stable).
Records provider-reported cached_tokens, prompt_tokens and latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path

CFG = "/home/qq/.openclaw/openclaw.json"
PREFIX = ("The quick brown fox jumps over the lazy dog. " * 900)[:30000]
TURNS = 5


def endpoint():
    cfg = json.load(open(CFG, encoding="utf-8"))
    p = cfg["models"]["providers"]["tierpool"]
    return p["baseUrl"].rstrip("/"), p["apiKey"]


def chat(messages, model="tierflow", max_tokens=8):
    base, key = endpoint()
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    usage = d.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "cached_tokens": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        "latency_s": round(time.monotonic() - t0, 3),
    }


def build(layout: str, turn: int):
    state = f"<state_snapshot>turn={turn} file=/tmp/a.py ts={turn}</state_snapshot>"
    question = f"Reply OK. turn={turn}"
    if layout == "head":
        return [{"role": "system", "content": PREFIX + "\n" + state},
                {"role": "user", "content": question}]
    if layout == "tail":
        return [{"role": "system", "content": PREFIX},
                {"role": "user", "content": question},
                {"role": "user", "content": state}]
    return [{"role": "system", "content": PREFIX},
            {"role": "user", "content": question}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="research/eval/2026-10-27/raw")
    ap.add_argument("--markdown", default="")
    ap.add_argument("--model", default="tierflow")
    args = ap.parse_args()

    rows = {}
    for layout in ("head", "tail", "control"):
        samples = []
        for turn in range(1, TURNS + 1):
            try:
                usage = chat(build(layout, turn), model=args.model)
            except Exception as e:
                usage = {"error": str(e)}
            samples.append({"turn": turn, **usage})
        rows[layout] = samples

    summary = {}
    for layout, samples in rows.items():
        ok = [s for s in samples if "error" not in s]
        if not ok:
            summary[layout] = {"error": "all calls failed"}
            continue
        ratios = [s["cached_tokens"] / max(1, s["prompt_tokens"]) for s in ok]
        summary[layout] = {
            "turns": len(ok),
            "avg_prompt_tokens": round(statistics.mean(s["prompt_tokens"] for s in ok), 1),
            "avg_cached_tokens": round(statistics.mean(s["cached_tokens"] for s in ok), 1),
            "cache_hit_ratio": round(statistics.mean(ratios), 3),
            "avg_latency_s": round(statistics.mean(s["latency_s"] for s in ok), 3),
        }

    md = ["# Cache layout measurement", "",
          f"Model: {args.model}; prefix chars: {len(PREFIX)}; turns: {TURNS}", "",
          "| layout | avg prompt | avg cached | hit ratio | avg latency |",
          "|---|---|---|---|---|"]
    for layout, s in summary.items():
        if "error" in s:
            md.append(f"| {layout} | - | - | - | - |")
        else:
            md.append(f"| {layout} | {s['avg_prompt_tokens']} | {s['avg_cached_tokens']} | "
                      f"{s['cache_hit_ratio']} | {s['avg_latency_s']}s |")
    md += ["", "`head` prepends a changing state frame to the system prompt; `tail` appends it as a trailing message."]
    print("\n".join(md))

    out = {"model": args.model, "prefix_chars": len(PREFIX), "turns": TURNS,
           "samples": rows, "summary": summary}
    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    Path(args.outdir, "cache_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
