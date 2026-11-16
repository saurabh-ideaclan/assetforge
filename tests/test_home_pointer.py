"""The machine's library pointer (~/.config/assetforge/home) and the unmounted-drive guard."""

from __future__ import annotations

from pathlib import Path

import pytest

import assetforge.config as cfg
from assetforge.core.catalog import Library
from assetforge.errors import StorageError


def test_pointer_is_used_when_no_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lib_dir = tmp_path / "Game Dev Asset"
    lib_dir.mkdir()
    pointer = tmp_path / "home"
    pointer.write_text(f"{lib_dir}\n")
    monkeypatch.setattr(cfg, "HOME_POINTER", str(pointer))
    monkeypatch.delenv("ASSETFORGE_HOME", raising=False)
    assert cfg.resolve_home() == lib_dir.resolve()
    with Library() as lib:
        assert lib.paths.home == lib_dir.resolve()
    assert (lib_dir / "catalog.sqlite3").exists()


def test_env_and_explicit_beat_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pointer = tmp_path / "home"
    pointer.write_text(str(tmp_path / "shared"))
    monkeypatch.setattr(cfg, "HOME_POINTER", str(pointer))
    monkeypatch.setenv("ASSETFORGE_HOME", str(tmp_path / "env"))
    assert cfg.resolve_home() == (tmp_path / "env").resolve()
    assert cfg.resolve_home(tmp_path / "x") == (tmp_path / "x").resolve()


def test_unmounted_drive_never_makes_a_new_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = tmp_path / "media" / "ETC" / "Game Dev Asset"
    pointer = tmp_path / "home"
    pointer.write_text(str(gone))
    monkeypatch.setattr(cfg, "HOME_POINTER", str(pointer))
    monkeypatch.delenv("ASSETFORGE_HOME", raising=False)
    with pytest.raises(StorageError, match="drive mounted"):
        Library()
    assert not gone.exists()


def test_dangling_link_is_refused_even_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = tmp_path / ".assetforge"
    target = tmp_path / "media" / "ETC" / "Game Dev Asset"
    link.symlink_to(target)
    monkeypatch.setenv("ASSETFORGE_HOME", str(link))
    with pytest.raises(StorageError, match="drive mounted"):
        Library()
    assert not target.exists()
    target.mkdir(parents=True)
    with Library() as lib:
        assert lib.paths.home == target.resolve()
