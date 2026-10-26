"""Tests for V11 ops/quality additions: session locking, retention, encryption,
calibration, task commitments."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="nf-opt-")
os.environ["COMPACTION_PROXY_SESSION_DB"] = str(Path(TMP) / "sessions.db")
os.environ["COMPACTION_PROXY_SEMANTIC_MEMORY"] = str(Path(TMP) / "sem.json")
os.environ["COMPACTION_PROXY_USER_PROFILE"] = str(Path(TMP) / "profile.md")
os.environ["COMPACTION_PROXY_FOLD_DB"] = str(Path(TMP) / "folds.db")
os.environ["COMPACTION_PROXY_STATE_DB"] = str(Path(TMP) / "state.db")
os.environ["COMPACTION_PROXY_CALIBRATION_DB"] = str(Path(TMP) / "calibration.json")
os.environ["COMPACTION_PROXY_LOG_LEVEL"] = "ERROR"

spec = importlib.util.spec_from_file_location("compaction_proxy_opt", ROOT / "compaction-proxy.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["compaction_proxy_opt"] = cp
spec.loader.exec_module(cp)

from calibration import TokenCalibrator  # noqa: E402
from folding import FoldArchive  # noqa: E402
from state_table import StateTable  # noqa: E402


class TestSessionStoreConcurrency(unittest.TestCase):
    def test_concurrent_saves(self):
        store = cp.SessionStore(str(Path(TMP) / "sessions_conc.db"))
        errors = []

        def worker(i):
            for j in range(20):
                try:
                    store.save_session(f"s{i}", f"summary {i}-{j}", {}, j)
                    store.get_prior_summary(f"s{i}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(store.session_count, 8)
        store.close()


class TestRetention(unittest.TestCase):
    def test_fold_prune_by_age_and_cap(self):
        archive = FoldArchive(Path(TMP) / "retention_folds.db")
        old = archive.put("s1", "compaction", [{"role": "user", "content": "old"}])
        archive.put("s1", "compaction", [{"role": "user", "content": "new1"}])
        archive.put("s1", "compaction", [{"role": "user", "content": "new2"}])
        with archive._lock:
            archive._conn.execute("UPDATE folds SET created_at=? WHERE ref=?", (time.time() - 40 * 86400, old))
            archive._conn.commit()
        self.assertEqual(archive.prune(max_age_days=30), 1)
        self.assertEqual(archive.prune(max_entries=1), 1)
        self.assertEqual(archive.stats()["entries"], 1)
        archive.close()

    def test_state_prune(self):
        table = StateTable(Path(TMP) / "retention_state.db")
        table.update("s1", [{"role": "assistant", "content": [
            {"type": "tool_use", "name": "edit", "input": {"path": "old.py"}}]}], now=time.time() - 40 * 86400)
        table.update("s1", [{"role": "assistant", "content": [
            {"type": "tool_use", "name": "edit", "input": {"path": "new.py"}}]}], now=time.time())
        self.assertEqual(table.prune(max_age_days=30), 1)
        paths = [f["path"] for f in table.get("s1")["files"]]
        self.assertIn("new.py", paths)
        self.assertNotIn("old.py", paths)
        table.close()

    def test_session_prune(self):
        store = cp.SessionStore(str(Path(TMP) / "retention_sessions.db"))
        store.save_session("old", "s", {}, 1)
        with store._lock:
            store._conn.execute("UPDATE sessions SET created_at=? WHERE session_id=?", (time.time() - 120 * 86400, "old"))
            store._conn.commit()
        store.save_session("new", "s", {}, 1)
        self.assertGreaterEqual(store.prune(max_age_days=90), 1)
        self.assertIsNone(store.get_session("old"))
        self.assertIsNotNone(store.get_session("new"))
        store.close()


@unittest.skipUnless(__import__("importlib").util.find_spec("cryptography"), "cryptography not installed")
class TestEncryption(unittest.TestCase):
    def test_roundtrip_and_ciphertext_at_rest(self):
        archive = FoldArchive(Path(TMP) / "enc.db", encrypt=True)
        msgs = [{"role": "user", "content": "secret /tmp/a.py"}]
        ref = archive.put("s1", "compaction", msgs)
        raw = archive._conn.execute("SELECT payload FROM folds WHERE ref=?", (ref,)).fetchone()[0]
        self.assertTrue(raw.startswith("enc:v1:"))
        self.assertEqual(archive.get(ref)["messages"], msgs)
        archive.close()


class TestCalibration(unittest.TestCase):
    def test_factor_and_apply(self):
        cal = TokenCalibrator(Path(TMP) / "cal.json")
        self.assertEqual(cal.factor("m"), 1.0)
        cal.observe("m", 1000, 1200)
        self.assertAlmostEqual(cal.factor("m"), 1.2, places=3)
        self.assertEqual(cal.apply("m", 1000), 1200)
        cal.observe("m", 1000, 1000)
        self.assertAlmostEqual(cal.factor("m"), 0.8 * 1.2 + 0.2 * 1.0, places=3)

    def test_persistence(self):
        path = Path(TMP) / "cal2.json"
        TokenCalibrator(path).observe("m", 100, 150)
        self.assertAlmostEqual(TokenCalibrator(path).factor("m"), 1.5, places=3)


class TestTaskCommitment(unittest.TestCase):
    def test_todo_extracted_as_task(self):
        msgs = [{"role": "user", "content": "TODO: migrate config.yaml to the new schema"}]
        cs = cp.extract_commitments(msgs)
        self.assertTrue(any(c[0] == "task" and "migrate config.yaml" in c[1] for c in cs))

    def test_state_table_records_task(self):
        table = StateTable(Path(TMP) / "task_state.db")
        table.update("s1", [{"role": "user", "content": "Pending: migrate config.yaml"}], now=time.time())
        kinds = {d["kind"] for d in table.get("s1")["decisions"]}
        self.assertIn("task", kinds)
        table.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
