"""Answers for the offline (`fake`) text provider: a small but coherent branching story built from the config,
so the whole pipeline (assets, export, player) can be demoed and tested without any model or quota."""

from __future__ import annotations

import json
import math
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


def names(config: StoryConfig) -> Builder:
    """A naming guide in the direction's tradition (a small fixed table per tradition keeps tests deterministic)."""
    d = config.direction
    tradition = (d.naming if d and d.naming != "match-culture" else (d.culture if d else "")) or "invented"
    table = {
        "western-fantasy": (["Cedric", "Elara", "Rowan", "Valen", "Isolde", "Theron", "Maren", "Alaric", "Sabine", "Corvin", "Liesl", "Edric"],
                            ["Vayne", "Ashcroft", "Dreyer", "Holloway", "Morrane", "Thorne"], ["Vallenmoor", "Greywatch", "Ashford", "Ironvale", "Sollen", "Carrow"],
                            ["Lord/Lady", "Knight-Captain", "Magister"]),
        "korean": (["Ji-ho", "Seo-yeon", "Min-jun", "Ha-eun", "Do-yun", "Ye-jin", "Tae-yang", "Su-bin", "Joon-woo", "Na-ri", "Hyun-woo", "A-ra"],
                   ["Kang", "Yoon", "Seo", "Baek", "Han", "Moon"], ["Haeseong", "Baekryeong", "Gwangju Gate", "Namsan Guild Hall", "Dongcheon", "Yeongrak"],
                   ["Guild Master", "Hunter (E-S rank)", "Sunbae/Hoobae"]),
        "japanese": (["Haruto", "Aoi", "Ren", "Yui", "Sora", "Hina", "Kaito", "Mio", "Riku", "Nanami", "Tsubasa", "Akari"],
                     ["Kisaragi", "Tachibana", "Shirogane", "Kurokawa", "Amane", "Hoshino"], ["Seiran Academy", "Kagerou Ward", "Mount Shiro", "Tsukimi Bay", "Hakua", "Reisen"],
                     ["-senpai", "-sensei", "Student Council President"]),
        "chinese": (["Lin Feng", "Su Yan", "Mo Chen", "Xue Ling", "Bai Yu", "Qin Hao", "Ye Mei", "Luo Zhen", "Han Shuang", "Gu Yun", "Wei Lan", "Jiang Rui"],
                    ["Lin", "Su", "Mo", "Xue", "Bai", "Qin"], ["Azure Cloud Sect", "Nine Peaks", "Mist River City", "Thousand Pine Valley", "Red Lotus Pass", "Jade Terrace"],
                    ["Elder", "Sect Master", "Senior Brother/Sister"]),
        "south-asian": (["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Anaya", "Vihaan", "Ishani", "Arjun", "Tara", "Dev", "Nisha"],
                        ["Rao", "Menon", "Khan", "Sen", "Pillai", "Kapoor"], ["Nilgiri Ridge", "Ravenport", "Kalpana Chowk", "Suryakund", "Deodar Lane", "Pashan Fort"],
                        ["Sir/Madam", "Guru", "Sahib"]),
    }
    given, family, places, titles = table.get(tradition, (
        ["Oren", "Tavi", "Sel", "Mirek", "Anwe", "Dast", "Ilo", "Veya", "Corun", "Lysa", "Brann", "Nael"],
        ["of the Reed", "Hallow", "Kest", "Orun", "Vael", "Dunmar"], ["Reedhollow", "The Salt Stair", "Kestmoor", "Orun's Gate", "Highwater", "The Lantern Coast"],
        ["Keeper", "Warden", "Elder"]))
    reserved = list(d.reserved_names) if d else []

    def build(_: TextCall) -> dict[str, Any]:
        return {"tradition": f"Names in the {tradition.replace('-', ' ')} manner, consistent for people and places.",
                "name_order": "family given" if tradition in ("korean", "japanese", "chinese") else "given family",
                "given_names": given, "family_names": family, "place_names": places, "titles": titles,
                "patterns": "Two or three syllables, soft endings for given names; place names from landscape words.",
                "avoid": ["names of real famous people", "mixing traditions"], "reserved": reserved}  # fmt: skip
    return build


