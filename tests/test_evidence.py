"""Tests for evidence probes (V11 P2)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence import run_evidence_probes  # noqa: E402


RENDERED = """### FACTS
Edited /tmp/proj/src/main.py and ran pytest -q.
### DECISIONS
Use sqlite for storage (done).
### OPEN
Re-run the migration.
### COMMITMENTS
Must keep /tmp/proj/src/main.py and the offline mode.
"""

MESSAGES = [
    {"role": "user", "content": "Please fix /tmp/proj/src/main.py and run pytest -q. Must keep offline mode."},
    {"role": "assistant", "content": "Done. We decided to use sqlite for storage."},
]


class TestEvidence(unittest.TestCase):
    def test_high_score_when_all_present(self):
        rep = run_evidence_probes(
            MESSAGES, RENDERED,
            commitments=[("decision", "use sqlite for storage"), ("constraint", "keep offline mode")],
            identifiers=["/tmp/proj/src/main.py"],
        )
        self.assertGreater(rep["score"], 0.85)
        self.assertEqual(rep["failures"], [])
        self.assertEqual(rep["note"], "")

    def test_detects_missing_identifier(self):
        rep = run_evidence_probes(
            MESSAGES, "### FACTS\nnothing useful\n",
            commitments=[], identifiers=["/tmp/proj/src/main.py", "/tmp/other.py"],
        )
        self.assertIn("identifiers", rep["failures"])
        self.assertIn("/tmp/other.py", rep["contract"]["identifiers_missing"])

    def test_sections_scored_when_present(self):
        partial = "### FACTS\nfoo\n### COMMITMENTS\nbar\n"
        rep = run_evidence_probes(MESSAGES, partial, commitments=[], identifiers=[])
        self.assertLess(rep["checks"]["sections"], 1.0)

    def test_last_ask_coverage(self):
        unrelated = "### FACTS\nthe weather is nice\n"
        rep = run_evidence_probes(MESSAGES, unrelated, commitments=[], identifiers=[])
        self.assertLess(rep["checks"]["last_ask"], 0.8)

    def test_note_mentions_actions(self):
        rep = run_evidence_probes(MESSAGES, "empty", [], ["/tmp/x.py"])
        self.assertIn("<evidence_report>", rep["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
