"""Adaptive compaction policy (V11 P4) + capability profiles (P5).

Research basis
--------------
- PoC (arXiv:2603.19733): specify a *performance floor*, maximize compression,
  instead of fixing a ratio and hoping.
- Memory-R1 (2508.19828) / Memory-R2 (2605.21768) / Uni-Mem (2610.29671):
  learn keep/update/delete decisions from downstream reward; credit assignment is
  the hard part in long horizons.
- MemoryTax (2610.29823) / Skip the Compressor (2610.21073): with strong models,
  light folding plus searchable raw history beats aggressive memory pipelines.

This module implements a small, auditable bandit over three actions
(light / standard / deep). It is intentionally *not* a neural RL policy: the
reward signal available at a transparent proxy is sparse and delayed, so a UCB
bandit with an explicit performance-floor bias is the honest first step.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

ACTIONS = ("light", "standard", "deep")
DEFAULT_PATH = os.environ.get(
    "COMPACTION_PROXY_POLICY_DB",
    os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "policy.json"),
)

CODE_HINTS = re.compile(
    r"(?:^|\s)(?:def |class |import |from \w+ import|function |const |let |var |func )"
    r"|```|\.py\b|\.ts\b|\.js\b|\.rs\b|\.go\b|\.java\b"
)
DATA_HINTS = re.compile(r"```json|\{[\s\S]{20,}\}|\.csv\b|\bSELECT\b|\bCREATE TABLE\b", re.I)
OPS_HINTS = re.compile(r"\b(?:bash|sh|zsh|systemctl|docker|kubectl|make|npm|pnpm|pytest|pip)\b", re.I)


def classify_task(messages: list) -> str:
    """Coarse task class used to key the bandit. Deterministic and cheap."""
    text_parts = []
    for msg in (messages or [])[-12:]:
        content = msg.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") in ("tool_use", "tool_call"):
                        name = str(block.get("name", ""))
                        if name.lower() in ("bash", "exec", "shell", "run"):
                            return "ops"
    text = "\n".join(text_parts)[:4000]
    if DATA_HINTS.search(text):
        return "data"
    if CODE_HINTS.search(text):
        return "code"
    if OPS_HINTS.search(text):
        return "ops"
    return "prose"


def effective_threshold(model: str = "", profile: str = "", window: Optional[int] = None) -> float:
    """Preemptive threshold by agent/model capability profile.

    profile: "" (use legacy PREEMPTIVE_THRESHOLD), "weak", "strong", "auto".
    Weak models get compressed earlier (smaller effective window); strong models
    get more raw context (MemoryTax / Skip the Compressor).
    """
    if not profile:
        return -1.0  # sentinel: caller keeps legacy threshold
    if profile == "weak":
        return 0.75
    if profile == "strong":
        return 0.90
    # auto
    if window is None:
        window = 128_000
    if window >= 200_000:
        return 0.90
    if window >= 100_000:
        return 0.85
    return 0.78


class ContextPolicy:
    """UCB1 bandit with a performance-floor bias, persisted as JSON."""

    def __init__(self, path: str | os.PathLike | None = None, epsilon: float = 0.15,
                 floor: float = 0.75, seed: int = 7) -> None:
        self.path = Path(path or DEFAULT_PATH).expanduser()
        self.epsilon = epsilon
        self.floor = floor
        self._rng = random.Random(seed)
        self._data: dict = {}
        self._ema: dict = {}
        self._load()

    def _load(self) -> None:
        redis_url = os.environ.get("COMPACTION_PROXY_REDIS_URL", "")
        if redis_url:
            try:
                import redis
                client = redis.Redis.from_url(redis_url, decode_responses=True)
                blob = client.get("nf:policy:state")
                self._data = json.loads(blob) if blob else {}
                if not isinstance(self._data, dict):
                    self._data = {}
                self._redis = client
                return
            except Exception:
                self._redis = None
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        if getattr(self, "_redis", None):
            try:
                self._redis.set("nf:policy:state", json.dumps(self._data, ensure_ascii=False))
                return
            except Exception:
                pass
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def choose(self, task_class: str, session_id: str = "") -> str:
        # Performance floor: if the rolling evidence score for this session is
        # below the floor, prefer the gentlest action regardless of the bandit.
        ema = self._ema.get(session_id)
        if ema is not None and ema < self.floor:
            return "light"

        stats = self._data.setdefault(task_class, {})
        if self._rng.random() < self.epsilon:
            return self._rng.choice(ACTIONS)

        total = sum(int(s.get("n", 0)) for s in stats.values()) or 1
        best, best_score = "standard", -1.0
        for action in ACTIONS:
            s = stats.get(action) or {}
            n = int(s.get("n", 0))
            if n == 0:
                return action  # try each arm once
            mean = float(s.get("mean", 0.0))
            score = mean + math.sqrt(2.0 * math.log(total + 1) / n)
            if score > best_score:
                best, best_score = action, score
        return best

    def reward(self, task_class: str, action: str, value: float) -> None:
        stats = self._data.setdefault(task_class, {})
        s = stats.setdefault(action, {"n": 0, "mean": 0.0})
        n = int(s.get("n", 0))
        s["mean"] = (float(s.get("mean", 0.0)) * n + max(0.0, min(1.0, value))) / (n + 1)
        s["n"] = n + 1
        s["ts"] = time.time()
        self._save()

    def observe_quality(self, session_id: str, score: float) -> None:
        prev = self._ema.get(session_id)
        self._ema[session_id] = score if prev is None else (0.7 * prev + 0.3 * score)

    def quality(self, session_id: str) -> Optional[float]:
        return self._ema.get(session_id)

    def stats(self) -> dict:
        return {
            "enabled": True,
            "epsilon": self.epsilon,
            "floor": self.floor,
            "tasks": self._data,
            "quality_ema": {k: round(v, 3) for k, v in self._ema.items()},
        }
