"""`library merge`: bring another library (assets, files, jobs, stories) into this one without losing anything."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assetforge.core import collections
from assetforge.core.catalog import Library
from assetforge.core.merge import merge_library
from assetforge.core.models import AssetPatch, ImportOptions
from assetforge.errors import InvalidInputError
from assetforge.story import steps
from assetforge.story.progress import library_assets
from assetforge.story.project import StoryProject, registered

from conftest import make_png, run_cli


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A second library with images (one keyed, one tagged and in a collection), a variant, usage, a story written
    with the fake provider (text jobs + jobs/ folders) and one story folder outside the library."""
    monkeypatch.setenv("ASSETFORGE_ENABLE_FAKE_PROVIDER", "1")
    home = tmp_path / "other"
    with Library(home) as other:
        hero = other.import_paths([make_png(tmp_path / "in/hero.png", color=(10, 200, 30, 255))],
                                  ImportOptions(type="character")).items[0].asset_id  # fmt: skip
        other.update_asset(hero, AssetPatch(key="vn/fog/char/tom/neutral", project="vn-fog", add_tags=["hero"]))
        other.add_variant(hero, make_png(tmp_path / "in/hero_small.png", (8, 8), (10, 200, 30, 255)), label="small")
        other.record_usage(hero, "vn-fog", usage_key="vn/fog/char/tom/neutral")
        sky = other.import_paths([make_png(tmp_path / "in/sky.png", color=(30, 30, 220, 255))],
                                 ImportOptions(type="background")).items[0].asset_id  # fmt: skip
        collections.ensure_collection(other, "fog-set")
        collections.add_assets(other, "fog-set", [sky])
        inside = steps.init(home / "stories" / "fog", title="Fog", premise="An apprentice uncovers smugglers.",
                            scenes=3, endings=1, provider="fake")  # fmt: skip
        ctx = steps.Ctx(other, inside)
        steps.world(ctx); steps.cast(ctx); steps.outline(ctx); steps.write(ctx)  # noqa: E702
        jobs = other.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0]
    outside = steps.init(tmp_path / "elsewhere" / "tide", title="Tide", premise="A diver finds a bell.", scenes=2,
                         endings=1, provider="fake")  # fmt: skip
    return {"home": home, "hero": hero, "sky": sky, "jobs": jobs, "outside": outside.root}


def test_merge_brings_everything_and_keeps_the_source(lib: Library, source: dict) -> None:
    before = (source["home"] / "catalog.sqlite3").read_bytes()
    mine = lib.import_paths([make_png(lib.paths.home.parent / "mine.png", color=(200, 10, 10, 255))],
                            ImportOptions(type="icon")).items[0].asset_id  # fmt: skip
    report = merge_library(lib, source["home"], stories=[source["outside"]])

    assert report["assets"] == {"added": 2, "linked": 0} and report["jobs"] == source["jobs"] > 0
    assert (source["home"] / "catalog.sqlite3").read_bytes() == before  # only read
    assert lib.get_asset(mine)  # what was here stays
    hero = lib.get_asset(lib.resolve_asset_id("vn-fog:vn/fog/char/tom/neutral"))
    assert {v.label for v in hero.variants} == {"original", "small"} and "hero" in hero.tags
    assert all(Path(v.file.path).is_file() for v in hero.variants)
    assert lib.verify()["ok"]

    # jobs moved with their folders; text answers are cached under the same fingerprints
    rows = lib.conn.execute("SELECT id FROM generation_jobs WHERE kind = 'text'").fetchall()
    assert rows and all((lib.paths.jobs / r["id"]).is_dir() for r in rows)

    # stories: the library's own and the outside one, copied and registered here
    stories = registered(lib.paths.home)
    assert set(stories) == {"fog", "tide"}
    assert all(lib.paths.home in p.parents for p in stories.values())
    assert StoryProject(stories["fog"]).scenes()
    assert library_assets(lib, StoryProject(stories["fog"])) is not None

    # merged assets are searchable and keep their collection
    from assetforge.core.search import SearchQuery, search

    assert hero.id in {hit.id for hit in search(lib, SearchQuery(text="hero")).results}
    _, members = collections.collection_assets(lib, "fog-set")
    assert [m.name for m in members] == ["sky"]


def test_merge_is_safe_to_rerun_and_never_overwrites(lib: Library, source: dict) -> None:
    first = merge_library(lib, source["home"])
    count = lib.conn.execute("SELECT count(*) FROM assets").fetchone()[0]
    jobs = lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0]
    again = merge_library(lib, source["home"])
    assert again["assets"] == {"added": 0, "linked": 0} and again["jobs"] == 0 and again["variants"] == 0
    assert lib.conn.execute("SELECT count(*) FROM assets").fetchone()[0] == count
    assert lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0] == jobs
    assert [s["status"] for s in again["stories"]] == ["updated"] and again["stories"][0]["files"] == 0
    assert first["stories"][0]["to"] == again["stories"][0]["to"]


