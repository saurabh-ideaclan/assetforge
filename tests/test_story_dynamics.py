"""Narrative Dynamics: planned tracks, text estimates, the genre's shape rules and the branching graph."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer

from assetforge.core.catalog import Library
from assetforge.story import dynamics, steps
from assetforge.story.models import Outline
from test_ui_server import jcall


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


@pytest.fixture
def server(home: Path, fake: None):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def _story(lib: Library, tmp_path: Path):
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=4, endings=2, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx)
    return project, ctx


def test_plan_estimate_review_and_graph(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _story(lib, tmp_path)
    d = dynamics.plan(ctx)
    outline = project.load("outline", Outline)
    order = [cid for cid, _, _ in dynamics.chapter_order(outline)]
    assert [p.chapter for p in d.planned.points] == order and ctx.calls[-1]["task"] == "dynamics.plan"
    assert d.reviewed["planned_chapters"] == len(order) and "estimate" in d.reviewed["method"]
    # a written chapter can be estimated; the estimate is kept apart from the plan and deviations are named
    steps.write(ctx)
    d = dynamics.estimate(ctx, order[0])
    assert order[0] in d.measured and d.measured[order[0]].chapter == order[0]
    assert all(dv["chapter"] == order[0] for dv in d.reviewed["deviations"])
    with pytest.raises(Exception):
        dynamics.estimate(ctx, "no_such_chapter")
    # the graph: every outline exit is an edge, decisions are the scenes with choices, endings carry their gates
    g = dynamics.graph(outline, {s.id: project.load_scene(s.id) for s in outline.scenes if project.load_scene(s.id)})
    exits = sum(len(s.exits()) for s in outline.scenes)
    assert len(g["edges"]) == exits and {d["scene"] for d in g["decisions"]} == {s.id for s in outline.scenes if s.choices}
    assert g["start"] == outline.start and len(g["endings"]) == len(outline.endings)
    view = dynamics.view(project)
    assert view["graph"] and view["planned"] and len(view["chapters"]) == len(order)


def test_shape_rules_name_the_genre_faults() -> None:
    pts = [dynamics.ChapterPoint(chapter=f"c{i}", title=f"C{i}", valence=0, tension=5, suspense=5, capability=2, status=6, family_conflict=5,
                                 milestones=[dynamics.Milestone(kind="victory", text="win")]) for i in range(5)]
    codes = {f["code"] for f in dynamics.review(dynamics.Dynamics(planned=dynamics.DynamicsPlan(points=pts)))["findings"]}
    assert {"flat_tension", "status_ahead_of_capability", "constant_victory", "no_awakening"} <= codes
    bleak = [dynamics.ChapterPoint(chapter=f"c{i}", title=f"C{i}", valence=-3, tension=2 + i, suspense=5, capability=1, status=1, family_conflict=8) for i in range(5)]
    codes = {f["code"] for f in dynamics.review(dynamics.Dynamics(planned=dynamics.DynamicsPlan(points=bleak)))["findings"]}
    assert "constant_suffering" in codes
    plateau = [dynamics.ChapterPoint(chapter=f"c{i}", title=f"C{i}", valence=0, tension=9 if 1 <= i <= 4 else 4, suspense=5, capability=3, status=2, family_conflict=5,
                                     milestones=[dynamics.Milestone(kind="awakening" if i == 1 else "setback", text="x")]) for i in range(6)]
    codes = {f["code"] for f in dynamics.review(dynamics.Dynamics(planned=dynamics.DynamicsPlan(points=plateau)))["findings"]}
    assert "tension_plateau" in codes
    good = [dynamics.ChapterPoint(chapter=f"c{i}", title=f"C{i}", valence=i - 2, tension=2 + 2 * i, suspense=3 + i, capability=min(10, 1 + 2 * i), status=1 + i // 2,
                                  family_conflict=8 - i, milestones=[dynamics.Milestone(kind="awakening" if i == 1 else "setback" if i == 2 else "decision", text="x")]) for i in range(5)]
    codes = {f["code"] for f in dynamics.review(dynamics.Dynamics(planned=dynamics.DynamicsPlan(points=good)))["findings"]}
    assert "flat_tension" not in codes and "status_ahead_of_capability" not in codes and "constant_victory" not in codes


def test_dynamics_routes(server, home: Path, fake: None) -> None:
    from assetforge.story.project import register

    with Library(home) as lib:
        project, ctx = _story(lib, home / "stories" / "fog")
        sid = register(home, project)
    status, out = jcall(server, f"/api/stories/{sid}/dynamics")
    assert status == 200 and out["graph"] and out["planned"] is None and out["tracks"] == list(dynamics.TRACKS)
    status, started = jcall(server, f"/api/stories/{sid}/run", method="POST", body={"action": "dynamics_plan"})
    assert status == 200 and (started.get("task_id") or started.get("task"))
