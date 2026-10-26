"""Tenant isolation and multi-instance primitives (V11 P6)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tenant import TENANT, normalize_tenant, scoped_id  # noqa: E402
from folding import FoldArchive  # noqa: E402
from shared_cache import SharedCache  # noqa: E402
from cost_tracker import CostTracker  # noqa: E402
from storage_backend import SqliteBackend  # noqa: E402


class TenantBase(unittest.TestCase):
    def setUp(self):
        TENANT.set("default")
        self.tmp = tempfile.mkdtemp(prefix="nf-tenant-")

    def tearDown(self):
        TENANT.set("default")


class TestTenantIds(TenantBase):
    def test_normalize(self):
        self.assertEqual(normalize_tenant(" Acme Corp! "), "Acme-Corp")
        self.assertEqual(normalize_tenant(""), "default")

    def test_scoped(self):
        TENANT.set("acme")
        self.assertEqual(scoped_id("s1"), "acme:s1")
        self.assertEqual(scoped_id("acme:s1"), "acme:s1")


class TestFoldIsolation(TenantBase):
    def test_cross_tenant_read_denied(self):
        archive = FoldArchive(Path(self.tmp) / "folds.db")
        TENANT.set("acme")
        ref = archive.put("s1", "compaction", [{"role": "user", "content": "secret"}])
        self.assertIsNotNone(archive.get(ref, tenant_id="acme"))
        self.assertIsNone(archive.get(ref, tenant_id="other"))
        self.assertIsNone(archive.get(ref, tenant_id="default"))
        archive.close()

    def test_refs_differ_for_same_content(self):
        a = FoldArchive(Path(self.tmp) / "refs.db")
        msgs = [{"role": "user", "content": "same"}]
        TENANT.set("acme"); r1 = a.put("s1", "compaction", msgs)
        TENANT.set("beta"); r2 = a.put("s1", "compaction", msgs)
        self.assertNotEqual(r1, r2)
        a.close()


class TestSharedCacheTenant(TenantBase):
    def test_tenant_partitioned(self):
        cache = SharedCache()
        msgs = [{"role": "user", "content": "hello"}]
        TENANT.set("acme")
        cache.put(msgs, "summary-acme")
        self.assertEqual(cache.get(msgs), "summary-acme")
        TENANT.set("beta")
        self.assertIsNone(cache.get(msgs))
        cache.put(msgs, "summary-beta")
        self.assertEqual(cache.get(msgs), "summary-beta")
        TENANT.set("acme")
        self.assertEqual(cache.get(msgs), "summary-acme")


class TestCostTenant(TenantBase):
    def test_by_tenant(self):
        t = CostTracker()
        t.record("s1", "m", 100, 20, 30, 5, tenant_id="acme")
        t.record("s2", "m", 100, 20, 30, 5, tenant_id="beta")
        s = t.summary()
        self.assertEqual(s["by_tenant"]["acme"]["calls"], 1)
        self.assertEqual(s["by_tenant"]["beta"]["calls"], 1)


class TestStorageBackend(TenantBase):
    def test_sqlite_roundtrip(self):
        db = SqliteBackend(str(Path(self.tmp) / "shared.db"))
        db.save_session("acme:s1", "summary text", "{}", 3)
        row = db.get_session("acme:s1")
        self.assertIsNotNone(row)
        self.assertEqual(row["summary"], "summary text")
        self.assertTrue(any(s["id"] == "acme:s1" for s in db.list_sessions()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
