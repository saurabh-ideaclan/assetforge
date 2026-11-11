"""The audio production revamp: composer controls and templates, ambience bus filters and evolution, measured
review and refinement, the production loop (offline direction), references and theme variations, the studio
routes. No model and no network: the direction falls back to heuristics when no writer is available."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from assetforge.app.api import router
from assetforge.app.server import AppServer
from assetforge.audio import production as pr
from assetforge.audio import quality
from assetforge.audio.ambience.engine import AmbienceRequest, generate_ambience
from assetforge.audio.music.composer import GENRES, MOOD_GENRE, Brief, compose
from assetforge.audio.music.generate import MusicRequest, generate_music
from assetforge.core.catalog import Library
from test_ui_server import jcall


def test_templates_and_controls_shape_the_score() -> None:
    assert {"fantasy_restrained", "melancholy_piano", "court_intrigue", "rising_magic", "heroic_lift"} <= set(GENRES)
    assert MOOD_GENRE["sad"] == "melancholy_piano" and MOOD_GENRE["calm"] != MOOD_GENRE["heroic"]
    full = compose(Brief(genre="heroic_lift", seconds=40, energy=0.7, melody=1.0, percussion="full"), 5)
    none = compose(Brief(genre="heroic_lift", seconds=40, energy=0.7, melody=0.0, percussion="none"), 5)
    names_full, names_none = {t.name for t in full.tracks}, {t.name for t in none.tracks}
    assert "drums" in names_full and "melody" in names_full and "drums" not in names_none and "melody" not in names_none
    rise = compose(Brief(genre="fantasy_restrained", seconds=40, energy=0.4, arc="rise", warmth=0.6), 5)
    flat = compose(Brief(genre="fantasy_restrained", seconds=40, energy=0.4, arc="flat"), 5)
    first_v = [n.velocity for n in rise.tracks[0].notes[:4]]
    assert first_v and all(v <= f.velocity for v, f in zip(first_v, flat.tracks[0].notes[:4]))
    assert any(e.get("name") == "lowpass" for t in rise.tracks for e in t.effects if isinstance(e, dict))


def test_ambience_filters_and_evolution_render(lib: Library) -> None:
    base = generate_ambience(lib, AmbienceRequest(scene="dungeon", length=20, seed=4, new_take=True, name="d0"))
    cut = generate_ambience(lib, AmbienceRequest(scene="dungeon", length=20, seed=4, new_take=True, name="d1", low_cut=120, evolve=0.6))
    m0 = quality.measure(Path(lib.get_asset(base["asset_id"]).path))
    m1 = quality.measure(Path(lib.get_asset(cut["asset_id"]).path))
    assert m1["sub_share"] < m0["sub_share"] and m1["centroid_hz"] > m0["centroid_hz"]
    assert m1["loop_seam_ok"] is not False


def test_review_names_problems_and_refinements() -> None:
    busy = {"onset_rate": 9.0, "centroid_hz": 600, "speech_band_share": 0.5, "sub_share": 0.1, "repetition_index": 0.1,
            "dynamic_range_db": 3.0, "lufs": -16.0, "loop_seam_ok": True, "clicks": 0, "issues": []}  # fmt: skip
    rev = quality.review("music", {"energy": 0.3, "brightness": 0.4, "under_dialogue": True}, busy)
    codes = {p["code"] for p in rev["problems"]}
    assert {"too_busy", "in_dialogue_band"} <= codes and rev["ok"] and rev["score"] < 0.8
    assert any(p["refinement"].get("energy", 0) < 0 for p in rev["problems"] if p["code"] == "too_busy")
    broken = {**busy, "loop_seam_ok": False, "issues": ["clipping"]}
    assert quality.review("music", {}, broken)["ok"] is False
    d, changes = pr.refine(pr.MusicDirection(energy=0.5, melody=0.8), rev["problems"])
    assert d.energy < 0.5 and d.melody < 0.8 and changes


def test_production_loop_offline(lib: Library) -> None:
    r = pr.produce_for_brief(lib, "Quiet political suspense in a royal court; sparse, low strings, never loud.", kind="music",
                             name="court", seed=2, candidates=2, max_rounds=1)  # fmt: skip
    assert r["directed_by"]["chosen_by"] in ("fallback", "managed", "override", "library", "story")
    assert r["direction"]["genre"] in GENRES and 1 <= len(r["candidates"]) <= 2 and r["chosen"]["reason"].startswith("score")
    assert len(r["rejected"]) + len(r["candidates"]) == sum(1 for _ in range(len(r["rounds"]))) * 2
    for c in r["rejected"]:
        assert lib.get_asset(c["asset_id"]).status.value == "rejected"
    meta = lib.get_asset(r["chosen"]["asset_id"]).metadata
    assert meta["production"]["kind"] == "music" and meta["production"]["direction"]["genre"] == r["final_direction"]["genre"]
    a = pr.produce_for_brief(lib, "A rainy night-time bedroom, rain on the roof, a candle, safe", kind="ambience", name="rain",
                             seed=2, candidates=2, max_rounds=1)  # fmt: skip
    assert a["direction"]["high_cut"] > 0 and a["chosen"]["review"]["measured"]["speech_band_share"] < 0.6


def test_references_guide_selection_and_themes(lib: Library) -> None:
    r = pr.produce_for_brief(lib, "calm exploration, harp and flute", kind="music", name="exp", seed=1, candidates=1, max_rounds=0)
    guide = pr.add_reference(lib, "demo", r["chosen"]["asset_id"], note="the calm register we like")
    assert guide["references"][0]["measured"]["centroid_hz"] and guide["references"][0]["direction"]["genre"]
    again = pr.produce_for_brief(lib, "calm exploration again", kind="music", name="exp2", seed=3, candidates=2, max_rounds=0, project="demo")
    assert all(c["reference_distance"] is not None for c in again["candidates"])
    variations = pr.theme_variations(lib, r["chosen"]["asset_id"], moods=["triumphant", "dark"], name="exp_theme")
    assert [v["mood"] for v in variations] == ["triumphant", "dark"]
    src = lib.get_asset(r["chosen"]["asset_id"]).metadata["generator"]["spec"]["compose"]
    for v in variations:
        spec = lib.get_asset(v["asset_id"]).metadata["generator"]["spec"]["compose"]
        assert spec["genre"] == src["genre"] and "melody" in spec["fixed"] and "harmony" in spec["fixed"]


@pytest.fixture
def server(home: Path):
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    srv.close_pool()


def test_studio_routes(server: AppServer, home: Path) -> None:
    status, d = jcall(server, "/api/audio/direct", method="POST", body={"brief": "A lonely castle corridor at night after a betrayal", "kind": "ambience"})
    assert status == 200 and d["direction"]["indoor"] and d["expected"]["lufs"] == -24.0
    status, run = jcall(server, "/api/audio/produce", method="POST", body={"brief": "Calm exploration music", "kind": "music", "candidates": 1, "max_rounds": 0, "project": "demo"})
    assert status == 200
    deadline = time.time() + 180
    while time.time() < deadline:
        _, t = jcall(server, f"/api/audio/tasks/{run['task_id']}")
        if t["task"]["state"] != "running":
            break
        time.sleep(0.3)
    assert t["task"]["state"] == "done", t
    cand = t["task"]["result"]["candidates"][0]
    assert cand["variant_id"] and cand["reason"]
    status, ok = jcall(server, "/api/audio/approve", method="POST", body={"asset_id": cand["asset_id"], "project": "demo", "as_reference": True, "key": "vn/demo/music/calm"})
    assert status == 200 and ok["guide"]["references"]
    _, g = jcall(server, "/api/audio/style/demo")
    assert g["references"][0]["asset_id"] == cand["asset_id"]
    _, m = jcall(server, f"/api/audio/measure/{cand['asset_id']}")
    assert m["measured"]["lufs"] and m["review"]["score"] >= 0


def test_refinement_escalates_when_a_problem_survives_a_round() -> None:
    """A change that did not move the measure is not repeated: the next round lowers the eventful bed, then drops it."""
    d = pr.AmbienceDirection(beds=[{"bed": "rain", "level_db": -14}, {"bed": "fire_crackle", "level_db": -34}], events=[{"event": "drip", "rate": 2}])
    prob = [{"code": "too_busy", "weight": 1.0, "refinement": quality.REFINEMENTS["too_busy"]["ambience"]}]
    d1, c1 = pr.refine(d, prob)
    assert any("event rates" in c for c in c1) and d1.events[0].rate == 1.2
    d2, c2 = pr.refine(d1, prob, {"too_busy"})
    assert c2 == ["too_busy: dropped the fire_crackle layer (its texture is the busyness)"] and [b.bed for b in d2.beds] == ["rain"]
    m = pr.MusicDirection(percussion="light", energy=0.5)
    m2, c3 = pr.refine(m, [{"code": "too_busy", "weight": 1.0, "refinement": quality.REFINEMENTS["too_busy"]["music"]}], {"too_busy"})
    assert m2.percussion == "none" and m2.energy < 0.5 and c3
