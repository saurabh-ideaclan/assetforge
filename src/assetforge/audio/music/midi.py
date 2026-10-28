"""Score ↔ Standard MIDI File (via mido): export every track with its GM program, import any .mid."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mido

from assetforge.audio.music.drums import GM_DRUMS, GM_TO_PIECE, drum_piece
from assetforge.audio.music.instruments import INSTRUMENTS
from assetforge.audio.music.score import Score
from assetforge.audio.music.theory import note_number
from assetforge.errors import InvalidInputError

TICKS = 480
DRUM_CHANNEL = 9
# GM program → closest synth instrument, for imports
_FAMILY_BY_PROGRAM = [
    (0, 7, "piano"), (8, 15, "celesta"), (16, 23, "organ"), (24, 31, "guitar"), (32, 39, "synth_bass"),
    (40, 47, "strings"), (48, 51, "strings"), (52, 54, "choir"), (55, 55, "strings"), (56, 63, "brass"),
    (64, 71, "clarinet"), (72, 79, "flute"), (80, 80, "square_lead"), (81, 87, "saw_lead"), (88, 95, "warm_pad"),
    (96, 103, "supersaw_pad"), (104, 111, "koto"), (112, 119, "marimba"), (120, 127, "warm_pad"),
]  # fmt: skip


def instrument_for_program(program: int) -> str:
    for lo, hi, name in _FAMILY_BY_PROGRAM:
        if lo <= program <= hi:
            return name
    return "piano"


def _key_signature(key: str | None) -> str | None:
    if not key:
        return None
    tonic, _, mode = key.partition(" ")
    if mode not in ("major", "minor"):
        return None
    names = {"A#": "Bb", "D#": "Eb", "G#": "Ab", "C#": "C#", "F#": "F#"}
    tonic = names.get(tonic, tonic)
    return tonic + ("m" if mode == "minor" else "")


def write_midi(score: Score, path: Path) -> Path:
    mid = mido.MidiFile(ticks_per_beat=TICKS)
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(score.tempo), time=0))
    meter = score.time
    meta.append(mido.MetaMessage("time_signature", numerator=meter.numerator, denominator=meter.denominator, time=0))
    if (sig := _key_signature(score.key)) is not None:
        try:
            meta.append(mido.MetaMessage("key_signature", key=sig, time=0))
        except ValueError:
            pass
    meta.append(mido.MetaMessage("end_of_track", time=int(score.total_beats * TICKS)))
    mid.tracks.append(meta)
    channel_pool = [c for c in range(16) if c != DRUM_CHANNEL]
    for i, track in enumerate(score.tracks):
        mt = mido.MidiTrack()
        mt.append(mido.MetaMessage("track_name", name=track.name, time=0))
        channel = DRUM_CHANNEL if track.is_drums else channel_pool[i % len(channel_pool)]
        if not track.is_drums:
            mt.append(mido.Message("program_change", program=INSTRUMENTS[track.instrument].program, channel=channel, time=0))
        events: list[tuple[int, int, str, int, int]] = []
        for note in track.notes:
            pitch = GM_DRUMS[drum_piece(note.pitch)] if track.is_drums else note_number(note.pitch)
            on, off = round(note.start * TICKS), round((note.start + note.beats) * TICKS)
            velocity = max(1, min(127, round(note.velocity * 127)))
            events += [(on, 1, "note_on", pitch, velocity), (max(off, on + 1), 0, "note_off", pitch, 0)]
        events.sort(key=lambda e: (e[0], e[1]))
        now = 0
        for tick, _, kind, pitch, velocity in events:
            mt.append(mido.Message(kind, note=pitch, velocity=velocity, channel=channel, time=tick - now))
            now = tick
        mt.append(mido.MetaMessage("end_of_track", time=0))
        mid.tracks.append(mt)
    mid.save(str(path))
    return path


def read_midi(path: Path, *, loop: bool = False) -> Score:
    """Any Standard MIDI File → Score (drums from channel 10, instruments from program changes)."""
    try:
        mid = mido.MidiFile(str(path))
    except (OSError, ValueError, EOFError) as exc:
        raise InvalidInputError(f"Cannot read MIDI file {path}: {exc}") from exc
    tpb = mid.ticks_per_beat or TICKS
    tempo, meter, key = 120.0, "4/4", None
    tracks: list[dict[str, Any]] = []
    for index, mt in enumerate(mid.tracks):
        now, name = 0, f"track_{index}"
        programs: dict[int, int] = {}
        open_notes: dict[tuple[int, int], tuple[int, int]] = {}
        notes: dict[int, list[dict[str, Any]]] = {}
        for msg in mt:
            now += msg.time
            if msg.type == "set_tempo" and tempo == 120.0:
                tempo = round(mido.tempo2bpm(msg.tempo), 3)
            elif msg.type == "time_signature":
                meter = f"{msg.numerator}/{msg.denominator}"
            elif msg.type == "key_signature" and key is None:
                tonic = msg.key.rstrip("m")
                key = f"{tonic} {'minor' if msg.key.endswith('m') else 'major'}"
            elif msg.type == "track_name":
                name = msg.name or name
            elif msg.type == "program_change":
                programs[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                open_notes[(msg.channel, msg.note)] = (now, msg.velocity)
            elif msg.type in ("note_off", "note_on") and (msg.channel, msg.note) in open_notes:
                start, velocity = open_notes.pop((msg.channel, msg.note))
                notes.setdefault(msg.channel, []).append({
                    "pitch": msg.note, "start": round(start / tpb, 4), "beats": max(round((now - start) / tpb, 4), 0.01),
                    "velocity": round(velocity / 127, 3)})  # fmt: skip
        for channel, items in notes.items():
            if channel == DRUM_CHANNEL:
                items = [n | {"pitch": GM_TO_PIECE[n["pitch"]]} for n in items if n["pitch"] in GM_TO_PIECE]
                instrument, role = "drums", "drums"
            else:
                instrument = instrument_for_program(programs.get(channel, 0))
                avg = sum(n["pitch"] for n in items) / len(items)
                role = "bass" if avg < 50 else "melody"
            if items:
                label = name if len(notes) == 1 else f"{name}_{channel}"
                tracks.append({"name": label, "instrument": instrument, "role": role, "notes": items})
    if not tracks:
        raise InvalidInputError(f"{path} has no notes")
    return Score(tempo=tempo, meter=meter, key=key, loop=loop, tracks=tracks[:24])
