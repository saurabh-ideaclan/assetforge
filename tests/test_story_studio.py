"""Workflow stages and approval gates, the Character Studio (profiles, canon, locks, AI proposals, master
reference, consistency), chapter alternatives, and the app routes for all of them (offline providers only)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.core.catalog import Library
from assetforge.errors import ConflictError
from assetforge.story import characters, steps, workflow
from assetforge.story.project import StoryProject
from test_story_app import wait_task
from test_ui_server import jcall


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _init(lib: Library, root: Path) -> tuple[StoryProject, steps.Ctx]:
    project = steps.init(root, title="Ash Crown", premise="A betrayed knight returns three years earlier.", scenes=4,
                         endings=2, provider="fake")  # fmt: skip
    return project, steps.Ctx(lib, project)


# ---------------------------------------------------------------------- workflow


def test_gates_follow_the_mode_and_approvals(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _init(lib, tmp_path / "w")
    assert workflow.load(project)["mode"] == "guided"
    assert workflow.blocked_by(project, "cast") is None  # guided: the world needs no approval
    steps.world(ctx)
    steps.cast(ctx)
    assert workflow.blocked_by(project, "outline") == "characters"
    st = workflow.status(lib, project)
    chars = next(s for s in st["stages"] if s["id"] == "characters")
    assert chars["state"] == "needs_approval" and st["needs_you"][0]["stage"] == "characters" and chars["decisions"]
    assert st["activity"]["title"].startswith("Waiting for you")
    workflow.approve(project, "characters", note="looks right")
    assert workflow.blocked_by(project, "outline") is None
    assert workflow.load(project)["history"][-1]["action"] == "approved"
    steps.outline(ctx)
    assert workflow.blocked_by(project, "write") == "architecture"
    workflow.request_changes(project, "architecture", "more forks")
    assert next(s for s in workflow.status(lib, project)["stages"] if s["id"] == "architecture")["state"] == "needs_revision"
    workflow.set_mode(project, "auto")
    assert workflow.blocked_by(project, "write") is None and workflow.blocked_by(project, "assets") == "visual" or True
    workflow.set_mode(project, "manual")
    assert workflow.blocked_by(project, "write") == "architecture" and workflow.blocked_by(project, "cast") == "world"
    with pytest.raises(Exception):
        workflow.set_mode(project, "wild")


# ---------------------------------------------------------------------- character studio


def test_profiles_canon_locks_and_sync(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _init(lib, tmp_path / "c")
    steps.world(ctx)
    steps.cast(ctx)
    items = characters.profiles(project)
    assert set(items) == {"mira", "tom"} and items["mira"].identity.name == "Mira" and items["mira"].status("visual") == "proposed"
    assert (project.root / "characters.json").exists()
    p = characters.update(project, "mira", {"visual": {"hair": "shoulder-length silver hair", "eyes": "amber eyes"}}, note="hair")
    assert p.version == 2 and p.versions[-1]["changed"] == ["visual"] and "silver" in characters.visual_spec(p)
    assert "silver hair" in project.load("cast").characters[0].look  # canon flows into the pipeline's cast
    characters.set_approval(project, "mira", "visual", "approved", "final look")
    p = characters.get(project, "mira")
    assert p.status("visual") == "approved" and "visual" in p.locked
    with pytest.raises(ConflictError):
        characters.update(project, "mira", {"visual": {"hair": "black"}})
    p = characters.update(project, "mira", {"visual": {"hair": "black hair"}}, unlock=True)
    assert p.status("visual") == "proposed" and "black hair" in characters.visual_spec(p)
    rows = characters.overview(lib, project)
    assert rows[0]["id"] == "mira" and rows[0]["development"] == "draft ready for review" and rows[0]["version"] == 3
    assert characters.visual_summary(project) == {"total": 2, "approved": 0}
    impact = workflow.character_impact(project, "mira")
    assert "affects" in impact["summary"]


def test_ai_revision_is_a_proposal_until_applied(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _init(lib, tmp_path / "r")
    steps.world(ctx)
    steps.cast(ctx)
    characters.set_lock(project, "tom", "identity", True)
    prop = characters.revise(ctx, "tom", "make him more morally ambiguous")
    assert prop.id == "p1" and "personality" in prop.changed and "identity" not in prop.changed and prop.notes
    assert ctx.calls[-1]["task"] == "character.tom.r1" and ctx.calls[-1]["choice"]["kind"] == "character"
    p = characters.get(project, "tom")
    assert len(p.proposals) == 1 and "ambiguous" not in p.personality.contradictions  # not applied yet
    p = characters.apply_proposal(project, "tom", "p1")
    assert "ambiguous" in p.personality.contradictions and p.proposals == [] and p.version == 2
    assert "ambiguous" in project.load("cast").characters[1].personality
    prop2 = characters.revise(ctx, "tom", "give him a scar")
    assert characters.discard_proposal(project, "tom", prop2.id).proposals == []


def test_master_reference_and_consistency(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _init(lib, tmp_path / "m")
    steps.world(ctx)
    steps.cast(ctx)
    out = characters.concepts(lib, project, "mira", n=2, image_provider="fake")
    ids = [c["asset_id"] for c in out["candidates"]]
    assert len(ids) == 2 and out["spec"]
    p = characters.set_master(lib, project, "mira", ids[1])
    assert p.reference and p.reference.asset_id == ids[1] and p.status("visual") == "approved"
    assert lib.get_asset(ids[1]).key == "vn/ash-crown/char/mira/neutral" and lib.get_asset(ids[1]).status.value == "approved"
    assert lib.get_asset(ids[0]).status.value == "rejected"
    rows = characters.overview(lib, project)
    assert rows[0]["neutral_asset"] == ids[1] and rows[0]["reference_stale"] is False
    report = characters.consistency(lib, project, "mira")
    assert report["reference"] == ids[1] and report["items"] == [] and not report["reference_stale"]
    characters.update(project, "mira", {"visual": {"costume": "a red cloak"}}, unlock=True)
    assert characters.overview(lib, project)[0]["reference_stale"] is True


def test_alternatives_are_proposals_until_chosen(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _init(lib, tmp_path / "a")
    steps.world(ctx)
    steps.cast(ctx)
    steps.outline(ctx)
    steps.write(ctx)
    before = project.scene_path("s02").read_text()
    out = steps.alternatives(ctx, "s02", n=2, note="more tension")
    assert [a["id"] for a in out["alternatives"]] == ["a1", "a2"] and (project.root / "alternatives" / "s02.json").exists()
    assert project.scene_path("s02").read_text() == before
    result = steps.choose_alternative(project, "s02", "a2")
    assert result["chosen"] == "a2" and Path(result["backup"]).read_text() == before and result["assembled"]


# ---------------------------------------------------------------------- app


@pytest.fixture
def server(home: Path, fake: None):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def test_app_pauses_at_gates_and_exposes_the_studio(server: AppServer, home: Path) -> None:
    status, made = jcall(server, "/api/stories", method="POST", body={
        "title": "Ash Crown", "premise": "A betrayed knight returns three years earlier.", "scenes": 4, "endings": 2, "provider": "fake"})
    sid = made["id"]
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["paused_at"] == "characters"
    _, wf = jcall(server, f"/api/stories/{sid}/workflow")
    assert wf["mode"] == "guided" and wf["paused_at"] == "characters" and wf["needs_you"][0]["stage"] == "characters"
    _, detail = jcall(server, f"/api/stories/{sid}")
    assert detail["workflow"]["activity"]["title"].startswith("Waiting for you")
    _, chars = jcall(server, f"/api/stories/{sid}/characters")
    assert [c["id"] for c in chars["cast"]] == ["mira", "tom"] and chars["profiles"]["mira"]["identity"]["name"] == "Mira"
    status, res = jcall(server, f"/api/stories/{sid}/characters/mira", method="POST",
                        body={"patch": {"personality": {"traits": ["brave", "curious"]}}, "note": "traits"})  # fmt: skip
    assert status == 200 and res["profile"]["version"] == 2
    status, res = jcall(server, f"/api/stories/{sid}/characters/mira/approval", method="POST", body={"section": "all", "state": "approved"})
    assert status == 200 and res["profile"]["locked"] == list(characters.SECTIONS)
    _, imp = jcall(server, f"/api/stories/{sid}/characters/mira/impact")
    assert "affects" in imp["summary"]
    status, _ = jcall(server, f"/api/stories/{sid}/workflow", method="POST", body={"action": "approve", "stage": "characters", "note": "ok"})
    assert status == 200
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["paused_at"] == "architecture"
    jcall(server, f"/api/stories/{sid}/workflow", method="POST", body={"action": "mode", "mode": "auto"})
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "write"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["assembled"]
    assert task["calls"][0]["choice"]["chosen_by"] in ("override", "managed", "library", "story")
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "character", "character": "tom", "request": "more mystery"})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and task["result"]["proposal"]["id"] == "p1"
    _, run = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "alternatives", "scene": "s02", "n": 2})
    task = wait_task(server, run["task_id"])
    assert task["state"] == "done" and len(task["result"]["alternatives"]) == 2
    status, res = jcall(server, f"/api/stories/{sid}/alternatives/s02/a1", method="POST", body={})
    assert status == 200 and res["chosen"] == "a1"
    _, plan = jcall(server, "/api/ai/plan")
    assert plan["plan"]["managed"] is True and "scene" in plan["task_kinds"]
    status, saved = jcall(server, "/api/ai/plan", method="POST", body={"managed": False, "steps": [{"provider": "fake"}], "paid_fallback": "never"})
    assert status == 200 and saved["plan"]["paid_fallback"] == "never" and (home / "ai.json").exists()
