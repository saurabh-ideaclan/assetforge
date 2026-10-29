"""Layered creature voices (`assetforge-creature`): the sound designer's recipe, run by code.

A game monster is never one recording. Each take picks clips from the installed creature packs
(`creatures/pack.py`) by role and stacks them the way studios do:

- **identity**: the growl, roar, grunt, attack, hurt or death that says what the creature is;
- **body**: a deep clip pitched further down under it, for size and weight;
- **grit**: a bright, aggressive clip high-passed on top, louder as `aggression` rises;
- **breath**: an intake before roars and a rasp after deaths, so it reads as alive.

Size sets the pitch: clips are slowed (varispeed), which lowers pitch and formants together, so the
same throat sounds bigger. Onsets are aligned, the bus gets parallel drive (aggression), compression
and the project's acoustic space. Every take draws a different blend from the same pools, so takes
of one request never sound alike, and the clips and shifts used are recorded in the take's info.
Specs store the pack versions, so a take only re-renders from the same recordings.
"""

from __future__ import annotations

import re
import zlib
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pedalboard as pb
from pydantic import BaseModel, ConfigDict, Field
from scipy.signal import resample_poly

from assetforge.audio import dsp, pipeline
from assetforge.audio.creatures import pack
from assetforge.audio.effects import apply_effects, resolve_effects
from assetforge.errors import InvalidInputError

if TYPE_CHECKING:
    from pathlib import Path

    from assetforge.core.catalog import Library

ENGINE = "assetforge-creature"
ENGINE_VERSION = 1
Sound = Literal["growl", "roar", "attack", "grunt", "hurt", "death", "idle", "breath"]
SOUNDS: tuple[str, ...] = Sound.__args__  # type: ignore[attr-defined]

# Creature presets: body size 0..1 (pitch), default aggression, family of recordings, preferred clip tags.
CREATURES: dict[str, dict[str, Any]] = {
    "goblin": {"size": 0.2, "aggression": 0.6, "family": "beast", "description": "Small, nasty humanoid"},
    "imp": {"size": 0.1, "aggression": 0.7, "family": "beast", "prefer": ["demon"], "description": "Tiny devil"},
    "dog": {"size": 0.3, "aggression": 0.6, "family": "beast", "prefer": ["snarls", "snarl"], "description": "Hound or dog"},
    "wolf": {"size": 0.45, "aggression": 0.7, "family": "beast", "prefer": ["snarls", "snarl"], "description": "Wolf, warg"},
    "zombie": {"size": 0.5, "aggression": 0.3, "family": "beast", "prefer": ["yucky", "grunt"], "description": "Undead groaner"},
    "orc": {"size": 0.55, "aggression": 0.7, "family": "beast", "prefer": ["troll"], "description": "Orc, brute"},
    "beast": {"size": 0.65, "aggression": 0.6, "family": "beast", "description": "Generic monster (default)"},
    "bear": {"size": 0.7, "aggression": 0.6, "family": "beast", "prefer": ["growl"], "description": "Bear-like beast"},
    "demon": {"size": 0.75, "aggression": 0.8, "family": "beast", "prefer": ["demon"], "description": "Demon, fiend"},
    "troll": {"size": 0.8, "aggression": 0.6, "family": "beast", "prefer": ["troll"], "description": "Troll, ogre"},
    "giant": {"size": 0.9, "aggression": 0.5, "family": "beast", "description": "Giant, golem"},
    "dragon": {"size": 1.0, "aggression": 0.85, "family": "beast", "prefer": ["roar", "demon"], "description": "Dragon, kaiju"},
    "critter": {"size": 0.1, "aggression": 0.2, "family": "critter", "description": "Small cute creature"},
    "insect": {"size": 0.3, "aggression": 0.5, "family": "insect", "description": "Bug, spider, swarm"},
    "slime": {"size": 0.4, "aggression": 0.4, "family": "slime", "description": "Slime, blob, ooze"},
    "alien": {"size": 0.4, "aggression": 0.6, "family": "alien", "description": "Alien, strange thing"},
}  # fmt: skip

