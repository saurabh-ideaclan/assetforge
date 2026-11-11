"""Story sound: the writer's sound plan (catalog-checked), indoor rain without hiss, the wall clock and the distant
dog, and the home scenes. These came from "Static Tomorrow", where a family home got the word-matched haunted-house
ambience (howling wind + loud room tone + outdoor rain = a wall of static)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from assetforge.audio.ambience.beds import BEDS
from assetforge.audio.ambience.engine import scenes
from assetforge.audio.ambience.events import EVENTS
from assetforge.core.catalog import Library
from assetforge.story import steps
from assetforge.story.assets import manifest
from assetforge.story.sound import BedPick, EventPick, LocationSound, MusicCue, SoundPlan, ambience_options, clean

SR = 44100


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def bands(x: np.ndarray) -> dict[str, float]:
    x = x.mean(axis=1) if x.ndim > 1 else x
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    total = spec.sum()
    return {"centroid": float((f * spec).sum() / total), "above_4k": float(spec[f > 4000].sum() / total),
            "presence": float(spec[(f > 1000) & (f < 4000)].sum() / total)}  # fmt: skip


def render_bed(name: str, seconds: float = 8, **params: float) -> np.ndarray:
    bed = BEDS[name]
    n = int(seconds * SR)
    return bed.render(n, SR, np.random.default_rng(3), 0.4, {**bed.params, **params}, n)


def test_rain_heard_from_inside_has_no_hiss_but_stays_clear() -> None:
    outside = bands(render_bed("rain", heaviness=0.35))
    inside = bands(render_bed("rain", heaviness=0.35, inside=0.8))
    assert outside["above_4k"] > 0.25  # outdoor rain keeps its natural sizzle (matched to recordings)
    assert inside["above_4k"] < 0.02  # indoors: no static-like hiss
    assert 700 < inside["centroid"] < 2200 and inside["presence"] > 0.35  # warm but not muffled


def test_outdoor_rain_is_unchanged_by_the_new_parameter() -> None:
    bed = BEDS["rain"]
    n = 2 * SR
    old_params = {k: v for k, v in bed.params.items() if k != "inside"}
    a = bed.render(n, SR, np.random.default_rng(5), 0.5, old_params, n)
    b = bed.render(n, SR, np.random.default_rng(5), 0.5, {**old_params, "inside": 0.0}, n)
    assert np.array_equal(a, b)  # older rain ambiences still reproduce bit for bit


def test_clock_ticks_on_a_grid_that_loops_seamlessly() -> None:
    bed = BEDS["clock_tick"]
    period = 10 * SR
    x = bed.render(period + SR, SR, np.random.default_rng(1), 0.5, bed.params, period)[:, 1]
    onsets = np.flatnonzero((np.abs(x[1:]) > 0.05) & (np.abs(x[:-1]) <= 0.05))
    first = onsets[np.insert(np.diff(onsets) > SR // 4, 0, True)]
    gaps = np.diff(first)
    assert len(first) >= 10 and np.allclose(gaps, gaps[0], atol=SR * 0.002)
    assert period % round(gaps.mean()) < SR * 0.01 or abs(period / gaps.mean() - round(period / gaps.mean())) < 0.01


def test_distant_dog_bark_is_short_voiced_and_muffled() -> None:
    x = EVENTS["dog_bark_far"].render(np.random.default_rng(2), SR)
    b = bands(x)
    assert 0.1 < len(x) / SR < 3.5 and np.max(np.abs(x)) > 0
    assert b["above_4k"] < 0.02 and 300 < b["centroid"] < 1800


def test_home_scenes_exist_and_are_calm() -> None:
    names = scenes(None)
    for name in ("home_interior", "home_rain"):
        scene = names[name]
        assert "indoor" in scene.tags and not {"wind", "insects_night"} & {b.bed for b in scene.beds}
        assert all(e.event not in ("distant_howl", "crow", "monster_far") for e in scene.events)


def test_clean_keeps_the_plan_inside_the_catalog_and_gentle(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    script = project.load("script")
    first = script.locations[0].id
    plan = SoundPlan(
        locations=[LocationSound(location=first, scene="not_a_scene",
                                 beds=[BedPick(bed="rain", level_db=0, inside=0.8), BedPick(bed="room_tone", level_db=-3),
                                       BedPick(bed="made_up", level_db=-10)],
                                 events=[EventPick(event="clock_tick", rate=50), EventPick(event="dog_bark_far", rate=50, gain_db=6)])],
        music=[MusicCue(mood="calm", genre="not_a_genre", instruments=[{"role": "melody", "instrument": "guitar"},
                                                                       {"role": "bass", "instrument": "kazoo"}], energy=3)])  # fmt: skip
    fixed, notes = clean(plan, script)
    loc = next(p for p in fixed.locations if p.location == first)
    assert loc.scene is None and [b.bed for b in loc.beds] == ["rain", "room_tone"]
    assert loc.beds[0].level_db == -12 and loc.beds[1].level_db == -20  # noise beds capped
    assert [e.event for e in loc.events] == ["dog_bark_far"] and loc.events[0].rate == 6 and loc.events[0].gain_db == -4
    assert {p.location for p in fixed.locations} == {loc_.id for loc_ in script.locations}  # every location covered
    assert fixed.music[0].genre == "ambient_drone" and [(p.role, p.instrument) for p in fixed.music[0].instruments] == [("melody", "guitar")]
    assert fixed.music[0].energy == 1.0 and len(notes) >= 3
    opts = ambience_options(loc)
    assert opts["scene"] is None and opts["beds"][0]["params"] == {"inside": 0.8}

    # the plan reaches the requests: explicit options instead of guessing from words
    items = {i.key: i for i in manifest(script, project.config, project.load("world"), sound_plan=fixed)}
    amb = items[f"vn/fog/amb/{first}"].request.sound
    assert amb["scene"] is None and amb["beds"][0]["bed"] == "rain"
    calm = items.get("vn/fog/music/calm")
    if calm:
        assert calm.request.sound["genre"] == "ambient_drone"


def test_plan_sound_step_saves_sound_json(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    plan, _ = steps.plan_sound(ctx)
    assert project.has("sound") and project.load("sound") == plan


def test_layers_already_in_the_scene_are_not_added_twice(lib: Library, tmp_path: Path, fake: None) -> None:
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    script = project.load("script")
    first = script.locations[0].id
    plan = SoundPlan(locations=[LocationSound(location=first, scene="home_interior",
                                              beds=[BedPick(bed="clock_tick", level_db=-14), BedPick(bed="rain", level_db=-14, inside=0.8)],
                                              events=[EventPick(event="dog_bark_far"), EventPick(event="chime")])], music=[])  # fmt: skip
    fixed, notes = clean(plan, script)
    loc = next(p for p in fixed.locations if p.location == first)
    assert [b.bed for b in loc.beds] == ["rain"] and [e.event for e in loc.events] == ["chime"]
    assert any("already in scene" in n for n in notes)


def test_strict_schema_keeps_instrument_pairs() -> None:
    from assetforge.llm.structured import strict_schema

    schema = strict_schema(SoundPlan)
    cue = schema["$defs"]["MusicCue"]["properties"]["instruments"]
    assert "items" in str(cue) and "RolePick" in str(cue)


def test_redo_keeps_every_sound_playable(lib: Library, tmp_path: Path, fake: None) -> None:
    """Regression: a remake that rendered identical audio resolved to the archived copy, leaving the place silent."""
    from assetforge.story.assets import produce
    from assetforge.story.progress import library_assets

    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake")  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    first = produce(lib, project, only=["ambience"])
    keys = {i["key"] for i in first["items"]}
    again = produce(lib, project, only=["ambience"], redo=True)
    assert {i["state"] for i in again["items"]} == {"ready"}
    assert keys <= set(library_assets(lib, project))


def test_clean_drops_the_crowd_bed_where_nobody_is(lib: Library, tmp_path: Path, fake: None) -> None:
    """The synthesized-talker bed is the "mumbling voices" complaint: it plays only where the script puts people."""
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    script = project.load("script")
    quiet, busy = script.locations[0], script.locations[0].model_copy(update={"id": "market_sq", "name": "Market square", "ambience": "a crowd haggling"})
    script = script.model_copy(update={"locations": [quiet, busy]})
    plan = SoundPlan(locations=[LocationSound(location=quiet.id, beds=[BedPick(bed="crowd_murmur", level_db=-14), BedPick(bed="wind", level_db=-14)]),
                                LocationSound(location="market_sq", beds=[BedPick(bed="crowd_murmur", level_db=-14)])], music=[])  # fmt: skip
    fixed, notes = clean(plan, script)
    assert [b.bed for b in fixed.locations[0].beds] == ["wind"] and any("crowd_murmur dropped" in n for n in notes)
    assert [b.bed for b in fixed.locations[1].beds] == ["crowd_murmur"]
    # the music a story asks for is long and calm enough to sit under minutes of reading
    from assetforge.story.sound import music_options
    opts = music_options(MusicCue(mood="tense", genre="court_intrigue", energy=0.9))
    assert opts["seconds"] == 96.0 and opts["energy"] == 0.5
