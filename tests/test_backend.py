"""The content backend (docs/narrative/27) against the shared in-memory S3 fake: channels and the signed index,
deltas, the reference client staying in sync (the plan's ten acceptance cases, one test each), the key prefix in
a shared bucket, and the client kit. No network, no real SDK."""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assetforge.core.catalog import Library
from assetforge.core.models import NewAsset
from assetforge.errors import ConflictError, PolicyError
from assetforge.publish import backend, r2
from assetforge.publish.client import Client
from assetforge.story import catalog_sign, contract
from assetforge.story import contract_models as cm
from assetforge.story.assets import IMAGE_GROUPS, manifest
from assetforge.story.project import StoryProject, register
from conftest import make_png, run_cli
from s3fake import CFG, FakeS3
from test_story_contract import SAMPLE, edit_script, stock

SID = "kite-season"


@pytest.fixture
def kite(lib: Library, home: Path, tmp_path: Path) -> StoryProject:
    shutil.copytree(SAMPLE, tmp_path / "kite")
    project = StoryProject(tmp_path / "kite")
    stock(lib, project, tmp_path)
    assert contract.compile_release(lib, project).release_id == "r0001"
    register(home, project)
    return project


def rel(project: StoryProject, rid: str) -> Path:
    return project.root / "releases" / rid


def publish(home: Path, project: StoryProject, rid: str, s3: FakeS3, cfg: r2.R2Config = CFG) -> dict[str, Any]:
    return backend.publish(rel(project, rid), yes=True, home=home, client=s3, cfg=cfg)


def make_client(home: Path, s3: FakeS3, cache: Path, channel: str = "staging", prefix: str = "") -> Client:
    return Client(s3.fetcher(prefix), catalog_sign.key_paths(home)[1].read_text().strip(), cache, channel=channel)


def text_change(lib: Library, project: StoryProject) -> str:
    edit_script(project, lambda d: d["scenes"][0]["nodes"][0].update(text="The bell is still ringing."))
    report = contract.compile_release(lib, project)
    assert report.status == "created", report.message
    return str(report.release_id)


def add_image(lib: Library, project: StoryProject, tmp: Path) -> str:
    before = {i.key for i in manifest(project.load("script"), project.config, project.load("world"))}
    edit_script(project, lambda d: d["scenes"][0]["nodes"].insert(1, {"kind": "cg", "cg": "kite_rising", "text": "The kite climbs."}))
    project = StoryProject(project.root)
    new = [i for i in manifest(project.load("script"), project.config, project.load("world")) if i.key not in before]
    assert len(new) == 1 and new[0].group in IMAGE_GROUPS
    src = make_png(tmp / "extra" / "kite_rising.png", (64, 36), (11, 222, 33, 255))
    lib.register_file(src, NewAsset(name="kite_rising", type=new[0].request.type, key=new[0].key, project=new[0].request.project,
                                    scope="project", status="candidate", origin="procedural"))  # fmt: skip
    report = contract.compile_release(lib, project)
    assert report.status == "created" and report.valid, report.message
    return str(report.release_id)


def cached_ok(client: Client, story_id: str = SID) -> bool:
    installed = client.installed(story_id)
    assert installed
    for sha in installed["objects"].values():
        data = (client.cache / "objects" / sha).read_bytes()
        assert cm.sha256(data) == sha
    return True


# ---------------------------------------------------------------------- acceptance 1-6: publish, sync, delta, promote, rollback, unlist


