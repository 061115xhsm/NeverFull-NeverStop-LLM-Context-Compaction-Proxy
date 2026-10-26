"""Per-tenant quota tests (V11 P6)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cost_tracker import DailyQuota  # noqa: E402
from folding import FoldArchive  # noqa: E402
from tenant import TENANT  # noqa: E402


class TestFoldQuota(unittest.TestCase):
    def setUp(self):
        TENANT.set("default")
        self.tmp = tempfile.mkdtemp(prefix="nf-quota-")

    def tearDown(self):
        TENANT.set("default")

    def test_trim_per_tenant(self):
        a = FoldArchive(Path(self.tmp) / "folds.db")
        TENANT.set("acme")
        for i in range(4):
            a.put("s1", "compaction", [{"role": "user", "content": f"m{i}"}], max_per_tenant=2)
        self.assertEqual(a.stats()["entries"], 2)
        TENANT.set("beta")
        a.put("s9", "compaction", [{"role": "user", "content": "b"}], max_per_tenant=2)
        self.assertEqual(a.stats()["entries"], 3)  # beta unaffected by acme cap
        a.close()


class TestDailyQuota(unittest.TestCase):
    def test_add_and_exceed(self):
        q = DailyQuota(100)
        self.assertFalse(q.exceeded("acme"))
        q.add("acme", 60)
        self.assertEqual(q.used("acme"), 60)
        self.assertFalse(q.exceeded("acme"))
        q.add("acme", 50)
        self.assertEqual(q.used("acme"), 110)
        self.assertTrue(q.exceeded("acme"))
        self.assertFalse(q.exceeded("beta"))

    def test_disabled(self):
        q = DailyQuota(0)
        q.add("acme", 999)
        self.assertEqual(q.used("acme"), 0)
        self.assertFalse(q.exceeded("acme"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