def cast(config: StoryConfig, guide: Any | None = None) -> Builder:
    """The fixed harbour cast; with a naming guide, Mira and Tom take the guide's first two given names (and a reserved
    name first), so tests can see the guide being followed."""
    pick = list(getattr(guide, "reserved", []) or []) + [n for n in getattr(guide, "given_names", []) or [] if n not in getattr(guide, "reserved", [])]
    n1 = pick[0] if pick else "Mira"
    n2 = pick[1] if len(pick) > 1 else "Tom"

    def build(_: TextCall) -> dict[str, Any]:
        return {
            "characters": [
                {"id": "mira", "name": n1, "role": "protagonist", "age": "17", "personality": "Curious and brave.",
                 "look": "Slim girl, shoulder-length copper hair, green eyes, yellow raincoat over a striped sweater.",
                 "palette": ["copper", "yellow"], "voice": "Quick, asks questions.",
                 "expressions": ["neutral", "smile", "worried", "surprised"]},
                {"id": "tom", "name": n2, "role": "friend", "age": "18", "personality": "Calm, loyal, secretive.",
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


FILLER = ("The wind pushes salt and old rope smell along the street while the clock above the shop ticks on, "
          "patient as the tide.")  # 21 words: a full textbox


def scene(plan: PlannedScene, cast_: Cast, config: StoryConfig | None = None, *, part: int = 1, parts: int = 1,
          variables: list[str] | None = None) -> Builder:  # fmt: skip
    """One beat pass of a chapter that meets the linter's targets: in-chapter choices with three options and
    reactions every ~120 words, a callback line, and the planned exit added later by conform_scene."""
    names = {c.id: c for c in cast_.characters}
    stat = (variables or ["courage"])[0]
    target_words = (config.chapter_words if config else 400) // (3 if plan.ending else 1) // parts
    target_choices = math.ceil((1 if plan.ending else (config.chapter_choices if config else 2)) / parts)

    def build(_: TextCall) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        if part == 1:
            nodes.append({"kind": "narrate", "text": plan.summary})
            for slot, cid in zip(("left", "right", "center"), plan.characters, strict=False):
                char = names[cid]
                nodes.append({"kind": "show", "character": cid, "expression": "neutral", "position": slot})
                nodes.append({"kind": "say", "speaker": cid, "expression": char.expressions[-1],
                              "text": f"I'm {char.name}. {plan.title} starts now."})  # fmt: skip
            if plan.id == "s01":
                nodes.append({"kind": "sfx", "cue": "door_creak", "text": "An old wooden door creaks open."})
            if plan.ending:
                nodes.append({"kind": "cg", "cg": f"{plan.id}_moment", "text": f"Key art of the ending: {plan.summary}"})
        speaker = plan.characters[0] if plan.characters else None
        per_choice = max(1, target_words // max(1, target_choices + 1))
        for k in range(target_choices):
            nodes += [{"kind": "narrate", "text": FILLER} for _ in range(max(1, (per_choice - 30) // 21))]
            reaction = [{"speaker": speaker, "text": "Then that's what we'll do.", "expression": None},
                        {"speaker": None, "text": "The moment settles, and the story moves on."}]
            nodes.append({"kind": "choice", "choices": [
                {"text": "Step forward.", "set": [{"var": stat, "op": "add", "value": 1}], "reaction": reaction},
                {"text": "Hold back.", "set": [{"var": stat, "op": "add", "value": -1}], "reaction": reaction},
                {"text": "Ask a question.", "reaction": reaction}]})  # fmt: skip
        nodes.append({"kind": "narrate", "text": "You remember how bold you were before.",
                      "when": [{"var": stat, "op": ">=", "value": 1}]})  # fmt: skip
        nodes += [{"kind": "narrate", "text": FILLER} for _ in range(max(1, (per_choice - 30) // 21))]
        if part == parts:
            nodes.append({"kind": "set", "assign": [{"var": stat, "op": "add", "value": 1}]})
        return {"nodes": nodes}  # the exit node is added from the outline by conform_scene
    return build


def character(profile: dict[str, Any], request: str) -> Builder:
    """The offline revision: the request lands in the personality's contradictions and the notes say so."""

    def build(_: TextCall) -> dict[str, Any]:
        data = json.loads(json.dumps(profile))
        pers = data.setdefault("personality", {})
        pers["contradictions"] = (pers.get("contradictions") or "").strip() + f" Revised: {request.strip()}"
        data["notes"] = f"Applied the request: {request.strip()}. Affects every chapter where this character speaks."
        return data
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