def test_same_file_links_and_taken_key_is_kept_apart(lib: Library, source: dict, tmp_path: Path) -> None:
    # the same hero image is already here; the sky's key is taken here by a different image
    same = lib.import_paths([make_png(tmp_path / "copy/hero.png", color=(10, 200, 30, 255))],
                            ImportOptions(type="character")).items[0].asset_id  # fmt: skip
    other = lib.import_paths([make_png(tmp_path / "copy/other.png", color=(90, 90, 90, 255))],
                             ImportOptions(type="background")).items[0].asset_id  # fmt: skip
    lib.update_asset(other, AssetPatch(key="bg/sky"))
    with Library(source["home"]) as src:
        src.update_asset(source["sky"], AssetPatch(key="bg/sky"))
    report = merge_library(lib, source["home"])
    assert report["assets"] == {"added": 1, "linked": 1}
    assert [k["key"] for k in report["key_conflicts"]] == ["bg/sky"]
    assert lib.get_asset(lib.resolve_asset_id("bg/sky")).id == other  # never overwritten
    linked = lib.get_asset(same)
    assert "small" in {v.label for v in linked.variants}  # its missing variant came along
    moved = lib.get_asset(report["key_conflicts"][0]["asset_id"])
    assert moved.key is None and moved.metadata["merged_key"] == "bg/sky"


def test_dry_run_changes_nothing(lib: Library, source: dict) -> None:
    report = merge_library(lib, source["home"], dry_run=True)
    assert report["assets"]["added"] == 2 and report["dry_run"]
    assert lib.conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    assert not registered(lib.paths.home) and not (lib.paths.home / "merges").exists()


def test_cli_merge(home: Path, source: dict, capsys: pytest.CaptureFixture[str]) -> None:
    code, payload, _ = run_cli(capsys, "library", "merge", str(source["home"]), "--json")
    assert code == 0 and payload["status"] == "ok" and payload["merge"]["assets"]["added"] == 2
    code, payload, _ = run_cli(capsys, "library", "merge", str(home), "--json")
    assert code == 4 and payload["error"]["code"]
    with pytest.raises(InvalidInputError):
        with Library(home) as lib:
            merge_library(lib, home / "nowhere")
    assert json.loads((home / "stories.json").read_text())["stories"]


def test_job_references_point_at_the_merged_jobs(lib: Library, tmp_path: Path) -> None:
    from assetforge.audio.ambience.engine import AmbienceRequest, generate_ambience
    from assetforge.audio.generate import AudioRequest, generate_audio

    generate_audio(lib, AudioRequest(recipe="jump", seed=1))  # job_00001 here is something else
    with Library(tmp_path / "other") as other:
        made = generate_ambience(other, AmbienceRequest(scene="home_interior", length=4, seed=2, name="room"))["asset_id"]
        assert other.get_asset(made).metadata["generator"]["job_id"] == "job_00001"
    merge_library(lib, tmp_path / "other")
    merged = lib.get_asset(report_asset(lib, "room"))
    job = merged.metadata["generator"]["job_id"]
    assert job != "job_00001"
    row = lib.conn.execute("SELECT asset_id, kind FROM generation_jobs WHERE id = ?", (job,)).fetchone()
    assert row["asset_id"] == merged.id


def test_shared_history_is_linked_not_copied(lib: Library, tmp_path: Path) -> None:
    """A library that started as a copy of this one: the jobs both have are linked, so costs aren't counted twice."""
    import shutil
    import sqlite3

    from assetforge.audio.generate import AudioRequest, generate_audio

    generate_audio(lib, AudioRequest(recipe="jump", seed=1))
    copy = tmp_path / "copy"
    copy.mkdir()
    out = sqlite3.connect(copy / "catalog.sqlite3")
    lib.conn.backup(out)
    out.close()
    shutil.copytree(lib.paths.objects, copy / "objects")
    with Library(copy) as other:
        generate_audio(other, AudioRequest(recipe="coin", seed=3))
    before = lib.conn.execute("SELECT count(*) FROM generation_jobs").fetchone()[0]
    report = merge_library(lib, copy)
    assert report["jobs_linked"] == before and report["jobs"] == 1
    assert report["assets"] == {"added": 1, "linked": 1}


def report_asset(lib: Library, name: str) -> str:
    return lib.conn.execute("SELECT id FROM assets WHERE name = ?", (name,)).fetchone()["id"]
