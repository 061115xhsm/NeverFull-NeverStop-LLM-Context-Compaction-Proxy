"""Span-level observation deduplication (V12 Phase 2/3).

Identical tool outputs (repeated pytest/git/read results) are replaced by a
lightweight reference stub; first occurrence stays verbatim.
"""

from __future__ import annotations

import hashlib
import json


def _text_of(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def dedupe_observations(messages: list, min_chars: int = 400) -> tuple[list, int]:
    """Return (messages, saved_chars). Only tool-ish long outputs are considered."""
    seen = {}
    out = []
    saved = 0
    for idx, msg in enumerate(messages or []):
        role = msg.get("role")
        text = _text_of(msg)
        is_observation = role in ("tool", "function") or role == "user"
        if not is_observation or len(text) < min_chars:
            out.append(msg)
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            ref = seen[digest]
            stub = f'<dup_stub ref="turn:{ref}" sha="{digest[:12]}"/>'
            saved += max(0, len(text) - len(stub))
            new_msg = dict(msg)
            if isinstance(msg.get("content"), list):
                new_msg["content"] = [{"type": "text", "text": stub}]
            else:
                new_msg["content"] = stub
            out.append(new_msg)
        else:
            seen[digest] = idx
            out.append(msg)
    return out, saved
