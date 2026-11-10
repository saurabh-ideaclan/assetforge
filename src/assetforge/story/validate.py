"""Deterministic story checks (no model calls) and the safe automatic fixes.

Errors make a story unplayable or its assets unknowable: broken links, unreachable scenes, scenes that can
never reach an ending, unknown characters/locations, bad node fields. Warnings are worth a look but play fine.
"""

from __future__ import annotations

from collections import deque

from assetforge.story.models import (
    Choice,
    Issue,
    Node,
    Outline,
    PlannedScene,
    Scene,
    Script,
    Variable,
    is_exit,
    node_exits,
    reads,
)


def issue(level: str, code: str, where: str, message: str) -> Issue:
    return Issue(level=level, code=code, where=where, message=message)  # type: ignore[arg-type]


def exits(scene: Scene) -> tuple[list[str], list[str]]:
    """(scene ids this scene can go to, ending ids it can reach)."""
    gotos: list[str] = []
    endings: list[str] = []
    for node in scene.nodes:
        gotos += node_exits(node)
        if node.kind == "ending" and node.ending:
            endings.append(node.ending)
    return gotos, endings


def validate_script(script: Script) -> list[Issue]:
    issues: list[Issue] = []
    scenes = {s.id: s for s in script.scenes}
    chars = {c.id: c for c in script.characters}
    locs = {loc.id: loc for loc in script.locations}
    endings = {e.id for e in script.endings}
    variables = {v.name for v in script.variables}
    chapters = {c.id for c in script.chapters}

    seen: set[str] = set()
    for scene in script.scenes:
        if scene.id in seen:
            issues.append(issue("error", "duplicate_scene", scene.id, f"Scene id {scene.id!r} is used twice"))
        seen.add(scene.id)
    if script.start not in scenes:
        issues.append(issue("error", "missing_start", "script", f"Start scene {script.start!r} does not exist"))

    issues += _check_variables(script.variables, chars, "script")
    for ending in script.endings:
        issues += [issue("error", "unknown_variable", ending.id, f"Ending requires undeclared variable {c.var!r}")
                   for c in ending.requires if c.var not in variables]  # fmt: skip
    for scene in script.scenes:
        where = scene.id
        loc = locs.get(scene.location)
        if loc is None:
            issues.append(issue("error", "unknown_location", where, f"Unknown location {scene.location!r}"))
        elif scene.time not in loc.times:
            issues.append(issue("warning", "unlisted_time", where, f"{loc.id} has no {scene.time} version listed"))
        if chapters and scene.chapter not in chapters:
            issues.append(issue("error" if scene.chapter else "warning", "unknown_chapter", where,
                                f"Unknown chapter {scene.chapter!r}" if scene.chapter else "Scene has no chapter"))  # fmt: skip
        if not is_exit(scene.nodes[-1]):
            issues.append(issue("error", "no_exit", where, "Scene must end with a choice to other scenes, a branch, "
                                                           "a jump or an ending"))  # fmt: skip
        for i, node in enumerate(scene.nodes):
            at = f"{where}#{i}"
            if is_exit(node) and i != len(scene.nodes) - 1:
                issues.append(issue("error", "exit_not_last", at, f"{node.kind} must be the last node of a scene"))
            issues += _check_node(node, at, chars, scenes, endings, variables, locs)
    issues += _check_flow(script, scenes, endings)
    issues += _check_reads(script)
    return issues


def _check_variables(variables: list[Variable], chars: dict | set, where: str) -> list[Issue]:
    out: list[Issue] = []
    for v in variables:
        if v.kind == "relationship" and v.character and v.character not in chars:
            out.append(issue("error", "unknown_character", f"{where}.{v.name}", f"Unknown character {v.character!r}"))
        if v.min is not None and v.max is not None and v.min > v.max:
            out.append(issue("error", "bad_range", f"{where}.{v.name}", f"min {v.min} is above max {v.max}"))
        elif (v.min is not None and v.initial < v.min) or (v.max is not None and v.initial > v.max):
            out.append(issue("warning", "initial_out_of_range", f"{where}.{v.name}", "initial value is outside min..max"))
    return out


