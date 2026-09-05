import hashlib
from pathlib import Path

from .config import get_settings


class ObjectStore:
    """Small object-store port. The filesystem adapter keeps local/CI runs deterministic."""

    def __init__(self, root: Path | None = None):
        settings = get_settings()
        self.backend = settings.object_store_backend
        self.root = root or settings.object_store_path
        if self.backend == "s3":
            import boto3

            self.bucket = settings.s3_bucket
            self.client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name="us-east-1",
            )
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except Exception:
                self.client.create_bucket(Bucket=self.bucket)
        else:
            self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes) -> tuple[str, str]:
        safe = Path(key)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError("unsafe object key")
        if self.backend == "s3":
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        else:
            target = self.root / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return key, hashlib.sha256(content).hexdigest()

    def get(self, key: str) -> bytes:
        safe = Path(key)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError("unsafe object key")
        if self.backend == "s3":
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return (self.root / safe).read_bytes()
