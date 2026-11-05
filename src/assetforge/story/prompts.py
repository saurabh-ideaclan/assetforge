"""Prompt templates for each story step. Bump PROMPT_VERSION whenever wording changes: it is part of every
text job's fingerprint, so a reworded step is asked again instead of reusing an answer to the old wording.

Each call carries a compact JSON "story bible" (config, world, cast, outline) instead of a vector store: it is
small enough for every model's context and keeps the steps deterministic and reproducible.
"""

from __future__ import annotations

import json
from typing import Any

from assetforge.story.models import Cast, Outline, PlannedScene, Scene, StoryConfig, World

PROMPT_VERSION = 1


def system(config: StoryConfig) -> str:
    parts = [
        "You are the lead writer of a branching visual novel. You write vivid, specific, character-driven fiction "
        "that reads well one line at a time on a textbox.",
        f"Write in {config.language}. Audience/rating: {config.audience}; keep content suitable for it.",
        "Return only the requested JSON. Ids are lowercase snake_case (a-z, 0-9, _), starting with a letter.",
    ]
    if config.tone:
        parts.append(f"Tone: {config.tone}.")
    if config.behaviour:
        parts.append(f"Author style rules (always follow): {config.behaviour}")
    return "\n".join(parts)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bible(config: StoryConfig, world: World | None = None, cast: Cast | None = None, *, full_cast: bool = True) -> str:
    data: dict[str, Any] = {"title": config.title, "premise": config.premise, "genre": config.genre}
    if world:
        data["world"] = world.model_dump(exclude={"art_direction", "music_direction"})
    if cast:
        if full_cast:
            data["characters"] = [c.model_dump(exclude={"look", "palette"}) for c in cast.characters]
        else:
            data["characters"] = [{"id": c.id, "name": c.name, "role": c.role, "voice": c.voice,
                                   "expressions": c.expressions} for c in cast.characters]  # fmt: skip
        data["locations"] = [{"id": loc.id, "name": loc.name, "times": loc.times} for loc in cast.locations]
    return f"STORY BIBLE (v{PROMPT_VERSION}):\n{_json(data)}"


def world_prompt(config: StoryConfig) -> str:
    return "\n\n".join([
        bible(config),
        "Build the world for this visual novel (like a series bible, but short: every field 1-4 sentences). "
        f"Genre: {config.genre}." + (f" Visual style wanted: {config.art_style}." if config.art_style else ""),
        "art_direction must describe one consistent illustrated look that works for both character sprites on "
        "transparent backgrounds and painted 16:9 backgrounds (medium, palette, lighting, line work).",
    ])  # fmt: skip


