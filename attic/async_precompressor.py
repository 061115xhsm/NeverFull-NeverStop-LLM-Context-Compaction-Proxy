"""
预测式异步预压缩调度模块(async_precompressor.py)
==================================================
预测式上下文管理 + 异步预压缩调度,供压缩代理调用:

- PrecompressionScheduler:后台异步预压缩调度器
  - 基于对话长度/压力预测即将触发阈值
  - 达到预测线时后台提前压缩,用户请求时无感知
  - 避免压缩阻塞主请求
- 支持线程池后台执行,调用方通过回调/轮询获取结果

纯标准库实现(threading/concurrent.futures)。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional


class PrecompressionScheduler:
    """
    预测式异步预压缩调度器。

    用法:
        sched = PrecompressionScheduler(trigger_fn=my_compress, predict_fn=my_predict)
        sched.observe(messages, context_window)   # 每次请求后观察,内部判断是否预压缩
        result = sched.get_last_result()          # 后台预压缩完成后取结果
    """

    def __init__(
        self,
        trigger_fn: Callable[[List[dict]], Any],
        predict_fn: Optional[Callable[[List[dict], int], float]] = None,
        trigger_threshold: float = 0.75,
        min_messages: int = 10,
        max_workers: int = 1,
    ) -> None:
        """
        Args:
            trigger_fn: 后台执行的预压缩函数,接收 messages,返回压缩结果
            predict_fn: 压力预测函数,返回 0-1;缺省用字符数/4 估算
            trigger_threshold: 触发预测线的压力阈值(默认 0.75,低于实际 0.80)
            min_messages: 最少消息数才触发(避免小对话频繁预压缩)
            max_workers: 后台线程数
        """
        self.trigger_fn = trigger_fn
        self.predict_fn = predict_fn
        self.trigger_threshold = trigger_threshold
        self.min_messages = min_messages
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._pending: Optional[Future] = None
        self._last_result: Any = None
        self._last_pressure: float = 0.0
        self._precompressions: int = 0
        self._last_trigger_ts: float = 0.0
        self._cooldown = 10.0  # 两次预压缩最小间隔(秒)

    # ── 压力预测(缺省实现) ──
    @staticmethod
    def _default_predict(messages: List[dict], context_window: int) -> float:
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        est_tokens = max(1, chars // 4)
        return min(1.0, est_tokens / max(1, context_window))

    def _pressure(self, messages: List[dict], context_window: int) -> float:
        if self.predict_fn is not None:
            try:
                return max(0.0, min(1.0, float(self.predict_fn(messages, context_window))))
            except Exception:
                pass
        return self._default_predict(messages, context_window)

    # ── 观察接口 ──
    def observe(self, messages: List[dict], context_window: int) -> bool:
        """
        每次请求后调用。若压力达到预测线且冷却期已过,启动后台预压缩。

        Returns:
            True 表示本次已触发预压缩
        """
        pressure = self._pressure(messages, context_window)
        with self._lock:
            self._last_pressure = pressure
            now = time.time()
            if (
                pressure >= self.trigger_threshold
                and len(messages) >= self.min_messages
                and now - self._last_trigger_ts >= self._cooldown
                and (self._pending is None or self._pending.done())
            ):
                self._last_trigger_ts = now
                self._precompressions += 1
                snapshot = list(messages)
                self._pending = self._executor.submit(self.trigger_fn, snapshot)
                return True
        return False

    # ── 结果接口 ──
    def get_last_result(self) -> Any:
        """返回最近一次后台预压缩的结果(未完成返回 None)。"""
        with self._lock:
            if self._pending is not None and self._pending.done():
                try:
                    self._last_result = self._pending.result()
                except Exception:
                    self._last_result = None
                self._pending = None
            return self._last_result

    @property
    def pressure(self) -> float:
        return self._last_pressure

    @property
    def precompression_count(self) -> int:
        return self._precompressions

    def shutdown(self) -> None:
        """关闭调度器(释放线程池)。"""
        self._executor.shutdown(wait=False, cancel_futures=True)
