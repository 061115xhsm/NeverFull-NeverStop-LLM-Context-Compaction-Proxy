"""Characterization tests for the V11 core pipeline.

Run:  python -m unittest discover -s tests -v
Requires: aiohttp (see requirements.txt).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="nf-core-")
os.environ["COMPACTION_PROXY_SESSION_DB"] = str(Path(TMP) / "sessions.db")
os.environ["COMPACTION_PROXY_SEMANTIC_MEMORY"] = str(Path(TMP) / "semantic-memory.json")
os.environ["COMPACTION_PROXY_USER_PROFILE"] = str(Path(TMP) / "user-profile.md")
os.environ["COMPACTION_PROXY_FOLD_DB"] = str(Path(TMP) / "folds.db")
os.environ["COMPACTION_PROXY_LOG_LEVEL"] = "ERROR"
os.environ.setdefault("COMPACTION_PROXY_FOLDING", "0")

spec = importlib.util.spec_from_file_location("compaction_proxy", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy"] = cp
spec.loader.exec_module(cp)


class TestTokenEstimation(unittest.TestCase):
    def test_positive(self):
        self.assertGreater(cp.estimate_tokens_v3("hello world"), 0)

    def test_cjk_heavier_than_ascii(self):
        cn = cp.estimate_tokens_v3("这是一个用于测试的中文句子")
        en = cp.estimate_tokens_v3("this is a test sentence ab")
        self.assertGreater(cn, en)

    def test_messages_estimate(self):
        msgs = [{"role": "user", "content": "hi" * 100}]
        self.assertGreater(cp.estimate_messages_tokens(msgs), 0)
        self.assertGreater(cp.estimate_tokens_accurate(msgs), 0)


class TestToolPairSanitization(unittest.TestCase):
    def test_valid_pair_preserved(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "a.py"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        ]
        out = cp.sanitize_tool_pairs(msgs)
        self.assertTrue(any(
            isinstance(m.get("content"), list) and
            any(b.get("tool_use_id") == "t1" for b in m["content"] if isinstance(b, dict))
            for m in out
        ))

    def test_orphan_result_removed(self):
        msgs = [
            {"role": "assistant", "content": "no tools"},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "ghost", "content": "orphan"}]},
        ]
        out = cp.sanitize_tool_pairs(msgs)
        for m in out:
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        self.assertNotEqual(b.get("tool_use_id"), "ghost")

    def test_missing_result_gets_placeholder(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t9", "name": "read", "input": {}}]},
            {"role": "user", "content": "continue"},
        ]
        out = cp.sanitize_tool_pairs(msgs)
        ids = []
        for m in out:
            c = m.get("content")
            if isinstance(c, list):
                ids += [b.get("tool_use_id") for b in c if isinstance(b, dict)]
            if m.get("role") == "tool":
                ids.append(m.get("tool_call_id"))
        self.assertIn("t9", ids)


class TestExtractors(unittest.TestCase):
    def test_identifiers_find_path(self):
        msgs = [{"role": "user", "content": "edit /tmp/project/src/main.py and run `pytest -q`"}]
        ids = cp.extract_identifiers(msgs)
        self.assertTrue(any("main.py" in i for i in ids))

    def test_commitments_find_constraint_and_decision(self):
        msgs = [{"role": "user", "content": "We must not delete the cache. We decided to use PostgreSQL."}]
        cs = cp.extract_commitments(msgs)
        types = {c[0] for c in cs}
        self.assertIn("constraint", types)
        self.assertIn("decision", types)


class TestCompressors(unittest.TestCase):
    def test_json_output_stays_valid(self):
        data = {"items": [{"id": i, "blob": "x" * 300} for i in range(30)], "note": "y" * 600}
        text = json.dumps(data)
        out = cp.compress_json_output(text, budget=2000)
        self.assertLess(len(out), len(text))
        json.loads(out)  # must remain parseable

    def test_log_output_keeps_only_important_lines(self):
        lines = ["INFO noisy line %d" % i for i in range(200)]
        lines += ["ERROR: database unavailable", "Traceback (most recent call last)"]
        text = "\n".join(lines)
        out = cp.compress_log_output(text, budget=400)
        self.assertLess(len(out), len(text))
        self.assertIn("ERROR", out)

    def test_classify_tool_fidelity(self):
        self.assertEqual(cp.classify_tool_fidelity("apply_patch"), cp.FIDELITY_FULL)
        self.assertEqual(cp.classify_tool_fidelity("ls"), cp.FIDELITY_PLACEHOLDER)


class TestSplitting(unittest.TestCase):
    def test_split_keeps_system_in_recent_and_order(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(4):
            msgs.append({"role": "user", "content": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        old, recent = cp.split_messages(msgs, 2)
        self.assertTrue(old)
        self.assertEqual(recent[0]["role"], "system")
        joined = [m["content"] for m in old] + [m["content"] for m in recent if m["role"] != "system"]
        self.assertEqual(joined, [m["content"] for m in msgs if m["role"] != "system"])


class TestSafety(unittest.TestCase):
    def test_rejects_growth(self):
        orig = {"messages": [{"role": "user", "content": "short"}]}
        bigger = {"messages": [{"role": "user", "content": "short"}, {"role": "user", "content": "x" * 5000}]}
        self.assertFalse(cp.verify_compaction_safety(orig, bigger))

    def test_accepts_shrink(self):
        orig = {"messages": [{"role": "user", "content": "x" * 5000}]}
        small = {"messages": [{"role": "user", "content": "x"}]}
        self.assertTrue(cp.verify_compaction_safety(orig, small))


class TestSessionId(unittest.TestCase):
    def test_header_priority(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        self.assertEqual(cp.extract_session_id(body, {"X-Session-Id": "abc"}), "default:abc")

    def test_fingerprint_stable(self):
        body = {"model": "m", "messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hello"},
        ]}
        a = cp.extract_session_id(body, {})
        b = cp.extract_session_id(body, {})
        self.assertEqual(a, b)

    def test_user_field_isolates(self):
        base = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        a = cp.extract_session_id({**base, "user": "alice"}, {})
        b = cp.extract_session_id({**base, "user": "bob"}, {})
        self.assertNotEqual(a, b)


class TestAssembledMessages(unittest.TestCase):
    def test_summary_included(self):
        out = cp.build_compacted_messages("MY UNIQUE SUMMARY", [{"role": "user", "content": "hi"}])
        self.assertTrue(any("MY UNIQUE SUMMARY" in str(m.get("content", "")) for m in out))


if __name__ == "__main__":
    unittest.main(verbosity=2)
