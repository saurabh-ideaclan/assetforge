"""Audio features for discovery: loudness, brightness, noisiness, busyness, tempo, key, loopability.

Computed once when a file is cataloged (`core/metadata.py`) and stored in `asset_files.audio`.
They feed search words ("loop", "dark", "calm", "minor"), the `--bpm/--key/--loopable` filters
and `similar` for sounds. NumPy/SciPy only. Tempo and key are estimates and are only reported
when the signal supports them (long enough, rhythmic or tonal enough). Generated music stores
its exact tempo and key in `metadata.audio`, which search prefers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

MAX_SECONDS = 120
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler key profiles
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    if len(x) < size:
        x = np.pad(x, (0, size - len(x)))
    count = 1 + (len(x) - size) // hop
    index = np.arange(size)[None, :] + hop * np.arange(count)[:, None]
    return x[index] * np.hanning(size)[None, :]


def _tempo(flux: np.ndarray, frame_rate: float) -> tuple[float | None, float]:
    """Beats per minute from the autocorrelation of the onset envelope, with a gentle 120-bpm prior."""
    env = flux - flux.mean()
    if len(env) < frame_rate * 4 or not env.any():
        return None, 0.0
    spectrum = np.fft.rfft(env, 2 * len(env))
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[: len(env)]
    if ac[0] <= 0:
        return None, 0.0
    ac = ac / ac[0]
    lo, hi = int(frame_rate * 60 / 200), int(frame_rate * 60 / 55)
    if hi >= len(ac) - 1:
        return None, 0.0
    lags = np.arange(lo, hi + 1)
    bpms = 60 * frame_rate / lags
    weight = np.exp(-0.5 * (np.log2(bpms / 120) / 1.0) ** 2)
    best = int(lags[np.argmax(ac[lags] * weight)])
    confidence = float(ac[best])
    # parabolic interpolation for sub-frame accuracy
    a, b, c = ac[best - 1], ac[best], ac[best + 1]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    bpm = 60 * frame_rate / (best + float(np.clip(shift, -0.5, 0.5)))
    return round(bpm, 1), round(confidence, 3)


def _key(power: np.ndarray, freqs: np.ndarray) -> tuple[str | None, float]:
    band = (freqs >= 55) & (freqs <= 4200)
    if not band.any():
        return None, 0.0
    pcs = np.round(12 * np.log2(freqs[band] / 440.0) + 69).astype(int) % 12
    chroma = np.bincount(pcs, weights=power[:, band].sum(axis=0), minlength=12)
    if chroma.sum() <= 0:
        return None, 0.0
    best, best_r = None, -1.0
    for tonic in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
            if r > best_r:
                best, best_r = f"{NOTE_NAMES[tonic]} {mode}", r
    return best, round(best_r, 3)


def analyze(samples: np.ndarray, sr: int) -> dict[str, Any]:
    """Features of float audio (n,) or (n, channels) in -1..1."""
    from assetforge.audio.loudness import integrated_loudness
    from assetforge.validation.audio import loop_seam, seam_ok

    data = np.asarray(samples, dtype=np.float64)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    data = data[: MAX_SECONDS * sr]
    mono = data.mean(axis=1) if data.ndim == 2 else data
    duration = len(mono) / sr
    size, hop = 2048, 512
    frames = _frames(mono, size, hop)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    power = mag**2
    freqs = np.fft.rfftfreq(size, 1 / sr)
    energy = power.sum(axis=1)
    loud = energy > energy.max() * 1e-4 if energy.max() > 0 else energy > 0
    out: dict[str, Any] = {"duration_s": round(duration, 3), "channels": channels,
                           "lufs": integrated_loudness(data, sr)}  # fmt: skip
    if not loud.any():
        return out | {"silent": True}
    p = power[loud]
    centroid = float(np.average((p * freqs).sum(axis=1) / p.sum(axis=1), weights=p.sum(axis=1)))
    cumulative = np.cumsum(p, axis=1)
    rolloff = float(np.median(freqs[np.argmax(cumulative >= 0.85 * cumulative[:, -1:], axis=1)]))
    # Flatness within the band that carries the sound, so dull (low-passed) noise still reads as noise.
    band = p[:, (freqs >= 80) & (freqs <= max(rolloff * 1.2, 500))]
    flatness = float(np.median(np.exp(np.mean(np.log(band + 1e-12), axis=1)) / (np.mean(band, axis=1) + 1e-12)))
    log_mag = np.log1p(mag * 10)
    flux = np.concatenate(([0.0], np.maximum(np.diff(log_mag, axis=0), 0).sum(axis=1)))
    frame_rate = sr / hop
    threshold = flux.mean() + flux.std()
    peaks = (flux[1:-1] > threshold) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:])
    onset_rate = float(peaks.sum() / max(duration, 1e-3))
    out |= {"centroid_hz": round(centroid), "rolloff_hz": round(rolloff), "flatness": round(flatness, 4),
            "onset_rate": round(onset_rate, 2)}  # fmt: skip
    if duration >= 6:
        bpm, confidence = _tempo(flux, frame_rate)
        if bpm and confidence >= 0.15 and onset_rate >= 0.5:
            out |= {"bpm": bpm, "bpm_confidence": confidence}
    if duration >= 3 and flatness < 0.2:
        key, r = _key(power[loud], freqs)
        if key and r >= 0.6:
            out |= {"key": key, "key_confidence": r}
    if duration >= 2:
        seam = loop_seam(data, sr)
        out["loop_seam_ok"] = seam_ok(seam)
    return out


def from_file(path: Path) -> dict[str, Any] | None:
    """Features of an audio file, or None when it cannot be decoded (QC reports that separately)."""
    try:
        from assetforge.audio.codecs import read_audio

        data, sr = read_audio(path)
        data = data[: MAX_SECONDS * sr]
        if len(data) == 0 or not np.isfinite(data).all() or np.max(np.abs(data)) > 4.0:
            return None  # empty, or not really audio (decoded garbage)
        return analyze(data if data.shape[1] > 1 else data[:, 0], sr)
    except Exception:  # features are a search aid; never fail cataloging over them
        return None


# ---------------------------------------------------------------------- search words


def tech_terms(features_json: str | None, *, loop: bool = False) -> list[str]:
    """Searchable words for a sound: stereo/mono, short/long, loop, bright/dark, noisy/tonal, calm/busy, key mode."""
    f = json.loads(features_json) if features_json else {}
    words: list[str] = []
    if not f:
        return ["loop"] if loop else []
    words.append("stereo" if f.get("channels", 1) > 1 else "mono")
    duration = f.get("duration_s", 0)
    words.append("short" if duration < 1.5 else "long" if duration >= 20 else "")
    if loop:
        words.append("loop")
    centroid = f.get("centroid_hz")
    if centroid is not None:
        words.append("bright" if centroid > 3500 else "dark" if centroid < 1200 else "")
    flatness = f.get("flatness")
    if flatness is not None:
        words.append("noisy" if flatness > 0.35 else "tonal" if flatness < 0.05 else "")
    rate = f.get("onset_rate")
    if rate is not None and duration >= 3:
        words.append("busy" if rate > 5 else "calm" if rate < 1 else "")
    if key := f.get("key"):
        words.append(key.split()[-1])
    return [w for w in words if w]


# ---------------------------------------------------------------------- similarity


def _vector(f: dict[str, Any], meta: dict[str, Any]) -> np.ndarray:
    duration = max(f.get("duration_s", 0.0), 0.01)
    bpm = meta.get("bpm") or f.get("bpm") or 0.0
    return np.array([
        math.log2(duration) / 3,
        math.log2(max(f.get("centroid_hz", 1000), 50)) / 2,
        math.log2(max(f.get("rolloff_hz", 2000), 50)) / 3,
        f.get("flatness", 0.1) * 4,
        math.log1p(f.get("onset_rate", 0.0)),
        (f.get("lufs", -20.0)) / 12,
        math.log2(bpm / 60) if bpm else 0.0,
    ])  # fmt: skip


def similar(lib: Library, ref: str, *, limit: int = 10, where: str = "1",
            params: list[Any] | None = None) -> list[tuple[str, float, float]]:  # fmt: skip
    """Sounds like `ref`: (asset_id, similarity 0-1, feature distance), best first."""
    from assetforge.errors import InvalidInputError

    asset_id = lib.resolve_asset_id(ref)
    columns = ("SELECT a.id, a.type, a.category, a.style, json_extract(a.metadata, '$.audio') AS meta, pf.audio "
               "FROM assets a JOIN asset_variants pv ON pv.id = a.primary_variant_id "
               "JOIN asset_files pf ON pf.sha256 = pv.file_sha256 ")  # fmt: skip
    base = lib.conn.execute(columns + "WHERE a.id = ?", (asset_id,)).fetchone()
    if base is None or not base["audio"]:
        raise InvalidInputError(f"{asset_id} has no audio features yet; run `assetforge reindex`", asset_id=asset_id)
    base_vec = _vector(json.loads(base["audio"]), json.loads(base["meta"] or "{}"))
    scored: list[tuple[str, float, float]] = []
    for row in lib.conn.execute(columns + f"WHERE pf.audio IS NOT NULL AND {where}", params or []):
        if row["id"] == asset_id:
            continue
        distance = float(np.linalg.norm(_vector(json.loads(row["audio"]), json.loads(row["meta"] or "{}")) - base_vec))
        score = math.exp(-distance) * 0.85 + 0.1 * (row["type"] == base["type"]) + 0.05 * bool(
            base["category"] and row["category"] == base["category"])  # fmt: skip
        scored.append((row["id"], round(score, 4), round(distance, 3)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit]


def ensure_features(lib: Library, sha256: str) -> bool:
    """Compute and store features for a cataloged audio file that predates them."""
    from assetforge.core import db
    from assetforge.core.util import dumps

    row = lib.conn.execute("SELECT rel_path, kind, audio FROM asset_files WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None or row["audio"] or row["kind"] != "audio":
        return False
    feats = from_file(lib.store.abs_path(row["rel_path"]))
    if feats is None:
        return False
    with db.transaction(lib.conn):
        lib.conn.execute("UPDATE asset_files SET audio = ? WHERE sha256 = ?", (dumps(feats), sha256))
    return True
