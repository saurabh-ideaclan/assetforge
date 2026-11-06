"""The story data model: config → world → cast → outline → scenes → playable script.

Everything a model writes is one of these Pydantic models (`extra="forbid"`, snake_case ids), so answers are
schema-checked; `validate.py` then checks the story logic (links, reachability, cast usage). The script is
engine-neutral JSON: the demo player reads it directly, and Ink/Ren'Py exporters can be added later.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from assetforge.core.util import slugify

Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]
TimeOfDay = Literal["day", "evening", "night"]
Position = Literal["left", "center", "right"]
Mood = Literal["calm", "happy", "playful", "romantic", "tense", "sad", "mysterious", "eerie", "heroic", "triumphant", "dark"]
EndingKind = Literal["good", "bad", "neutral", "true"]
NodeKind = Literal["say", "narrate", "show", "hide", "sfx", "cg", "set", "choice", "jump", "ending"]
TERMINAL: frozenset[str] = frozenset({"choice", "jump", "ending"})


def to_id(text: str) -> str:
    value = slugify(text, "_") or "item"
    return value if value[0].isalpha() else f"x_{value}"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- config (written by `story init`)


class StoryConfig(Strict):
    title: str = Field(min_length=1)
    title_from_writer: bool = Field(False, description="No title was given: use the one the writer suggests")
    slug: str = Field(description="kebab-case; keys are vn/<slug>/…")
    premise: str = Field(min_length=3)
    genre: str = "drama"
    audience: str = Field("teen", description="Rating/age group the writing must suit")
    language: str = "English"
    tone: str = ""
    behaviour: str = Field("", description="Author voice/style rules for every step (storycraftr's behaviour file)")
    scenes: int = Field(8, ge=2, le=60, description="Target number of scenes")
    endings: int = Field(2, ge=1, le=6)
    art_style: str | None = Field(None, description="Art preset (`assetforge art presets`) or free style text")
    project: str | None = Field(None, description="AssetForge project owning the assets (default vn-<slug>)")
    provider: str | None = Field(None, description="Text provider (claude-cli, codex-cli, gemini-cli, …) or auto")
    model: str | None = None

    @property
    def asset_project(self) -> str:
        return self.project or f"vn-{self.slug}"


# ---------------------------------------------------------------------- world + cast


class World(Strict):
    title: str = Field("", description="A short, evocative title for the novel (2-5 words)")
    logline: str = Field(description="One sentence hook")
    setting: str = Field(description="Where and when, in 2-3 sentences")
    history: str = Field(description="Backstory that matters to the plot, short")
    culture: str = Field(description="Society, customs, how people talk")
    rules: str = Field(description="Technology/magic/what is possible, and its limits")
    themes: list[str] = Field(description="2-4 themes")
    art_direction: str = Field(description="The visual look of the novel in 2-3 sentences: medium, palette, lighting, "
                                           "line work. Must suit visual-novel sprites and painted backgrounds.")  # fmt: skip
    music_direction: str = Field(description="Instruments and feel of the soundtrack in one sentence")


class Character(Strict):
    id: Id
    name: str
    role: str = Field(description="protagonist, love interest, rival, mentor, antagonist…")
    age: str = ""
    personality: str
    look: str = Field(description="Visual description for the sprite: build, hair, eyes, skin, outfit, accessories. "
                                  "Concrete and consistent; no expression or pose.")  # fmt: skip
    palette: list[str] = Field(default_factory=list, description="2-4 signature colours as words or hex")
    voice: str = Field("", description="How they talk (diction, verbal tics)")
    expressions: list[Id] = Field(description="Facial expressions the story needs; first is neutral")

    @field_validator("expressions")
    @classmethod
    def _neutral_first(cls, value: list[str]) -> list[str]:
        rest = [e for e in dict.fromkeys(value) if e != "neutral"]
        return ["neutral", *rest][:8]


class Location(Strict):
    id: Id
    name: str
    description: str = Field(description="Visual description of the place for a painted background, no people")
    times: list[TimeOfDay] = Field(description="Times of day scenes happen here")
    ambience: str = Field("", description="What it sounds like (wind, crowd, machines…), one sentence")

    @field_validator("times")
    @classmethod
    def _times(cls, value: list[str]) -> list[str]:
        ordered = [t for t in ("day", "evening", "night") if t in value]
        return ordered or ["day"]


class Cast(Strict):
    characters: list[Character] = Field(min_length=1)
    locations: list[Location] = Field(min_length=1)


# ---------------------------------------------------------------------- outline


class Variable(Strict):
    name: Id
    initial: int = 0
    meaning: str = Field("", description="What it tracks, e.g. trust with Mira (0-3)")


class PlannedChoice(Strict):
    text: str
    goto: Id


class PlannedScene(Strict):
    id: Id
    title: str
    location: Id
    time: TimeOfDay = "day"
    characters: list[Id] = Field(default_factory=list, description="Character ids on screen")
    summary: str = Field(description="What happens, 2-4 sentences")
    music: Mood = "calm"
    next: Id | None = Field(None, description="Following scene when there is no choice")
    choices: list[PlannedChoice] = Field(default_factory=list, description="Player choice at the end (2-3), if any")
    ending: Id | None = Field(None, description="Ending id if the story ends here")


class Ending(Strict):
    id: Id
    title: str
    kind: EndingKind = "neutral"
    summary: str


class Outline(Strict):
    start: Id
    variables: list[Variable] = Field(default_factory=list)
    scenes: list[PlannedScene] = Field(min_length=1)
    endings: list[Ending] = Field(min_length=1)


# ---------------------------------------------------------------------- script


class Condition(Strict):
    var: Id
    op: Literal["==", "!=", ">=", "<=", ">", "<"] = ">="
    value: int = 1


class Assign(Strict):
    var: Id
    op: Literal["set", "add"] = "add"
    value: int = 1


class Choice(Strict):
    text: str
    goto: Id
    set: list[Assign] = Field(default_factory=list)
    require: Condition | None = Field(None, description="Only offered when this holds")


class Node(Strict):
    """One step of a scene. Fields used per kind:
    say: speaker, text, expression?, position?   narrate: text   show: character, expression?, position?
    hide: character   sfx: cue, text (what it sounds like)   cg: cg, text (what the illustration shows)
    set: assign   choice: choices   jump: goto   ending: ending"""

    kind: NodeKind
    speaker: Id | None = None
    character: Id | None = None
    expression: Id | None = None
    position: Position | None = None
    text: str | None = None
    cue: Id | None = None
    cg: Id | None = None
    assign: list[Assign] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    goto: Id | None = None
    ending: Id | None = None


class Scene(Strict):
    id: Id
    title: str
    location: Id
    time: TimeOfDay = "day"
    music: Mood = "calm"
    nodes: list[Node] = Field(min_length=1)


class SceneDraft(Strict):
    """What the model writes for one scene (the scene header comes from the outline)."""

    nodes: list[Node] = Field(min_length=3)


class Script(Strict):
    """The assembled, playable story (script.json)."""

    format: Literal["assetforge-vn/1"] = "assetforge-vn/1"
    title: str
    slug: str
    language: str = "English"
    start: Id
    variables: list[Variable] = Field(default_factory=list)
    characters: list[Character]
    locations: list[Location]
    endings: list[Ending]
    scenes: list[Scene]


# ---------------------------------------------------------------------- review


class Issue(Strict):
    level: Literal["error", "warning"]
    code: str
    where: str
    message: str


class ContinuityReport(Strict):
    """What the continuity pass (`story check`) returns."""

    issues: list[ContinuityIssue] = Field(default_factory=list)
    summary: str = ""


class ContinuityIssue(Strict):
    scene: Id
    severity: Literal["high", "medium", "low"] = "medium"
    problem: str
    suggestion: str = ""


ContinuityReport.model_rebuild()


class SceneEdit(Strict):
    scene: Id
    note: str = Field(description="Concrete, self-contained instruction for rewriting this scene")


class RevisionPlan(Strict):
    """What `story improve` asks for: which scenes to rewrite, and how, to address the feedback."""

    summary: str = Field(description="One or two sentences: how the feedback will be addressed")
    edits: list[SceneEdit] = Field(default_factory=list)
