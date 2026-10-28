"""Magic and powers: casts, charges, heals, buffs, shields, teleports, elements."""

from __future__ import annotations

import numpy as np

from assetforge.audio import dsp
from assetforge.audio import synth as s
from assetforge.audio.recipes.base import RenderContext, choice, num, recipe
from assetforge.audio.recipes.music import CHORDS, norm
from assetforge.audio.recipes.physical import clicks, fade_tail, flame, n_wave, rms_norm


def shimmer(ctx: RenderContext, n: int, base_hz: float, density: float, rise: float = 0.0) -> np.ndarray:
    """Sparkling cloud of short high sine grains."""
    out = np.zeros(n)
    sr = ctx.sr
    count = max(1, int(density * 40 * n / sr))
    for _ in range(count):
        start = int(ctx.rng.uniform(0, 0.9) * n)
        gn = min(n - start, int(ctx.rng.uniform(0.03, 0.12) * sr))
        if gn <= 4:
            continue
        f = base_hz * ctx.pitch * 2 ** (ctx.rng.integers(0, 3) + rise * start / n) * ctx.rng.choice([1, 1.25, 1.5])
        f = min(f, 7000.0)  # sparkle, not a whistle: keep grains out of the piercing top octave
        out[start : start + gn] += np.sin(2 * np.pi * f * np.arange(gn) / sr) * s.perc(gn, sr, attack=0.002, curve=3)
    return out


