"""Recipe model, presets, parameter resolution and seeded variation.

A recipe is a pure function `(RenderContext, params) -> samples` plus a typed parameter
schema. Every recipe shares three common parameters handled here:
`duration` (seconds), `pitch` (semitones) and `brightness` (-1 muffled .. +1 thin/bright).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from assetforge.audio.synth import semitones
from assetforge.core.models import AssetType
from assetforge.errors import InvalidInputError

ParamValue = float | int | str


@dataclass(frozen=True)
class Param:
    name: str
    default: ParamValue
    min: float | None = None
    max: float | None = None
    description: str = ""
    jitter: float = 0.1  # fraction of the range a variation of 1.0 may move the value
    kind: Literal["float", "int", "choice"] = "float"
    choices: tuple[str, ...] = ()

    def coerce(self, value: Any) -> ParamValue:
        if self.kind == "choice":
            text = str(value).strip().lower()
            if text not in self.choices:
                raise InvalidInputError(f"{self.name} must be one of {', '.join(self.choices)}", value=value)
            return text
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(f"{self.name} must be a number", value=value) from exc
        if self.kind == "int":
            if number != int(number):
                raise InvalidInputError(f"{self.name} must be a whole number", value=value)
            number = int(number)
        assert self.min is not None and self.max is not None
        if not self.min <= number <= self.max:
            raise InvalidInputError(f"{self.name}={number} is outside {self.min:g}..{self.max:g}", value=value)
        return number

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "kind": self.kind, "default": self.default,
                                "description": self.description}  # fmt: skip
        if self.kind == "choice":
            data["choices"] = list(self.choices)
        else:
            data |= {"min": self.min, "max": self.max}
        return data


def num(name: str, default: float, lo: float, hi: float, description: str, jitter: float = 0.1) -> Param:
    return Param(name, default, lo, hi, description, jitter)


def integer(name: str, default: int, lo: int, hi: int, description: str, jitter: float = 0.0) -> Param:
    return Param(name, default, lo, hi, description, jitter, kind="int")


def choice(name: str, default: str, choices: tuple[str, ...], description: str) -> Param:
    return Param(name, default, description=description, jitter=0.0, kind="choice", choices=choices)


WAVES = ("sine", "triangle", "square", "saw")


@dataclass
class RenderContext:
    sr: int
    duration: float
    pitch: float  # frequency multiplier from the common `pitch` parameter
    rng: np.random.Generator

    @property
    def n(self) -> int:
        return max(1, int(round(self.duration * self.sr)))


RenderFn = Callable[[RenderContext, dict[str, Any]], np.ndarray]


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    render: RenderFn
    duration: float
    duration_range: tuple[float, float]
    params: tuple[Param, ...] = ()
    tags: tuple[str, ...] = ()
    type: AssetType = AssetType.SFX
    version: int = 1
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def all_params(self) -> tuple[Param, ...]:
        lo, hi = self.duration_range
        return (
            num("duration", self.duration, lo, hi, "Length in seconds (tail may be trimmed)", jitter=0.08),
            num("pitch", 0.0, -24.0, 24.0, "Pitch offset in semitones", jitter=0.05),
            num("brightness", 0.0, -1.0, 1.0, "Tone: negative is muffled/soft, positive is thin/bright", 0.08),
            *self.params,
        )

    def param(self, name: str) -> Param | None:
        return next((p for p in self.all_params() if p.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
            "version": self.version,
            "tags": list(self.tags),
            "default_duration": self.duration,
            "params": [p.to_dict() for p in self.all_params()],
            "presets": sorted(set(PRESETS) | set(self.presets)),
        }


# Presets are partial parameter sets plus effects. Parameters a recipe doesn't have are skipped,
# so one vocabulary ("soft", "retro", ...) works across every recipe.
PRESETS: dict[str, dict[str, Any]] = {
    "soft": {"description": "Gentle, rounded, muffled", "params": {"brightness": -0.35, "wave": "triangle",
             "crunch": 0.1, "grit": 0.15}, "effects": []},  # fmt: skip
    "punchy": {"description": "Tight and loud", "params": {"brightness": 0.1},
               "effects": [{"name": "compressor", "params": {"threshold_db": -22, "ratio": 6}}]},  # fmt: skip
    "retro": {"description": "8-bit console", "params": {"wave": "square"},
              "effects": [{"name": "bitcrush", "params": {"bit_depth": 6}},
                          {"name": "resample", "params": {"target_sample_rate": 11025}}]},  # fmt: skip
    "cartoon": {"description": "Bouncy and exaggerated", "params": {"pitch": 3, "wobble": 0.6, "brightness": 0.05},
                "effects": []},  # fmt: skip
    "heavy": {"description": "Low, weighty, distorted", "params": {"pitch": -7, "brightness": -0.15},
              "effects": [{"name": "distortion", "params": {"drive_db": 10}}]},  # fmt: skip
    "bright": {"description": "Crisp and sparkly", "params": {"brightness": 0.35}, "effects": []},
    "dark": {"description": "Muffled and ominous", "params": {"brightness": -0.6, "pitch": -3}, "effects": []},
    "tiny": {"description": "Small and high", "params": {"pitch": 9}, "effects": []},
    "spacious": {"description": "Roomy reverb tail", "params": {},
                 "effects": [{"name": "reverb", "params": {"room_size": 0.6, "wet_level": 0.3}}]},  # fmt: skip
    "lofi": {"description": "Dull, crunchy, low fidelity", "params": {},
             "effects": [{"name": "lowpass", "params": {"cutoff_hz": 3500}},
                         {"name": "bitcrush", "params": {"bit_depth": 8}}]},  # fmt: skip
}

RECIPES: dict[str, Recipe] = {}


def register(recipe: Recipe) -> Recipe:
    if recipe.name in RECIPES:
        raise ValueError(f"duplicate recipe {recipe.name}")
    RECIPES[recipe.name] = recipe
    return recipe


def recipe(name: str, description: str, *, duration: float, duration_range: tuple[float, float],
           params: tuple[Param, ...] = (), tags: tuple[str, ...] = (), type: AssetType = AssetType.SFX,
           version: int = 1, presets: dict[str, dict[str, Any]] | None = None) -> Callable[[RenderFn], RenderFn]:  # fmt: skip
    def decorate(fn: RenderFn) -> RenderFn:
        register(Recipe(name, description, fn, duration, duration_range, params, tags, type, version, presets or {}))
        return fn

    return decorate


def get_recipe(name: str) -> Recipe:
    key = name.strip().lower().replace("-", "_")
    if key not in RECIPES:
        raise InvalidInputError(f"Unknown recipe {name!r}", available=sorted(RECIPES))
    return RECIPES[key]


def preset_for(recipe_: Recipe, name: str) -> dict[str, Any]:
    key = name.strip().lower()
    base = PRESETS.get(key)
    local = recipe_.presets.get(key)
    if base is None and local is None:
        raise InvalidInputError(f"Unknown preset {name!r}", available=sorted(set(PRESETS) | set(recipe_.presets)))
    merged = {"params": {}, "effects": []}
    for part in (base, local):
        if part:
            merged["params"] |= part.get("params", {})
            merged["effects"] = part.get("effects", merged["effects"])
    return merged


def resolve_params(
    recipe_: Recipe,
    *,
    preset: str | None = None,
    overrides: dict[str, Any] | None = None,
    base: dict[str, Any] | None = None,
) -> tuple[dict[str, ParamValue], list[dict[str, Any]]]:
    """Defaults (or `base`, e.g. a previous sound's params) < preset < explicit overrides.

    Returns (params, preset effects).
    """
    params: dict[str, ParamValue] = {p.name: p.default for p in recipe_.all_params()}
    for key, value in (base or {}).items():
        if (p := recipe_.param(key)) is not None:
            params[key] = p.coerce(value)
    effects: list[dict[str, Any]] = []
    if preset:
        chosen = preset_for(recipe_, preset)
        for key, value in chosen["params"].items():
            p = recipe_.param(key)
            if p is None:
                continue
            if p.kind == "choice" and str(value) not in p.choices:
                continue
            params[key] = p.coerce(value)
        effects = list(chosen["effects"])
    for key, value in (overrides or {}).items():
        p = recipe_.param(key)
        if p is None:
            raise InvalidInputError(
                f"Recipe {recipe_.name} has no parameter {key!r}", allowed=[q.name for q in recipe_.all_params()]
            )
        params[key] = p.coerce(value)
    return params, effects


def vary_params(
    recipe_: Recipe, base: dict[str, ParamValue], variation: float, rng: np.random.Generator
) -> dict[str, ParamValue]:
    """Nudge numeric parameters within their ranges; `variation` 0 returns `base` unchanged."""
    varied = dict(base)
    for p in recipe_.all_params():
        if p.kind == "choice" or p.jitter <= 0 or variation <= 0:
            continue
        assert p.min is not None and p.max is not None
        delta = rng.uniform(-1.0, 1.0) * variation * p.jitter * (p.max - p.min)
        value = float(np.clip(float(base[p.name]) + delta, p.min, p.max))
        varied[p.name] = int(round(value)) if p.kind == "int" else round(value, 6)
    return varied


def render_recipe(recipe_: Recipe, params: dict[str, ParamValue], sr: int, seed: int) -> np.ndarray:
    ctx = RenderContext(
        sr=sr,
        duration=float(params["duration"]),
        pitch=float(semitones(float(params["pitch"]))),
        rng=np.random.default_rng(seed),
    )
    return np.asarray(recipe_.render(ctx, params), dtype=np.float64)
