#!/usr/bin/env python3
"""Run Hermes' real ContextCompressor against a transcript.

Invoked as a subprocess with the Hermes venv python:
    /media/qq/文档/dot_hermes/hermes-agent/venv/bin/python benchmark/hermes_runner.py in.json out.json

Input:  {"messages": [...], "context_length": 16000, "threshold": 0.5, "protect_last_n": 20}
Output: {"messages": [...], "usage": {...}, "seconds": 12.3, "error": null}
"""

import json
import sys
import time
import urllib.request

HERMES_ROOT = "/media/qq/文档/dot_hermes/hermes-agent"
HERMES_SITE = "/media/qq/文档/dot_hermes/hermes-agent/venv/lib/python3.11/site-packages"
sys.path.insert(0, HERMES_SITE)
sys.path.insert(0, HERMES_ROOT)

CFG_PATH = "/home/qq/.openclaw/openclaw.json"


def _endpoint():
    cfg = json.load(open(CFG_PATH))
    p = cfg["models"]["providers"]["tierpool"]
    return p["baseUrl"].rstrip("/"), p["apiKey"]


def _chat(prompt, max_tokens=1600):
    base, key = _endpoint()
    body = json.dumps({
        "model": "tierflow_pro",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.load(r)
    text = d["choices"][0]["message"].get("content") or ""
    return text, d.get("usage", {})


def main():
    src, dst = sys.argv[1], sys.argv[2]
    payload = json.load(open(src, encoding="utf-8"))
    messages = payload["messages"]
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    started = time.monotonic()
    error = None
    out = messages

    try:
        import agent.context_compressor as cc
        from agent.context_compressor import ContextCompressor

        def fake_call_llm(**kwargs):
            msgs = kwargs.get("messages") or []
            prompt = "\n\n".join(
                m.get("content", "") if isinstance(m.get("content"), str) else ""
                for m in msgs
            )
            text, usage = _chat(prompt)
            for k in usage_total:
                usage_total[k] += int(usage.get(k, 0) or 0)
            return {
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": usage,
            }

        cc.call_llm = fake_call_llm
        comp = ContextCompressor(
            model="tierflow_pro",
            threshold_percent=payload.get("threshold", 0.5),
            protect_first_n=3,
            protect_last_n=payload.get("protect_last_n", 20),
            summary_target_ratio=0.2,
            quiet_mode=True,
            base_url=_endpoint()[0],
            api_key=_endpoint()[1],
            provider="openai",
            config_context_length=payload.get("context_length", 16000),
            max_tokens=2048,
            tail_mode="legacy",
        )
        out = comp.compress(messages, current_tokens=payload.get("current_tokens"), force=True)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        out = messages

    json.dump({
        "messages": out,
        "usage": usage_total,
        "seconds": round(time.monotonic() - started, 2),
        "error": error,
    }, open(dst, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
