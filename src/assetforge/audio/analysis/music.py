"""Musical analysis: rhythm, tonality, structure and development.

Tempo comes from the autocorrelation of the onset envelope with a confidence; without a confident periodicity
the BPM is reported as unknown, not guessed. Events are the salient-event detector from `audio.quality`
(SuperFlux with a median floor). Chroma folds the STFT into pitch classes (55 Hz–4.2 kHz); the key is the
best Krumhansl–Kessler correlation and tonal stability is how slowly the chroma moves. Structure comes from
a self-similarity matrix of log-mel frames (Foote's checkerboard novelty for section boundaries, the lag
profile for repetition). Energy development is the short-term loudness arc split into quarters. All of this
describes shape, not merit: a composer decides whether the repetition is a motif or a rut.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import uniform_filter1d

from assetforge.audio.features import MAJOR_PROFILE, MINOR_PROFILE, NOTE_NAMES, _tempo
from assetforge.audio.quality import events as salient_events


def chroma(mag: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Frames × 12 pitch-class energy (normalised per frame) from 110 Hz to 4.2 kHz (below 110 Hz the 21.5 Hz
    bins of a 2048-point STFT straddle semitones and smear a bass note across classes)."""
    band = (freqs >= 110) & (freqs <= 4200)
    pcs = np.round(12 * np.log2(freqs[band] / 440.0) + 69).astype(int) % 12
    power = mag[:, band] ** 2
    out = np.zeros((mag.shape[0], 12))
    for pc in range(12):
        out[:, pc] = power[:, pcs == pc].sum(axis=1)
    return out / (out.sum(axis=1, keepdims=True) + 1e-12)


def key_of(chroma_mean: np.ndarray) -> tuple[str | None, float]:
    best, best_r = None, -1.0
    for tonic in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = float(np.corrcoef(chroma_mean, np.roll(profile, tonic))[0, 1])
            if r > best_r:
                best, best_r = f"{NOTE_NAMES[tonic]} {mode}", r
    return best, round(best_r, 3)


def harmonic_changes(ch: np.ndarray, frame_rate: float, *, window_s: float = 1.0) -> dict[str, Any]:
    """How often the pitch-class content changes: chroma smoothed over `window_s`, the cosine distance between
    consecutive windows, and the count of changes above 0.2 per minute (a chord-change proxy)."""
    n = max(1, int(window_s * frame_rate))
    smooth = uniform_filter1d(ch, size=n, axis=0)[::n]
    if len(smooth) < 3:
        return {"changes_per_min": 0.0, "stability": 1.0}
    a, b = smooth[:-1], smooth[1:]
    cos = (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9)
    dist = 1 - cos
    minutes = len(ch) / frame_rate / 60
    # ordinary chord changes (C→Am, C→G) sit near 0.3 cosine distance between one-second chroma means
    return {"changes_per_min": round(float((dist > 0.2).sum() / max(minutes, 1e-3)), 1), "stability": round(float(1 - dist.mean()), 3)}


