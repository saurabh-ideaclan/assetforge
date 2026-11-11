"""Prompt templates for each story step. Bump PROMPT_VERSION whenever wording changes: it is part of every
text job's fingerprint, so a reworded step is asked again instead of reusing an answer to the old wording.

Each call carries a compact JSON "story bible" (config, world, cast, outline) instead of a vector store: it is
small enough for every model's context and keeps the steps deterministic and reproducible.
"""

from __future__ import annotations

import json
import math
from typing import Any

from assetforge.story import direction as creative
from assetforge.story.models import Cast, NamingGuide, Outline, PlannedScene, Scene, StoryConfig, World

PROMPT_VERSION = 5


def system(config: StoryConfig) -> str:
    parts = [
        "You are the lead writer of a branching visual novel. You write vivid, specific, character-driven fiction "
        "that reads well one line at a time on a textbox.",
        f"Write in {config.language}. Audience/rating: {config.audience}; keep content suitable for it.",
        "Return only the requested JSON. Ids are lowercase snake_case (a-z, 0-9, _), starting with a letter.",
    ]
    if config.direction is not None:
        parts += creative.system_rules(config.direction)
    if config.tone:
        parts.append(f"Tone: {config.tone}.")
    if config.behaviour:
        parts.append(f"Author style rules (always follow): {config.behaviour}")
    return "\n".join(parts)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bible(config: StoryConfig, world: World | None = None, cast: Cast | None = None, *, full_cast: bool = True) -> str:
    data: dict[str, Any] = {"title": config.title, "premise": config.premise, "genre": config.genre}
    if config.direction is not None:
        data["direction"] = creative.summary(config.direction)
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
    blocks = [
        bible(config),
        "Build the world for this visual novel (like a series bible, but short: every field 1-4 sentences). "
        f"Genre: {config.genre}." + (f" Visual style wanted: {config.art_style}." if config.art_style else ""),
        "title: a short, evocative title (2-5 words) that fits the premise. "
        "art_direction must describe one consistent illustrated look that works for both character sprites on "
        "transparent backgrounds and painted 16:9 backgrounds (medium, palette, lighting, line work).",
    ]  # fmt: skip
    if config.direction is not None:
        blocks.append(creative.world_block(config.direction))
        blocks.append("art_direction follows the visual direction above (it is independent of the narrative influence); "
                      "the world's culture and names are not the art style.")  # fmt: skip
    return "\n\n".join(blocks)


def names_prompt(config: StoryConfig, world: World) -> str:
    d = config.direction or creative.CreativeDirection()
    return "\n\n".join([
        bible(config, world),
        "Write the NAMING GUIDE for this world before any character is created. It is the single source of names for "
        "the whole story: the cast, places and every later chapter follow it.",
        creative.names_block(d),
        "Give 12-16 example given names (mixed genders), 6-10 family or house names, 6-10 place names, the titles and "
        "honorifics the hierarchy uses (with meaning), the sound/structure patterns, and what to avoid. Examples must be "
        "original and consistent with each other; include every reserved name in `reserved`.",
    ])  # fmt: skip


