"""Tests for key rotation, per-request cost tracking, and protocol conversion."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="nf-extra-")
os.environ["COMPACTION_PROXY_SESSION_DB"] = str(Path(TMP) / "sessions.db")
os.environ["COMPACTION_PROXY_SEMANTIC_MEMORY"] = str(Path(TMP) / "sem.json")
os.environ["COMPACTION_PROXY_USER_PROFILE"] = str(Path(TMP) / "profile.md")
os.environ["COMPACTION_PROXY_FOLD_DB"] = str(Path(TMP) / "folds.db")
os.environ["COMPACTION_PROXY_STATE_DB"] = str(Path(TMP) / "state.db")
os.environ["COMPACTION_PROXY_CALIBRATION_DB"] = str(Path(TMP) / "cal.json")
os.environ["COMPACTION_PROXY_LOG_LEVEL"] = "ERROR"

spec = importlib.util.spec_from_file_location("compaction_proxy_extra", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_extra"] = cp
spec.loader.exec_module(cp)

from cost_tracker import CostTracker  # noqa: E402
from folding import FoldArchive  # noqa: E402


class TestKeyRotation(unittest.TestCase):
    @unittest.skipUnless(__import__("importlib").util.find_spec("cryptography"), "no cryptography")
    def test_rotate_keeps_data_readable(self):
        archive = FoldArchive(Path(TMP) / "rot.db", encrypt=True)
        msgs = [{"role": "user", "content": "rotate me"}]
        ref = archive.put("s1", "compaction", msgs)
        raw1 = archive._conn.execute("SELECT payload FROM folds WHERE ref=?", (ref,)).fetchone()[0]
        self.assertEqual(archive.rotate_key(), 1)
        raw2 = archive._conn.execute("SELECT payload FROM folds WHERE ref=?", (ref,)).fetchone()[0]
        # envelope rotation rewraps DEKs only; payload bytes stay stable
        self.assertEqual(raw1, raw2)
        self.assertTrue(raw2.startswith("env:v1:") or raw2.startswith("enc:v1:"))
        self.assertEqual(archive.get(ref)["messages"], msgs)
        archive.close()

    def test_plaintext_noop(self):
        archive = FoldArchive(Path(TMP) / "plain.db")
        archive.put("s1", "compaction", [{"role": "user", "content": "x"}])
        self.assertEqual(archive.rotate_key(), 0)
        archive.close()


class TestCostTracker(unittest.TestCase):
    def test_summary(self):
        t = CostTracker()
        t.record("s1", "m", before_tokens=1000, after_tokens=200, cost_tokens=300, latency_ms=50)
        t.record("s1", "m", before_tokens=500, after_tokens=100, cost_tokens=0, latency_ms=10, cache_hit=True)
        s = t.summary()
        self.assertEqual(s["events"], 2)
        self.assertEqual(s["saved_tokens"], 1200)
        self.assertEqual(s["cost_tokens"], 300)
        self.assertEqual(s["net_saved_tokens"], 900)
        self.assertEqual(s["cache_hits"], 1)
        self.assertEqual(s["by_model"]["m"]["calls"], 2)

    def test_empty(self):
        self.assertEqual(CostTracker().summary(), {"events": 0})


class TestProtocolConversion(unittest.TestCase):
    def test_openai_to_anthropic_request(self):
        body = {
            "model": "claude-x", "max_tokens": 128,
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
        }
        out = cp.openai_to_anthropic_request(body)
        self.assertEqual(out.get("model"), "claude-x")
        self.assertEqual(out.get("max_tokens"), 128)
        self.assertEqual(out.get("system"), "sys")
        self.assertEqual(out["messages"][0]["role"], "user")
        self.assertEqual(out["messages"][0]["content"][0]["text"], "hi")

    def test_anthropic_to_openai_response(self):
        data = {"id": "msg_1", "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4}}
        out = cp.anthropic_to_openai_response(data, model="claude-x")
        self.assertEqual(out["choices"][0]["message"]["content"], "hello")
        self.assertEqual(out["usage"]["prompt_tokens"], 3)
        self.assertEqual(out["usage"]["completion_tokens"], 4)


class TestCostWiring(unittest.TestCase):
    def test_record_updates_tracker(self):
        cp.cost_tracker._events.clear()
        cp.record_compaction_cost(
            [{"role": "user", "content": "x" * 400}], "short summary", 12.5
        )
        s = cp.cost_tracker.summary()
        self.assertEqual(s["events"], 1)
        self.assertGreaterEqual(s["saved_tokens"], 0)
        self.assertEqual(s["p50_latency_ms"], 12.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