def _check_reads(script: Script) -> list[Issue]:
    """Delayed branching: a stat that choices change but nothing ever reads has no consequence."""
    written: set[str] = set()
    read: set[str] = {c.var for e in script.endings for c in e.requires}
    for scene in script.scenes:
        for node in scene.nodes:
            read |= set(reads(node))
            written |= {a.var for a in node.assign} | {a.var for c in node.choices for a in c.set}
    return [issue("warning", "unread_variable", var, "Choices change it, but no line, option, branch or ending reads it")
            for var in sorted(written - read)]  # fmt: skip


def _check_node(node: Node, at: str, chars: dict, scenes: dict, endings: set[str], variables: set[str],
                locs: dict | None = None) -> list[Issue]:  # fmt: skip
    out: list[Issue] = []
    locs = locs or {}
    out += [issue("error", "unknown_variable", at, f"Undeclared variable {c.var!r}") for c in node.when if c.var not in variables]
    if node.kind == "place":
        if not node.location:
            out.append(issue("error", "missing_location", at, "place needs a location"))
        elif node.location not in locs:
            out.append(issue("error", "unknown_location", at, f"Unknown location {node.location!r}"))
        elif node.time and node.time not in locs[node.location].times:
            out.append(issue("warning", "unlisted_time", at, f"{node.location} has no {node.time} version listed"))
    if node.kind == "music" and not node.music:
        out.append(issue("error", "missing_music", at, "music needs a mood"))
    if node.kind == "branch":
        if not node.branches:
            out.append(issue("error", "empty_branch", at, "branch has no conditions"))
        if not node.goto:
            out.append(issue("error", "branch_no_fallback", at, "branch needs a fallback goto for when nothing holds"))
        for b in node.branches:
            out += [issue("error", "unknown_variable", at, f"Undeclared variable {c.var!r}") for c in b.when if c.var not in variables]
        for target in node_exits(node):
            if target not in scenes:
                out.append(issue("error", "bad_goto", at, f"branch goes to unknown scene {target!r}"))
    who = node.speaker if node.kind == "say" else node.character if node.kind in ("show", "hide") else None
    if node.kind in ("say", "show", "hide"):
        if not who:
            out.append(issue("error", "missing_character", at, f"{node.kind} needs a {'speaker' if node.kind == 'say' else 'character'}"))
        elif who not in chars:
            out.append(issue("error", "unknown_character", at, f"Unknown character {who!r}"))
        elif node.expression and node.expression not in chars[who].expressions:
            out.append(issue("error", "unknown_expression", at, f"{who} has no {node.expression!r} expression"))
    if node.kind in ("say", "narrate") and not (node.text or "").strip():
        out.append(issue("error", "empty_text", at, f"{node.kind} has no text"))
    if node.kind == "sfx" and not node.cue:
        out.append(issue("error", "missing_cue", at, "sfx needs a cue id"))
    if node.kind == "cg" and not node.cg:
        out.append(issue("error", "missing_cg", at, "cg needs a cg id"))
    if node.kind == "jump" and node.goto not in scenes:
        out.append(issue("error", "bad_goto", at, f"jump to unknown scene {node.goto!r}"))
    if node.kind == "ending" and node.ending not in endings:
        out.append(issue("error", "unknown_ending", at, f"Unknown ending {node.ending!r}"))
    if node.kind == "choice":
        if not node.choices:
            out.append(issue("error", "empty_choice", at, "choice has no options"))
        elif len(node.choices) == 1:
            out.append(issue("warning", "single_choice", at, "choice has only one option"))
        unconditional = [c for c in node.choices if c.require is None]
        if node.choices and not unconditional:
            out.append(issue("warning", "all_conditional", at, "every option has a condition: the player may get stuck"))
        leads = [c.goto is not None for c in node.choices]
        if any(leads) and not all(leads):
            out.append(issue("error", "mixed_choice", at, "either every option goes to a scene or none does"))
        for choice in node.choices:
            if not choice.text.strip():
                out.append(issue("error", "empty_choice_text", at, "a choice option has no text"))
            if choice.goto is not None and choice.goto not in scenes:
                out.append(issue("error", "bad_goto", at, f"choice goes to unknown scene {choice.goto!r}"))
            if choice.goto is None and not choice.reaction:
                out.append(issue("warning", "no_reaction", at, f"option {choice.text!r} has no reaction lines"))
            for line in choice.reaction:
                if not line.text.strip():
                    out.append(issue("error", "empty_text", at, "a reaction line has no text"))
                if line.speaker and line.speaker not in chars:
                    out.append(issue("error", "unknown_character", at, f"Unknown character {line.speaker!r}"))
                elif line.speaker and line.expression and line.expression not in chars[line.speaker].expressions:
                    out.append(issue("error", "unknown_expression", at, f"{line.speaker} has no {line.expression!r} expression"))
            used = [a.var for a in choice.set] + ([choice.require.var] if choice.require else [])
            out += [issue("error", "unknown_variable", at, f"Undeclared variable {v!r}") for v in used if v not in variables]
    for assign in node.assign:
        if assign.var not in variables:
            out.append(issue("error", "unknown_variable", at, f"Undeclared variable {assign.var!r}"))
    return out


