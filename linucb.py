"""Disjoint LinUCB contextual bandit for compaction strength (V12 Phase 3).

Context features describe the current session pressure; arms map to compaction
operators. Persisted as JSON or shared via Redis (COMPACTION_PROXY_REDIS_URL).
Falls back to a no-op if numpy is unavailable (caller keeps UCB1).
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

ACTIONS = ("dedup_only", "refold_chunk", "extractive_ast", "slipstream_deep")
DEFAULT_PATH = os.environ.get(
    "COMPACTION_PROXY_LINUCB_DB",
    os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "linucb.json"),
)


class DisjointLinUCB:
    def __init__(self, dim: int = 6, alpha: float = 0.5,
                 path: str | os.PathLike | None = None, redis_url: str = "") -> None:
        if np is None:
            raise RuntimeError("numpy is required for LinUCB")
        self.dim = dim
        self.alpha = alpha
        self.path = Path(path or DEFAULT_PATH).expanduser()
        self._lock = threading.RLock()
        self._A = {a: np.identity(dim) for a in ACTIONS}
        self._b = {a: np.zeros(dim) for a in ACTIONS}
        self._redis = None
        self._load(redis_url)

    def _load(self, redis_url: str) -> None:
        if redis_url:
            try:
                import redis
                client = redis.Redis.from_url(redis_url, decode_responses=True)
                blob = client.get("nf:linucb:state")
                if blob:
                    self._deserialize(json.loads(blob))
                self._redis = client
                return
            except Exception:
                self._redis = None
        try:
            self._deserialize(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            pass

    def _deserialize(self, data: dict) -> None:
        for arm in ACTIONS:
            entry = data.get(arm) or {}
            A = entry.get("A")
            b = entry.get("b")
            if A and b and len(A) == self.dim:
                self._A[arm] = np.array(A, dtype=float)
                self._b[arm] = np.array(b, dtype=float)

    def _serialize(self) -> dict:
        return {a: {"A": self._A[a].tolist(), "b": self._b[a].tolist()} for a in ACTIONS}

    def _save(self) -> None:
        data = self._serialize()
        if self._redis is not None:
            try:
                self._redis.set("nf:linucb:state", json.dumps(data))
                return
            except Exception:
                pass
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def choose(self, context: list, actions=ACTIONS) -> str:
        x = np.array(context, dtype=float)
        best, best_score = actions[0], -float("inf")
        with self._lock:
            for arm in actions:
                A_inv = np.linalg.inv(self._A[arm])
                theta = A_inv @ self._b[arm]
                mean = float(theta @ x)
                bonus = self.alpha * math.sqrt(max(0.0, float(x @ A_inv @ x)))
                score = mean + bonus
                if score > best_score:
                    best, best_score = arm, score
        return best

    def update(self, arm: str, context: list, reward: float) -> None:
        x = np.array(context, dtype=float)
        r = max(0.0, min(1.0, float(reward)))
        with self._lock:
            self._A[arm] = self._A[arm] + np.outer(x, x)
            self._b[arm] = self._b[arm] + r * x
            self._save()

    def stats(self) -> dict:
        with self._lock:
            return {a: {"samples": int(round(float(np.trace(self._A[a])) - self.dim))}
                    for a in ACTIONS}
