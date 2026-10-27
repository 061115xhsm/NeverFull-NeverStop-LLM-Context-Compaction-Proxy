"""Optional S3/MinIO tiered payload storage (V12 Phase 3).

When configured, FoldArchive stores compressed (zlib) payloads in object storage
and keeps only a small pointer in the database. Encryption happens before upload.
boto3 is optional; a client can be injected for tests.
"""

from __future__ import annotations

import os


class ObjectStore:
    def __init__(self, endpoint: str = "", bucket: str = "", access_key: str = "",
                 secret_key: str = "", prefix: str = "neverfull", client=None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client
        self.enabled = bool(bucket) and client is not None
        if self.enabled or not (endpoint and bucket):
            return
        try:
            import boto3  # type: ignore
            self.client = boto3.client(
                "s3", endpoint_url=endpoint,
                aws_access_key_id=access_key or None,
                aws_secret_access_key=secret_key or None,
                region_name="us-east-1",
            )
            self.enabled = True
        except Exception:
            self.enabled = False

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> bool:
        if not self.enabled:
            return False
        try:
            self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)
            return True
        except Exception:
            return False

    def get(self, key: str):
        if not self.enabled:
            return None
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return resp["Body"].read()
        except Exception:
            return None

    def delete(self, key: str) -> None:
        if not self.enabled:
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except Exception:
            pass


def from_env() -> ObjectStore:
    return ObjectStore(
        endpoint=os.environ.get("COMPACTION_PROXY_S3_ENDPOINT", ""),
        bucket=os.environ.get("COMPACTION_PROXY_S3_BUCKET", ""),
        access_key=os.environ.get("COMPACTION_PROXY_S3_ACCESS_KEY", ""),
        secret_key=os.environ.get("COMPACTION_PROXY_S3_SECRET_KEY", ""),
        prefix=os.environ.get("COMPACTION_PROXY_S3_PREFIX", "neverfull"),
    )
