from __future__ import annotations

import json
import math
import shutil
import struct
import wave
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from assetforge.cli import main
from assetforge.core.catalog import Library


def make_png(path: Path, size: tuple[int, int] = (16, 16), color: tuple[int, ...] = (200, 40, 40, 255)) -> Path:
    """A flat image with a small contrasting square, so it is distinct and not 'blank' to QC."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, color)
    w, h = size
    inverse = (255 - color[0], 255 - color[1], 255 - color[2], 255)
    image.paste(inverse, (w // 4, h // 4, max(w // 4 + 1, w // 2), max(h // 4 + 1, h // 2)))
    image.save(path)
    return path


def make_wav(path: Path, duration_ms: int = 200, freq: float = 440.0, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(rate * duration_ms / 1000)
    samples = (int(12000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(frames))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return path


@pytest.fixture(autouse=True)
def no_real_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never reach a paid API: run outside the repo (its .env holds real keys),
    drop key variables, and make constructing a real SDK client fail loudly."""
    from assetforge.config import SETTING_KEYS
    from assetforge.image import providers

    monkeypatch.chdir(tmp_path)
    for key in SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASSETFORGE_PRICING_OFFLINE", "1")  # never fetch prices from the internet in tests

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test tried to create a real provider client")

    import google.genai
    import openai

    monkeypatch.setattr(openai, "OpenAI", forbidden)
    monkeypatch.setattr(google.genai, "Client", forbidden)
    monkeypatch.setattr(providers, "OVERRIDES", {})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    library_home = tmp_path / "library"
    monkeypatch.setenv("ASSETFORGE_HOME", str(library_home))
    monkeypatch.delenv("ASSETFORGE_JSON", raising=False)
    return library_home


@pytest.fixture
def lib(home: Path):
    with Library(home) as library:
        yield library


PACK_UNIQUE = 220
PACK_DUPLICATES = 40


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    """A messy existing asset folder: 220 unique files, 40 byte-identical copies, junk to ignore."""
    root = tmp_path / "pack"
    for i in range(40):
        make_png(root / "UI" / "Buttons" / f"blue_button_{i:02d}.png", (32, 12), (20, 40, 200 - i, 255))
    for i in range(40):
        make_png(root / "Characters" / "Player" / f"panda_idle_{i:02d}.png", (24, 24), (i, 120, 60, 0 if i % 2 else 255))
    for i in range(20):
        make_png(root / "Backgrounds" / f"jungle_bg_{i:02d}.png", (64, 36), (10, 90 + i, 30, 255))
    for i in range(20):
        make_png(root / "Items" / f"bamboo_{i:02d}.png", (8, 8), (180, 200, i, 255))
    for i in range(50):
        make_wav(root / "Audio" / "SFX" / f"coin_pickup_{i:02d}.wav", 150, 600 + i * 7)
    for i in range(48):
        make_wav(root / "Audio" / "SFX" / f"jump_{i:02d}.wav", 120, 200 + i * 5)
    make_wav(root / "Audio" / "Music" / "jungle_theme.wav", 1000, 220)
    make_wav(root / "Audio" / "long_ambient_bed.wav", 21_000, 110, rate=2000)
    # byte-identical copies under other names
    sources = sorted((root / "UI" / "Buttons").glob("*.png"))[:20] + sorted((root / "Audio" / "SFX").glob("jump_*"))[:20]
    for src in sources:
        dest = root / "duplicates" / src.parent.name / f"copy_of_{src.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (root / "README.txt").write_text("credits")
    make_png(root / ".cache" / "hidden.png")
    (root / ".DS_Store").write_bytes(b"\0")
    return root


def run_cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, Any, str]:
    """Run the CLI in-process; returns (exit code, parsed stdout JSON or raw text, stderr)."""
    code = main(list(args))
    captured = capsys.readouterr()
    try:
        payload: Any = json.loads(captured.out)
    except json.JSONDecodeError:
        payload = captured.out
    return code, payload, captured.err
