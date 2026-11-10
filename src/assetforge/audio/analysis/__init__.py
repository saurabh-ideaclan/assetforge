"""The Audio Analysis Engine: one call that reads a file and reports what is in it, at every level.

    report = analyze_file(path, kind="music")        # technical, spectral, musical, speech, events
    text   = interpret(report)                        # the human-readable reading of it (interpret.py)
    findings = diagnose(report, brief=…)              # named problems with evidence and corrections (diagnose.py)
    visuals = render(report, out_dir)                 # PNG / JSON diagnostics (visuals.py)

Layers and what each can and cannot say:
  technical  objective (true peak, LUFS, LRA, clipping, clicks, stereo, loop seam) — a defect is a defect
  spectral   descriptive (brightness, balance, texture, transients) — says how it sounds, not what it is
  music      structural (events, tempo with confidence, key, harmonic motion, sections, repetition, arc)
  speech     evidence (four cues per second, optional VAD) — a flag with a listening pointer
  events     heuristic signatures (rain, wind, drips, hum, music…) with confidence, never recognition
Big arrays (STFT, mel, chroma, self-similarity) stay in the report under `arrays` for the visuals and are
dropped by `to_json`. Nothing here listens; the point is to measure what a listener would be reacting to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from assetforge.audio.analysis import events as events_mod
from assetforge.audio.analysis import music as music_mod
from assetforge.audio.analysis import signal as signal_mod
from assetforge.audio.analysis import spectral as spectral_mod
from assetforge.audio.analysis import speech as speech_mod
from assetforge.audio.codecs import read_audio

MAX_SECONDS = 180


def analyze_array(x: np.ndarray, sr: int, *, kind: str | None = None, path: Path | None = None, with_vad: bool = True) -> dict[str, Any]:
    data = np.asarray(x, dtype=np.float64)[: MAX_SECONDS * sr]
    mono = data.mean(axis=1) if data.ndim == 2 else data
    technical = signal_mod.analyze(data, sr)
    spectral = spectral_mod.analyze(mono, sr)
    report: dict[str, Any] = {"path": str(path) if path else None, "kind": kind, "technical": technical}
    if spectral.get("silent"):
        report |= {"spectral": {"silent": True}, "music": None, "speech": None, "events": None, "arrays": {}}
        return report
    arrays = {"mag": spectral.pop("mag"), "freqs": spectral.pop("freqs"), "mel_db": spectral.pop("mel_db"),
              "mel_centres_hz": spectral.pop("mel_centres_hz")}  # fmt: skip
    music = music_mod.analyze(mono, sr, {**spectral, **arrays}, technical["loudness"])
    arrays["chroma"] = music.pop("chroma")
    arrays["similarity"] = music.pop("similarity_matrix")
    speech = speech_mod.analyze(mono, sr, with_vad=with_vad)
    ev = events_mod.analyze(path, arrays["mag"], arrays["freqs"], spectral["frame_rate"], speech, arrays["chroma"])
    report |= {"spectral": spectral, "music": music, "speech": speech, "events": ev, "arrays": arrays}
    return report


def analyze_file(path: Path | str, *, kind: str | None = None, with_vad: bool = True) -> dict[str, Any]:
    x, sr = read_audio(path, always_2d=False)
    return analyze_array(x, sr, kind=kind, path=Path(path), with_vad=with_vad)


def to_json(report: dict[str, Any]) -> dict[str, Any]:
    """The report without the big arrays, with NumPy scalars turned into Python ones."""
    def clean(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k != "arrays"}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            return v.tolist()
        return v

    return clean(report)


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(to_json(report), indent=1)


def diagnose_file(path: Path | str, *, kind: str | None = None, brief: str | None = None, direction: dict[str, Any] | None = None,
                  loop: bool | None = None, with_vad: bool = True) -> dict[str, Any]:
    """Analyse, diagnose and interpret in one go. Returns the JSON-safe report plus `findings`, `summary` and `text`."""
    from assetforge.audio.analysis.diagnose import diagnose, summarize
    from assetforge.audio.analysis.interpret import text

    report = analyze_file(path, kind=kind, with_vad=with_vad)
    findings = diagnose(report, kind=kind, brief=brief, direction=direction, loop=loop)
    out = to_json(report)
    out["findings"] = findings
    out["summary"] = summarize(findings)
    out["text"] = text(report, findings)
    return out
