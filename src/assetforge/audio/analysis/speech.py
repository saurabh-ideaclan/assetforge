"""Speech-likeness: is there something in this sound that a listener will hear as a voice?

The recurring ambience failure is "people mumbling nonsense". A voice activity detector alone is not an
answer (wind, tremolo strings and choir pads trip it), so four independent cues are measured per second and
combined, each a known property of speech:

1. Syllabic modulation: the envelope of the 300–3400 Hz band modulates at 2–8 Hz (speech peaks near 4 Hz).
   Wind modulates below 2 Hz; a tremolo sits on one sharp rate. The cue is the share of envelope-modulation
   energy in 2–8 Hz, penalised when it is a single spike.
2. Voicing: harmonicity in the 80–400 Hz range (mean normalised autocorrelation peak), reduced when the
   pitch never glides the way speech does (a held note or a melody that leaps).
3. Vowel-like formants: LPC (order 16 at 16 kHz) shows F1 in 300–900 Hz and F2 in 800–2500 Hz, and F2 moves
   between frames the way vowels alternate (a static resonance is an instrument or a room mode).
4. Pause structure: the 300–3400 Hz envelope rises and falls above/below its median several times a second,
   with crest ratios like syllables, not the steady level of a bed.

A second counts as voice-like when at least three cues pass and the combined score ≥ 0.6. The file report
gives the voice-like share of time, the strongest segments and the cue breakdown, so a reviewer can see *why*.
Optional corroboration: the Silero VAD ONNX model (`silero.py`) when installed; the verdict then says
"confirmed" (both agree), "suspected" (one of them) or "unlikely". The DSP cues alone are the floor, never a
claim of certainty: the brief that forbids voices gets a flag and a listening pointer, not a verdict.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import butter, hilbert, lfilter, resample_poly, sosfilt

from assetforge.audio.analysis import silero

ANALYSIS_SR = 16000
SEGMENT_S = 1.0


def _resample(mono: np.ndarray, sr: int, target: int = ANALYSIS_SR) -> np.ndarray:
    if sr == target:
        return mono
    from math import gcd

    g = gcd(sr, target)
    return resample_poly(mono, target // g, sr // g)


def _band(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    sos = butter(4, [lo / (sr / 2), min(hi, sr / 2 - 1) / (sr / 2)], "band", output="sos")
    return sosfilt(sos, x)


def modulation_cue(seg: np.ndarray, sr: int) -> tuple[float, dict[str, float]]:
    """Share of envelope-modulation energy in 2–8 Hz over 0.3–30 Hz, with a penalty for a single-rate peak."""
    env = np.abs(hilbert(_band(seg, sr, 300, 3400)))
    env = resample_poly(env, 1, sr // 100)  # 100 Hz envelope
    env = env - env.mean()
    if not env.any():
        return 0.0, {"mod_ratio": 0.0, "mod_peak_hz": 0.0, "mod_peakiness": 0.0}
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env)), 1024)) ** 2
    f = np.fft.rfftfreq(1024, 1 / 100)
    total = spec[(f >= 0.3) & (f <= 30)].sum() + 1e-12
    syll = spec[(f >= 2) & (f <= 8)].sum() / total
    band = spec[(f >= 2) & (f <= 8)]
    peak_hz = float(f[(f >= 2) & (f <= 8)][np.argmax(band)]) if band.size else 0.0
    # a tremolo or a steady note rate is one spike: the top three bins hold most of the band; speech spreads
    top = np.sort(band)[-3:].sum() / (band.sum() + 1e-12) if band.size >= 3 else 0.0
    peakiness = float(top)
    score = float(np.clip((syll - 0.25) / 0.35, 0, 1)) * (0.5 if top > 0.5 else 1.0)
    return score, {"mod_ratio": round(float(syll), 3), "mod_peak_hz": round(peak_hz, 1), "mod_peakiness": round(peakiness, 1)}


def voicing_cue(seg: np.ndarray, sr: int) -> tuple[float, dict[str, float]]:
    """Harmonicity in the voice range (mean normalised autocorrelation peak at lags for 80–400 Hz: a single
    voice ≈ 0.6–0.8, a crowd 0.35–0.55, wind and rain < 0.2, a sustained note ≈ 0.9) and how the pitch moves:
    speech glides (1.5–8 % per 10 ms), a held note sits, a melody leaps. The glide only *reduces* the cue, so
    a sustained instrument still reads as harmonic (it is), but at most half as voice-like."""
    frame, hop = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 80)
    peaks: list[float] = []
    f0s: list[float] = []
    for i in range(0, len(seg) - frame, hop):
        x = seg[i:i + frame]
        x = x - x.mean()
        if np.sqrt(np.mean(x**2)) < 1e-4:
            continue
        ac = np.correlate(x, x, "full")[frame - 1:]
        ac = ac / (ac[0] + 1e-12)
        lag = lo + int(np.argmax(ac[lo:hi]))
        peaks.append(float(ac[lag]))
        if 0 < lag < len(ac) - 1:
            a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
            shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0  # sub-sample lag
            f0s.append(sr / (lag + float(np.clip(shift, -0.5, 0.5))))
        else:
            f0s.append(sr / lag)
    if len(peaks) < 5:
        return 0.0, {"harmonicity": 0.0, "pitch_glide": 0.0}
    harm = float(np.mean(peaks))
    f = np.array(f0s)
    rel = np.abs(np.diff(f)) / (f[:-1] + 1e-9)
    glide = float(np.mean((rel > 0.015) & (rel < 0.08)))
    score = float(np.clip((harm - 0.2) / 0.4, 0, 1)) * max(0.5, float(np.clip(glide / 0.3, 0, 1)))
    return score, {"harmonicity": round(harm, 3), "pitch_glide": round(glide, 3)}


def _lpc(x: np.ndarray, order: int) -> np.ndarray:
    r = np.correlate(x, x, "full")[len(x) - 1:len(x) + order]
    if r[0] <= 0:
        return np.zeros(order)
    # Levinson–Durbin
    a = np.zeros(order + 1)
    a[0] = 1.0
    err = r[0]
    for i in range(1, order + 1):
        acc = r[i] + np.dot(a[1:i], r[i - 1:0:-1])
        k = -acc / err
        a[1:i] = a[1:i] + k * a[i - 1:0:-1]
        a[i] = k
        err *= 1 - k * k
        if err <= 0:
            break
    return a


def formant_cue(seg: np.ndarray, sr: int) -> tuple[float, dict[str, float]]:
    """Share of 25 ms frames with vowel-like F1/F2, and how much F2 moves between frames."""
    frame, hop = int(0.025 * sr), int(0.01 * sr)
    pre = lfilter([1, -0.97], [1], seg)
    f1s, f2s = [], []
    for i in range(0, len(pre) - frame, hop):
        x = pre[i:i + frame] * np.hamming(frame)
        if np.sqrt(np.mean(x**2)) < 1e-4:
            continue
        a = _lpc(x, 16)
        roots = np.roots(a)
        roots = roots[np.imag(roots) > 0.01]
        fr = np.arctan2(np.imag(roots), np.real(roots)) * sr / (2 * np.pi)
        bw = -(sr / np.pi) * np.log(np.abs(roots) + 1e-12)  # 3 dB bandwidth of a pole at radius r
        order = np.argsort(fr)
        # a formant is a resonance 40–300 Hz wide: the broad peaks of band-limited noise (> 300) and the LPC poles
        # that lock onto a single harmonic of a melody (< 40) are both excluded
        cand = [float(fr[i]) for i in order if 90 < fr[i] < 4000 and 40 < bw[i] < 300]
        if len(cand) >= 2:
            f1s.append(cand[0])
            f2s.append(cand[1])
    if len(f1s) < 10:
        return 0.0, {"vowel_share": 0.0, "f2_movement": 0.0}
    f1, f2 = np.array(f1s), np.array(f2s)
    vowel = (f1 > 300) & (f1 < 900) & (f2 > 800) & (f2 < 2500) & (f2 - f1 > 300)
    share = float(vowel.mean())
    move = float(np.std(f2[vowel]) / (np.mean(f2[vowel]) + 1e-9)) if vowel.sum() > 5 else 0.0
    # vowels glide: F2 of consecutive frames moves smoothly; the peaks of noise jump at random (> 300 Hz per 10 ms)
    jumps = np.abs(np.diff(f2[vowel])) if vowel.sum() > 5 else np.array([])
    jumpy = float(np.mean(jumps > 300)) if jumps.size else 1.0
    score = float(np.clip((share - 0.25) / 0.4, 0, 1)) * float(np.clip(move / 0.12, 0, 1)) * (0.3 if jumpy > 0.5 else 1.0)
    return score, {"vowel_share": round(share, 3), "f2_movement": round(move, 3), "f2_jumpy": round(jumpy, 3)}


def pause_cue(seg: np.ndarray, sr: int) -> tuple[float, dict[str, float]]:
    """Syllable-like level alternation: crossings of the 300–3400 Hz envelope through its median at 2–10/s
    and an envelope crest (peak/median) above 2.5."""
    env = np.abs(hilbert(_band(seg, sr, 300, 3400)))
    env = resample_poly(env, 1, sr // 100)
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    med = np.median(env) + 1e-9
    above = env > med
    crossings = int(np.sum(np.diff(above.astype(int)) != 0)) / 2 / (len(env) / 100)
    crest = float(np.percentile(env, 95) / med)
    score = float(np.clip((crossings - 1.5) / 4, 0, 1)) * float(np.clip((crest - 1.8) / 1.2, 0, 1))
    return score, {"syllable_rate": round(float(crossings), 2), "envelope_crest": round(crest, 2)}


def analyze(mono: np.ndarray, sr: int, *, with_vad: bool = True) -> dict[str, Any]:
    """Per-second cue scores, the voice-like segments, the file verdict and (when installed) the Silero VAD."""
    x = _resample(np.asarray(mono, dtype=np.float64), sr)
    sr = ANALYSIS_SR
    n = int(SEGMENT_S * sr)
    segments = []
    for i in range(0, max(len(x) - n + 1, 1), n):
        seg = x[i:i + n]
        if len(seg) < n // 2 or np.sqrt(np.mean(seg**2)) < 1e-4:
            continue
        m, md = modulation_cue(seg, sr)
        v, vd = voicing_cue(seg, sr)
        f, fd = formant_cue(seg, sr)
        p, pd = pause_cue(seg, sr)
        cues = {"modulation": round(m, 3), "voicing": round(v, 3), "formants": round(f, 3), "pauses": round(p, 3)}
        passed = sum(1 for c in cues.values() if c >= 0.4)
        score = round(float(np.mean(list(cues.values()))), 3)
        # a crowd fills the pauses, but still shows harmonic voices with moving formants under syllabic modulation
        crowd = m >= 0.3 and v >= 0.4 and f >= 0.4
        segments.append({"start_s": round(i / sr, 1), "end_s": round((i + len(seg)) / sr, 1), "score": score, "passed": passed,
                         "voice_like": bool(((passed >= 3 and score >= 0.6) or crowd) and f >= 0.3), "crowd_like": bool(crowd and passed < 3),
                         "cues": cues, "detail": {**md, **vd, **fd, **pd}})  # fmt: skip
    share = float(np.mean([s["voice_like"] for s in segments])) if segments else 0.0
    strongest = sorted(segments, key=lambda s: -s["score"])[:5]
    out: dict[str, Any] = {"segments": segments, "voice_like_share": round(share, 3),
                           "max_score": max((s["score"] for s in segments), default=0.0),
                           "strongest": [{k: s[k] for k in ("start_s", "end_s", "score", "passed")} for s in strongest],
                           "method": "dsp cues (modulation, voicing, formants, pauses)"}  # fmt: skip
    vad = silero.detect(x, sr) if with_vad else None
    out["vad"] = vad
    hot = [s for s in segments if s["voice_like"]]
    dsp_hit = share > 0.1 or any(s["score"] >= 0.7 for s in hot)  # a short mumble in a long loop still counts
    if vad and vad.get("available"):
        vad_segs = vad.get("segments", [])
        vad_hit = vad["speech_share"] > 0.1 or any(v["end_s"] - v["start_s"] >= 0.5 for v in vad_segs)
        # confirmation means both fired on the *same* seconds, not merely somewhere in the file
        same = [s for s in hot if any(v["start_s"] < s["end_s"] and v["end_s"] > s["start_s"] for v in vad_segs)]
        out["agreed_segments"] = [{k: s[k] for k in ("start_s", "end_s", "score")} for s in same][:10]
        out["verdict"] = "confirmed" if same else "suspected" if (dsp_hit or vad_hit) else "unlikely"
        out["verdict_basis"] = f"dsp cues and Silero VAD agree on {len(same)} second(s)" if same else \
            "one of the two detectors fired; listen to the strongest segment" if out["verdict"] == "suspected" else "neither detector fired"
    else:
        out["verdict"] = "suspected" if dsp_hit else "unlikely"
        out["verdict_basis"] = "dsp cues only (no VAD model installed): a flag to listen to, not a finding"
    return out
