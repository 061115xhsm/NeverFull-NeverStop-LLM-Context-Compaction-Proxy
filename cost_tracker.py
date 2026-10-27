"""Per-request cost attribution ring buffer (V11 P5)."""

from __future__ import annotations

import threading
import time
from collections import deque


class CostTracker:
    def __init__(self, maxlen: int = 256) -> None:
        self._events = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, session_id: str, model: str, before_tokens: int, after_tokens: int,
               cost_tokens: int, latency_ms: float, cache_hit: bool = False,
               tenant_id: str = "default", effective_cost_tokens: int = 0) -> None:
        saved = max(0, int(before_tokens) - int(after_tokens))
        event = {
            "ts": time.time(),
            "tenant_id": (tenant_id or "default")[:32],
            "session_id": (session_id or "")[:24],
            "model": (model or "")[:40],
            "before": int(before_tokens),
            "after": int(after_tokens),
            "saved": saved,
            "cost": int(cost_tokens),
            "effective_cost": int(effective_cost_tokens or 0),
            "latency_ms": round(float(latency_ms), 1),
            "cache_hit": bool(cache_hit),
        }
        with self._lock:
            self._events.append(event)

    def summary(self) -> dict:
        with self._lock:
            events = list(self._events)
        if not events:
            return {"events": 0}
        total_cost = sum(e["cost"] for e in events)
        total_effective_cost = sum(e.get("effective_cost", 0) for e in events)
        total_saved = sum(e["saved"] for e in events)
        lat = sorted(e["latency_ms"] for e in events)
        by_model: dict = {}
        by_tenant: dict = {}
        for e in events:
            row = by_model.setdefault(e["model"] or "?", {"calls": 0, "cost": 0, "saved": 0})
            row["calls"] += 1
            row["cost"] += e["cost"]
            row["saved"] += e["saved"]
            trow = by_tenant.setdefault(e.get("tenant_id", "default"), {"calls": 0, "cost": 0, "saved": 0})
            trow["calls"] += 1
            trow["cost"] += e["cost"]
            trow["saved"] += e["saved"]
        return {
            "events": len(events),
            "cost_tokens": total_cost,
            "effective_cost_tokens": total_effective_cost,
            "saved_tokens": total_saved,
            "net_saved_tokens": total_saved - total_cost,
            "cache_hits": sum(1 for e in events if e["cache_hit"]),
            "p50_latency_ms": lat[len(lat) // 2],
            "p95_latency_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
            "by_model": by_model,
            "by_tenant": by_tenant,
            "last": events[-1],
        }


class DailyQuota:
    """Per-tenant daily token budget (in-memory; resets on restart)."""

    def __init__(self, limit: int = 0) -> None:
        self.limit = max(0, int(limit))
        self._data: dict = {}
        self._lock = threading.Lock()

    def _day(self) -> str:
        return time.strftime("%Y-%m-%d")

    def add(self, tenant: str, tokens: int) -> None:
        if self.limit <= 0:
            return
        with self._lock:
            key = (tenant or "default", self._day())
            self._data[key] = self._data.get(key, 0) + int(tokens)

    def used(self, tenant: str) -> int:
        with self._lock:
            return self._data.get((tenant or "default", self._day()), 0)

    def exceeded(self, tenant: str) -> bool:
        return self.limit > 0 and self.used(tenant) >= self.limit

    def stats(self) -> dict:
        with self._lock:
            today = self._day()
            return {"limit": self.limit,
                    "today": {t: v for (t, d), v in self._data.items() if d == today}}
