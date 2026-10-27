"""Span-level observation dedup tests."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dedupe import dedupe_observations  # noqa: E402


class TestDedupe(unittest.TestCase):
    def test_duplicate_observation_replaced(self):
        text = "x" * 800
        msgs = [
            {"role": "tool", "content": text},
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": text},
        ]
        out, saved = dedupe_observations(msgs)
        self.assertGreater(saved, 0)
        self.assertIn("<dup_stub", out[2]["content"])
        self.assertEqual(out[0]["content"], text)
        self.assertEqual(out[1]["content"], "hello")

    def test_short_or_unique_kept(self):
        msgs = [
            {"role": "tool", "content": "short"},
            {"role": "tool", "content": "y" * 800},
        ]
        out, saved = dedupe_observations(msgs)
        self.assertEqual(saved, 0)
        self.assertEqual(out, msgs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
