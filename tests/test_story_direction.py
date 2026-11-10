"""Creative direction: schema and presets, precedence, contradiction warnings, the naming-guide step, prompt
integration (and the unchanged behaviour of stories without a direction), the impact review, the app API and the CLI."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.core.catalog import Library
from assetforge.errors import ConflictError, InvalidInputError
from assetforge.story import direction as creative
from assetforge.story import prompts, steps
from assetforge.story.models import Cast, Character, Location, Outline, PlannedChoice, PlannedScene, StoryConfig
from assetforge.story.project import StoryProject
from conftest import run_cli
from test_ui_server import jcall


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


# ---------------------------------------------------------------------- schema, presets, precedence


def test_taxonomy_and_presets_load() -> None:
    g, t = creative.genres(), creative.traditions()
    assert "power-fantasy" in g["primary"] and {"regression", "progression", "revenge-rise", "hidden-power"} <= set(g["subgenres"])
    assert {"korean-webnovel", "japanese-light-novel", "chinese-webnovel", "western-fantasy-novel", "short-form-drama"} <= set(t)
    names = {p.name for p in creative.presets()}
    assert "medieval-regression-power-fantasy" in names and all(p.source == "built-in" for p in creative.presets())
    opts = creative.options()
    assert opts["genres"] and opts["subgenres"] and opts["traditions"] and "region" in opts["help"]


def test_resolve_precedence_preset_then_explicit(home: Path) -> None:
    preset = creative.get_preset("medieval-regression-power-fantasy")
    d = creative.resolve({"romance": "medium", "world": {"magic": "Ley-lines only."}}, preset="medieval-regression-power-fantasy")
    assert d.preset == "medieval-regression-power-fantasy" and d.tradition == preset.direction.tradition
    assert d.romance == "medium" and d.world.magic == "Ley-lines only." and d.subgenres == preset.direction.subgenres
    # an explicit field equal to the default still overrides the preset
    d2 = creative.resolve({"pacing": "fast", "romance": "low"}, preset="villainess-court-intrigue")
    assert d2.romance == "low" and d2.pacing == "fast" and d2.genre == "romance-drama"
    # user presets live in the library and win over a built-in of the same name
    path = creative.save_preset(home, "My Preset", d, title="Mine", summary="s")
    assert path.parent == home / "story_presets"
    mine = creative.get_preset("my-preset", home)
    assert mine.source == "user" and mine.direction.romance == "medium"
    creative.save_preset(home, "cozy-mystery", d2, title="override")
    assert creative.get_preset("cozy-mystery", home).source == "user"
    creative.delete_preset(home, "my-preset")
    with pytest.raises(Exception):
        creative.get_preset("my-preset", home)


def test_warnings_name_contradictions_not_refuse() -> None:
    d = creative.CreativeDirection(genre="power-fantasy", subgenres=["regression", "overpowered-protagonist", "medieval-kingdom"],
                                   world_type="modern-fantasy", naming="korean", culture="western-european", romance="none",
                                   tone=["romantic"], writing_style="custom")  # fmt: skip
    codes = {w["code"] for w in creative.warnings(d)}
    assert {"subgenre_conflict", "world_vs_subgenre", "naming_vs_culture", "romance_vs_tone", "style_note_missing"} <= codes
    assert creative.warnings(creative.get_preset("medieval-regression-power-fantasy").direction) == []
    assert "Power Fantasy" in creative.summary(d) and "Korean" in creative.summary(d)


def test_old_story_config_without_direction_is_unchanged(tmp_path: Path) -> None:
    data = {"title": "Old", "slug": "old", "premise": "A thing happens.", "genre": "mystery", "audience": "teen"}
    config = StoryConfig(**data)
    assert config.direction is None
    assert "WRITING STYLE" not in prompts.system(config) and "direction" not in json.loads(prompts.bible(config).split("\n", 1)[1])
    assert "CREATIVE DIRECTION" not in prompts.world_prompt(config)


# ---------------------------------------------------------------------- the pipeline with a direction


def _story(lib: Library, root: Path, direction: creative.CreativeDirection) -> tuple[StoryProject, steps.Ctx]:
    project = steps.init(root, title="Ash Crown", premise="A betrayed knight returns three years earlier.", scenes=5,
                         endings=2, provider="fake", direction=direction)  # fmt: skip
    return project, steps.Ctx(lib, project)


def test_names_step_runs_before_cast_and_overrides_apply(lib: Library, tmp_path: Path, fake: None) -> None:
    d = creative.resolve({"reserved_names": ["Vaelith"], "name_overrides": {"protagonist": "Vaelith"}},
                         preset="medieval-regression-power-fantasy")  # fmt: skip
    project, ctx = _story(lib, tmp_path / "s", d)
    steps.world(ctx)
    cast = steps.cast(ctx)
    assert [c["task"] for c in ctx.calls][:3] == ["world", "names", "cast"]
    guide = project.load("names")
    assert "Vaelith" in guide.reserved and guide.given_names and project.has("names")
    assert cast.characters[0].name == "Vaelith"  # the operator's fixed name wins
    assert cast.characters[1].name in guide.given_names  # the writer followed the guide
    # prompts carry the direction at every step
    world_p = prompts.world_prompt(project.config)
    assert "CREATIVE DIRECTION" in world_p and "Medieval fantasy" in world_p and "VISUAL DIRECTION" in world_p
    assert "Korean web novel" in world_p and "Regression" in world_p and "PREMISE SHEET" in world_p
    assert "WRITING STYLE: Simple" in prompts.system(project.config) and "Infer nothing" in prompts.system(project.config)
    assert "NAMING GUIDE" in prompts.cast_prompt(project.config, project.load("world"), guide)
    outline_p = prompts.outline_prompt(project.config, project.load("world"), cast)
    assert "SEASON RULES" in outline_p and "DIFFERENT scenes" in outline_p
    plan, _ = steps.outline(ctx)
    scene_p = prompts.scene_prompt(project.config, project.load("world"), cast, plan, plan.scenes[1], ["before"],
                                   upcoming=["Next: the duel"], decided=["flag_told"])  # fmt: skip
    assert "CHAPTER SHAPE" in scene_p and "WHAT COMES NEXT" in scene_p and "flag_told" in scene_p
    assert "creative direction" in prompts.check_prompt(project.config, project.load("world"), cast, [])
    result = steps.write(ctx)
    assert result["errors"] == 0 and len(result["written"]) == 5


def test_story_without_direction_skips_names(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "p", title="Plain", premise="An apprentice uncovers smugglers.", scenes=4, endings=2, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx)
    steps.cast(ctx)
    assert not project.has("names") and [c["task"] for c in ctx.calls] == ["world", "cast"]


def test_art_direction_comes_from_the_visual_choice_not_the_tradition(lib: Library, tmp_path: Path, fake: None) -> None:
    from assetforge import art
    from assetforge.story.assets import ensure_art_profile, story_art_direction

    d = creative.resolve({"art_style": "western-fantasy", "color_mood": "cold dawn light"}, preset="medieval-regression-power-fantasy")
    project, ctx = _story(lib, tmp_path / "a", d)
    world = steps.world(ctx)
    direction = story_art_direction(project.config, world)
    assert direction.style == "western-fantasy" and direction.mood == "cold dawn light" and "power fantasy" in direction.genre
    rendered = art.render(direction, preset=art.get_preset(None, "western-fantasy"))
    assert "Western fantasy" in rendered and "manhwa" not in rendered.lower()
    assert ensure_art_profile(lib, project.config, world) is True
    assert art.find_profile(lib, project.config.asset_project).direction.style == "western-fantasy"


def test_repair_outline_turns_single_destination_choices_into_next() -> None:
    cast = Cast(characters=[Character(id="a", name="A", role="protagonist", personality="p", look="l", expressions=["neutral"])],
                locations=[Location(id="hall", name="Hall", description="d", times=["day"])])  # fmt: skip
    plan = Outline(start="s1", scenes=[
        PlannedScene(id="s1", title="One", location="hall", time="day", summary="s", choices=[
            PlannedChoice(text="Fight", goto="s2"), PlannedChoice(text="Talk", goto="s2"), PlannedChoice(text="Wait", goto="s2")]),
        PlannedScene(id="s2", title="Two", location="hall", time="day", summary="s", ending="e"),
    ], endings=[{"id": "e", "title": "End", "summary": "s"}])  # fmt: skip
    fixed, fixes = steps.repair_outline(plan, cast)
    assert fixed.scenes[0].choices == [] and fixed.scenes[0].next == "s2"
    assert any("one destination" in f for f in fixes)


def test_impact_review_lists_only_stale_parts() -> None:
    old = creative.get_preset("medieval-regression-power-fantasy").direction
    new = old.model_copy(update={"naming": "korean", "writing_style": "literary"})
    assert creative.impact(old, new, {"world": False, "cast": False, "outline": False, "chapters": False, "assets": False}) == []
    notes = creative.impact(old, new, {"world": True, "cast": True, "outline": True, "chapters": True, "assets": False})
    assert any("named" in n for n in notes) and any("English" in n for n in notes) and not any("Art" in n for n in notes)


# ---------------------------------------------------------------------- app API


@pytest.fixture
def server(home: Path, fake: None):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def test_direction_api_presets_check_create_and_settings_impact(server: AppServer, home: Path) -> None:
    status, info = jcall(server, "/api/story/direction")
    assert status == 200 and info["options"]["regions"]["global"] and any(p["name"] == "medieval-regression-power-fantasy" for p in info["presets"])
    status, chk = jcall(server, "/api/story/direction/check", method="POST",
                        body={"preset": "medieval-regression-power-fantasy", "direction": {"romance": "none", "tone": ["romantic"]}})  # fmt: skip
    assert status == 200 and chk["direction"]["romance"] == "none" and any(w["code"] == "romance_vs_tone" for w in chk["warnings"])
    status, saved = jcall(server, "/api/story/presets", method="POST",
                          body={"name": "Team Default", "direction": chk["direction"], "summary": "ours"})  # fmt: skip
    assert status == 200 and saved["preset"]["source"] == "user" and (home / "story_presets" / "team-default.yaml").exists()
    status, made = jcall(server, "/api/stories", method="POST", body={
        "title": "Ash Crown", "premise": "A betrayed knight returns three years earlier.", "scenes": 4, "endings": 2,
        "provider": "fake", "preset": "team-default", "direction": {"romance": "medium"}})  # fmt: skip
    assert status == 200
    sid = made["id"]
    _, detail = jcall(server, f"/api/stories/{sid}")
    assert detail["config"]["direction"]["preset"] == "team-default" and detail["config"]["direction"]["romance"] == "medium"
    assert "Power Fantasy" in detail["direction"]["summary"]
    # nothing written yet: a direction change needs no confirmation
    status, res = jcall(server, f"/api/stories/{sid}/settings", method="POST", body={"direction": {"preset": "team-default", "naming": "korean"}})
    assert status == 200 and res["config"]["direction"]["naming"] == "korean" and not res.get("needs_confirm")
    # write the world and cast, then a naming change asks for confirmation first
    _, task = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    from test_story_app import wait_task

    assert wait_task(server, task["task_id"])["state"] == "done"
    _, detail = jcall(server, f"/api/stories/{sid}")
    assert detail["names"]["given_names"]
    status, res = jcall(server, f"/api/stories/{sid}/settings", method="POST", body={"direction": {"preset": "team-default", "naming": "japanese"}})
    assert status == 200 and res["needs_confirm"] and any("named" in n for n in res["impact"])
    _, again = jcall(server, f"/api/stories/{sid}")
    assert again["config"]["direction"]["naming"] == "korean"  # unchanged until confirmed
    status, res = jcall(server, f"/api/stories/{sid}/settings", method="POST",
                        body={"direction": {"preset": "team-default", "naming": "japanese"}, "confirm": True})  # fmt: skip
    assert status == 200 and res["config"]["direction"]["naming"] == "japanese"
    status, gone = jcall(server, "/api/story/presets/team-default/delete", method="POST", body={})
    assert status == 200 and not (home / "story_presets" / "team-default.yaml").exists()


# ---------------------------------------------------------------------- CLI


def test_cli_init_with_preset_and_direction_file(home: Path, tmp_path: Path, fake: None, capsys: pytest.CaptureFixture[str]) -> None:
    extra = tmp_path / "dir.json"
    extra.write_text(json.dumps({"romance": "medium", "reserved_names": ["Vaelith"]}))
    code, out, _ = run_cli(capsys, "story", "init", "Ash Crown", "-p", "A betrayed knight returns.", "--dir", str(tmp_path / "s"),
                           "--preset", "medieval-regression-power-fantasy", "--direction", f"@{extra}", "--provider", "fake", "--json")  # fmt: skip
    assert code == 0 and out["config"]["direction"]["romance"] == "medium" and out["config"]["direction"]["reserved_names"] == ["Vaelith"]
    code, listed, _ = run_cli(capsys, "story", "presets", "--json")
    assert code == 0 and any(p["name"] == "cultivation-rise" for p in listed["presets"])
    code, shown, _ = run_cli(capsys, "story", "direction", "--dir", str(tmp_path / "s"), "--set", "romance=high", "--json")
    assert code == 0 and shown["direction"]["romance"] == "high" and "Power Fantasy" in shown["summary"]
    code, saved, _ = run_cli(capsys, "story", "presets", "--save", "house-style", "--dir", str(tmp_path / "s"), "--json")
    assert code == 0 and any(p["name"] == "house-style" and p["source"] == "user" for p in saved["presets"])
    # a written story refuses a stale-making change without --yes
    project = StoryProject(tmp_path / "s")
    ctx = steps.Ctx(Library(home), project)
    steps.world(ctx)
    steps.cast(ctx)
    code, out, _ = run_cli(capsys, "story", "direction", "--dir", str(tmp_path / "s"), "--set", "naming=korean", "--json")
    assert code != 0 and out["status"] == "error" and "stale" in out["error"]["message"]
    code, out, _ = run_cli(capsys, "story", "direction", "--dir", str(tmp_path / "s"), "--set", "naming=korean", "--yes", "--json")
    assert code == 0 and out["direction"]["naming"] == "korean" and out["impact"]


def test_cast_prompt_carries_canon_and_outline_repair_unties_unknown_characters(lib: Library, tmp_path: Path, fake: None) -> None:
    from assetforge.story.models import Variable

    d = creative.resolve({"custom": {"canon": "Cast: Nell Dray (12, the sister). Brother Hesketh Lind (deaf mentor)."}},
                         preset="medieval-regression-power-fantasy")  # fmt: skip
    project, ctx = _story(lib, tmp_path / "c", d)
    world = steps.world(ctx)
    assert "ESTABLISHED CANON" in prompts.cast_prompt(project.config, world, None) and "Hesketh" in prompts.cast_prompt(project.config, world, None)
    cast = Cast(characters=[Character(id="a", name="A", role="protagonist", personality="p", look="l", expressions=["neutral"])],
                locations=[Location(id="hall", name="Hall", description="d", times=["day"])])  # fmt: skip
    plan = Outline(start="s1", variables=[Variable(name="trust_x", kind="relationship", character="nobody", initial=0)],
                   scenes=[PlannedScene(id="s1", title="One", location="hall", time="day", summary="s", ending="e")],
                   endings=[{"id": "e", "title": "End", "summary": "s"}])  # fmt: skip
    fixed, fixes = steps.repair_outline(plan, cast)
    assert fixed.variables[0].character is None and any("untied" in f for f in fixes)
