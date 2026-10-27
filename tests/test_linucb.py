"""LinUCB policy tests."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linucb import ACTIONS, DisjointLinUCB  # noqa: E402


class TestLinUCB(unittest.TestCase):
    def test_choose_valid(self):
        p = DisjointLinUCB(dim=4, path=Path(tempfile.mkdtemp()) / "l.json")
        arm = p.choose([0.5, 0.1, 0.2, 0.0])
        self.assertIn(arm, ACTIONS)

    def test_update_prefers_rewarded_arm(self):
        p = DisjointLinUCB(dim=3, alpha=0.0, path=Path(tempfile.mkdtemp()) / "l.json")
        x = [1.0, 0.5, 0.2]
        for _ in range(20):
            p.update("dedup_only", x, 1.0)
            p.update("slipstream_deep", x, 0.0)
        self.assertEqual(p.choose(x), "dedup_only")

    def test_persistence(self):
        path = Path(tempfile.mkdtemp()) / "l.json"
        p = DisjointLinUCB(dim=2, path=path)
        p.update("refold_chunk", [1.0, 1.0], 0.8)
        q = DisjointLinUCB(dim=2, path=path)
        self.assertGreaterEqual(q.stats()["refold_chunk"]["samples"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
