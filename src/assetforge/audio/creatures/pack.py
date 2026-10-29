"""Creature source recordings: free CC0 packs installed into the library, cataloged clip by clip.

Synthesis alone does not give game-quality creature voices; studios layer and process recordings
(animal and human growls, grunts, breaths). `assetforge creature install --yes` downloads five CC0
packs from OpenGameArt (about 12 MB, checked against pinned SHA-256s) into `$ASSETFORGE_HOME/creatures/`;
the recordings never go into the repo. `add_folder` registers your own recordings the same way.

Every clip gets an entry in `creatures/index.json`: the file, an optional slice (long takes hold
several growls), its family (beast, alien, insect, slime, critter), the roles it can play in a layered
voice (body, growl, roar, grunt, attack, breath, hurt, death, voice) and measured features (length,
onset, brightness, low-frequency share). Packs are identified by the SHA-256 of their downloads, which
is stored in every creature spec so a take re-renders only from the same recordings.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from assetforge.errors import InvalidInputError, StorageError

OGA = "https://opengameart.org/sites/default/files/"
PACKS: dict[str, dict[str, Any]] = {
    "rubberduck-1": {
        "title": "80 CC0 creature SFX", "author": "rubberduck", "page": "https://opengameart.org/content/80-cc0-creature-sfx",
        "downloads": [{"url": OGA + "80-CC0-creature-SFX_0.zip", "file": "80-CC0-creature-SFX.zip",
                       "sha256": "5d4002eea42f278e76543bb36e8ddd2cb978588a13271984191d8228f5d26768"}],
    },
    "rubberduck-2": {
        "title": "80 CC0 creature SFX 2", "author": "rubberduck", "page": "https://opengameart.org/content/80-cc0-creture-sfx-2",
        "downloads": [{"url": OGA + "80-CC0-creature-sfx-2.zip", "file": "80-CC0-creature-sfx-2.zip",
                       "sha256": "6d251aeaaaa9c2fffed8fc2f64bab3104725ceabf9259a8441994e7a20cfe22f"}],
    },
    "ogrebane": {
        "title": "Monster Sound Effects Pack", "author": "Ogrebane", "page": "https://opengameart.org/content/monster-sound-effects-pack",
        "downloads": [{"url": OGA + "monster_sfx_pack.zip", "file": "monster_sfx_pack.zip",
                       "sha256": "f39c5ae356b1fc01284057ee48e35ec1a52fcc84bfa959b77d0a696adc8459e4"}],
    },
    "darsycho-snarls": {
        "title": "Monster snarls", "author": "Darsycho", "page": "https://opengameart.org/content/monster-snarls",
        "downloads": [{"url": OGA + f, "file": f, "sha256": h} for f, h in (
            ("monster-snarls.ogg", "7401193d0cc5eb193379cf326c8868d6823a284ebc32ffaa298c1a94d2e25994"),
            ("monster-snarls-2.ogg", "dbc7b5e621892b5dbc27ee25230b57daaddbc83daadfb20131988b7367f967e1"),
            ("monsters-snarls-3.ogg", "5917d2697b8688b2c59e592d448d473ae0eeaed83802541ebd19bc87f97fe8a7"),
            ("monster-snarl-attack.ogg", "34bf1ceef265725abf00ab3263bbbb492792e126bb1619addd4a5d3ad123347f"),
            ("monster-snarl-5.ogg", "aeaf9ab0f25cb8b035a3257913206afd8ba33d47876106291d38dfac7e5c07aa"))],
    },
    "pauliuw": {
        "title": "Monster or beast sounds", "author": "pauliuw", "page": "https://opengameart.org/content/monster-or-beast-sounds",
        "downloads": [{"url": OGA + "monsters_or_beasts.7z", "file": "monsters_or_beasts.7z",
                       "sha256": "b032a255a22e32e08b31c9e7adc560b899d24661da12af5320346e5aa4c059ec"}],
    },
}  # fmt: skip
LICENSE = "CC0 1.0 (public domain)"
INDEX = "index.json"
AUDIO = (".wav", ".ogg", ".flac", ".mp3", ".aif", ".aiff")
ROLES = ("body", "growl", "roar", "grunt", "attack", "breath", "hurt", "death", "voice")
FAMILIES = ("beast", "alien", "insect", "slime", "critter")
SKIP = re.compile(r"human|stomp|children|pixie|spirit|cute_0?7")  # people, footsteps, fairies: not creature voices
MAX_CLIP_S = 4.0


def folder(home: Path) -> Path:
    return home / "creatures"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_index(home: Path) -> dict[str, Any]:
    path = folder(home) / INDEX
    if not path.is_file():
        return {"packs": {}, "clips": []}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"packs": {}, "clips": []}


def _save_index(home: Path, index: dict[str, Any]) -> None:
    target = folder(home) / INDEX
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1))
    tmp.replace(target)


def installed_packs(home: Path) -> dict[str, str]:
    """Pack name → version (SHA-256 of its downloads) for packs whose files are all on disk."""
    index = load_index(home)
    root = folder(home)
    present = {c["pack"] for c in index["clips"] if (root / c["path"]).is_file()}
    return {name: info["sha256"] for name, info in index["packs"].items() if name in present}


def status(home: Path) -> dict[str, Any]:
    index = load_index(home)
    packs = installed_packs(home)
    clips = [c for c in index["clips"] if c["pack"] in packs]
    by_role = {r: sum(r in c["roles"] for c in clips) for r in ROLES}
    by_family = {f: sum(c["family"] == f for c in clips) for f in FAMILIES}
    return {"ready": bool(clips), "folder": str(folder(home)), "packs": {n: index["packs"][n] for n in packs},
            "clips": len(clips), "roles": by_role, "families": by_family,
            "missing": [] if clips else ["creature recordings (assetforge creature install --yes)"],
            "known": {n: {k: p[k] for k in ("title", "author", "page")} for n, p in PACKS.items()}}  # fmt: skip


# ---------------------------------------------------------------------- classification


def features(x: np.ndarray, sr: int) -> dict[str, float]:
    x = np.asarray(x, dtype=np.float64)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-20
    f = np.fft.rfftfreq(len(x), 1 / sr)
    k = max(1, int(0.01 * sr))
    env = np.sqrt(np.convolve(x**2, np.ones(k) / k, "same"))
    above = np.nonzero(env > env.max() * 0.1)[0]
    return {"duration": round(len(x) / sr, 3), "centroid": round(float((spec * f).sum() / spec.sum()), 1),
            "low_share": round(float(spec[f < 250].sum() / spec.sum()), 3),
            "onset": round(float(above[0] / sr) if len(above) else 0.0, 3),
            "peak": round(float(np.argmax(env) / sr), 3), "rms_db": round(float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)), 1)}  # fmt: skip


def classify(name: str, feats: dict[str, float]) -> tuple[str, list[str], list[str]] | None:
    """(family, roles, tags) for a clip from its file name and sound, or None to leave it out."""
    stem = Path(name).stem.lower().replace("-", " ").replace("_", " ")
    if SKIP.search(stem.replace(" ", "_")):
        return None
    tags = sorted(set(re.findall(r"[a-z]+", stem)) - {"wav", "ogg", "monster", "monsters", "sfx", "pack"})
    low, bright = feats["low_share"] >= 0.75, feats["centroid"] >= 1500
    for prefix, family in (("alien", "alien"), ("bug", "insect"), ("insect", "insect"), ("slime", "slime"),
                           ("burble", "slime"), ("cute", "critter"), ("critter", "critter")):  # fmt: skip
        if stem.startswith(prefix):
            role = "hurt" if "hurt" in stem else "death" if "die" in stem else "voice"
            return family, [role, "voice"] if role != "voice" else ["voice"], tags
    roles: list[str] = []
    if re.search(r"breath|snore|nose|inhale|exhale", stem):
        roles = ["breath"]
    elif re.search(r"\bdie|dieing|dying|death", stem):
        roles = ["death"]
    elif "hurt" in stem or "pain" in stem:
        roles = ["hurt"]
    elif re.search(r"attack|scream|bark", stem):
        roles = ["attack"]
    elif re.search(r"roar|howl|call", stem):
        roles = ["roar"]
    elif re.search(r"grunt|yucky", stem):
        roles = ["grunt"]
    elif re.search(r"growl|snarl|troll|monster|beast|demon", stem):
        roles = ["attack"] if bright else ["growl"]
    if low and set(roles) & {"growl", "roar", "grunt"} or (not roles and low):
        roles.append("body")  # deep enough to sit under other layers as weight
    return ("beast", roles, tags) if roles else None


def _segments(x: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """Separate vocalizations in a long take (gaps > 150 ms below -30 dB), long ones cut into ≤4 s pieces."""
    hop = int(0.02 * sr)
    env = 20 * np.log10(np.sqrt(np.convolve(x**2, np.ones(hop) / hop, "same")) + 1e-9)[::hop]
    on = np.append(env > env.max() - 30, False)
    segs: list[list[float]] = []
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > 10:
                if segs and start * 0.02 - segs[-1][1] < 0.15:
                    segs[-1][1] = i * 0.02
                else:
                    segs.append([start * 0.02, i * 0.02])
            start = None
    out = []
    for a, b in segs:
        pieces = max(1, int(np.ceil((b - a) / MAX_CLIP_S)))
        step = (b - a) / pieces
        out += [(round(a + k * step, 2), round(a + (k + 1) * step, 2)) for k in range(pieces)]
    return out


def read_clip(home: Path, clip: dict[str, Any], sr: int) -> np.ndarray:
    """A clip as mono float64 at `sr` (sliced, resampled)."""
    from fractions import Fraction

    import soundfile
    from scipy.signal import resample_poly

    x, file_sr = soundfile.read(str(folder(home) / clip["path"]), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    if clip.get("end") is not None:
        x = x[int(clip["start"] * file_sr) : int(clip["end"] * file_sr)]
    if file_sr != sr:
        ratio = Fraction(sr, file_sr).limit_denominator(1000)
        x = resample_poly(x, ratio.numerator, ratio.denominator)
    return x


def _catalog_files(home: Path, pack: str, root: Path) -> list[dict[str, Any]]:
    import soundfile

    clips = []
    for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO and not p.name.startswith(".")):
        try:
            x, sr = soundfile.read(str(path), dtype="float64", always_2d=True)
        except Exception:  # noqa: BLE001 - an unreadable file is simply not cataloged
            continue
        x = x.mean(axis=1)
        if not len(x) or np.max(np.abs(x)) < 1e-4:
            continue
        rel = str(path.relative_to(folder(home)))
        sha = _sha256(path)
        windows = _segments(x, sr) if len(x) / sr > MAX_CLIP_S else [(None, None)]
        for a, b in windows:
            piece = x if a is None else x[int(a * sr) : int(b * sr)]
            if len(piece) < 0.08 * sr:
                continue
            feats = features(piece, sr)
            found = classify(path.name, feats)
            if found is None:
                continue
            family, roles, tags = found
            cid = f"{pack}/{path.relative_to(root)}" + ("" if a is None else f"#{a:g}-{b:g}")
            clips.append({"id": cid, "pack": pack, "path": rel, "sha256": sha, "start": a, "end": b, "family": family,
                          "roles": roles, "tags": tags, **feats})  # fmt: skip
    return clips


def _register(home: Path, name: str, root: Path, info: dict[str, Any], version: str) -> dict[str, Any]:
    clips = _catalog_files(home, name, root)
    if not clips:
        raise InvalidInputError(f"No usable creature recordings found for {name!r}",
                                hint="Name files after what they are (growl, roar, grunt, breath, hurt, die…)")  # fmt: skip
    index = load_index(home)
    index["clips"] = [c for c in index["clips"] if c["pack"] != name] + clips
    index["packs"][name] = info | {"sha256": version, "clips": len(clips)}
    _save_index(home, index)
    return index["packs"][name]


def _extract(archive: Path, dest: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):  # no path traversal out of the pack folder
                    raise StorageError(f"Unsafe path in {archive.name}: {member.filename}")
            z.extractall(dest)
    elif archive.suffix == ".7z":
        tool = shutil.which("bsdtar") or shutil.which("7z") or shutil.which("7zz")
        if tool is None:
            raise StorageError(f"Extracting {archive.name} needs bsdtar or 7z", hint="macOS has bsdtar; on Linux install p7zip")
        cmd = [tool, "x", "-y", f"-o{dest}", str(archive)] if Path(tool).name.startswith("7z") else [tool, "-xf", str(archive), "-C", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
        if proc.returncode != 0:
            raise StorageError(f"Could not extract {archive.name}", stderr=proc.stderr.decode(errors="replace")[-300:])
    else:
        shutil.copyfile(archive, dest / archive.name)


def install(home: Path, names: list[str] | None = None, *, source_dir: Path | None = None) -> dict[str, Any]:
    """Download (or take from `source_dir`) the known packs, verify them, extract and catalog every clip."""
    results, skipped = {}, {}
    for name in names or list(PACKS):
        if name not in PACKS:
            raise InvalidInputError(f"Unknown creature pack {name!r}", available=sorted(PACKS))
        pack = PACKS[name]
        dest = folder(home) / name
        with tempfile.TemporaryDirectory(prefix="assetforge-creatures-") as tmp:
            try:
                for d in pack["downloads"]:
                    target = Path(tmp) / d["file"]
                    local = source_dir / d["file"] if source_dir else None
                    if local is not None and local.is_file():
                        shutil.copyfile(local, target)
                    else:
                        req = urllib.request.Request(d["url"], headers={"User-Agent": "AssetForge creature pack installer"})
                        with urllib.request.urlopen(req, timeout=120) as r, target.open("wb") as out:  # noqa: S310 (pinned URL)
                            shutil.copyfileobj(r, out)
                    if _sha256(target) != d["sha256"]:
                        raise StorageError(f"{d['file']} does not match its pinned SHA-256", url=d["url"])
                if dest.exists():
                    shutil.rmtree(dest)
                dest.mkdir(parents=True)
                for d in pack["downloads"]:
                    _extract(Path(tmp) / d["file"], dest)
            except (OSError, StorageError) as exc:
                skipped[name] = str(exc)
                continue
        version = hashlib.sha256("".join(d["sha256"] for d in pack["downloads"]).encode()).hexdigest()
        info = {k: pack[k] for k in ("title", "author", "page")} | {"license": LICENSE}
        results[name] = _register(home, name, dest, info, version)
    return {"installed": results, "skipped": skipped, "status": status(home)}


def add_folder(home: Path, name: str, source: Path, *, license_: str, title: str = "") -> dict[str, Any]:
    """Copy your own creature recordings (named after what they are: growl, roar, breath…) into the library."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or name in PACKS:
        raise InvalidInputError("A pack name is lowercase letters, digits and dashes, and not a built-in pack name")
    if not source.is_dir():
        raise InvalidInputError(f"Not a folder: {source}")
    dest = folder(home) / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=lambda d, files: [f for f in files if not (Path(d, f).is_dir() or f.lower().endswith(AUDIO))])
    digest = hashlib.sha256()
    for path in sorted(dest.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode() + _sha256(path).encode())
    return _register(home, name, dest, {"title": title or name, "author": "local", "page": str(source), "license": license_},
                     digest.hexdigest())  # fmt: skip
