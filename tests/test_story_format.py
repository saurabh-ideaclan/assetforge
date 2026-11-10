"""The Phase 1 story format: in-chapter choices with reactions, callbacks (`when`), place/music changes, stats,
gated exits (branch) and chapters made of several scenes. `examples/kite-season` is the hand-written sample."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from assetforge.story import steps
from assetforge.story.assets import manifest
from assetforge.story.models import (
    Assign,
    Branch,
    Cast,
    Choice,
    Condition,
    Line,
    Node,
    Outline,
    PlannedChoice,
    PlannedScene,
    Scene,
    Script,
    Variable,
    is_exit,
    walk,
)
from assetforge.story.progress import chapter_numbers
from assetforge.story.project import StoryProject
from assetforge.story.usage import _chapters
from assetforge.story.validate import conform_scene, validate_outline, validate_script

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "kite-season"


@pytest.fixture
def sample() -> StoryProject:
    return StoryProject(SAMPLE)


@pytest.fixture
def script(sample: StoryProject) -> Script:
    return sample.load("script")


def codes(issues) -> set[str]:  # noqa: ANN001
    return {i.code for i in issues}


def test_sample_meets_the_phase_1_bar(script: Script, sample: StoryProject) -> None:
    assert validate_script(script) == []
    assert validate_outline(sample.load("outline"), {c.id for c in script.characters}, {loc.id for loc in script.locations}) == []
    nodes = [n for s in script.scenes for n in s.nodes]
    inline = [n for n in nodes if n.kind == "choice" and not is_exit(n)]
    assert len(inline) >= 7 and all(c.reaction for n in inline for c in n.choices)
    assert sum(1 for n in nodes if n.when) >= 2  # callbacks
    assert len({st.location for s in script.scenes for st in walk(s)}) >= 2
    assert [c.id for c in script.chapters] == ["ch1", "ch2"] and {s.chapter for s in script.scenes} == {"ch1", "ch2"}
    assert {v.kind for v in script.variables} == {"relationship", "personality", "resource", "flag"}
    assert any(e.requires for e in script.endings) and any(n.kind == "branch" for n in nodes)


def test_the_writer_path_keeps_the_new_nodes(sample: StoryProject, script: Script) -> None:
    """conform_scene (run on every model answer) must leave a well-formed scene as it is."""
    plan = sample.load("outline")
    chars = {c.id: c.expressions for c in script.characters}
    locs = {loc.id for loc in script.locations}
    for scene in script.scenes:
        planned = next(p for p in plan.scenes if p.id == scene.id)
        fixed, fixes = conform_scene(scene, planned, chars, locs)
        assert fixes == [] and fixed == scene


def test_conform_repairs_reactions_places_and_sets_the_gate(script: Script) -> None:
    chars = {c.id: c.expressions for c in script.characters}
    scene = Scene(id="s_beach", title="t", location="beach", nodes=[
        Node(kind="place", location="moon"),
        Node(kind="choice", choices=[Choice(text="a", reaction=[Line(speaker="ghost", text="boo"),
                                                                Line(speaker="ira", text="hm", expression="smug")]),
                                     Choice(text="b", reaction=[Line(text="ok")])]),
        Node(kind="choice", choices=[Choice(text="leave", goto="s_roof")]),  # a stray exit mid-scene
        Node(kind="narrate", text="..."),
    ])  # fmt: skip
    plan = PlannedScene(id="s_beach", title="t", location="beach", summary="s", next="e_solo_scene",
                        branches=[Branch(when=[Condition(var="parts", op=">=", value=2)], goto="e_together_scene")])  # fmt: skip
    fixed, fixes = conform_scene(scene, plan, chars, {"beach"})
    kinds = [n.kind for n in fixed.nodes]
    assert kinds == ["choice", "narrate", "branch"]
    reaction = fixed.nodes[0].choices[0].reaction
    assert reaction[0].speaker is None and reaction[1].expression == "neutral"
    assert fixed.nodes[-1].branches == plan.branches and fixed.nodes[-1].goto == "e_solo_scene"
    assert any("unknown place" in f for f in fixes)


def test_validator_catches_format_mistakes(script: Script) -> None:
    roof = script.scenes[0]
    bad = roof.model_copy(update={"chapter": "ch9", "nodes": [
        Node(kind="narrate", text="x", when=[Condition(var="nope")]),
        Node(kind="place", location="mars"),
        Node(kind="music"),
        Node(kind="choice", choices=[Choice(text="a", goto="s_beach"), Choice(text="b", reaction=[Line(text="r")])]),
        Node(kind="choice", choices=[Choice(text="silent")]),
        Node(kind="branch", branches=[Branch(when=[Condition(var="parts")], goto="nowhere")]),
    ]})  # fmt: skip
    broken = script.model_copy(update={"scenes": [bad, *script.scenes[1:]],
                                       "variables": [*script.variables, Variable(name="ghost_stat", kind="relationship",
                                                                                 character="casper", min=3, max=1)]})  # fmt: skip
    found = codes(validate_script(broken))
    assert {"unknown_chapter", "unknown_variable", "unknown_location", "missing_music", "mixed_choice", "no_reaction",
            "branch_no_fallback", "bad_goto", "unknown_character", "bad_range"} <= found  # fmt: skip


def test_unread_stats_are_flagged(script: Script) -> None:
    roof = script.scenes[0]
    nodes = [n for n in roof.nodes if not n.when] + []
    quiet = script.model_copy(update={"scenes": [roof.model_copy(update={"nodes": nodes}), *script.scenes[1:]]})
    warnings = {i.where for i in validate_script(quiet) if i.code == "unread_variable"}
    assert "told_truth" in warnings and "trust_ira" not in warnings  # trust is still read by the gate


def test_assets_follow_moves_music_and_reactions(sample: StoryProject, script: Script) -> None:
    items = {i.key for i in manifest(script, sample.config, sample.load("world"))}
    slug = "vn/kite-season"
    assert {f"{slug}/bg/repair_shop/evening", f"{slug}/bg/beach/evening", f"{slug}/bg/school_roof/day"} <= items
    assert {f"{slug}/music/playful", f"{slug}/music/heroic", f"{slug}/music/calm"} <= items  # from place/music nodes
    assert f"{slug}/char/ravi/determined" in items  # only used in a reaction line
    assert f"{slug}/amb/repair_shop" in items
    usage = _chapters(script)
    assert "s_roof" in usage["bg"]["repair_shop/evening"] and "s_roof" in usage["char"]["ravi/determined"]


def test_branch_exits_count_for_order_and_chapter_numbers(sample: StoryProject) -> None:
    plan = sample.load("outline")
    ordered, _ = steps.scene_order(plan)
    assert [s.id for s in ordered] == ["s_roof", "s_workbench", "s_beach", "e_together_scene", "e_solo_scene"]
    assert chapter_numbers(plan) == {"s_roof": 1, "s_workbench": 1, "s_beach": 2, "e_together_scene": 2, "e_solo_scene": 2}
    assert chapter_numbers(plan.model_copy(update={"chapters": []}))["e_together_scene"] == 4


def test_repair_outline_keeps_gates_playable() -> None:
    cast = Cast.model_validate(json.loads((SAMPLE / "cast.json").read_text()))
    plan = Outline(start="a", variables=[Variable(name="trust")], endings=[{"id": "e", "title": "E", "summary": "s"}],
                   scenes=[
                       PlannedScene(id="a", title="A", location="beach", summary="s",
                                    branches=[Branch(when=[Condition(var="trust")], goto="b"),
                                              Branch(when=[Condition(var="unknown")], goto="b"),
                                              Branch(when=[Condition(var="trust", value=3)], goto="c")]),
                       PlannedScene(id="b", title="B", location="beach", summary="s", ending="e"),
                       PlannedScene(id="c", title="C", location="beach", summary="s", ending="e",
                                    choices=[PlannedChoice(text="x", goto="a")]),
                   ])  # fmt: skip
    fixed, fixes = steps.repair_outline(plan, cast)
    a = fixed.scenes[0]
    assert [b.goto for b in a.branches] == ["b"] and a.next == "c"  # unknown var dropped; last gate → fallback
    assert fixed.scenes[2].ending is None  # choices win over an ending
    assert any("no fallback" in f for f in fixes)


def test_old_scripts_still_load(script: Script) -> None:
    old = {"text": "Go", "goto": "s_roof"}
    assert Choice(**old).reaction == [] and Node(kind="jump", goto="x").when == []
    assert Assign(var="v").op == "add"


def test_export_carries_chapters_and_stats(tmp_path: Path, home: Path) -> None:
    from assetforge.core.catalog import Library
    from assetforge.exporters.vn import export_story

    folder = tmp_path / "kite"
    shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("build"))
    with Library(home) as lib:
        result = export_story(lib, StoryProject(folder), tmp_path / "out")
    data = json.loads((tmp_path / "out" / "story.json").read_text())
    assert [c["id"] for c in data["script"]["chapters"]] == ["ch1", "ch2"]
    assert data["script"]["variables"][0]["max"] == 5 and Path(result["player"]).is_file()


def test_gate_without_fallback_falls_back_to_the_open_ending() -> None:
    from assetforge.story.models import Ending

    cast = Cast.model_validate(json.loads((SAMPLE / "cast.json").read_text()))
    plan = Outline(start="a", variables=[Variable(name="trust")], scenes=[
        PlannedScene(id="a", title="A", location="beach", summary="s",
                     branches=[Branch(when=[Condition(var="trust", value=4)], goto="good")]),
        PlannedScene(id="good", title="G", location="beach", summary="s", ending="e_good"),
        PlannedScene(id="quiet", title="Q", location="beach", summary="s", ending="e_quiet"),
    ], endings=[Ending(id="e_good", title="G", summary="s", requires=[Condition(var="trust", value=4)]),
                Ending(id="e_quiet", title="Q", summary="s")])  # fmt: skip
    fixed, _ = steps.repair_outline(plan, cast)
    assert fixed.scenes[0].next == "quiet" and [b.goto for b in fixed.scenes[0].branches] == ["good"]


def test_ending_scenes_named_after_their_ending_are_endings() -> None:
    from assetforge.story.models import Ending

    cast = Cast.model_validate(json.loads((SAMPLE / "cast.json").read_text()))
    plan = Outline(start="a", scenes=[
        PlannedScene(id="a", title="A", location="beach", summary="s", choices=[PlannedChoice(text="x", goto="end_good"),
                                                                                 PlannedChoice(text="y", goto="end_quiet")]),
        PlannedScene(id="end_good", title="G", location="beach", summary="s", ending="good"),  # the kind, not the id
        PlannedScene(id="end_quiet", title="Q", location="beach", summary="s"),
    ], endings=[Ending(id="end_good", title="G", summary="s"), Ending(id="end_quiet", title="Q", summary="s")])  # fmt: skip
    fixed, _ = steps.repair_outline(plan, cast)
    assert [(s.id, s.ending, s.next) for s in fixed.scenes[1:]] == [("end_good", "end_good", None), ("end_quiet", "end_quiet", None)]


def test_options_to_the_same_scene_stay_distinct(script: Script) -> None:
    chars = {c.id: c.expressions for c in script.characters}
    plan = PlannedScene(id="s", title="t", location="beach", summary="s", choices=[
        PlannedChoice(text="Tell the truth", goto="s_beach"), PlannedChoice(text="Keep the secret", goto="s_beach")])  # fmt: skip
    written = Scene(id="s", title="t", location="beach", nodes=[Node(kind="narrate", text="x"), Node(kind="choice", choices=[
        Choice(text="Tell them everything", goto="s_beach", set=[Assign(var="told_truth", op="set", value=1)]),
        Choice(text="Say nothing", goto="s_beach", set=[Assign(var="told_truth", op="set", value=0)])])])  # fmt: skip
    fixed, fixes = conform_scene(written, plan, chars)
    assert [c.text for c in fixed.nodes[-1].choices] == ["Tell them everything", "Say nothing"] and fixes == []
