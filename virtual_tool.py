"""Transparent virtual tool injection for reversible unfolding (V12 Phase 2).

The gateway advertises a stable `neverfull_restore_context` tool. When the model
calls it, the proxy intercepts the call, fetches the folded messages from the
archive, appends a tool result, and re-issues the upstream request internally.
The client never sees the virtual tool.
"""

from __future__ import annotations

import json

TOOL_NAME = "neverfull_restore_context"

RESTORE_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Retrieve original messages hidden inside a <folded_context> block. "
            "Use when the summary lacks a detail you need (error stack, file lines, exact command)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fold_ref": {"type": "string", "description": "Reference such as fold:6f775980e76a"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["fold_ref"],
        },
    },
}


def inject_restore_tool(body: dict) -> dict:
    tools = body.get("tools")
    if not isinstance(tools, list):
        tools = []
    if not any(isinstance(t, dict) and (t.get("function") or {}).get("name") == TOOL_NAME for t in tools):
        tools = list(tools) + [RESTORE_TOOL]
    body["tools"] = tools
    return body


def extract_restore_calls(data: dict) -> list:
    """Return [{id, args}] for virtual-tool calls in an OpenAI-format response."""
    out = []
    try:
        for choice in (data or {}).get("choices", []) or []:
            msg = choice.get("message") or {}
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                if fn.get("name") != TOOL_NAME:
                    continue
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                out.append({"id": call.get("id") or "", "args": args})
    except Exception:
        pass
    return out


def build_restore_messages(calls: list, archive, tenant: str = "default") -> list:
    """Turn virtual calls into OpenAI tool messages with restored content."""
    messages = []
    for call in calls:
        ref = str((call.get("args") or {}).get("fold_ref") or "")
        offset = int((call.get("args") or {}).get("offset") or 0)
        limit = min(200, max(1, int((call.get("args") or {}).get("limit") or 50)))
        payload = {"ref": ref, "found": False}
        try:
            item = archive.get(ref, tenant_id=tenant) if archive is not None else None
            if item:
                msgs = item.get("messages") or []
                payload = {
                    "ref": ref,
                    "found": True,
                    "total": len(msgs),
                    "offset": offset,
                    "messages": msgs[offset:offset + limit],
                }
            else:
                payload["error"] = "fold ref not found"
        except Exception as e:  # noqa: BLE001
            payload["error"] = f"restore failed: {e}"
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id") or "",
            "content": json.dumps(payload, ensure_ascii=False),
        })
    return messages


def parse_stream_restore_calls(raw: bytes) -> list:
    """Extract virtual-tool calls from an OpenAI-format SSE stream."""
    calls = {}
    try:
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                evt = json.loads(payload)
            except Exception:
                continue
            for ch in evt.get("choices", []) or []:
                delta = ch.get("delta") or {}
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
    except Exception:
        pass
    out = []
    for _, slot in sorted(calls.items()):
        if slot["name"] != TOOL_NAME:
            continue
        try:
            args = json.loads(slot["arguments"] or "{}")
        except Exception:
            args = {}
        out.append({"id": slot["id"] or "", "args": args})
    return out
