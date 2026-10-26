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
               cost_tokens: int, latency_ms: float, cache_hit: bool = False) -> None:
        saved = max(0, int(before_tokens) - int(after_tokens))
        event = {
            "ts": time.time(),
            "session_id": (session_id or "")[:24],
            "model": (model or "")[:40],
            "before": int(before_tokens),
            "after": int(after_tokens),
            "saved": saved,
            "cost": int(cost_tokens),
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
        total_saved = sum(e["saved"] for e in events)
        lat = sorted(e["latency_ms"] for e in events)
        by_model: dict = {}
        for e in events:
            row = by_model.setdefault(e["model"] or "?", {"calls": 0, "cost": 0, "saved": 0})
            row["calls"] += 1
            row["cost"] += e["cost"]
            row["saved"] += e["saved"]
        return {
            "events": len(events),
            "cost_tokens": total_cost,
            "saved_tokens": total_saved,
            "net_saved_tokens": total_saved - total_cost,
            "cache_hits": sum(1 for e in events if e["cache_hit"]),
            "p50_latency_ms": lat[len(lat) // 2],
            "p95_latency_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
            "by_model": by_model,
            "last": events[-1],
        }
