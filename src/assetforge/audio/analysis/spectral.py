"""Spectral and timbral analysis: brightness, balance, texture and how the spectrum moves.

One STFT (2048-point Hann, hop 512 at 44.1 kHz: 46 ms windows, 11.6 ms steps) feeds everything: a mel
spectrogram (64 bands, 20 Hz–16 kHz), per-frame centroid / bandwidth / rolloff / flatness / flux, a
harmonic-vs-percussive split by median filtering (Fitzgerald 2010), octave-band energy with the spectral tilt
against a pink reference, and the named bands a mixer listens for: rumble (< 80 Hz), mud (200–500 Hz), the
dialogue band (1–4 kHz), harshness (2–5 kHz) and hiss (> 8 kHz). A feature describes the sound; it does not
name the instrument or the emotion. NumPy/SciPy only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import median_filter

N_FFT, HOP = 2048, 512
MEL_BANDS = 64
BANDS = {"rumble": (0, 80), "low": (80, 200), "mud": (200, 500), "low_mid": (500, 1000), "dialogue": (1000, 4000),
         "harsh": (2000, 5000), "presence": (4000, 8000), "hiss": (8000, 20000)}  # fmt: skip
OCTAVES = [(31.5, 63), (63, 125), (125, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000)]


def stft(mono: np.ndarray, sr: int, *, n_fft: int = N_FFT, hop: int = HOP) -> tuple[np.ndarray, np.ndarray, float]:
    """Magnitude spectrogram (frames × bins), bin frequencies and the frame rate."""
    if len(mono) < n_fft:
        mono = np.pad(mono, (0, n_fft - len(mono)))
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop] * np.hanning(n_fft)
    mag = np.abs(np.fft.rfft(frames, axis=1)) / n_fft
    return mag, np.fft.rfftfreq(n_fft, 1 / sr), sr / hop


def _hz_to_mel(f: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(f) / 700.0)


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (np.asarray(m) / 2595.0) - 1.0)


def mel_filterbank(freqs: np.ndarray, sr: int, bands: int = MEL_BANDS, fmin: float = 20.0, fmax: float = 16000.0) -> np.ndarray:
    fmax = min(fmax, sr / 2)
    edges = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), bands + 2))
    fb = np.zeros((bands, len(freqs)))
    for i in range(bands):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        up = (freqs - lo) / max(mid - lo, 1e-9)
        down = (hi - freqs) / max(hi - mid, 1e-9)
        fb[i] = np.clip(np.minimum(up, down), 0, 1)
    return fb


def mel_spectrogram(mag: np.ndarray, freqs: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Log-power mel spectrogram (frames × bands, dB) and the band centre frequencies."""
    fb = mel_filterbank(freqs, sr)
    power = (mag**2) @ fb.T
    centres = _mel_to_hz(np.linspace(_hz_to_mel(20.0), _hz_to_mel(min(16000.0, sr / 2)), MEL_BANDS + 2))[1:-1]
    return 10 * np.log10(power + 1e-12), centres


def hpss(mag: np.ndarray, *, kernel: int = 17) -> tuple[np.ndarray, np.ndarray]:
    """Harmonic / percussive masks by median filtering along time (harmonic) and frequency (percussive)."""
    harm = median_filter(mag, size=(kernel, 1), mode="reflect")
    perc = median_filter(mag, size=(1, kernel), mode="reflect")
    total = harm**2 + perc**2 + 1e-12
    return mag * (harm**2 / total), mag * (perc**2 / total)


def per_frame(mag: np.ndarray, freqs: np.ndarray) -> dict[str, np.ndarray]:
    power = mag**2
    total = power.sum(axis=1) + 1e-12
    centroid = (power * freqs).sum(axis=1) / total
    bandwidth = np.sqrt((power * (freqs[None, :] - centroid[:, None]) ** 2).sum(axis=1) / total)
    cum = np.cumsum(power, axis=1)
    rolloff = freqs[np.argmax(cum >= 0.85 * cum[:, -1:], axis=1)]
    band = (freqs >= 80) & (freqs <= 10000)
    p = power[:, band] + 1e-14
    flatness = np.exp(np.mean(np.log(p), axis=1)) / np.mean(p, axis=1)
    log_mag = np.log1p(mag * 1000)
    flux = np.concatenate(([0.0], np.maximum(np.diff(log_mag, axis=0), 0).sum(axis=1)))
    return {"centroid": centroid, "bandwidth": bandwidth, "rolloff": rolloff, "flatness": flatness, "flux": flux, "energy": total}


