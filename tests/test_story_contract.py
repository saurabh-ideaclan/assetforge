"""Content contract v2.0: compiling `examples/kite-season` (with stand-in library assets) into a release, the
mapping rules, validation, id/key stability, graph ids and migrations, the download closure, key checks, the
signed catalog, the CLI and the app routes. No network: publishing is covered in test_publish_r2.py."""

from __future__ import annotations

import base64
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assetforge.core.catalog import Library
from assetforge.core.models import NewAsset
from assetforge.errors import PolicyError
from assetforge.story import catalog_sign, contract
from assetforge.story import contract_models as cm
from assetforge.story.assets import IMAGE_GROUPS, manifest
from assetforge.story.project import StoryProject
from conftest import make_png, make_wav, run_cli

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "kite-season"


@pytest.fixture
def kite(tmp_path: Path) -> StoryProject:
    shutil.copytree(SAMPLE, tmp_path / "kite")
    return StoryProject(tmp_path / "kite")


def stock(lib: Library, project: StoryProject, tmp: Path, *, skip: set[str] | None = None) -> None:
    """A small library file for every key the story's manifest asks for (images as PNG, sound as WAV)."""
    for i, item in enumerate(manifest(project.load("script"), project.config, project.load("world"))):
        if skip and item.key in skip:
            continue
        if item.group in IMAGE_GROUPS:
            size = (64, 36) if item.request.aspect in ("16:9", "21:9") else (32, 48)
            src = make_png(tmp / "src" / f"{i}.png", size, ((i * 37) % 255, 90, 140, 255))
        else:
            src = make_wav(tmp / "src" / f"{i}.wav", 300, 220 + i * 31)
        lib.register_file(src, NewAsset(name=f"kite_{i}", type=item.request.type, key=item.key, project=item.request.project,
                                        scope="project", status="candidate", origin="procedural"))  # fmt: skip


@pytest.fixture
def stocked(lib: Library, kite: StoryProject, tmp_path: Path) -> StoryProject:
    stock(lib, kite, tmp_path)
    return kite


def edit_script(project: StoryProject, change: Any) -> None:
    path = project.root / "script.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data, indent=1))


def load(directory: Path) -> dict[str, Any]:
    """release.json plus each chapter's documents, read from the local mirror."""
    release = json.loads((directory / "release.json").read_text())
    root, sid = directory.parent, release["storyId"]
    chapters = {}
    for ref in release["chapters"]:
        bundle = json.loads(contract.local_path(root, sid, ref["manifest"]["key"]).read_text())
        docs = {n: json.loads(contract.local_path(root, sid, bundle[n]["key"]).read_text()) for n in ("graph", "presentation", "assets", "downloads")}
        docs["locale"] = json.loads(contract.local_path(root, sid, bundle["locales"][release["defaultLocale"]]["key"]).read_text())
        chapters[ref["id"]] = docs
    return {"release": release, "chapters": chapters}


def errors(issues: list[Any]) -> list[Any]:
    return [i for i in issues if i.level == "error"]


# ---------------------------------------------------------------------- compiling and validating


