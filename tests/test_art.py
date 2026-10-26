"""Art direction: presets, project art profiles, merge order, prompts and CLI."""

from __future__ import annotations

import pytest

from assetforge import art
from assetforge.config import Settings
from assetforge.core import styles
from assetforge.core.models import AssetType
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.image import engine, providers
from assetforge.image.generate import ImageRequest, engine_input, generate_image
from assetforge.image.prompts import SheetSpec, build_prompt
from assetforge.image.providers.fake import FakeProvider

from conftest import run_cli

SETTINGS = Settings({})
PROFILE_YAML = """
game: space_survivor
style: sci-fi
mood: dark
perspective: side_view
palette:
  - "#17212D"
  - "#38536B"
  - "#FF7A32"
lighting: cinematic
detail: high
avoid:
  - childish_shapes
  - toy_like_objects
  - generic_cubes
"""
REQUIRED = {"horror", "dark-fantasy", "pixel-art", "realistic-2d", "semi-realistic", "hand-drawn", "flat-vector",
            "cartoon", "anime", "sci-fi", "cyberpunk", "retro-arcade", "isometric", "minimalist", "paper-cut",
            "hand-painted", "casual-mobile"}  # fmt: skip


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    providers.OVERRIDES["gemini"] = FakeProvider(SETTINGS, name="gemini", native_transparency=False)
    providers.OVERRIDES["openai"] = FakeProvider(SETTINGS, name="openai", native_transparency=True)


def gen(lib, **kwargs):
    kwargs.setdefault("provider", "gemini")
    return generate_image(lib, ImageRequest(**kwargs), SETTINGS)


def test_builtin_presets_describe_a_look_not_just_a_name(lib):
    found = art.presets(lib)
    assert REQUIRED <= set(found)
    for preset in found.values():
        assert preset.summary and preset.description and preset.best_for, preset.name
        described = [preset.lighting, preset.shapes, preset.outline, preset.shading, preset.texture]
        assert sum(1 for x in described if x) >= 3, preset.name  # visual characteristics, not a bare label
    assert "#0E0E12" in found["dark-fantasy"].palette and "cute faces" in found["dark-fantasy"].avoid


def test_library_presets_add_and_override(lib):
    folder = lib.paths.home / "presets"
    folder.mkdir()
    (folder / "ink-noir.yaml").write_text("summary: Ink noir\nlighting: hard rim light\nshading: solid black pools\n")
    (folder / "horror.yaml").write_text("summary: My horror\nmood: quiet dread\n")
    found = art.presets(lib)
    assert found["ink-noir"].lighting == "hard rim light" and found["ink-noir"].source.endswith("ink-noir.yaml")
    assert found["horror"].summary == "My horror"
    (folder / "broken.yaml").write_text("mood: [unclosed")
    with pytest.raises(InvalidInputError):
        art.presets(lib)


def test_merge_order_and_yaml_round_trip(lib):
    project, direction = art.parse_profile_yaml(PROFILE_YAML)
    profile = art.set_profile(lib, project, direction)
    assert profile.project == "space-survivor" and profile.direction.palette[2] == "#FF7A32"
    again_project, again = art.parse_profile_yaml(art.profile_yaml(profile))
    assert again_project == "space-survivor" and again == profile.direction

    # profile only: sci-fi preset under the profile's own fields
    resolved = art.resolve(lib, project="space-survivor", style=None, art=None)
    assert resolved.layers == ["preset:sci-fi", "profile:space-survivor"]
    assert resolved.direction.mood == "dark" and resolved.direction.shapes.startswith("hard-surface")
    assert {"generic_cubes", "cute faces"} <= set(resolved.direction.avoid)  # avoid accumulates

    # a request style overrides the profile's look; request art overrides everything per field
    resolved = art.resolve(lib, project="space-survivor", style="horror",
                           art=art.ArtDirection(lighting="green emergency strobes"))  # fmt: skip
    assert resolved.layers == ["preset:sci-fi", "profile:space-survivor", "preset:horror", "request:art"]
    assert resolved.style == "horror" and resolved.direction.lighting == "green emergency strobes"
    assert resolved.direction.palette[0] == "#07090A"  # horror palette replaces the profile's

    # fully custom style text, no preset
    resolved = art.resolve(lib, project=None, style="ukiyo-e woodblock print with flat indigo washes", art=None)
    assert resolved.preset is None and resolved.style == "ukiyo-e-woodblock-print-with-flat-indigo-washes"
    assert "ukiyo-e woodblock print" in resolved.text()

    # merging, replacing, deleting
    art.set_profile(lib, "space-survivor", art.ArtDirection(mood="bleak"))
    assert art.get_profile(lib, "space-survivor").direction.lighting == "cinematic"
    art.set_profile(lib, "space-survivor", art.ArtDirection(mood="bleak"), replace=True)
    assert art.get_profile(lib, "space-survivor").direction.lighting is None
    art.delete_profile(lib, "space-survivor")
    with pytest.raises(NotFoundError):
        art.get_profile(lib, "space-survivor")
    with pytest.raises(InvalidInputError):
        art.parse_profile_yaml("palette: [not-a-colour]")


def test_legacy_style_profiles_still_apply(lib):
    styles.create_style(lib, "ink-wash", description="sumi ink wash", palette=["#111111"])
    resolved = art.resolve(lib, project=None, style="ink-wash", art=None)
    assert resolved.layers == ["style:ink-wash"] and "sumi ink wash" in resolved.text()


