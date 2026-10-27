"""Phase 3: AST symbol freshness + chunk fallback."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="nf-p3-")
for k, v in {
    "COMPACTION_PROXY_STATE_DB": str(Path(TMP) / "state.db"),
}.items():
    os.environ[k] = v

from state_table import StateTable  # noqa: E402

CODE1 = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
CODE2 = "def foo():\n    return 3\n\ndef bar():\n    return 2\n"


def read_msgs(tid: str, path: str, content: str):
    return [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": "read", "input": {"path": path}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content}]},
    ]


class TestAstSymbols(unittest.TestCase):
    def test_only_changed_symbol_stale(self):
        table = StateTable(Path(TMP) / "ast.db")
        path = "/tmp/proj/core.py"
        table.update("s1", read_msgs("t1", path, CODE1), now=1.0)
        table.update("s1", read_msgs("t2", path, CODE2), now=2.0)
        out = table.render("s1")
        self.assertIn("foo", out)
        self.assertIn("symbols stale", out)
        self.assertIn("1/2", out)
        table.close()

    def test_chunk_fallback_for_non_python(self):
        table = StateTable(Path(TMP) / "chunk.db")
        path = "/tmp/proj/app.ts"
        table.update("s1", read_msgs("t1", path, "a\n" * 60), now=1.0)
        table.update("s1", read_msgs("t2", path, "b\n" + "a\n" * 59), now=2.0)
        out = table.render("s1")
        self.assertIn("chunks stale", out)
        table.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
