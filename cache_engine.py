"""
多级缓存引擎模块(cache_engine.py)
====================================
多级缓存体系 + 预测式异步预压缩支持,供压缩代理调用:

- LRUCache:内存热点缓存(有界 LRU)
- MultiLevelCache:三级缓存(内存 LRU → SQLite 持久化 → 上游缓存断点提示)
- PredictivePrecompressor:预测式预压缩(基于消息数/长度预测触发)

纯标准库实现,SQLite 持久化。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional


# ── 内存 LRU 缓存 ───────────────────────────────────────────────────

class LRUCache:
    """有界 LRU 缓存(线程安全)。"""

    def __init__(self, capacity: int = 1024, ttl_seconds: float = 1800.0) -> None:
        self.capacity = capacity
        self.ttl = ttl_seconds
        self._data: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._data:
                return None
            value, ts = self._data[key]
            if time.time() - ts > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (value, time.time())
            self._data.move_to_end(key)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ── SQLite 持久化层 ─────────────────────────────────────────────────

class SqliteCacheStore:
    """SQLite 持久化缓存(跨进程/跨重启)。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, value TEXT, ts REAL)"
            )

    def get(self, key: str, ttl_seconds: float = 86400.0) -> Optional[str]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value, ts FROM cache WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, ts = row
        if time.time() - ts > ttl_seconds:
            with self._lock, sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM cache WHERE key=?", (key,))
            return None
        return value

    def put(self, key: str, value: str) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, ts) VALUES (?,?,?)",
                (key, value, time.time()),
            )

    def clear(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")


# ── 多级缓存 ────────────────────────────────────────────────────────

class MultiLevelCache:
    """
    多级缓存:内存 LRU(L1)→ SQLite 持久化(L2)→ 上游缓存断点提示(L3)。

    L3 不存储数据,只生成 cache_control 断点建议(配合 Anthropic
    Prompt Caching / OpenAI 缓存命中)。
    """

    def __init__(self, lru_capacity: int = 1024, db_path: str = "cache.sqlite") -> None:
        self.l1 = LRUCache(capacity=lru_capacity)
        self.l2 = SqliteCacheStore(db_path)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        v = self.l1.get(key)
        if v is not None:
            return v
        v = self.l2.get(key)
        if v is not None:
            self.l1.put(key, v)
            return v
        return None

    def put(self, key: str, value: str) -> None:
        self.l1.put(key, value)
        self.l2.put(key, value)

    def cache_control_breakpoints(self, messages: List[dict], breakpoint_every: int = 8) -> List[int]:
        """
        生成上游缓存断点建议:每 breakpoint_every 条消息插入 cache_control
        断点索引,最大化 Anthropic Prompt Caching / OpenAI 缓存命中。
        """
        indices: List[int] = []
        for i in range(breakpoint_every - 1, len(messages), breakpoint_every):
            indices.append(i)
        return indices

    def clear(self) -> None:
        self.l1.clear()
        self.l2.clear()


# ── 预测式预压缩 ────────────────────────────────────────────────────

class PredictivePrecompressor:
    """
    预测式异步预压缩:

    - 基于消息数/字符数预测即将触发阈值
    - 达到预测线时触发后台预压缩(此处返回建议,由调用方执行)
    """

    def __init__(self, threshold: float = 0.75, predict_window: int = 10) -> None:
        self.threshold = threshold          # 预测触发比例(低于实际 80% 阈值)
        self.predict_window = predict_window

    def predict_pressure(self, messages: List[dict], context_window: int) -> float:
        """估算当前压力(0-1)。"""
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        est_tokens = max(1, chars // 4)  # 粗略估算
        return min(1.0, est_tokens / max(1, context_window))

    def should_precompress(self, messages: List[dict], context_window: int) -> bool:
        """压力达到预测线且消息数接近窗口时建议预压缩。"""
        pressure = self.predict_pressure(messages, context_window)
        return pressure >= self.threshold and len(messages) >= self.predict_window

    def estimate_until_trigger(self, messages: List[dict], context_window: int) -> int:
        """估算还有多少条消息会触发实际压缩(80%)。"""
        pressure = self.predict_pressure(messages, context_window)
        if pressure <= 0:
            return 9999
        if len(messages) == 0:
            return 9999
        avg_per_msg = pressure / len(messages)
        remain = (0.80 - pressure) / avg_per_msg if avg_per_msg > 0 else 9999
        return max(0, int(remain))
