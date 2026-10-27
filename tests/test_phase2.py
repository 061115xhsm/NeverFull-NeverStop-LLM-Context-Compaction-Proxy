"""Phase 2: async candidate hot-swap + virtual restore tool helpers."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="nf-p2-")
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

spec = importlib.util.spec_from_file_location("compaction_proxy_p2", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_p2"] = cp
spec.loader.exec_module(cp)

from virtual_tool import build_restore_messages, extract_restore_calls, inject_restore_tool  # noqa: E402


class FakeArchive:
    def get(self, ref, tenant_id=None):
        if ref == "fold:ok":
            return {"messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]}
        return None


class TestVirtualTool(unittest.TestCase):
    def test_inject_idempotent(self):
        body = {}
        inject_restore_tool(body)
        self.assertEqual(len(body["tools"]), 1)
        inject_restore_tool(body)
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["function"]["name"], "neverfull_restore_context")

    def test_extract_calls(self):
        data = {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "neverfull_restore_context",
                          "arguments": '{"fold_ref":"fold:ok","limit":1}'}}
        ]}}]}
        calls = extract_restore_calls(data)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"]["fold_ref"], "fold:ok")

    def test_build_messages_found(self):
        msgs = build_restore_messages(
            [{"id": "c1", "args": {"fold_ref": "fold:ok", "limit": 1}}],
            FakeArchive(), tenant="acme")
        self.assertEqual(msgs[0]["role"], "tool")
        payload = json.loads(msgs[0]["content"])
        self.assertTrue(payload["found"])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(len(payload["messages"]), 1)

    def test_build_messages_missing(self):
        msgs = build_restore_messages([{"id": "c1", "args": {"fold_ref": "fold:nope"}}], FakeArchive())
        payload = json.loads(msgs[0]["content"])
        self.assertFalse(payload["found"])
        self.assertIn("error", payload)


class TestAsyncCandidate(unittest.TestCase):
    def setUp(self):
        cp._async_candidates.clear()

    def test_hot_swap_appends_tail(self):
        cp._async_candidates["s1"] = {
            "messages": [{"role": "user", "content": "checkpoint"}],
            "msg_count": 2, "evidence": 0.9, "ready_at": 0,
        }
        body = {"messages": [
            {"role": "user", "content": "m1"},
            {"role": "assistant", "content": "m2"},
            {"role": "user", "content": "tail"},
        ]}
        self.assertTrue(cp.apply_async_candidate(body, "s1"))
        self.assertEqual([m["content"] for m in body["messages"]], ["checkpoint", "tail"])
        self.assertFalse(cp.apply_async_candidate(body, "s1"))

    def test_small_request_not_scheduled(self):
        body = {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}]}
        self.assertFalse(cp.schedule_async_compaction(body, "s2", "k", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
