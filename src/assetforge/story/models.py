"""The story data model: config → world → cast → outline → scenes → playable script.

Everything a model writes is one of these Pydantic models (`extra="forbid"`, snake_case ids), so answers are
schema-checked; `validate.py` then checks the story logic (links, reachability, cast usage). The script is
engine-neutral JSON: the demo player reads it directly, and Ink/Ren'Py exporters can be added later.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from assetforge.core.util import slugify
from assetforge.llm.plans import ExecutionPlan
from assetforge.story.direction import CreativeDirection

Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]
TimeOfDay = Literal["day", "evening", "night"]
Position = Literal["left", "center", "right"]
Mood = Literal["calm", "happy", "playful", "romantic", "tense", "sad", "mysterious", "eerie", "heroic", "triumphant", "dark",
               "silence"]  # silence: the music stops (deliberate quiet); no music asset is made for it
EndingKind = Literal["good", "bad", "neutral", "true"]
NodeKind = Literal["say", "narrate", "show", "hide", "sfx", "cg", "set", "place", "music", "choice", "branch", "jump",
                   "ending", "ambience"]  # fmt: skip
TERMINAL: frozenset[str] = frozenset({"choice", "branch", "jump", "ending"})  # kinds that *can* end a scene
VariableKind = Literal["relationship", "personality", "resource", "flag", "world"]


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
    scenes: int = Field(8, ge=2, le=60, description="Target number of scenes (each scene is one chapter)")
    chapter_words: int = Field(1500, ge=200, le=4000, description="Target words per chapter (benchmark ~1,500-2,000)")
    chapter_choices: int = Field(6, ge=1, le=15, description="Target choices per chapter (benchmark 6-8)")
    endings: int = Field(2, ge=1, le=6)
    art_style: str | None = Field(None, description="Art preset (`assetforge art presets`) or free style text")
    project: str | None = Field(None, description="AssetForge project owning the assets (default vn-<slug>)")
    provider: str | None = Field(None, description="Text provider (claude-cli, codex-cli, gemini-cli, …) or auto")
    model: str | None = None
    direction: CreativeDirection | None = Field(None, description="Creative direction (audience, genre, world, naming, "
                                                                  "narrative style, art). None: the pre-direction behaviour")  # fmt: skip
    ai: ExecutionPlan | None = Field(None, description="This story's AI execution plan (overrides the library plan)")

    @property
    def asset_project(self) -> str:
        return self.project or f"vn-{self.slug}"


class NamingGuide(Strict):
    """How people and places are named in this world, written once (after the world, before the cast) and kept."""

    tradition: str = Field(description="The naming system in one or two sentences (origin, sound, structure)")
    name_order: str = Field("given family", description="given family | family given | single name")
    given_names: list[str] = Field(description="12-16 example given names across genders, in the world's style")
    family_names: list[str] = Field(default_factory=list, description="6-10 example family or house names")
    place_names: list[str] = Field(default_factory=list, description="6-10 example place names (cities, regions, landmarks)")
    titles: list[str] = Field(default_factory=list, description="Titles and honorifics with what they mean")
    patterns: str = Field("", description="Sound and structure rules (syllables, endings, what is avoided)")
    avoid: list[str] = Field(default_factory=list, description="Styles or real-world names that must not appear")
    reserved: list[str] = Field(default_factory=list, description="Names fixed by the operator, used exactly as given")


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
    """A hidden stat. Choices change it; later lines, options, branches and endings read it (delayed branching)."""

    name: Id
    initial: int = 0
    meaning: str = Field("", description="What it tracks, e.g. trust with Mira (0-3)")
    kind: VariableKind | None = Field(None, description="relationship (per character), personality (an axis such as "
                                                        "bold vs careful), resource, flag (0/1) or world")  # fmt: skip
    character: Id | None = Field(None, description="For a relationship: the character it is about")
    min: int | None = Field(None, description="Lowest value (the player clamps to it)")
    max: int | None = Field(None, description="Highest value (the player clamps to it)")


class Condition(Strict):
    var: Id
    op: Literal["==", "!=", ">=", "<=", ">", "<"] = ">="
    value: int = 1


class Branch(Strict):
    when: list[Condition] = Field(min_length=1)
    goto: Id


class PlannedChoice(Strict):
    text: str
    goto: Id


class PlannedScene(Strict):
    id: Id
    title: str
    chapter: Id | None = Field(None, description="Chapter this scene belongs to (several scenes can share one)")
    location: Id
    time: TimeOfDay = "day"
    characters: list[Id] = Field(default_factory=list, description="Character ids on screen")
    summary: str = Field(description="What happens, 2-4 sentences")
    music: Mood = "calm"
    next: Id | None = Field(None, description="Following scene when there is no choice")
    choices: list[PlannedChoice] = Field(default_factory=list, description="Player choice at the end (2-3), if any")
    branches: list[Branch] = Field(default_factory=list, description="Gated exits checked in order (e.g. to the "
                                   "ending the player earned); `next` is the fallback")  # fmt: skip
    ending: Id | None = Field(None, description="Ending id if the story ends here")
    hook: str = Field("", description="The cliffhanger or question this chapter ends on")
    callback: str = Field("", description="Which earlier choice or stat this chapter pays off, and how")

    def exits(self) -> list[str]:
        return [c.goto for c in self.choices] + [b.goto for b in self.branches] + ([self.next] if self.next else [])


class Ending(Strict):
    id: Id
    title: str
    kind: EndingKind = "neutral"
    summary: str
    requires: list[Condition] = Field(default_factory=list, description="What the player must have built up to earn "
                                      "this ending (all must hold); a `branch` node checks it at the climax")  # fmt: skip


class Chapter(Strict):
    """The unit players read (6-8 minutes); it can hold several scenes."""

    id: Id
    title: str
    hook: str = Field("", description="Why the player will want the next chapter")


class Outline(Strict):
    start: Id
    variables: list[Variable] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list, description="Chapters in reading order (empty: every scene "
                                    "is its own chapter)")  # fmt: skip
    scenes: list[PlannedScene] = Field(min_length=1)
    endings: list[Ending] = Field(min_length=1)


# ---------------------------------------------------------------------- script


class Assign(Strict):
    var: Id
    op: Literal["set", "add"] = "add"
    value: int = 1


class Line(Strict):
    """One line of a choice's reaction: spoken (speaker set) or narration (speaker null)."""

    speaker: Id | None = None
    text: str
    expression: Id | None = None


