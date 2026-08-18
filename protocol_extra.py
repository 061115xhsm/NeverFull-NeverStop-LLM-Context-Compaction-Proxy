"""
协议兼容深化模块(protocol_extra.py)
======================================
补全协议边缘场景与主流 Agent 框架适配,供压缩代理调用:

- ResponsesAPIAdapter:OpenAI Responses API(简化为 chat completions 适配)
- LangChainAdapter / LlamaIndexAdapter:框架请求体标准化
- reasoning_content 透传辅助

纯标准库实现。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


# ── OpenAI Responses API 适配 ───────────────────────────────────────

class ResponsesAPIAdapter:
    """
    将 OpenAI Responses API 请求体转换为代理可处理的 chat completions 结构,
    响应时再转回 Responses API 风格。

    Responses API 请求:
      {"model": ..., "input": "text" | [{"role","content"}], "stream": bool}
    """

    @staticmethod
    def to_chat_completions(body: Dict[str, Any]) -> Dict[str, Any]:
        out = {"model": body.get("model", "")}
        inp = body.get("input", "")
        if isinstance(inp, str):
            out["messages"] = [{"role": "user", "content": inp}]
        elif isinstance(inp, list):
            messages = []
            for item in inp:
                if isinstance(item, str):
                    messages.append({"role": "user", "content": item})
                elif isinstance(item, dict):
                    messages.append({
                        "role": item.get("role", "user"),
                        "content": item.get("content", ""),
                    })
            out["messages"] = messages
        if body.get("stream"):
            out["stream"] = True
        return out

    @staticmethod
    def from_chat_completions(resp: Dict[str, Any]) -> Dict[str, Any]:
        """将 chat completions 响应转回 Responses API 风格。"""
        choices = resp.get("choices", [])
        text = ""
        if choices:
            delta = choices[0].get("message") or choices[0].get("delta") or {}
            text = delta.get("content", "")
        return {
            "id": resp.get("id", ""),
            "object": "response",
            "output_text": text,
            "usage": resp.get("usage", {}),
        }


# ── Agent 框架适配 ──────────────────────────────────────────────────

class LangChainAdapter:
    """LangChain ChatOpenAI 请求体 → 标准 chat completions。"""

    @staticmethod
    def normalize(messages: List[dict]) -> List[dict]:
        """LangChain 消息可能含额外字段,只保留 role/content/tool 相关。"""
        out: List[dict] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "tool":
                out.append({"role": "tool", "content": content,
                            "tool_call_id": m.get("tool_call_id", "")})
            else:
                out.append({"role": role, "content": content})
        return out


class LlamaIndexAdapter:
    """LlamaIndex 消息结构适配。"""

    @staticmethod
    def normalize(messages: List[dict]) -> List[dict]:
        """LlamaIndex 可能用 'content' 为列表块,展开为字符串。"""
        out: List[dict] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        text_parts.append(str(block.get("text", "")))
                    else:
                        text_parts.append(str(block))
                content = "\n".join(text_parts)
            out.append({"role": role, "content": content})
        return out


# ── reasoning_content 透传 ──────────────────────────────────────────

class ReasoningPassthrough:
    """
    推理内容(reasoning_content / thinking)透传与剥离辅助。

    - strip:从消息中移除推理块(用于压缩时节省 token)
    - restore_placeholder:将推理块替换为占位标记,便于透传
    """

    @staticmethod
    def strip(messages: List[dict]) -> List[dict]:
        out: List[dict] = []
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, list):
                new_blocks = [b for b in c if not (isinstance(b, dict)
                              and b.get("type") in ("thinking", "reasoning"))]
                nm = dict(m)
                nm["content"] = new_blocks
                out.append(nm)
            elif isinstance(c, str) and ("<thinking>" in c or "reasoning" in c):
                nm = dict(m)
                nm["content"] = re.sub(r"<thinking>.*?</thinking>", "[thinking-stripped]", c, flags=re.S)
                out.append(nm)
            else:
                out.append(m)
        return out

    @staticmethod
    def placeholder(messages: List[dict]) -> List[dict]:
        """将推理块替换为 [REASONING] 占位。"""
        out: List[dict] = []
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, list):
                new_blocks = []
                for b in c:
                    if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning"):
                        new_blocks.append({"type": "text", "text": "[REASONING]"})
                    else:
                        new_blocks.append(b)
                nm = dict(m)
                nm["content"] = new_blocks
                out.append(nm)
            else:
                out.append(m)
        return out
