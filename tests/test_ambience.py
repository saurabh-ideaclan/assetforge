"""Ambience: beds, events and scenes rendered as seamless loops with stems and intensity versions."""

from __future__ import annotations

import numpy as np
import pytest

from assetforge.audio import pipeline
from assetforge.audio.ambience import engine
from assetforge.audio.ambience.beds import BEDS
from assetforge.audio.ambience.events import EVENTS
from assetforge.audio.loudness import integrated_loudness
from assetforge.errors import InvalidInputError, NotFoundError
from assetforge.validation.audio import loop_seam, seam_ok

from conftest import run_cli

SR = 22050


def spec_for(name: str, **kwargs) -> engine.AmbienceSpec:
    scene = engine.builtin_scenes()[name]
    return engine.AmbienceSpec(scene=name, beds=scene.beds, events=scene.events, space=scene.space,
                               **{"length": 8, **kwargs})  # fmt: skip


def test_catalog_has_the_planned_scenes_beds_and_events():
    scenes = engine.builtin_scenes()
    assert len(scenes) >= 22
    assert {"forest_day", "forest_night", "rain_storm", "ocean_shore", "cave", "dungeon", "tavern", "city_street",
            "space_station", "underwater", "snow_blizzard", "lava_cave"} <= set(scenes)  # fmt: skip
    assert {"wind", "rain", "ocean_waves", "fire_crackle", "crowd_murmur", "space_drone", "insects_night"} <= set(BEDS)
    assert {"bird_chirp", "owl", "drip", "thunder", "creak", "distant_howl", "radio_chatter"} <= set(EVENTS)


@pytest.mark.parametrize("name", sorted(engine.builtin_scenes()))
def test_every_scene_loops_seamlessly(name):
    take = engine.render(spec_for(name).model_dump(mode="json"), 1, 5, SR)
    mix = take.mix / np.max(np.abs(take.mix))
    assert mix.shape == (8 * SR, 2) and np.isfinite(mix).all()
    assert seam_ok(loop_seam(mix, SR)), (name, loop_seam(mix, SR))


def test_render_is_deterministic_and_intensity_scales_activity():
    spec = spec_for("rain_storm", intensities=[0.1, 1.0], stems=True).model_dump(mode="json")
    a, b = engine.render(spec, 1, 9, SR), engine.render(spec, 1, 9, SR)
    assert np.array_equal(a.mix, b.mix)
    assert set(a.extras) == {"calm", "intense", "stem_rain", "stem_wind", "stem_thunder"}
    assert integrated_loudness(a.extras["intense"], SR) > integrated_loudness(a.extras["calm"], SR) + 3
    stems = sum(v for k, v in a.extras.items() if k.startswith("stem_"))
    assert np.allclose(stems, a.mix, atol=1e-6)  # stems are linear parts of the mix


def test_request_resolution_time_of_day_mute_levels_and_errors(lib):
    spec, scene = engine.resolve_spec(lib, engine.AmbienceRequest(scene="forest-day", time_of_day="night",
                                                                  mute=["wind"], levels={"forest_air": -3}))  # fmt: skip
    assert scene and scene.name == "forest_day"
    assert [b.bed for b in spec.beds] == ["forest_air", "insects_night"] and spec.beds[0].level_db == -3
    assert not {e.event for e in spec.events} & engine.DAY_ONLY
    custom, _ = engine.resolve_spec(lib, engine.AmbienceRequest(beds=[{"bed": "rain"}], events=[{"event": "owl"}],
                                                                space="cave"))  # fmt: skip
    assert custom.scene is None and custom.space == "cave"
    with pytest.raises(NotFoundError):
        engine.get_scene(lib, "moon_base")
    with pytest.raises(InvalidInputError):
        engine.resolve_spec(lib, engine.AmbienceRequest(scene="rain_light", mute=["rain", "drip"]))
    with pytest.raises(ValueError):
        engine.AmbienceRequest(beds=[{"bed": "lasers"}])


def test_generate_catalogs_loop_metadata_and_reproduces(lib):
    res = engine.generate_ambience(lib, engine.AmbienceRequest(scene="cave", length=6, seed=2, stems=True,
                                                               project="crypt-run"))  # fmt: skip
    asset = lib.get_asset(res["asset_id"])
    assert asset.type.value == "ambience" and asset.metadata["audio"]["loop"] is True
    assert asset.metadata["audio"]["scene"] == "cave" and "stem_drip" in res["variants"][0]["extras"]
    assert asset.primary_variant.file.duration_ms == 6000
    assert abs(asset.metadata["audio"]["loop_end"] - 6.0) < 1e-9
    assert pipeline.reproduce(lib, res["asset_id"])["ok"]
    assert engine.generate_ambience(lib, engine.AmbienceRequest(scene="cave", length=6, seed=2, stems=True,
                                                                project="crypt-run"))["reused"]  # fmt: skip


def test_cli_ambience(capsys, home, tmp_path):
    code, out, _ = run_cli(capsys, "ambience", "scenes", "--json")
    assert code == 0 and out["count"] >= 22
    code, out, _ = run_cli(capsys, "ambience", "layers", "--json")
    assert code == 0 and any(b["name"] == "rain" for b in out["beds"])
    layers = tmp_path / "extra.yaml"
    layers.write_text("events:\n  - event: bell_toll\n    rate: 2\n")
    code, out, _ = run_cli(capsys, "ambience", "generate", "meadow", "--length", "5", "--seed", "1", "--layers-file",
                           str(layers), "--level", "wind=-6", "--intensity-level", "0.9", "--json")  # fmt: skip
    assert code == 0 and "bell_toll" in out["audio"]["layers"] and "intensity_90" in out["variants"][0]["extras"]
    code, out, _ = run_cli(capsys, "search", "meadow", "--loopable", "--json")
    assert code == 0 and out["count"] == 1
    code, out, _ = run_cli(capsys, "ambience", "generate", "--length", "5", "--json")
    assert code == 4