GRIT_MAX_CENTROID = 3500  # brighter attack clips are screeches that read as alien, not grit
LENGTHS = {"growl": 1.4, "roar": 1.8, "attack": 0.7, "grunt": 0.6, "hurt": 0.6, "death": 1.4, "idle": 1.2, "breath": 1.0}
# Per sound: identity roles (in preference order), whether a body/grit/intake/tail layer is wanted.
PLANS: dict[str, dict[str, Any]] = {
    "growl": {"identity": ("growl", "grunt"), "body": True, "grit": 0.5, "intake": False, "tail": False},
    "roar": {"identity": ("roar", "growl"), "body": True, "grit": 0.35, "intake": True, "tail": False},
    "attack": {"identity": ("attack", "growl", "roar"), "body": True, "grit": 0.3, "intake": False, "tail": False},
    "grunt": {"identity": ("grunt", "growl"), "body": True, "grit": 1.1, "intake": False, "tail": False},
    "hurt": {"identity": ("hurt", "grunt", "attack"), "body": False, "grit": 1.1, "intake": False, "tail": False},
    "death": {"identity": ("death", "hurt", "roar"), "body": True, "grit": 1.1, "intake": False, "tail": True},
    "idle": {"identity": ("grunt", "breath", "growl"), "body": False, "grit": 1.1, "intake": False, "tail": False},
    "breath": {"identity": ("breath",), "body": False, "grit": 1.1, "intake": False, "tail": False},
}


class CreatureSpec(BaseModel):
    """Fully resolved; stored with the asset and fingerprinted."""

    model_config = ConfigDict(extra="forbid")

    creature: str = "beast"
    sound: Sound = "growl"
    family: str = "beast"
    size: float = Field(0.65, ge=0, le=1)
    aggression: float = Field(0.6, ge=0, le=1)
    duration: float | None = Field(None, ge=0.2, le=8, description="Stretch the main layer towards this length")
    prefer: list[str] = Field(default_factory=list)
    space: str = "dry"
    packs: dict[str, str] = Field(default_factory=dict, description="Pack name → version the clips come from")


# ---------------------------------------------------------------------- clips


def _pool(clips: list[dict[str, Any]], roles: tuple[str, ...], family: str) -> list[dict[str, Any]]:
    for role in roles:
        found = [c for c in clips if role in c["roles"] and c["family"] == family]
        if found:
            return found
    return [c for c in clips if c["family"] == family and "voice" in c["roles"]]


def _pick(rng: np.random.Generator, pool: list[dict[str, Any]], prefer: list[str], *, avoid: set[str] = frozenset(),
          weight_by: str | None = None, low: bool = False) -> dict[str, Any] | None:  # fmt: skip
    choices = [c for c in pool if c["id"] not in avoid] or pool
    preferred = [c for c in choices if set(prefer) & set(c["tags"])]
    if len(preferred) >= 2:  # a wolf always snarls: a preset's own kind of voice, when there is enough of it
        choices = preferred
    if not choices:
        return None
    w = np.ones(len(choices))
    if low:  # body layers: the deeper the better
        w *= np.array([0.2 + c["low_share"] for c in choices])
    return choices[int(rng.choice(len(choices), p=w / w.sum()))]


def _varispeed(x: np.ndarray, semitones: float) -> np.ndarray:
    """Slow down / speed up: pitch and formants move together (a bigger or smaller throat)."""
    if abs(semitones) < 0.05:
        return x
    ratio = Fraction(2 ** (-semitones / 12)).limit_denominator(64)
    return resample_poly(x, ratio.numerator, ratio.denominator)


def _onset(x: np.ndarray, sr: int) -> int:
    k = max(1, int(0.01 * sr))
    env = np.sqrt(np.convolve(x**2, np.ones(k) / k, "same"))
    above = np.nonzero(env > env.max() * 0.1)[0]
    return int(above[0]) if len(above) else 0


