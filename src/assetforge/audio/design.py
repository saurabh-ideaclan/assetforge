"""Sound design without new code: synth patches and layered designs, written as data by an agent.

A **patch** describes one synthesized voice: oscillators (sine/triangle/square/pulse/saw, noise colours,
a plucked string, FM), a pitch envelope, an amplitude envelope, a filter with its own cutoff
envelope and resonance, vibrato/tremolo, drive and bit-crush.

A **design** stacks layers. Each layer is a built-in recipe (with preset and params) or a patch, placed at a
start time with gain, pan, pitch, repeats and its own effects; the bus adds effects, an acoustic
space and stereo width. A laser charge + impact + debris tail is three layers.

```json
{"layers": [
  {"recipe": "charge_up", "params": {"duration": 0.6}},
  {"recipe": "explosion", "start": 0.55, "gain_db": -3, "preset": "heavy"},
  {"patch": {"duration": 1.2, "oscillators": [{"wave": "noise_brown"}],
             "filter": {"type": "lowpass", "cutoff": 900, "envelope": [[0, 2000], [1.2, 200]]}},
   "start": 0.6, "gain_db": -8, "pan": 0.3}
], "space": "cave"}
```

Variation: take 1 is the design as written; later takes nudge layer timing, gain, pitch and recipe
params by `variation` and use fresh noise.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assetforge.audio import dsp, pipeline
from assetforge.audio import synth as s
from assetforge.audio.effects import apply_effects, resolve_effects
from assetforge.audio.recipes import get_recipe, render_recipe, resolve_params, vary_params

ENGINE = "assetforge-design"
ENGINE_VERSION = 1
Wave = Literal["sine", "triangle", "square", "pulse", "saw", "noise_white", "noise_pink", "noise_brown", "pluck", "fm"]
Points = list[tuple[float, float]]


class Oscillator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wave: Wave = "sine"
    freq: float = Field(440.0, gt=0, le=20000, description="Hz (ignored for noise)")
    note: str | int | None = Field(None, description="Instead of freq: C4, F#3 or a MIDI number")
    level: float = Field(1.0, ge=0, le=4)
    detune: float = Field(0.0, ge=-1200, le=1200, description="Cents")
    duty: float = Field(0.5, gt=0.02, lt=0.98, description="Pulse width (pulse/square)")
    fm_ratio: float = Field(2.0, gt=0, le=16, description="FM modulator ratio")
    fm_index: float = Field(3.0, ge=0, le=40, description="FM depth")
    unison: int = Field(1, ge=1, le=7, description="Stacked detuned copies")
    spread: float = Field(12.0, ge=0, le=100, description="Unison spread in cents")
    decay: float = Field(0.996, gt=0.9, lt=1.0, description="Pluck sustain")

    def hz(self) -> float:
        if self.note is None:
            return self.freq
        from assetforge.audio.music.theory import note_number

        return float(s.note_hz(note_number(self.note)))


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attack: float = Field(0.005, ge=0, le=10)
    decay: float = Field(0.1, ge=0, le=10)
    sustain: float = Field(0.7, ge=0, le=1)
    release: float = Field(0.15, ge=0, le=20)
    points: Points = Field(default_factory=list, description="[[seconds, level 0..1], …] instead of ADSR")


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["lowpass", "highpass", "bandpass", "notch"] = "lowpass"
    cutoff: float = Field(2000.0, ge=20, le=20000)
    resonance: float = Field(0.707, ge=0.3, le=20, description="Q; above ~4 it rings")
    envelope: Points = Field(default_factory=list, description="[[seconds, cutoff Hz], …]")


class Modulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate: float = Field(5.0, gt=0, le=60, description="Hz")
    depth: float = Field(0.3, ge=0, le=24, description="Semitones (vibrato) or 0..1 (tremolo)")


class Patch(BaseModel):
    """One synthesized voice."""

    model_config = ConfigDict(extra="forbid")

    duration: float = Field(0.5, gt=0.005, le=30)
    oscillators: list[Oscillator] = Field(default_factory=lambda: [Oscillator()], min_length=1, max_length=8)
    pitch: Points = Field(default_factory=list, description="Pitch envelope [[seconds, semitones], …]")
    amp: Envelope = Field(default_factory=Envelope)
    filter: Filter | None = None
    vibrato: Modulation | None = None
    tremolo: Modulation | None = None
    drive: float = Field(0.0, ge=0, le=1)
    bits: int | None = Field(None, ge=2, le=16, description="Bit-crush depth")


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe: str | None = Field(None, description="A built-in recipe (see `audio recipes`)")
    preset: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    patch: Patch | None = None
    start: float = Field(0.0, ge=0, le=60, description="Seconds from the start")
    gain_db: float = Field(0.0, ge=-60, le=24)
    pan: float = Field(0.0, ge=-1, le=1)
    pitch: float = Field(0.0, ge=-36, le=36, description="Semitones")
    repeat: int = Field(1, ge=1, le=64, description="Play this layer N times…")
    interval: float = Field(0.1, gt=0, le=10, description="…this many seconds apart")
    effects: list[dict[str, Any] | str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_source(self) -> Layer:
        if (self.recipe is None) == (self.patch is None):
            raise ValueError("each layer needs exactly one of `recipe` or `patch`")
        return self


class Design(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: list[Layer] = Field(min_length=1, max_length=24)
    effects: list[dict[str, Any] | str] = Field(default_factory=list, description="Bus effects")
    space: str | None = Field(None, description="Acoustic space (see `sound spaces`)")
    width: float = Field(1.0, ge=0, le=2, description="Stereo width")
    variation: float = Field(0.3, ge=0, le=1)

    @field_validator("space")
    @classmethod
    def _space(cls, value: str | None) -> str | None:
        from assetforge.sound import SPACES

        if value is not None and value not in SPACES:
            raise ValueError(f"space must be one of {', '.join(SPACES)}")
        return value


# ---------------------------------------------------------------------- patch rendering


def _curve(points: Points, n: int, sr: int, default: float, *, log: bool = False) -> np.ndarray:
    if not points:
        return np.full(n, default)
    times = np.array([p[0] for p in points]) * sr
    values = np.array([p[1] for p in points], dtype=float)
    order = np.argsort(times, kind="stable")
    times, values = times[order], values[order]
    x = np.arange(n)
    if log:
        return np.exp(np.interp(x, times, np.log(np.maximum(values, 1e-3))))
    return np.interp(x, times, values)


def _voice(o: Oscillator, freq: np.ndarray, n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    if o.wave.startswith("noise_"):
        return s.noise(n, rng, o.wave.split("_")[1])  # type: ignore[arg-type]
    if o.wave == "pluck":
        return dsp.karplus(float(freq[0]), n, sr, rng, decay=o.decay)
    out = np.zeros(n)
    for k in range(o.unison):
        cents = o.detune + (0.0 if o.unison == 1 else o.spread * (2 * k / (o.unison - 1) - 1))
        f = freq * 2 ** (cents / 1200)
        if o.wave == "fm":
            voice = dsp.fm(f, o.fm_ratio, o.fm_index, n, sr)
        else:
            shape = "square" if o.wave == "pulse" else o.wave
            voice = s.osc(shape, f, n, sr, duty=o.duty, phase=float(rng.uniform()) if o.unison > 1 else 0.0)  # type: ignore[arg-type]
        out += voice
    return out / np.sqrt(o.unison)


def render_patch(patch: Patch, sr: int, rng: np.random.Generator, *, pitch: float = 0.0) -> np.ndarray:
    n = max(1, int(patch.duration * sr))
    semis = _curve(patch.pitch, n, sr, 0.0) + pitch
    if patch.vibrato:
        semis = semis + patch.vibrato.depth * np.sin(2 * np.pi * patch.vibrato.rate * np.arange(n) / sr)
    ratio = 2 ** (semis / 12)
    x = sum(o.level * _voice(o, o.hz() * ratio, n, sr, rng) for o in patch.oscillators) / len(patch.oscillators)
    if patch.filter:
        cutoff = _curve(patch.filter.envelope, n, sr, patch.filter.cutoff, log=True)
        x = dsp.sweep_filter(x, patch.filter.type, cutoff, sr, q=patch.filter.resonance)
    a = patch.amp
    env = _curve(a.points, n, sr, 1.0) if a.points else s.adsr(n, sr, attack=a.attack, decay=a.decay,
                                                                sustain=a.sustain, release=a.release)  # fmt: skip
    x = x * env
    if patch.tremolo:
        x = x * (1 - patch.tremolo.depth * 0.5 * (1 + np.sin(2 * np.pi * patch.tremolo.rate * np.arange(n) / sr)))
    if patch.drive:
        x = s.soft_clip(x / max(np.max(np.abs(x)), 1e-9), patch.drive)
    if patch.bits:
        steps = 2 ** (patch.bits - 1)
        x = np.round(x * steps) / steps
    return x


# ---------------------------------------------------------------------- design rendering


def _layer_signal(layer: Layer, sr: int, rng: np.random.Generator, variation: float, varied: bool) -> np.ndarray:
    pitch = layer.pitch + (rng.uniform(-1, 1) * variation if varied else 0.0)
    if layer.patch is not None:
        x = render_patch(layer.patch, sr, rng, pitch=pitch)
    else:
        recipe = get_recipe(layer.recipe or "")
        params, preset_effects = resolve_params(recipe, preset=layer.preset, overrides=layer.params)
        if varied:
            params = vary_params(recipe, params, variation, rng)
        p = recipe.param("pitch")
        assert p is not None and p.min is not None and p.max is not None
        params["pitch"] = float(np.clip(float(params["pitch"]) + pitch, p.min, p.max))
        from assetforge.audio.generate import apply_brightness

        x = apply_brightness(render_recipe(recipe, params, sr, int(rng.integers(2**31))), float(params["brightness"]), sr)
        if preset_effects and not layer.effects:
            x = apply_effects(x, resolve_effects(preset_effects), sr)
    if layer.effects:
        x = apply_effects(x, resolve_effects(layer.effects), sr)
    return x


def render(spec: dict[str, Any], index: int, seed: int, sr: int) -> pipeline.Take:
    design = Design(**spec)
    rng = np.random.default_rng(seed)
    varied = index > 1 and design.variation > 0
    placed: list[tuple[int, np.ndarray]] = []
    for layer in design.layers:
        gain = dsp.db(layer.gain_db + (rng.uniform(-1.5, 1.5) * design.variation if varied else 0.0))
        for k in range(layer.repeat):
            jitter = rng.uniform(-0.01, 0.01) * design.variation if varied else 0.0
            start = max(0, int((layer.start + k * layer.interval + jitter) * sr))
            x = _layer_signal(layer, sr, rng, design.variation, varied) * gain
            placed.append((start, dsp.pan(x, layer.pan)))
    out = np.zeros((max(start + len(x) for start, x in placed), 2))
    for start, x in placed:
        dsp.add_at(out, x, start)
    from assetforge.sound import SPACES

    bus = [*design.effects, *(SPACES[design.space] if design.space else [])]
    if bus:
        out = apply_effects(out, resolve_effects(bus), sr)
    return pipeline.Take(dsp.width(out, design.width), info={"layers": len(design.layers)})


ENGINE_REF = pipeline.register_engine(ENGINE, ENGINE_VERSION, render, job_kind="audio_design")


def validate(spec: dict[str, Any]) -> dict[str, Any]:
    """Check a design (recipes, presets, params and effects all exist) and return it with defaults filled in."""
    from assetforge.errors import InvalidInputError

    try:
        design = Design(**spec)
    except ValueError as exc:
        raise InvalidInputError(f"Invalid design: {exc}") from exc
    for layer in design.layers:
        if layer.recipe:
            resolve_params(get_recipe(layer.recipe), preset=layer.preset, overrides=layer.params)
        resolve_effects(layer.effects)
    resolve_effects(design.effects)
    return design.model_dump(mode="json", exclude_defaults=True)


def design_from_patch(patch: dict[str, Any]) -> dict[str, Any]:
    return {"layers": [{"patch": patch}]}


class DesignRequest(pipeline.Common):
    """`audio design --file`: a design (or a single `patch`) plus catalog fields."""

    design: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    type: Literal["sfx", "ui", "effect"] = "sfx"


def _default_name(spec: dict[str, Any]) -> str:
    names = [layer.get("recipe") or "synth" for layer in spec["layers"]]
    return "_".join(dict.fromkeys(names))[:40] + "_design"


def generate_design(lib: Any, req: DesignRequest) -> dict[str, Any]:
    from assetforge import sound
    from assetforge.core.models import AssetType
    from assetforge.errors import InvalidInputError

    if (req.design is None) == (req.patch is None):
        raise InvalidInputError("Give exactly one of `design` or `patch`")
    spec = validate(req.design if req.design is not None else design_from_patch(req.patch or {}))
    direction, _ = sound.resolve(lib, req.project, use_profile=req.use_profile)
    if "space" not in spec and direction.space and direction.space != "dry":
        spec["space"] = direction.space
    recipes = [layer["recipe"] for layer in spec["layers"] if layer.get("recipe")]
    out = pipeline.Output(
        engine=ENGINE_REF, spec=spec, kind="sfx", asset_type=AssetType(req.type), name=req.name or _default_name(spec),
        description=req.description or ("Layered sound: " + " + ".join(recipes or ["synth patch"])),
        target_lufs=sound.loudness_target(direction, "sfx"), sample_rate=req.sample_rate or direction.sample_rate or 44100,
        format=req.format or direction.format or "ogg", tags=[*recipes, "designed", *req.tags], **req.catalog(),
    )  # fmt: skip
    return pipeline.produce(lib, out, variants=req.variants, seed=req.seed, new_take=req.new_take)
