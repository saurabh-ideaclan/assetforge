"""Optional realistic instruments: General MIDI SoundFonts played by the `fluidsynth` program.

The synth instruments always work. When `fluidsynth` is installed (`brew install fluid-synth`) and a
SoundFont has been installed into the library (`assetforge audio soundfont install --yes`), music
requests with backend `auto` (the default) or `soundfont` play each track through the SoundFont:
real piano, strings, brass, guitars, choirs and drum kits.

Rendering is offline and deterministic: one temporary MIDI file per track, fluidsynth's own reverb
and chorus off (AssetForge's effect chain adds the space), gain fixed. The SoundFont's SHA-256 is part
of each request's spec, so a different SoundFont shows up as drift in `audio reproduce`.
A track whose instrument has no General MIDI equivalent, or a failed render, falls back to the synth.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from assetforge.errors import InvalidInputError, StorageError

if TYPE_CHECKING:
    from assetforge.audio.music.score import Score


KNOWN: dict[str, dict[str, str]] = {
    "generaluser": {
        "title": "GeneralUser GS 2.0 by S. Christian Collins",
        "url": "https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2",
        "license": "GeneralUser GS License v2.0: free for any use, including commercial music and games",
        "file": "GeneralUser-GS.sf2",
    },
    "fluidr3": {
        "title": "FluidR3_GM by Frank Wen",
        "url": "https://raw.githubusercontent.com/musescore/MuseScore/2.1/share/sound/FluidR3Mono_GM.sf3",
        "license": "MIT",
        "file": "FluidR3Mono_GM.sf3",
    },
}
REGISTRY = "soundfonts.json"


def _home(home: Path | None = None) -> Path:
    from assetforge.audio.pipeline import active_home
    from assetforge.config import resolve_home

    return home or active_home.get() or resolve_home()


def folder(home: Path | None = None) -> Path:
    return _home(home) / "soundfonts"


def fluidsynth_path() -> str | None:
    return shutil.which("fluidsynth")


def installed(home: Path | None = None) -> dict[str, dict[str, Any]]:
    path = folder(home) / REGISTRY
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.get("soundfonts", {}).items() if Path(v.get("path", "")).is_file()}


def default(home: Path | None = None) -> dict[str, Any] | None:
    fonts = installed(home)
    path = folder(home) / REGISTRY
    chosen = json.loads(path.read_text()).get("default") if path.is_file() else None
    return fonts.get(chosen) or next(iter(fonts.values()), None)


def status(home: Path | None = None) -> dict[str, Any]:
    exe = fluidsynth_path()
    font = default(home)
    missing = []
    if not exe:
        missing.append("fluidsynth program (macOS: brew install fluid-synth; Linux: apt install fluidsynth)")
    if not font:
        missing.append("a SoundFont (assetforge audio soundfont install --yes)")
    return {"ready": bool(exe and font), "fluidsynth": exe, "soundfont": font, "installed": installed(home),
            "missing": missing, "known": KNOWN}  # fmt: skip


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_registry(home: Path, fonts: dict[str, Any], chosen: str | None) -> None:
    target = folder(home) / REGISTRY
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"default": chosen, "soundfonts": fonts}, indent=2))
    tmp.replace(target)


def set_default(home: Path, name: str) -> None:
    fonts = installed(home)
    if name not in fonts:
        raise InvalidInputError(f"No installed SoundFont {name!r}", available=sorted(fonts))
    _write_registry(home, fonts, name)


def register(home: Path, name: str, source: Path, *, license_: str, title: str = "", origin: str = "") -> dict[str, Any]:
    """Copy a .sf2/.sf3 into the library and make it the default."""
    if source.suffix.lower() not in (".sf2", ".sf3"):
        raise InvalidInputError("A SoundFont file ends in .sf2 or .sf3")
    dest = folder(home) / source.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != dest.resolve():
        shutil.copyfile(source, dest)
    entry = {"name": name, "title": title or source.stem, "path": str(dest), "sha256": _sha256(dest),
             "size_bytes": dest.stat().st_size, "license": license_, "source": origin or str(source)}  # fmt: skip
    fonts = installed(home) | {name: entry}
    _write_registry(home, fonts, name)
    return entry


def install(home: Path, name: str = "generaluser", *, url: str | None = None, license_: str | None = None) -> dict[str, Any]:
    """Download a known (or given) SoundFont into the library."""
    known = KNOWN.get(name, {})
    url = url or known.get("url")
    if not url:
        raise InvalidInputError(f"Unknown SoundFont {name!r}; pass --url", available=sorted(KNOWN))
    filename = known.get("file") or Path(url.split("?")[0]).name or f"{name}.sf2"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / filename
        try:
            with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:  # noqa: S310 (user-approved URL)
                shutil.copyfileobj(response, out)
        except OSError as exc:
            raise StorageError(f"Download failed: {exc}", url=url, hint="Check the network or pass --url") from exc
        if target.stat().st_size < 100_000:
            raise StorageError("The download is too small to be a SoundFont", url=url)
        return register(home, name, target, license_=license_ or known.get("license", "see source"),
                        title=known.get("title", ""), origin=url)  # fmt: skip


def spec_entry(home: Path | None = None) -> dict[str, str] | None:
    font = default(home)
    return {"name": font["name"], "sha256": font["sha256"]} if font else None


def _font_for(sha256: str | None, home: Path | None = None) -> dict[str, Any] | None:
    fonts = installed(home)
    if sha256:
        return next((f for f in fonts.values() if f["sha256"] == sha256), None)
    return default(home)


# ---------------------------------------------------------------------- rendering


def _track_midi(score: Score, index: int, path: Path) -> Path:
    from assetforge.audio.music.midi import write_midi
    from assetforge.audio.music.score import Score as ScoreModel

    track = score.tracks[index]
    single = ScoreModel(**(score.model_dump(mode="json") | {"tracks": [track.model_dump(mode="json")], "loop": False}))
    return write_midi(single, path)


def render_tracks(score: Score, sr: int, length: int, *, required: bool, sha256: str | None = None
                  ) -> tuple[dict[str, np.ndarray], dict[str, str]]:  # fmt: skip
    """Each track rendered by fluidsynth (mono, `length` samples). Missing pieces fall back to the synth."""
    import soundfile

    exe = fluidsynth_path()
    font = _font_for(sha256)
    if not exe or not font:
        if required:
            raise InvalidInputError("SoundFont rendering needs fluidsynth and an installed SoundFont",
                                    hint="See `assetforge audio soundfont status`")  # fmt: skip
        return {}, {}
    out: dict[str, np.ndarray] = {}
    used: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="assetforge-sf-") as tmp:
        for i, track in enumerate(score.tracks):
            if not track.notes:
                continue
            mid = _track_midi(score, i, Path(tmp) / f"t{i}.mid")
            wav = Path(tmp) / f"t{i}.wav"
            cmd = [exe, "-ni", "-q", "-g", "0.5", "-r", str(sr), "-o", "synth.reverb.active=0",
                   "-o", "synth.chorus.active=0", "-F", str(wav), font["path"], str(mid)]  # fmt: skip
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
            except (OSError, subprocess.TimeoutExpired):
                proc = None
            if proc is None or proc.returncode != 0 or not wav.is_file():
                if required:
                    raise InvalidInputError(f"fluidsynth failed on track {track.name}",
                                            stderr=(proc.stderr.decode(errors="replace")[-400:] if proc else ""))  # fmt: skip
                continue
            data, rate = soundfile.read(str(wav), dtype="float64", always_2d=True)
            mono = data.mean(axis=1)
            if rate != sr:
                mono = np.interp(np.arange(int(len(mono) * sr / rate)) * rate / sr, np.arange(len(mono)), mono)
            buffer = np.zeros(length)
            buffer[: min(length, len(mono))] = mono[:length]
            out[track.name] = buffer * 2.0  # fluidsynth renders quietly at -g 0.5
            used[track.name] = f"soundfont:{font['name']}"
    return out, used