def _rms(x: np.ndarray) -> np.ndarray:
    r = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    return x / r if r > 0 else x


def _fade(x: np.ndarray, sr: int, head: float = 0.004, tail: float = 0.03) -> np.ndarray:
    x = x.copy()
    h, t = min(len(x), int(head * sr)), min(len(x), int(tail * sr))
    if h:
        x[:h] *= np.linspace(0, 1, h)
    if t:
        x[-t:] *= np.cos(np.linspace(0, np.pi / 2, t)) ** 2
    return x


def size_shift(centroid: float, size: float) -> float:
    """Semitones that move a clip towards the brightness of a creature of `size` (0 tiny … 1 colossal).

    Only part of the way (60%), so each recording keeps its own character."""
    target = 1400 * 2 ** (-3.2 * size)  # ~1.4 kHz for a critter, ~150 Hz for a dragon
    return float(np.clip(0.6 * 12 * np.log2(target / max(centroid, 60.0)), -12.0, 7.0))


# ---------------------------------------------------------------------- render


def _clips_for(spec: CreatureSpec, home: Path) -> list[dict[str, Any]]:
    installed = pack.installed_packs(home)
    missing = {n: v for n, v in spec.packs.items() if installed.get(n) != v}
    if missing or not spec.packs:
        raise InvalidInputError("The creature recordings this sound was made from are not installed",
                                missing=sorted(missing), hint="Run `assetforge creature install --yes`")  # fmt: skip
    return sorted((c for c in pack.load_index(home)["clips"] if c["pack"] in spec.packs), key=lambda c: c["id"])


