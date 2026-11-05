"""Visual-novel stories end to end with the offline text and image providers: writing steps, validation and
automatic fixes, the asset manifest (keys, references, reuse), the demo export, and the CLI contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.errors import ConflictError
from assetforge.exporters.vn import export_story
from assetforge.request import AssetRequest, handle
from assetforge.story import steps
from assetforge.story.assets import manifest, produce
from assetforge.story.models import (Cast, Character, Choice, Ending, Location, Node, Outline, PlannedChoice, PlannedScene,
                                     Scene, Script, Variable)  # fmt: skip
from assetforge.story.project import StoryProject
from assetforge.story.validate import conform_scene, validate_script
from conftest import run_cli


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def written(lib: Library, root: Path, scenes: int = 5, endings: int = 2) -> StoryProject:
    project = steps.init(root, title="Fog Lantern", premise="An apprentice uncovers smugglers.", scenes=scenes,
                         endings=endings, provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx)
    steps.cast(ctx)
    steps.outline(ctx)
    result = steps.write(ctx)
    assert result["assembled"] and result["errors"] == 0, result
    return project


# ---------------------------------------------------------------------- writing


def test_steps_write_a_valid_branching_story_and_reuse_answers(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    script: Script = project.load("script")
    assert script.start == "s01" and len(script.endings) == 2
    assert {n.kind for s in script.scenes for n in s.nodes} >= {"say", "show", "choice", "jump", "ending", "sfx", "cg", "set"}
    assert (project.root / "notes.md").read_text().startswith("# Fog Lantern")
    # resumable + cached: a second write keeps every scene and makes no model call
    ctx = steps.Ctx(lib, project)
    again = steps.write(ctx)
    assert again["written"] == [] and len(again["kept"]) == 5 and ctx.calls == []
    # cached answers are reused (fresh=False) when a step is re-run
    steps.world(ctx)
    assert ctx.calls[-1]["reused"] is True


def test_refine_keeps_a_backup(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    before = project.scene_path("s02").read_text()
    result = steps.refine(steps.Ctx(lib, project), "s02", "more tension")
    assert Path(result["backup"]).read_text() == before
    assert "more tension" in project.scene_path("s02").read_text()
    report = steps.check(steps.Ctx(lib, project))
    assert report.issues == [] and project.has("check")


# ---------------------------------------------------------------------- validation


def _script(scenes: list[Scene], variables: list[Variable] | None = None) -> Script:
    return Script(title="T", slug="t", start="a", variables=variables or [],
                  characters=[Character(id="mira", name="Mira", role="lead", personality="p", look="l", expressions=["neutral", "smile"])],
                  locations=[Location(id="pier", name="Pier", description="d", times=["day"])],
                  endings=[Ending(id="good", title="Good", summary="s"), Ending(id="bad", title="Bad", summary="s")],
                  scenes=scenes)  # fmt: skip


def test_validator_finds_broken_stories() -> None:
    scenes = [
        Scene(id="a", title="A", location="pier", nodes=[
            Node(kind="say", speaker="mira", expression="furious", text="Hi"),
            Node(kind="say", speaker="ghost", text="Boo"),
            Node(kind="choice", choices=[Choice(text="Go", goto="b", set=[{"var": "trust", "value": 1}]),
                                         Choice(text="Nowhere", goto="zzz")])]),
        Scene(id="b", title="B", location="dock", nodes=[Node(kind="jump", goto="c")]),
        Scene(id="c", title="C", location="pier", nodes=[Node(kind="jump", goto="b")]),  # b ⇄ c loop, no ending
        Scene(id="d", title="D", location="pier", nodes=[Node(kind="narrate", text="lost")]),
    ]  # fmt: skip
    codes = {(i.code, i.where.split("#")[0]) for i in validate_script(_script(scenes))}
    assert {("unknown_expression", "a"), ("unknown_character", "a"), ("unknown_variable", "a"), ("bad_goto", "a"),
            ("unknown_location", "b"), ("no_path_to_ending", "b"), ("no_path_to_ending", "c"), ("no_exit", "d"),
            ("unreachable_scene", "d"), ("unreached_ending", "good")} <= codes  # fmt: skip


def test_conform_scene_enforces_the_plan() -> None:
    plan = PlannedScene(id="a", title="A", location="pier", summary="s",
                        choices=[PlannedChoice(text="Left", goto="b"), PlannedChoice(text="Right", goto="c")])  # fmt: skip
    scene = Scene(id="a", title="A", location="pier", nodes=[
        Node(kind="say", speaker="mira", expression="furious", text="Hi"),
        Node(kind="say", speaker="ghost", text="Boo"),
        Node(kind="jump", goto="somewhere"),
        Node(kind="choice", choices=[Choice(text="Right!", goto="c", set=[{"var": "x", "value": 1}])]),
    ])  # fmt: skip
    fixed, notes = conform_scene(scene, plan, {"mira": ["neutral", "smile"]})
    kinds = [n.kind for n in fixed.nodes]
    assert kinds == ["say", "narrate", "choice"]
    assert fixed.nodes[0].expression == "neutral"
    assert [c.goto for c in fixed.nodes[-1].choices] == ["b", "c"]
    assert fixed.nodes[-1].choices[1].text == "Right!" and fixed.nodes[-1].choices[1].set  # model's wording/effects kept
    assert len(notes) == 3


def test_outline_repair_gives_every_scene_one_exit() -> None:
    cast = Cast(characters=[Character(id="mira", name="M", role="r", personality="p", look="l", expressions=["neutral"])],
                locations=[Location(id="pier", name="P", description="d", times=["night"])])  # fmt: skip
    plan = Outline(start="nope", endings=[Ending(id="end", title="E", summary="s")], scenes=[
        PlannedScene(id="a", title="A", location="mars", time="day", characters=["mira", "x"], summary="s"),
        PlannedScene(id="b", title="B", location="pier", summary="s", choices=[PlannedChoice(text="t", goto="ghost")]),
    ])  # fmt: skip
    fixed, fixes = steps.repair_outline(plan, cast)
    a, b = fixed.scenes
    assert fixed.start == "a" and a.location == "pier" and a.time == "night" and a.characters == ["mira"]
    assert a.next == "b" and b.choices == [] and b.ending == "end" and len(fixes) >= 4


# ---------------------------------------------------------------------- assets + export


def test_manifest_keys_and_references(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    items = {i.key: i for i in manifest(project.load("script"), project.config, project.load("world"))}
    base = "vn/fog-lantern/char/mira/neutral"
    assert items[base].request.transparent is True and items[base].request.aspect == "2:3"
    assert items["vn/fog-lantern/char/mira/surprised"].refs == [base]
    assert items["vn/fog-lantern/bg/harbour/evening"].refs == ["vn/fog-lantern/bg/harbour/day"]
    assert "vn/fog-lantern/char/mira/worried" not in items  # expressions nobody uses are not made
    assert {"vn/fog-lantern/ui/textbox", "vn/fog-lantern/sfx/door_creak", "vn/fog-lantern/music/calm",
            "vn/fog-lantern/amb/lighthouse"} <= set(items)  # fmt: skip
    assert all(i.request.project == "vn-fog-lantern" for i in items.values())


def test_assets_export_and_rerun(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s", scenes=4, endings=2)
    # before any asset: the demo still exports, with placeholders
    early = export_story(lib, project, tmp_path / "early")
    assert early["assets"] == 0 and early["missing"] and (tmp_path / "early" / "index.html").exists()

    dry = produce(lib, project, dry_run=True, image_provider="fake")
    assert dry["counts"] == {"dry_run": dry["total"]} and lib.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0

    made = produce(lib, project, image_provider="fake")
    assert made["counts"].get("ready") == made["total"], made["items"]
    expr = next(i for i in made["items"] if i["group"] == "expr")
    base_id = next(i["asset_id"] for i in made["items"] if i["key"] == "vn/fog-lantern/char/mira/neutral")
    assert expr["references"] == [base_id]
    related = {r["related_asset_id"] for r in lib.conn.execute("SELECT related_asset_id FROM asset_lineage WHERE asset_id = ?", (expr["asset_id"],))}
    assert base_id in related
    music = lib.get_asset(next(i["asset_id"] for i in made["items"] if i["group"] == "music"))
    assert music.key and music.key.startswith("vn/fog-lantern/music/")

    again = produce(lib, project, image_provider="fake")
    assert again["counts"] == {"exists": made["total"]}

    out = tmp_path / "demo"
    result = export_story(lib, project, out)
    assert result["missing"] == [] and result["assets"] == made["total"]
    data = json.loads((out / "story.json").read_text())
    for entry in data["assets"].values():
        for rel in [entry.get("image"), *entry.get("audio", [])]:
            if rel:
                assert (out / rel).exists()
    assert (out / "story.js").read_text().startswith("window.STORY = ")
    assert "assetforge-vn-player" in (out / "index.html").read_text()


def test_export_never_overwrites_a_foreign_player(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    out = tmp_path / "game"
    out.mkdir()
    (out / "index.html").write_text("<html>my game</html>")
    with pytest.raises(ConflictError):
        export_story(lib, project, out)
    export_story(lib, project, out, overwrite=True)


def test_limit_and_only(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    first = produce(lib, project, only=["chars"], limit=1, image_provider="fake")
    states = [i["state"] for i in first["items"]]
    assert states.count("ready") == 1 and {i["group"] for i in first["items"]} <= {"char", "expr"}
    assert "skipped" in states


def test_music_request_keeps_its_musical_key(lib: Library) -> None:
    """Regression: a keyed music request used to pass the asset key as the musical key."""
    result = handle(lib, AssetRequest(type="music", description="calm piano loop", key="game/music/calm",
                                      project="demo", sound={"key": "D minor", "bars": 4}))  # fmt: skip
    asset = lib.get_asset(result.asset_id)
    assert asset.key == "game/music/calm"
    assert "D minor" in json.dumps(asset.metadata)


# ---------------------------------------------------------------------- CLI


def test_cli_make_validate_status_export(home: Path, tmp_path: Path, fake: None, capsys: pytest.CaptureFixture[str]) -> None:
    story = tmp_path / "story"
    code, out, _ = run_cli(capsys, "story", "make", "An apprentice uncovers smugglers in a foggy town", "--title",
                           "Fog Lantern", "--scenes", "4", "--endings", "2", "--provider", "fake", "--dir", str(story), "--json")  # fmt: skip
    assert code == 0 and out["status"] == "ok" and out["write"]["assembled"]
    assert Path(out["export"]["player"]).exists() and out["export"]["missing"]
    code, out, _ = run_cli(capsys, "story", "validate", "--dir", str(story), "--json")
    assert code == 0 and out["errors"] == 0
    code, out, _ = run_cli(capsys, "story", "status", "--dir", str(story), "--json")
    assert out["steps"]["script"] and out["assets"]["have"] == 0 and out["assets"]["total"] > 5
    code, out, _ = run_cli(capsys, "story", "assets", "--dir", str(story), "--only", "ui", "--image-provider", "fake", "--json")
    assert code == 8 and out["status"] == "partial" and out["counts"] == {"ready": 2, "blocked": 1}
    blocked = next(i for i in out["items"] if i["state"] == "blocked")
    assert blocked["key"].endswith("ui/title") and "char/" in blocked["reason"]  # title art needs the sprites first
    code, out, _ = run_cli(capsys, "llm", "test", "--provider", "fake", "--json")
    assert code == 0 and out["ok"] and out["billing"] == "offline"


def test_cli_reports_missing_login_as_json(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "llm", "test", "--json")
    assert code == 9 and out["status"] == "error" and out["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    code, out, _ = run_cli(capsys, "llm", "providers", "--json")
    assert code == 0 and out["auto"] == [] and {p["name"] for p in out["providers"]} >= {"claude-cli", "codex-cli"}


def test_write_keeps_finished_scenes_when_one_fails(lib: Library, tmp_path: Path, fake: None,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    from assetforge.errors import ProviderError
    from assetforge.story import offline

    project = steps.init(tmp_path / "s", title="Fog Lantern", premise="An apprentice uncovers smugglers.", scenes=5,
                         endings=2, provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx)
    steps.cast(ctx)
    steps.outline(ctx)
    real = offline.scene

    def flaky(plan, cast):  # noqa: ANN001, ANN202
        if plan.id == "s02":
            def boom(_):  # noqa: ANN001, ANN202
                raise ProviderError("usage limit reached", provider="fake", kind="quota")
            return boom
        return real(plan, cast)

    monkeypatch.setattr(offline, "scene", flaky)
    with pytest.raises(ProviderError):
        steps.write(steps.Ctx(lib, project), workers=3)
    assert set(project.scenes()) == {"s01", "s03", "e01", "e02"}  # everything else was saved
    monkeypatch.setattr(offline, "scene", real)
    again = steps.write(steps.Ctx(lib, project), workers=3)
    assert again["written"] == ["s02"] and again["assembled"] and again["errors"] == 0
