"""Answers for the offline (`fake`) text provider: a small but coherent branching story built from the config,
so the whole pipeline (assets, export, player) can be demoed and tested without any model or quota."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from assetforge.llm.base import TextCall
from assetforge.story.models import Cast, PlannedScene, Scene, Script, StoryConfig

Builder = Callable[[TextCall], Any]


def world(config: StoryConfig) -> Builder:
    def build(_: TextCall) -> dict[str, Any]:
        return {"title": config.title, "logline": config.premise, "setting": f"The small harbour town where {config.title} takes place.",
                "history": "An old secret has been kept here for a generation.", "culture": "Close-knit; everyone talks.",
                "rules": "Ordinary world; no magic.", "themes": ["trust", "courage"],
                "art_direction": config.art_style or "Soft painted anime illustration, warm muted palette, clean line art.",
                "music_direction": "Gentle piano with strings."}  # fmt: skip
    return build


def cast(config: StoryConfig) -> Builder:
    def build(_: TextCall) -> dict[str, Any]:
        return {
            "characters": [
                {"id": "mira", "name": "Mira", "role": "protagonist", "age": "17", "personality": "Curious and brave.",
                 "look": "Slim girl, shoulder-length copper hair, green eyes, yellow raincoat over a striped sweater.",
                 "palette": ["copper", "yellow"], "voice": "Quick, asks questions.",
                 "expressions": ["neutral", "smile", "worried", "surprised"]},
                {"id": "tom", "name": "Tom", "role": "friend", "age": "18", "personality": "Calm, loyal, secretive.",
                 "look": "Tall boy, short black hair, brown eyes, navy fisherman's sweater, grey scarf.",
                 "palette": ["navy", "grey"], "voice": "Few words, dry humour.",
                 "expressions": ["neutral", "smile", "angry"]},
            ],
            "locations": [
                {"id": "harbour", "name": "Harbour", "description": "Wooden piers, moored fishing boats, gulls, a lighthouse.",
                 "times": ["day", "evening"], "ambience": "Waves lapping, gulls, creaking ropes."},
                {"id": "lighthouse", "name": "Lighthouse", "description": "Spiral stairs inside an old stone lighthouse.",
                 "times": ["night"], "ambience": "Wind howling around stone walls."},
            ],
        }  # fmt: skip
    return build


def outline(config: StoryConfig, cast_: Cast) -> Builder:
    def build(_: TextCall) -> dict[str, Any]:
        endings = max(1, config.endings)
        total = max(config.scenes, endings + 2)
        trunk = total - endings
        chars = [c.id for c in cast_.characters]
        locs = cast_.locations
        scenes: list[dict[str, Any]] = []
        for i in range(trunk):
            loc = locs[min(i, len(locs) - 1) if i < trunk - 1 else len(locs) - 1]
            scene: dict[str, Any] = {"id": f"s{i + 1:02d}", "title": f"Part {i + 1}", "location": loc.id,
                                     "time": loc.times[0], "characters": chars[: 1 + (i > 0)],
                                     "summary": f"Step {i + 1} of the mystery in {loc.name}.",
                                     "music": ["calm", "mysterious", "tense"][i % 3]}  # fmt: skip
            if i < trunk - 1:
                scene["next"] = f"s{i + 2:02d}"
            else:
                scene["choices"] = [{"text": f"Choose path {k + 1}", "goto": f"e{k + 1:02d}"} for k in range(endings)]
            scenes.append(scene)
        for k in range(endings):
            loc = locs[k % len(locs)]
            scenes.append({"id": f"e{k + 1:02d}", "title": f"Ending path {k + 1}", "location": loc.id,
                           "time": loc.times[-1], "characters": chars[:2], "summary": f"Where path {k + 1} leads.",
                           "music": "triumphant" if k == 0 else "sad", "ending": f"end_{k + 1}"})  # fmt: skip
        return {"start": "s01", "variables": [{"name": "courage", "initial": 0, "meaning": "How bold Mira has been"}],
                "scenes": scenes,
                "endings": [{"id": f"end_{k + 1}", "title": f"Ending {k + 1}", "kind": "good" if k == 0 else "bad",
                             "summary": f"Ending number {k + 1}."} for k in range(endings)]}  # fmt: skip
    return build


def scene(plan: PlannedScene, cast_: Cast) -> Builder:
    names = {c.id: c for c in cast_.characters}

    def build(_: TextCall) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = [{"kind": "narrate", "text": plan.summary}]
        for slot, cid in zip(("left", "right", "center"), plan.characters, strict=False):
            char = names[cid]
            nodes.append({"kind": "show", "character": cid, "expression": "neutral", "position": slot})
            nodes.append({"kind": "say", "speaker": cid, "expression": char.expressions[-1],
                          "text": f"I'm {char.name}. {plan.title} starts now."})  # fmt: skip
        if plan.id == "s01":
            nodes.append({"kind": "sfx", "cue": "door_creak", "text": "An old wooden door creaks open."})
        if plan.ending:
            nodes.append({"kind": "cg", "cg": f"{plan.id}_moment", "text": f"Key art of the ending: {plan.summary}"})
        nodes.append({"kind": "set", "assign": [{"var": "courage", "op": "add", "value": 1}]})
        return {"nodes": nodes}  # the exit node is added from the outline by conform_scene
    return build


def refine(current: Scene, note: str) -> Builder:
    def build(_: TextCall) -> dict[str, Any]:
        nodes = [n.model_dump(exclude_none=True) for n in current.nodes]
        return {"nodes": [{"kind": "narrate", "text": f"(Revised: {note})"}, *nodes]}
    return build


def check() -> Builder:
    return lambda _: {"issues": [], "summary": "No continuity problems found (offline check)."}


def improve(script: Script, feedback: str) -> Builder:
    first = [s.id for s in script.scenes[:2]]
    return lambda _: {"summary": f"Address: {feedback[:80]}", "edits": [{"scene": sid, "note": feedback} for sid in first]}
