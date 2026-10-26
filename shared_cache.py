"""Cross-instance compaction cache (V11 P6).

Redis when configured (COMPACTION_PROXY_REDIS_URL), otherwise process-local.
Keys include the tenant so two tenants never share a summary.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

from tenant import current_tenant


class SharedCache:
    def __init__(self, url: str | None = None, ttl: int = 1800) -> None:
        self.ttl = ttl
        self._local: dict = {}
        self._lock = threading.Lock()
        self._redis = None
        self.backend = "local"
        if url:
            try:
                import redis  # type: ignore
                client = redis.Redis.from_url(url, decode_responses=True)
                client.ping()
                self._redis = client
                self.backend = "redis"
            except Exception:
                self._redis = None
                self.backend = "local"

    def _key(self, messages: list, salt: str) -> str:
        raw = json.dumps(messages, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256((current_tenant() + "\x00" + raw + "\x00" + salt).encode("utf-8")).hexdigest()
        return "nf:compact:" + digest

    def get(self, messages: list, salt: str = "") -> str | None:
        key = self._key(messages, salt)
        if self._redis is not None:
            try:
                return self._redis.get(key)
            except Exception:
                pass
        with self._lock:
            item = self._local.get(key)
            if not item:
                return None
            value, ts = item
            if time.time() - ts > self.ttl:
                del self._local[key]
                return None
            return value

    def put(self, messages: list, summary: str, salt: str = "") -> None:
        key = self._key(messages, salt)
        if self._redis is not None:
            try:
                self._redis.setex(key, self.ttl, summary)
                return
            except Exception:
                pass
        with self._lock:
            self._local[key] = (summary, time.time())

    def stats(self) -> dict:
        with self._lock:
            size = len(self._local)
        return {"backend": self.backend, "local_entries": size, "ttl": self.ttl}
