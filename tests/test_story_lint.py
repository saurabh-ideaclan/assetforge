"""Phase 2: the narrative linter, chapters written in beat passes, and the linter's repair round."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.story import offline, prompts, steps
from assetforge.story.lint import lint_scene, lint_story, measure, parts_for, repair_note
from assetforge.story.models import Choice, Line, Node, Scene, StoryConfig
from assetforge.story.progress import _base, _label

from conftest import run_cli

CONFIG = StoryConfig(title="T", slug="t", premise="A test story.", chapter_words=600, chapter_choices=3)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def text(n: int) -> str:
    return " ".join(["word"] * n)


def inline(*reactions: int) -> Node:
    return Node(kind="choice", choices=[Choice(text=f"o{i}", reaction=[Line(text=text(w))]) for i, w in enumerate(reactions)])


def test_measure_reads_one_path() -> None:
    scene = Scene(id="s", title="S", location="x", nodes=[
        Node(kind="narrate", text=text(30)), Node(kind="narrate", text=text(30)),
        inline(10, 20, 30),                       # +20 words, +1 box on average
        Node(kind="say", speaker="a", text=text(50)),
        Node(kind="narrate", text=text(5), when=[{"var": "v"}]),
        Node(kind="jump", goto="t"),
    ])  # fmt: skip
    m = measure(scene)
    assert m["words"] == 135 and m["boxes"] == 5 and m["choices"] == 1 and m["first_choice_box"] == 3
    assert m["max_stretch"]["words"] == 75 and m["callbacks"] == 1 and m["long_boxes"] == 1


def test_hard_rules() -> None:
    wall = Scene(id="s", title="S", location="x", nodes=[*[Node(kind="narrate", text=text(25)) for _ in range(18)],
                                                         inline(5, 5, 5), Node(kind="jump", goto="t")])  # fmt: skip
    report = lint_scene(wall, CONFIG, number=3)
    codes = {i["code"] for i in report["issues"] if i["level"] == "error"}
    assert codes == {"wall_of_text", "few_choices", "late_first_choice", "no_callback"} and not report["ok"]
    note = repair_note(report)
    assert "450 words without a choice" in note and "600 words" in note
    ending = Scene(id="e", title="E", location="x", nodes=[Node(kind="narrate", text="Bye."), Node(kind="ending", ending="e")])
    assert lint_scene(ending, CONFIG, number=9)["ok"]  # epilogues are short


def test_old_stories_measure_like_the_gap_analysis() -> None:
    """Static Tomorrow-like chapters (~300 words, no choice) fail; the format sample's chapter 1 has callbacks."""
    sample = Path(__file__).resolve().parents[1] / "examples" / "kite-season"
    from assetforge.story.project import StoryProject

    report = lint_story(StoryProject(sample).load("script"), CONFIG)
    first = report["chapters"][0]
    assert first["scene"] == "s_roof" and first["choices"] == 3 and first["callbacks"] == 3


def test_parts_split_the_beats() -> None:
    assert parts_for(CONFIG.model_copy(update={"chapter_words": 400})) == 1 and parts_for(CONFIG.model_copy(update={"chapter_words": 1500})) == 4
    assert prompts.part_beats(1, 3)[0].startswith("hook") and prompts.part_beats(3, 3)[-1].startswith("the cliffhanger")
    assert sum(len(prompts.part_beats(k, 2)) for k in (1, 2)) == len(prompts.BEATS)
    assert "chapter 10: the endings" in prompts.season_arc(10)


def test_progress_groups_the_calls_of_one_chapter() -> None:
    assert _base("story.scene.s01.p2") == _base("story.scene.s01.fix") == "story.scene.s01"
    label = _label("story.scene.s01.p2", {"s01": "Fog"}, {"s01": 1})
    assert label == "Writing chapter 1 · Fog (part 2)"