def test_release_resolves_every_reference(lib: Library, stocked: StoryProject) -> None:
    report = contract.compile_release(lib, stocked)
    assert report.status == "created" and report.release_id == "r0001" and report.valid, report.findings
    assert report.graph_id == "kite-season.graph.1" and errors(contract.validate_release(Path(report.path))) == []
    data = load(Path(report.path))
    release = data["release"]
    assert release["kind"] == "vn.release" and release["schemaVersion"] == "2.0" and release["defaultLocale"] == "en"
    assert release["requires"] == {"minEngineVersion": "2.0.0", "capabilities": ["graph.branching@1", "scene.layers@1", "audio.mix@1"]}
    assert [c["id"] for c in release["chapters"]] == ["ch1", "ch2"] and release["entryChapter"] == "ch1"
    for cid, docs in data["chapters"].items():
        graph, pres, assets, strings = docs["graph"], docs["presentation"], docs["assets"]["assets"], docs["locale"]["strings"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        assert graph["entry"] in nodes
        for n in graph["nodes"]:
            assert all(t in nodes for t in cm.node_targets(n)), n  # targets stay inside the chapter
            for key in [n.get("textKey"), n.get("disabledReasonKey"), *[o["textKey"] for o in n.get("options", [])]]:
                assert key is None or strings.get(key), (cid, key)
            if n["type"] == "dialogue":
                assert strings[graph["speakers"][n["speakerId"]]["nameKey"]]
            if "presentationRef" in n:
                cue = pres["cues"][n["presentationRef"]]
                scene = pres["scenes"][cue["scene"]]
                refs = [scene["background"], *[c["asset"] for c in scene["characters"]], *[e["asset"] for e in cue["events"]]]
                refs += [x["asset"] for x in (scene["audio"]["music"], scene["audio"]["ambience"]) if x]
                assert all(r in assets for r in refs if r), refs
            if n["type"] == "chapterEnd":
                assert n["nextChapter"] in {c["id"] for c in release["chapters"]} and n["nextChapter"] != cid
        for lid, a in assets.items():
            assert a["variants"], lid
            if a["type"] == "image":
                assert strings[a["altKey"]] and all(v["file"]["key"].startswith("blobs/sha256/") for v in a["variants"])
            else:
                assert {v["container"] for v in a["variants"]} == {"ogg", "mp4"} and all(v["durationMs"] > 0 for v in a["variants"])
        assert strings[f"chapter.{cid}.title"]
    # blobs are content-addressed copies of the library's own files
    blob = next(iter(data["chapters"]["ch1"]["assets"]["assets"].values()))["variants"][0]["file"]
    path = contract.local_path(Path(report.path).parent, "kite-season", blob["key"])
    assert cm.sha256(path.read_bytes()) == blob["sha256"] and blob["key"] == f"blobs/sha256/{blob['sha256']}{path.suffix}"


def test_mapping_choices_conditions_and_chapters(lib: Library, stocked: StoryProject) -> None:
    report = contract.compile_release(lib, stocked)
    ch = load(Path(report.path))["chapters"]
    g1 = {n["id"]: n for n in ch["ch1"]["graph"]["nodes"]}
    # in-chapter choice: each option plays its reaction lines, then all merge into the node after the choice
    choice = g1["s_roof.n6"]
    assert choice["type"] == "choice" and [o["target"] for o in choice["options"]] == ["s_roof.n6.o0.r0", "s_roof.n6.o1.r0", "s_roof.n6.o2.r0"]
    ends = {g1[t]["next"] for t in ("s_roof.n6.o0.r1", "s_roof.n6.o1.r1", "s_roof.n6.o2.r0")}
    assert ends == {"s_roof.n7"}
    assert {"op": "set", "path": "flags.told_truth", "value": True} in choice["options"][0]["effects"]
    # `when` on a line → a condition node that plays it or skips it
    gate = g1["s_roof.n10.w"]
    assert gate["type"] == "condition" and gate["cases"][0] == {"when": {"path": "stats.bold", "op": "gte", "value": 1}, "target": "s_roof.n10"}
    assert gate["fallback"].startswith("s_roof.n11")
    assert g1["s_roof.n18.w"]["cases"][0]["when"] == {"path": "flags.told_truth", "op": "eq", "value": True}
    # a scene jump inside a chapter is a direct link; into another chapter only through chapterEnd
    assert g1["s_roof.n18"]["next"] == "s_workbench.n0" and g1["s_roof.n18.w"]["fallback"] == "s_workbench.n0"
    assert {n["nextChapter"] for n in g1.values() if n["type"] == "chapterEnd"} == {"ch2"}
    # branch → condition: cases in order, fallback = goto (both endings live in ch2)
    g2 = {n["id"]: n for n in ch["ch2"]["graph"]["nodes"]}
    branch = g2["s_beach.n8"]
    assert branch["type"] == "condition" and branch["cases"][0]["target"] == "e_together_scene.n1"
    assert branch["cases"][0]["when"] == {"all": [{"path": "relationships.trust_ira", "op": "gte", "value": 3},
                                                  {"path": "stats.parts", "op": "gte", "value": 2}]}  # fmt: skip
    assert branch["fallback"] == "e_solo_scene.n0"
    assert {n["endingId"] for n in g2.values() if n["type"] == "ending"} == {"e_together", "e_solo"}
    # staging is folded into presentation: no show/place/music nodes, but their effect is on the cue's scene
    assert not any(k.endswith((".n2", ".n4")) and k.startswith("s_roof.n") for k in g1)  # the two `show` nodes
    pres = ch["ch1"]["presentation"]
    shop = pres["scenes"][pres["cues"]["s_roof.n13"]["scene"]]  # the `place` narration
    assert shop["background"] == "bg.repair_shop.evening" and shop["characters"] == []
    assert shop["audio"]["music"]["asset"] == "music.playful" and shop["audio"]["ambience"]["asset"] == "amb.repair_shop"
    speaking = pres["scenes"][pres["cues"]["s_roof.n3"]["scene"]]
    assert [c["characterId"] for c in speaking["characters"]] == ["ira"]
    # text keys come from ids and positions
    assert g1["s_roof.n3"]["textKey"] == "ch1.s_roof.n3" and choice["options"][1]["textKey"] == "ch1.s_roof.n6.o1"


def test_require_becomes_enabled_if_and_locked_choices_stay_playable(lib: Library, stocked: StoryProject) -> None:
    def change(d: dict[str, Any]) -> None:
        beach = d["scenes"][2]["nodes"]
        beach[7]["choices"][2]["require"] = {"var": "parts", "op": ">=", "value": 2}
        for option in beach[4]["choices"]:  # every option conditional: the compiler must keep it playable
            option["require"] = {"var": "bold", "op": ">=", "value": 1}
        beach[4]["choices"][1]["require"] = {"var": "told_truth", "op": "==", "value": 1}

    edit_script(stocked, change)
    report = contract.compile_release(lib, stocked)
    assert report.valid, errors(report.findings)
    ch2 = load(Path(report.path))["chapters"]["ch2"]
    g2 = {n["id"]: n for n in ch2["graph"]["nodes"]}
    option = g2["s_beach.n7"]["options"][2]
    assert option["enabledIf"] == {"path": "stats.parts", "op": "gte", "value": 2}
    assert ch2["locale"]["strings"][option["disabledReasonKey"]] == "Requires parts at least 2"
    gate = g2["s_beach.n4.gate"]
    assert gate["cases"][0]["target"] == "s_beach.n4" and gate["fallback"] == "s_beach.n4.open"
    assert gate["cases"][0]["when"]["any"][1] == {"path": "flags.told_truth", "op": "eq", "value": True}
    assert not any("enabledIf" in o for o in g2["s_beach.n4.open"]["options"])
    assert ch2["locale"]["strings"][g2["s_beach.n4"]["options"][1]["disabledReasonKey"]].startswith("Requires: Ravi admitted")
    # a hand-made release where an all-conditional choice is reachable directly is refused by validation
    graph_path = contract.local_path(Path(report.path).parent, "kite-season", json.loads((Path(report.path) / "ch2" / "bundle.json").read_text())["graph"]["key"])
    graph = json.loads(graph_path.read_text())
    for n in graph["nodes"]:
        if n["id"] == "s_beach.n4.gate":
            n["fallback"] = "s_beach.n4"
    graph_path.write_bytes(cm.canonical(graph))
    codes = {i.code for i in contract.validate_release(Path(report.path))}
    assert {"choice_may_block", "digest_mismatch"} <= codes


def test_state_schema_and_overflow(lib: Library, stocked: StoryProject) -> None:
    report = contract.compile_release(lib, stocked)
    variables = load(Path(report.path))["release"]["stateSchema"]["variables"]
    assert variables["relationships.trust_ira"] == {"type": "int", "default": 1, "min": 0, "max": 5, "overflow": "clamp"}
    assert variables["stats.bold"] == {"type": "int", "default": 0, "min": -3, "max": 3, "overflow": "clamp"}
    assert variables["flags.told_truth"] == {"type": "bool", "default": False, "overflow": "reject"}
    assert set(variables) == {"relationships.trust_ira", "stats.bold", "stats.parts", "flags.told_truth"}
    # every condition and effect is typed against the schema
    with pytest.raises(Exception):
        cm.Compare.model_validate({"path": "Stats.x", "op": "gte", "value": 1})
    bad = contract._check_state([{"path": "flags.told_truth", "op": "gte", "value": 1}],
                                [{"op": "add", "path": "flags.told_truth", "value": True}, {"op": "set", "path": "stats.nope", "value": 1}],
                                variables, "x")  # fmt: skip
    assert [i.code for i in bad] == ["state_type", "state_type", "unknown_state"]


def test_download_plan_covers_every_branch(lib: Library, stocked: StoryProject) -> None:
    report = contract.compile_release(lib, stocked)
    ch2 = load(Path(report.path))["chapters"]["ch2"]
    plan, pres = ch2["downloads"], ch2["presentation"]
    standard = set(plan["profiles"]["standard"]["assets"])
    assert {"music.triumphant", "music.sad", "music.tense", "music.heroic"} <= standard  # both endings' moods
    every = set().union(*(contract._cue_assets(pres, cue) for cue in pres["cues"]))
    assert every == standard
    core = plan["profiles"]["core"]
    assert set(core["assets"]) <= standard and core["assets"] == ["bg.beach.evening"] and plan["startupNodes"] == ["s_beach.n0"]
    assert plan["profiles"]["enhanced"]["bytes"] > plan["profiles"]["standard"]["bytes"] >= core["bytes"]
    assert plan["profiles"]["enhanced"]["qualities"] == ["standard", "high"]
    assert all(k.startswith("stories/kite-season/releases/r0001/ch2/") for k in core["documents"])


@pytest.mark.parametrize("key", ["../x.json", "/abs/x.json", "a/b.json?x=1", "a/%2e%2e/b.json", "a//b.json", "a/./b.json",
                                 "a/b.exe", "https://cdn.example/a.json", "a\\b.json", "a/b c.json", "a/b.json#f", ""])
def test_descriptor_keys_reject_unsafe_values(key: str) -> None:
    with pytest.raises(cm.ContractError):
        cm.check_key(key)
    with pytest.raises(Exception):
        cm.FileDescriptor.model_validate({"key": key, "mediaType": "application/json", "bytes": 1, "sha256": "0" * 64})


def test_descriptor_keys_accept_the_layout() -> None:
    for key in ("stories/kite-season/releases/r0001/ch1/graph.json", "blobs/sha256/" + "a" * 64 + ".webp", "catalog/history/3.json"):
        assert cm.check_key(key) == key
    assert cm.descriptor("a/b.ogg", b"xyz")["mediaType"] == "audio/ogg"


# ---------------------------------------------------------------------- stability, graph ids, migrations


def test_identical_recompile_mints_nothing(lib: Library, stocked: StoryProject) -> None:
    first = contract.compile_release(lib, stocked)
    before = sorted(p.name for p in (stocked.root / "releases").iterdir())
    again = contract.compile_release(lib, stocked)
    assert again.status == "identical" and again.release_id == "r0001" and "no new release" in again.message
    assert sorted(p.name for p in (stocked.root / "releases").iterdir()) == before
    assert [r["releaseId"] for r in contract.releases_view(stocked)["releases"]] == ["r0001"]
    assert first.graph_id == again.graph_id


def test_text_change_keeps_graph_and_gameplay_change_bumps_it(lib: Library, stocked: StoryProject) -> None:
    r1 = contract.compile_release(lib, stocked)
    edit_script(stocked, lambda d: d["scenes"][0]["nodes"][3].update(text="Two whole months of work."))
    r2 = contract.compile_release(lib, stocked)
    assert r2.status == "created" and r2.release_id == "r0002" and r2.graph_id == r1.graph_id and r2.graph_changed is False
    mig_path = contract.local_path(stocked.root / "releases", "kite-season", r2.migration or "")
    mig = json.loads(mig_path.read_text())
    assert mig["kind"] == "vn.migration" and mig["fromRelease"] == "r0001" and mig["toRelease"] == "r0002"
    assert mig["nodeAliases"] == {} and mig["stateOperations"] == []
    ch1 = load(Path(r2.path))["chapters"]["ch1"]
    assert ch1["locale"]["strings"]["ch1.s_roof.n3"] == "Two whole months of work."  # same key, new text
    assert (stocked.root / "releases" / "r0001" / "release.json").read_text() == (Path(r1.path) / "release.json").read_text()

    def gameplay(d: dict[str, Any]) -> None:
        d["scenes"][0]["nodes"][6]["choices"][1]["set"][0]["value"] = 2  # an effect changes
        del d["scenes"][0]["nodes"][1]  # a line goes: later ids shift
        d["variables"].append({"name": "kites", "initial": 1, "kind": "resource", "min": 0, "max": 9})

    edit_script(stocked, gameplay)
    r3 = contract.compile_release(lib, stocked)
    assert r3.release_id == "r0003" and r3.graph_id == "kite-season.graph.2" and r3.graph_changed is True and r3.valid
    mig = json.loads(contract.local_path(stocked.root / "releases", "kite-season", r3.migration or "").read_text())
    new_ids = {n["id"] for c in load(Path(r3.path))["chapters"].values() for n in c["graph"]["nodes"]}
    assert mig["nodeAliases"] and set(mig["nodeAliases"].values()) <= new_ids
    assert mig["nodeAliases"]["s_roof.n3"] == "s_roof.n2"  # matched by its text
    assert {"op": "default", "path": "stats.kites", "value": 1} in mig["stateOperations"]
    assert [r["graphId"] for r in contract.releases_view(stocked)["releases"]] == ["kite-season.graph.1"] * 2 + ["kite-season.graph.2"]


def test_missing_assets_block_or_make_a_draft(lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    stock(lib, kite, tmp_path, skip={"vn/kite-season/music/sad", "vn/kite-season/bg/beach/evening"})
    blocked = contract.compile_release(lib, kite)
    assert blocked.status == "blocked" and blocked.release_id is None
    assert blocked.missing_assets == ["vn/kite-season/bg/beach/evening", "vn/kite-season/music/sad"]
    assert not (kite.root / "releases" / "r0001").exists()
    draft = contract.compile_release(lib, kite, allow_missing=True)
    assert draft.status == "draft" and draft.release_id == "draft" and not draft.valid
    assert json.loads((kite.root / "releases" / "draft" / "release.json").read_text())["draft"] is True
    assert any(i.code == "draft" for i in contract.validate_release(kite.root / "releases" / "draft"))
    assert contract.releases_view(kite)["releases"] == [] and contract.releases_view(kite)["draft"]["releaseId"] == "draft"


def test_out_dir_and_read_only_mode(lib: Library, stocked: StoryProject, tmp_path: Path) -> None:
    out = tmp_path / "elsewhere"
    count = lambda: lib.conn.execute("SELECT COUNT(*) FROM asset_variants").fetchone()[0]  # noqa: E731
    before = count()
    report = contract.compile_release(lib, stocked, out, derive=False)
    assert count() == before  # nothing added to the library
    assert report.status == "blocked" and any(f.code == "no_audio_format" for f in report.findings)  # WAV only: no ogg yet
    draft = contract.compile_release(lib, stocked, out, derive=False, allow_missing=True)
    assert Path(draft.path).parent == out and not (stocked.root / "releases").exists() and count() == before
    variants = load(Path(draft.path))["chapters"]["ch1"]["assets"]["assets"]["bg.school_roof.day"]["variants"]
    assert [v["id"] for v in variants] == ["png"]  # the library file as it is
    report = contract.compile_release(lib, stocked, out)
    assert report.valid and count() > before  # web formats are derived variants, as `export` makes them


# ---------------------------------------------------------------------- the signed catalog


def _release(rid: str, graph: str) -> dict[str, Any]:
    return {"storyId": "kite-season", "releaseId": rid, "graphId": graph, "defaultLocale": "en",
            "requires": {"minEngineVersion": "2.0.0"}}  # fmt: skip


def _manifest(rid: str) -> dict[str, Any]:
    return cm.descriptor(f"stories/kite-season/releases/{rid}/release.json", rid.encode())


def test_catalog_signing_sequence_and_rollback(home: Path) -> None:
    key = catalog_sign.load_or_create_key(home)
    private, public = catalog_sign.key_paths(home)
    assert private.stat().st_mode & 0o777 == 0o600 and public.exists()
    assert catalog_sign.load_or_create_key(home).public_key().public_bytes_raw() == key.public_key().public_bytes_raw()
    pub = catalog_sign.load_public(home)
    one = catalog_sign.next_catalog(None, catalog_sign.entry_for(_release("r0001", "k.graph.1"), _manifest("r0001"), "Kite", "s", None))
    env = catalog_sign.sign(one, key)
    assert catalog_sign.verify(env, pub) == one and one["catalogSequence"] == 1
    assert json.loads(base64.b64decode(env["payload"])) == one
    two = catalog_sign.next_catalog(one, catalog_sign.entry_for(_release("r0002", "k.graph.1"), _manifest("r0002"), "Kite", "s",
                                                                 one["stories"][0]))  # fmt: skip
    assert two["catalogSequence"] == 2 and two["stories"][0]["current"] == "r0002"
    assert [r["releaseId"] for r in two["stories"][0]["releases"]] == ["r0001", "r0002"]
    back = catalog_sign.next_catalog(two, catalog_sign.entry_for(_release("r0001", "k.graph.1"), _manifest("r0001"), "Kite", "s",
                                                                  two["stories"][0]))  # fmt: skip
    assert back["catalogSequence"] == 3 and back["stories"][0]["current"] == "r0001"  # a rollback is a new sequence
    other = catalog_sign.next_catalog(back, {**back["stories"][0], "storyId": "another"})
    assert [s["storyId"] for s in other["stories"]] == ["another", "kite-season"] and other["catalogSequence"] == 4
    tampered = dict(env, payload=base64.b64encode(cm.canonical({**one, "catalogSequence": 9})).decode())
    with pytest.raises(PolicyError):
        catalog_sign.verify(tampered, pub)
    with pytest.raises(PolicyError):
        catalog_sign.verify(env, Ed25519PrivateKey.generate().public_key())


# ---------------------------------------------------------------------- CLI and app


def test_cli_contract_releases_and_publish_plan(home: Path, lib: Library, stocked: StoryProject, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "story", "contract", str(stocked.root), "--json")
    assert code == 0 and out["status"] == "ok" and out["release_status"] == "created" and out["release_id"] == "r0001" and out["valid"]
    code, out, _ = run_cli(capsys, "story", "contract", "--dir", str(stocked.root), "--json")
    assert code == 0 and out["release_status"] == "identical" and out["release_id"] == "r0001" and "identical" in out["message"]
    code, out, _ = run_cli(capsys, "story", "releases", str(stocked.root), "--json")
    assert code == 0 and [r["releaseId"] for r in out["releases"]] == ["r0001"]
    code, out, _ = run_cli(capsys, "story", "publish", str(stocked.root), "--json")  # no --yes: the plan, no network
    assert code == 0 and out["published"] is False and out["plan"]["remote"].startswith("not checked")
    assert out["plan"]["valid"] and out["plan"]["totals"]["unknown"]["count"] == len(out["plan"]["objects"])
    code, out, _ = run_cli(capsys, "story", "publish", str(stocked.root), "--yes", "--json")
    assert code == 4 and out["error"]["code"] == "PUBLISH_NOT_CONFIGURED"


def test_app_releases_route_and_actions(home: Path, kite: StoryProject, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from assetforge.app.api import router
    from assetforge.app.server import AppServer
    from assetforge.story.project import register
    from test_ui_server import jcall

    with Library(home) as lib:
        stock(lib, kite, tmp_path)
        sid = register(home, kite)
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        status, view = jcall(srv, f"/api/stories/{sid}/releases")
        assert status == 200 and view["releases"] == [] and view["r2"]["configured"] is False

        def run(body: dict[str, Any]) -> dict[str, Any]:
            _, started = jcall(srv, f"/api/stories/{sid}/run", method="POST", body=body)
            for _ in range(600):
                _, data = jcall(srv, f"/api/story-tasks/{started['task_id']}")
                if data["task"]["state"] != "running":
                    return data["task"]
                time.sleep(0.05)
            raise AssertionError("task did not finish")

        task = run({"action": "contract"})
        assert task["state"] == "done" and task["result"]["report"]["release_id"] == "r0001"
        task = run({"action": "publish", "release": "r0001", "confirm": True})  # confirmed, but R2 is not set up: plan only
        assert task["state"] == "done" and task["result"]["published"] is False and not task["result"]["plan"]["publishable"]
        _, view = jcall(srv, f"/api/stories/{sid}/releases")
        assert view["releases"][0]["releaseId"] == "r0001" and view["last_report"]["status"] == "created"
    finally:
        srv.shutdown()
        srv.server_close()
        srv.close_pool()
