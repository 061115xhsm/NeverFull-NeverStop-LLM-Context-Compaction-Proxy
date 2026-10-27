"""PostgreSQL session store integration tests (skips when PG is unreachable)."""

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("NF_PG_DSN", "postgresql://postgres:postgres@127.0.0.1:54329/nf")

PG_OK = False
try:
    import psycopg2
    psycopg2.connect(DSN).close()
    PG_OK = True
except Exception:
    PG_OK = False

if PG_OK:
    from session_store_pg import PostgresSessionStore  # noqa: E402


@unittest.skipUnless(PG_OK, "postgres not reachable (set NF_PG_DSN)")
class TestPgSessionStore(unittest.TestCase):
    def setUp(self):
        self.tag = "tpg-" + uuid.uuid4().hex[:8]
        self.a = PostgresSessionStore(DSN, tenant_max_sessions=2)
        self.b = PostgresSessionStore(DSN)

    def tearDown(self):
        for store in (self.a, self.b):
            try:
                with store._lock, store._conn.cursor() as cur:
                    cur.execute("DELETE FROM sessions WHERE session_id LIKE %s OR session_id LIKE %s",
                                (f"{self.tag}:%", f"default:{self.tag}:%"))
                    cur.execute("DELETE FROM prior_summaries WHERE session_id LIKE %s", (f"%{self.tag}%",))
            except Exception:
                pass
            store.close()

    def test_cross_instance_read(self):
        self.a.save_session(f"{self.tag}:s1", "we used sqlite storage", {"goals": ["x"]}, 3)
        row = self.b.get_session(f"{self.tag}:s1")
        self.assertIsNotNone(row)
        self.assertEqual(row["summary"], "we used sqlite storage")
        self.assertEqual(row["semantic_memory"].get("goals"), ["x"])

    def test_prior_summary_roundtrip(self):
        self.a.save_prior_summary(f"{self.tag}:s1", "prior text", 12)
        summary, count = self.b.get_prior_summary(f"{self.tag}:s1")
        self.assertEqual(summary, "prior text")
        self.assertEqual(count, 12)

    def test_search_finds_summary(self):
        self.a.save_session(f"{self.tag}:s2", "we decided to use zod for validation", {}, 2)
        rows = self.b.search_sessions("zod", limit=5)
        self.assertTrue(any(self.tag in r["session_id"] for r in rows))

    def test_transcript_roundtrip(self):
        msgs = [{"role": "user", "content": "hello"}]
        self.a.save_transcript(f"{self.tag}:s1", msgs)
        self.assertEqual(self.b.get_transcript(f"{self.tag}:s1"), msgs)

    def test_tenant_trim(self):
        for i in range(4):
            self.a.save_session(f"{self.tag}:t{i}", f"s{i}", {}, i)
        with self.b._lock, self.b._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sessions WHERE tenant_id=%s", (self.tag,))
            self.assertLessEqual(cur.fetchone()[0], 2)
        # the two newest must survive, the oldest two may be trimmed
        self.assertIsNotNone(self.b.get_session(f"{self.tag}:t3"))
        self.assertIsNotNone(self.b.get_session(f"{self.tag}:t2"))

    def test_session_count_property(self):
        self.a.save_session(f"{self.tag}:c1", "x", {}, 1)
        self.assertGreaterEqual(self.a.session_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(PG_OK, "postgres not reachable")
class TestPgFoldArchive(unittest.TestCase):
    def setUp(self):
        from folding_pg import PgFoldArchive
        self.archive = PgFoldArchive(DSN)

    def tearDown(self):
        try:
            self.archive._run("DELETE FROM folds WHERE session_id LIKE %s", ("tpg-fold-%",))
        except Exception:
            pass
        self.archive.close()

    def test_roundtrip_and_tenant_isolation(self):
        ref = self.archive.put("tpg-fold-s1", "compaction",
                               [{"role": "user", "content": "secret pg"}], tenant_id="acme")
        got = self.archive.get(ref, tenant_id="acme")
        self.assertIsNotNone(got)
        self.assertEqual(got["messages"][0]["content"], "secret pg")
        self.assertIsNone(self.archive.get(ref, tenant_id="beta"))

    def test_stats_and_rotate(self):
        self.archive.put("tpg-fold-s2", "compaction", [{"role": "user", "content": "x"}])
        self.assertGreaterEqual(self.archive.stats()["entries"], 1)
        self.assertGreaterEqual(self.archive.rotate_key(), 0)
