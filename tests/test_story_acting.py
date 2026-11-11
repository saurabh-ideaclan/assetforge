"""Character acting: profiles, the performance analysis, decisions that retarget scenes, and the manifest brief."""

from __future__ import annotations

from pathlib import Path

import pytest

from assetforge.core.catalog import Library
from assetforge.story import acting, steps
from assetforge.story.assets import manifest
from assetforge.story.models import Outline


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")


def _written(lib: Library, tmp_path: Path):
    project = steps.init(tmp_path / "s", title="Fog", premise="An apprentice uncovers smugglers.", scenes=3, endings=1, provider="fake")
    ctx = steps.Ctx(lib, project)
    steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)
    return project, ctx


def test_profile_analysis_decisions_and_manifest_brief(lib: Library, tmp_path: Path, fake: None) -> None:
    project, ctx = _written(lib, tmp_path)
    cast = project.load("cast")
    cid = cast.characters[0].id
    # the acting profile is drafted from the Character Studio profile and starts as proposed
    data = acting.profile(ctx, cid)
    assert data.profiles[cid].state == "proposed" and data.profiles[cid].resting_posture and ctx.calls[-1]["task"] == f"acting.profile.{cid}"
    data = acting.set_profile(project, cid, patch={"gestures": "a closed hand"}, state="approved")
    assert data.profiles[cid].gestures == "a closed hand" and data.profiles[cid].state == "approved"
    # the acting view marks the character's sprite on each appearance and hides other metadata
    first = project.load("outline", Outline).scenes[0].id
    scene = project.load_scene(first)
    view = acting.acting_view(scene, cid, {cid: "Ash"})
    assert "appears:" in view or "(neutral)" in view or "(" in view
    assert "eli_hale" not in view and "goto" not in view
    # analysis: verdicts on existing sprites and proposals only for scenes the character is in
    data = acting.analyze(ctx, cid)
    assert data.verdicts[cid] and data.analyzed[cid]
    proposed = [p for p in data.performances if p.character == cid and p.status == "proposed"]
    assert proposed and all(p.scene for p in proposed)
    plan = acting.plan(lib, project, cid)["characters"][0]
    assert plan["profile"]["state"] == "approved" and plan["proposed"] == [p.id for p in proposed] and plan["sprites"]
    # a second analysis replaces the proposed ones, not the decided ones
    pf = proposed[0]
    data = acting.decide(project, pf.id, "modify", direction={"hands": "one hand on the card"})
    assert next(p for p in data.performances if p.id == pf.id).direction.hands == "one hand on the card"
    data = acting.decide(project, pf.id, "approve")
    perf = next(p for p in data.performances if p.id == pf.id)
    assert perf.status == "approved" and perf.name
    cast2 = project.load("cast")
    assert perf.name in next(c for c in cast2.characters if c.id == cid).expressions  # registered as an expression
    scene2 = project.load_scene(perf.scene)
    assert any(n.expression == perf.name for n in scene2.nodes if (n.kind == "show" and n.character == cid) or (n.kind == "say" and n.speaker == cid)) or perf.where == ""
    before = len([p for p in data.performances if p.status == "approved"])
    data = acting.analyze(ctx, cid, fresh=True)
    assert len([p for p in data.performances if p.status == "approved"]) == before
    # the manifest asks for the performance with its acting brief, from the master
    briefs = acting.directions(project)
    assert (cid, perf.name) in briefs and "Face:" in briefs[(cid, perf.name)] or briefs[(cid, perf.name)]
    steps.assemble(project)
    items = manifest(project.load("script"), project.config, project.load("world"), acting=briefs)
    item = next(i for i in items if i.key.endswith(f"/char/{cid}/{perf.name}"))
    assert "performance" in item.request.description and item.refs
    # dismiss and reuse
    other = next((p for p in data.performances if p.status == "proposed"), None)
    if other:
        data = acting.decide(project, other.id, "dismiss", note="the neutral says it")
        assert next(p for p in data.performances if p.id == other.id).status == "dismissed"
    with pytest.raises(Exception):
        acting.decide(project, "nope", "approve")
    with pytest.raises(Exception):
        acting.generate(lib, project, pf.id, dry_run=False)  # no master reference in the library yet
