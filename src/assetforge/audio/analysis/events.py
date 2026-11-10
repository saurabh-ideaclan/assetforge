"""Sound-event timeline: what seems to be happening, second by second, with how sure the measurement is.

Without a trained tagger this is a set of *acoustic signatures* — each a named pattern of the measurements
that a sound engineer would use to say "that is probably rain" — not recognition. Every label carries a
confidence in 0..1 and the signature that produced it, and the report is marked `method: "heuristic"`. When
an AudioSet-style tagger is installed (`taggers.py`), its labels are listed beside these with their own
confidence; agreement raises confidence, disagreement is reported, neither is hidden.

Signatures (per 1 s window of the 2048/512 STFT):
  speech_like   from `speech.py` (four independent cues)
  rain_like     steady broadband noise in 4–8 kHz (rain, hiss or spray: the signature cannot tell them apart)
  water_drips   sparse, sharp, irregular broadband clicks (3–20 per 10 s) with little sustained HF noise
  wind          band-limited noise 60–800 Hz, flatness high, level wandering at 0.1–2 Hz, no transients
  footsteps     low-frequency thumps (< 300 Hz transients) in a 1–3 Hz quasi-regular pattern
  metal         tonal ringing after a transient: a sharp attack followed by sustained narrow peaks above 1 kHz
  hum           a stable narrow peak under 200 Hz for the whole window
  music         tonal, pitch-class content that changes on a beat-ish grid, harmonic stability between
  silence       below -60 dBFS
"""

from __future__ import annotations

from typing import Any

import numpy as np

from assetforge.audio.analysis import taggers


def _flatness(p: np.ndarray) -> float:
    p = p + 1e-14
    return float(np.exp(np.mean(np.log(p))) / np.mean(p))


def _peakiness(spec: np.ndarray) -> float:
    """Share of the energy in narrow peaks (bins at least 4× the mean of their ±4 neighbours): pitched sound
    is sparse peaks; band-limited noise is not, however low its flatness."""
    if len(spec) < 9:
        return 0.0
    neigh = np.convolve(spec, np.ones(9) / 9, mode="same")
    peaks = spec > 4 * neigh
    return float(spec[peaks].sum() / (spec.sum() + 1e-12))


