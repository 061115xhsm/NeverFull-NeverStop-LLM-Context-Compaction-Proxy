"""Tests for the state table (V11 P1b)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from state_table import StateTable  # noqa: E402


class TestStateTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nf-state-")
        self.db = StateTable(Path(self.tmp) / "state.db")
        self.sid = "s1"

    def tearDown(self):
        self.db.close()

    def test_tracks_edits_and_reads(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "read", "input": {"path": "src/a.py"}}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "edit", "input": {"path": "src/a.py"}}]},
        ]
        self.db.update(self.sid, msgs, now=1000.0)
        data = self.db.get(self.sid)
        self.assertEqual(len(data["files"]), 1)
        f = data["files"][0]
        self.assertEqual(f["path"], "src/a.py")
        self.assertEqual(f["edits"], 1)
        self.assertEqual(f["reads"], 1)
        self.assertEqual(f["last_op"], "edit")

    def test_render_marks_stale_reads(self):
        self.db.update(self.sid, [{"role": "assistant", "content": [
            {"type": "tool_use", "name": "edit", "input": {"path": "src/a.py"}}]}], now=1000.0)
        out = self.db.render(self.sid)
        self.assertIn("<state_snapshot>", out)
        self.assertIn("src/a.py", out)
        self.assertIn("earlier reads stale", out)

    def test_decisions_active_and_superseded(self):
        self.db.update(self.sid, [{"role": "user", "content":
            "We decided to use PostgreSQL for storage"}], now=1000.0)
        self.db.update(self.sid, [{"role": "user", "content":
            "We decided to use PostgreSQL for storage now"}], now=1001.0)
        data = self.db.get(self.sid)
        statuses = [d["status"] for d in data["decisions"]]
        self.assertIn("active", statuses)
        self.assertIn("superseded", statuses)
        self.assertEqual(statuses.count("active"), 1)

    def test_duplicate_decision_not_doubled(self):
        msg = [{"role": "user", "content": "We decided to use sqlite for storage"}]
        self.db.update(self.sid, msg, now=1000.0)
        self.db.update(self.sid, msg, now=1001.0)
        data = self.db.get(self.sid)
        active = [d for d in data["decisions"] if d["status"] == "active"]
        self.assertEqual(len(active), 1)

    def test_reset_and_stats(self):
        self.db.update(self.sid, [{"role": "user", "content": "We must not delete data"}], now=1000.0)
        self.assertGreaterEqual(self.db.stats()["decisions"], 1)
        self.db.reset(self.sid)
        self.assertEqual(self.db.get(self.sid)["decisions"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
