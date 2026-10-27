"""Cache-aware request alignment (V12 Phase 1).

- Deterministic tool ordering (stable prefix bytes).
- OpenAI-compatible `prompt_cache_key` derived from tenant+session.
- Anthropic `cache_control` breakpoints (up to 4): last tool, system end,
  and the last two message blocks.

Pure functions; no network or global state.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    if isinstance(tool.get("function"), dict):
        return str(tool["function"].get("name") or "")
    return str(tool.get("name") or tool.get("type") or "")


def sort_tools(body: dict) -> dict:
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        body["tools"] = sorted(tools, key=_tool_name)
    return body


def session_cache_key(tenant: str, session_id: str) -> str:
    raw = f"{tenant or 'default'}\x00{session_id or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def apply_openai_cache(body: dict, tenant: str, session_id: str) -> dict:
    """Sort tools and set prompt_cache_key without touching message content."""
    sort_tools(body)
    if session_id and not body.get("prompt_cache_key"):
        body["prompt_cache_key"] = session_cache_key(tenant, session_id)
    return body


def _mark_last_block(content: Any) -> Any:
    if isinstance(content, str) and content:
        return [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    if isinstance(content, list) and content:
        blocks = list(content)
        last = blocks[-1]
        if isinstance(last, dict) and last.get("type") == "text":
            last = dict(last)
            last["cache_control"] = {"type": "ephemeral"}
            blocks[-1] = last
        return blocks
    return content


def apply_anthropic_cache(body: dict, max_breakpoints: int = 4) -> dict:
    """Place up to 4 ephemeral cache breakpoints on stable boundaries."""
    used = 0

    tools = body.get("tools")
    if isinstance(tools, list) and tools and used < max_breakpoints:
        tools = list(tools)
        last = dict(tools[-1])
        last["cache_control"] = {"type": "ephemeral"}
        tools[-1] = last
        body["tools"] = tools
        used += 1

    system = body.get("system")
    if system and used < max_breakpoints:
        if isinstance(system, str):
            body["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(system, list) and system:
            blocks = list(system)
            last = dict(blocks[-1])
            if last.get("type") == "text":
                last["cache_control"] = {"type": "ephemeral"}
            blocks[-1] = last
            body["system"] = blocks
        used += 1

    msgs = body.get("messages")
    if isinstance(msgs, list):
        for idx in range(len(msgs) - 1, max(-1, len(msgs) - 4), -1):
            if used >= max_breakpoints:
                break
            msg = dict(msgs[idx])
            msg["content"] = _mark_last_block(msg.get("content"))
            msgs[idx] = msg
            used += 1

    return body
