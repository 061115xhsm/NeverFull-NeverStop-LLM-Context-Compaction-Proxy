"""Tests for the paired evaluation harness (V11 P3)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import paired_eval  # noqa: E402


def long_conversation() -> list:
    msgs = [{"role": "user", "content":
             "We are working on /tmp/proj/src/core.py. Must keep offline mode enabled."}]
    for i in range(30):
        msgs.append({"role": "user", "content": f"filler message {i} " + "x" * 100})
    msgs.append({"role": "assistant", "content": [
        {"type": "tool_use", "name": "exec", "input": {"command": "pytest -q"}}]})
    msgs.append({"role": "assistant", "content": "We decided to use sqlite for the cache."})
    return msgs


class TestPairedEval(unittest.TestCase):
    def test_probes_extracted(self):
        probes = paired_eval.probes_from_messages(long_conversation())
        kinds = {p["kind"] for p in probes}
        self.assertIn("file", kinds)
        self.assertTrue(any("pytest" in p["span"] for p in probes))

    def test_fold_keeps_evidence_truncate_loses_it(self):
        conv = [{"id": "t1", "messages": long_conversation()}]
        result = paired_eval.run(conv)
        s = result["summary"]
        self.assertEqual(s["fold"]["recall"], 1.0)
        self.assertLess(s["truncate"]["recall"], 1.0)
        self.assertGreater(s["fold"]["compression"], 0.0)
        for row in s.values():
            self.assertGreaterEqual(row["recall"], 0.0)
            self.assertLessEqual(row["recall"], 1.0)

    def test_markdown_contains_table(self):
        result = paired_eval.run([{"id": "t1", "messages": long_conversation()}])
        md = paired_eval.to_markdown(result)
        self.assertIn("| strategy | compression | evidence recall |", md)
        self.assertIn("fold", md)

    def test_empty_conversation_is_skipped(self):
        result = paired_eval.run([{"id": "empty", "messages": []}])
        self.assertEqual(result["summary"]["fold"]["views"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
