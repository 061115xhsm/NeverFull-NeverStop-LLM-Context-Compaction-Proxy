"""Concurrency tests for the SQLite-backed stores (V11 stability)."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folding import FoldArchive  # noqa: E402
from state_table import StateTable  # noqa: E402


class TestConcurrentWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nf-conc-")

    def test_fold_archive_concurrent_put(self):
        archive = FoldArchive(Path(self.tmp) / "folds.db")
        errors = []

        def worker(i):
            for j in range(25):
                try:
                    archive.put(f"s{i}", "compaction", [{"role": "user", "content": f"{i}-{j}"}])
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(archive.stats()["entries"], 200)
        archive.close()

    def test_state_table_concurrent_update(self):
        table = StateTable(Path(self.tmp) / "state.db")
        errors = []

        def worker(i):
            for j in range(25):
                try:
                    table.update(f"s{i}", [{"role": "user", "content": f"We decided to use sqlite {i}-{j}"}],
                                 now=time.time())
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(table.stats()["decisions"], 8)
        table.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
