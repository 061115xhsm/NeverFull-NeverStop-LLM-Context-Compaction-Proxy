"""Phase 0: cache-usage parsing, effective-cost metering, hit-ratio metrics."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="nf-p0-")
for k, v in {
    "COMPACTION_PROXY_SESSION_DB": str(Path(TMP) / "sessions.db"),
    "COMPACTION_PROXY_SEMANTIC_MEMORY": str(Path(TMP) / "sem.json"),
    "COMPACTION_PROXY_USER_PROFILE": str(Path(TMP) / "profile.md"),
    "COMPACTION_PROXY_FOLD_DB": str(Path(TMP) / "folds.db"),
    "COMPACTION_PROXY_STATE_DB": str(Path(TMP) / "state.db"),
    "COMPACTION_PROXY_CALIBRATION_DB": str(Path(TMP) / "cal.json"),
    "COMPACTION_PROXY_LOG_LEVEL": "ERROR",
}.items():
    os.environ[k] = v

spec = importlib.util.spec_from_file_location("compaction_proxy_p0", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_p0"] = cp
spec.loader.exec_module(cp)


class TestParseUsage(unittest.TestCase):
    def test_openai_shape(self):
        d = {"usage": {"prompt_tokens": 1000, "completion_tokens": 50,
                       "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}}
        u = cp.parse_upstream_usage(d)
        self.assertEqual(u, {"prompt": 1000, "completion": 50, "cached": 800, "cache_write": 100})

    def test_anthropic_shape(self):
        d = {"usage": {"input_tokens": 500, "output_tokens": 20,
                       "cache_read_input_tokens": 400, "cache_creation_input_tokens": 60}}
        u = cp.parse_upstream_usage(d)
        self.assertEqual(u, {"prompt": 500, "completion": 20, "cached": 400, "cache_write": 60})

    def test_gemini_shape(self):
        d = {"usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 10,
                               "cachedContentTokenCount": 250}}
        u = cp.parse_upstream_usage(d)
        self.assertEqual(u, {"prompt": 300, "completion": 10, "cached": 250, "cache_write": 0})

    def test_empty(self):
        self.assertEqual(cp.parse_upstream_usage({}), {"prompt": 0, "completion": 0, "cached": 0, "cache_write": 0})
        self.assertEqual(cp.parse_upstream_usage(None), {"prompt": 0, "completion": 0, "cached": 0, "cache_write": 0})


class TestUpstreamMetering(unittest.TestCase):
    def test_counters(self):
        before_n = cp.metrics.get("upstream_cache_hit_ratio_n")
        cp.record_upstream_usage("m", {"prompt": 1000, "completion": 50, "cached": 800, "cache_write": 0})
        self.assertEqual(cp.metrics.get("upstream_cached_read_tokens_total"), 800)
        self.assertEqual(cp.metrics.get("upstream_cache_hit_ratio_n"), before_n + 1)

    def test_empty_ignored(self):
        before = cp.metrics.get("upstream_prompt_tokens_total")
        cp.record_upstream_usage("m", {})
        self.assertEqual(cp.metrics.get("upstream_prompt_tokens_total"), before)


class TestEffectiveCost(unittest.TestCase):
    def test_effective_cost_with_cache(self):
        cp.COMPACTION_USAGE.set({"prompt": 1000, "completion": 100, "cached": 800, "cache_write": 0,
                                 "model": "m"})
        cp.cost_tracker._events.clear()
        cp.record_compaction_cost([{"role": "user", "content": "x" * 400}], "short", 5.0)
        ev = cp.cost_tracker.summary()["last"]
        # (1000-800)*1.0 + 800*0.1 = 280 input + 100 output = 380
        self.assertEqual(ev["effective_cost"], 380)
        self.assertEqual(cp.metrics.get("compaction_effective_cost_tokens_total"), 380)
        cp.COMPACTION_USAGE.set(None)

    def test_fallback_estimate(self):
        cp.COMPACTION_USAGE.set(None)
        cp.cost_tracker._events.clear()
        cp.record_compaction_cost([{"role": "user", "content": "x" * 400}], "short", 5.0)
        ev = cp.cost_tracker.summary()["last"]
        self.assertEqual(ev["effective_cost"], ev["cost"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
