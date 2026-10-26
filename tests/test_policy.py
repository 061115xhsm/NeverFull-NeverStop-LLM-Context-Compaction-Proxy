"""Tests for the adaptive policy and capability profiles (V11 P4/P5)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy import ACTIONS, ContextPolicy, classify_task, effective_threshold  # noqa: E402


class TestTaskClass(unittest.TestCase):
    def test_code(self):
        msgs = [{"role": "user", "content": "def foo():\n    return 1"}]
        self.assertEqual(classify_task(msgs), "code")

    def test_data(self):
        msgs = [{"role": "user", "content": '{"a": 1, "b": [1,2,3], "c": "x"}'}]
        self.assertEqual(classify_task(msgs), "data")

    def test_ops(self):
        msgs = [{"role": "assistant", "content": [
            {"type": "tool_use", "name": "bash", "input": {"command": "systemctl restart app"}}]}]
        self.assertEqual(classify_task(msgs), "ops")

    def test_prose(self):
        msgs = [{"role": "user", "content": "Please summarize the meeting notes."}]
        self.assertEqual(classify_task(msgs), "prose")


class TestThreshold(unittest.TestCase):
    def test_legacy_sentinel(self):
        self.assertEqual(effective_threshold("m", "", 128_000), -1.0)

    def test_profiles(self):
        self.assertAlmostEqual(effective_threshold("m", "weak", 200_000), 0.75)
        self.assertAlmostEqual(effective_threshold("m", "strong", 8_000), 0.90)

    def test_auto_by_window(self):
        self.assertAlmostEqual(effective_threshold("m", "auto", 256_000), 0.90)
        self.assertAlmostEqual(effective_threshold("m", "auto", 128_000), 0.85)
        self.assertAlmostEqual(effective_threshold("m", "auto", 32_000), 0.78)


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nf-policy-")
        self.p = ContextPolicy(Path(self.tmp) / "policy.json", epsilon=0.0, floor=0.75, seed=1)

    def test_choice_valid_and_arms_explored(self):
        seen = set()
        for _ in range(6):
            a = self.p.choose("code", "s1")
            self.assertIn(a, ACTIONS)
            seen.add(a)
            self.p.reward("code", a, 0.8)
        self.assertEqual(seen, set(ACTIONS))

    def test_reward_updates_mean(self):
        self.p.reward("code", "light", 1.0)
        self.p.reward("code", "light", 0.5)
        stats = self.p.stats()["tasks"]["code"]["light"]
        self.assertEqual(stats["n"], 2)
        self.assertAlmostEqual(stats["mean"], 0.75)

    def test_floor_bias_forces_light(self):
        self.p.observe_quality("s1", 0.40)
        self.assertEqual(self.p.choose("code", "s1"), "light")

    def test_persistence(self):
        self.p.reward("data", "deep", 0.9)
        p2 = ContextPolicy(Path(self.tmp) / "policy.json", epsilon=0.0, floor=0.75, seed=1)
        self.assertEqual(p2.stats()["tasks"]["data"]["deep"]["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
