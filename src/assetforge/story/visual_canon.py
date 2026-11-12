"""The visual canon: what the story's world looks like, so no image is painted against its logic.

A background prompt used to carry one location's description and nothing about the world around it, and that
description was written before the chapters. The owner's second story showed both faults at once (2026-11-12): the gap
"between two carriages" came back as two trains side by side, a rescued slum carriage as a plush lounge (the location
had been described before the chapters decided what it was), and the windows of a dead dust continent showed cities,
forests and snow. The canon is drafted **after** the chapters are written, from the world, the direction's canon and
how each place is actually used in the script:

- `world`, `outside`: what is true everywhere, and what is seen beyond any place (through windows, at the horizon);
- `always` / `never`: short rules appended to every background, illustration and title prompt;
- per location: the description corrected to the story's use, the `geometry` a painter gets wrong, its own `never`;
- `props`: recurring objects and effects with one look (the blue of Charge, a coupler's hook).

`assets.manifest(canon=…)` uses it for every `bg`, `bg_time`, `cg` and title request; `story/visual_check.py` judges
finished images against it. Stored in `visual_canon.json` (proposed until approved, like an acting profile).
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from assetforge.story.models import Script, walk
from assetforge.story.project import StoryProject, write_atomic

FILE = "visual_canon.json"
MAX_USAGE = 14  # excerpts per location handed to the writer


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationCanon(Strict):
    id: str
    description: str = Field(description="What a painter must show: the place as the written chapters use it, concrete and physical, no people")
    geometry: str = Field("", description="Spatial facts a painter is likely to get wrong, stated flatly (e.g. 'one train on one track; the two carriages are joined end to end')")
    never: list[str] = Field(default_factory=list, description="Things that must not appear in this place")


class Prop(Strict):
    name: str
    look: str = Field(description="How it always looks, one sentence")


class CanonAnswer(Strict):
    world: str = Field(description="Two or three sentences: what this world looks like everywhere (materials, light, wealth, state of repair)")
    outside: str = Field(description="What is seen beyond any place: through windows, past open doors, at the horizon")
    always: list[str] = Field(default_factory=list, description="Up to 6 short visual rules true in every image")
    never: list[str] = Field(default_factory=list, description="Up to 8 things that contradict the world and must never be painted")
    locations: list[LocationCanon] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list, description="Up to 6 recurring objects or effects with a fixed look")


class VisualCanon(CanonAnswer):
    state: Literal["proposed", "approved"] = "proposed"
    note: str = ""
    updated_at: str = ""


def load(project: StoryProject) -> VisualCanon | None:
    path = project.root / FILE
    try:
        return VisualCanon.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def save(project: StoryProject, canon: VisualCanon) -> None:
    canon.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_atomic(project.root / FILE, canon.model_dump_json(indent=1) + "\n")


def usage(script: Script, location: str) -> list[str]:
    """How the written chapters use a place: the text of every arrival there and the narration that follows it."""
    out: list[str] = []
    for scene in script.scenes:
        follow = 0
        for i, st in enumerate(walk(scene)):
            node = st.node
            if st.location != location:
                follow = 0
                continue
            arrival = (node.kind == "place" and node.text) or (i == 0 and node.text)
            if arrival:
                out.append(f"[{scene.id}] {node.text}")
                follow = 2
            elif follow and node.kind == "narrate" and node.text:
                out.append(f"[{scene.id}] {node.text}")
                follow -= 1
    seen: set[str] = set()
    unique = [t for t in out if not (t in seen or seen.add(t))]
    return unique[:MAX_USAGE]


def prompt(project: StoryProject, script: Script) -> str:
    world = project.load("world") if project.has("world") else None
    direction = project.config.direction
    custom = direction.custom.canon if direction and direction.custom else ""
    parts = [
        "You are the art director's continuity editor for an illustrated visual novel. Painters (image models) will get one "
        "prompt per background and per illustration. They know nothing about this world, and they fill every gap with "
        "whatever looks pretty: a city on the horizon of a dead continent, a second train beside a train that is the only "
        "one in the world, a lounge where the story has a slum. Write the VISUAL CANON that prevents that.",
        "Rules for your answer:\n"
        "- `world`, `outside`, `always`, `never`: physical and concrete, short. `never` names the specific wrong things a "
        "painter would add here (not generic advice).\n"
        "- For EVERY location below: rewrite its description so it shows the place AS THE CHAPTERS USE IT. The original "
        "description was written before the chapters; where the excerpts contradict it, the excerpts win. No people, "
        "no named characters, no plot. State `geometry` flatly where a painter could misread the layout (how many "
        "vehicles, what is joined to what, which way the view looks, inside or outside). For anything long or moving (a "
        "train, a ship, a road, a corridor) say WHERE THE CAMERA STANDS and WHICH WAY THE LONG AXIS RUNS IN THE FRAME (left "
        "to right, or away from the viewer), and what that view therefore hides: a part that joins two things along the "
        "axis cannot be drawn sideways in a view that looks along it. Say that vehicles and rooms are solid and closed "
        "unless the story opens them. Give the place its own `never`.\n"
        "- Motion: blur only what is close to a moving viewer; the distance stays sharp.\n"
        "- `props`: recurring objects or effects that must look the same everywhere.\n"
        "- Never ask for text, lettering, signs with words or numbers in an image.",
        f"WORLD\nSetting: {world.setting if world else ''}\nRules: {world.rules if world else ''}\n"
        f"Art direction: {world.art_direction if world else ''}",
    ]
    if custom:
        parts.append(f"CANON FROM THE DIRECTION\n{custom}")
    blocks = []
    for loc in script.locations:
        lines = "\n".join(f"  - {t}" for t in usage(script, loc.id)) or "  (not used in the written chapters)"
        blocks.append(f"LOCATION {loc.id} ({loc.name}), times {', '.join(loc.times)}\nOriginal description: {loc.description}\n"
                      f"How the chapters use it:\n{lines}")
    parts.append("\n\n".join(blocks))
    return "\n\n".join(parts)


def offline(script: Script) -> Any:
    def build(_: Any = None) -> dict[str, Any]:
        return {"world": "A plain, consistent world.", "outside": "Open sky and plain ground.",
                "always": ["one consistent place per image"], "never": ["text or lettering"],
                "locations": [{"id": loc.id, "description": loc.description, "geometry": "", "never": []} for loc in script.locations],
                "props": []}  # fmt: skip

    return build


def draft(ctx: Any, *, fresh: bool = False) -> VisualCanon:
    """Draft the canon from the world and the written script (task `visual.canon`). Locations the writer left out
    keep their cast description; ids it invented are dropped."""
    from assetforge.errors import NotFoundError
    from assetforge.story.steps import _ask

    project = ctx.project
    if not project.has("script"):
        raise NotFoundError("No script yet: the canon is drafted from the written chapters")
    script: Script = project.load("script")
    answer: CanonAnswer = _ask(ctx, "visual.canon", prompt(project, script), CanonAnswer, offline(script), fresh=fresh)
    known = {loc.id: loc for loc in script.locations}
    got = {lc.id: lc for lc in answer.locations if lc.id in known}
    locations = [got.get(i) or LocationCanon(id=i, description=known[i].description) for i in known]
    canon = VisualCanon(world=answer.world, outside=answer.outside, always=answer.always[:6], never=answer.never[:8],
                        locations=locations, props=answer.props[:6])  # fmt: skip
    save(project, canon)
    return canon


def approve(project: StoryProject, note: str = "") -> VisualCanon | None:
    canon = load(project)
    if canon:
        canon.state, canon.note = "approved", note
        save(project, canon)
    return canon


def location_text(canon: VisualCanon | None, loc: Any, *, illustration: bool = False) -> str:
    """The description a background or illustration gets for this place. A background takes the place's camera,
    layout and never-list; an illustration has its own camera (its event text), so it takes the place only as
    materials and mood: handing it the background's viewpoint made the model paint the background again."""
    lc = next((x for x in canon.locations if x.id == loc.id), None) if canon else None
    if lc is None:
        return loc.description
    text = lc.description.rstrip()
    if illustration:
        return ("(what this place is made of and how it feels; IGNORE any camera position or viewpoint in these sentences, "
                "the event description sets the camera) " + text)
    if lc.geometry:
        text += f" Layout, exactly: {lc.geometry.rstrip('.')}."
    if lc.never:
        text += " Not in this place: " + "; ".join(n.rstrip(".") for n in lc.never) + "."
    return text


def world_block(canon: VisualCanon | None) -> str:
    """The world rules appended to every background, illustration and title prompt ('' without a canon)."""
    if canon is None:
        return ""
    parts = [f"The world: {canon.world.rstrip('.')}." if canon.world else "",
             f"Beyond this place (windows, open doors, horizon): {canon.outside.rstrip('.')}." if canon.outside else "",
             ("Always: " + "; ".join(a.rstrip(".") for a in canon.always) + ".") if canon.always else "",
             ("Never paint: " + "; ".join(n.rstrip(".") for n in canon.never) + ".") if canon.never else "",
             ("Fixed looks, ONLY for things the description itself puts in the picture (do not add them): "
              + "; ".join(f"{p.name}: {p.look.rstrip('.')}" for p in canon.props) + ".") if canon.props else ""]  # fmt: skip
    return " ".join(p for p in parts if p)
