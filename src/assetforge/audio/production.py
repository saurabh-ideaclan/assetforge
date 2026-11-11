"""Audio production: a scene or a sentence → a structured music or ambience direction → a small batch of
candidates from the engines → measured review → targeted refinement (bounded) → the best candidates with the
reason, ready for a human to listen and approve. Approved pieces feed a per-project style guide that later
directions and reviews follow, so the operator does not repeat themselves.

The engines are the existing procedural ones (composer + SoundFont, ambience layers). The direction step is a
structured model call (signed-in CLI) with a heuristic fallback; the review is `audio.quality` (measured, never
a model's opinion of a prompt). Nothing runs at app runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.audio import quality
from assetforge.core.util import utcnow
from assetforge.errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.core.catalog import Library

PROMPT_VERSION = 1
MAX_ROUNDS = 2  # refinement rounds after the first batch (cost control)
Kind = Literal["music", "ambience"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RolePick(Strict):
    role: Literal["melody", "harmony", "bass", "drums", "arp", "counter"]
    instrument: str


class MusicDirection(Strict):
    """The music direction brief: what the piece is for and how it should be built."""

    purpose: str = Field("", description="Narrative purpose in one sentence")
    emotion: str = Field("", description="Emotional objective and how it should progress")
    genre: str = Field("fantasy_restrained", description="A composer genre template (see the catalog)")
    mood: str = Field("calm", description="A mood word the composer leans on (calm, sad, tense, mysterious, heroic…)")
    instruments: list[RolePick] = Field(default_factory=list, description="Instrument per role; only roles that should play")
    tempo: float | None = Field(None, ge=40, le=180)
    key: str | None = Field(None, description="e.g. 'D minor'")
    energy: float = Field(0.35, ge=0, le=1, description="0 sparse and quiet … 1 full and busy")
    melody: float = Field(0.6, ge=0, le=1, description="Melody prominence: 0 none, 1 carries the piece")
    percussion: Literal["auto", "none", "light", "full"] = "none"
    arc: Literal["flat", "rise", "rise_fall", "fall"] = "flat"
    warmth: float = Field(0.45, ge=0, le=1, description="Softer top end for an under-dialogue mix")
    brightness: float = Field(0.4, ge=0, le=1, description="Expected spectral brightness (the review measures it)")
    under_dialogue: bool = Field(True, description="Must stay readable under text: sparse, mid-band light")
    seconds: float = Field(96.0, ge=8, le=240, description="Long enough that a reader hears one repeat per beat, not four")
    loop: bool = True
    space: str = Field("hall", description="dry | room | hall | cave | outdoor")
    avoid: list[str] = Field(default_factory=list, description="Musical elements to avoid")
    theme: str | None = Field(None, description="A named theme to reuse (keeps the tune, changes the mood)")
    rationale: str = Field("", description="Why these choices, for the operator")


class BedPick(Strict):
    bed: str
    level_db: float = Field(-14.0, ge=-40, le=0)
    params: dict[str, float] = Field(default_factory=dict)


class EventPick(Strict):
    event: str
    rate: float = Field(1.0, gt=0, le=30, description="Times per minute")
    gain_db: float = Field(-12.0, ge=-40, le=0)
    distance: float = Field(0.5, ge=0, le=1)


class AmbienceDirection(Strict):
    """The soundscape brief: the place as layers, with the acoustics and the restraint the scene needs."""

    environment: str = Field("", description="The place in one phrase")
    indoor: bool = True
    size: Literal["small", "medium", "large", "open"] = "medium"
    activity: float = Field(0.3, ge=0, le=1, description="How much happens: 0 still … 1 busy")
    time_of_day: Literal["day", "night"] | None = None
    weather: str = ""
    scene: str | None = Field(None, description="A catalog scene to start from, or null to build from layers")
    beds: list[BedPick] = Field(default_factory=list, description="Continuous layers (foundation and environment)")
    events: list[EventPick] = Field(default_factory=list, description="Intermittent details, rare by default")
    space: str = Field("room", description="dry | room | hall | cave | outdoor")
    width: float = Field(1.0, ge=0, le=2)
    distance: float = Field(0.0, ge=0, le=1)
    low_cut: float = Field(70.0, ge=0, le=400, description="High-pass (Hz): no rumble under a place")
    high_cut: float = Field(0.0, ge=0, le=20000, description="Low-pass (Hz) when hiss would fight the text")
    evolve: float = Field(0.4, ge=0, le=1, description="Slow level drift so the loop breathes")
    intensity: float = Field(0.4, ge=0, le=1)
    brightness: float = Field(0.4, ge=0, le=1)
    under_dialogue: bool = True
    length: float = Field(60.0, ge=10, le=180)
    avoid: list[str] = Field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------- style guide (approved references)


def _style_path(lib: Library, project: str) -> Path:
    return Path(lib.paths.home) / "audio_style" / f"{project}.json"


def style_guide(lib: Library, project: str | None) -> dict[str, Any]:
    if not project:
        return {"project": None, "references": [], "preferences": {}}
    path = _style_path(lib, project)
    if not path.exists():
        return {"project": project, "references": [], "preferences": {}}
    return json.loads(path.read_text())


def add_reference(lib: Library, project: str, asset_id: str, *, note: str = "", kind: Kind | None = None) -> dict[str, Any]:
    """Make an approved piece a reference: its measured character and its direction (when the asset carries one)
    guide later directions and reviews for the project."""
    asset = lib.get_asset(asset_id)
    m = quality.measure(Path(asset.path))
    meta = asset.metadata or {}
    direction = (meta.get("production") or {}).get("direction")
    kind = kind or ("music" if asset.type.value == "music" else "ambience")
    guide = style_guide(lib, project)
    guide["project"] = project
    guide["references"] = [r for r in guide["references"] if r["asset_id"] != asset.id]
    guide["references"].append({"asset_id": asset.id, "name": asset.name, "kind": kind, "note": note, "at": utcnow(),
                                "measured": {k: m[k] for k in ("lufs", "centroid_hz", "flatness", "onset_rate", "speech_band_share",
                                                               "dynamic_range_db", "repetition_index")}, "direction": direction})  # fmt: skip
    path = _style_path(lib, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(guide, indent=2))
    return guide


def remove_reference(lib: Library, project: str, asset_id: str) -> dict[str, Any]:
    guide = style_guide(lib, project)
    guide["references"] = [r for r in guide["references"] if r["asset_id"] != asset_id]
    _style_path(lib, project).write_text(json.dumps(guide, indent=2))
    return guide


def set_preferences(lib: Library, project: str, prefs: dict[str, Any]) -> dict[str, Any]:
    guide = style_guide(lib, project)
    guide["project"] = project
    guide["preferences"] = {**guide.get("preferences", {}), **prefs}
    path = _style_path(lib, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(guide, indent=2))
    return guide


def _reference_for(guide: dict[str, Any], kind: str, reference: str | None) -> dict[str, Any] | None:
    refs = [r for r in guide.get("references", []) if r["kind"] == kind]
    if reference:
        return next((r for r in refs if r["asset_id"] == reference), None)
    return refs[-1] if refs else None


# ---------------------------------------------------------------------- direction


def catalog_text(lib: Library) -> str:
    from assetforge.audio.ambience.beds import BEDS
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.ambience.events import EVENTS
    from assetforge.audio.music import composer, instruments

    data = {
        "music_genres": {g["name"]: g["description"] for g in composer.catalog()},
        "instruments": [i["name"] for i in instruments.catalog()],
        "ambience_scenes": {s.name: s.description for s in scenes(lib).values()},
        "beds": {b.name: b.description for b in BEDS.values()},
        "events": {e.name: e.description for e in EVENTS.values()},
        "spaces": ["dry", "room", "hall", "cave", "outdoor"],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _prompt(lib: Library, text: str, kind: Kind, context: dict[str, Any] | None, guide: dict[str, Any],
            feedback: str | None) -> str:  # fmt: skip
    parts = [f"CATALOG (use only these names):\n{catalog_text(lib)}", f"BRIEF: {text.strip()}"]
    if context:
        parts.append("STORY CONTEXT: " + json.dumps(context, ensure_ascii=False)[:3000])
    refs = [r for r in guide.get("references", []) if r["kind"] == kind]
    if refs or guide.get("preferences"):
        parts.append("PROJECT SOUND IDENTITY (approved by the operator; follow it unless the brief says otherwise): "
                     + json.dumps({"preferences": guide.get("preferences", {}),
                                   "approved_references": [{"name": r["name"], "note": r["note"], "direction": r.get("direction"),
                                                            "character": r["measured"]} for r in refs[-3:]]}, ensure_ascii=False))  # fmt: skip
    if kind == "music":
        parts.append(
            "You are the music director of an interactive visual novel. Write the MUSIC DIRECTION for this brief as the "
            "schema asks: purpose, emotional objective, a genre template from the catalog, mood, the instruments per role "
            "(only roles that should play; omit drums for no percussion), tempo, energy (most reading music sits at 0.2-0.45), "
            "melody prominence (under dialogue keep ≤ 0.6), percussion none|light|full, an arc (rise_fall for a loop that "
            "develops), warmth (≥ 0.4 under dialogue), brightness as you expect the piece to measure, seconds and loop, the "
            "acoustic space, what to avoid, and a short rationale. Subtle and polished beats ambitious; never heroic fanfare "
            "or busy drums unless the brief asks for them.")
    else:
        parts.append(
            "You are the sound designer of an interactive visual novel. Write the SOUNDSCAPE DIRECTION as layers: a "
            "foundation bed (room_tone with hum 0 and low_cut 160 indoors, wind or forest_air outdoors) at -20 to -26 dB, "
            "at most two environmental beds at -12 to -18 dB, and up to four detail events at 0.2-3 per minute, -8 to -18 dB, "
            "with a distance each. Pick a catalog scene only if it truly fits (never a horror scene for a normal place). Set "
            "the space, width, low_cut (70-120 Hz unless the place should rumble), high_cut (4-6 kHz when rain, wind or "
            "crowd hiss would sit in the reading band), evolve 0.3-0.6, intensity 0.2-0.5, activity as the brief implies, "
            "brightness as you expect it to measure, and what to avoid. A believable place, not a pile of effects.")
    if feedback:
        parts.append(f"OPERATOR FEEDBACK ON THE LAST RESULT (change the direction accordingly): {feedback.strip()}")
    return "\n\n".join(parts)


def _fallback_music(text: str) -> MusicDirection:
    from assetforge.audio.music.composer import MOOD_GENRE

    low = text.lower()
    mood = next((w for w in ("heroic", "triumphant", "tense", "suspense", "mysterious", "sad", "grief", "betray", "calm", "romantic", "dark", "eerie") if w in low), "calm")
    mood = {"betray": "sad", "suspense": "tense"}.get(mood, mood)
    genre = MOOD_GENRE.get(mood, "fantasy_restrained")
    energy = {"heroic": 0.6, "triumphant": 0.6, "tense": 0.3, "mysterious": 0.35, "sad": 0.25, "calm": 0.3, "dark": 0.3}.get(mood, 0.35)
    perc = "light" if mood in ("heroic", "triumphant") else "none"
    return MusicDirection(purpose=text[:160], emotion=mood, genre=genre, mood=mood, energy=energy, percussion=perc,
                          arc="rise_fall", warmth=0.4, brightness=0.45 if mood in ("heroic", "triumphant") else 0.35,
                          rationale="heuristic direction (no writer available)")  # fmt: skip


def _fallback_ambience(text: str, lib: Library) -> AmbienceDirection:
    from assetforge.audio.infer import ambience_from_words

    guess = ambience_from_words(text, lib) or {}
    indoor = any(w in text.lower() for w in ("room", "hall", "corridor", "dungeon", "chamber", "inside", "indoor", "court", "castle", "cell"))
    beds = [BedPick(bed="room_tone", level_db=-22, params={"hum": 0.0, "low_cut": 160.0})] if indoor else [BedPick(bed="forest_air", level_db=-18)]
    return AmbienceDirection(environment=text[:120], indoor=indoor, scene=guess.get("scene"), beds=beds if not guess.get("scene") else [],
                             space="hall" if indoor else "outdoor", low_cut=80.0, evolve=0.4, intensity=0.35,
                             high_cut=5500.0 if "rain" in text.lower() else 0.0, rationale="heuristic direction (no writer available)")  # fmt: skip


def direct(lib: Library, text: str, kind: Kind, *, project: str | None = None, context: dict[str, Any] | None = None,
           provider: str | None = None, model: str | None = None, feedback: str | None = None,
           fresh: bool = False) -> MusicDirection | AmbienceDirection:  # fmt: skip
    """The structured direction for a brief: a writer call with the catalog and the project's sound identity, or
    a heuristic when no writer is available (the fake provider in tests)."""
    from assetforge.config import load_settings
    from assetforge.llm import plans
    from assetforge.llm.base import TextCall
    from assetforge.llm.structured import generate

    guide = style_guide(lib, project)
    schema = MusicDirection if kind == "music" else AmbienceDirection
    settings = load_settings(lib.paths.home)
    fallback = (lambda _: _fallback_music(text).model_dump()) if kind == "music" else (lambda _: _fallback_ambience(text, lib).model_dump())
    call = TextCall(prompt=_prompt(lib, text, kind, context, guide, feedback),
                    system=f"You direct audio for interactive fiction. Return only JSON. Direction schema v{PROMPT_VERSION}.",
                    model=model, task=f"audio_direction.{kind}", offline=fallback)  # fmt: skip
    resolved = plans.resolve(call.task, settings, home=lib.paths.home, override_provider=provider, override_model=model)
    try:
        out = generate(lib, call, schema, provider=provider, settings=settings, project=project, resolved=resolved, fresh=fresh)
        direction = out.data
        chosen = out.choice
    except Exception as exc:  # noqa: BLE001 - no writer: the heuristic still produces a usable direction
        direction = schema(**fallback(None))
        chosen = {"chosen_by": "fallback", "error": str(exc)[:200]}
    return clean(direction, lib), chosen


def clean(direction: Any, lib: Library) -> Any:
    """Keep the direction inside the catalog so a model slip cannot break a render."""
    from assetforge.audio.ambience.beds import BEDS
    from assetforge.audio.ambience.engine import scenes
    from assetforge.audio.ambience.events import EVENTS
    from assetforge.audio.music import composer, instruments

    if isinstance(direction, MusicDirection):
        if direction.genre not in composer.GENRES:
            direction.genre = composer.resolve_genre(direction.genre) if direction.genre else "fantasy_restrained"
            if direction.genre not in composer.GENRES:
                direction.genre = "fantasy_restrained"
        known = {i["name"] for i in instruments.catalog()}
        from assetforge.audio.music.drums import KITS
        direction.instruments = [p for p in direction.instruments if instruments.resolve_instrument(p.instrument) in known
                                 and (p.role not in ("drums", "percussion") or p.instrument in KITS)]
        return direction
    names = set(scenes(lib))
    if direction.scene and direction.scene not in names:
        direction.scene = None
    direction.beds = [b for b in direction.beds if b.bed in BEDS][:4]
    direction.events = [e for e in direction.events if e.event in EVENTS][:5]
    if not direction.scene and not direction.beds:
        direction.beds = [BedPick(bed="room_tone" if direction.indoor else "forest_air", level_db=-22 if direction.indoor else -18,
                                  params={"hum": 0.0, "low_cut": 160.0} if direction.indoor else {})]
    return direction


# ---------------------------------------------------------------------- requests from a direction


def music_request(d: MusicDirection, *, name: str, seed: int, project: str | None, key: str | None = None) -> Any:
    from assetforge.audio.music.generate import MusicRequest

    roles = {p.role: p.instrument for p in d.instruments}
    return MusicRequest(name=name, genre=d.genre, mood=d.mood, tempo=d.tempo, key=d.key, energy=d.energy, seconds=d.seconds,
                        form="loop" if d.loop else "linear", instruments=roles, space=d.space, seed=seed, new_take=True,
                        backend="auto", project=project, asset_key=key, use_profile=False,
                        description=d.purpose or d.emotion, tags=["production", d.mood])  # fmt: skip


def ambience_request(d: AmbienceDirection, *, name: str, seed: int, project: str | None, key: str | None = None) -> Any:
    from assetforge.audio.ambience.engine import AmbienceRequest, BedLayer, EventLayer

    beds = [BedLayer(bed=b.bed, level_db=b.level_db, params=b.params) for b in d.beds]
    events = [EventLayer(event=e.event, rate=e.rate, gain_db=e.gain_db, distance=e.distance) for e in d.events]
    return AmbienceRequest(name=name, scene=d.scene, length=d.length, intensity=d.intensity, time_of_day=d.time_of_day,
                           distance=d.distance, width=d.width, space=d.space, beds=beds, events=events, seed=seed, new_take=True,
                           low_cut=d.low_cut, high_cut=d.high_cut, evolve=d.evolve, project=project, key=key, use_profile=False,
                           description=d.environment, tags=["production"])  # fmt: skip


def _extra_compose(d: MusicDirection) -> dict[str, Any]:
    return {"melody": d.melody, "percussion": d.percussion, "arc": d.arc, "warmth": d.warmth}


def _render(lib: Library, d: Any, kind: Kind, *, name: str, seed: int, project: str | None, key: str | None) -> dict[str, Any]:
    if kind == "music":
        from assetforge.audio.music import generate as mg

        req = music_request(d, name=name, seed=seed, project=project, key=key)
        return mg.generate_music(lib, req, compose_extra=_extra_compose(d))
    from assetforge.audio.ambience.engine import generate_ambience

    return generate_ambience(lib, ambience_request(d, name=name, seed=seed, project=project, key=key))


# ---------------------------------------------------------------------- refinement


def refine(d: Any, problems: list[dict[str, Any]], history: set[str] | None = None) -> tuple[Any, list[str]]:
    """Apply the refinements the worst problems call for (each at most once), and say what changed. A problem
    already refined in an earlier round (`history`) and still present gets its escalation instead."""
    changes: list[str] = []
    data = d.model_dump()
    seen: set[str] = set()
    kind = "music" if "instruments" in data else "ambience"
    for p in sorted(problems, key=lambda x: -x["weight"]):
        refinement = p.get("refinement") or {}
        if history and p["code"] in history and quality.ESCALATIONS.get(p["code"], {}).get(kind):
            refinement = quality.ESCALATIONS[p["code"]][kind]
        if p["code"] in seen or not refinement:
            continue
        seen.add(p["code"])
        for field, delta in refinement.items():
            if field in ("reseed", "instrument_swap"):
                changes.append(f"{p['code']}: {field} {delta}")
                continue
            if field == "bed_level_scale" and "beds" in data:
                hit = [b for b in data["beds"] if b.get("bed") in delta["beds"]]
                for b in hit:
                    if b["level_db"] + float(delta["db"]) < -36.0:  # already quiet: the layer goes
                        data["beds"] = [x for x in data["beds"] if x is not b]
                        changes.append(f"{p['code']}: dropped the {b['bed']} layer (its texture is the busyness)")
                    else:
                        b["level_db"] = round(b["level_db"] + float(delta["db"]), 1)
                        changes.append(f"{p['code']}: {b['bed']} {delta['db']:+} dB (its texture is the busyness)")
                if not hit:
                    changes.append(f"{p['code']}: no eventful bed to lower; event rates × 0.5")
                    for e in data.get("events", []):
                        e["rate"] = max(0.1, round(e["rate"] * 0.5, 2))
                continue
            if field == "drop_bed" and "beds" in data:
                before = len(data["beds"])
                data["beds"] = [b for b in data["beds"] if b.get("bed") != delta]
                if len(data["beds"]) < before:
                    changes.append(f"{p['code']}: dropped the {delta} layer")
                continue
            if field == "event_rate_scale" and "events" in data:
                for e in data["events"]:
                    e["rate"] = max(0.1, round(e["rate"] * float(delta), 2))
                changes.append(f"{p['code']}: event rates × {delta}")
            elif field == "level_scale" and "beds" in data:
                for b in data["beds"]:
                    b["level_db"] = max(-40.0, b["level_db"] + float(delta))
                changes.append(f"{p['code']}: bed levels {delta:+} dB")
            elif field in data:
                if isinstance(delta, (int, float)) and isinstance(data[field], (int, float)):
                    lo, hi = (0.0, 1.0) if field in ("energy", "melody", "warmth", "intensity", "evolve") else (0.0, 20000.0)
                    data[field] = float(min(hi, max(lo, data[field] + delta)))
                else:
                    data[field] = delta
                changes.append(f"{p['code']}: {field} → {data[field]}")
    return type(d)(**data), changes


DSP_FIXABLE = ("sub_rumble", "hissy", "in_dialogue_band", "harsh", "no_headroom", "loop_seam", "dc_offset", "muddy", "stereo_balance")


def _deep(lib: Library, asset_id: str, kind: str, brief: str, direction: dict[str, Any]) -> dict[str, Any]:
    from assetforge.audio.analysis import analyze_file
    from assetforge.audio.analysis.diagnose import diagnose, summarize

    asset = lib.get_asset(asset_id)
    # measure the WAV master, not the OGG delivery copy the primary variant points at
    path = next((v.file.path for v in asset.variants if v.label == "v1" and v.file.path.lower().endswith(".wav")), asset.path)
    report = analyze_file(Path(path), kind=kind)
    findings = diagnose(report, kind=kind, brief=brief, direction=direction, loop=bool(direction.get("loop", True)))
    return {"findings": findings, "summary": summarize(findings), "report": report}


def _correct_in_place(lib: Library, cand: dict[str, Any], kind: str, brief: str, direction: dict[str, Any], ref: dict[str, Any] | None) -> None:
    """Apply the DSP corrections a candidate's findings call for, as a new variant; keep it (primary) only when the
    targeted problems went away without new ones, and re-review the candidate on the corrected file."""
    from assetforge.audio.analysis import analyze_file
    from assetforge.audio.analysis.corrections import compare, correct_asset, fill_donors, plan_corrections
    from assetforge.audio.analysis.diagnose import diagnose

    findings = cand.get("findings") or []
    targets = [f["code"] for f in findings if f["severity"] != "note" and f["code"] in DSP_FIXABLE]
    if not targets:
        return
    report = _deep(lib, cand["asset_id"], kind, brief, direction)["report"]
    plan = fill_donors(plan_corrections([f for f in findings if f["code"] in DSP_FIXABLE], kind=kind), report)
    if not plan["dsp"]:
        return
    try:
        made = correct_asset(lib, cand["asset_id"], plan, label="corrected")
    except Exception as exc:  # a correction that cannot be applied is reported, never fatal
        cand["corrected"] = {"applied": False, "error": str(exc)[:200]}
        return
    if not made:
        return
    asset = lib.get_asset(cand["asset_id"])
    variant = next(v for v in asset.variants if v.id == made["variant_id"])
    path = Path(variant.file.path)
    after = analyze_file(path, kind=kind)
    after_f = diagnose(after, kind=kind, brief=brief, direction=direction, loop=bool(direction.get("loop", True)))
    cmp_ = compare({"findings": findings}, {"findings": after_f}, targets)
    cand["corrected"] = {"variant_id": variant.id, "ops": made["ops"], **{k: cmp_[k] for k in ("verdict", "fixed", "remaining", "regressions")}}
    if cmp_["verdict"] == "improved":
        lib.set_primary_variant(asset.id, variant.id)
        m = quality.measure(path)
        rev = quality.review(kind, direction, m)
        dist = quality.reference_distance(m, ref["measured"]) if ref else None
        cand["review"], cand["reference_distance"] = rev, dist
        cand["rank"] = round(rev["score"] - (0.3 * dist if dist is not None else 0.0), 3)
        cand["reason"] = _reason(rev, dist) + f" (after {', '.join(o['op'] for o in made['ops'] if o['op'] != 'normalize')})"
        cand["findings"] = after_f


def _reason(review: dict[str, Any], ref_dist: float | None) -> str:
    good = []
    m = review["measured"]
    good.append(f"{m['onset_rate']:.1f} events/s, centre {m['centroid_hz']} Hz, {m['speech_band_share']:.0%} in the reading band")
    if m.get("loop_seam_ok"):
        good.append("seamless loop")
    if ref_dist is not None:
        good.append(f"{(1 - ref_dist):.0%} close to the approved reference")
    bad = "; ".join(p["message"] for p in review["problems"][:2])
    return f"score {review['score']:.2f}: " + ", ".join(good) + (f". Open issues: {bad}" if bad else ". No measured issues")


# ---------------------------------------------------------------------- the loop


def produce_for_brief(lib: Library, text: str, *, kind: Kind, name: str, seed: int = 0, candidates: int = 3,
                      project: str | None = None, context: dict[str, Any] | None = None, reference: str | None = None,
                      direction: Any | None = None, provider: str | None = None, model: str | None = None,
                      feedback: str | None = None, key: str | None = None, max_rounds: int = MAX_ROUNDS) -> dict[str, Any]:  # fmt: skip
    """The production loop. Returns the direction, every candidate with its review, the chosen one with a reason,
    the refinements that happened, and what was rejected (those assets are marked rejected in the library)."""
    from assetforge.core.models import Status

    chosen_by: dict[str, Any] = {}
    if direction is None:
        direction, chosen_by = direct(lib, text, kind, project=project, context=context, provider=provider, model=model, feedback=feedback)
    elif isinstance(direction, dict):
        direction = (MusicDirection if kind == "music" else AmbienceDirection)(**direction)
    guide = style_guide(lib, project)
    ref = _reference_for(guide, kind, reference)
    rounds: list[dict[str, Any]] = []
    produced: list[dict[str, Any]] = []
    current = direction
    for round_no in range(max_rounds + 1):
        batch = []
        for i in range(candidates):
            take_seed = (seed * 1000 + round_no * 100 + i) % (2**31)
            result = _render(lib, current, kind, name=f"{name}_r{round_no}c{i + 1}", seed=take_seed, project=project, key=None)
            asset = lib.get_asset(result["asset_id"])
            m = quality.measure(Path(asset.path))
            rev = quality.review(kind, current.model_dump(), m)
            # the deep analysis: technical hard failures and voice-like content are judged here, not by the
            # brief-adherence ranges alone (analysis/diagnose.py; a confirmed voice in a voiceless brief is unusable)
            deep = _deep(lib, asset.id, kind, text, current.model_dump())
            for f in deep["findings"]:
                if f["severity"] == "note" or f["code"] in {p["code"] for p in rev["problems"]}:
                    continue
                hard = f["severity"] == "hard"
                # a "problem" from the deep diagnosis (a suspected voice, mud, a rumble) counts against the candidate
                # too, so a kept second choice is never one the Workbench would flag (demonstration, 2026-11-11)
                rev["problems"].append({"code": f["code"], "message": f["message"], "hard": hard, "weight": 0.5 if hard else 0.3,
                                        "refinement": quality.REFINEMENTS.get(f["code"], {}).get(kind, {})})  # fmt: skip
                if hard:
                    rev["ok"] = False
                    rev["score"] = 0.0
                else:
                    rev["score"] = round(max(0.0, rev["score"] - 0.3), 3)
            dist = quality.reference_distance(m, ref["measured"]) if ref else None
            rank = rev["score"] - (0.3 * dist if dist is not None else 0.0)
            batch.append({"asset_id": asset.id, "round": round_no, "seed": take_seed, "review": rev, "reference_distance": dist,
                          "rank": round(rank, 3), "reason": _reason(rev, dist), "direction": current.model_dump(),
                          "diagnosis": deep["summary"], "findings": deep["findings"]})  # fmt: skip
        produced += batch
        best = max(batch, key=lambda c: c["rank"])
        # Stage 7: a precise DSP correction of the best candidate before any regeneration (rumble, hiss, the reading
        # band, headroom, a seam): applied as a variant, re-measured, kept only when the targets improved
        _correct_in_place(lib, best, kind, text, current.model_dump(), ref)
        rounds.append({"round": round_no, "best_score": best["review"]["score"], "problems": [p["code"] for p in best["review"]["problems"]],
                       "corrected": best.get("corrected")})  # fmt: skip
        if best["review"]["ok"] and best["review"]["score"] >= 0.8:
            break
        if round_no < max_rounds and best["review"]["problems"]:
            history = {c.split(":")[0] for r in rounds[:-1] for c in r.get("refinements", [])}
            current, changes = refine(current, best["review"]["problems"], history)
            rounds[-1]["refinements"] = changes
            if not changes:
                break
    usable = [c for c in produced if c["review"]["ok"]]
    ranked = sorted(usable or produced, key=lambda c: -c["rank"])
    keep = ranked[:2]
    rejected = [c for c in produced if c not in keep]
    if rejected:
        lib.set_status([c["asset_id"] for c in rejected], Status.REJECTED, note="production review: a better candidate was kept")
    # a kept candidate may be a fingerprint reuse of an earlier run's render that was rejected since: it is a candidate again
    revived = [c["asset_id"] for c in keep if lib.get_asset(c["asset_id"]).status == Status.REJECTED]
    if revived:
        lib.set_status(revived, Status.CANDIDATE, note="production review: kept by a later run")
    chosen = ranked[0]
    if key:  # the key names the current pick: an earlier holder in the project (whatever its status) gives it up
        from assetforge.core.models import AssetPatch

        row = lib.conn.execute("SELECT id FROM assets WHERE key = ? AND ifnull(project, '') = ? AND id != ?",
                               (key, project or "", chosen["asset_id"])).fetchone()
        if row:
            lib.update_asset(row["id"], AssetPatch(key=""))
        lib.update_asset(chosen["asset_id"], AssetPatch(key=key))
    for c in keep:  # remember how each kept candidate was made, so it can become a reference later
        a = lib.get_asset(c["asset_id"])
        lib.update_asset(c["asset_id"], _patch_metadata(a.metadata or {}, {"production": {"kind": kind, "brief": text[:500],
                                                                                            "direction": c["direction"], "review": c["review"]["score"]}}))
    method = ("composer + SoundFont" if kind == "music" else "layered ambience") + f", {len(produced)} candidates in {len(rounds)} round(s)"
    return {"kind": kind, "brief": text, "direction": direction.model_dump(), "final_direction": current.model_dump(),
            "directed_by": chosen_by, "method": method, "rounds": rounds, "candidates": keep, "chosen": chosen,
            "rejected": [{"asset_id": c["asset_id"], "score": c["review"]["score"], "problems": [p["code"] for p in c["review"]["problems"]]} for c in rejected],
            "limitation": None if chosen["review"]["ok"] and chosen["review"]["score"] >= 0.6 else
            "The engines could not meet the brief within the attempt limit; the best attempt is shown with its measured problems."}  # fmt: skip


def _patch_metadata(existing: dict[str, Any], extra: dict[str, Any]) -> Any:
    from assetforge.core.models import AssetPatch

    return AssetPatch(metadata={**existing, **extra})


def theme_variations(lib: Library, source_asset: str, *, moods: list[str], name: str, project: str | None = None,
                     seed: int = 0) -> list[dict[str, Any]]:  # fmt: skip
    """The same tune in other moods: the source's melody and harmony seeds are pinned, and energy, percussion
    and arc change per mood, so a character or location theme stays recognisable across chapters."""
    from assetforge.audio.music import composer
    from assetforge.audio.music import generate as mg
    from assetforge.audio.music.generate import MusicRequest

    asset = lib.get_asset(source_asset)
    gen = (asset.metadata or {}).get("generator") or {}
    spec = gen.get("spec") or {}
    compose = dict(spec.get("compose") or {})
    if not compose:
        raise InvalidInputError("The source is not a composed piece (no compose brief in its metadata)")
    base_seed = int(gen.get("seed") or seed)
    seeds = composer.seeds_for(composer.Brief(**compose), base_seed)
    fixed = {"melody": seeds["melody"], "harmony": seeds["harmony"], "structure": seeds["structure"]}
    energy = {"calm": 0.25, "suspenseful": 0.35, "tense": 0.35, "emotional": 0.3, "triumphant": 0.7, "heroic": 0.65,
              "dark": 0.35, "mysterious": 0.4}  # fmt: skip
    out = []
    for mood in moods:
        extra = {"fixed": fixed, "arc": "rise_fall", "melody": compose.get("melody", 0.6),
                 "percussion": "light" if mood in ("triumphant", "heroic") else "none",
                 "warmth": 0.2 if mood in ("triumphant", "heroic") else 0.4}  # fmt: skip
        req = MusicRequest(name=f"{name}_{mood}", genre=compose.get("genre"), mood=mood, energy=energy.get(mood, 0.4),
                           tempo=compose.get("tempo"), key=compose.get("key"), seconds=compose.get("seconds") or 48,
                           instruments=compose.get("instruments") or {}, space=compose.get("space"), seed=base_seed,
                           new_take=True, backend="auto", project=project, use_profile=False, tags=["theme", mood])  # fmt: skip
        result = mg.generate_music(lib, req, compose_extra=extra)
        from assetforge.core.models import LineageRelation

        lib.add_lineage(result["asset_id"], asset.id, LineageRelation.DERIVED_FROM)
        out.append({"mood": mood, "asset_id": result["asset_id"]})
    return out
