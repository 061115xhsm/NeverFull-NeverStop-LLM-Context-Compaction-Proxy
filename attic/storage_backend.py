"""
存储后端适配器模块(storage_backend.py)
========================================
分布式存储后端,供压缩代理调用,实现会话/记忆状态与本地 SQLite 解耦:

- StorageBackend(ABC):统一存储接口(session/memory 读写)
- SqliteBackend:本地 SQLite 实现(默认,零依赖)
- PostgresBackend:PostgreSQL 实现(可选 psycopg2,支持多实例共享)
- RedisBackend:Redis 缓存/会话实现(可选 redis-py,支持多实例)
- get_backend():按配置返回后端实例

依赖策略:psycopg2/redis-py 为可选;未安装时对应后端不可用,
回退 SQLite。纯标准库基础可用。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

# ── 可选依赖:PostgreSQL / Redis ────────────────────────────────────
try:
    import psycopg2  # type: ignore

    _HAS_PSYCOPG2 = True
except Exception:
    _HAS_PSYCOPG2 = False

try:
    import redis  # type: ignore

    _HAS_REDIS = True
except Exception:
    _HAS_REDIS = False


# ── 统一存储接口 ────────────────────────────────────────────────────

class StorageBackend(ABC):
    """统一存储接口:会话 + 语义记忆。"""

    @abstractmethod
    def save_session(self, session_id: str, summary: str, semantic: str, msg_count: int) -> None:
        """保存会话摘要。"""

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读取会话。"""

    @abstractmethod
    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出最近会话。"""

    @abstractmethod
    def save_memory(self, key: str, value: Dict[str, Any]) -> None:
        """保存语义记忆条目。"""

    @abstractmethod
    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        """读取语义记忆条目。"""


# ── SQLite 后端(默认,零依赖) ───────────────────────────────────────

class SqliteBackend(StorageBackend):
    """本地 SQLite 实现。"""

    def __init__(self, db_path: str = "storage.sqlite") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(id TEXT PRIMARY KEY, summary TEXT, semantic TEXT, msg_count INTEGER, ts REAL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory "
                "(key TEXT PRIMARY KEY, value TEXT, ts REAL)"
            )

    def save_session(self, session_id: str, summary: str, semantic: str, msg_count: int) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, summary, semantic, msg_count, ts) VALUES (?,?,?,?,?)",
                (session_id, summary, semantic, msg_count, time.time()),
            )

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, summary, semantic, msg_count, ts FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "summary": row[1], "semantic": row[2],
                "msg_count": row[3], "ts": row[4]}

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, summary, msg_count, ts FROM sessions ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"id": r[0], "summary": r[1], "msg_count": r[2], "ts": r[3]} for r in rows]

    def save_memory(self, key: str, value: Dict[str, Any]) -> None:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory (key, value, ts) VALUES (?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )

    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None


# ── PostgreSQL 后端(可选 psycopg2) ─────────────────────────────────

class PostgresBackend(StorageBackend):
    """
    PostgreSQL 实现,支持多实例共享状态。

    需安装 psycopg2-binary,并通过环境变量配置:
      PG_DSN = postgresql://user:pass@host:5432/dbname
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        self.dsn = dsn or os.environ.get("PG_DSN", "")
        if not _HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 未安装,PostgresBackend 不可用")
        if not self.dsn:
            raise RuntimeError("PG_DSN 未配置")
        self._init_db()

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS sessions ("
                    "id TEXT PRIMARY KEY, summary TEXT, semantic TEXT, "
                    "msg_count INTEGER, ts DOUBLE PRECISION)"
                )
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS memory ("
                    "key TEXT PRIMARY KEY, value TEXT, ts DOUBLE PRECISION)"
                )
            conn.commit()

    def save_session(self, session_id: str, summary: str, semantic: str, msg_count: int) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions (id, summary, semantic, msg_count, ts) "
                    "VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET summary=EXCLUDED.summary, "
                    "semantic=EXCLUDED.semantic, msg_count=EXCLUDED.msg_count, ts=EXCLUDED.ts",
                    (session_id, summary, semantic, msg_count, time.time()),
                )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, summary, semantic, msg_count, ts FROM sessions WHERE id=%s",
                    (session_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {"id": row[0], "summary": row[1], "semantic": row[2],
                "msg_count": row[3], "ts": row[4]}

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, summary, msg_count, ts FROM sessions ORDER BY ts DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
        return [{"id": r[0], "summary": r[1], "msg_count": r[2], "ts": r[3]} for r in rows]

    def save_memory(self, key: str, value: Dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memory (key, value, ts) VALUES (%s,%s,%s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, ts=EXCLUDED.ts",
                    (key, json.dumps(value, ensure_ascii=False), time.time()),
                )
            conn.commit()

    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM memory WHERE key=%s", (key,))
                row = cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None


# ── Redis 后端(可选 redis-py) ──────────────────────────────────────

class RedisBackend(StorageBackend):
    """
    Redis 实现,支持多实例共享与低延迟缓存。

    需安装 redis,并通过环境变量配置:
      REDIS_URL = redis://host:6379/0
    """

    def __init__(self, url: Optional[str] = None) -> None:
        url = url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        if not _HAS_REDIS:
            raise RuntimeError("redis 未安装,RedisBackend 不可用")
        self.client = redis.Redis.from_url(url, decode_responses=True)

    def save_session(self, session_id: str, summary: str, semantic: str, msg_count: int) -> None:
        key = f"session:{session_id}"
        self.client.hset(key, mapping={
            "summary": summary, "semantic": semantic, "msg_count": msg_count, "ts": time.time(),
        })
        self.client.expire(key, 7 * 86400)  # 7 天 TTL

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        data = self.client.hgetall(f"session:{session_id}")
        if not data:
            return None
        return {"id": session_id, "summary": data.get("summary", ""),
                "semantic": data.get("semantic", ""),
                "msg_count": int(data.get("msg_count", 0) or 0),
                "ts": float(data.get("ts", 0) or 0)}

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        keys = self.client.keys("session:*")[:limit]
        out: List[Dict[str, Any]] = []
        for k in keys:
            sid = k.split(":", 1)[1]
            s = self.get_session(sid)
            if s:
                out.append(s)
        return out

    def save_memory(self, key: str, value: Dict[str, Any]) -> None:
        self.client.set(f"memory:{key}", json.dumps(value, ensure_ascii=False))
        self.client.expire(f"memory:{key}", 7 * 86400)

    def get_memory(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self.client.get(f"memory:{key}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None


# ── 后端工厂 ────────────────────────────────────────────────────────

def get_backend(kind: str = "sqlite", **kwargs) -> StorageBackend:
    """
    按配置返回存储后端实例。

    Args:
        kind: "sqlite"(默认) | "postgres" | "redis"
        **kwargs: 传给具体后端的参数(dsn/url/db_path)

    Raises:
        RuntimeError: 后端不可用(依赖缺失或未配置)
    """
    kind = (kind or "sqlite").lower()
    if kind == "postgres":
        return PostgresBackend(dsn=kwargs.get("dsn"))
    if kind == "redis":
        return RedisBackend(url=kwargs.get("url"))
    return SqliteBackend(db_path=kwargs.get("db_path", "storage.sqlite"))