def make_story(lib: Library, root: Path, **options) -> steps.Ctx:  # noqa: ANN003
    project = steps.init(root, title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake", **options)  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx), steps.cast(ctx), steps.outline(ctx)
    return ctx


def test_chapters_are_written_in_passes_and_meet_the_targets(lib: Library, tmp_path: Path, fake: None) -> None:
    ctx = make_story(lib, tmp_path / "s", chapter_words=1500, chapter_choices=6)
    result = steps.write(ctx, workers=1)
    tasks = [c["task"] for c in ctx.calls]
    assert {"scene.s01.p1", "scene.s01.p2", "scene.s01.p3", "scene.s01.p4"} <= set(tasks)
    assert "scene.e01" in tasks and "scene.e01.p1" not in tasks  # endings: one call
    assert not any(t.endswith(".fix") for t in tasks)  # the offline chapters already pass
    quality = json.loads((ctx.project.root / "lint.json").read_text())
    assert quality["summary"]["passing"] == quality["summary"]["chapters"] and result["quality"]["avg_words"] >= 1125
    s01 = ctx.project.load_scene("s01")
    assert [n.kind for n in s01.nodes].count("jump") == 1 and s01.nodes[-1].kind == "jump"  # one exit, at the end
    # part 2 was told what part 1 wrote
    prompt = (lib.paths.jobs / next(c["job_id"] for c in ctx.calls if c["task"] == "scene.s01.p2") / "prompt.txt")
    if prompt.exists():
        assert "ALREADY WRITTEN" in prompt.read_text()


def test_a_failing_chapter_gets_one_repair_round(lib: Library, tmp_path: Path, fake: None,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    ctx = make_story(lib, tmp_path / "s", chapter_words=600, chapter_choices=3)
    good = offline.scene

    def short(plan, cast, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        return lambda _: {"nodes": [{"kind": "narrate", "text": "Too short."}] * 3}

    fixed_with: list[str] = []

    def repair(current, note):  # noqa: ANN001, ANN202
        fixed_with.append(current.id)
        return good(next(p for p in ctx.project.load("outline").scenes if p.id == current.id), ctx.project.load("cast"),
                    CONFIG.model_copy(update={"chapter_words": 600, "chapter_choices": 3}), variables=["courage"])  # fmt: skip

    monkeypatch.setattr(offline, "scene", short)
    monkeypatch.setattr(offline, "refine", repair)
    result = steps.write(ctx, workers=1)
    assert "s01" in fixed_with and any("linter repair: too_short" in f for f in result["fixes"]["s01"])
    assert lint_scene(ctx.project.load_scene("s01"), ctx.config, 1)["ok"]


def test_cli_lint(home: Path, tmp_path: Path, fake: None, capsys: pytest.CaptureFixture[str]) -> None:
    with Library(home) as lib:
        ctx = make_story(lib, tmp_path / "s", chapter_words=400, chapter_choices=2)
        steps.write(ctx, workers=1)
    code, payload, _ = run_cli(capsys, "story", "lint", "--dir", str(tmp_path / "s"), "--json")
    assert code == 0 and payload["lint"]["summary"]["chapters"] == 4


def test_fix_lint_rewrites_failing_chapters_and_never_makes_them_worse(lib: Library, tmp_path: Path, fake: None,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    ctx = make_story(lib, tmp_path / "s", chapter_words=600, chapter_choices=3)
    steps.write(ctx, workers=1)
    s02 = ctx.project.load_scene("s02")
    ctx.project.save_scene(s02.model_copy(update={"nodes": [Node(kind="narrate", text="Short text here now."), s02.nodes[-1]]}))
    steps.assemble(ctx.project)
    monkeypatch.setattr(offline, "refine", lambda current, note: lambda _: {"nodes": [{"kind": "narrate", "text": "No."}] * 3})
    result = steps.fix_lint(ctx, rounds=2)
    assert result["fixed"]["s02"]["after"] == result["fixed"]["s02"]["before"]  # no better rewrite: kept as it was
    assert ctx.project.load_scene("s02").nodes[0].text == "Short text here now."
    good = offline.scene
    monkeypatch.setattr(offline, "refine", lambda current, note: good(
        next(p for p in ctx.project.load("outline").scenes if p.id == current.id), ctx.project.load("cast"),
        ctx.config, variables=["courage"]))  # fmt: skip
    result = steps.fix_lint(ctx, rounds=1)
    assert result["fixed"]["s02"]["after"] == [] and "s01" not in result["fixed"]
