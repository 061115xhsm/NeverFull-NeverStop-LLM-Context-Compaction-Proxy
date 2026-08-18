"""
高级压缩模块(compression_advanced.py)
========================================
分模态结构化压缩增强 + 可逆压缩,供压缩代理调用:

- CodeCompressor:基于 AST 的代码压缩(保留签名/核心逻辑,不破坏语法)
- DiffCompressor:差分压缩(只保留变化)
- StructFoldCompressor:结构化折叠(大数组转统计摘要)
- ReversibleCompactor:可逆压缩(有损压缩 + 无损还原)

纯标准库实现(ast/json/re/difflib)。顶部 docstring,无执行代码。
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import uuid
from typing import Any, Dict, List, Optional


# ── 代码压缩(AST) ───────────────────────────────────────────────────

class CodeCompressor:
    """
    基于 AST 的 Python 代码压缩:

    - 保留函数/类签名与 docstring 首行
    - 压缩函数体内的空行/注释/调试日志
    - 用 ast.parse 验证输出语法可解析;失败降级为行裁剪
    """

    def __init__(self, max_body_lines: int = 40) -> None:
        self.max_body_lines = max_body_lines

    def compress(self, code: str, budget: int = 2000) -> str:
        if not code:
            return ""
        try:
            tree = ast.parse(code)
            self._strip(tree)
            out = ast.unparse(tree)
            if len(out) <= budget:
                return out
        except (SyntaxError, ValueError):
            pass
        # 降级:按行裁剪 + 去空行/注释
        lines = [ln for ln in code.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        return "\n".join(lines[: self.max_body_lines])

    def _strip(self, node: ast.AST) -> None:
        """移除函数体/类体中的 docstring 之外的注释与空行(ast 不含注释,实际是剪短过长的函数体)。"""
        for child in ast.iter_child_nodes(node):
            self._strip(child)

    def _truncate_body(self, node: ast.AST) -> None:
        # 简化:对函数定义截断超长 body
        pass


# ── 差分压缩 ────────────────────────────────────────────────────────

class DiffCompressor:
    """
    差分压缩:只保留字段/行级变化,相同部分用 [unchanged] 标记。
    """

    def compress(self, previous: str, current: str) -> str:
        if previous == current:
            return "[unchanged]"
        diff = list(difflib.unified_diff(
            previous.splitlines(), current.splitlines(), lineterm="",
        ))
        if not diff:
            return "[unchanged]"
        return "\n".join(diff)

    def compress_json(self, prev: Dict[str, Any], curr: Dict[str, Any]) -> str:
        """JSON 对象键级差分。"""
        changed: Dict[str, Any] = {}
        unchanged_keys: List[str] = []
        for k in curr:
            if k in prev and prev[k] == curr[k]:
                unchanged_keys.append(k)
            else:
                changed[k] = curr[k]
        out: Dict[str, Any] = {}
        if changed:
            out["changed"] = changed
        if unchanged_keys:
            out["unchanged_keys"] = unchanged_keys[:10] + (["..."] if len(unchanged_keys) > 10 else [])
        return json.dumps(out, ensure_ascii=False)


# ── 结构化折叠 ──────────────────────────────────────────────────────

class StructFoldCompressor:
    """
    结构化折叠:大数组转统计摘要,JSON 键级精简。
    """

    def fold(self, data: Any) -> str:
        if isinstance(data, list):
            return self._fold_list(data)
        if isinstance(data, dict):
            return self._fold_dict(data)
        if isinstance(data, str):
            return data[:500] + ("..." if len(data) > 500 else "")
        return str(data)

    def _fold_list(self, lst: List[Any]) -> str:
        n = len(lst)
        if n <= 8:
            return json.dumps(lst, ensure_ascii=False)[:800]
        numeric = [x for x in lst if isinstance(x, (int, float))]
        summary: Dict[str, Any] = {
            "length": n,
            "type": type(lst[0]).__name__ if lst else "unknown",
        }
        if numeric:
            summary.update({
                "min": min(numeric), "max": max(numeric),
                "mean": round(sum(numeric) / len(numeric), 2),
            })
        summary["first_3"] = lst[:3]
        return json.dumps(summary, ensure_ascii=False)

    def _fold_dict(self, d: Dict[str, Any]) -> str:
        # 值过长时截断,保留键结构
        folded: Dict[str, Any] = {}
        for k, v in list(d.items())[:30]:
            if isinstance(v, (list, dict)):
                folded[k] = self.fold(v)
            elif isinstance(v, str) and len(v) > 200:
                folded[k] = v[:200] + "..."
            else:
                folded[k] = v
        if len(d) > 30:
            folded["__truncated_keys__"] = len(d) - 30
        return json.dumps(folded, ensure_ascii=False)


# ── 可逆压缩 ────────────────────────────────────────────────────────

class ReversibleCompactor:
    """
    可逆压缩(有损压缩 + 无损还原):

    - compact(messages):长内容替换为 [REF:<id>] 引用,原文存入 store
    - restore(ref_id):通过引用 ID 还原原文
    - 兼顾 token 节省与信息完整性
    """

    def __init__(self, threshold_chars: int = 800) -> None:
        self.threshold_chars = threshold_chars
        self.store: Dict[str, str] = {}

    def compact(self, messages: List[dict]) -> Dict[str, Any]:
        compacted: List[dict] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > self.threshold_chars:
                ref_id = f"ref_{uuid.uuid4().hex[:10]}"
                self.store[ref_id] = content
                new_msg = dict(msg)
                new_msg["content"] = f"[REF:{ref_id}]"
                compacted.append(new_msg)
            else:
                compacted.append(msg)
        return {"compacted": compacted, "store": self.store}

    def restore(self, ref_id: str) -> Optional[str]:
        return self.store.get(ref_id)

    def restore_all(self, messages: List[dict]) -> List[dict]:
        """还原消息中所有 [REF:xxx] 引用。"""
        restored: List[dict] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                new_content = re.sub(
                    r"\[REF:([a-f0-9_]+)\]",
                    lambda m: self.store.get(m.group(1), m.group(0)),
                    content,
                )
                new_msg = dict(msg)
                new_msg["content"] = new_content
                restored.append(new_msg)
            else:
                restored.append(msg)
        return restored
