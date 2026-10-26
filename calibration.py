"""Per-model token calibration (V11 P1).

Learns the ratio between our CJK-aware estimate and the provider's reported
prompt tokens, so preemptive thresholds stop drifting on models with different
tokenizers. Pure stdlib, persisted as JSON.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DEFAULT_PATH = os.environ.get(
    "COMPACTION_PROXY_CALIBRATION_DB",
    os.path.join(os.path.expanduser("~/.local/share/compaction-proxy"), "calibration.json"),
)


class TokenCalibrator:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path or DEFAULT_PATH).expanduser()
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = data if isinstance(data, dict) else {}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def factor(self, model: str) -> float:
        with self._lock:
            return float(self._data.get(model, {}).get("factor", 1.0))

    def apply(self, model: str, tokens: int) -> int:
        return int(max(0, tokens) * self.factor(model))

    def observe(self, model: str, estimated: int, actual: int) -> None:
        if estimated <= 0 or actual <= 0:
            return
        ratio = max(0.5, min(2.0, float(actual) / float(estimated)))
        with self._lock:
            entry = self._data.setdefault(model, {"factor": 1.0, "n": 0})
            n = int(entry.get("n", 0))
            prev = float(entry.get("factor", 1.0))
            entry["factor"] = ratio if n == 0 else (0.8 * prev + 0.2 * ratio)
            entry["n"] = n + 1
            entry["ts"] = time.time()
            self._save()

    def stats(self) -> dict:
        with self._lock:
            return {m: {"factor": round(float(v.get("factor", 1.0)), 4), "n": int(v.get("n", 0))}
                    for m, v in self._data.items()}