def band_energy(mag: np.ndarray, freqs: np.ndarray) -> dict[str, Any]:
    """Share of the energy in the named bands and the octave-band levels (dB re the loudest octave), plus the
    spectral tilt in dB/octave fitted on those *octave sums* from 63 Hz to 8 kHz: pink noise measures 0 here
    (equal energy per octave), white +3, a balanced mix about -1 to -3."""
    power = (mag**2).mean(axis=0)
    total = power.sum() + 1e-12
    shares = {name: round(float(power[(freqs >= lo) & (freqs < hi)].sum() / total), 4) for name, (lo, hi) in BANDS.items()}
    levels = []
    for lo, hi in OCTAVES:
        sel = (freqs >= lo) & (freqs < hi)
        levels.append(10 * np.log10(power[sel].sum() + 1e-14) if sel.any() else -140.0)
    levels = np.array(levels)
    ref = levels.max()
    rel = levels - ref
    centres = np.log2(np.array([np.sqrt(lo * hi) for lo, hi in OCTAVES]))
    use = (np.array([lo for lo, _ in OCTAVES]) >= 63) & (np.array([hi for _, hi in OCTAVES]) <= 8000) & (rel > -60)
    tilt = float(np.polyfit(centres[use], levels[use], 1)[0]) if use.sum() >= 3 else 0.0
    return {"shares": shares, "octaves_db": [round(float(v), 1) for v in rel],
            "octave_centres_hz": [round(float(np.sqrt(lo * hi))) for lo, hi in OCTAVES], "tilt_db_per_octave": round(tilt, 2)}  # fmt: skip


def transients(mag: np.ndarray, frame_rate: float) -> dict[str, Any]:
    """How much of the energy arrives as transients: the percussive share from HPSS, and the attack sharpness
    (median rise of the frame energy over the 3 frames before a flux peak, in dB)."""
    harm, perc = hpss(mag)
    ph, pp = (harm**2).sum(), (perc**2).sum()
    share = float(pp / (ph + pp + 1e-12))
    energy = 10 * np.log10((mag**2).sum(axis=1) + 1e-12)
    log_mag = np.log1p(mag * 1000)
    flux = np.maximum(np.diff(log_mag, axis=0), 0).sum(axis=1)
    thr = flux.mean() + 2 * flux.std()
    peaks = np.flatnonzero((flux[1:-1] > thr) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:])) + 1
    rises = [float(energy[p + 1] - energy[max(0, p - 3)]) for p in peaks if p + 1 < len(energy)]
    return {"percussive_share": round(share, 3), "attack_db": round(float(np.median(rises)), 1) if rises else 0.0,
            "transients_per_s": round(len(peaks) / max(len(flux) / frame_rate, 1e-3), 2)}  # fmt: skip


def analyze(mono: np.ndarray, sr: int) -> dict[str, Any]:
    """The spectral report: summary statistics, the band balance, texture, and the timelines the visuals draw
    (downsampled to ≤ 600 points)."""
    mag, freqs, frame_rate = stft(mono - float(np.mean(mono)), sr)  # a DC offset is a technical fault, not rumble
    energy = (mag**2).sum(axis=1)
    loud = energy > energy.max() * 1e-5 if energy.max() > 0 else energy > 0
    if not loud.any():
        return {"silent": True}
    f = per_frame(mag, freqs)
    w = energy[loud] / energy[loud].sum()
    summary = {k: round(float(np.average(f[k][loud], weights=w)), 4 if k == "flatness" else 0) for k in ("centroid", "bandwidth", "rolloff", "flatness")}
    summary["centroid_p10"] = round(float(np.percentile(f["centroid"][loud], 10)))
    summary["centroid_p90"] = round(float(np.percentile(f["centroid"][loud], 90)))
    mel, centres = mel_spectrogram(mag, freqs, sr)
    # tonal vs noise: flatness under 0.1 is tonal, over 0.4 noise-like (within the 80 Hz–10 kHz band)
    fl = f["flatness"][loud]
    texture = "tonal" if np.median(fl) < 0.1 else "noisy" if np.median(fl) > 0.4 else "mixed"

    def thin(v: np.ndarray, points: int = 600) -> list[float]:
        if len(v) <= points:
            return [round(float(x), 3) for x in v]
        idx = np.linspace(0, len(v) - 1, points).astype(int)
        return [round(float(x), 3) for x in v[idx]]

    return {"frame_rate": round(frame_rate, 3), "summary": summary, "texture": texture, "bands": band_energy(mag[loud], freqs),
            "transients": transients(mag, frame_rate),
            "timeline": {"centroid_hz": thin(f["centroid"]), "flatness": thin(f["flatness"]), "flux": thin(f["flux"]),
                         "energy_db": thin(10 * np.log10(energy + 1e-12))},
            "mel_db": mel, "mel_centres_hz": centres, "mag": mag, "freqs": freqs}  # fmt: skip
