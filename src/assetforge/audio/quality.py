"""Quality review of produced music and ambience: measured technical checks, measured adherence to a brief, and
the concrete production change a failure calls for. Nothing here listens; it measures. A human still decides.

Technical: silence, clipping, DC, leading silence (validation.audio), clicks (very fast broadband spikes), loop
seam, loudness error against the kind's target. Adherence: how busy the piece is (onsets per second) against
the brief's energy, how much of it sits in the 1–4 kHz band where reading attention lives, spectral centre
against the brightness wanted, repetition (self-similarity at lags ≥ 2 s), dynamic range, sub-bass share for
ambience, event regularity. Each failure maps to a change (`REFINEMENTS`), so a bad candidate is refined for
its actual problem instead of re-prompted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter1d

from assetforge.audio import features
from assetforge.audio.codecs import read_audio
from assetforge.audio.master import LOUDNESS_TARGETS
from assetforge.validation.audio import analyze as technical

# beds whose texture is made of small events (crackle, drips, chirps): the usual reason an ambience stays "busy"
# after its event rates were cut
EVENTFUL_BEDS = ("fire_crackle", "water_stream", "lava_bubble", "insects_night", "swamp", "electrical_buzz")

# when a refinement was applied in an earlier round and the same problem is still there, the next round escalates
# instead of repeating a change that did not move the measure (demonstration, 2026-11-11: three rounds of
# event-rate cuts left a cabin at 5 events/s because the busyness was the fire bed's crackle)
ESCALATIONS: dict[str, dict[str, Any]] = {
    "too_busy": {"ambience": {"bed_level_scale": {"beds": list(EVENTFUL_BEDS), "db": -6.0}}, "music": {"percussion": "none", "energy": -0.15, "melody": -0.2}},
    "in_dialogue_band": {"ambience": {"high_cut": 3500, "level_scale": -3}, "music": {"warmth": +0.3, "instrument_swap": "darker"}},
    "too_static": {"ambience": {"intensity": +0.2, "event_rate_scale": 1.6}, "music": {"energy": +0.2, "percussion": "light"}},
}

REFINEMENTS: dict[str, dict[str, Any]] = {
    # metric code → what to change on the next candidate (music / ambience)
    "too_busy": {"music": {"energy": -0.15, "percussion": "light", "melody": -0.3}, "ambience": {"intensity": -0.15, "event_rate_scale": 0.6}},
    "too_static": {"music": {"energy": +0.15, "melody": +0.3}, "ambience": {"intensity": +0.15, "event_rate_scale": 1.4}},
    "in_dialogue_band": {"music": {"warmth": +0.4, "melody": -0.3}, "ambience": {"high_cut": 5000, "level_scale": -2}},
    "too_bright": {"music": {"warmth": +0.4}, "ambience": {"high_cut": 4000}},
    "too_dull": {"music": {"warmth": -0.3, "instrument_swap": "brighter"}, "ambience": {"low_cut": 120}},
    "sub_rumble": {"ambience": {"low_cut": 90}, "music": {"instrument_swap": "lighter_bass"}},
    "repetitive": {"music": {"arc": "rise_fall", "reseed": "melody"}, "ambience": {"evolve": +0.5, "reseed": True}},
    "mechanical_events": {"ambience": {"reseed": True, "event_rate_scale": 0.8}},
    "loop_seam": {"music": {"reseed": "structure"}, "ambience": {"reseed": True}},
    "clicks": {"music": {"reseed": "structure"}, "ambience": {"reseed": True}},
    "loudness_off": {"music": {}, "ambience": {}},
    "flat_dynamics": {"music": {"arc": "rise_fall"}, "ambience": {"evolve": +0.4}},
    "voice_like": {"ambience": {"drop_bed": "crowd_murmur"}, "music": {}},
    "hissy": {"ambience": {"high_cut": 6000}, "music": {"warmth": +0.3}},
}


def events(mono: np.ndarray, sr: int) -> tuple[float, float | None]:
    """Salient events per second and the regularity (CV) of their spacing. `features.analyze`'s onset rate
    (kept for search tags) counts every bump above the file's own mean flux, so tremolo strings or a rain bed
    read as ten "onsets" a second and a review would call a sustained chord busy. Here a frame counts only
    when it rises against the maximum of the previous three frames and the neighbouring bins (SuperFlux:
    vibrato and tremolo cancel),
    beats the file's median flux several times over (texture has no salient events), is a local maximum
    over ±35 ms and comes at least 60 ms after the last one."""
    size, hop, mu = 2048, 512, 3
    if len(mono) < size * 2:
        return 0.0, None
    frames = np.lib.stride_tricks.sliding_window_view(mono, size)[::hop] * np.hanning(size)
    log_mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) / size * 1000)
    # SuperFlux: the reference is the maximum over the previous frames *and* ±3 bins (±65 Hz), so a partial that
    # wobbles with vibrato never counts as a rise; only energy arriving where there was none does.
    widened = maximum_filter1d(log_mag, size=7, axis=1)
    prev = np.stack([np.roll(widened, j, axis=0) for j in range(1, mu + 1)]).max(axis=0)
    flux = np.maximum(log_mag - prev, 0).sum(axis=1)
    flux[:mu] = 0
    fps, duration = sr / hop, len(mono) / sr
    thr = max(flux.mean() + 1.5 * flux.std(), 2.5 * float(np.median(flux)))
    w, gap = 3, int(0.06 * fps)
    idx: list[int] = []
    last = -gap
    for i in range(w, len(flux) - w):
        if flux[i] > thr and flux[i] == flux[i - w:i + w + 1].max() and i - last >= gap:
            idx.append(i)
            last = i
    intervals = np.diff(idx) / fps if len(idx) > 2 else np.array([])
    cv = float(intervals.std() / intervals.mean()) if len(intervals) > 2 and intervals.mean() > 0 else None
    return round(len(idx) / max(duration, 1e-3), 2), (None if cv is None else round(cv, 2))


def measure(path: Path) -> dict[str, Any]:
    """Every number the review uses, from the file."""
    x, sr = read_audio(path)
    data = np.asarray(x, dtype=np.float64)
    f = features.analyze(data, sr)
    tech = technical(data, sr)
    mono = data.mean(axis=1) if data.ndim == 2 else data
    size, hop = 2048, 1024
    frames = np.lib.stride_tricks.sliding_window_view(mono, size)[::hop] * np.hanning(size)
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(size, 1 / sr)
    total = power.sum() + 1e-12
    speech = float(power[:, (freqs >= 1000) & (freqs <= 4000)].sum() / total)
    sub = float(power[:, freqs < 80].sum() / total)
    edges = np.geomspace(60, min(12000, sr / 2), 25)
    bands = np.log1p(np.stack([power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) for lo, hi in zip(edges[:-1], edges[1:])], axis=1))
    bands -= bands.mean(axis=0)
    unit = bands / (np.linalg.norm(bands, axis=1) + 1e-9)[:, None]
    fps = sr / hop
    repetition = 0.0
    for lag in range(int(2 * fps), max(int(2 * fps) + 1, len(unit) // 2)):
        repetition = max(repetition, float(np.mean(np.sum(unit[:-lag] * unit[lag:], axis=1))))
    onset_rate, cv = events(mono, sr)
    win = int(3 * sr)
    rms = [20 * np.log10(np.sqrt(np.mean(mono[i:i + win] ** 2)) + 1e-9) for i in range(0, max(1, len(mono) - win), win)]
    # clicks: sample-level jumps far above the local envelope
    env = np.convolve(np.abs(mono), np.ones(256) / 256, mode="same") + 1e-6
    clicks = int(np.sum(np.abs(np.diff(mono)) > 8 * env[1:]))
    return {"sr": sr, "duration_s": f.get("duration_s"), "lufs": f.get("lufs"), "centroid_hz": f.get("centroid_hz"),
            "flatness": f.get("flatness"), "onset_rate": onset_rate, "loop_seam_ok": f.get("loop_seam_ok"),
            "speech_band_share": round(speech, 3), "sub_share": round(sub, 3), "repetition_index": round(repetition, 3),
            "event_cv": None if cv is None else round(cv, 2), "dynamic_range_db": round(float(max(rms) - min(rms)), 1) if len(rms) > 1 else 0.0,
            "clicks": clicks, "issues": list(tech.get("issues", []))}  # fmt: skip


def expectations(kind: str, direction: dict[str, Any]) -> dict[str, Any]:
    """The measurable ranges a brief implies. Energy 0..1 and brightness 0..1 come from the direction."""
    energy = float(direction.get("energy", 0.4))
    bright = float(direction.get("brightness", 0.5))
    under = bool(direction.get("under_dialogue", True))
    if kind == "music":
        return {"onset_rate": (0.05 + 0.6 * energy, 1.0 + 4.0 * energy), "centroid_hz": (250 + 400 * bright, 900 + 2400 * bright),
                "speech_band_share_max": 0.35 if under else 0.55, "repetition_max": 0.32, "dynamic_range": (1.5, 12.0),
                "sub_share_max": 0.6, "lufs": LOUDNESS_TARGETS["music"]}  # fmt: skip
    activity = float(direction.get("activity", 0.4))
    # stillness is a legitimate ambience, so there is no lower bound; events are also judged by regularity
    return {"onset_rate": (0.0, 1.5 + 6.0 * activity), "centroid_hz": (150 + 300 * bright, 1200 + 3500 * bright),
            "speech_band_share_max": 0.40 if under else 0.6, "repetition_max": 0.5, "dynamic_range": (0.5, 9.0),
            "sub_share_max": 0.45, "event_cv_min": 0.45, "lufs": LOUDNESS_TARGETS["ambience"]}  # fmt: skip


def review(kind: str, direction: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
    """Score a candidate: technical failures are hard (unusable), adherence misses cost points and name a
    refinement. Returns ok, score 0..1, problems (code, message, refinement) and the measured values."""
    exp = expectations(kind, direction)
    problems: list[dict[str, Any]] = []

    def bad(code: str, message: str, *, hard: bool = False, weight: float = 0.15) -> None:
        problems.append({"code": code, "message": message, "hard": hard, "weight": weight,
                         "refinement": REFINEMENTS.get(code, {}).get(kind, {})})  # fmt: skip

    for issue in m.get("issues", []):
        if issue in ("silent", "clipping", "undecodable", "empty"):
            bad("technical_" + issue, f"technical: {issue}", hard=True, weight=1.0)
    if m.get("clicks", 0) > 3:
        bad("clicks", f"{m['clicks']} click-like discontinuities", hard=m["clicks"] > 20, weight=0.3)
    if m.get("loop_seam_ok") is False:
        bad("loop_seam", "the loop boundary is audible", hard=True, weight=0.5)
    if m.get("lufs") is not None and abs(m["lufs"] - exp["lufs"]) > 2.0:
        bad("loudness_off", f"loudness {m['lufs']:.1f} LUFS, target {exp['lufs']:.0f}", weight=0.1)
    lo, hi = exp["onset_rate"]
    if m.get("onset_rate", 0) > hi:
        bad("too_busy", f"{m['onset_rate']:.1f} events/s; the brief's energy implies at most {hi:.1f}", weight=0.25)
    elif m.get("onset_rate", 0) < lo:
        bad("too_static", f"{m['onset_rate']:.1f} events/s; the brief's energy implies at least {lo:.1f}", weight=0.15)
    c = m.get("centroid_hz") or 0
    lo, hi = exp["centroid_hz"]
    if c > hi:
        bad("too_bright", f"spectral centre {c} Hz, above the {hi:.0f} Hz the brief implies", weight=0.15)
    elif c < lo:
        bad("too_dull", f"spectral centre {c} Hz, below the {lo:.0f} Hz the brief implies", weight=0.15)
    if m.get("speech_band_share", 0) > exp["speech_band_share_max"]:
        bad("in_dialogue_band", f"{m['speech_band_share']:.0%} of the energy sits in the 1–4 kHz reading band (max {exp['speech_band_share_max']:.0%})", weight=0.25)
    if m.get("sub_share", 0) > exp["sub_share_max"]:
        bad("sub_rumble", f"{m['sub_share']:.0%} of the energy is below 80 Hz: a rumble, not a place", weight=0.25)
    if m.get("repetition_index", 0) > exp["repetition_max"]:
        bad("repetitive", f"self-similarity {m['repetition_index']:.2f} (max {exp['repetition_max']:.2f})", weight=0.2)
    lo, hi = exp["dynamic_range"]
    if m.get("dynamic_range_db", 0) < lo:
        bad("flat_dynamics", f"dynamic range {m['dynamic_range_db']} dB: nothing changes over the loop", weight=0.1)
    elif m.get("dynamic_range_db", 0) > hi:
        bad("too_busy", f"dynamic range {m['dynamic_range_db']} dB: jumps that will distract", weight=0.15)
    if kind == "ambience" and m.get("event_cv") is not None and m["event_cv"] < exp["event_cv_min"]:
        bad("mechanical_events", f"events arrive on a near-regular grid (CV {m['event_cv']})", weight=0.15)
    score = max(0.0, 1.0 - sum(p["weight"] for p in problems))
    hard = any(p["hard"] for p in problems)
    return {"ok": not hard, "score": round(0.0 if hard else score, 3), "problems": problems, "measured": m, "expected": exp}


def reference_distance(m: dict[str, Any], ref: dict[str, Any]) -> float:
    """How far a candidate's character is from an approved reference's (0 identical … ~1 far), on the
    measures that carry style: brightness, noisiness, busyness, reading-band share, dynamics."""
    def rel(a: float | None, b: float | None, scale: float) -> float:
        if a is None or b is None:
            return 0.0
        return min(1.0, abs(float(a) - float(b)) / scale)

    return round(float(np.mean([
        rel(np.log(max(m.get("centroid_hz") or 1, 1)), np.log(max(ref.get("centroid_hz") or 1, 1)), 1.0),
        rel(m.get("flatness"), ref.get("flatness"), 0.3),
        rel(m.get("onset_rate"), ref.get("onset_rate"), 4.0),
        rel(m.get("speech_band_share"), ref.get("speech_band_share"), 0.3),
        rel(m.get("dynamic_range_db"), ref.get("dynamic_range_db"), 6.0),
    ])), 3)
