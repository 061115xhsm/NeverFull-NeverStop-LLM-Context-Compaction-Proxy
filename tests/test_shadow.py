"""Shadow mode: observe-only compaction estimates (never mutates requests)."""

import copy
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="nf-shadow-")
os.environ["COMPACTION_PROXY_SESSION_DB"] = str(Path(TMP) / "sessions.db")
os.environ["COMPACTION_PROXY_SEMANTIC_MEMORY"] = str(Path(TMP) / "sem.json")
os.environ["COMPACTION_PROXY_USER_PROFILE"] = str(Path(TMP) / "profile.md")
os.environ["COMPACTION_PROXY_FOLD_DB"] = str(Path(TMP) / "folds.db")
os.environ["COMPACTION_PROXY_STATE_DB"] = str(Path(TMP) / "state.db")
os.environ["COMPACTION_PROXY_CALIBRATION_DB"] = str(Path(TMP) / "cal.json")
os.environ["COMPACTION_PROXY_LOG_LEVEL"] = "ERROR"

spec = importlib.util.spec_from_file_location("compaction_proxy_shadow", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_shadow"] = cp
spec.loader.exec_module(cp)


class TestShadowProbe(unittest.TestCase):
    def test_small_request_no_compaction(self):
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}
        before = copy.deepcopy(body)
        before_count = cp.metrics.get("shadow_requests")
        out = cp.shadow_probe(body, "s1")
        self.assertFalse(out["would_compact"])
        self.assertEqual(body, before)  # never mutates
        self.assertEqual(cp.metrics.get("shadow_requests"), before_count + 1)

    def test_large_request_projects_savings(self):
        body = {"model": "gpt-3.5-turbo", "messages": [
            {"role": "user", "content": "x" * 5000} for _ in range(20)]}
        before = copy.deepcopy(body)
        out = cp.shadow_probe(body, "s2")
        self.assertTrue(out["would_compact"])
        self.assertGreater(out["projected_saved"], 0)
        self.assertEqual(body, before)
        self.assertGreaterEqual(cp.metrics.get("shadow_potential_saved_tokens"), out["projected_saved"])

    def test_shadow_mode_flag_default_off(self):
        self.assertFalse(cp.SHADOW_MODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
