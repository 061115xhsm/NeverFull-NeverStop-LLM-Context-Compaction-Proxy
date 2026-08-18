"""
LongBench / BFCL / SWE-bench 适配器(benchmark/longbench_adapter.py)
====================================================================
提供评测集加载适配器接口。当前为接口定义 + 格式说明,真实数据集
需按各自官方格式放置后调用。

支持的评测集与预期格式:

1. LongBench(通用长上下文理解)
   - JSONL 每行: {"input": "...", "answers": [...], "all_classes": [...]}
   - load_longbench(path) 返回 [{"input":..., "answers":..., "query":...}]

2. BFCL(Berkeley Function Calling Leaderboard,工具调用)
   - JSON 数组,每项含 messages(含 tool_calls)与期望的 function call
   - 建议字段: {"messages": [...], "expected_function": "...", "query": "..."}

3. SWE-bench(代码任务)
   - 每条含 issue 描述与 patch: {"instance_id": "...", "problem_statement": "...", "patch": "..."}
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def load_longbench(path: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    加载 LongBench 格式评测集(JSONL)。

    兼容两种格式:
    1. 官方 LongBench:{"input": 问题, "context": 长文档, "answers": [...], ...}
       → 返回 {"input": context(长文档), "query": input(问题), "answers": [...]}
    2. 自建格式:{"input": 长文本, "query": ..., "answers": [...]}
       → 原样返回

    Args:
        path: JSONL 文件路径
        limit: 最多加载条数

    Returns:
        [{"input": str(长上下文), "query": str(问题), "answers": list}, ...]
    """
    items: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 官方 LongBench 格式:input=问题, context=长文档
            if "context" in d and d.get("context"):
                items.append({
                    "input": d.get("context", ""),
                    "query": d.get("input", ""),
                    "answers": d.get("answers", []),
                })
            else:
                items.append({
                    "input": d.get("input", ""),
                    "answers": d.get("answers", []),
                    "query": d.get("query", d.get("input", "")[:200]),
                })
    return items


def load_bfcl(path: str, limit: int = 20) -> List[Dict[str, Any]]:
    """加载 BFCL 风格工具调用评测集(JSON 数组)。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: List[Dict[str, Any]] = []
    for d in data[:limit]:
        out.append({
            "messages": d.get("messages", []),
            "expected_function": d.get("expected_function", d.get("function", "")),
            "query": d.get("query", d.get("instruction", "")),
        })
    return out


def load_swebench(path: str, limit: int = 20) -> List[Dict[str, Any]]:
    """加载 SWE-bench 风格代码任务评测集(JSON/JSONL)。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: List[Dict[str, Any]] = []
    items = data if isinstance(data, list) else list(data.values())
    for d in items[:limit]:
        out.append({
            "instance_id": d.get("instance_id", ""),
            "problem_statement": d.get("problem_statement", ""),
            "patch": d.get("patch", ""),
            "query": d.get("problem_statement", "")[:200],
        })
    return out