def cast_prompt(config: StoryConfig, world: World, names: NamingGuide | None = None) -> str:
    n_chars = max(2, min(6, config.scenes // 2 + 1))
    n_locs = max(2, min(8, config.scenes // 2 + 1))
    blocks = [
        bible(config, world),
        f"Create the cast ({n_chars - 1}-{n_chars + 1} characters) and the locations ({n_locs - 1}-{n_locs + 1}).",
        "Characters: `look` is a precise, reusable visual description for an image model (build, hair, eyes, skin, "
        "outfit, accessories, signature colours); never mention expression, pose or background. `expressions` lists "
        "3-6 facial expressions this character will need, starting with neutral (e.g. neutral, smile, sad, angry, "
        "surprised, worried, smirk, blush).",
        "Locations: `description` is a painted-background brief without people; `times` are the times of day "
        "scenes will happen there; `ambience` says what it sounds like.",
    ]  # fmt: skip
    if names is not None:
        blocks.append("NAMING GUIDE (every name must follow it; use its examples or new names in the same patterns; "
                      "reserved names exactly as given):\n" + _json(names.model_dump(exclude_none=True)))  # fmt: skip
    if config.direction is not None:
        d = config.direction
        if d.name_overrides:
            blocks.append("Fixed names by role or id: " + "; ".join(f"{k} = {v}" for k, v in d.name_overrides.items()) + ".")
        if d.custom.canon.strip():
            blocks.append("ESTABLISHED CANON (every character it names must be in the cast with that name and role, even "
                          f"beyond the count above; invent nobody who contradicts it): {d.custom.canon.strip()}")  # fmt: skip
        if d.custom.requirements.strip():
            blocks.append(f"REQUIREMENTS: {d.custom.requirements.strip()}")
        blocks.append("The cast needs an antagonist (or rival) with an agenda of their own, and every supporting character "
                      "a want that can collide with the protagonist's. `look` follows the world's culture and the visual "
                      "direction, not any real country's present-day fashion unless the world is contemporary.")  # fmt: skip
    return "\n\n".join(blocks)


def season_arc(chapters: int) -> str:
    """Where each beat of a season falls for this many chapters (scaled from a 10-chapter arc)."""
    beats = [(0.0, "establish the world, the cast and the stats"), (0.25, "the first real test"),
             (0.45, "the midpoint reversal"), (0.55, "consequences of earlier choices come back"),
             (0.75, "the crisis"), (0.85, "the climax choices"), (1.0, "the endings, gated by what the player built up")]
    by_chapter: dict[int, list[str]] = {}
    for share, beat in beats:
        by_chapter.setdefault(max(1, min(chapters, round(1 + share * (chapters - 1)))), []).append(beat)
    return "; ".join(f"chapter {n}: {', then '.join(b)}" for n, b in sorted(by_chapter.items()))


def outline_prompt(config: StoryConfig, world: World, cast: Cast) -> str:
    main = [c for c in cast.characters if c.role.lower() not in ("protagonist", "player", "main character")][:3]
    blocks = [
        bible(config, world, cast),
        f"Plan the branching story as about {config.scenes} scenes and exactly {config.endings} endings. Each scene is "
        f"one chapter the player reads in 6-8 minutes (~{config.chapter_words} words, ~{config.chapter_choices} "
        "in-chapter choices; the chapters are written later, you only plan them).",
        f"Season arc: {season_arc(max(2, config.scenes - config.endings + 1))}.",
        "Stats (`variables`, all hidden from the player, integers with `kind`, `min`, `max`, `meaning`): one "
        "`relationship` per main character (`character` = their id; e.g. trust_<id>, 0-5) for "
        + (", ".join(c.id for c in main) or "the main characters") +
        "; 2-3 `personality` axes (e.g. bold, from -3 careful to +3 bold); one `resource` or `world` variable; "
        "`flag`s (0/1) for key facts the player learns or does. Plan where each is set and where it is read again.",
        "Exits: `start` is the first scene. Every scene has exactly one exit: `next`, or `choices` (2-3 options, each "
        "with `goto`: a choice that changes which scene comes next, at least every other chapter; the options must lead "
        "to DIFFERENT scenes, otherwise use `next`), or `branches` "
        "(gated exits: `when` conditions on stats, checked in order, AND `next` set to the fallback scene for when no "
        "condition holds: always set it; use them at the climax to reach the ending the player earned), or `ending` (the ending's id from `endings`, not its kind). Branches may rejoin. Every scene and ending "
        "must be reachable. Endings: give each a `requires` list (the stats it needs; the fallback ending none).",
        "For each scene also write `hook` (the cliffhanger or open question it ends on) and, from the third chapter "
        "on, `callback` (which earlier choice or stat it pays off, and how). Mention stats in summaries where they "
        "matter. `location`, `characters` use ids from the bible; `time` must be listed for that location; `music` is "
        "the mood. Leave `chapters` empty.",
    ]  # fmt: skip
    if config.direction is not None:
        blocks.append(creative.plan_block(config.direction))
    return "\n\n".join(blocks)


BEATS = ["hook: drop the player into a moment (3-6 boxes)", "first in-chapter choice by box 8",
         "setup", "complication", "a midpoint test: a choice that really costs something, with a reaction",
         "a callback: a line (`when`) or option (`require`) that reads a stat set in an earlier chapter",
         "escalation", "the cliffhanger the chapter ends on"]  # fmt: skip


def part_beats(part: int, parts: int) -> list[str]:
    """The beat template split across the calls that write one chapter."""
    size = math.ceil(len(BEATS) / parts)
    return BEATS[(part - 1) * size: part * size]


CHOICE_RULES = (
    "CHOICES. Most choices are in-chapter choices: a `choice` node whose options have NO `goto` (null); each option "
    "has 2-4 `reaction` lines (speaker id or null for narration, text, expression) and then the chapter carries on. "
    "Give 3 options on different axes (e.g. warm / honest / guarded, or bold / careful / clever), each worth choosing "
    "and in character for the protagonist; most options `set` a declared stat (relationship +1/-1, a personality "
    "axis, a flag). Mix flavour choices (how you say it), relationship choices, establishing choices (who you are) "
    "and tests (from chapter 3). Gate an option with `require` only at extremes (e.g. trust >= 4 or bold <= -2). "
    "Space choices about 200-300 words apart, never more than ~350. Before every choice the reader must already know "
    "what is being decided, why it matters to the protagonist now and the immediate trade-off (what each option costs "
    "or risks), from what the scene has shown; never require an unexplained rule to choose. After the choice, the "
    "reactions show the immediate consequence. Callbacks: lines with `when` conditions on stats set in earlier "
    "chapters, so the player sees their choices mattered."
)
LINES = (
    "LINES. Node kinds: `narrate` (text), `say` (speaker id, text, expression from that character's list, optional "
    "position left|center|right), `show`/`hide` (character id; show may set expression and position), `place` "
    "(location id, optional time and music, optional text: move to another place within the chapter; clears the "
    "stage), `music` (mood), `sfx` (cue: snake_case id, text: what it sounds like), `cg` (cg: snake_case id, text: a "
    "full-screen illustration of a big moment; at most one per chapter), `set` (assign). A box is one to three "
    "sentences, up to about 45 words; vary the length, and when one more sentence makes the moment clearer, write it. "
    "A reaction or expression on most lines. Show characters before they speak. Perspective, always: second person, "
    "present tense for narration ('You brace the shaft'); spoken lines carry the speaker, the protagonist's too."
)
CLARITY_RULES = (
    "CLARITY WITHOUT SIMPLIFICATION. The reader has not read the bible. (1) A fictional term, rule or institution is "
    "understood the first time the scene depends on it, through what people do and say (a clerk's board, a price "
    "chalked up, a line someone would naturally say), never through a lecture; one sentence of plain context beats a "
    "mystery nobody asked for. (2) Dialogue: every line answers the line before it, or the reader sees why it does "
    "not; people speak plainly most of the time; no dramatic one-liners where a person would just talk; a frightened "
    "person stumbles, a tired one answers short, an angry one interrupts. (3) Cause and effect: for every event, the "
    "reader can tell what caused it, why now, how the protagonist understands it and what it changes; the next action "
    "follows. (4) Emotion: show why the moment matters to the protagonist here, in his body or his words, not in a "
    "biography. (5) Transitions: when place or time changes, the first line says where, when and who is there. (6) "
    "Keep the mystery that creates curiosity ('why does his card say NULL when he can do the work?'); remove the "
    "confusion that creates friction ('what is this trial and why does the clerk want the slip back?')."
)


def _exit_rule(plan: PlannedScene) -> str:
    if plan.choices:
        return (f"End with one `choice` node whose options lead to other chapters, exactly these in this order: "
                f"{_json([c.model_dump() for c in plan.choices])} (improve the wording, add `set` effects or a `require` "
                "using declared variables).")  # fmt: skip
    if plan.branches:
        return "End with the cliffhanger; the chapter's gated exit is added automatically (do not write it)."
    if plan.ending:
        return f"End with an `ending` node with ending {plan.ending!r}."
    return f"End with a `jump` node to {plan.next!r}."


def scene_prompt(config: StoryConfig, world: World, cast: Cast, outline: Outline, plan: PlannedScene,
                 previous: list[str], written_before: Scene | None = None, note: str | None = None, *,
                 part: int = 1, parts: int = 1, so_far: list[Any] | None = None, upcoming: list[str] | None = None,
                 decided: list[str] | None = None, knowledge: str = "") -> str:  # fmt: skip
    """One call of a chapter: the whole chapter (parts=1), or part `part` of its `parts` beat passes, continuing
    `so_far` (the nodes the earlier parts wrote). `upcoming` summarises the chapters this one leads to (so the
    writer does not write their events), `decided` lists flags earlier chapters can set (never offered again)."""
    exit_rule = _exit_rule(plan)
    on_stage = [c for c in cast.characters if c.id in plan.characters]
    blocks = [
        bible(config, world, cast, full_cast=False),
        "OUTLINE:\n" + _json({"start": outline.start, "variables": [v.model_dump(exclude_none=True) for v in outline.variables],
                              "scenes": [{"id": s.id, "title": s.title, "summary": s.summary} for s in outline.scenes],
                              "endings": [e.model_dump() for e in outline.endings]}),  # fmt: skip
        "STORY SO FAR (planned chapters leading here):\n" + ("\n".join(f"- {p}" for p in previous) or "- (this is the opening)"),
        "WRITE THIS CHAPTER:\n" + _json(plan.model_dump(exclude_none=True)),
        "On stage: " + _json([{"id": c.id, "name": c.name, "personality": c.personality, "voice": c.voice,
                               "expressions": c.expressions} for c in on_stage]),  # fmt: skip
        LINES,
        CHOICE_RULES,
        CLARITY_RULES,
    ]
    if knowledge:
        blocks.append(knowledge)
    ending = bool(plan.ending)
    words = max(150, round(config.chapter_words * 1.4) // (3 if ending else 1) // parts)  # Sonnet delivers ~70% of what is asked
    choices = max(1, math.ceil((1 if ending else config.chapter_choices) / parts))
    size = f"about {words} words in {words // 17}-{words // 12} boxes, with {choices} choice(s)"
    if parts == 1:
        rule = f"Write the whole chapter: {size}. Beats: " + "; ".join(BEATS) + ". " + exit_rule
    else:
        rule = (f"This call writes PART {part} OF {parts} of the chapter: {size}. Beats for this part: "
                + "; ".join(part_beats(part, parts)) + ". "
                + (exit_rule if part == parts else "Do not end the chapter: no `jump`, `ending`, `branch` or choice "
                   "with `goto`; stop on a line that leads into the next part."))  # fmt: skip
    if part == 1 and not ending:
        rule += " The FIRST in-chapter choice must come within the first 8 boxes (a hook of 3-6 boxes, then the choice)."
    if ending:
        rule += " This is an ending chapter: an epilogue that pays off the player's choices."
    blocks.append(rule)
    if upcoming:
        blocks.append("WHAT COMES NEXT (planned for the following chapter(s); do NOT write these events here, only lead "
                      "into them):\n" + "\n".join(f"- {u}" for u in upcoming))  # fmt: skip
    if decided:
        blocks.append("DECISIONS EARLIER CHAPTERS MAY HAVE SETTLED (flags): " + ", ".join(decided) + ". Never offer "
                      "these decisions again; acknowledge them with `when` lines (both outcomes) instead.")  # fmt: skip
    if config.direction is not None:
        blocks.append(creative.scene_block(config.direction))
    if so_far:
        blocks.append("ALREADY WRITTEN (earlier parts of this chapter; continue straight on from the last line, "
                      "do not repeat it):\n" + "\n".join(_line(n) for n in so_far))  # fmt: skip
    if written_before is not None:
        blocks.append("CURRENT VERSION OF THE CHAPTER:\n" + _json({"nodes": [n.model_dump(exclude_none=True) for n in written_before.nodes]}))
    if note:
        blocks.append(f"REVISION NOTE (apply it, keep everything else that works): {note}")
    return "\n\n".join(blocks)


def check_prompt(config: StoryConfig, world: World, cast: Cast, scenes: list[Scene]) -> str:
    script = [{"id": s.id, "title": s.title, "lines": [_line(n) for n in s.nodes]} for s in scenes]
    blocks = [
        bible(config, world, cast, full_cast=False),
        "SCRIPT:\n" + _json(script),
        "You are the continuity editor. Find real problems only: contradictions between scenes, characters acting "
        "or speaking out of character, facts introduced and forgotten, choices whose consequences never show, "
        "pacing holes, wrong names, the same event told twice across a chapter break, deadlines that change. "
        "Each issue names its scene id. Then write a one-paragraph summary.",
    ]  # fmt: skip
    if config.direction is not None:
        blocks.append(creative.check_block(config.direction))
    return "\n\n".join(blocks)


def _line(node: Any) -> str:
    text = _plain_line(node)
    if getattr(node, "when", None):
        text = "IF " + " and ".join(f"{c.var} {c.op} {c.value}" for c in node.when) + ": " + text
    return text


def _plain_line(node: Any) -> str:
    if node.kind == "choice" and not any(c.goto for c in node.choices):
        return "CHOICE (in-chapter): " + " | ".join(
            f"{c.text} => " + " / ".join(f"{r.speaker + ': ' if r.speaker else ''}{r.text}" for r in c.reaction)
            for c in node.choices)  # fmt: skip
    if node.kind == "place":
        return f"(move to {node.location} {node.time or ''} {node.text or ''})".replace("  ", " ")
    if node.kind == "music":
        return f"(music {node.music})"
    if node.kind == "ambience":
        return f"(ambience {node.ambience})"
    if node.kind == "sfx" and node.stop:
        return f"(stop sound {node.stop})"
    if node.kind == "branch":
        return "BRANCH: " + " | ".join(f"{' and '.join(f'{c.var} {c.op} {c.value}' for c in b.when)} -> {b.goto}"
                                       for b in node.branches) + f" | else -> {node.goto}"  # fmt: skip
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


def character_prompt(config: StoryConfig, world: World, profile: dict[str, Any], request: str, locked: list[str],
                     others: list[dict[str, str]]) -> str:  # fmt: skip
    """Revise one character's profile on request; locked sections come back unchanged."""
    blocks = [
        bible(config, world),
        "OTHER CHARACTERS (for relationships; do not change them): " + _json(others),
        "CURRENT PROFILE (sections: identity, personality, narrative, visual):\n" + _json(profile),
        f"REQUEST FROM THE CREATIVE DIRECTOR: {request.strip()}",
        "Return the complete revised profile with the same section structure. Change only what the request needs and "
        "what must change with it for consistency; keep everything else word for word. "
        + ("These sections are LOCKED and must come back exactly as they are: " + ", ".join(locked) + ". " if locked else "")
        + "The visual identity must stay consistent with the world's culture and the art direction. In `notes`, say in "
        "2-4 sentences what you changed and why, and which chapters or relationships it affects.",
    ]  # fmt: skip
    if config.direction is not None:
        blocks.append(creative.check_block(config.direction))
    return "\n\n".join(blocks)


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