def self_similarity(mel_db: np.ndarray, frame_rate: float, *, beat_s: float = 0.5) -> dict[str, Any]:
    """Self-similarity over `beat_s` blocks of the log-mel spectrogram: the matrix (for the visual), Foote
    novelty peaks as section boundaries, and the repetition profile (mean similarity at each lag ≥ 2 s)."""
    n = max(1, int(beat_s * frame_rate))
    blocks = mel_db[: len(mel_db) // n * n].reshape(-1, n, mel_db.shape[1]).mean(axis=1)
    if len(blocks) < 4:
        return {"matrix": blocks @ blocks.T if len(blocks) else np.zeros((0, 0)), "boundaries_s": [], "repetition_index": 0.0, "best_lag_s": None, "block_s": beat_s}
    z = blocks - blocks.mean(axis=0)
    z /= np.linalg.norm(z, axis=1, keepdims=True) + 1e-9
    sim = z @ z.T
    k = min(8, len(blocks) // 4)
    kernel = np.outer(np.r_[-np.ones(k), np.ones(k)], np.r_[-np.ones(k), np.ones(k)])
    novelty = np.zeros(len(blocks))
    for i in range(k, len(blocks) - k):
        novelty[i] = float((sim[i - k:i + k, i - k:i + k] * kernel).sum())
    novelty = np.maximum(novelty, 0)
    thr = novelty.mean() + 1.5 * novelty.std() if novelty.any() else 1.0
    peaks = [i for i in range(1, len(novelty) - 1) if novelty[i] > thr and novelty[i] >= novelty[i - 1] and novelty[i] > novelty[i + 1]]
    lags = range(int(2 / beat_s), len(blocks) // 2 + 1)
    profile = [(lag, float(np.mean(np.diag(sim, lag)))) for lag in lags]
    best_lag, best = max(profile, key=lambda p: p[1]) if profile else (None, 0.0)
    return {"matrix": sim, "boundaries_s": [round(p * beat_s, 1) for p in peaks], "repetition_index": round(max(best, 0.0), 3),
            "best_lag_s": None if best_lag is None else round(best_lag * beat_s, 1), "block_s": beat_s,
            "sections": len(peaks) + 1}  # fmt: skip


def energy_arc(short_term: list[float]) -> dict[str, Any]:
    """The loudness arc in quarters (LU relative to the piece's mean) and its shape word."""
    v = np.array([x for x in short_term if x > -70])
    if len(v) < 4:
        return {"quarters_lu": [], "shape": "flat", "range_lu": 0.0}
    q = [float(np.mean(part)) for part in np.array_split(v, 4)]
    rel = [round(x - float(v.mean()), 1) for x in q]
    rng = float(max(q) - min(q))
    if rng < 1.5:
        shape = "flat"
    elif q[-1] - q[0] > 1.5 and q[-1] >= max(q) - 0.5:
        shape = "rise"
    elif q[0] - q[-1] > 1.5 and q[0] >= max(q) - 0.5:
        shape = "fall"
    elif max(q[1], q[2]) > q[0] + 1 and max(q[1], q[2]) > q[-1] + 1:
        shape = "rise_fall"
    else:
        shape = "varied"
    return {"quarters_lu": rel, "shape": shape, "range_lu": round(rng, 1)}


def density(mel_db: np.ndarray, frame_rate: float) -> dict[str, Any]:
    """Arrangement density: the share of mel bands within 30 dB of the frame's loudest band (how much of the
    spectrum is occupied), averaged, with its quarter-by-quarter trend."""
    top = mel_db.max(axis=1, keepdims=True)
    occ = (mel_db > top - 30).mean(axis=1)
    quarters = [round(float(part.mean()), 3) for part in np.array_split(occ, 4)] if len(occ) >= 4 else []
    return {"occupancy": round(float(occ.mean()), 3), "quarters": quarters}


def analyze(mono: np.ndarray, sr: int, spectral: dict[str, Any], loudness: dict[str, Any]) -> dict[str, Any]:
    """The musical report, built on the spectral analysis (its STFT and mel) and the loudness timeline."""
    mag, freqs, frame_rate = spectral["mag"], spectral["freqs"], spectral["frame_rate"]
    duration = len(mono) / sr
    rate, cv = salient_events(mono, sr)
    log_mag = np.log1p(mag * 1000)
    flux = np.concatenate(([0.0], np.maximum(np.diff(log_mag, axis=0), 0).sum(axis=1)))
    bpm, conf = _tempo(flux, frame_rate) if duration >= 6 else (None, 0.0)
    tempo = {"bpm": bpm if (bpm and conf >= 0.15 and rate >= 0.5) else None, "confidence": conf,
             "note": None if (bpm and conf >= 0.15 and rate >= 0.5) else "no confident periodicity: tempo is not a meaningful property of this sound"}  # fmt: skip
    ch = chroma(mag, freqs)
    energy = (mag**2).sum(axis=1)
    loud = energy > energy.max() * 1e-5 if energy.max() > 0 else energy > 0
    mean_ch = ch[loud].mean(axis=0) if loud.any() else ch.mean(axis=0)
    key, r = key_of(mean_ch)
    tonal = spectral.get("summary", {}).get("flatness", 1.0) < 0.2
    harm = harmonic_changes(ch[loud] if loud.any() else ch, frame_rate)
    structure = self_similarity(spectral["mel_db"], frame_rate)
    return {"events_per_s": rate, "event_regularity_cv": cv, "tempo": tempo,
            "key": {"name": key if (tonal and r >= 0.6) else None, "confidence": r, "tonal": tonal},
            "harmony": harm, "structure": {k: v for k, v in structure.items() if k != "matrix"},
            "energy_arc": energy_arc(loudness.get("short_term", [])), "density": density(spectral["mel_db"], frame_rate),
            "chroma": ch, "similarity_matrix": structure["matrix"]}  # fmt: skip