def signatures(mag: np.ndarray, freqs: np.ndarray, frame_rate: float, speech_segments: list[dict[str, Any]],
               chroma: np.ndarray | None = None) -> list[dict[str, Any]]:  # fmt: skip
    power = mag**2
    n = max(1, int(round(frame_rate)))
    out = []
    speech_by_s = {int(s["start_s"]): s for s in speech_segments}
    log_mag = np.log1p(mag * 1000)
    flux_all = np.concatenate(([0.0], np.maximum(np.diff(log_mag, axis=0), 0).sum(axis=1)))
    for w in range(0, power.shape[0] // n):
        sl = slice(w * n, (w + 1) * n)
        p = power[sl]
        total = p.sum() + 1e-12
        level_db = 10 * np.log10(p.sum(axis=1) + 1e-12)
        if level_db.max() < -100:
            out.append({"start_s": w, "end_s": w + 1, "labels": [{"label": "silence", "confidence": 1.0, "why": "below -60 dBFS"}]})
            continue
        labels: list[dict[str, Any]] = []
        hf = p[:, (freqs >= 4000) & (freqs < 8000)]  # the occupied top band (a low-passed bed has empty bins above)
        lf = p[:, (freqs >= 60) & (freqs < 800)]
        sub = p[:, freqs < 300]
        hf_share = float(hf.sum() / total)
        lf_share = float(lf.sum() / total)
        hf_flat = _flatness(hf.mean(axis=0)) if hf.shape[1] else 0.0
        lf_flat = _flatness(lf.mean(axis=0)) if lf.shape[1] else 0.0
        level_sd = float(level_db.std())
        flux = flux_all[sl]
        thr = flux.mean() + 2.5 * flux.std()
        trans = int(np.sum((flux[1:-1] > thr) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:])))
        sp = speech_by_s.get(w)
        if sp and sp["voice_like"]:
            labels.append({"label": "speech_like", "confidence": round(min(1.0, sp["score"]), 2), "why": f"{sp['passed']}/4 speech cues"})
        if hf_share > 0.15 and hf_flat > 0.4 and level_sd < 2.0:
            labels.append({"label": "rain_like", "confidence": round(min(1.0, 0.4 + hf_share), 2), "why": "steady broadband high-frequency noise (rain, hiss or spray)"})
        if trans >= 1 and hf_share > 0.15 and hf_flat > 0.3 and level_sd > 2.5 and trans <= 4:
            labels.append({"label": "water_drips", "confidence": 0.5, "why": "sparse sharp broadband clicks"})
        if lf_share > 0.6 and lf_flat > 0.35 and trans == 0:
            labels.append({"label": "wind", "confidence": round(min(1.0, 0.4 + lf_share * 0.5), 2), "why": "band-limited low-mid noise, no transients"})
        sub_level = 10 * np.log10(sub.sum(axis=1) + 1e-12)
        thumps = int(np.sum(np.diff(sub_level) > 6))
        if 1 <= thumps <= 3 and float(sub.sum() / total) > 0.4:
            labels.append({"label": "footsteps", "confidence": 0.4, "why": f"{thumps} low-frequency thumps"})
        mean_spec = p.mean(axis=0)
        peak_bin = int(np.argmax(mean_spec))
        narrow = mean_spec[peak_bin] > 4 * mean_spec[max(0, peak_bin - 3):peak_bin + 4].mean() if 3 <= peak_bin < len(mean_spec) - 3 else False
        if freqs[peak_bin] < 200 and narrow and mean_spec[peak_bin] > 20 * np.median(mean_spec) and level_sd < 1.5:
            labels.append({"label": "hum", "confidence": 0.6, "why": f"stable narrow peak at {freqs[peak_bin]:.0f} Hz"})
        tonal = _peakiness(mean_spec[(freqs >= 80) & (freqs <= 8000)]) > 0.3
        attack = float(np.max(np.diff(level_db))) if len(level_db) > 1 else 0.0
        if tonal and trans >= 1 and attack > 10:
            hi = mean_spec[freqs > 1000]
            if hi.max() > 15 * np.median(hi) and level_db[-1] < level_db.max() - 6:
                labels.append({"label": "metal", "confidence": 0.4, "why": "sharp attack, then narrow peaks above 1 kHz ringing down"})
        if tonal and chroma is not None:
            ch = chroma[sl]
            if len(ch) > 2:
                a, b = ch[:-1], ch[1:]
                cos = (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9)
                if cos.mean() > 0.9:
                    labels.append({"label": "music", "confidence": 0.5, "why": "tonal with stable pitch-class content"})
        if not labels:
            labels.append({"label": "texture", "confidence": 0.3, "why": "no signature matched"})
        out.append({"start_s": w, "end_s": w + 1, "labels": labels})
    return out


def analyze(path, mag: np.ndarray, freqs: np.ndarray, frame_rate: float, speech: dict[str, Any], chroma: np.ndarray | None) -> dict[str, Any]:
    timeline = signatures(mag, freqs, frame_rate, speech.get("segments", []), chroma)
    counts: dict[str, int] = {}
    for w in timeline:
        for lab in w["labels"]:
            counts[lab["label"]] = counts.get(lab["label"], 0) + 1
    total = max(len(timeline), 1)
    summary = sorted(({"label": k, "share": round(v / total, 3)} for k, v in counts.items()), key=lambda d: -d["share"])
    tagged = taggers.tag(path)
    return {"method": "heuristic signatures" + (" + " + tagged["model"] if tagged.get("available") else ""),
            "timeline": timeline, "summary": summary, "tagger": tagged}  # fmt: skip