def render(spec_data: dict[str, Any], index: int, seed: int, sr: int) -> pipeline.Take:
    from assetforge.config import resolve_home
    from assetforge.sound import SPACES

    spec = CreatureSpec(**spec_data)
    home = pipeline.active_home.get() or resolve_home()
    clips = _clips_for(spec, home)
    rng = np.random.default_rng([seed, 7, zlib.crc32(f"{spec.creature}/{spec.sound}".encode())])  # each creature its own picks
    plan = PLANS[spec.sound]
    used: list[dict[str, Any]] = []
    layers: list[tuple[np.ndarray, int, float]] = []  # (signal, onset offset in samples from the hit, gain)

    def add(role: str, clip: dict[str, Any], shift: float, gain_db: float, offset: float, *, fit: float | None = None,
            process: Any = None, reverse: bool = False) -> np.ndarray:  # fmt: skip
        """Place a clip; `fit` (seconds) time-stretches it towards that length (at most ×1.5), then crops it."""
        x = pack.read_clip(home, clip, sr)
        if reverse:
            x = x[::-1]
        x = _varispeed(x, shift)
        if fit is not None:
            stretch = float(np.clip(len(x) / sr / fit, 0.67, 1.5))
            if abs(stretch - 1) > 0.05:
                x = pb.time_stretch(x.astype(np.float32), sr, stretch_factor=stretch)[0].astype(np.float64)
            x = x[: int(fit * 1.1 * sr)]
        if process is not None:
            x = process(x)
        x = _fade(x, sr, tail=0.03 if fit is None else min(0.25, len(x) / sr * 0.3))
        layers.append((_rms(x) * dsp.db(gain_db), int(offset * sr) - _onset(x, sr), gain_db))
        used.append({"role": role, "clip": clip["id"], "semitones": round(shift, 2), "gain_db": round(gain_db, 1),
                     "offset_s": round(offset, 3)})  # fmt: skip
        return x

    identity = _pick(rng, _pool(clips, plan["identity"], spec.family), spec.prefer)
    if identity is None:
        raise InvalidInputError(f"No {spec.family} recordings for a {spec.sound}", hint="See `assetforge creature status`")
    shift = size_shift(identity["centroid"], spec.size) + rng.uniform(-1.0, 1.0)
    target = spec.duration or LENGTHS[spec.sound] * (0.8 + 0.5 * spec.size) * rng.uniform(0.85, 1.15)
    main = add("identity", identity, shift, 0.0, 0.0, fit=target)
    span = len(main) / sr

    if spec.family == "beast" and len(main) / sr > 0.25 and rng.uniform() < 0.6 and spec.sound in ("growl", "roar"):
        second = _pick(rng, _pool(clips, plan["identity"], spec.family), spec.prefer, avoid={identity["id"]})
        if second is not None:  # a second voice of the same kind doubles it: bigger, less recognisable
            s2 = size_shift(second["centroid"], spec.size) + rng.uniform(-2.0, 0.5)
            add("double", second, s2, -4.0 - rng.uniform(0, 3), rng.uniform(0.0, 0.03), fit=span)

    beastly = spec.family == "beast" or spec.size >= 0.7  # small aliens, bugs and slimes stay themselves
    if plan["body"] and spec.size >= 0.35 and beastly:
        body = _pick(rng, _pool(clips, ("body",), "beast"), [], low=True)
        if body is not None:
            bshift = float(np.clip(size_shift(body["centroid"], spec.size) - rng.uniform(2, 6), -12, 0))
            gain = -8 + 6 * spec.size + rng.uniform(-1.5, 1.5)
            add("body", body, bshift, gain, -rng.uniform(0.0, 0.02), fit=span,
                process=lambda x: dsp.filt(dsp.filt(x, "lowpass", 380, sr), "lowpass", 380, sr))  # fmt: skip

    if spec.aggression >= plan["grit"] and beastly:
        harsh = [c for c in _pool(clips, ("attack",), "beast") if c["centroid"] <= GRIT_MAX_CENTROID]
        grit = _pick(rng, harsh, spec.prefer, avoid={u["clip"] for u in used})
        if grit is not None:
            gshift = rng.uniform(-4, 1) - 4 * spec.size
            add("grit", grit, gshift, -12 + 9 * spec.aggression + rng.uniform(-1.5, 1.5), rng.uniform(0.0, 0.04), fit=span,
                process=lambda x: dsp.filt(dsp.filt(x, "highpass", 900, sr), "highpass", 900, sr))  # fmt: skip

    if plan["intake"]:
        breath = _pick(rng, _pool(clips, ("breath",), "beast"), [])
        if breath is not None:  # an inhale is a reversed exhale; it ends where the roar starts
            x = add("intake", breath, max(-12.0, size_shift(breath["centroid"], spec.size) - 3), -11.0, 0.0, reverse=True, fit=0.6)
            layers[-1] = (layers[-1][0], -len(x), layers[-1][2])
            used[-1]["offset_s"] = round(-len(x) / sr, 3)

    if plan["tail"]:
        breath = _pick(rng, _pool(clips, ("breath",), "beast"), [])
        if breath is not None:
            add("tail", breath, max(-12.0, size_shift(breath["centroid"], spec.size) - 2), -10.0, span * 0.85, fit=0.8)

    start = -min(offset for _, offset, _ in layers)
    total = start + max(offset + len(x) for x, offset, _ in layers)
    mix = np.zeros(total)
    for x, offset, _ in layers:
        dsp.add_at(mix, x, start + offset)
    mix = _bus(mix, spec, sr)
    space = resolve_effects(SPACES.get(spec.space, []))
    if space:
        mix = apply_effects(mix, space, sr)
    return pipeline.Take(mix, info={"layers": used})


