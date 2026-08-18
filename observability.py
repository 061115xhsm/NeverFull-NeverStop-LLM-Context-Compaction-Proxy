"""
高可用容灾与监控模块(observability.py)
========================================
多上游容灾 + 多级降级 + 全链路可观测,供压缩代理调用:

- FailoverManager:多上游自动切换(主上游故障切备用)
- GracefulDegrader:四级降级(压缩→轻量压缩→截断→透传)
- MetricsCollector:20+ 指标采集(Prometheus 文本格式输出)
- Tracer:OpenTelemetry 风格 span 追踪(可选 otel,降级本地计时)

依赖:优先 opentelemetry;未安装则降级为本地计时,保证可用。
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

# ── 可选依赖:OpenTelemetry ─────────────────────────────────────────
try:
    from opentelemetry import trace  # type: ignore

    _HAS_OTEL = True
except Exception:
    _HAS_OTEL = False


# ── 多上游容灾 ──────────────────────────────────────────────────────

class FailoverManager:
    """
    多上游自动切换:

    - add_upstream(url, weight)
    - 当前上游连续失败 N 次 → 自动切换到下一个健康上游
    - 冷却期后恢复
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.upstreams: List[Dict[str, Any]] = []
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self._current = 0
        self._lock = threading.Lock()

    def add_upstream(self, url: str, weight: float = 1.0) -> None:
        with self._lock:
            self.upstreams.append({
                "url": url, "weight": weight,
                "failures": 0, "state": "healthy",
                "last_failure": 0.0,
            })

    def current(self) -> Optional[str]:
        with self._lock:
            if not self.upstreams:
                return None
            return self.upstreams[self._current]["url"]

    def record_success(self) -> None:
        with self._lock:
            if not self.upstreams:
                return
            u = self.upstreams[self._current]
            u["failures"] = 0
            u["state"] = "healthy"

    def record_failure(self) -> bool:
        """
        记录失败;若当前上游达到阈值则切换。返回是否发生了切换。
        """
        with self._lock:
            if not self.upstreams:
                return False
            u = self.upstreams[self._current]
            u["failures"] += 1
            u["last_failure"] = time.time()
            if u["failures"] >= self.failure_threshold:
                u["state"] = "open"
                switched = self._switch_locked()
                return switched
            return False

    def _switch_locked(self) -> bool:
        n = len(self.upstreams)
        for _ in range(n):
            self._current = (self._current + 1) % n
            cand = self.upstreams[self._current]
            # 冷却期后恢复候选
            if cand["state"] == "open" and time.time() - cand["last_failure"] > self.cooldown:
                cand["state"] = "healthy"
                cand["failures"] = 0
                return True
            if cand["state"] == "healthy":
                return True
        return False

    def status(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"url": u["url"], "state": u["state"], "failures": u["failures"]}
                for u in self.upstreams
            ]


# ── 多级降级 ────────────────────────────────────────────────────────

class GracefulDegrader:
    """
    四级降级策略:压缩 → 轻量压缩 → 智能截断 → 纯透传。
    逐级降级保证业务不中断。
    """

    LEVELS = ["full", "light", "truncate", "passthrough"]

    def __init__(self, handlers: Optional[Dict[str, Callable]] = None) -> None:
        """
        handlers: {"full": fn, "light": fn, "truncate": fn, "passthrough": fn}
        调用方注入各层级实现。
        """
        self.handlers = handlers or {}
        self.current_level = "full"

    def execute(self, messages: List[dict], **kwargs) -> Any:
        """从当前层级开始尝试,失败则逐级降级。"""
        start = self.LEVELS.index(self.current_level)
        for lvl in self.LEVELS[start:]:
            handler = self.handlers.get(lvl)
            if handler is None:
                continue
            try:
                result = handler(messages, **kwargs)
                if result is not None:
                    self.current_level = lvl
                    return result
            except Exception:
                continue
            # handler 返回 None 视为本级不可用,继续降级
        # 最终兜底:透传
        return messages

    def degrade_to(self, level: str) -> None:
        if level in self.LEVELS:
            self.current_level = level

    def reset(self) -> None:
        self.current_level = "full"


# ── 指标采集 ────────────────────────────────────────────────────────

class MetricsCollector:
    """20+ 指标采集,Prometheus 文本格式输出。"""

    def __init__(self) -> None:
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, name: str, delta: int = 1) -> None:
        with self._lock:
            self.counters[name] += delta

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = value

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
            }

    def prometheus(self) -> str:
        """Prometheus 文本格式。"""
        lines: List[str] = []
        with self._lock:
            for k, v in sorted(self.counters.items()):
                lines.append(f"# TYPE compaction_{k} counter")
                lines.append(f"compaction_{k} {v}")
            for k, v in sorted(self.gauges.items()):
                lines.append(f"# TYPE compaction_{k} gauge")
                lines.append(f"compaction_{k} {v}")
        return "\n".join(lines) + "\n"

    def register_defaults(self) -> None:
        """注册常用指标(压缩/缓存/上游/质量)。"""
        for name in [
            "compactions_total", "compaction_success_total", "compaction_failed_total",
            "cache_hit_total", "cache_miss_total",
            "upstream_error_total", "failover_switches_total",
            "low_fidelity_total", "degrade_total",
            "request_total", "request_success_total", "request_overflow_total",
            "tokens_before_total", "tokens_after_total", "tokens_saved_total",
            "sessions_saved_total", "arc_applied_total", "secret_redacted_total",
            "streaming_total", "preemptive_total", "timeout_total",
        ]:
            # 预置为零,便于监控面板展示
            self.counters[name] = self.counters.get(name, 0)


# ── 追踪 ────────────────────────────────────────────────────────────

class Tracer:
    """
    OpenTelemetry 风格 span 追踪。有 otel 则用真实 trace,否则本地计时。
    """

    def __init__(self, service_name: str = "compaction-proxy") -> None:
        self.service_name = service_name
        self._local_spans: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> Any:
        if _HAS_OTEL:
            tracer = trace.get_tracer(self.service_name)
            return tracer.start_span(name, attributes=attributes or {})
        return LocalSpan(name, attributes or {})

    def record(self, span: Any) -> None:
        if isinstance(span, LocalSpan):
            with self._lock:
                self._local_spans.append(span.to_dict())


class LocalSpan:
    """otel 不可用时的本地计时 span。"""

    def __init__(self, name: str, attributes: Dict[str, Any]) -> None:
        self.name = name
        self.attributes = attributes
        self.start = time.time()
        self.end: Optional[float] = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def end(self) -> None:  # noqa: A003 - otel 兼容命名
        self.end = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "attributes": self.attributes,
            "duration_ms": round((self.end or time.time() - self.start) * 1000, 2),
        }
