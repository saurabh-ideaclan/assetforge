"""Render a `Score` (written by an agent, imported from MIDI, or made by the composer) to stereo audio.

Notes are rendered per track by the synth instruments (or the SoundFont backend when installed and
asked for), tracks are panned and processed, grouped by role into stems (drums, bass, harmony,
melody…), and the acoustic space is applied per stem, so the stems always sum to the mix.

Loops render exactly `bars` long: notes and reverb ringing past the end wrap onto the start
(`looping.fold_loop` / `circular_effects`), so the track repeats without a gap or click.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from assetforge.audio import dsp, pipeline
from assetforge.audio.effects import apply_effects, resolve_effects
from assetforge.audio.looping import circular_effects, fold_loop
from assetforge.audio.music import drums, instruments
from assetforge.audio.music.score import Score, Track
from assetforge.audio.music.theory import note_number, swing

ENGINE = "assetforge-music"
ENGINE_VERSION = 1
STEM_OF_ROLE = {"melody": "melody", "counter": "melody", "harmony": "harmony", "pad": "harmony", "arp": "harmony",
                "bass": "bass", "drums": "drums", "percussion": "drums", "fx": "fx"}  # fmt: skip
TAIL_SECONDS = 4.0


def render_track_synth(track: Track, score: Score, sr: int, rng: np.random.Generator, length: int) -> np.ndarray:
    spb = score.seconds_per_beat
    buffer = np.zeros(length + int(TAIL_SECONDS * sr))
    human = 0.0 if track.instrument.startswith("chip") or track.instrument == "drums_chip" else 0.004
    for note in sorted(track.notes, key=lambda n: n.start):
        start = swing(note.start, score.swing) * spb + (rng.uniform(-human, human) if human else 0.0)
        at = max(0, int(round(start * sr)))
        if track.is_drums:
            x = drums.render_hit(note.pitch, track.instrument, note.velocity, rng, sr)
        else:
            x = instruments.INSTRUMENTS[track.instrument].render(note_number(note.pitch), note.beats * spb,
                                                                 note.velocity, sr, rng)  # fmt: skip
        dsp.add_at(buffer, x, at)
    return buffer


def render_score(score: Score, seed: int, sr: int, *, backend: str = "synth",
                 soundfont_sha: str | None = None) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, Any]]:  # fmt: skip
    """Returns (mix, stems by role group, info)."""
    from assetforge.sound import SPACES

    length = int(round(score.seconds * sr))
    space = resolve_effects(SPACES.get(score.space or "room", []))
    groups: dict[str, np.ndarray] = {}
    used: dict[str, str] = {}
    rendered = None
    if backend in ("soundfont", "auto"):
        from assetforge.audio.music import soundfont

        rendered, used = soundfont.render_tracks(score, sr, length + int(TAIL_SECONDS * sr),
                                                 required=backend == "soundfont", sha256=soundfont_sha)  # fmt: skip
    for i, track in enumerate(score.tracks):
        rng = np.random.default_rng([seed, 7, i])
        x = rendered.get(track.name) if rendered else None
        if x is None:
            x = render_track_synth(track, score, sr, rng, length)
            used.setdefault(track.name, "synth")
        if score.loop:
            x = fold_loop(x, length)
            x = circular_effects(x, resolve_effects(track.effects), sr) if track.effects else x
        elif track.effects:
            x = apply_effects(x, resolve_effects(track.effects), sr)
        y = dsp.pan(x, track.pan) * dsp.db(track.gain_db)
        group = STEM_OF_ROLE[track.role]
        if group in groups:
            n = max(len(groups[group]), len(y))
            groups[group] = dsp.fit_len(groups[group], n) + dsp.fit_len(y, n)
        else:
            groups[group] = y
    stems: dict[str, np.ndarray] = {}
    for group, x in groups.items():
        stems[group] = circular_effects(x, space, sr) if score.loop else apply_effects(x, space, sr)
    n = max(len(x) for x in stems.values())
    stems = {k: dsp.fit_len(v, n) for k, v in stems.items()}
    mix = sum(stems.values())
    info = {"summary": score.summary(), "backends": used}
    return mix, stems, info


def score_for(spec: dict[str, Any], seed: int) -> tuple[Score, bool]:
    """The score a spec describes: written out, or composed from a brief (True when composed)."""
    if "score" in spec:
        return Score(**spec["score"]), False
    from assetforge.audio.music.composer import Brief, compose

    return compose(Brief(**spec["compose"]), seed), True


def render(spec: dict[str, Any], index: int, seed: int, sr: int) -> pipeline.Take:
    from assetforge.audio.music.midi import write_midi

    score, composed = score_for(spec, seed)
    backend = {"backend": spec.get("backend", "synth"), "soundfont_sha": (spec.get("soundfont") or {}).get("sha256")}
    mix, stems, info = render_score(score, seed, sr, **backend)
    extras = {f"stem_{k}": v for k, v in stems.items()} if spec.get("stems") and len(stems) > 1 else {}
    for label, layer_spec in (spec.get("layers") or {}).items():
        layer_score, _ = score_for(layer_spec, seed)
        layer_mix, _, _ = render_score(layer_score, seed, sr, **backend)
        extras[label] = dsp.fit_len(layer_mix, len(mix)) if score.loop else layer_mix
    if composed:
        from assetforge.audio.music.composer import Brief, seeds_for

        info["score"] = score.model_dump(mode="json", exclude_defaults=True)
        info["seeds"] = seeds_for(Brief(**spec["compose"]), seed)
    midi = Path(tempfile.mkdtemp(prefix="assetforge-midi-")) / "score.mid"
    return pipeline.Take(mix, extras, files={"midi": write_midi(score, midi)}, info=info)


ENGINE_REF = pipeline.register_engine(ENGINE, ENGINE_VERSION, render, job_kind="audio_music")


def audio_meta(score: Score) -> dict[str, Any]:
    beats_per_bar = score.time.beats_per_bar
    return {"bpm": score.tempo, "key": score.key, "meter": score.meter,
            "bars": int(round(score.total_beats / beats_per_bar)), "length_s": round(score.seconds, 4),
            "loop_start": 0.0 if score.loop else None, "loop_end": round(score.seconds, 4) if score.loop else None,
            "sections": [{"name": s.name, "start_s": round(s.start * score.seconds_per_beat, 4),
                          "end_s": round((s.start + s.beats) * score.seconds_per_beat, 4)} for s in score.sections],
            "instruments": sorted({t.instrument for t in score.tracks})}  # fmt: skip