class Choice(Strict):
    text: str
    goto: Id | None = Field(None, description="Scene to go to; null = play the reaction, then continue this scene")
    set: list[Assign] = Field(default_factory=list)
    require: Condition | None = Field(None, description="Only offered when this holds")
    reaction: list[Line] = Field(default_factory=list, description="2-6 lines answering this option before the scene "
                                 "continues (in-chapter choices)")  # fmt: skip


class Node(Strict):
    """One step of a scene. Fields used per kind:
    say: speaker, text, expression?, position?   narrate: text   show: character, expression?, position?
    hide: character   sfx: cue, text (what it sounds like)   cg: cg, text (what the illustration shows)
    set: assign   place: location, time?, music?, text? (move within the chapter; clears the stage)
    music: music (change the mood)   choice: choices (all with goto = ends the scene; none = in-chapter choice)
    branch: branches + goto (first branch whose conditions hold, else goto)   jump: goto   ending: ending
    Any node may have `when`: it only plays when every condition holds (callbacks to earlier choices)."""

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
    location: Id | None = None
    time: TimeOfDay | None = None
    music: Mood | None = None
    branches: list[Branch] = Field(default_factory=list)
    when: list[Condition] = Field(default_factory=list, description="Only play this node when all of these hold")
    # audio direction (all optional; older scripts have none of these)
    ambience: str | None = Field(None, description="kind ambience: a location id, a catalog ambience scene, or 'none'")
    loop: bool = Field(False, description="sfx: keep playing until a node with `stop` names this cue")
    stop: Id | None = Field(None, description="sfx: stop the looping cue with this id (no new sound)")
    fade: float | None = Field(None, description="music/ambience/stop: transition seconds")
    level: float | None = Field(None, description="sfx/ambience: relative level 0.1-1")
    why: str = Field("", description="The audio cue's narrative purpose (set by the audio direction)")


def is_exit(node: Node) -> bool:
    """Does this node end its scene? A choice does only when its options lead to other scenes."""
    if node.kind == "choice":
        return any(c.goto for c in node.choices)
    return node.kind in ("branch", "jump", "ending")


def node_exits(node: Node) -> list[str]:
    if node.kind == "choice":
        return [c.goto for c in node.choices if c.goto]
    if node.kind == "branch":
        return [b.goto for b in node.branches] + ([node.goto] if node.goto else [])
    if node.kind == "jump" and node.goto:
        return [node.goto]
    return []


def reads(node: Node) -> list[str]:
    """Variables this node checks."""
    out = [c.var for c in node.when]
    out += [c.require.var for c in node.choices if c.require]
    out += [c.var for b in node.branches for c in b.when]
    return out


def spoken(node: Node) -> list[Node]:
    """The node as lines on screen: a choice contributes its reaction lines (as say/narrate nodes)."""
    if node.kind != "choice":
        return [node]
    return [Node(kind="say" if r.speaker else "narrate", speaker=r.speaker, text=r.text, expression=r.expression)
            for c in node.choices for r in c.reaction]  # fmt: skip


class Scene(Strict):
    id: Id
    title: str
    chapter: Id | None = None
    location: Id
    time: TimeOfDay = "day"
    music: Mood = "calm"
    nodes: list[Node] = Field(min_length=1)


class Staged(NamedTuple):
    node: Node
    location: str
    time: str
    music: str


def walk(scene: Scene) -> Iterator[Staged]:
    """Every node with the place, time and music in effect when it plays (`place`/`music` nodes move them);
    a choice is followed by its reaction lines."""
    location, time, music = scene.location, scene.time, scene.music
    for node in scene.nodes:
        if node.kind == "place" and node.location:
            location, time = node.location, node.time or time
            music = node.music or music
        elif node.kind == "music" and node.music:
            music = node.music
        yield Staged(node, location, time, music)
        if node.kind == "choice":
            for line in spoken(node):
                yield Staged(line, location, time, music)


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
    chapters: list[Chapter] = Field(default_factory=list)
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
