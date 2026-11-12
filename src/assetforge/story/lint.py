"""The narrative linter: measures each chapter against the category leaders (docs/vn-benchmark-gap-analysis.md)
without a model call.

A chapter is read along one path: lines play in order, an in-chapter choice adds its options' average reaction, and
`when` lines count (they play on some paths). Hard rules are errors and send a freshly written chapter into one
targeted repair round (`steps.write`); the rest are warnings for the author.

Hard rules:  ≥ 75% of the target words · ≥ the target choices (minus one) · first choice by box 15 ·
             never 400+ words without a choice · a callback (a `when` line, a `require`d option or a `branch`)
             from chapter 3 on.
Ending chapters (they end the story) are epilogues: only the wall-of-text rule applies.
"""

from __future__ import annotations

import math
import re
from collections import deque
from typing import Any

from assetforge.story.models import Issue, Node, Scene, Script, StoryConfig, is_exit, node_exits

WALL = 400  # words without a choice that always fail (Choice of Games: input every 100-200 words, never 400+)
FIRST_CHOICE_BY = 15  # boxes
LONG_BOX = 40  # words: more than ~3 textbox lines
WORDS_PER_MINUTE = 230


def words(text: str | None) -> int:
    return len((text or "").split())


def _text_nodes(node: Node) -> bool:
    return node.kind in ("say", "narrate") or (node.kind == "place" and bool(node.text))


def measure(scene: Scene) -> dict[str, Any]:
    total = boxes = choices = writes = callbacks = long_boxes = 0
    stretch = 0.0
    stretches: list[dict[str, Any]] = []
    stretch_from = 1
    first_choice: int | None = None
    narrow: list[int] = []
    for i, node in enumerate(scene.nodes):
        if node.when or (node.kind == "choice" and any(c.require for c in node.choices)) or node.kind == "branch":
            callbacks += 1
        writes += len(node.assign) + sum(len(c.set) for c in node.choices)
        if _text_nodes(node):
            w = words(node.text)
            boxes += 1
            total += w
            stretch += w
            long_boxes += w > LONG_BOX
        elif node.kind == "choice":
            choices += 1
            if first_choice is None:
                first_choice = boxes + 1
            stretches.append({"words": round(stretch), "from_box": stretch_from, "to_box": boxes})
            if not is_exit(node) and len(node.choices) < 3:
                narrow.append(i)
            reactions = [sum(words(r.text) for r in c.reaction) for c in node.choices] or [0]
            lines = [len(c.reaction) for c in node.choices] or [0]
            avg_words, avg_lines = sum(reactions) / len(reactions), sum(lines) / len(lines)
            total += avg_words
            boxes += round(avg_lines)
            stretch, stretch_from = avg_words, boxes + 1
    if not (scene.nodes and scene.nodes[-1].kind == "choice"):
        stretches.append({"words": round(stretch), "from_box": stretch_from, "to_box": boxes})
    worst = max(stretches, key=lambda s: s["words"], default={"words": 0, "from_box": 0, "to_box": 0})
    return {"words": round(total), "boxes": boxes, "choices": choices, "first_choice_box": first_choice,
            "max_stretch": worst, "callbacks": callbacks, "writes": writes, "long_boxes": long_boxes,
            "minutes": round(total / WORDS_PER_MINUTE, 1), "two_option_choices": narrow}  # fmt: skip


def chapter_numbers(script: Script) -> dict[str, int]:
    """Declared chapters first, else the shortest distance from the start (+1)."""
    if script.chapters:
        index = {c.id: i + 1 for i, c in enumerate(script.chapters)}
        if all(s.chapter in index for s in script.scenes):
            return {s.id: index[s.chapter or ""] for s in script.scenes}
    by_id = {s.id: s for s in script.scenes}
    depth = {script.start: 1}
    queue = deque([script.start])
    while queue:
        sid = queue.popleft()
        for node in by_id[sid].nodes if sid in by_id else []:
            for nxt in node_exits(node):
                if nxt not in depth:
                    depth[nxt] = depth[sid] + 1
                    queue.append(nxt)
    return {s.id: depth.get(s.id, max(depth.values(), default=0) + 1) for s in script.scenes}


PLACEHOLDER = re.compile(r"\b(test line|lorem ipsum|placeholder|insert (text|line|choice) here|tbd|todo)\b", re.I)


def placeholders(scene: Scene) -> list[str]:
    """Boxes that are filler rather than story (a repair call once wrote 'test line one' to move a choice up)."""
    found: list[str] = []
    for node in scene.nodes:
        texts = [node.text or ""] if node.kind in ("say", "narrate") else []
        texts += [r.text for c in node.choices for r in c.reaction] + [c.text for c in node.choices]
        found += [t for t in texts if t and PLACEHOLDER.search(t)]
    return found


