"""An in-memory stand-in for the S3 API as boto3 exposes it (the calls `publish/r2.py` makes), shared by the
publisher and backend tests. It counts every write and delete, honours `IfNoneMatch="*"` atomically (or rejects
the parameter like an old botocore when `conditional=False`), paginates listings, and serves the reference
client's anonymous GETs with ETags. Never touches a network or a real SDK."""

from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Any

from assetforge.publish import r2
from assetforge.story import catalog_sign

CFG = r2.R2Config(bucket="creative-library-v2-stage-vn", account_id="acct0000abcd", access_key="AK", secret_key="SK")


class S3Error(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self, bucket: str = CFG.bucket, *, denied: bool = False, conditional: bool = True, page_size: int = 1000) -> None:
        self.bucket, self.denied, self.conditional, self.page_size = bucket, denied, conditional, page_size
        self.objects: dict[str, dict[str, Any]] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.lists: list[str] = []
        self.lock = threading.Lock()

    @property
    def writes(self) -> int:
        return len(self.puts) + len(self.deletes)

    def _check(self, bucket: str) -> None:
        if self.denied:
            raise S3Error("AccessDenied", 403)
        if bucket != self.bucket:
            raise S3Error("NoSuchBucket", 404)

    def head_bucket(self, Bucket: str) -> dict[str, Any]:  # noqa: N803 - boto3's names
        self._check(Bucket)
        return {}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self._check(Bucket)
        if Key not in self.objects:
            raise S3Error("404", 404)
        return {"ContentLength": len(self.objects[Key]["body"]), "Metadata": self.objects[Key]["meta"]}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self._check(Bucket)
        if Key not in self.objects:
            raise S3Error("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objects[Key]["body"])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str, CacheControl: str,  # noqa: N803
                   Metadata: dict[str, str], **extra: Any) -> None:  # fmt: skip
        self._check(Bucket)
        if extra and not self.conditional:
            raise TypeError(f"put_object() got an unexpected keyword argument {next(iter(extra))!r}")
        with self.lock:
            if extra.get("IfNoneMatch") == "*" and Key in self.objects:
                raise S3Error("PreconditionFailed", 412)
            self.objects[Key] = {"body": Body, "type": ContentType, "cache": CacheControl, "meta": Metadata}
            self.puts.append(Key)

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self._check(Bucket)
        self.objects.pop(Key, None)
        self.deletes.append(Key)

    def list_objects_v2(self, Bucket: str, Prefix: str = "", ContinuationToken: str | None = None) -> dict[str, Any]:  # noqa: N803
        self._check(Bucket)
        self.lists.append(Prefix)
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        out: dict[str, Any] = {"Contents": [{"Key": k, "Size": len(self.objects[k]["body"])} for k in page], "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + self.page_size)
        return out

    # ------------------------------------------------------------------ test helpers

    def seed(self, key: str, body: bytes) -> None:
        """An object put there by someone else (not counted as our write)."""
        self.objects[key] = {"body": body, "type": "application/octet-stream", "cache": "", "meta": {}}

    def catalog(self, home: Path, channel: str = "staging", prefix: str = "") -> dict[str, Any]:
        key = f"{prefix}/catalog/{channel}/current.json" if prefix else f"catalog/{channel}/current.json"
        return catalog_sign.verify(json.loads(self.objects[key]["body"]), catalog_sign.load_public(home))

    def fetcher(self, prefix: str = "", log: list[str] | None = None) -> Any:
        """The reference client's anonymous GET (`fetch(key, etag)` → (status, body, etag))."""

        def fetch(key: str, etag: str | None = None) -> tuple[int, bytes, str | None]:
            full = f"{prefix}/{key}" if prefix else key
            if log is not None:
                log.append(key)
            obj = self.objects.get(full)
            if obj is None:
                return 404, b"", None
            tag = '"' + hashlib.md5(obj["body"]).hexdigest() + '"'  # noqa: S324 - an ETag, as S3 makes them
            if etag == tag:
                return 304, b"", tag
            return 200, obj["body"], tag

        return fetch