def _check_flow(script: Script, scenes: dict[str, Scene], endings: set[str]) -> list[Issue]:
    out: list[Issue] = []
    if script.start not in scenes:
        return out
    graph = {sid: exits(s) for sid, s in scenes.items()}
    reachable: set[str] = set()
    queue = deque([script.start])
    reached_endings: set[str] = set()
    while queue:
        sid = queue.popleft()
        if sid in reachable or sid not in graph:
            continue
        reachable.add(sid)
        gotos, ends = graph[sid]
        reached_endings |= set(ends)
        queue.extend(g for g in gotos if g in graph)
    for sid in scenes:
        if sid not in reachable:
            out.append(issue("warning", "unreachable_scene", sid, "No path from the start reaches this scene"))
    for ending in sorted(endings - reached_endings):
        out.append(issue("warning", "unreached_ending", ending, "No reachable scene leads to this ending"))
    # every reachable scene must be able to reach some ending (no inescapable loops)
    can_end = {sid for sid, (_, ends) in graph.items() if ends}
    changed = True
    while changed:
        changed = False
        for sid, (gotos, _) in graph.items():
            if sid not in can_end and any(g in can_end for g in gotos):
                can_end.add(sid)
                changed = True
    for sid in sorted(reachable - can_end):
        out.append(issue("error", "no_path_to_ending", sid, "From here the story can never reach an ending"))
    return out


def validate_outline(outline: Outline, characters: set[str], locations: set[str]) -> list[Issue]:
    issues: list[Issue] = []
    ids = {s.id for s in outline.scenes}
    endings = {e.id for e in outline.endings}
    chapters = {c.id for c in outline.chapters}
    variables = {v.name for v in outline.variables}
    issues += _check_variables(outline.variables, characters, "outline")
    for ending in outline.endings:
        issues += [issue("error", "unknown_variable", ending.id, f"Ending requires undeclared variable {c.var!r}")
                   for c in ending.requires if c.var not in variables]  # fmt: skip
    if outline.start not in ids:
        issues.append(issue("error", "missing_start", "outline", f"Start scene {outline.start!r} is not planned"))
    for plan in outline.scenes:
        if chapters and plan.chapter not in chapters:
            issues.append(issue("error" if plan.chapter else "warning", "unknown_chapter", plan.id,
                                f"Unknown chapter {plan.chapter!r}" if plan.chapter else "Scene has no chapter"))  # fmt: skip
        if plan.location not in locations:
            issues.append(issue("error", "unknown_location", plan.id, f"Unknown location {plan.location!r}"))
        for c in plan.characters:
            if c not in characters:
                issues.append(issue("error", "unknown_character", plan.id, f"Unknown character {c!r}"))
        targets = [c.goto for c in plan.choices] + ([plan.next] if plan.next else [])
        for t in targets:
            if t not in ids:
                issues.append(issue("error", "bad_goto", plan.id, f"Leads to unknown scene {t!r}"))
        if plan.ending and plan.ending not in endings:
            issues.append(issue("error", "unknown_ending", plan.id, f"Unknown ending {plan.ending!r}"))
        if not targets and not plan.ending:
            issues.append(issue("error", "no_exit", plan.id, "Planned scene has no next scene, choices or ending"))
    return issues


