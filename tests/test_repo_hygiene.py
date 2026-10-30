"""Guards that keep API keys out of git: .gitignore and scripts/check_secrets.sh (pre-commit hook and CI)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check_secrets.sh"
FAKE_OPENAI_KEY = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"  # built up so this file never matches itself
FAKE_GOOGLE_KEY = "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
FAKE_PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def check(cwd: Path, mode: str = "--staged") -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(CHECK), mode], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "main.py").write_text("print('hi')\n")
    git(root, "add", "main.py")
    return root


def test_env_is_ignored_in_this_repo() -> None:
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    for name in (".env", ".env.local", "prod.env"):
        assert git(REPO, "check-ignore", "-q", name).returncode == 0, name
    assert git(REPO, "check-ignore", "-q", ".env.example").returncode == 1


def test_clean_changes_pass(repo: Path) -> None:
    (repo / ".env.example").write_text("OPENAI_API_KEY=\n")
    git(repo, "add", ".env.example")
    result = check(repo)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", [".env", "config/.env.production", "local.env"])
def test_staged_env_file_is_refused(repo: Path, name: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("OPENAI_API_KEY=\n")
    git(repo, "add", name)
    result = check(repo)
    assert result.returncode == 1
    assert name in result.stderr


@pytest.mark.parametrize("secret", [FAKE_OPENAI_KEY, FAKE_GOOGLE_KEY, FAKE_PRIVATE_KEY])
def test_key_in_any_file_is_refused(repo: Path, secret: str) -> None:
    (repo / "settings.py").write_text(f"KEY = '{secret}'\n")
    git(repo, "add", "settings.py")
    result = check(repo)
    assert result.returncode == 1
    assert "settings.py" in result.stderr
    assert secret not in result.stderr + result.stdout  # never echo the key itself


def test_key_inside_binary_is_refused(repo: Path) -> None:
    (repo / "catalog.sqlite3").write_bytes(b"\x00SQLite\x00" + FAKE_OPENAI_KEY.encode() + b"\x00\xff")
    git(repo, "add", "catalog.sqlite3")
    assert check(repo).returncode == 1


def test_all_mode_checks_tracked_files(repo: Path) -> None:
    assert check(repo, "--all").returncode == 0
    (repo / "notes.txt").write_text(FAKE_GOOGLE_KEY)
    git(repo, "add", "notes.txt")
    assert check(repo, "--all").returncode == 1


def test_this_repo_is_clean() -> None:
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(["bash", str(CHECK), "--all"], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