def cast_prompt(config: StoryConfig, world: World) -> str:
    n_chars = max(2, min(6, config.scenes // 2 + 1))
    n_locs = max(2, min(8, config.scenes // 2 + 1))
    return "\n\n".join([
        bible(config, world),
        f"Create the cast ({n_chars - 1}-{n_chars + 1} characters) and the locations ({n_locs - 1}-{n_locs + 1}).",
        "Characters: `look` is a precise, reusable visual description for an image model (build, hair, eyes, skin, "
        "outfit, accessories, signature colours); never mention expression, pose or background. `expressions` lists "
        "3-6 facial expressions this character will need, starting with neutral (e.g. neutral, smile, sad, angry, "
        "surprised, worried, smirk, blush).",
        "Locations: `description` is a painted-background brief without people; `times` are the times of day "
        "scenes will happen there; `ambience` says what it sounds like.",
    ])  # fmt: skip


def outline_prompt(config: StoryConfig, world: World, cast: Cast) -> str:
    return "\n\n".join([
        bible(config, world, cast),
        f"Plan the branching story: about {config.scenes} scenes and exactly {config.endings} endings.",
        "Rules: `start` is the first scene id. Every scene has exactly one exit: `next` (a scene id), or `choices` "
        "(2-3 options, each with a `goto` scene id), or `ending` (an ending id). Use 2-4 meaningful choice points; "
        "branches may rejoin. Every scene must be reachable from the start and every ending must be reachable. "
        "Scene `location`, `characters` must use ids from the bible; `time` must be one listed for that location. "
        "`music` is the scene's mood. Declare any `variables` (integers, e.g. trust_mira) that choices will change "
        "or check later, and mention them in summaries where they matter.",
    ])  # fmt: skip


def scene_prompt(config: StoryConfig, world: World, cast: Cast, outline: Outline, plan: PlannedScene,
                 previous: list[str], written_before: Scene | None = None, note: str | None = None) -> str:  # fmt: skip
    exit_rule = (f"End with one `choice` node offering exactly these options in this order: "
                 f"{_json([c.model_dump() for c in plan.choices])} (you may improve the wording, add `set` effects or "
                 f"a `require` condition using declared variables)." if plan.choices else
                 f"End with an `ending` node with ending {plan.ending!r}." if plan.ending else
                 f"End with a `jump` node to {plan.next!r}.")  # fmt: skip
    on_stage = [c for c in cast.characters if c.id in plan.characters]
    parts = [
        bible(config, world, cast, full_cast=False),
        "OUTLINE:\n" + _json({"start": outline.start, "variables": [v.model_dump() for v in outline.variables],
                              "scenes": [{"id": s.id, "title": s.title, "summary": s.summary} for s in outline.scenes],
                              "endings": [e.model_dump() for e in outline.endings]}),  # fmt: skip
        "STORY SO FAR (planned scenes leading here):\n" + ("\n".join(f"- {p}" for p in previous) or "- (this is the opening)"),
        "WRITE THIS SCENE:\n" + _json(plan.model_dump()),
        "On stage: " + _json([{"id": c.id, "name": c.name, "personality": c.personality, "voice": c.voice,
                               "expressions": c.expressions} for c in on_stage]),  # fmt: skip
        "Write it as 12-30 nodes. Node kinds: `narrate` (text), `say` (speaker id, text, optional expression from that "
        "character's list, optional position left|center|right), `show`/`hide` (character id; show may set "
        "expression and position), `sfx` (cue: short snake_case id, text: what it sounds like), `cg` (cg: snake_case "
        "id, text: what a full-screen illustration of a key moment shows; at most one per scene and only for big "
        "moments), `set` (assign: variable changes). Keep each line short (one or two sentences, as on a textbox). "
        "Show characters before they speak. Use expressions to carry emotion. " + exit_rule,
    ]
    if written_before is not None:
        parts.append("CURRENT VERSION OF THE SCENE:\n" + _json({"nodes": [n.model_dump(exclude_none=True) for n in written_before.nodes]}))
    if note:
        parts.append(f"REVISION NOTE (apply it, keep everything else that works): {note}")
    return "\n\n".join(parts)


def check_prompt(config: StoryConfig, world: World, cast: Cast, scenes: list[Scene]) -> str:
    script = [{"id": s.id, "title": s.title, "lines": [_line(n) for n in s.nodes]} for s in scenes]
    return "\n\n".join([
        bible(config, world, cast, full_cast=False),
        "SCRIPT:\n" + _json(script),
        "You are the continuity editor. Find real problems only: contradictions between scenes, characters acting "
        "or speaking out of character, facts introduced and forgotten, choices whose consequences never show, "
        "pacing holes, wrong names. Each issue names its scene id. Then write a one-paragraph summary.",
    ])  # fmt: skip


def _line(node: Any) -> str:
    if node.kind == "say":
        return f"{node.speaker}[{node.expression or ''}]: {node.text}"
    if node.kind == "narrate":
        return node.text or ""
    if node.kind == "choice":
        return "CHOICE: " + " | ".join(f"{c.text} -> {c.goto}" for c in node.choices)
    if node.kind == "jump":
        return f"-> {node.goto}"
    if node.kind == "ending":
        return f"ENDING {node.ending}"
    return f"({node.kind} {node.character or node.cue or node.cg or ''} {node.text or ''})".strip()


def improve_prompt(config: StoryConfig, world: World, cast: Cast, scenes: list[Scene], feedback: str,
                   focus: list[str] | None = None) -> str:  # fmt: skip
    script = [{"id": s.id, "title": s.title, "lines": [_line(n) for n in s.nodes]} for s in scenes]
    return "\n\n".join([
        bible(config, world, cast, full_cast=False),
        "SCRIPT:\n" + _json(script),
        f"FEEDBACK FROM THE AUTHOR:\n{feedback.strip()}",
        ("Only these scenes may change: " + ", ".join(focus) + "." if focus else
         "Change only the scenes that need it (usually 1-6)."),
        "You are the story editor. Turn the feedback into a revision plan: for each scene to rewrite, give a "
        "concrete, self-contained note the scene writer can follow without seeing this feedback (what to add, cut "
        "or change, which lines or beats, which character, what tone). If the feedback is about a character, "
        "revise the scenes where they appear. Scene exits (choices, jumps, endings) stay as planned; you may "
        "change choice wording. Use scene ids from the script.",
    ])  # fmt: skip