def _bus(x: np.ndarray, spec: CreatureSpec, sr: int) -> np.ndarray:
    """Parallel drive for aggression, a weight shelf for size, gentle compression to glue the layers."""
    x = x / (np.max(np.abs(x)) + 1e-12)
    drive = pb.Pedalboard([pb.Distortion(drive_db=6 + 18 * spec.aggression), pb.LowpassFilter(cutoff_frequency_hz=7000)])
    wet = drive(x.astype(np.float32), sr).astype(np.float64)
    x = x + (0.1 + 0.4 * spec.aggression) * _rms(wet) * float(np.sqrt(np.mean(x**2)))
    glue = pb.Pedalboard([pb.LowShelfFilter(cutoff_frequency_hz=160, gain_db=2 + 4 * spec.size),
                          pb.Compressor(threshold_db=-18, ratio=3, attack_ms=8, release_ms=120),
                          pb.HighpassFilter(cutoff_frequency_hz=35)])  # fmt: skip
    return glue(x.astype(np.float32), sr).astype(np.float64)


ENGINE_REF = pipeline.register_engine(ENGINE, ENGINE_VERSION, render, job_kind="audio_creature")


# ---------------------------------------------------------------------- requests


class CreatureRequest(pipeline.Common):
    creature: str = Field("beast", description="A preset (see `creature list`) or any name with your own size")
    sound: Sound = Field("growl", description="growl, roar, attack, grunt, hurt, death, idle or breath")
    size: float | None = Field(None, ge=0, le=1, description="0 tiny … 1 colossal (default: the creature's)")
    aggression: float | None = Field(None, ge=0, le=1, description="0 calm … 1 furious: grit and drive")
    duration: float | None = Field(None, ge=0.2, le=8, description="Approximate length of the main voice")
    family: str | None = Field(None, description="beast, alien, insect, slime or critter (default: the creature's)")
    space: str | None = Field(None, description="Acoustic space (default: the project's)")


def resolve_spec(lib: Library, req: CreatureRequest, direction_space: str | None = None) -> CreatureSpec:
    from assetforge.sound import SPACES

    key = req.creature.strip().lower().replace(" ", "_")
    preset = CREATURES.get(key, CREATURES["beast"])
    family = req.family or preset["family"]
    if family not in pack.FAMILIES:
        raise InvalidInputError(f"Unknown creature family {family!r}", available=list(pack.FAMILIES))
    packs = pack.installed_packs(lib.paths.home)
    if not packs:
        raise InvalidInputError("Creature voices need the creature recordings",
                                hint="Run `assetforge creature install --yes` (about 12 MB of CC0 recordings)")  # fmt: skip
    space = req.space or direction_space or "dry"
    if space not in SPACES:
        raise InvalidInputError(f"Unknown space {space!r}", available=sorted(SPACES))
    return CreatureSpec(creature=key, sound=req.sound, family=family,
                        size=req.size if req.size is not None else preset["size"],
                        aggression=req.aggression if req.aggression is not None else preset["aggression"],
                        duration=req.duration, prefer=list(preset.get("prefer", [])), space=space, packs=packs)  # fmt: skip


@pipeline.for_library
def generate_creature(lib: Library, req: CreatureRequest) -> dict[str, Any]:
    from assetforge import sound
    from assetforge.core.models import AssetType

    direction, _ = sound.resolve(lib, req.project, use_profile=req.use_profile)
    spec = resolve_spec(lib, req, direction.space)
    name = req.name or f"{spec.creature}_{spec.sound}"
    credits = [lib_pack["title"] + " by " + lib_pack["author"] for n in spec.packs
               if (lib_pack := pack.load_index(lib.paths.home)["packs"].get(n))]  # fmt: skip
    out = pipeline.Output(
        engine=ENGINE_REF, spec=spec.model_dump(mode="json"), kind="sfx", asset_type=AssetType.SFX, name=name,
        description=req.description or f"{spec.creature.replace('_', ' ').title()} {spec.sound} (layered creature voice)",
        target_lufs=sound.loudness_target(direction, "sfx"), sample_rate=req.sample_rate or direction.sample_rate or 44100,
        format=req.format or direction.format or "ogg",
        tags=["creature", "monster", spec.creature, spec.sound, spec.family, *req.tags],
        audio_meta={"creature": spec.creature, "sound": spec.sound, "size": spec.size, "aggression": spec.aggression},
        extra_metadata={"sources": {"license": pack.LICENSE, "packs": credits}}, **req.catalog(),
    )  # fmt: skip
    return pipeline.produce(lib, out, variants=req.variants, seed=req.seed, new_take=req.new_take)