# ---------------------------------------------------------------------- automatic fixes


def conform_scene(scene: Scene, plan: PlannedScene, script_chars: dict[str, list[str]],
                  locations: set[str] | None = None) -> tuple[Scene, list[str]]:  # fmt: skip
    """Make a written scene honour its plan and the cast, without another model call:
    the exit always matches the outline (planned choices/next/ending), stray exits mid-scene are dropped,
    unknown expressions fall back to neutral, and unknown speakers become narration."""
    fixes: list[str] = []
    body: list[Node] = []
    written_choice = next((n for n in scene.nodes if n.kind == "choice" and is_exit(n)), None)
    for node in scene.nodes:
        if is_exit(node) or node.kind == "branch":
            continue  # the scene's exit comes from the outline, below
        if node.kind == "place" and (not node.location or (locations is not None and node.location not in locations)):
            fixes.append(f"dropped a move to unknown place {node.location!r}")
            continue
        if node.kind == "choice":
            node, line_fixes = _conform_reactions(node, script_chars)
            fixes += line_fixes
        if node.kind in ("say", "show", "hide"):
            who = node.speaker if node.kind == "say" else node.character
            if who not in script_chars:
                if node.kind == "say" and node.text:
                    fixes.append(f"unknown speaker {who!r} → narration")
                    body.append(Node(kind="narrate", text=node.text))
                else:
                    fixes.append(f"dropped {node.kind} of unknown character {who!r}")
                continue
            if node.expression and node.expression not in script_chars[who]:
                fixes.append(f"{who}: unknown expression {node.expression!r} → neutral")
                node = node.model_copy(update={"expression": "neutral"})
        body.append(node)
    if plan.choices:
        options = list(written_choice.choices) if written_choice else []
        merged = []
        for i, p in enumerate(plan.choices):  # by position first: several options may lead to the same scene
            if i < len(options) and options[i].goto == p.goto:
                pick = options[i]
            else:
                pick = next((o for o in options if o.goto == p.goto and o not in merged), None)
            merged.append(pick or Choice(text=p.text, goto=p.goto))
        if written_choice is None or [c.goto for c in options] != [c.goto for c in merged]:
            fixes.append("choice options aligned with the outline")
        body.append(Node(kind="choice", choices=merged))
    elif plan.branches:
        if not any(n.kind == "branch" for n in scene.nodes):
            fixes.append("gated exits set from the outline")
        body.append(Node(kind="branch", branches=plan.branches, goto=plan.next))
    elif plan.ending:
        if not (scene.nodes and scene.nodes[-1].kind == "ending" and scene.nodes[-1].ending == plan.ending):
            fixes.append(f"ending set to {plan.ending}")
        body.append(Node(kind="ending", ending=plan.ending))
    elif plan.next:
        if not (scene.nodes and scene.nodes[-1].kind == "jump" and scene.nodes[-1].goto == plan.next):
            fixes.append(f"jump set to {plan.next}")
        body.append(Node(kind="jump", goto=plan.next))
    if len(body) == 1:  # nothing but the exit survived
        body.insert(0, Node(kind="narrate", text=plan.summary))
        fixes.append("empty scene → planned summary as narration")
    return scene.model_copy(update={"nodes": body}), fixes



def _conform_reactions(node: Node, script_chars: dict[str, list[str]]) -> tuple[Node, list[str]]:
    fixes: list[str] = []
    options = []
    for choice in node.choices:
        lines = []
        for line in choice.reaction:
            if line.speaker and line.speaker not in script_chars:
                fixes.append(f"unknown speaker {line.speaker!r} → narration")
                line = line.model_copy(update={"speaker": None, "expression": None})
            elif line.speaker and line.expression and line.expression not in script_chars[line.speaker]:
                fixes.append(f"{line.speaker}: unknown expression {line.expression!r} → neutral")
                line = line.model_copy(update={"expression": "neutral"})
            lines.append(line)
        options.append(choice.model_copy(update={"reaction": lines}))
    return node.model_copy(update={"choices": options}), fixes