def lint_scene(scene: Scene, config: StoryConfig, number: int, *, flags: set[str] | None = None) -> dict[str, Any]:
    m = measure(scene)
    ending = any(n.kind == "ending" for n in scene.nodes)
    found: list[Issue] = []

    def add(level: str, code: str, message: str) -> None:
        found.append(Issue(level=level, code=code, where=scene.id, message=message))  # type: ignore[arg-type]

    if filler := placeholders(scene):
        add("error", "placeholder_text", f"{len(filler)} placeholder line(s) such as {filler[0][:40]!r}; write real story lines")
    if not ending and not any(n.kind == "cg" for n in scene.nodes):
        add("warning", "no_cg", "no `cg` node: mark the chapter's peak moment with an event illustration")
    if flags and not ending:
        written = {a.var for n in scene.nodes for a in n.assign} | {a.var for n in scene.nodes for c in n.choices for a in c.set}
        if not written & flags:
            add("warning", "no_decision_flag", "no decision flag is set in this chapter; one choice should set a declared flag "
                "that a later chapter reads")  # fmt: skip
    wall = m["max_stretch"]
    if wall["words"] >= WALL:
        add("error", "wall_of_text", f"{wall['words']} words without a choice (boxes {wall['from_box']}-{wall['to_box']}); "
            f"add an in-chapter choice in that stretch so no stretch exceeds ~200 words")  # fmt: skip
    if not ending:
        if m["words"] < 0.75 * config.chapter_words:
            add("error", "too_short", f"{m['words']} words; the chapter needs about {config.chapter_words} "
                f"(add beats, reactions and scenes; do not pad lines)")  # fmt: skip
        if m["choices"] < max(1, config.chapter_choices - 1):
            add("error", "few_choices", f"{m['choices']} choices; the chapter needs {config.chapter_choices} "
                "(in-chapter choices with 3 options and short reactions)")  # fmt: skip
        if m["first_choice_box"] is None or m["first_choice_box"] > FIRST_CHOICE_BY:
            add("error", "late_first_choice", f"first choice at box {m['first_choice_box'] or 'never'}; "
                f"put one by box 8 (at most {FIRST_CHOICE_BY})")  # fmt: skip
        if number >= 3 and not m["callbacks"]:
            add("error", "no_callback", "no callback: add a line with `when`, or an option with `require`, that reads a "
                "variable set in an earlier chapter")  # fmt: skip
        if not m["writes"]:
            add("warning", "no_stat_change", "no choice changes a stat, so nothing here can matter later")
        if m["choices"] > config.chapter_choices + 4:
            add("warning", "many_choices", f"{m['choices']} choices; the target is {config.chapter_choices}")
    if m["long_boxes"]:
        add("warning", "long_box", f"{m['long_boxes']} line(s) over {LONG_BOX} words; split them (2-3 lines per box)")
    if m["two_option_choices"]:
        add("warning", "two_options", f"{len(m['two_option_choices'])} in-chapter choice(s) with fewer than 3 options")
    return {"scene": scene.id, "title": scene.title, "chapter": number, "ending": ending, **m,
            "target_words": config.chapter_words, "target_choices": config.chapter_choices,
            "ok": not any(i.level == "error" for i in found), "issues": [i.model_dump() for i in found]}  # fmt: skip


def lint_story(script: Script, config: StoryConfig) -> dict[str, Any]:
    numbers = chapter_numbers(script)
    flags = {v.name for v in script.variables if v.kind == "flag"}
    chapters = [lint_scene(s, config, numbers[s.id], flags=flags) for s in sorted(script.scenes, key=lambda s: numbers[s.id])]
    body = [c for c in chapters if not c["ending"]] or chapters
    from assetforge.story.stakes import choice_report, continuity_report

    return {
        "chapters": chapters,
        "choices": choice_report(script),
        "continuity": continuity_report(script),
        "summary": {
            "chapters": len(chapters), "passing": sum(c["ok"] for c in chapters),
            "avg_words": round(sum(c["words"] for c in body) / len(body)),
            "avg_choices": round(sum(c["choices"] for c in body) / len(body), 1),
            "worst_stretch": max(c["max_stretch"]["words"] for c in chapters),
            "minutes": round(sum(c["minutes"] for c in chapters), 1),
        },
    }  # fmt: skip


def repair_note(report: dict[str, Any]) -> str:
    """The linter's errors as a concrete revision note for the scene writer."""
    errors = [i["message"] for i in report["issues"] if i["level"] == "error"]
    codes = {i["code"] for i in report["issues"] if i["level"] == "error"}
    note = "Fix these measured problems and keep everything else that works: " + "; ".join(errors) + "."
    if codes & {"wall_of_text", "late_first_choice"} and "too_short" not in codes:
        note += (" This is an insertion, not a rewrite: keep every existing line in order and add a NEW short "
                 "in-chapter choice (3 options, 1-2 reaction lines each, a `set` on a declared stat) at the place named "
                 "(by box 6 for the first choice; in the middle of a long stretch).")  # fmt: skip
    return note + f" The chapter must keep about {report['target_words']} words and {report['target_choices']} choices."


def parts_for(config: StoryConfig) -> int:
    """How many calls write one chapter: a single call drifts short (15-25 lines); measured with Sonnet, a call asked
    for ~575 words delivers ~400, so ~450 words per call (4 calls for 1,500)."""
    return max(1, min(4, math.ceil(config.chapter_words / 450)))
