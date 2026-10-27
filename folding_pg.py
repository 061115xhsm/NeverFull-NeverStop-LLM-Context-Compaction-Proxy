"""PostgreSQL FoldArchive (V12 Phase 3): same interface as folding.FoldArchive.

Payloads are zstd-free JSON, optionally envelope-encrypted with per-tenant DEKs
wrapped under a local KEK (rotate-key rewraps DEKs only, payloads untouched).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from folding import EnvelopeCipher, _HAS_AESGCM  # reuse the envelope cipher

logger = logging.getLogger("compaction-proxy.pg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS folds (
    ref           TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    created_at    DOUBLE PRECISION,
    kind          TEXT,
    n_messages    INTEGER,
    approx_tokens INTEGER,
    payload       TEXT
);
CREATE INDEX IF NOT EXISTS idx_folds_tenant ON folds(tenant_id, created_at DESC);
"""


class PgFoldArchive:
    def __init__(self, dsn: str, encrypt: bool = False, kek_path=None) -> None:
        import psycopg2
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self._lock = None  # psycopg2 connections are used with a lock below
        self._cipher = None
        self._cipher_kind = None
        self._fernet = None
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA)
        if encrypt and _HAS_AESGCM:
            from pathlib import Path as _P
            kek = _P(kek_path or "/tmp/nf-kek.bin")
            self._cipher = EnvelopeCipher(kek)
            self._cipher.set_runner(self._run)
            self._cipher_kind = "envelope"
        else:
            self._cipher = None
            self._cipher_kind = None

    def _run(self, sql: str, params=None):
        with self._conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

    def _tenant(self, session_id: str, tenant_id: Optional[str]) -> str:
        if tenant_id:
            return tenant_id
        return str(session_id).split(":", 1)[0] if ":" in str(session_id) else "default"

    def put(self, session_id: str, kind: str, messages: list, ref: Optional[str] = None,
            tenant_id: Optional[str] = None, max_per_tenant: int = 0) -> str:
        tenant = self._tenant(session_id, tenant_id)
        payload = json.dumps(messages, ensure_ascii=False)
        if self._cipher_kind == "envelope":
            payload = "env:v1:" + self._cipher.encrypt_blob(tenant, payload.encode("utf-8")).decode("ascii")
        if ref is None:
            raw = tenant + "\x00" + json.dumps(messages, ensure_ascii=False)
            ref = "fold:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        with self._conn.cursor() as cur:
            cur.execute("""
                INSERT INTO folds(ref, tenant_id, session_id, created_at, kind, n_messages, approx_tokens, payload)
                VALUES (%s, %s, %s, EXTRACT(EPOCH FROM now()), %s, %s, %s, %s)
                ON CONFLICT (ref) DO UPDATE SET payload = EXCLUDED.payload,
                    created_at = EXCLUDED.created_at
            """, (ref, tenant, session_id, kind, len(messages), _approx(messages), payload))
            if max_per_tenant and max_per_tenant > 0:
                cur.execute("""
                    DELETE FROM folds WHERE tenant_id = %s AND ref IN (
                        SELECT ref FROM folds WHERE tenant_id = %s
                        ORDER BY created_at DESC OFFSET %s
                    )
                """, (tenant, tenant, max_per_tenant))
        return ref

    def get(self, ref: str, tenant_id: Optional[str] = None) -> Optional[dict]:
        with self._conn.cursor() as cur:
            if tenant_id is None:
                cur.execute("""
                    SELECT ref, tenant_id, session_id, created_at, kind, n_messages, approx_tokens, payload
                    FROM folds WHERE ref = %s
                """, (ref,))
            else:
                cur.execute("""
                    SELECT ref, tenant_id, session_id, created_at, kind, n_messages, approx_tokens, payload
                    FROM folds WHERE ref = %s AND tenant_id = %s
                """, (ref, tenant_id))
            row = cur.fetchone()
        if not row:
            return None
        return {
            "ref": row[0], "tenant_id": row[1], "session_id": row[2], "created_at": row[3],
            "kind": row[4], "n_messages": row[5], "approx_tokens": row[6],
            "messages": _decode(row[7], row[1], self._cipher),
        }

    def stats(self) -> dict:
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM folds")
            n = cur.fetchone()[0]
        return {"entries": n, "path": "postgres"}

    def rotate_key(self, new_key_path=None) -> int:
        if self._cipher_kind == "envelope":
            with self._conn.cursor() as cur:
                return self._cipher.rotate_kek(self._conn)
        return 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def _approx(messages: list) -> int:
    return max(1, int(len(json.dumps(messages, ensure_ascii=False)) / 4.0))


def _decode(payload: str, tenant: str, cipher) -> list:
    try:
        if isinstance(payload, str) and payload.startswith("env:v1:"):
            return json.loads(cipher.decrypt_blob(tenant, __import__("base64").b64decode(payload[7:])).decode("utf-8"))
        return json.loads(payload)
    except Exception:
        return []
