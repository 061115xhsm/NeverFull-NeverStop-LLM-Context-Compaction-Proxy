"""
三层记忆引擎模块(memory_engine.py)
====================================
借鉴人类记忆规律实现三层记忆架构 + 记忆衰减与遗忘机制:

- 工作记忆(working):最近 N 轮对话,原文完整保留
- 短期记忆(short):本轮会话已压缩的结构化摘要
- 长期记忆(long):跨会话抽取的实体、知识、用户偏好

支持按查询主动检索、按预算动态注入、基于访问频率/重要性/时间衰减
的权重评分与遗忘机制。纯标准库实现,JSON 原子持久化。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── 记忆项 ──────────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    """单条记忆。"""
    text: str
    importance: float = 0.5          # 0-1 重要性
    access_count: int = 0            # 访问频率
    last_access_ts: float = 0.0      # 最近访问时间戳
    created_ts: float = 0.0          # 创建时间戳
    permanent: bool = False          # 永久保留(核心决策/用户偏好)
    category: str = "general"        # 分类:goal/decision/error/file/constraint/insight/preference

    def __post_init__(self) -> None:
        now = time.time()
        if not self.created_ts:
            self.created_ts = now
        if not self.last_access_ts:
            self.last_access_ts = now


# ── 三层记忆 ────────────────────────────────────────────────────────

class ThreeLayerMemory:
    """
    三层记忆:工作记忆(原文)→ 短期记忆(摘要)→ 长期记忆(知识)。

    - add_to_working: 加入工作记忆(原始消息)
    - promote_to_short: 摘要提升到短期记忆
    - promote_to_long: 结构化知识提升到长期记忆
    - recall: 按查询检索(相关性 + 重要性排序)
    - inject_for_prompt: 按预算动态注入(预算充足多注入,紧张只留最高相关)
    """

    def __init__(self) -> None:
        self.working: List[dict] = []            # 最近消息(原文)
        self.short: List[MemoryItem] = []        # 短期记忆
        self.long: List[MemoryItem] = []         # 长期记忆

    # ── 写入 ──
    def add_to_working(self, messages: List[dict], max_working: int = 20) -> None:
        self.working.extend(messages)
        if len(self.working) > max_working:
            self.working = self.working[-max_working:]

    def promote_to_short(self, text: str, importance: float = 0.5) -> None:
        self.short.append(MemoryItem(text=text, importance=importance, category="short-summary"))

    def promote_to_long(
        self,
        text: str,
        importance: float = 0.6,
        category: str = "knowledge",
        permanent: bool = False,
    ) -> None:
        self.long.append(MemoryItem(
            text=text, importance=importance, category=category, permanent=permanent,
        ))

    # ── 检索 ──
    def _score_item(self, item: MemoryItem, query_tokens: set) -> float:
        """相关性 + 重要性综合分。"""
        item_tokens = set(self._tokenize(item.text))
        overlap = len(query_tokens & item_tokens) / len(query_tokens) if query_tokens else 0.0
        return 0.6 * overlap + 0.4 * item.importance

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        import re
        return re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", (text or "").lower())

    def recall(self, query: str, k: int = 5) -> List[MemoryItem]:
        """从短期+长期记忆检索 top-k(按综合分排序)。"""
        q_tokens = set(self._tokenize(query))
        pool = self.short + self.long
        scored = [(self._score_item(it, q_tokens), it) for it in pool]
        scored.sort(key=lambda x: x[0], reverse=True)
        for _, it in scored[:k]:
            it.access_count += 1
            it.last_access_ts = time.time()
        return [it for _, it in scored[:k]]

    def inject_for_prompt(self, query: str, budget_chars: int = 800) -> str:
        """
        按预算动态注入:预算充足注入更多高相关项,紧张只留最高相关。
        """
        items = self.recall(query, k=10)
        if not items:
            return ""
        parts: List[str] = []
        used = 0
        for it in items:
            snippet = f"- [{it.category}] {it.text}"
            if used + len(snippet) > budget_chars:
                break
            parts.append(snippet)
            used += len(snippet)
        if not parts:
            return ""
        return "## RELEVANT MEMORY\n" + "\n".join(parts)

    # ── 持久化 ──
    def save(self, path: str) -> None:
        data = {
            "working": self.working,
            "short": [vars(it) for it in self.short],
            "long": [vars(it) for it in self.long],
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.working = data.get("working", [])
            self.short = [MemoryItem(**it) for it in data.get("short", [])]
            self.long = [MemoryItem(**it) for it in data.get("long", [])]
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass


# ── 记忆衰减与遗忘 ──────────────────────────────────────────────────

class MemoryDecay:
    """
    记忆权重评分与遗忘机制。

    weight = importance * (1 - decay_rate) ^ (hours_since_last_access / 24)

    - 低权重记忆自动降级(short→long 或标记待遗忘)
    - permanent 项永不忘
    """

    def __init__(self, decay_rate: float = 0.1) -> None:
        self.decay_rate = decay_rate

    def weight(self, item: MemoryItem, now: Optional[float] = None) -> float:
        now = now or time.time()
        hours = max(0.0, (now - item.last_access_ts) / 3600.0)
        if item.permanent:
            return item.importance  # 永久项不衰减
        return item.importance * ((1.0 - self.decay_rate) ** (hours / 24.0))

    def decay(self, items: List[MemoryItem]) -> List[MemoryItem]:
        """
        计算各项当前权重并返回需要降级/遗忘的项(权重低于 0.2 且非永久)。
        """
        now = time.time()
        to_forget: List[MemoryItem] = []
        for it in items:
            if it.permanent:
                continue
            if self.weight(it, now) < 0.2:
                to_forget.append(it)
        return to_forget

    def should_forget(self, item: MemoryItem, threshold: float = 0.2) -> bool:
        if item.permanent:
            return False
        return self.weight(item) < threshold

    def prune(self, items: List[MemoryItem], threshold: float = 0.2) -> List[MemoryItem]:
        """移除应遗忘的项,返回剩余列表。"""
        return [it for it in items if not self.should_forget(it, threshold)]
