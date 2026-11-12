"""The screen a story is read on. The stories are consumed in the mobile app, on a phone held upright, so new stories
are portrait: backgrounds, illustrations and title art are requested as tall 9:16 pictures composed for that frame.
Stories from before the setting (`screen` unset) stay landscape with their prompts unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.core.models import AssetType
from assetforge.exporters.vn import export_story
from assetforge.image import prompts
from assetforge.story import steps, visual_canon
from assetforge.story.assets import manifest, produce, screen_report
from assetforge.story.models import StoryConfig
from assetforge.story.progress import preview_data
from assetforge.story.project import StoryProject
from conftest import run_cli


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def written(lib: Library, root: Path, **options: object) -> StoryProject:
    project = steps.init(root, title="Fog Lantern", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake", **options)  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx)
    steps.cast(ctx)
    steps.outline(ctx)
    assert steps.write(ctx)["assembled"]
    return project


def _set_screen(project: StoryProject, screen: str | None) -> None:
    project.save("config", project.config.model_copy(update={"screen": screen}))


def test_new_stories_are_portrait_and_older_ones_stay_landscape(tmp_path: Path) -> None:
    assert steps.init(tmp_path / "new", title="T", premise="A keeper finds a letter.").config.screen == "portrait"
    assert steps.init(tmp_path / "wide", title="T", premise="A keeper finds a letter.", screen="landscape").config.screen == "landscape"
    old = StoryConfig(title="T", slug="t", premise="A keeper finds a letter.")  # a story.json written before the setting
    assert old.screen is None and old.screen_shape == "landscape"


def test_portrait_pictures_are_tall_and_composed_for_a_phone(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    script, world = project.load("script"), project.load("world")
    tall = {i.key: i for i in manifest(script, project.config, world)}
    by_group: dict[str, set[str]] = {}
    for item in tall.values():
        if item.request.aspect:
            by_group.setdefault(item.group, set()).add(item.request.aspect)
    assert by_group["bg"] == {"9:16"} and by_group.get("bg_time", {"9:16"}) == {"9:16"}
    assert by_group["char"] == {"2:3"}  # sprites were already upright
    assert tall["vn/fog-lantern/ui/title"].request.aspect == "9:16"
    assert tall["vn/fog-lantern/ui/textbox"].request.aspect == "16:9" and tall["vn/fog-lantern/ui/choice"].request.aspect == "21:9"
    bg = next(i for i in tall.values() if i.group == "bg").request.description
    assert "phone held upright" in bg and "wide view" not in bg and "lower third" in bg
    assert "phone held upright" in tall["vn/fog-lantern/ui/title"].request.description
    cgs = [i for i in tall.values() if i.group == "cg"]
    assert all(i.request.aspect == "9:16" and "phone held upright" in i.request.description for i in cgs)

    # the same story before the setting existed: the earlier requests, word for word
    _set_screen(project, None)
    wide = {i.key: i for i in manifest(script, project.config, world)}
    bg = next(i for i in wide.values() if i.group == "bg").request
    assert bg.aspect == "16:9" and bg.description.endswith("eye-level wide view with room for characters in the foreground.")
    assert wide["vn/fog-lantern/ui/title"].request.aspect == "16:9" and wide["vn/fog-lantern/ui/textbox"].request.aspect == "21:9"
    assert "phone" not in " ".join(i.request.description for i in wide.values())


def test_a_tall_background_is_not_told_to_compose_wide() -> None:
    assert "wide" in prompts.framing(AssetType.BACKGROUND, "16:9") and "wide" in prompts.framing(AssetType.BACKGROUND)
    assert "tall" in prompts.framing(AssetType.BACKGROUND, "9:16") and "wide" not in prompts.framing(AssetType.BACKGROUND, "9:16")
    assert prompts.framing(AssetType.CHARACTER, "9:16") == prompts.FRAMING[AssetType.CHARACTER]
    built = prompts.build_prompt("A harbour at dusk", asset_type=AssetType.BACKGROUND, transparent=False,
                                 native_transparency=True, aspect="9:16")  # fmt: skip
    assert prompts.TALL_BACKGROUND in built.final and "wide composition" not in built.final


def test_the_visual_canon_is_written_for_the_frame(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    script = project.load("script")
    assert "TALL 9:16" in visual_canon.prompt(project, script)
    _set_screen(project, None)
    assert "9:16" not in visual_canon.prompt(project, script)


def test_pictures_in_the_other_shape_are_reported_and_remade(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s", screen="landscape")
    produce(lib, project, only=["bg"], image_provider="fake")
    report = screen_report(lib, project)
    assert report["screen"] == "landscape" and report["checked"] >= 1 and report["wrong"] == []

    _set_screen(project, "portrait")  # the story moves to the phone: its wide backgrounds are now the wrong shape
    report = screen_report(lib, project)
    assert len(report["wrong"]) == report["checked"] and all(w["width"] > w["height"] for w in report["wrong"])

    produce(lib, project, only=["bg"], image_provider="fake", redo=True)
    report = screen_report(lib, project)
    assert report["checked"] >= 1 and report["wrong"] == []


def test_the_player_is_told_the_screen(lib: Library, tmp_path: Path, fake: None) -> None:
    project = written(lib, tmp_path / "s")
    assert preview_data(lib, project)["screen"] == "portrait"
    export_story(lib, project, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "story.json").read_text())["screen"] == "portrait"
    player = (tmp_path / "out" / "index.html").read_text()
    assert 'DATA.screen === "portrait"' in player and "body.portrait #stage" in player


def test_cli_screen(lib: Library, tmp_path: Path, fake: None, capsys: pytest.CaptureFixture[str]) -> None:
    project = written(lib, tmp_path / "s", screen="landscape")
    code, out, _ = run_cli(capsys, "story", "screen", "--dir", str(project.root), "--json")
    assert code == 0 and out["screen"] == "landscape"
    code, out, _ = run_cli(capsys, "story", "screen", "portrait", "--dir", str(project.root), "--json")
    assert code == 0 and out["screen"] == "portrait" and StoryProject(project.root).config.screen == "portrait"
    code, out, _ = run_cli(capsys, "story", "screen", "square", "--dir", str(project.root), "--json")
    assert code == 4 and out["error"]["code"] == "INVALID_INPUT"
