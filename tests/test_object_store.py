"""S3/MinIO tiered storage tests with an injected fake client."""

import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folding import FoldArchive  # noqa: E402
from object_store import ObjectStore  # noqa: E402


class FakeClient:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


class TestObjectStore(unittest.TestCase):
    def test_put_get_roundtrip(self):
        client = FakeClient()
        store = ObjectStore(bucket="nf", client=client)
        self.assertTrue(store.enabled)
        self.assertTrue(store.put("a/b", b"hello"))
        self.assertEqual(store.get("a/b"), b"hello")

    def test_fold_archive_tiering(self):
        client = FakeClient()
        store = ObjectStore(bucket="nf", client=client)
        archive = FoldArchive(Path(tempfile.mkdtemp()) / "folds.db", encrypt=True, object_store=store)
        msgs = [{"role": "user", "content": "tiered secret"}]
        ref = archive.put("s1", "compaction", msgs)
        payload = archive._conn.execute("SELECT payload FROM folds WHERE ref=?", (ref,)).fetchone()[0]
        self.assertTrue(payload.startswith("s3:v1:"))
        self.assertTrue(client.objects)
        self.assertEqual(archive.get(ref)["messages"], msgs)
        archive.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
