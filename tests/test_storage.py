from __future__ import annotations

import hashlib
import os
import stat

from assetforge.config import LibraryPaths
from assetforge.core.storage import ObjectStore, hash_file

from conftest import make_png


def test_put_file_is_content_addressed_idempotent_and_read_only(tmp_path):
    paths = LibraryPaths(tmp_path / "lib")
    paths.ensure()
    store = ObjectStore(paths)
    src = make_png(tmp_path / "a.png")

    first = store.put_file(src)
    second = store.put_file(src)

    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    assert first.sha256 == second.sha256 == sha == hash_file(src)
    assert first.created and not second.created
    assert first.path == paths.objects / sha[:2] / f"{sha}.png"
    assert first.path.read_bytes() == src.read_bytes()
    assert not os.stat(first.path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert list(paths.tmp.iterdir()) == []


def test_put_bytes_matches_put_file(tmp_path):
    paths = LibraryPaths(tmp_path / "lib")
    paths.ensure()
    store = ObjectStore(paths)
    src = make_png(tmp_path / "a.png")

    from_bytes = store.put_bytes(src.read_bytes(), ".png")
    from_file = store.put_file(src)

    assert from_bytes.sha256 == from_file.sha256
    assert from_bytes.created and not from_file.created
