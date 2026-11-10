"""Creative direction for a story: who it is for, what kind of story it is, which world it lives in, how it is
told and how it looks, as data the operator picks from presets and the pipeline turns into prompt guidance.

Precedence (highest first):
1. explicit instructions on the story (`custom.instructions`, `custom.exclusions`, `custom.canon`, and the
   config's own premise/tone/behaviour);
2. the story's saved direction and the canon written from it (world, names, cast);
3. the preset the direction started from;
4. production defaults (`DEFAULTS`).

Nothing is ever inferred from the operator: `system_rules()` tells the writer so. Where the direction is silent
the writer invents something consistent with the chosen world, never a real country by default.

Presets are YAML files in `story/direction_presets/` (built-in) and `$ASSETFORGE_HOME/story_presets/` (the
operator's, same name wins). Genre and tradition knowledge lives in `genres.yaml` and `traditions.yaml`.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from assetforge.core.util import slugify
from assetforge.errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from assetforge.art import ArtDirection

# ---------------------------------------------------------------------- options (what the UI offers)

REGIONS = {
    "global": "Global / international",
    "north-america": "United States / North America",
    "uk-europe": "United Kingdom / Europe",
    "india-south-asia": "India / South Asia",
    "japan": "Japan",
    "korea": "South Korea",
    "china": "China",
    "southeast-asia": "Southeast Asia",
    "custom": "Custom country or region",
}
READERS = {
    "adult": "Adults (general)",
    "adult-women": "Adult women who read romance and drama",
    "young-adult": "Young adults (18–25)",
    "teen": "Teens (13–17)",
    "all-ages": "All ages",
}
WRITING_STYLES = {
    "simple": "Simple, accessible English (default)",
    "conversational": "Natural conversational English",
    "cinematic": "Dramatic, cinematic English",
    "literary": "Literary or descriptive English",
    "custom": "Custom writing instructions",
}
WORLD_TYPES = {
    "medieval-fantasy": "Medieval fantasy",
    "high-fantasy": "High fantasy",
    "dark-fantasy": "Dark fantasy",
    "modern-fantasy": "Modern fantasy",
    "urban-supernatural": "Urban supernatural",
    "historical-fantasy": "Historical-inspired fantasy",
    "eastern-fantasy": "Eastern fantasy",
    "post-apocalyptic": "Post-apocalyptic fantasy",
    "contemporary": "Contemporary, no magic",
    "original": "Original / custom world",
}
CULTURES = {
    "original": "Original fictional culture",
    "western-european": "Western European-inspired",
    "korean": "Korean-inspired",
    "japanese": "Japanese-inspired",
    "chinese": "Chinese-inspired",
    "south-asian": "South Asian-inspired",
    "mixed": "Mixed, with defined influences",
    "custom": "Custom",
}
NAMING = {
    "western-fantasy": "Western-inspired fantasy names",
    "korean": "Korean naming conventions",
    "japanese": "Japanese names, or coherent invented ones",
    "chinese": "Chinese naming conventions",
    "south-asian": "South Asian naming conventions",
    "invented": "An invented system for this world",
    "match-culture": "Follow the cultural inspiration",
    "custom": "Custom naming guidance",
}
TONES = ["heroic", "dark", "emotional", "romantic", "political", "comedic", "mysterious", "warm", "tense"]
PACING = {"fast": "Fast", "balanced": "Balanced", "slow-burn": "Slow burn"}
LEVELS = {"none": "None", "low": "Low", "medium": "Medium", "high": "High", "central": "Central"}
LEVELS_3 = {"low": "Low", "medium": "Medium", "high": "High"}
PROGRESSION = {"none": "No power progression", "slow": "Slow, hard-won", "gradual": "Gradual with major milestones",
               "fast": "Fast, frequent power-ups"}  # fmt: skip
DEPTH = {"light": "Light", "medium": "Medium", "strong": "Strong"}
CHOICES = {"light": "Light: mostly flavour and relationships", "meaningful": "Meaningful with persistent consequences",
           "heavy": "Heavy: routes diverge and endings differ widely"}  # fmt: skip
CLIFFHANGERS = {"soft": "Soft: an open question", "strong": "Strong: a reveal or a confrontation",
                "extreme": "Extreme: a reversal every chapter"}  # fmt: skip
ART_STYLES = {
    "manhwa-fantasy": "Korean manhwa-inspired illustration",
    "light-novel-anime": "Japanese anime / light-novel illustration",
    "western-fantasy": "Western fantasy illustration",
    "dark-fantasy": "Dark fantasy illustration",
    "cinematic-semi-realistic": "Semi-realistic cinematic",
    "stylized-vn": "Stylized 2D visual novel",
    "custom": "Custom art direction",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorldPrefs(Strict):
    """Optional world characteristics (free text, each one or two sentences)."""

    technology: str = Field("", description="Technology and civilisation level")
    politics: str = Field("", description="Kingdoms, empires, guilds, factions")
    magic: str = Field("", description="Magic or supernatural rules and their limits")
    hierarchy: str = Field("", description="Social hierarchy and class structure")
    religion: str = Field("", description="Religious or mythological influences")
    conflict: str = Field("", description="The structure of the central conflict")
    progression: str = Field("", description="How power is gained, ranked and limited")

    def lines(self) -> list[str]:
        return [f"{k.capitalize()}: {v.strip()}" for k, v in self.model_dump().items() if v and v.strip()]


class Custom(Strict):
    instructions: str = Field("", description="Free-text creative direction (highest priority)")
    requirements: str = Field("", description="Special requirements")
    exclusions: str = Field("", description="Explicit exclusions: things that must not appear")
    canon: str = Field("", description="Existing canon that must be preserved")


class CreativeDirection(Strict):
    preset: str | None = Field(None, description="Preset this direction started from")
    # A. audience and language
    region: str = "global"
    region_note: str = Field("", description="For a custom region, or notes on the market")
    readers: str = "adult"
    writing_style: str = "simple"
    writing_notes: str = ""
    # B. genre and story identity
    genre: str = "power-fantasy"
    subgenres: list[str] = Field(default_factory=list)
    tradition: str = Field("korean-webnovel", description="Narrative influence (see traditions.yaml)")
    themes: list[str] = Field(default_factory=list, description="Emotional themes, e.g. betrayal, hidden power")
    # C. world and cultural setting
    world_type: str = "medieval-fantasy"
    culture: str = "original"
    culture_note: str = Field("", description="Defined influences for mixed/custom cultures")
    world: WorldPrefs = Field(default_factory=WorldPrefs)
    naming: str = "match-culture"
    naming_note: str = Field("", description="Custom naming guidance")
    reserved_names: list[str] = Field(default_factory=list, description="Names that must be used as given")
    name_overrides: dict[str, str] = Field(default_factory=dict, description="role or character id → fixed name")
    # D. narrative experience
    pacing: str = "fast"
    tone: list[str] = Field(default_factory=list)
    drama: str = "high"
    romance: str = "low"
    mystery: str = "medium"
    progression: str = "gradual"
    depth: str = "strong"
    choices: str = "meaningful"
    cliffhangers: str = "strong"
    # E. visual direction
    art_style: str = "manhwa-fantasy"
    art_note: str = Field("", description="Custom art direction text when art_style is custom")
    character_design: str = ""
    environment: str = ""
    color_mood: str = ""
    # F. custom
    custom: Custom = Field(default_factory=Custom)

    @field_validator("subgenres", "themes", "tone", "reserved_names", mode="before")
    @classmethod
    def _list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = value.split(",") if isinstance(value, str) else list(value)
        return [str(v).strip() for v in items if str(v).strip()]

    @field_validator("region", "readers", "writing_style", "world_type", "culture", "naming", "pacing", "drama", "romance",
                     "mystery", "progression", "depth", "choices", "cliffhangers", "genre", "tradition", mode="before")  # fmt: skip
    @classmethod
    def _slug(cls, value: Any) -> str:
        return slugify(str(value)) if value is not None else ""

    def is_default(self) -> bool:
        return self == CreativeDirection()


DEFAULTS = CreativeDirection()


# ---------------------------------------------------------------------- knowledge (genres, traditions)


def _read_yaml(name: str) -> dict[str, Any]:
    try:
        text = resources.files("assetforge.story").joinpath(name).read_text()
    except (FileNotFoundError, OSError):
        return {}
    return yaml.safe_load(text) or {}


@lru_cache(maxsize=1)
def genres() -> dict[str, Any]:
    data = _read_yaml("genres.yaml")
    return {"primary": {g["id"]: g for g in data.get("primary", [])},
            "subgenres": {s["id"]: s for s in data.get("subgenres", [])}}  # fmt: skip


@lru_cache(maxsize=1)
def traditions() -> dict[str, Any]:
    return {t["id"]: t for t in _read_yaml("traditions.yaml").get("traditions", [])}


def options() -> dict[str, Any]:
    """Everything the UI needs to draw the form: option lists with labels, help text and the knowledge files."""
    g, t = genres(), traditions()
    return {
        "regions": REGIONS, "readers": READERS, "writing_styles": WRITING_STYLES, "world_types": WORLD_TYPES,
        "cultures": CULTURES, "naming": NAMING, "tones": TONES, "pacing": PACING, "levels": LEVELS, "levels3": LEVELS_3,
        "progression": PROGRESSION, "depth": DEPTH, "choices": CHOICES, "cliffhangers": CLIFFHANGERS, "art_styles": ART_STYLES,
        "genres": [{"id": k, "name": v.get("name", k), "promise": v.get("promise", ""), "default_subgenres": v.get("default_subgenres", [])}
                   for k, v in g["primary"].items()],
        "subgenres": [{"id": k, "name": v.get("name", k), "kind": v.get("kind", ""), "primary": v.get("primary", []),
                       "promise": v.get("promise", ""), "pairs_well": v.get("pairs_well", []), "conflicts_with": v.get("conflicts_with", [])}
                      for k, v in g["subgenres"].items()],
        "traditions": [{"id": k, "name": v.get("name", k), "summary": v.get("summary", "")} for k, v in t.items()],
        "defaults": DEFAULTS.model_dump(),
        "help": HELP,
    }  # fmt: skip


HELP = {
    "region": "Where the story will be sold. It does not set the story's culture: a global story can be set anywhere.",
    "readers": "Who you are writing for. Pair it with the rating in the story's basic settings.",
    "writing_style": "How the English reads. Simple is best for a broad mobile audience.",
    "genre": "The core fantasy the reader comes for. Subgenres add premise, mechanics, setting or theme.",
    "tradition": "Storytelling techniques to borrow (chapter rhythm, reveals, monologue). Independent of the art style.",
    "world_type": "The kind of world. Cultural inspiration and naming are chosen separately.",
    "culture": "What the world's customs, titles and institutions are inspired by. Original means invented for this story.",
    "naming": "How people and places are named. The writer makes a naming guide before the cast, so names stay consistent.",
    "choices": "How much player choices change the story. Meaningful keeps a decision per chapter that the story remembers.",
    "art_style": "The look of sprites, backgrounds and CGs. Independent of the narrative tradition.",
}


# ---------------------------------------------------------------------- presets


class Preset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str = ""
    summary: str = ""
    source: str = "built-in"
    direction: CreativeDirection


def _preset_dirs(home: Path | None) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = []
    try:
        dirs.append(("built-in", Path(str(resources.files("assetforge.story").joinpath("direction_presets")))))
    except (FileNotFoundError, OSError):
        pass
    if home is not None:
        dirs.append(("user", Path(home) / "story_presets"))
    return dirs


def _load_preset(path: Path, source: str) -> Preset:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise InvalidInputError(f"Preset {path.name} must be a mapping")
    name = slugify(str(data.pop("name", path.stem)))
    return Preset(name=name, title=str(data.pop("title", "") or name.replace("-", " ").title()),
                  summary=str(data.pop("summary", "")), source=source,
                  direction=CreativeDirection(preset=name, **data))  # fmt: skip


def presets(home: Path | None = None) -> list[Preset]:
    """Built-in presets, then the operator's (`$ASSETFORGE_HOME/story_presets/*.yaml`); same name: the operator's wins."""
    found: dict[str, Preset] = {}
    for source, folder in _preset_dirs(home):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            try:
                preset = _load_preset(path, source)
            except Exception as exc:  # a broken user file must not hide the others
                raise InvalidInputError(f"Story preset {path} is invalid: {exc}") from exc
            found[preset.name] = preset
    return list(found.values())


def get_preset(name: str, home: Path | None = None) -> Preset:
    key = slugify(name)
    for preset in presets(home):
        if preset.name == key:
            return preset
    raise NotFoundError(f"No story preset named {name!r}", hint="`assetforge story presets` lists them")


def save_preset(home: Path, name: str, direction: CreativeDirection, *, title: str = "", summary: str = "") -> Path:
    key = slugify(name)
    if not key:
        raise InvalidInputError("A preset needs a name")
    folder = Path(home) / "story_presets"
    folder.mkdir(parents=True, exist_ok=True)
    data = direction.model_dump(exclude_defaults=True)
    data.pop("preset", None)
    body = {"name": key, "title": title or name, "summary": summary, **data}
    path = folder / f"{key}.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
    return path


def delete_preset(home: Path, name: str) -> None:
    path = Path(home) / "story_presets" / f"{slugify(name)}.yaml"
    if not path.exists():
        raise NotFoundError(f"No user preset named {name!r}")
    path.unlink()


def resolve(data: dict[str, Any] | CreativeDirection | None, *, preset: str | None = None,
            home: Path | None = None) -> CreativeDirection:  # fmt: skip
    """Preset values first, then every field the operator set explicitly (a field present in `data` wins even when it
    equals the default, so a preset can be overridden back to the default)."""
    explicit = data.model_dump(exclude_defaults=True) if isinstance(data, CreativeDirection) else dict(data or {})
    name = preset or explicit.pop("preset", None)
    base: dict[str, Any] = {}
    if name:
        base = get_preset(name, home).direction.model_dump()
        base["preset"] = name
    for key, value in explicit.items():
        if key in ("world", "custom") and isinstance(value, dict):
            base[key] = {**(base.get(key) or {}), **{k: v for k, v in value.items() if v is not None}}
        elif value is not None:
            base[key] = value
    return CreativeDirection(**base)


# ---------------------------------------------------------------------- warnings and summary


def warnings(d: CreativeDirection) -> list[dict[str, str]]:
    """Contradictory or unknown combinations. Warnings, never refusals: the operator may mean it."""
    found: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        found.append({"code": code, "message": message})

    g, t = genres(), traditions()
    if g["primary"] and d.genre not in g["primary"]:
        warn("unknown_genre", f"Genre {d.genre!r} is not in the taxonomy; the writer gets only the name.")
    for sub in d.subgenres:
        if g["subgenres"] and sub not in g["subgenres"]:
            warn("unknown_subgenre", f"Subgenre {sub!r} is not in the taxonomy; the writer gets only the name.")
        elif g["subgenres"] and d.genre in g["primary"] and d.genre not in (g["subgenres"][sub].get("primary") or [d.genre]):
            warn("subgenre_fit", f"{g['subgenres'][sub].get('name', sub)} is usually paired with "
                 f"{', '.join(g['subgenres'][sub].get('primary', []))}, not {d.genre}.")  # fmt: skip
    for sub in d.subgenres:
        for other in (g["subgenres"].get(sub, {}).get("conflicts_with") or []):
            if other in d.subgenres and sub < other:
                warn("subgenre_conflict", f"{sub} and {other} pull in different directions; the premise needs a reason for both.")
    if t and d.tradition not in t:
        warn("unknown_tradition", f"Narrative influence {d.tradition!r} is not profiled; the writer gets only the name.")
    if d.world_type in ("medieval-fantasy", "high-fantasy", "historical-fantasy") and any(
            s in d.subgenres for s in ("dungeon-hunter", "system-leveling")) and not d.world.progression:
        warn("mechanic_needs_rules", "Dungeon/system mechanics in a medieval world need `world.progression` to say how the "
             "system exists there (gates, a tower, a divine ledger…).")  # fmt: skip
    if d.world_type in ("modern-fantasy", "urban-supernatural", "contemporary") and "medieval-kingdom" in d.subgenres:
        warn("world_vs_subgenre", "A modern world with the medieval-kingdom subgenre needs a frame (regression into a game, a portal…).")
    if d.naming not in ("match-culture", "invented", "custom") and d.culture not in ("original", "mixed", "custom") \
            and d.naming.split("-")[0] != d.culture.split("-")[0]:
        warn("naming_vs_culture", f"Names follow {NAMING.get(d.naming, d.naming)} while the culture is {CULTURES.get(d.culture, d.culture)}; "
             "the world needs a reason (a founding people, a conquest) or readers will notice.")  # fmt: skip
    if d.naming == "match-culture" and d.culture in ("original", "custom") and not d.naming_note:
        warn("naming_undefined", "Naming follows the culture, but the culture is original: the naming guide will invent a system. "
             "Add a naming note if you want a particular flavour.")  # fmt: skip
    if d.romance == "none" and "romantic" in d.tone:
        warn("romance_vs_tone", "Tone includes romantic but romance emphasis is none.")
    if d.romance == "central" and d.genre == "power-fantasy" and "romantic" not in d.tone:
        warn("romance_central", "Romance is central in a power fantasy; add romantic to the tone or lower the emphasis.")
    if d.pacing == "slow-burn" and d.tradition in ("short-form-drama", "korean-webnovel") and d.cliffhangers == "extreme":
        warn("pacing_vs_cliffhangers", "Slow burn with a reversal every chapter is hard to sustain; pick one.")
    if d.progression == "none" and any(s in d.subgenres for s in ("progression", "system-leveling", "weak-to-strong", "cultivation", "tower-climbing")):
        warn("progression_off", "Progression subgenres with no power progression contradict each other.")
    if d.writing_style == "literary" and d.readers in ("teen", "all-ages"):
        warn("style_vs_readers", "Literary English for younger readers: consider simple or conversational.")
    if d.writing_style == "custom" and not d.writing_notes:
        warn("style_note_missing", "Custom writing style without writing notes falls back to simple English.")
    if d.art_style == "custom" and not d.art_note:
        warn("art_note_missing", "Custom art style without an art note; the world's own art direction will be used.")
    if d.region == "custom" and not d.region_note:
        warn("region_note_missing", "Custom region without a note.")
    if d.culture in ("mixed", "custom") and not d.culture_note:
        warn("culture_note_missing", "Mixed or custom culture without a note on the influences; the writer will invent them.")
    return found


def summary(d: CreativeDirection) -> str:
    """One short paragraph a person can read at a glance."""
    g, t = genres(), traditions()
    genre = g["primary"].get(d.genre, {}).get("name", d.genre.replace("-", " ").title())
    subs = ", ".join(g["subgenres"].get(s, {}).get("name", s.replace("-", " ")) for s in d.subgenres)
    tradition = t.get(d.tradition, {}).get("name", d.tradition.replace("-", " "))
    parts = [f"{genre}" + (f" ({subs})" if subs else "") + f" for {READERS.get(d.readers, d.readers)} in {REGIONS.get(d.region, d.region_note or d.region)}.",
             f"{WORLD_TYPES.get(d.world_type, d.world_type)}, {CULTURES.get(d.culture, d.culture).lower()}; names: {NAMING.get(d.naming, d.naming).lower()}.",
             f"Told like {tradition}, {PACING.get(d.pacing, d.pacing).lower()} pacing" + (f", {'/'.join(d.tone)} tone" if d.tone else "")
             + f"; romance {d.romance}, drama {d.drama}, choices {d.choices}, cliffhangers {d.cliffhangers}.",
             f"{WRITING_STYLES.get(d.writing_style, d.writing_style).split(' (')[0]}. Art: {ART_STYLES.get(d.art_style, d.art_note or d.art_style)}."]  # fmt: skip
    return " ".join(parts)


# ---------------------------------------------------------------------- prompt guidance


STYLE_RULES = {
    "simple": ("Simple, accessible English for a broad international mobile audience: everyday words, short sentences "
               "(dialogue mostly under 18 words), one idea per line, no rare vocabulary, no idioms or slang that need "
               "explaining, no purple prose, no poetry in dialogue. Vivid through concrete nouns and verbs, not adjectives."),
    "conversational": ("Natural conversational English: people talk the way real people do, with contractions, "
                       "interruptions and subtext; narration is plain and direct."),
    "cinematic": ("Dramatic, cinematic English: present-tense immediacy, strong verbs, short punchy lines at peaks, "
                  "a wider sentence only to breathe; dialogue crackles but stays natural."),
    "literary": ("Literary, descriptive English: textured sensory detail, rhythm and image, still readable one textbox "
                 "at a time; dialogue stays human, never ornamental."),
}  # fmt: skip


def system_rules(d: CreativeDirection) -> list[str]:
    """Lines for the system prompt: the writing style and the no-inference rule."""
    style = STYLE_RULES.get(d.writing_style) or d.writing_notes.strip() or STYLE_RULES["simple"]
    lines = [
        f"WRITING STYLE: {style}" + (f" Also: {d.writing_notes.strip()}" if d.writing_notes.strip() and d.writing_style != "custom" else ""),
        "CONSISTENCY: names, titles, institutions, customs, food, religion, expressions and slang belong to the story's own world "
        "and its stated cultural inspiration. A character never uses a real-world region's modern expressions unless the "
        "direction says so. Infer nothing about culture or setting from the operator, from earlier stories or from the "
        "premise's author: where the direction is silent, invent something consistent with the chosen world and never "
        "default to a real country.",
    ]  # fmt: skip
    if d.custom.exclusions.strip():
        lines.append(f"NEVER INCLUDE: {d.custom.exclusions.strip()}")
    if d.custom.instructions.strip():
        lines.append(f"OPERATOR INSTRUCTIONS (highest priority): {d.custom.instructions.strip()}")
    return lines


def _strs(items: Any) -> list[str]:
    """YAML list items as plain strings (a mapping item becomes 'key: value')."""
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append("; ".join(f"{k}: {v}" for k, v in item.items()))
        else:
            out.append(str(item))
    return out


def _genre_lines(d: CreativeDirection) -> list[str]:
    g = genres()
    lines: list[str] = []
    primary = g["primary"].get(d.genre)
    label = primary.get("name", d.genre) if primary else d.genre.replace("-", " ")
    lines.append(f"GENRE: {label}." + (f" Promise: {primary['promise']}" if primary and primary.get("promise") else ""))
    for sub in d.subgenres:
        info = g["subgenres"].get(sub)
        if not info:
            lines.append(f"Subgenre: {sub.replace('-', ' ')}.")
            continue
        mech = "; ".join(_strs(info.get("mechanics")))
        avoid = "; ".join(_strs(info.get("cliches_to_avoid")))
        lines.append(f"Subgenre {info.get('name', sub)} ({info.get('kind', '')}): {info.get('promise', '')}"
                     + (f" How it works: {mech}." if mech else "") + (f" Avoid: {avoid}." if avoid else ""))  # fmt: skip
    if d.themes:
        lines.append("Emotional themes: " + ", ".join(d.themes) + ".")
    return lines


def _tradition_lines(d: CreativeDirection) -> list[str]:
    info = traditions().get(d.tradition)
    if not info:
        return [f"NARRATIVE INFLUENCE: {d.tradition.replace('-', ' ')} storytelling techniques (not its specific works)."]
    lines = [f"NARRATIVE INFLUENCE: {info.get('name', d.tradition)}. {info.get('summary', '')}".strip()]
    if info.get("techniques"):
        lines.append("Techniques to use: " + "; ".join(_strs(info["techniques"])) + ".")
    for key in ("chapter_rhythm", "dialogue", "protagonist", "romance"):
        if info.get(key):
            lines.append(f"{key.replace('_', ' ').capitalize()}: {info[key]}")
    if info.get("pitfalls"):
        lines.append("Pitfalls to avoid: " + "; ".join(_strs(info["pitfalls"])) + ".")
    return lines


def _experience_lines(d: CreativeDirection) -> list[str]:
    romance = {"none": "no romance plot", "low": "romance is a light undercurrent at most", "medium": "romance is a secondary thread with real tension",
               "high": "romance is a major thread with obstacles", "central": "romance is the spine of the story"}  # fmt: skip
    choices = {"light": "choices mostly colour tone and relationships",
               "meaningful": "every chapter holds one decision that costs something, is remembered (a flag the story reads later) and is never offered again; other choices colour tone and relationships",
               "heavy": "decisions open and close routes; chapters differ by route and endings differ widely"}  # fmt: skip
    cliff = {"soft": "each chapter ends on an open question", "strong": "each chapter ends on a reveal, a confrontation or a decision the reader must make",
             "extreme": "each chapter ends on a reversal that changes what the reader believes"}  # fmt: skip
    return [
        "NARRATIVE EXPERIENCE: "
        f"pacing {d.pacing.replace('-', ' ')}; tone {', '.join(d.tone) if d.tone else 'as the genre demands'}; drama intensity {d.drama}; "
        f"mystery emphasis {d.mystery}; {romance.get(d.romance, d.romance)}; power progression: {PROGRESSION.get(d.progression, d.progression).lower()}; "
        f"character development {d.depth}; {choices.get(d.choices, d.choices)}; {cliff.get(d.cliffhangers, d.cliffhangers)}.",
    ]  # fmt: skip


def world_block(d: CreativeDirection) -> str:
    """Guidance for the world step."""
    lines = [
        f"CREATIVE DIRECTION. Market: {REGIONS.get(d.region, d.region)}{(' (' + d.region_note + ')') if d.region_note else ''}; "
        f"readers: {READERS.get(d.readers, d.readers)}. The market does not decide the setting: the world below does.",
        f"WORLD: {WORLD_TYPES.get(d.world_type, d.world_type)}. Cultural inspiration: {CULTURES.get(d.culture, d.culture)}"
        + (f" ({d.culture_note.strip()})" if d.culture_note.strip() else "") + ". Use it for customs, titles, institutions, "
        "architecture, food and manners, coherently and without naming real countries, cities or religions.",
    ]  # fmt: skip
    if d.world.lines():
        lines.append("World characteristics to honour: " + " ".join(d.world.lines()))
    lines += _genre_lines(d) + _tradition_lines(d) + _experience_lines(d)
    lines.append("VISUAL DIRECTION (for `art_direction`; independent of the narrative influence): " + _art_summary(d))
    lines.append("PREMISE SHEET (write it into the world's fields): the reader's fantasy (what they get to be or have), "
                 "the protagonist's want and what it costs, an antagonist with an agenda of their own, what is lost on "
                 "failure, and the central question the final act answers. If the premise lacks a stake, give it one.")  # fmt: skip
    if d.custom.requirements.strip():
        lines.append(f"REQUIREMENTS: {d.custom.requirements.strip()}")
    if d.custom.canon.strip():
        lines.append(f"ESTABLISHED CANON (keep it): {d.custom.canon.strip()}")
    return "\n".join(lines)


def _art_summary(d: CreativeDirection) -> str:
    """The chosen art preset in words (so the world's own art_direction text agrees with it)."""
    if d.art_style == "custom":
        base = d.art_note.strip() or "the world's own look"
    else:
        try:
            from assetforge import art

            preset = art.get_preset(None, d.art_style)
            base = f"{preset.summary or ART_STYLES.get(d.art_style, d.art_style)}: {preset.description or ''}".strip(": ")
        except Exception:  # unknown preset name: the label is still useful
            base = ART_STYLES.get(d.art_style, d.art_style)
    extras = [x for x in (d.character_design and f"characters: {d.character_design}", d.environment and f"environments: {d.environment}",
                          d.color_mood and f"colour and mood: {d.color_mood}") if x]  # fmt: skip
    return base + ("; " + "; ".join(extras) if extras else "") + "."


def names_block(d: CreativeDirection) -> str:
    """Guidance for the naming-guide step."""
    tradition = NAMING.get(d.naming, d.naming)
    if d.naming == "match-culture":
        tradition = f"names that fit the cultural inspiration ({CULTURES.get(d.culture, d.culture)})"
    lines = [f"NAMING TRADITION: {tradition}.",
             "Rules: one coherent system for people and one for places; given and family names follow the same tradition "
             "(say which order they come in); titles and honorifics fit the world's hierarchy; never mix traditions "
             "without a narrative reason; no names of real famous people or of characters from existing works."]  # fmt: skip
    if d.naming_note.strip():
        lines.append(f"Naming guidance from the operator: {d.naming_note.strip()}")
    if d.reserved_names:
        lines.append("Reserved names that must be used exactly as given: " + ", ".join(d.reserved_names) + ".")
    if d.name_overrides:
        lines.append("Fixed names: " + "; ".join(f"{k} = {v}" for k, v in d.name_overrides.items()) + ".")
    return "\n".join(lines)


def plan_block(d: CreativeDirection) -> str:
    """Guidance for the outline step."""
    lines = _genre_lines(d) + _tradition_lines(d) + _experience_lines(d)
    lines.append("SEASON RULES: every chapter opens a question and closes a smaller one; the central question closes only "
                 "in the final act; the antagonist acts (not just appears) at least every second chapter; each chapter names "
                 "one decision (a flag) that a later chapter reads, and the chapter after it shows the consequence; "
                 "endings are reached by named decisions, foreshadowed after they are made, and the fallback ending is "
                 "satisfying on its own terms.")  # fmt: skip
    if d.custom.canon.strip():
        lines.append(f"ESTABLISHED CANON (keep it): {d.custom.canon.strip()}")
    return "\n".join(lines)


def scene_block(d: CreativeDirection) -> str:
    """Guidance for every chapter-writing call."""
    cliff = {"soft": "an open question", "strong": "a reveal, a confrontation or a decision",
             "extreme": "a reversal of what the reader believed"}[d.cliffhangers if d.cliffhangers in ("soft", "strong", "extreme") else "strong"]  # fmt: skip
    lines = _tradition_lines(d)[:2] + _experience_lines(d) + [
        "CHAPTER SHAPE: open inside a moment that is already turning (no weather, no waking up, no recap); 2-4 scenes "
        f"with at least one `place` move; a peak worth a `cg`; end on {cliff}, never on a mood or a rest.",
        "DECISIONS: never narrate 'whatever you chose'; each option's consequence is shown in its reaction and the chapter "
        "goes on from the option actually taken; a decision the player already made (a flag set earlier) is never offered "
        "again, acknowledge it with `when` lines instead.",
    ]  # fmt: skip
    return "\n".join(lines)


def check_block(d: CreativeDirection) -> str:
    return ("Also check the creative direction: names that break the naming guide, modern or real-world regional slang "
            f"in a {WORLD_TYPES.get(d.world_type, d.world_type)} world, customs or references that contradict the "
            f"{CULTURES.get(d.culture, d.culture)} inspiration, drift in writing style, and decisions offered twice.")  # fmt: skip


def art_direction(d: CreativeDirection, world_look: str = "") -> ArtDirection:
    """The story's art profile: a built-in art preset for the chosen style plus the direction's own notes."""
    from assetforge import art

    style = None if d.art_style == "custom" else d.art_style
    notes = [n for n in (d.art_note if d.art_style == "custom" else "", d.character_design and f"Character design: {d.character_design}",
                         d.environment and f"Environments: {d.environment}") if n]  # fmt: skip
    notes.append("Visual novel: one consistent art direction for every sprite, background and CG; the same character design "
                 "in every expression and illustration.")  # fmt: skip
    return art.ArtDirection(style=style, description=world_look or None, genre=d.genre.replace("-", " "),
                            theme=WORLD_TYPES.get(d.world_type, d.world_type).lower(), mood=d.color_mood or None,
                            notes=" ".join(notes))  # fmt: skip


def impact(d_old: CreativeDirection | None, d_new: CreativeDirection, written: dict[str, bool]) -> list[str]:
    """What changing the direction would leave stale, for an impact review before altering an existing story."""
    if d_old is None:
        d_old = CreativeDirection()
    changed = {k for k in CreativeDirection.model_fields if getattr(d_old, k) != getattr(d_new, k)} - {"preset"}
    notes: list[str] = []
    world_keys = {"world_type", "culture", "culture_note", "world", "genre", "subgenres", "themes", "region", "readers"}
    if changed & world_keys and written.get("world"):
        notes.append("The world is already written; it will not change unless you rewrite it (cast, outline and chapters depend on it).")
    if changed & {"naming", "naming_note", "reserved_names", "name_overrides", "culture"} and written.get("cast"):
        notes.append("The cast is already named; existing names stay unless the naming guide and cast are rewritten.")
    if changed & {"tradition", "pacing", "tone", "drama", "romance", "mystery", "progression", "depth", "choices", "cliffhangers"} and written.get("outline"):
        notes.append("The outline is already planned; the new narrative settings apply to chapters written from now on.")
    if changed & {"writing_style", "writing_notes"} and written.get("chapters"):
        notes.append("Written chapters keep their current English; only new or rewritten chapters use the new style.")
    if changed & {"art_style", "art_note", "character_design", "environment", "color_mood"} and written.get("assets"):
        notes.append("Art already made keeps its look; the art profile changes for images made from now on, which can mix styles.")
    return notes
