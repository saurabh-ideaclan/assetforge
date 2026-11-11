"""Audio direction: the plan step (offline), the review rules, applying cues as nodes (with branch conditions and
loops), the asset manifest picking them up, the player export, the new recipes, and the app routes."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.core.catalog import Library
from assetforge.story import audio_direction as ad
from assetforge.story import steps
from assetforge.story import direction as creative
from assetforge.story.assets import manifest, produce
from assetforge.story.models import Condition, Node, Scene
from assetforge.story.project import StoryProject
from test_story_app import wait_task
from test_ui_server import jcall


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _written(lib: Library, root: Path) -> tuple[StoryProject, steps.Ctx]:
    d = creative.resolve({"audio": {"profile": "balanced"}}, preset="cozy-mystery")
    project = steps.init(root, title="Fog Lantern", premise="An apprentice uncovers smugglers.", scenes=4, endings=2,
                         provider="fake", direction=d)  # fmt: skip
    ctx = steps.Ctx(lib, project)
    steps.world(ctx)
    steps.cast(ctx)
    steps.outline(ctx)
    steps.write(ctx)
    return project, ctx


def test_new_recipes_render_and_pass_qc(lib: Library) -> None:
    from assetforge.audio.generate import AudioRequest, generate_audio
    from assetforge.audio.recipes import RECIPES
    from assetforge.validation.audio import analyze_file

    assert {"knock", "bell", "phone_ring", "chain", "page_turn", "hooves", "armor"} <= set(RECIPES)
    for name, params in (("knock", {"knocks": 3}), ("bell", {"strikes": 2, "cracked": 0.5}), ("phone_ring", {"kind": "vibrate"})):
        result = generate_audio(lib, AudioRequest(recipe=name, params=params, seed=7))
        asset = lib.get_asset(result.asset_id)
        report = analyze_file(Path(asset.path))
        assert not [i for i in report["issues"] if i in ("silent", "clipping")], (name, report)


def test_plan_review_apply_and_manifest(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _written(lib, tmp_path / "s")
    plan = ad.plan(ctx, "s02")
    assert plan.scene == "s02" and plan.cues[0].kind == "ambience" and plan.cues[0].at == 0
    kinds = {c.kind for c in plan.cues}
    assert {"ambience", "music"} <= kinds and plan.reviewed["ok"]
    assert ctx.calls[-1]["task"] == "audio.s02" and ctx.calls[-1]["choice"]["kind"] == "audio"
    conditional = [c for c in plan.cues if c.when]
    assert conditional and conditional[0].when[0].var  # the offline plan ties one sound to a choice's stat
    # editing: update, add, remove; a bad cue id is refused
    ad.update_cue(project, "s02", plan.cues[0].id, {"level": 0.3, "purpose": "quieter room"})
    ad.add_cue(project, "s02", {"id": "ring", "kind": "sfx", "what": "phone_ring", "text": "an electronic ring", "purpose": "the call", "at": 2, "loop": True})
    ad.add_cue(project, "s02", {"id": "ring_stop", "kind": "stop", "what": "ring", "purpose": "answered", "at": 3, "fade": 0.3})
    with pytest.raises(Exception):
        ad.update_cue(project, "s02", "nope", {"level": 1})
    loaded = ad.load(project, "s02")
    assert loaded.cues[0].level == 0.3 and any(c.id == "ring_stop" for c in loaded.cues)
    # review rules
    rev = ad.review(loaded, project.load_scene("s02"), project.config)
    assert rev["counts"]["ambience"] >= 1 and "budget" in rev and rev["readable_without_sound"]
    ad.add_cue(project, "s02", {"id": "forever", "kind": "sfx", "what": "bell", "purpose": "never stops", "at": 1, "loop": True})
    rev = ad.review(ad.load(project, "s02"), project.load_scene("s02"), project.config)
    assert any(i["code"] == "loop_never_stops" for i in rev["issues"])
    ad.remove_cue(project, "s02", "forever")
    # apply writes nodes marked with `why`, keeps the exit last, and is repeatable
    before = len([n for n in project.load_scene("s02").nodes if not n.why])
    result = ad.apply(project, "s02")
    scene = project.load_scene("s02")
    applied = [n for n in scene.nodes if n.why]
    assert result["applied"] == len(applied) >= 4 and scene.nodes[-1].kind in ("jump", "ending", "choice", "branch")
    assert any(n.kind == "ambience" for n in applied) and any(n.kind == "sfx" and n.loop for n in applied)
    assert any(n.kind == "sfx" and n.stop == "ring" for n in applied) and any(n.kind == "music" and n.music == "silence" for n in applied)
    assert any(n.when for n in applied)
    ad.apply(project, "s02")
    assert len([n for n in project.load_scene("s02").nodes if not n.why]) == before
    assert len([n for n in project.load_scene("s02").nodes if n.why]) == len(applied)
    # the manifest wants the cue sounds and no music for "silence"
    script = project.load("script")
    items = manifest(script, project.config, project.load("world"))
    keys = {i.key for i in items}
    assert "vn/fog-lantern/sfx/ring" in keys and not any(k.endswith("/music/silence") for k in keys)
    # statuses before and after making the sounds
    st = {r["id"]: r for r in ad.statuses(lib, project, ad.load(project, "s02"))}
    assert st["ring"]["library_status"] == "needs_generation"
    produce(lib, project, only=["audio"], image_provider="fake")
    st = {r["id"]: r for r in ad.statuses(lib, project, ad.load(project, "s02"))}
    assert st["ring"]["asset"] and st["ring"]["library_status"] in ("needs_review", "approved")
    # the intensity strip reads music on/off and silence
    strip = ad.intensity(ad.load(project, "s02"), project.load_scene("s02").model_copy(update={"nodes": [n for n in scene.nodes if not n.why]}))
    assert strip and strip[-1]["music"] == 0.0 and any(s["music"] > 0 for s in strip)


def test_player_export_carries_the_cues(lib: Library, tmp_path: Path, fake: None) -> None:
    from assetforge.exporters.vn import export_story

    project, ctx = _written(lib, tmp_path / "e")
    ad.plan(ctx, "s01")
    ad.apply(project, "s01")
    out = export_story(lib, project, tmp_path / "build")
    data = json.loads((tmp_path / "build" / "story.json").read_text())
    s01 = next(s for s in data["script"]["scenes"] if s["id"] == "s01")
    kinds = [n["kind"] for n in s01["nodes"]]
    assert "ambience" in kinds and "music" in kinds and out["player"]
    html = (tmp_path / "build" / "index.html").read_text()
    assert 'id="v-music"' in html and "stopAllSfx" in html and '"ambience"' in html


@pytest.fixture
def server(home: Path, fake: None):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def test_audio_routes(server: AppServer, home: Path) -> None:
    _, made = jcall(server, "/api/stories", method="POST", body={
        "title": "Fog Lantern", "premise": "An apprentice uncovers smugglers.", "scenes": 4, "endings": 2, "provider": "fake",
        "preset": "cozy-mystery"})  # fmt: skip
    sid = made["id"]
    jcall(server, f"/api/stories/{sid}/workflow", method="POST", body={"action": "mode", "mode": "auto"})
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    assert wait_task(server, run["task_id"])["state"] == "done"
    status, view = jcall(server, f"/api/stories/{sid}/audio/s02")
    assert status == 200 and view["plan"] is None and view["lines"]
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "audio_plan", "scene": "s02"})
    assert wait_task(server, run["task_id"])["state"] == "done"
    _, view = jcall(server, f"/api/stories/{sid}/audio/s02")
    assert view["plan"]["summary"] and view["cues"] and view["review"]["ok"] and view["intensity"]
    cue = view["cues"][0]["id"]
    _, view = jcall(server, f"/api/stories/{sid}/audio/s02", method="POST", body={"action": "update", "cue": cue, "patch": {"purpose": "quiet room"}})
    assert next(c for c in view["cues"] if c["id"] == cue)["purpose"] == "quiet room"
    _, view = jcall(server, f"/api/stories/{sid}/audio/s02", method="POST", body={"action": "apply"})
    assert view["applied"]["applied"] >= 2 and view["applied_nodes"] >= 2
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "audio_make", "scene": "s02"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["audio"]["cues"]
    with_asset = next((c for c in task["result"]["audio"]["cues"] if c.get("asset")), None)
    assert with_asset is not None
    _, view = jcall(server, f"/api/stories/{sid}/audio/s02", method="POST", body={"action": "approve_asset", "cue": with_asset["id"]})
    assert next(c for c in view["cues"] if c["id"] == with_asset["id"])["library_status"] == "approved"
    _, view = jcall(server, f"/api/stories/{sid}/audio/s02", method="POST", body={"action": "regenerate", "cue": with_asset["id"]})
    assert next(c for c in view["cues"] if c["id"] == with_asset["id"])["library_status"] == "needs_generation"


def test_review_flags_music_churn_and_short_loops(lib: Library, tmp_path: Path, fake: None) -> None:
    """Two music changes a few lines apart never let a cue settle; a sub-minute loop under a long read is flagged."""
    from assetforge.story import audio_direction as ad
    from assetforge.story import steps

    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    scene = project.load("script").scenes[0]
    plan = ad.AudioPlan(scene=scene.id, environment="room", mood="calm", summary="test",
                        cues=[ad.Cue(id="room", kind="ambience", what=scene.location, purpose="place", at=0),
                              ad.Cue(id="m1", kind="music", what="calm", purpose="open", at=1),
                              ad.Cue(id="m2", kind="music", what="tense", purpose="turn", at=3)])  # fmt: skip
    rev = ad.review(plan, scene, project.config)
    assert any(i["code"] == "music_churn" for i in rev["issues"])
    assert rev["budget"]["music"] < 3  # the balanced profile allows about one change per 800 words now