def catalog() -> dict[str, Any]:
    return {"creatures": {k: {"size": v["size"], "aggression": v["aggression"], "family": v["family"],
                              "description": v["description"]} for k, v in CREATURES.items()},
            "sounds": list(SOUNDS), "families": list(pack.FAMILIES)}  # fmt: skip


# ---------------------------------------------------------------------- descriptions

CREATURE_WORDS = {
    "goblin": ("goblin", "kobold", "gremlin", "gnoll"), "imp": ("imp", "devilkin"), "dog": ("dog", "hound", "puppy", "mutt"),
    "wolf": ("wolf", "wolves", "warg", "werewolf", "coyote"), "zombie": ("zombie", "ghoul", "undead", "walker", "corpse"),
    "orc": ("orc", "brute", "barbarian"), "beast": ("beast", "monster", "creature", "animal", "mutant"),
    "bear": ("bear", "grizzly"), "demon": ("demon", "devil", "fiend", "hellhound"), "troll": ("troll", "ogre", "cyclops"),
    "giant": ("giant", "golem", "titan", "colossus"), "dragon": ("dragon", "wyvern", "kaiju", "dinosaur", "rex", "behemoth"),
    "critter": ("critter", "pet", "hamster", "cute"), "insect": ("insect", "bug", "spider", "beetle", "swarm", "wasp", "ant"),
    "slime": ("slime", "blob", "ooze", "jelly"), "alien": ("alien", "xeno", "extraterrestrial"),
}  # fmt: skip
VOCAL = {"growl": ("growl", "growls", "growling", "snarl", "snarls", "snarling"),
         "roar": ("roar", "roars", "roaring", "howl", "howls", "bellow", "screech", "call")}  # fmt: skip
NEEDS_CREATURE = {"attack": ("attack", "bite", "lunge", "swipe"), "hurt": ("hurt", "pain", "injured", "damage", "hit"),
                  "death": ("death", "die", "dies", "dying", "killed"), "grunt": ("grunt", "grunts", "groan", "moan"),
                  "breath": ("breath", "breathing", "sniff", "snort", "pant"), "idle": ("idle", "ambient", "lurking")}  # fmt: skip


def creature_from_words(description: str) -> dict[str, Any] | None:
    """{"creature", "sound"} when a description asks for a creature's voice, else None.

    A growl/roar/snarl/howl is always a creature voice; attack, hurt, death, grunt or breath only when a
    creature is named ("orc death", not "player hit")."""
    words = set(re.findall(r"[a-z]+", description.lower()))
    creature = next((name for name, options in CREATURE_WORDS.items() if words & set(options)), None)
    sound = next((s for s, options in VOCAL.items() if words & set(options)), None)
    if sound is None and creature is not None:
        sound = next((s for s, options in NEEDS_CREATURE.items() if words & set(options)), None)
    if sound is None:
        return None
    out: dict[str, Any] = {"creature": creature or "beast", "sound": sound}
    for word, size in (("tiny", 0.1), ("small", 0.25), ("little", 0.25), ("big", 0.8), ("huge", 0.9), ("giant", 0.95),
                       ("massive", 0.95), ("colossal", 1.0)):  # fmt: skip
        if word in words and creature != "giant":
            out["size"] = size
    for word, value in (("calm", 0.2), ("sleepy", 0.15), ("angry", 0.85), ("furious", 0.95), ("aggressive", 0.85),
                        ("menacing", 0.7), ("enraged", 0.95)):  # fmt: skip
        if word in words:
            out["aggression"] = value
    return out
