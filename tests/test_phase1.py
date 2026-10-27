"""Phase 1: cache alignment, append-only checkpoints, trailing state frame."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="nf-p1-")
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

spec = importlib.util.spec_from_file_location("compaction_proxy_p1", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_p1"] = cp
spec.loader.exec_module(cp)

from cache_align import apply_anthropic_cache, apply_openai_cache, session_cache_key  # noqa: E402


class TestCacheAlign(unittest.TestCase):
    def test_tools_sorted(self):
        body = {"tools": [
            {"type": "function", "function": {"name": "zeta"}},
            {"type": "function", "function": {"name": "alpha"}},
        ]}
        out = apply_openai_cache(body, "acme", "s1")
        self.assertEqual([t["function"]["name"] for t in out["tools"]], ["alpha", "zeta"])

    def test_prompt_cache_key(self):
        body = {}
        apply_openai_cache(body, "acme", "s1")
        self.assertEqual(body["prompt_cache_key"], session_cache_key("acme", "s1"))
        self.assertEqual(len(body["prompt_cache_key"]), 32)
        self.assertNotEqual(session_cache_key("acme", "s1"), session_cache_key("beta", "s1"))

    def test_anthropic_breakpoints(self):
        body = {
            "tools": [{"name": "read"}],
            "system": "sys",
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
                {"role": "user", "content": "c"},
            ],
        }
        out = apply_anthropic_cache(body)
        count = 0
        if isinstance(out.get("system"), list):
            count += sum(1 for b in out["system"] if b.get("cache_control"))
        if out.get("tools"):
            count += sum(1 for t in out["tools"] if t.get("cache_control"))
        for m in out["messages"]:
            if isinstance(m.get("content"), list):
                count += sum(1 for b in m["content"] if b.get("cache_control"))
        self.assertLessEqual(count, 4)
        self.assertGreater(count, 0)


class TestCheckpoints(unittest.TestCase):
    def test_append_and_order(self):
        store = cp.SessionStore(str(Path(TMP) / "cp.db"))
        self.assertEqual(store.append_checkpoint("s1", "first"), 1)
        self.assertEqual(store.append_checkpoint("s1", "second"), 2)
        self.assertEqual(store.get_checkpoints("s1"), ["first", "second"])
        store.close()

    def test_append_only_prefix(self):
        store = cp.SessionStore(str(Path(TMP) / "cp2.db"))
        store.append_checkpoint("s1", "A" * 100)
        first = store.get_checkpoints("s1")[0]
        store.append_checkpoint("s1", "B" * 100)
        joined = "\n\n".join(store.get_checkpoints("s1"))
        self.assertTrue(joined.startswith(first))
        store.close()


class TestTrailingFrame(unittest.TestCase):
    def test_frame_at_end(self):
        msgs = cp.build_compacted_messages(
            "SUMMARY", [{"role": "user", "content": "hi"}],
            trailing_frame="<state_snapshot>FILES</state_snapshot>",
        )
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("<state_frame>", str(msgs[-1]["content"]))
        self.assertIn("FILES", str(msgs[-1]["content"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
