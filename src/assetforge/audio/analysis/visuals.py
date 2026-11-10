"""Visual diagnostics: the pictures and timelines a reviewer reads next to the findings.

PNGs (Pillow, no plotting library): waveform with clip and click marks, log-frequency spectrogram, mel
spectrogram, chromagram, self-similarity matrix. JSON timelines for the app to draw: loudness (momentary,
short-term), spectral centroid and flux, salient events, octave balance, stereo correlation per second,
speech-likeness per second, event labels per second. A spectrogram shows where the energy is; it cannot show
whether a melody moves anyone, which is why the findings sit beside it rather than inside it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from assetforge.audio.codecs import read_audio

# a small perceptual colormap (dark → bright), interpolated
_STOPS = np.array([[12, 10, 36], [48, 24, 96], [86, 44, 140], [140, 60, 150], [196, 90, 120], [238, 140, 80], [250, 200, 60], [252, 245, 160]], dtype=float)


def colormap(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0, 1)
    pos = v * (len(_STOPS) - 1)
    i = np.clip(pos.astype(int), 0, len(_STOPS) - 2)
    frac = (pos - i)[..., None]
    return (_STOPS[i] * (1 - frac) + _STOPS[i + 1] * frac).astype(np.uint8)


def _matrix_png(mat: np.ndarray, path: Path, *, width: int = 900, height: int = 260, lo: float | None = None, hi: float | None = None,
                flip: bool = True) -> Path:  # fmt: skip
    """mat: time × bands (or any 2-D); drawn with time along x. Values are scaled between lo and hi (dB)."""
    m = np.asarray(mat, dtype=float)
    if m.size == 0:
        Image.new("RGB", (width, height), (12, 10, 36)).save(path)
        return path
    lo = np.percentile(m, 2) if lo is None else lo
    hi = np.percentile(m, 99.5) if hi is None else hi
    norm = (m - lo) / max(hi - lo, 1e-6)
    img = Image.fromarray(colormap(norm.T if flip else norm), "RGB")
    if flip:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    img = img.resize((width, height), Image.BILINEAR)
    img.save(path)
    return path


def waveform_png(x: np.ndarray, sr: int, path: Path, *, width: int = 900, height: int = 160, clicks: list[float] | None = None) -> Path:
    mono = x.mean(axis=1) if x.ndim == 2 else x
    img = Image.new("RGB", (width, height), (16, 16, 24))
    d = ImageDraw.Draw(img)
    n = len(mono)
    mid = height / 2
    step = max(1, n // width)
    for col in range(width):
        seg = mono[col * step:(col + 1) * step]
        if seg.size == 0:
            break
        lo, hi = float(seg.min()), float(seg.max())
        colour = (120, 170, 255) if max(abs(lo), abs(hi)) < 0.999 else (255, 80, 80)
        d.line([(col, mid - hi * (mid - 2)), (col, mid - lo * (mid - 2))], fill=colour)
    d.line([(0, mid), (width, mid)], fill=(60, 60, 80))
    for c in clicks or []:
        xpix = int(c * sr / step)
        d.line([(xpix, 0), (xpix, height)], fill=(255, 200, 60))
    img.save(path)
    return path


def spectrogram_png(mag: np.ndarray, freqs: np.ndarray, path: Path, *, fmin: float = 30.0, fmax: float = 16000.0, bins: int = 256) -> Path:
    """Log-frequency spectrogram (dB) by binning the STFT into `bins` log-spaced bands."""
    edges = np.geomspace(fmin, min(fmax, freqs[-1]), bins + 1)
    power = mag**2
    out = np.zeros((mag.shape[0], bins))
    for i in range(bins):
        sel = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if sel.any():
            out[:, i] = power[:, sel].mean(axis=1)
        elif i:
            out[:, i] = out[:, i - 1]
    return _matrix_png(10 * np.log10(out + 1e-12), path, height=300)


def mel_png(mel_db: np.ndarray, path: Path) -> Path:
    return _matrix_png(mel_db, path, height=220)


def chroma_png(ch: np.ndarray, path: Path) -> Path:
    return _matrix_png(ch, path, height=120, lo=0.0, hi=float(np.percentile(ch, 99)) if ch.size else 1.0)


def similarity_png(sim: np.ndarray, path: Path, *, size: int = 360) -> Path:
    if sim.size == 0:
        Image.new("RGB", (size, size), (12, 10, 36)).save(path)
        return path
    return _matrix_png(sim, path, width=size, height=size, lo=-0.2, hi=1.0, flip=False)


def timelines(report: dict[str, Any]) -> dict[str, Any]:
    """Everything the app draws as lines, bars or strips."""
    t = report["technical"]
    sp = report.get("spectral") or {}
    m = report.get("music") or {}
    speech = report.get("speech") or {}
    ev = report.get("events") or {}
    out: dict[str, Any] = {"duration_s": t["duration_s"],
                           "loudness": {"step_s": t["loudness"]["step_s"], "momentary": t["loudness"]["momentary"],
                                        "short_term": t["loudness"]["short_term"], "integrated": t["loudness"]["integrated"], "lra": t["loudness"]["lra"]},
                           "stereo": t["stereo"], "silence": t["silence"], "clicks_s": t["discontinuities"]["positions_s"]}  # fmt: skip
    if sp and not sp.get("silent"):
        out["spectral"] = {"frame_rate": sp["frame_rate"], **sp["timeline"]}
        out["balance"] = {"octave_centres_hz": sp["bands"]["octave_centres_hz"], "octaves_db": sp["bands"]["octaves_db"],
                          "tilt_db_per_octave": sp["bands"]["tilt_db_per_octave"], "shares": sp["bands"]["shares"]}  # fmt: skip
    if m:
        out["music"] = {"events_per_s": m["events_per_s"], "tempo": m["tempo"], "key": m["key"], "structure": m["structure"],
                        "energy_arc": m["energy_arc"], "density": m["density"]}  # fmt: skip
    if speech:
        out["speech"] = [{"start_s": s["start_s"], "score": s["score"], "voice_like": s["voice_like"]} for s in speech.get("segments", [])]
    if ev:
        out["events"] = [{"start_s": w["start_s"], "labels": [(l["label"], l["confidence"]) for l in w["labels"]]} for w in ev.get("timeline", [])]
    return out


def render(report: dict[str, Any], out_dir: Path, *, path: Path | None = None) -> dict[str, str]:
    """Write every PNG and the timelines JSON into `out_dir`; returns name → file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    src = path or (Path(report["path"]) if report.get("path") else None)
    if src is not None:
        x, sr = read_audio(src, always_2d=False)
        files["waveform"] = str(waveform_png(np.asarray(x, dtype=np.float64), sr, out_dir / "waveform.png",
                                             clicks=report["technical"]["discontinuities"]["positions_s"]))
    arrays = report.get("arrays") or {}
    if "mag" in arrays:
        files["spectrogram"] = str(spectrogram_png(arrays["mag"], arrays["freqs"], out_dir / "spectrogram.png"))
        files["mel"] = str(mel_png(arrays["mel_db"], out_dir / "mel.png"))
    if "chroma" in arrays:
        files["chroma"] = str(chroma_png(arrays["chroma"], out_dir / "chroma.png"))
    if "similarity" in arrays:
        files["similarity"] = str(similarity_png(np.asarray(arrays["similarity"]), out_dir / "similarity.png"))
    (out_dir / "timelines.json").write_text(json.dumps(timelines(report)))
    files["timelines"] = str(out_dir / "timelines.json")
    return files
