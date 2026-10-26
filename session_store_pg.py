"""PostgreSQL SessionStore backend (V11 P6).

Same public surface as compaction-proxy.SessionStore, backed by PostgreSQL so
multiple proxy instances share sessions, prior summaries, ARC entries and
transcripts. FTS uses PostgreSQL full-text search (english) with Chinese text
falling back to plain ILIKE matching.

Requires `psycopg2-binary`; raises RuntimeError at construction when missing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("compaction-proxy.pg")


class PostgresSessionStore:
    def __init__(self, dsn: str, tenant_max_sessions: int = 0) -> None:
        self.tenant_max_sessions = max(0, int(tenant_max_sessions))
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise RuntimeError("psycopg2 is required for the postgres session store") from e
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id      TEXT PRIMARY KEY,
                    tenant_id       TEXT NOT NULL DEFAULT 'default',
                    summary         TEXT,
                    semantic_memory TEXT,
                    msg_count       INTEGER DEFAULT 0,
                    transcript      TEXT,
                    created_at      DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM now()),
                    updated_at      DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM now())
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id, updated_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS prior_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary    TEXT,
                    msg_count  INTEGER DEFAULT 0,
                    updated_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM now())
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS arc_entries (
                    arc_id     TEXT PRIMARY KEY,
                    content    TEXT,
                    source_idx INTEGER DEFAULT -1,
                    stored_at  DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM now()),
                    size       INTEGER DEFAULT 0
                )
            """)
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS sessions_summary_ftx ON sessions USING gin (to_tsvector('english', summary))")
            except Exception:
                pass

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _split(session_id: str) -> tuple[str, str]:
        tenant, _, sid = str(session_id).partition(":")
        return (tenant or "default", sid or session_id)

    def _scoped(self, session_id: str) -> str:
        return str(session_id) if ":" in str(session_id) else f"default:{session_id}"

    # -- sessions ----------------------------------------------------------
    def save_session(self, session_id: str, summary: str, semantic_memory_data, msg_count: int) -> None:
        tenant, _ = self._split(session_id)
        sem_json = json.dumps(semantic_memory_data, ensure_ascii=False) if semantic_memory_data else ""
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sessions (session_id, tenant_id, summary, semantic_memory, msg_count, updated_at)
                VALUES (%s, %s, %s, %s, %s, EXTRACT(EPOCH FROM now()))
                ON CONFLICT (session_id) DO UPDATE SET
                    summary = EXCLUDED.summary,
                    semantic_memory = EXCLUDED.semantic_memory,
                    msg_count = EXCLUDED.msg_count,
                    updated_at = EXCLUDED.updated_at
            """, (self._scoped(session_id), tenant, summary, sem_json, msg_count))
        self._trim_tenant(tenant)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                SELECT session_id, summary, semantic_memory, msg_count, created_at, updated_at
                FROM sessions WHERE session_id = %s
            """, (self._scoped(session_id),))
            row = cur.fetchone()
        if not row:
            return None
        try:
            sem = json.loads(row[2]) if row[2] else {}
        except (TypeError, ValueError):
            sem = {}
        return {"session_id": row[0], "summary": row[1], "semantic_memory": sem,
                "msg_count": row[3], "created_at": row[4], "updated_at": row[5]}

    def get_recent_sessions(self, n: int = 5) -> List[Dict[str, Any]]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                SELECT session_id, summary, msg_count, updated_at FROM sessions
                ORDER BY updated_at DESC LIMIT %s
            """, (n,))
            rows = cur.fetchall()
        return [{"session_id": r[0], "summary": (r[1] or "")[:500], "msg_count": r[2], "updated_at": r[3]} for r in rows]

    def search_sessions(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                SELECT session_id, summary, msg_count, updated_at,
                       ts_rank FROM (
                    SELECT session_id, summary, msg_count, updated_at,
                           ts_rank(to_tsvector('english', coalesce(summary,'')), plainto_tsquery('english', %s)) AS ts_rank
                    FROM sessions
                    WHERE to_tsvector('english', coalesce(summary,'')) @@ plainto_tsquery('english', %s)
                ) t
                UNION ALL
                SELECT session_id, summary, msg_count, updated_at, 0.0 AS ts_rank FROM (
                    SELECT session_id, summary, msg_count, updated_at FROM sessions
                    WHERE summary ILIKE %s LIMIT %s
                ) u
                LIMIT %s
            """, (query, query, f"%{query}%", limit, limit))
            rows = cur.fetchall()
        seen, out = set(), []
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            out.append({"session_id": r[0], "summary": (r[1] or "")[:500], "msg_count": r[2], "updated_at": r[3], "rank": r[4]})
        return out

    def get_background_knowledge(self, n: int = 3) -> List[str]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                SELECT summary FROM sessions
                WHERE summary IS NOT NULL AND summary <> ''
                ORDER BY updated_at DESC LIMIT %s
            """, (n,))
            rows = cur.fetchall()
        return [r[0] for r in rows if r[0]]

    # -- prior summaries -----------------------------------------------------
    def get_prior_summary(self, session_id: str):
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT summary, msg_count FROM prior_summaries WHERE session_id = %s", (self._scoped(session_id),))
            row = cur.fetchone()
        return (row[0], row[1]) if row else (None, 0)

    def save_prior_summary(self, session_id: str, summary: str, msg_count: int) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO prior_summaries (session_id, summary, msg_count, updated_at)
                VALUES (%s, %s, %s, EXTRACT(EPOCH FROM now()))
                ON CONFLICT (session_id) DO UPDATE SET
                    summary = EXCLUDED.summary, msg_count = EXCLUDED.msg_count,
                    updated_at = EXCLUDED.updated_at
            """, (self._scoped(session_id), summary, msg_count))

    # -- transcripts -----------------------------------------------------------
    def save_transcript(self, session_id: str, messages: list) -> None:
        scoped = self._scoped(session_id)
        tenant, _ = self._split(session_id)
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sessions (session_id, tenant_id, transcript) VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
            """, (scoped, tenant, json.dumps(messages, ensure_ascii=False)))
            cur.execute("UPDATE sessions SET transcript = %s WHERE session_id = %s",
                        (json.dumps(messages, ensure_ascii=False), scoped))

    def get_transcript(self, session_id: str) -> Optional[list]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT transcript FROM sessions WHERE session_id = %s", (self._scoped(session_id),))
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return None

    # -- ARC -------------------------------------------------------------------
    def save_arc_entry(self, arc_id: str, content: str, source_idx: int = -1) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO arc_entries (arc_id, content, source_idx, stored_at, size)
                VALUES (%s, %s, %s, EXTRACT(EPOCH FROM now()), %s)
                ON CONFLICT (arc_id) DO UPDATE SET content = EXCLUDED.content,
                    source_idx = EXCLUDED.source_idx, size = EXCLUDED.size
            """, (arc_id, content, source_idx, len(content or "")))

    def load_arc_entries(self) -> Dict[str, str]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT arc_id, content FROM arc_entries")
            rows = cur.fetchall()
        return {r[0]: r[1] or "" for r in rows}

    # -- maintenance --------------------------------------------------------------
    @property
    def session_count(self) -> int:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sessions")
            return cur.fetchone()[0]

    def _trim_tenant(self, tenant: str) -> None:
        if self.tenant_max_sessions and self.tenant_max_sessions > 0:
            with self._lock, self._conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM sessions WHERE tenant_id = %s AND session_id IN (
                        SELECT session_id FROM sessions WHERE tenant_id = %s
                        ORDER BY updated_at DESC OFFSET %s
                    )
                """, (tenant, tenant, self.tenant_max_sessions))

    def prune(self, max_age_days: float = 90.0, max_sessions: int = 2000) -> int:
        removed = 0
        cutoff = time.time() - max_age_days * 86400
        with self._lock, self._conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE updated_at < %s", (cutoff,))
            removed += cur.rowcount or 0
            cur.execute("""
                DELETE FROM sessions WHERE session_id IN (
                    SELECT session_id FROM sessions ORDER BY updated_at DESC OFFSET %s
                )
            """, (max_sessions,))
            removed += cur.rowcount or 0
            cur.execute("DELETE FROM prior_summaries WHERE session_id NOT IN (SELECT session_id FROM sessions)")
        return removed

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
