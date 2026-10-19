"""Effect chain built on Spotify's Pedalboard.

Effects are plain data, `{"name": "reverb", "params": {"room_size": 0.4}}`, so they can be
stored with each sound and replayed exactly. `resolve_effects` fills defaults and
validates ranges; `apply_effects` renders them, leaving room for reverb/delay tails.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pedalboard as pb

from assetforge.errors import InvalidInputError


@dataclass(frozen=True)
class EffectParam:
    name: str
    default: float
    min: float
    max: float
    description: str


@dataclass(frozen=True)
class EffectSpec:
    name: str
    description: str
    params: tuple[EffectParam, ...]
    build: Callable[[dict[str, float]], pb.Plugin]
    tail: Callable[[dict[str, float]], float] = lambda p: 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": [
                {"name": p.name, "default": p.default, "min": p.min, "max": p.max, "description": p.description}
                for p in self.params
            ],
        }


def _p(name: str, default: float, lo: float, hi: float, description: str) -> EffectParam:
    return EffectParam(name, default, lo, hi, description)


EFFECTS: dict[str, EffectSpec] = {
    spec.name: spec
    for spec in (
        EffectSpec(
            "reverb",
            "Room reverb",
            (
                _p("room_size", 0.3, 0.0, 1.0, "Room size"),
                _p("damping", 0.5, 0.0, 1.0, "High-frequency damping"),
                _p("wet_level", 0.2, 0.0, 1.0, "Reverb level"),
                _p("dry_level", 0.8, 0.0, 1.0, "Direct level"),
            ),
            lambda p: pb.Reverb(room_size=p["room_size"], damping=p["damping"], wet_level=p["wet_level"],
                                dry_level=p["dry_level"]),  # fmt: skip
            tail=lambda p: 0.4 + 2.6 * p["room_size"],
        ),
        EffectSpec(
            "delay",
            "Echo",
            (
                _p("delay_seconds", 0.12, 0.01, 1.0, "Echo spacing"),
                _p("feedback", 0.3, 0.0, 0.9, "Echo repeats"),
                _p("mix", 0.25, 0.0, 1.0, "Wet/dry mix"),
            ),
            lambda p: pb.Delay(delay_seconds=p["delay_seconds"], feedback=p["feedback"], mix=p["mix"]),
            tail=lambda p: min(3.0, p["delay_seconds"] * (1 + 20 * p["feedback"])),
        ),
        EffectSpec(
            "distortion",
            "tanh distortion",
            (_p("drive_db", 12.0, 0.0, 48.0, "Drive"),),
            lambda p: pb.Distortion(drive_db=p["drive_db"]),
        ),
        EffectSpec(
            "bitcrush",
            "Bit-depth reduction (retro / lo-fi)",
            (_p("bit_depth", 8.0, 2.0, 16.0, "Bits"),),
            lambda p: pb.Bitcrush(bit_depth=p["bit_depth"]),
        ),
        EffectSpec(
            "resample",
            "Down-and-up resampling for aliased, 8-bit-console grit",
            (_p("target_sample_rate", 11025.0, 2000.0, 44100.0, "Intermediate sample rate"),),
            lambda p: pb.Resample(target_sample_rate=p["target_sample_rate"]),
        ),
        EffectSpec(
            "lowpass",
            "Low-pass filter",
            (_p("cutoff_hz", 4000.0, 50.0, 20000.0, "Cutoff"),),
            lambda p: pb.LowpassFilter(cutoff_frequency_hz=p["cutoff_hz"]),
        ),
        EffectSpec(
            "highpass",
            "High-pass filter",
            (_p("cutoff_hz", 200.0, 20.0, 10000.0, "Cutoff"),),
            lambda p: pb.HighpassFilter(cutoff_frequency_hz=p["cutoff_hz"]),
        ),
        EffectSpec(
            "chorus",
            "Chorus",
            (
                _p("rate_hz", 1.0, 0.05, 20.0, "LFO rate"),
                _p("depth", 0.25, 0.0, 1.0, "Depth"),
                _p("mix", 0.5, 0.0, 1.0, "Wet/dry mix"),
            ),
            lambda p: pb.Chorus(rate_hz=p["rate_hz"], depth=p["depth"], mix=p["mix"]),
            tail=lambda p: 0.05,
        ),
        EffectSpec(
            "phaser",
            "Phaser",
            (
                _p("rate_hz", 1.0, 0.05, 20.0, "LFO rate"),
                _p("depth", 0.5, 0.0, 1.0, "Depth"),
                _p("mix", 0.5, 0.0, 1.0, "Wet/dry mix"),
            ),
            lambda p: pb.Phaser(rate_hz=p["rate_hz"], depth=p["depth"], mix=p["mix"]),
            tail=lambda p: 0.05,
        ),
        EffectSpec(
            "compressor",
            "Dynamic range compressor",
            (
                _p("threshold_db", -18.0, -60.0, 0.0, "Threshold"),
                _p("ratio", 4.0, 1.0, 20.0, "Ratio"),
                _p("attack_ms", 1.0, 0.1, 100.0, "Attack"),
                _p("release_ms", 80.0, 5.0, 1000.0, "Release"),
            ),
            lambda p: pb.Compressor(threshold_db=p["threshold_db"], ratio=p["ratio"], attack_ms=p["attack_ms"],
                                    release_ms=p["release_ms"]),  # fmt: skip
        ),
        EffectSpec(
            "pitch_shift",
            "Pitch shift without changing length",
            (_p("semitones", 0.0, -24.0, 24.0, "Shift"),),
            lambda p: pb.PitchShift(semitones=p["semitones"]),
        ),
        EffectSpec(
            "gain",
            "Gain before final normalization (drives later effects harder)",
            (_p("gain_db", 0.0, -24.0, 24.0, "Gain"),),
            lambda p: pb.Gain(gain_db=p["gain_db"]),
        ),
    )
}


def parse_effect(text: str) -> dict[str, Any]:
    """'reverb:room_size=0.5,wet_level=0.3' -> {'name': 'reverb', 'params': {...}}."""
    name, _, rest = text.partition(":")
    params: dict[str, Any] = {}
    for pair in filter(None, (p.strip() for p in rest.split(","))):
        key, sep, value = pair.partition("=")
        if not sep:
            raise InvalidInputError(f"Effect parameter {pair!r} must look like name=value", effect=text)
        try:
            params[key.strip()] = float(value)
        except ValueError as exc:
            raise InvalidInputError(f"Effect parameter {key!r} must be a number", effect=text) from exc
    return {"name": name.strip().lower(), "params": params}


def resolve_effects(effects: list[dict[str, Any] | str]) -> list[dict[str, Any]]:
    """Validate effects and fill in every default, so the stored chain replays exactly."""
    resolved = []
    for raw in effects:
        effect = parse_effect(raw) if isinstance(raw, str) else raw
        name = str(effect.get("name", "")).lower()
        spec = EFFECTS.get(name)
        if spec is None:
            raise InvalidInputError(f"Unknown effect {name!r}", available=sorted(EFFECTS))
        given = dict(effect.get("params") or {})
        unknown = set(given) - {p.name for p in spec.params}
        if unknown:
            raise InvalidInputError(
                f"Unknown parameter(s) for {name}: {', '.join(sorted(unknown))}",
                allowed=[p.name for p in spec.params],
            )
        params = {}
        for p in spec.params:
            value = float(given.get(p.name, p.default))
            if not p.min <= value <= p.max:
                raise InvalidInputError(f"{name}.{p.name}={value} is outside {p.min}..{p.max}")
            params[p.name] = value
        resolved.append({"name": name, "params": params})
    return resolved


def apply_effects(x: np.ndarray, effects: list[dict[str, Any]], sr: int) -> np.ndarray:
    if not effects:
        return x
    specs = [(EFFECTS[e["name"]], e["params"]) for e in effects]
    tail = sum(spec.tail(params) for spec, params in specs)
    padded = np.concatenate([x, np.zeros(int(tail * sr))]).astype(np.float32)
    board = pb.Pedalboard([spec.build(params) for spec, params in specs])
    return board(padded, sr).astype(np.float64)
