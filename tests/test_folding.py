"""Tests for the V11 reversible folding module (no aiohttp required)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import folding  # noqa: E402


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nf-fold-")
        self.archive = folding.FoldArchive(Path(self.tmp) / "folds.db")

    def tearDown(self):
        self.archive.close()

    def test_roundtrip(self):
        msgs = [{"role": "user", "content": "hello"}]
        ref = self.archive.put("s1", "compaction", msgs)
        self.assertTrue(ref.startswith("fold:"))
        got = self.archive.get(ref)
        self.assertEqual(got["messages"], msgs)
        self.assertEqual(got["session_id"], "s1")

    def test_stats(self):
        self.archive.put("s1", "compaction", [{"role": "user", "content": "x" * 100}])
        stats = self.archive.stats()
        self.assertEqual(stats["entries"], 1)
        self.assertGreater(stats["tokens_archived"], 0)

    def test_missing_ref(self):
        self.assertIsNone(self.archive.get("fold:does-not-exist"))


class TestDigest(unittest.TestCase):
    def test_counts_tools_files_commands(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "exec",
                 "input": {"command": "pytest -q", "path": "src/main.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ERROR: 2 failed"}]},
            {"role": "assistant", "content": "We decided to use /tmp/project/src/app.py instead."},
        ]
        d = folding.build_fold_digest(msgs, commitments=[("decision", "use src/app.py")])
        self.assertEqual(d["n_messages"], 3)
        self.assertEqual(d["tools"].get("exec"), 1)
        self.assertIn("src/main.py", d["files"])
        self.assertIn("pytest -q", d["commands"])
        self.assertGreaterEqual(d["errors"], 1)
        self.assertEqual(d["commitments"].get("decision"), 1)

    def test_render_contains_ref_and_retrieve(self):
        d = folding.build_fold_digest([{"role": "user", "content": "hi"}])
        out = folding.render_fold_block(d, "fold:abc123")
        self.assertIn('ref="fold:abc123"', out)
        self.assertIn("GET /fold/fold:abc123", out)
        self.assertIn("<folded_context", out)


class TestCommitmentContract(unittest.TestCase):
    def test_kept_and_missing(self):
        rendered = "### DECISIONS\nUse PostgreSQL for storage.\nPath: /tmp/a.py"
        commitments = [
            ("decision", "Use PostgreSQL for storage"),
            ("constraint", "never delete the cache directory"),
        ]
        identifiers = ["/tmp/a.py", "/tmp/missing.py"]
        rep = folding.verify_commitments(rendered, commitments, identifiers)
        self.assertEqual(rep["total"], 2)
        self.assertEqual(rep["kept"], 1)
        self.assertEqual(len(rep["missing"]), 1)
        self.assertEqual(rep["identifiers_kept"], 1)
        self.assertEqual(rep["identifiers_missing"], ["/tmp/missing.py"])

    def test_missing_note(self):
        rep = folding.verify_commitments("nothing", [("goal", "ship v11")], [])
        note = folding.format_missing_note(rep)
        self.assertIn("<unpreserved_commitments>", note)
        self.assertIn("ship v11", note)

    def test_perfect_report_has_empty_note(self):
        rep = folding.verify_commitments("ship v11", [("goal", "ship v11")], [])
        self.assertEqual(folding.format_missing_note(rep), "")


class TestSections(unittest.TestCase):
    def test_parse(self):
        text = (
            "### FACTS\nfile a.py has 3 lines\n"
            "### DECISIONS\nuse sqlite\n"
            "### OPEN\nnone\n"
            "### COMPRESSOR_INFERENCE\nguess\n"
            "### COMMITMENTS\nmust keep /tmp/a.py\n"
        )
        secs = folding.parse_sections(text)
        self.assertIn("FACTS", secs)
        self.assertIn("use sqlite", secs["DECISIONS"])
        self.assertIn("/tmp/a.py", secs["COMMITMENTS"])

    def test_unlabelled_falls_back(self):
        secs = folding.parse_sections("just a summary")
        self.assertEqual(secs["_preamble"], "just a summary")


if __name__ == "__main__":
    unittest.main(verbosity=2)
