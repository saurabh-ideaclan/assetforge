"""Content-addressed object store: objects/sha256/<ab>/<sha256><ext>.

Writes go to a temp file in the library and are moved into place atomically, so a
crashed import never leaves a partial object under its final name. Stored objects
are made read-only: originals are never modified, derivatives become new objects.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from assetforge.config import LibraryPaths
from assetforge.errors import StorageError

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    path: Path
    size_bytes: int
    created: bool


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                digest.update(chunk)
    except OSError as exc:
        raise StorageError(f"Cannot read {path}: {exc}", path=str(path)) from exc
    return digest.hexdigest()


class ObjectStore:
    def __init__(self, paths: LibraryPaths) -> None:
        self.paths = paths

    def object_path(self, sha256: str, ext: str) -> Path:
        return self.paths.objects / sha256[:2] / f"{sha256}{ext.lower()}"

    def rel_path(self, path: Path) -> str:
        return path.relative_to(self.paths.home).as_posix()

    def abs_path(self, rel_path: str) -> Path:
        return self.paths.home / rel_path

    def put_file(self, src: Path, ext: str | None = None, *, expected_sha256: str | None = None) -> StoredObject:
        """Copy `src` into the store. A no-op when the same content is already stored."""
        ext = (ext if ext is not None else src.suffix).lower()
        if expected_sha256:
            existing = self.object_path(expected_sha256, ext)
            if existing.is_file():
                return StoredObject(expected_sha256, existing, existing.stat().st_size, created=False)
        tmp = self._tmp_file(ext)
        digest = hashlib.sha256()
        size = 0
        try:
            with src.open("rb") as source, tmp.open("wb") as out:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            sha256 = digest.hexdigest()
            if expected_sha256 and sha256 != expected_sha256:
                raise StorageError(f"{src} changed while it was being imported", path=str(src))
            return self._finalize(tmp, sha256, ext, size)
        except OSError as exc:
            raise StorageError(f"Cannot store {src}: {exc}", path=str(src)) from exc
        finally:
            tmp.unlink(missing_ok=True)

    def put_bytes(self, data: bytes, ext: str) -> StoredObject:
        """Store generated content, e.g. a synthesized sound or a provider's image bytes."""
        ext = ext.lower()
        tmp = self._tmp_file(ext)
        try:
            with tmp.open("wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            return self._finalize(tmp, hashlib.sha256(data).hexdigest(), ext, len(data))
        except OSError as exc:
            raise StorageError(f"Cannot store generated object: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)

    def _tmp_file(self, ext: str) -> Path:
        self.paths.tmp.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.paths.tmp, prefix="put-", suffix=ext)
        os.close(fd)
        return Path(name)

    def _finalize(self, tmp: Path, sha256: str, ext: str, size: int) -> StoredObject:
        target = self.object_path(sha256, ext)
        if target.is_file():
            return StoredObject(sha256, target, size, created=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(tmp, 0o444)
        os.replace(tmp, target)
        return StoredObject(sha256, target, size, created=True)

    def iter_objects(self) -> list[Path]:
        if not self.paths.objects.is_dir():
            return []
        return sorted(p for p in self.paths.objects.glob("*/*") if p.is_file())