def test_generation_uses_profile_quality_and_metadata(lib):
    art.set_profile(lib, *art.parse_profile_yaml(PROFILE_YAML))
    art.set_profile(lib, "space-survivor", art.ArtDirection(genre="survival", theme="derelict station"))
    result = gen(lib, prompt="scavenger drone with a cracked sensor eye", type="enemy", project="space-survivor",
                 purpose="common enemy of level 1")  # fmt: skip
    p = result.prompt
    assert "Purpose in the game: common enemy of level 1." in p and "A single enemy for a game" in p
    assert "Mood: dark." in p and "#17212D" in p and "Do not include:" in p and "generic cubes" in p
    assert "Made for a survival game with a derelict station theme." in p
    asset = lib.get_asset(result.assets[0].asset_id)
    assert (asset.type, asset.style, asset.genre, asset.theme) == ("enemy", "sci-fi", "survival", "derelict-station")
    gen_meta = asset.metadata["generator"]
    assert gen_meta["art_layers"] == ["preset:sci-fi", "profile:space-survivor"]
    inp, _ = engine_input(lib, ImageRequest(prompt="drone", type="enemy", project="space-survivor"))
    assert inp.quality == "high"  # detail: high asks for larger images

    # same request after the profile changes → new job, not the cached one
    art.set_profile(lib, "space-survivor", art.ArtDirection(mood="hopeful"))
    again = gen(lib, prompt="scavenger drone with a cracked sensor eye", type="enemy", project="space-survivor",
                purpose="common enemy of level 1")  # fmt: skip
    assert again.job_id != result.job_id and "Mood: hopeful." in again.prompt
    # --no-profile drops it
    plain = gen(lib, prompt="scavenger drone", type="enemy", project="space-survivor", use_profile=False, dry_run=True)
    assert "Mood" not in plain.prompt and plain.art_layers == []


def test_sheet_framing_and_reference_labels():
    props = build_prompt("forest props", asset_type=AssetType.SPRITESHEET, transparent=True, native_transparency=False,
                         sheet=SheetSpec(columns=3, rows=2, one_character=False, poses=["stump", "rock", "bush"]))  # fmt: skip
    assert "same character" not in props.final and "3 columns by 2 rows (6 equal cells)" in props.final
    assert "Row 1, left to right: 1. stump; 2. rock; 3. bush." in props.final
    walk = build_prompt("knight", asset_type=AssetType.SPRITESHEET, transparent=True, native_transparency=True,
                        sheet=SheetSpec(columns=4, poses=["contact", "down", "pass", "up"]), user_refs=1, style_refs=2)  # fmt: skip
    assert "same character with an identical design" in walk.final
    assert "Image 1: subject reference" in walk.final and "Images 2-3: art-style reference only" in walk.final
    keyed = build_prompt("orchid", asset_type=AssetType.OBJECT, transparent=True, native_transparency=False,
                         palette=["#E040FB", "#7B1FA2"])  # fmt: skip
    assert keyed.key_colour == "#00FF00"  # a purple palette keys on green


def test_cli_art_commands(capsys, home, tmp_path):
    profile_file = tmp_path / "art.yaml"
    profile_file.write_text(PROFILE_YAML)
    code, out, _ = run_cli(capsys, "art", "presets", "--json")
    assert code == 0 and out["count"] >= 18 and {p["name"] for p in out["presets"]} >= REQUIRED
    code, out, _ = run_cli(capsys, "art", "preset", "horror", "--json")
    assert code == 0 and "Mood:" in out["prompt_text"]
    assert run_cli(capsys, "art", "preset", "nope", "--json")[0] == 3
    code, out, _ = run_cli(capsys, "art", "profile", "set", "--file", str(profile_file), "--mood", "grim", "--json")
    assert code == 0 and out["profile"]["art"]["mood"] == "grim" and out["profile"]["project"] == "space-survivor"
    code, text, _ = run_cli(capsys, "art", "profile", "show", "space-survivor", "--yaml")
    assert "game: space-survivor" in text and "mood: grim" in text
    code, out, _ = run_cli(capsys, "art", "preview", "-p", "derelict corridor", "--type", "background",
                           "--project", "space-survivor", "--provider", "gemini", "--json")  # fmt: skip
    assert code == 0 and out["layers"] == ["preset:sci-fi", "profile:space-survivor"] and "Mood: grim." in out["prompt"]
    code, out, _ = run_cli(capsys, "image", "generate", "-p", "rusty hatch", "--type", "object", "--style", "pixel-art",
                           "--mood", "lonely", "--avoid", "people", "--provider", "gemini", "--dry-run", "--json")  # fmt: skip
    assert code == 0 and out["art"]["mood"] == "lonely" and "people" in out["art"]["avoid"]
    assert "1-pixel dark outline" in out["prompt"]
    assert run_cli(capsys, "art", "profile", "list", "--json")[1]["count"] == 1
    assert run_cli(capsys, "art", "profile", "delete", "space-survivor", "--json")[0] == 0


def test_cli_guide(capsys, home):
    code, out, _ = run_cli(capsys, "guide", "--json")
    guide = out["guide"]
    assert code == 0 and REQUIRED <= set(guide["art"]["presets"]) and guide["workflow"][0]["step"] == "search"
    assert "enemy" in guide["types"] and guide["request_example"]["art"]["mood"]