@recipe(
    "spell_cast",
    "Spell release: rising swell into a bright magical burst",
    duration=1.0,
    duration_range=(0.3, 3.0),
    params=(
        choice("element", "arcane", ("arcane", "fire", "ice", "nature", "dark", "holy"), "Flavour of magic"),
        num("power", 0.6, 0, 1, "Size of the burst"),
        num("sparkle", 0.5, 0, 1, "Glittering high grains"),
    ),
    tags=("spell", "cast", "magic", "wizard", "mage", "arcane", "sorcery", "ability", "skill"),
    version=2,
)
def spell_cast(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    if p["element"] == "fire":  # a fire spell is fire: a rushing flame that ignites and roars, then crackles out
        x = np.linspace(0, 1, n)
        env = np.where(x < 0.3, (x / 0.3) ** 1.5, np.exp(-(x - 0.3) * (3.5 - 1.5 * p["power"])))
        out = flame(ctx, n, 0.4 + 0.6 * p["power"], env)
        ignite = s.osc("sine", s.sweep(180, 60, n), n, sr) * np.exp(-np.maximum(x - 0.28, 0) * 14) * (x > 0.27)
        return fade_tail(rms_norm(out) + 1.2 * p["power"] * rms_norm(ignite) * 0.6 + p["sparkle"] * 0.1 * rms_norm(
            shimmer(ctx, n, 900, 0.6)), sr)  # fmt: skip
    x = np.linspace(0, 1, n)
    base = {"arcane": 330, "fire": 180, "ice": 520, "nature": 260, "dark": 110, "holy": 440}[p["element"]] * ctx.pitch
    swell_env = np.where(x < 0.4, (x / 0.4) ** 2, np.exp(-(x - 0.4) * 6))
    pad = sum(dsp.fm(base * r, 1.5 if p["element"] != "dark" else 0.5, 1.5 + 2 * p["power"], n, sr) for r in (1, 1.5, 2))
    noise_kind = {"fire": "pink", "ice": "white", "dark": "brown"}.get(p["element"], "pink")
    burst = s.filter_track(s.noise(n, ctx.rng, noise_kind), "bandpass", base * 4 * (1 + 3 * x), sr)
    burst *= np.exp(-np.maximum(x - 0.38, 0) * 8) * (x > 0.35)
    return norm(pad) * swell_env * 0.6 + p["power"] * norm(burst) * 0.7 + p["sparkle"] * 0.3 * shimmer(ctx, n, base * 4, 1.0)


@recipe(
    "charge_up",
    "Energy charging: rising whine with building pulses",
    duration=1.2,
    duration_range=(0.3, 4.0),
    params=(
        num("start_hz", 120, 40, 800, "Starting pitch"),
        num("rise", 24, 3, 48, "Pitch rise in semitones"),
        num("pulse", 0.5, 0, 1, "Throbbing amplitude pulses"),
    ),
    tags=("charge", "charging", "power", "energy", "build", "rise", "laser", "beam", "ultimate", "scifi"),
)
def charge_up(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = p["start_hz"] * ctx.pitch * s.semitones(p["rise"] * x**1.5)
    tone = s.osc("saw", f, n, sr) * 0.5 + s.osc("sine", f * 2, n, sr) * 0.5
    tone = dsp.sweep_filter(tone, "lowpass", 400 + 6000 * x, sr, q=2.0)
    rate = 4 + 26 * x
    trem = 1 - p["pulse"] * 0.5 * (1 + np.sin(2 * np.pi * np.cumsum(rate) / sr))
    return tone * trem * (0.2 + 0.8 * x)


@recipe(
    "heal",
    "Healing chime: warm rising arpeggio with soft sparkle",
    duration=1.0,
    duration_range=(0.3, 3.0),
    params=(num("warmth", 0.6, 0, 1, "Softer, rounder tone"), num("sparkle", 0.5, 0, 1, "High glitter")),
    tags=("heal", "healing", "restore", "health", "cure", "regen", "potion", "recover", "hp"),
)
def heal(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    out = np.zeros(n)
    notes = [72 + i for i in CHORDS["major"][:5]]
    for k, note in enumerate(notes):
        start = int(k * n * 0.12)
        dn = n - start
        f = s.note_hz(note) * ctx.pitch
        voice = dsp.fm(f, 2.0, 1.2 * (1 - p["warmth"]) + 0.3, dn, sr) * s.adsr(dn, sr, attack=0.01, decay=0.2,
                                                                                sustain=0.4, release=dn / sr * 0.6)  # fmt: skip
        out[start:] += voice / (1 + k * 0.2)
    return norm(out) * 0.8 + p["sparkle"] * 0.25 * shimmer(ctx, n, 1400, 1.2, rise=1)


@recipe(
    "buff",
    "Positive status effect: bright upward shimmer",
    duration=0.7,
    duration_range=(0.2, 2.0),
    params=(num("brightness_boost", 0.5, 0, 1, "Extra top end"),),
    tags=("buff", "boost", "status", "strength", "haste", "upgrade", "positive", "power"),
)
def buff(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = 400 * ctx.pitch * s.semitones(12 * x)
    tone = s.osc("triangle", f, n, sr) + 0.5 * s.osc("triangle", f * 1.5, n, sr)
    return norm(tone) * np.sin(np.pi * x) ** 0.7 + 0.3 * p["brightness_boost"] * shimmer(ctx, n, 1800, 1.5, rise=1)


@recipe(
    "debuff",
    "Negative status effect: sinking, wobbling dissonance",
    duration=0.8,
    duration_range=(0.2, 2.5),
    params=(num("wobble", 0.6, 0, 1, "Seasick pitch wobble"),),
    tags=("debuff", "curse", "poison", "slow", "weaken", "status", "negative", "hex"),
)
def debuff(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    f = 330 * ctx.pitch * s.semitones(-10 * x)
    f = s.vibrato(f, n, sr, rate_hz=6, depth_semitones=0.8 * p["wobble"])
    tone = s.osc("saw", f, n, sr) + s.osc("saw", f * 1.06, n, sr)
    return s.lowpass(tone, 2500, sr) * s.adsr(n, sr, attack=0.02, decay=0.1, sustain=0.7, release=n / sr * 0.4)


@recipe(
    "shield_up",
    "Energy shield or barrier forming: resonant hum with a bright onset",
    duration=0.9,
    duration_range=(0.3, 3.0),
    params=(num("hum_hz", 110, 50, 400, "Body hum pitch"), num("shimmer", 0.5, 0, 1, "Surface shimmer")),
    tags=("shield", "barrier", "protect", "force", "field", "bubble", "defense", "armor", "scifi", "magic"),
)
def shield_up(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    t = np.arange(n) / sr
    hum = sum(s.osc("saw", p["hum_hz"] * ctx.pitch * k * (1 + 0.003 * k), n, sr) / k for k in (1, 2, 3))
    hum = dsp.filt(hum, "lowpass", 1400, sr, q=3)
    env = s.adsr(n, sr, attack=0.08, decay=0.2, sustain=0.6, release=n / sr * 0.4)
    onset = s.place(norm(s.highpass(s.noise(int(0.08 * sr), ctx.rng), 3000, sr)) * s.perc(int(0.08 * sr), sr), n, 0)
    return norm(hum) * env * (1 + 0.2 * np.sin(2 * np.pi * 7 * t)) + 0.4 * onset + p["shimmer"] * 0.2 * shimmer(ctx, n, 2000, 0.8)


@recipe(
    "teleport",
    "Teleport or warp: fast sweep through a phasing whoosh",
    duration=0.8,
    duration_range=(0.2, 2.5),
    params=(choice("direction", "out", ("out", "in"), "Vanishing (rising) or arriving (falling)"),
            num("sweep", 36, 6, 60, "Sweep size in semitones")),  # fmt: skip
    tags=("teleport", "warp", "blink", "portal", "vanish", "appear", "phase", "dash", "scifi", "magic"),
)
def teleport(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    x = np.linspace(0, 1, n)
    curve = x if p["direction"] == "out" else 1 - x
    f = 150 * ctx.pitch * s.semitones(p["sweep"] * curve)
    tone = s.osc("sine", f, n, sr) + 0.5 * s.osc("sine", f * 1.01, n, sr)
    air = s.filter_track(s.noise(n, ctx.rng), "bandpass", f * 4, sr)
    return (norm(tone) + 0.5 * norm(air)) * np.sin(np.pi * x)


@recipe(
    "sparkle",
    "Magical twinkle: a cloud of bright bell-like grains",
    duration=0.8,
    duration_range=(0.15, 3.0),
    params=(num("density", 0.6, 0.1, 1, "Grains per second"), num("height", 1600, 400, 5000, "Base pitch")),
    tags=("sparkle", "twinkle", "glitter", "shine", "magic", "fairy", "star", "chime", "dust"),
)
def sparkle(ctx: RenderContext, p: dict) -> np.ndarray:
    return shimmer(ctx, ctx.n, p["height"], p["density"] * 2)


@recipe(
    "curse",
    "Dark magic: low growling drone with whispered noise",
    duration=1.4,
    duration_range=(0.4, 4.0),
    params=(num("depth", 0.7, 0, 1, "Lower, heavier drone"), num("whisper", 0.5, 0, 1, "Breathy noise")),
    tags=("curse", "dark", "evil", "necro", "shadow", "hex", "doom", "magic", "horror", "spell"),
)
def curse(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    f0 = (70 - 30 * p["depth"]) * ctx.pitch
    drone = sum(s.osc("saw", f0 * r, n, sr) for r in (1, 1.5, 1.06, 2.12))
    drone = dsp.sweep_filter(drone, "lowpass", 300 + 900 * np.linspace(0, 1, n) ** 0.5, sr, q=4)
    whisper = s.filter_track(s.noise(n, ctx.rng), "bandpass", 2500 + 1500 * dsp.drift(n, sr, 6, ctx.rng), sr)
    env = s.adsr(n, sr, attack=0.15, decay=0.2, sustain=0.8, release=n / sr * 0.35)
    return (norm(drone) + p["whisper"] * 0.5 * norm(whisper)) * env


@recipe(
    "freeze",
    "Ice forming: crackling crystal growth with a cold shimmer",
    duration=0.9,
    duration_range=(0.2, 3.0),
    params=(num("crackle", 0.6, 0, 1, "Crystal crackles"),),
    tags=("freeze", "ice", "frost", "cold", "crystal", "frozen", "snow", "magic", "spell"),
)
def freeze(ctx: RenderContext, p: dict) -> np.ndarray:
    n, sr = ctx.n, ctx.sr
    crackles = np.zeros(n)
    for _ in range(int(20 + 80 * p["crackle"])):
        i = int(ctx.rng.uniform(0, 0.85) * n)
        k = min(n - i, int(0.004 * sr))
        crackles[i : i + k] += ctx.rng.uniform(-1, 1, k) * np.linspace(1, 0, k)
    crackles = s.bandpass(crackles, 1800, 7000, sr)
    return norm(crackles) * 0.6 + 0.4 * shimmer(ctx, n, 1800, 1.2)


@recipe(
    "fire_burst",
    "Fireball or flame burst: whooshing roar with crackles",
    duration=1.0,
    duration_range=(0.25, 3.0),
    params=(num("size", 0.6, 0, 1, "Bigger flames are lower and longer"), num("crackle", 0.5, 0, 1, "Crackling embers")),
    tags=("fire", "fireball", "flame", "burn", "blaze", "ignite", "inferno", "magic", "spell", "explosion"),
    version=2,
)
def fire_burst(ctx: RenderContext, p: dict) -> np.ndarray:
    """Flames flaring up: a 0.1-0.2 s accelerating swell, a hold that grows with size (real flare-ups roar
    steadily within ±5 dB), then a ~0.4 s die-down, with extra snapping embers."""
    n, sr = ctx.n, ctx.sr
    size = p["size"]
    t = np.arange(n) / sr
    dur = n / sr
    att = min(0.12 + 0.08 * size, 0.3 * dur)
    hold = max(0.0, (0.1 + 0.6 * size) * dur - att)
    env = np.where(t < att, (t / att) ** 1.5, np.exp(-np.maximum(t - att - hold, 0) / (0.3 + 0.1 * size)))
    out = flame(ctx, n, size, env)
    embers = clicks(ctx, n, 15 + 30 * p["crackle"], hp=1500, lp=10000, envelope=np.clip(env * 3, 0, 1))
    return fade_tail(out + p["crackle"] * 0.15 * rms_norm(embers), sr)


@recipe(
    "lightning_zap",
    "Electric zap or lightning bolt: buzzing crackle",
    duration=0.5,
    duration_range=(0.08, 2.0),
    params=(num("buzz_hz", 90, 30, 400, "Mains-like buzz pitch"), num("crackle", 0.7, 0, 1, "Arcing crackle")),
    tags=("lightning", "zap", "electric", "shock", "spark", "thunderbolt", "tesla", "electricity", "magic"),
)
def lightning_zap(ctx: RenderContext, p: dict) -> np.ndarray:
    """Matched to recordings of electric sparks/arcs: a storm of discrete crackles, broadband (roughly flat from
    a few hundred Hz to 10 kHz) and very impulsive (peaks ~16 dB over the average), arriving in bursts as the
    arc strikes and breaks, over a faint mains buzz. Crackle, not hiss: every pop is a separate tiny grain."""
    n, sr, rng = ctx.n, ctx.sr, ctx.rng
    x = np.linspace(0, 1, n)
    env = np.minimum(1, x * 200) * (1 - x) ** 0.6
    # bursts: the arc strikes, holds a few tens of ms, breaks, re-strikes
    gate = np.zeros(n)
    t = 0
    while t < n:
        on = int(rng.uniform(0.02, 0.09) * sr)
        gate[t : t + on] = rng.uniform(0.5, 1.0)
        t += on + int(rng.uniform(0.005, 0.05) * sr * (1.3 - p["crackle"]))
    gate = dsp.one_pole_lowpass(gate, 400, sr)  # 1-2 ms edges: no click from the gate itself
    pops = np.zeros(n)
    count = rng.poisson((2500 + 6000 * p["crackle"]) * n / sr)
    where = rng.integers(0, n, count)
    np.add.at(pops, where, rng.pareto(2.2, count).clip(0, 6) * rng.choice([-1.0, 1.0], count))
    grain = np.hanning(max(3, int(0.0003 * sr)))
    crackle_ = np.convolve(pops * gate, grain, mode="same")
    crackle_ = dsp.filt(dsp.filt(crackle_, "highpass", 250, sr), "lowpass", 11000, sr)
    f = p["buzz_hz"] * ctx.pitch
    buzz = dsp.filt(sum(s.osc("saw", f * k, n, sr) / k for k in (1, 2, 3)), "bandpass", 600, sr, q=0.8) * gate
    crack = n_wave(ctx, n, ms=1.0, tone_hz=9000)
    peak = lambda v: v / (np.max(np.abs(v)) + 1e-12)  # noqa: E731
    out = peak(crackle_) * env + 0.12 * rms_norm(buzz) * env * np.max(np.abs(peak(crackle_))) / 3 + 0.25 * peak(crack)
    return fade_tail(peak(out), sr)
