"""
增量压缩深度优化模块(incremental_compaction.py)
================================================
CoMem 增强(路线图 #4):摘要分层迭代 + 滑动窗口。

- L2Layer:历史深度摘要(跨多次压缩累积,不随每次对话重算)
- L1Layer:新增对话层(仅压缩本轮新增,合并进 L2)
- IncrementalCompactor:
  - 滑动窗口:窗口内保留原文,窗口外才做分级压缩
  - 每次仅压缩 L1 并更新 L2,无需全量重压缩
  - 与现有 prior_summary 增量机制互补,提供分层实现

纯标准库实现。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── 分层结构 ────────────────────────────────────────────────────────

@dataclass
class L2Layer:
    """
    历史深度摘要层(L2):

    - summary: 累积的历史摘要(跨多次压缩)
    - msg_count: 已并入摘要的消息数
    - updated_ts: 最后更新时间
    """
    summary: str = ""
    msg_count: int = 0
    updated_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary, "msg_count": self.msg_count,
                "updated_ts": self.updated_ts}


@dataclass
class L1Layer:
    """
    新增对话层(L1):

    - messages: 自上次压缩以来的新增消息(未压缩原文)
    """
    messages: List[dict] = field(default_factory=list)

    def append(self, messages: List[dict]) -> None:
        self.messages.extend(messages)


# ── 增量压缩器 ──────────────────────────────────────────────────────

class IncrementalCompactor:
    """
    摘要分层迭代 + 滑动窗口的增量压缩器。

    用法:
        c = IncrementalCompactor(summarize_fn=my_summarize, window_size=6)
        c.add_messages(new_msgs)          # 追加新消息
        c.maybe_compact(force=True)       # 压缩 L1 并入 L2
        state = c.snapshot()              # 持久化状态
        c.restore(state)                  # 恢复状态
    """

    def __init__(
        self,
        summarize_fn: Callable[[List[dict], str], Optional[str]],
        window_size: int = 6,
        min_new_messages: int = 2,
        l2_cap_chars: int = 4000,
    ) -> None:
        """
        Args:
            summarize_fn: 摘要函数 fn(messages, previous_summary) -> summary str
            window_size: 滑动窗口内保留的原文轮次(窗口内不压缩)
            min_new_messages: 至少新增这么多消息才触发压缩
            l2_cap_chars: L2 摘要上限字符(超出截断,防无限膨胀)
        """
        self.summarize_fn = summarize_fn
        self.window_size = window_size
        self.min_new_messages = min_new_messages
        self.l2_cap_chars = l2_cap_chars
        self.l1 = L1Layer()
        self.l2 = L2Layer()
        self._compactions: int = 0

    # ── 写入 ──
    def add_messages(self, messages: List[dict]) -> None:
        """追加新消息到 L1。"""
        self.l1.append(messages)

    # ── 滑动窗口切分 ──
    def _split_window(self) -> tuple:
        """
        将 L1 消息按滑动窗口切分:窗口内保留原文,窗口外进入待压缩集。

        Returns: (window_messages, to_compact_messages)
        """
        if len(self.l1.messages) <= self.window_size:
            return self.l1.messages, []
        window = self.l1.messages[-self.window_size:]          # 最近窗口原文
        to_compact = self.l1.messages[: -self.window_size]     # 窗口外待压缩
        return window, to_compact

    # ── 压缩 ──
    def maybe_compact(self, force: bool = False) -> bool:
        """
        执行一次增量压缩:

        - 窗口外消息交给 summarize_fn,与现有 L2 摘要合并
        - 压缩完成后 L1 仅保留窗口原文,L2 更新

        Returns: 是否执行了压缩
        """
        if not force and len(self.l1.messages) < self.min_new_messages:
            return False

        window, to_compact = self._split_window()
        if not to_compact and not force:
            return False

        if to_compact:
            new_summary = self.summarize_fn(to_compact, self.l2.summary)
            if new_summary:
                if self.l2.summary:
                    # 分层合并:L2 = 旧 L2 + 新 L1 摘要(compact 形式)
                    merged = f"{self.l2.summary}\n[NEW] {new_summary}"
                else:
                    merged = new_summary
                self.l2.summary = merged[: self.l2_cap_chars]
                self.l2.msg_count += len(to_compact)
                self.l2.updated_ts = time.time()

        # L1 重置为窗口原文
        self.l1.messages = list(window)
        self._compactions += 1
        return True

    # ── 读取 ──
    def build_context(self, query_hint: str = "") -> List[dict]:
        """
        构建注入上下文的最终消息列表:

        - L2 历史摘要作为 system 前缀
        - L1 窗口原文保留
        """
        ctx: List[dict] = []
        if self.l2.summary:
            ctx.append({
                "role": "system",
                "content": f"[COMPRESSED HISTORY]\n{self.l2.summary}",
            })
        ctx.extend(self.l1.messages)
        return ctx

    @property
    def compactions(self) -> int:
        return self._compactions

    # ── 持久化 ──
    def snapshot(self) -> Dict[str, Any]:
        """返回可持久化的状态快照。"""
        return {
            "l2": self.l2.to_dict(),
            "l1_count": len(self.l1.messages),
            "compactions": self._compactions,
            "window_size": self.window_size,
        }

    def restore(self, state: Dict[str, Any]) -> None:
        """从快照恢复 L2(不恢复 L1 原文,仅计数)。"""
        l2 = state.get("l2", {})
        self.l2.summary = l2.get("summary", "")
        self.l2.msg_count = l2.get("msg_count", 0)
        self.l2.updated_ts = l2.get("updated_ts", 0.0)
        self._compactions = state.get("compactions", 0)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)
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
                self.restore(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass


# ── 便捷工厂 ────────────────────────────────────────────────────────

def default_summarize(messages: List[dict], previous_summary: str = "") -> str:
    """
    默认摘要实现(无 LLM 时的启发式):

    - 每条消息取前 120 字符
    - 保留角色标签
    """
    parts = [f"{m.get('role','?')}: {str(m.get('content',''))[:120]}" for m in messages]
    if previous_summary:
        parts.insert(0, f"[PREV] {previous_summary[:300]}")
    return "\n".join(parts)


def create_incremental_compactor(
    summarize_fn: Optional[Callable[[List[dict], str], Optional[str]]] = None,
    window_size: int = 6,
) -> IncrementalCompactor:
    """创建增量压缩器;未提供摘要函数时使用默认启发式。"""
    return IncrementalCompactor(
        summarize_fn=summarize_fn or default_summarize,
        window_size=window_size,
    )