def test_1_publish_then_client_syncs_from_empty(home: Path, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    receipt = publish(home, kite, "r0001", s3)
    assert receipt["channel"] == "staging" and receipt["sequence"] == 1
    assert s3.puts[-2:] == ["catalog/staging/history/1.json", "catalog/staging/current.json"]
    index = s3.catalog(home)
    entry = index["stories"][0]
    assert entry["chapterCount"] == 2 and entry["locales"] == ["en"] and entry["downloadBytes"]["standard"] >= entry["downloadBytes"]["core"] > 0
    assert entry["releases"][0]["publishedAt"] == index["generatedAt"] and "delta" not in entry["releases"][0]
    client = make_client(home, s3, tmp_path / "cache")
    result = client.sync()
    assert result["status"] == "updated" and result["new"] == [SID] and result["sequence"] == 1
    assert cached_ok(client) and client.installed(SID)["release"] == "r0001"
    assert set(client.installed(SID)["objects"]) <= set(s3.objects)
    assert client.sync()["status"] == "not_modified"  # If-None-Match → 304
    # local records: log line and cached view
    log = backend.read_log(home)
    assert log[0]["command"] == "publish" and log[0]["sequence"] == 1
    assert backend.load_state(home)["channels"]["staging"]["sequence"] == 1


def test_2_text_change_downloads_one_locale_and_the_delta_agrees(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    client = make_client(home, s3, tmp_path / "cache")
    client.sync()
    rid = text_change(lib, kite)
    delta_path = rel(kite, rid) / "delta-from-r0001.json"
    doc = json.loads(delta_path.read_bytes())
    assert cm.canonical(doc) == delta_path.read_bytes()  # canonical, deterministic
    assert doc["graphChanged"] is False and doc["chapters"]["added"] == doc["chapters"]["removed"] == [] and doc["blobs"]["added"] == []
    [changed] = doc["chapters"]["changed"]
    assert changed["documents"] == ["locales/en"] and changed["textOnly"]
    receipt = publish(home, kite, rid, s3)
    assert f"stories/{SID}/releases/{rid}/delta-from-r0001.json" in receipt["objects"]
    index = s3.catalog(home)
    ref = index["stories"][0]["releases"][-1]
    assert ref["delta"]["key"].endswith("delta-from-r0001.json") and index["stories"][0]["updatedSequence"] == 2
    assert index["changes"][0]["type"] == "release_published" and index["changes"][0]["summary"]["textOnly"]
    # both planning paths give the same answer (a copy of the client cache plans without syncing)
    shutil.copytree(tmp_path / "cache", tmp_path / "copy")
    twin = make_client(home, s3, tmp_path / "copy")
    installed = twin.installed(SID)
    new = twin._tree(ref["manifest"])
    locale_key = new["chapters"][changed["id"]]["locales/en"]["key"]
    expected = {"documents": [locale_key], "blobs": []}
    assert Client.plan_from_delta(doc, new, installed) == Client.plan_from_manifests(installed, new) == expected
    result = client.sync()
    assert result["changed"] == [SID] and result["downloaded"].get("documents") == [locale_key] and "blobs" not in result["downloaded"]
    assert ref["delta"]["key"] in result["downloaded"]["manifests"] and cached_ok(client)


def test_3_new_image_downloads_one_blob_and_the_changed_chapter(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    client = make_client(home, s3, tmp_path / "cache")
    client.sync()
    rid = add_image(lib, kite, tmp_path)
    doc = json.loads((rel(kite, rid) / "delta-from-r0001.json").read_bytes())
    # the image's chapter changes everywhere; a new graph id also rewrites every other chapter's graph (it names the id)
    changes = {c["id"]: c["documents"] for c in doc["chapters"]["changed"]}
    assert doc["graphChanged"] and changes == {"ch1": ["assets", "graph", "locales/en", "presentation"], "ch2": ["graph"]}
    publish(home, kite, rid, s3)
    result = client.sync()
    new = client._tree(s3.catalog(home)["stories"][0]["releases"][-1]["manifest"])
    assert result["downloaded"]["documents"] == sorted(new["chapters"][c][n]["key"] for c, names in changes.items() for n in names)
    assert len(result["downloaded"]["blobs"]) == 1 and result["downloaded"]["blobs"][0] in {b["key"] for b in doc["blobs"]["added"]}
    assert cached_ok(client)


def test_4_promote_writes_only_the_production_index(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    rid = text_change(lib, kite)
    publish(home, kite, rid, s3)
    prod = make_client(home, s3, tmp_path / "prod", channel="production")
    assert prod.sync()["status"] == "rejected"  # no production index yet: nothing to read
    s3.puts.clear()
    plan = backend.promote(SID, yes=False, home=home, client=s3, cfg=CFG)
    assert plan["written"] is False and plan["promotable"] and plan["release"] == rid and s3.writes == 0
    done = backend.promote(SID, yes=True, home=home, client=s3, cfg=CFG)
    assert done["written"] and done["sequence"] == 1
    assert s3.puts == ["catalog/production/history/1.json", "catalog/production/current.json"]  # zero objects uploaded
    index = s3.catalog(home, "production")
    assert index["channel"] == "production" and index["stories"][0]["current"] == rid
    assert index["changes"][0]["type"] == "promoted" and [r["releaseId"] for r in index["stories"][0]["releases"]] == [rid]
    assert index["stories"][0]["downloadBytes"] == s3.catalog(home)["stories"][0]["downloadBytes"]
    result = prod.sync()
    assert result["new"] == [SID] and prod.installed(SID)["release"] == rid and cached_ok(prod)
    view = backend.status(home=home, client=s3, cfg=CFG)
    assert view["stories"][0]["state"] == "in sync" and view["stories"][0]["production"] == rid


def test_5_rollback_is_a_new_sequence_and_downloads_nothing(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    client = make_client(home, s3, tmp_path / "cache")
    client.sync()
    rid = text_change(lib, kite)
    publish(home, kite, rid, s3)
    client.sync()
    s3.puts.clear()
    result = backend.rollback(SID, "r0001", channel="staging", yes=True, home=home, client=s3, cfg=CFG)
    assert result["sequence"] == 3 and s3.puts == ["catalog/staging/history/3.json", "catalog/staging/current.json"]
    index = s3.catalog(home)
    assert index["stories"][0]["current"] == "r0001" and index["changes"][0]["type"] == "rollback"
    assert index["changes"][0]["fromReleaseId"] == rid
    synced = client.sync()
    assert synced["changed"] == [SID] and synced["downloaded"] == {} and client.installed(SID)["release"] == "r0001"


def test_6_unlist_keeps_the_entry_and_moves_the_sequence(home: Path, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    client = make_client(home, s3, tmp_path / "cache")
    client.sync()
    backend.set_listed(SID, listed=False, channel="staging", yes=True, home=home, client=s3, cfg=CFG)
    entry = s3.catalog(home)["stories"][0]
    assert entry["status"] == "unlisted" and entry["current"] == "r0001" and entry["updatedSequence"] == 2 and entry["addedSequence"] == 1
    result = client.sync()
    assert result["unlisted"] == [SID] and client.shelf() == [] and client.installed(SID)["release"] == "r0001"  # saves keep working
    backend.set_listed(SID, listed=True, channel="staging", yes=True, home=home, client=s3, cfg=CFG)
    assert client.sync()["relisted"] == [SID] and [s["storyId"] for s in client.shelf()] == [SID]
    assert [c["type"] for c in s3.catalog(home)["changes"]] == ["story_relisted", "story_unlisted", "story_added"]


# ---------------------------------------------------------------------- acceptance 7-10: trust, repair/gc, races, explicit writes


def test_7_tampered_foreign_and_replayed_indexes_are_rejected(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    publish(home, kite, text_change(lib, kite), s3)
    client = make_client(home, s3, tmp_path / "cache")
    assert client.sync()["sequence"] == 2
    good = s3.objects["catalog/staging/current.json"]["body"]
    env = json.loads(good)
    payload = json.loads(base64.b64decode(env["payload"]))
    tampered = dict(env, payload=base64.b64encode(cm.canonical({**payload, "catalogSequence": 9})).decode())
    foreign = catalog_sign.sign({**payload, "catalogSequence": 9}, Ed25519PrivateKey.generate())
    replay = s3.objects["catalog/staging/history/1.json"]["body"]
    for body, why in ((cm.canonical(tampered), "signature"), (cm.canonical(foreign), "unknown key"), (replay, "replayed")):
        s3.seed("catalog/staging/current.json", body)
        result = client.sync()
        assert result["status"] == "rejected" and why in result["errors"][0], result
        assert client.state["sequence"] == 2 and client.installed(SID)["release"] == "r0002"  # the last good index stays
    s3.seed("catalog/staging/current.json", good)
    assert client.sync()["status"] in ("updated", "not_modified")


def test_8_verify_repair_and_gc(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    rid = add_image(lib, kite, tmp_path)
    publish(home, kite, rid, s3)
    assert backend.verify(home=home, client=s3, cfg=CFG)["healthy"]
    _, objs = contract.release_objects(rel(kite, rid))
    blob = next(o for o in objs if o.role == "blob")
    doc = next(o for o in objs if o.key.endswith("/graph.json"))
    del s3.objects[blob.key]
    s3.objects[doc.key]["body"] = s3.objects[doc.key]["body"][:-1] + b" "  # same size, wrong bytes: only --deep sees it
    shallow = backend.verify(home=home, client=s3, cfg=CFG)
    assert [r["key"] for r in shallow["missing"]] == [blob.key] and shallow["corrupted"] == []
    report = backend.verify(home=home, client=s3, cfg=CFG, deep=True)
    assert [r["key"] for r in report["missing"]] == [blob.key] and [r["key"] for r in report["corrupted"]] == [doc.key]
    s3.puts.clear()
    planned = backend.repair(home=home, yes=False, deep=True, client=s3, cfg=CFG)
    assert planned["written"] is False and len(planned["fixable"]) == 2 and s3.writes == 0
    repaired = backend.repair(home=home, yes=True, deep=True, client=s3, cfg=CFG)
    assert repaired["repaired"] == 2 and sorted(s3.puts) == sorted([blob.key, doc.key])
    assert backend.verify(home=home, client=s3, cfg=CFG, deep=True)["healthy"]
    # gc: the new image of rid is referenced only by old history entries once a newer index drops rid
    image = next(b["key"] for b in json.loads((rel(kite, rid) / "delta-from-r0001.json").read_bytes())["blobs"]["added"])
    previous = backend.read_index(r2.Store(s3, CFG), "staging", home)
    entry = previous.story(SID)
    old_only = {**entry, "current": "r0001", "releases": [r for r in entry["releases"] if r["releaseId"] == "r0001"]}
    backend.commit_index(r2.Store(s3, CFG), previous,
                         catalog_sign.next_catalog(previous.payload, old_only, change={"type": "rollback", "storyId": SID}),
                         catalog_sign.load_or_create_key(home))  # fmt: skip
    orphan = "blobs/sha256/" + "e" * 64 + ".webp"
    s3.seed(orphan, b"nobody points at me")
    dry = backend.gc(home=home, client=s3, cfg=CFG)
    assert [r["key"] for r in dry["orphans"]] == [orphan] and dry["written"] is False and orphan in s3.objects
    assert image not in [r["key"] for r in dry["orphans"]]
    with pytest.raises(PolicyError, match="no key prefix"):  # this bucket has no prefix: the whole bucket must be declared ours
        backend.gc(home=home, yes=True, delete_orphans=True, client=s3, cfg=CFG)
    done = backend.gc(home=home, yes=True, delete_orphans=True, whole_bucket=True, client=s3, cfg=CFG)
    assert done["deleted"] == 1 and s3.deletes == [orphan] and image in s3.objects


def test_9_two_writers_race_for_one_sequence(home: Path, kite: StoryProject) -> None:
    for conditional in (True, False):  # IfNoneMatch="*", and the HEAD-then-PUT fallback for clients without it
        s3 = FakeS3(conditional=conditional)
        publish(home, kite, "r0001", s3)
        store = r2.Store(s3, CFG)
        key = catalog_sign.load_or_create_key(home)
        a = backend.read_index(store, "staging", home)
        b = backend.read_index(store, "staging", home)
        entry = a.story(SID)
        first = catalog_sign.next_catalog(a.payload, {**entry, "status": "unlisted"}, change={"type": "story_unlisted", "storyId": SID})
        second = catalog_sign.next_catalog(b.payload, entry, change={"type": "release_published", "storyId": SID})
        assert backend.commit_index(store, a, first, key) == 2
        before = s3.objects["catalog/staging/current.json"]["body"]
        with pytest.raises(ConflictError, match="another writer"):
            backend.commit_index(store, b, second, key)
        assert s3.objects["catalog/staging/current.json"]["body"] == before and s3.catalog(home)["stories"][0]["status"] == "unlisted"


def test_10_every_write_needs_yes_and_plans_write_nothing(home: Path, lib: Library, kite: StoryProject, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    rid = text_change(lib, kite)
    publish(home, kite, rid, s3)
    s3.seed("blobs/sha256/" + "f" * 64 + ".png", b"orphan")
    s3.puts.clear()
    with pytest.raises(PolicyError, match="explicit yes"):
        backend.publish(rel(kite, rid), yes=False, home=home, client=s3, cfg=CFG)
    kw = {"home": home, "client": s3, "cfg": CFG}
    for result in (backend.promote(SID, yes=False, **kw), backend.rollback(SID, "r0001", channel="staging", yes=False, **kw),
                   backend.set_listed(SID, listed=False, channel="staging", yes=False, **kw),
                   backend.repair(yes=False, **kw), backend.gc(yes=False, delete_orphans=True, **kw),
                   backend.gc(yes=True, delete_orphans=False, **kw)):  # fmt: skip
        assert result["written"] is False
    backend.plan(rel(kite, rid), **kw)
    backend.status(**kw)
    backend.verify(deep=True, **kw)
    backend.ls(**kw)
    assert s3.writes == 0
    # the CLI: without --yes every write command prints its plan and writes nothing
    monkeypatch.setattr(r2, "make_client", lambda cfg: s3)
    monkeypatch.setenv("ASSETFORGE_R2_BUCKET", CFG.bucket)
    for name, value in (("ASSETFORGE_R2_ACCOUNT_ID", "acct0000abcd"), ("ASSETFORGE_R2_ACCESS_KEY", "AK"), ("ASSETFORGE_R2_SECRET_KEY", "SK")):
        monkeypatch.setenv(name, value)
    for args in (("publish", str(kite.root)), ("promote", SID), ("rollback", SID, "r0001"), ("unlist", SID), ("relist", SID),
                 ("repair",), ("gc", "--delete-orphans")):  # fmt: skip
        code, out, _ = run_cli(capsys, "backend", *args, "--json")
        assert code == 0 and out["status"] == "ok", (args, out)
    code, out, _ = run_cli(capsys, "story", "publish", str(kite.root), "--json")  # the alias
    assert code == 0 and out["published"] is False and out["plan"]["catalog"]["key"] == "catalog/staging/current.json"
    code, out, _ = run_cli(capsys, "backend", "status", "--json")
    assert code == 0 and out["connection"]["ok"] and out["stories"][0]["staging"] == rid
    assert s3.writes == 0


# ---------------------------------------------------------------------- the prefix in a shared bucket, the client kit


def test_prefix_keeps_everything_inside_it(home: Path, lib: Library, kite: StoryProject, tmp_path: Path) -> None:
    cfg = replace(CFG, prefix="vn")
    assert r2.clean_prefix("/vn/") == "vn" and r2.clean_prefix("") == ""
    with pytest.raises(r2.PublishNotConfiguredError):
        r2.clean_prefix("../up")
    s3 = FakeS3(page_size=7)  # several listing pages
    foreign = {"other-product/catalog/staging/current.json": b"theirs", "blobs/sha256/" + "a" * 64 + ".png": b"theirs",
               "vnx/blobs/sha256/" + "b" * 64 + ".png": b"theirs", "vn-old/stories/x/release.json": b"theirs"}  # fmt: skip
    for k, v in foreign.items():
        s3.seed(k, v)
    publish(home, kite, "r0001", s3, cfg)
    publish(home, kite, text_change(lib, kite), s3, cfg)
    assert s3.puts and all(k.startswith("vn/") for k in s3.puts)
    index = s3.catalog(home, prefix="vn")
    assert all(not r["manifest"]["key"].startswith("vn/") for r in index["stories"][0]["releases"])  # keys stay relative
    orphan = "blobs/sha256/" + "c" * 64 + ".webp"
    s3.seed("vn/" + orphan, b"ours, unreferenced")
    inv = backend.ls(home=home, client=s3, cfg=cfg)
    listed = {r["key"] for r in inv["objects"]}
    assert all(p == "vn/" for p in s3.lists) and orphan in listed and not any(k.startswith(("vn/", "other-product", "vnx", "vn-old")) for k in listed)
    assert [r["key"] for r in inv["orphans"]] == [orphan] and "catalog/staging/current.json" in listed
    done = backend.gc(home=home, yes=True, delete_orphans=True, client=s3, cfg=cfg)
    assert s3.deletes == ["vn/" + orphan] and done["deleted"] == 1
    assert all(s3.objects[k]["body"] == v for k, v in foreign.items())  # never listed, counted or deleted
    assert backend.verify(home=home, client=s3, cfg=cfg, deep=True)["healthy"]
    client = make_client(home, s3, tmp_path / "cache", prefix="vn")
    assert client.sync()["new"] == [SID] and cached_ok(client)
    assert cfg.public_url("catalog/staging/current.json") is None
    assert replace(cfg, public_base="https://cdn.example.com/").public_url("catalog/production/current.json") == \
        "https://cdn.example.com/vn/catalog/production/current.json"
    # verify --public reads anonymously through the public origin (a stand-in fetch here)
    pub_cfg = replace(cfg, public_base="https://cdn.example.com")
    seen: list[str] = []

    def public_fetch(url: str, method: str) -> tuple[int, bytes, dict[str, str]]:
        seen.append(url)
        key = url.removeprefix("https://cdn.example.com/")
        return (200, s3.objects[key]["body"], {}) if key in s3.objects else (404, b"", {})

    report = backend.verify(home=home, client=s3, cfg=pub_cfg, channel="staging", public=True, public_fetch=public_fetch)
    assert report["healthy"] and report["public"]["ok"] and all(u.startswith("https://cdn.example.com/vn/") for u in seen)


def test_pull_rebuilds_local_records(home: Path, kite: StoryProject) -> None:
    s3 = FakeS3()
    publish(home, kite, "r0001", s3)
    backend.promote(SID, yes=True, home=home, client=s3, cfg=CFG)
    shutil.rmtree(backend.backend_dir(home))
    index = contract.load_index(kite.root / "releases")
    for entry in index["releases"]:
        entry.pop("channels", None)
        entry["published"] = None
    contract.save_index(kite.root / "releases", index)
    s3.puts.clear()
    result = backend.pull(home=home, client=s3, cfg=CFG)
    assert result["channels"] == {"staging": 1, "production": 1} and result["stories_updated"] == [SID] and s3.writes == 0
    entry = contract.load_index(kite.root / "releases")["releases"][0]
    assert set(entry["channels"]) == {"staging", "production"} and entry["published"]["sequence"] == 1
    assert backend.load_state(home)["channels"]["production"]["sequence"] == 1


def test_client_kit(home: Path, kite: StoryProject, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "kit"
    code, data, _ = run_cli(capsys, "backend", "client-kit", "--out", str(out), "--json")
    assert code == 0 and data["status"] == "partial" and data["warnings"]  # no key yet: made on the first publish
    publish(home, kite, "r0001", FakeS3())
    code, data, _ = run_cli(capsys, "backend", "client-kit", "--out", str(out), "--json")
    assert code == 0 and data["status"] == "ok"
    key = json.loads((out / "public-key.json").read_text())
    assert key["publicKey"] == catalog_sign.key_paths(home)[1].read_text().strip() and key["alg"] == "Ed25519"
    kinds = {p.name.removesuffix(".schema.json") for p in (out / "schemas").glob("*.schema.json")}
    assert kinds >= {"vn.catalog", "vn.delta", "vn.release", "vn.chapter", "vn.chapter.bundle", "vn.presentation", "vn.assets",
                     "vn.locale", "vn.downloads", "vn.migration", "vn.catalog.envelope"}  # fmt: skip
    assert "channel" in json.loads((out / "schemas" / "vn.catalog.schema.json").read_text())["properties"]
    cm.Catalog.model_validate(json.loads((out / "samples" / "index.sample.json").read_text()))
    cm.Delta.model_validate(json.loads((out / "samples" / "delta.sample.json").read_text()))
    guide = (out / "INTEGRATION.md").read_text()
    assert "If-None-Match" in guide and "accessPolicyId" in guide and key["publicKey"] in guide
    # the shipped client stands alone (stdlib + cryptography)
    source = (out / "client.py").read_text()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert imports and not any("assetforge" in line for line in imports)
    spec = importlib.util.spec_from_file_location("kit_client", out / "client.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.key_id(base64.b64decode(key["publicKey"])) == key["keyId"]


def test_app_routes_plan_first_then_write(home: Path, kite: StoryProject, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from assetforge.app.api import router
    from assetforge.app.server import AppServer
    from test_ui_server import jcall

    s3 = FakeS3()
    monkeypatch.setattr(r2, "make_client", lambda cfg: s3)
    for name, value in (("ASSETFORGE_R2_BUCKET", CFG.bucket), ("ASSETFORGE_R2_PREFIX", "vn"), ("ASSETFORGE_R2_ACCOUNT_ID", "acct0000abcd"),
                        ("ASSETFORGE_R2_ACCESS_KEY", "AK"), ("ASSETFORGE_R2_SECRET_KEY", "SK"),
                        ("ASSETFORGE_R2_PUBLIC_BASE", "https://cdn.example.com")):  # fmt: skip
        monkeypatch.setenv(name, value)
    srv = AppServer(home, router, token="secret")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        status, view = jcall(srv, "/api/backend/status?cached=1")
        assert status == 200 and view["connection"]["ok"] is None and view["stories"][0]["local_latest"] == "r0001" and s3.lists == []
        status, view = jcall(srv, "/api/backend/status")
        assert view["connection"]["ok"] and view["config"]["prefix"] == "vn" and view["stories"][0]["state"] == "never published"
        assert view["channels"]["production"]["index_url"] == "https://cdn.example.com/vn/catalog/production/current.json"
        sid = view["stories"][0]["registered"]
        _, planned = jcall(srv, "/api/backend/publish", method="POST", body={"story": sid})
        assert planned["written"] is False and planned["plan"]["publishable"] and s3.writes == 0
        _, done = jcall(srv, "/api/backend/publish", method="POST", body={"story": sid, "confirm": True})
        assert done["written"] and done["receipt"]["sequence"] == 1 and all(k.startswith("vn/") for k in s3.puts)
        _, planned = jcall(srv, "/api/backend/promote", method="POST", body={"story": SID})
        assert planned["written"] is False and planned["promotable"]
        _, done = jcall(srv, "/api/backend/promote", method="POST", body={"story": SID, "confirm": True})
        assert done["written"] and done["sequence"] == 1
        _, report = jcall(srv, "/api/backend/verify", method="POST", body={})
        assert report["healthy"] and report["channels"]["production"]["sequence"] == 1
        _, view = jcall(srv, "/api/backend/status?cached=1")
        assert view["stories"][0]["state"] == "in sync" and view["channels"]["staging"]["changes"][0]["type"] == "story_added"
        assert view["key"]["public_key"] and view["log"][0]["command"] == "promote"
        status, err = jcall(srv, "/api/backend/rollback", method="POST", body={"story": SID})
        assert status >= 400 and err["error"]["code"] == "INVALID_INPUT"
    finally:
        srv.shutdown()
        srv.server_close()
        srv.close_pool()


def test_https_fetcher_sends_an_explicit_user_agent(monkeypatch) -> None:  # noqa: ANN001
    """The content CDN answers 403 to urllib's default `Python-urllib/3.x` agent."""
    import urllib.request

    from assetforge.publish import client

    seen = {}

    class Response:
        status, headers = 200, {"ETag": '"e1"'}

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *exc) -> None:  # noqa: ANN002
            return None

    def urlopen(request, timeout):  # noqa: ANN001, ANN202
        seen.update(request.header_items())
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    status, body, etag = client.https_fetcher("https://cdn.example/prefix")("index.json", etag='"e0"')
    assert status == 200 and etag == '"e1"'
    assert seen["User-agent"] == client.USER_AGENT and not seen["User-agent"].startswith("Python-urllib")
    assert seen["If-none-match"] == '"e0"'
